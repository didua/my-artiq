# ─────────────────────────────────────────────────────────────────────────────
# WDLEE 변경 이력 (2026-05-14)
# ─────────────────────────────────────────────────────────────────────────────
# 1. TCP(LAN) 통신 지원 추가
#    - Piezo(transport='tcp', host='192.168.50.63') 형태로 사용
#    - pipython의 PISocket을 PIUSB와 동일한 send/read/close 인터페이스로 활용
#    - USB(기본) / TCP 두 가지 transport를 같은 클래스에서 선택 가능
#
# 2. get_network_info() 수정 → 컨트롤러 멎는 문제 해결
#    - 이전: IPADR? / MAC? / IPSTART? 개별 쿼리 → E-727이 인식 못함 → USB 큐 깨짐 → 연결 끊김
#    - 변경: PI GCS 표준 IFC? 한 번에 일괄 조회 → 안전. 반환은 {key: value} 딕셔너리.
#    - USB 큐 깨졌을 때 복구는 `usbreset <bus>/<dev>` 한 방으로 가능 (전원 재인가 불필요).
#
# 3. _query() 응답 종결 규칙 정정
#    - PI GCS 멀티라인 응답은 '<data> \n<data> \n...<data>\n' 형식
#      (마지막 라인 외에는 '<space>\n' 으로 이어짐)
#    - 이전: 첫 '\n' 보고 종료 → 멀티라인 중간에서 잘림.
#    - 변경: endswith('\n') and not endswith(' \n') 로 판정하여 끝까지 누적.
#    - 응답 없으면 TimeoutError 발생.
#
# 4. get_position() / _wait_on_target() 도 _query() 기반으로 통일
#    - 이전: 한 번 read한 결과만 보고 파싱 시도 → TCP에서 청크 단위 도착 시 첫 조각만 보고 깨짐.
#    - 변경: _query()로 PI GCS 종결 규칙까지 누적해서 받은 다음 파싱.
#
# 5. _read_idn() 제거
#    - 기존 1.5초 sleep 후 2회 read 패턴은 USB에서 IDN이 두 패킷으로 쪼개져 오던 케이스 대응이었음.
#    - 이제 _query('*IDN?') 가 PI GCS 종결 규칙으로 알아서 처리 → 별도 helper 불필요.
#
# 운영상 주의 (DHCP IP 가변)
#    - 현재 컨트롤러는 IPSTART=1 (DHCP). 2026-05-14 시점 라우터 할당 IP는 192.168.50.63.
#    - lease 갱신 시 IP가 바뀔 수 있음 → 라우터에서 MAC d8-47-8f-c0-b7-f3 에 고정 IP 예약 권장.
#    - LAN 케이블 빠진 채 부팅하면 fallback static IP 10.10.2.175 로 떨어짐 (다른 망이라 접근 불가).
#      그땐 USB로 붙어서 get_network_info() 로 상태 확인.
# ─────────────────────────────────────────────────────────────────────────────

"""
piezo.py
--------
layer0/piezo.py

E-727.3CDA + P-562.3CD 피에조 나노 스테이지 제어 모듈
PIUSB / PISocket 직접 통신 방식 (libpi_pi_gcs2.so 불필요)

사용 예시:
    from kist_nv.layer0.piezo import Piezo

    # USB 연결 (기본)
    piezo = Piezo()
    piezo.connect()

    # LAN(TCP) 연결
    piezo = Piezo(transport='tcp', host='192.168.50.63')
    piezo.connect()

    piezo.initialize()
    piezo.move_to(x=50.0, y=50.0, z=10.0)
    x, y, z = piezo.get_position()
    piezo.close()
"""

import time
import logging
import numpy as np
from pipython.pidevice.interfaces.piusb import PIUSB
from pipython.pidevice.interfaces.pisocket import PISocket

logger = logging.getLogger(__name__)

# ── 장비 설정 ──────────────────────────────────────────
SERIAL = "0126011356"
PID    = 0x101e
VID    = 0x1a72

# P-562.3CD 이동 범위 (µm) — 데이터시트 기준
SOFT_LIMIT_X = (0.0, 200.0)
SOFT_LIMIT_Y = (0.0, 200.0)
SOFT_LIMIT_Z = (0.0, 200.0)


class Piezo:
    """
    E-727.3CDA + P-562.3CD 피에조 스테이지 제어 클래스

    레이어 구조:
        layer2 (측정) → layer1 (기능) → layer0/piezo.py → 하드웨어
    """

    def __init__(
        self,
        transport: str = 'usb',
        serial: str = SERIAL,
        host: str = None,
        port: int = 50000,
        soft_limits: dict = None,
        settle_time: float = 0.02,
    ):
        """
        Parameters
        ----------
        transport : 'usb' | 'tcp'
            'usb' (기본) → PIUSB. serial로 디바이스 식별.
            'tcp'        → PISocket. host:port 로 연결.
        serial : str
            USB 시리얼 번호 (transport='usb' 일 때).
        host : str
            컨트롤러 IP (transport='tcp' 일 때 필수).
            ⚠ DHCP 사용 시 IP가 바뀔 수 있음. 라우터에서 MAC 고정 IP 예약 권장.
        port : int
            TCP 포트 (PI 기본 50000).
        """
        if transport not in ('usb', 'tcp'):
            raise ValueError(f"transport는 'usb' 또는 'tcp' 여야 함 (받은 값: {transport!r})")

        self._transport = transport
        self._serial = serial
        self._host = host
        self._port = port
        self._settle_time = settle_time
        self._soft_limits = soft_limits or {
            'X': SOFT_LIMIT_X,
            'Y': SOFT_LIMIT_Y,
            'Z': SOFT_LIMIT_Z,
        }
        self._gateway = None
        self._connected = False
        self._saved_position = None

    # ──────────────────────────────────────────────────
    # 연결 / 해제
    # ──────────────────────────────────────────────────

    def connect(self) -> None:
        """USB 또는 TCP 연결. IDN 읽기까지 마쳐서 명령어 수신 가능 상태로."""
        if self._transport == 'tcp':
            if not self._host:
                raise ValueError("transport='tcp' 일 때 host 인자 필수")
            logger.info("피에조 TCP 연결 중... (%s:%d)", self._host, self._port)
            self._gateway = PISocket(host=self._host, port=self._port)
        else:
            logger.info("피에조 USB 연결 중... (시리얼: %s)", self._serial)
            self._gateway = PIUSB()
            self._gateway._timeout = 10000
            self._gateway.connect(serialnumber=self._serial, pid=PID, vid=VID)

        # E-727 USB는 연결 직후 IDN을 먼저 읽어야 다른 명령어를 받음.
        # TCP에선 불필요하지만 IDN으로 sanity check.
        idn = self._query('*IDN?', timeout=3.0)
        logger.info("피에조 연결 성공! %s", idn)

        self._connected = True

    def close(self) -> None:
        """연결 종료"""
        if self._gateway:
            try:
                self._gateway.close()
                logger.info("피에조 연결 종료.")
            except Exception:
                pass
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ──────────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────────

    def initialize(self, restore_position: bool = True) -> None:
        """
        피에조 초기화

        초기화 시 위치가 0으로 리셋되는 문제를 방지하기 위해
        초기화 전 현재 위치를 저장하고, 초기화 후 복귀합니다.

        Parameters
        ----------
        restore_position : bool
            True  → 초기화 후 이전 위치로 복귀 (기본값)
            False → 초기화 후 현재 위치 유지
        """
        self._check_connected()

        # 1. 현재 위치 저장
        try:
            self._saved_position = self.get_position()
            logger.info(
                "초기화 전 위치 저장: X=%.4f Y=%.4f Z=%.4f",
                *self._saved_position
            )
        except Exception as e:
            logger.warning("위치 저장 실패: %s", e)
            self._saved_position = None

        # 2. 서보 ON
        self._servo_on()
        time.sleep(0.5)

        # 3. 이전 위치로 복귀
        if restore_position and self._saved_position:
            x, y, z = self._saved_position
            logger.info("이전 위치로 복귀 중...")
            self.move_to(x=x, y=y, z=z)
            logger.info("복귀 완료!")

        logger.info("피에조 초기화 완료!")

    def _servo_on(self) -> None:
        """서보(closed-loop) 활성화"""
        logger.info("서보 ON...")
        self._gateway.send('SVO 1 1\n')
        time.sleep(0.3)
        self._gateway.send('SVO 2 1\n')
        time.sleep(0.3)
        self._gateway.send('SVO 3 1\n')
        time.sleep(0.3)
        logger.info("서보 ON 완료!")

    # ──────────────────────────────────────────────────
    # 위치 제어
    # ──────────────────────────────────────────────────

    def move_to(
        self,
        x: float = None,
        y: float = None,
        z: float = None,
        wait: bool = True,
    ) -> None:
        """
        절대 위치로 이동

        Parameters
        ----------
        x, y, z : float, optional
            목표 위치 (µm). None이면 해당 축 이동 안 함
        wait : bool
            True면 이동 완료까지 대기
        """
        self._check_connected()

        for ax, val in zip(['X', 'Y', 'Z'], [x, y, z]):
            if val is not None:
                self._check_soft_limit(ax, val)

        if x is not None:
            self._gateway.send(f'MOV 1 {x:.6f}\n')
            time.sleep(0.05)
        if y is not None:
            self._gateway.send(f'MOV 2 {y:.6f}\n')
            time.sleep(0.05)
        if z is not None:
            self._gateway.send(f'MOV 3 {z:.6f}\n')
            time.sleep(0.05)

        if wait:
            self._wait_on_target()

        time.sleep(self._settle_time)

    def move_by(
        self,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
        wait: bool = True,
    ) -> None:
        """
        상대 위치로 이동

        Parameters
        ----------
        dx, dy, dz : float
            현재 위치에서의 이동량 (µm)
        """
        self._check_connected()
        cx, cy, cz = self.get_position()
        self.move_to(x=cx + dx, y=cy + dy, z=cz + dz, wait=wait)

    def stop(self) -> None:
        """긴급 정지"""
        if self._gateway:
            self._gateway.send('STP\n')
            logger.warning("피에조 긴급 정지!")

    # ──────────────────────────────────────────────────
    # 위치 읽기
    # ──────────────────────────────────────────────────

    def get_position(self) -> tuple:
        """
        현재 XYZ 위치 반환

        Returns
        -------
        tuple : (x, y, z) in µm
        """
        self._check_connected()
        raw = self._query('POS?', timeout=5.0)
        return self._parse_position(raw)

    # ──────────────────────────────────────────────────
    # 네트워크 정보 조회 (LAN 연결용 IP 확인)
    # ──────────────────────────────────────────────────

    def get_network_info(self) -> dict:
        """
        컨트롤러 네트워크 설정 조회 (USB 연결 상태에서 호출)

        LAN(TCP/IP) 연결로 전환하기 전에 IP 주소를 확인하는 용도.
        PI GCS 표준 IFC? 명령을 사용해 모든 인터페이스 파라미터를 일괄 조회.

        ⚠ 과거 버전은 IPADR?/MAC?/IPSTART? 개별 쿼리를 썼는데, E-727이
        이 명령들을 인식하지 못해서 USB 통신 큐가 깨지는 문제가 있었음.

        Returns
        -------
        dict
            컨트롤러가 반환한 모든 인터페이스 파라미터 (key=value 형식).
            펌웨어 01.038 기준 예시:
              {'RSBAUD': '115200',
               'IPADR': '10.10.2.175:50000',
               'MACADR': 'd8-47-8f-c0-b7-f3',
               'IPMASK': '255.255.255.0',
               'IPSTART': '0'}              # 0 = static, 1 = DHCP
        """
        self._check_connected()
        raw = self._query('IFC?', timeout=3.0)

        params = {}
        for line in raw.replace(' \n', '\n').strip().split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                params[k.strip()] = v.strip()
        return params

    def _query(self, cmd: str, timeout: float = 3.0) -> str:
        """
        GCS 쿼리 — PI GCS 멀티라인 응답 종결 규칙으로 폴링.

        응답 종결 규칙:
            - 단일 라인:  '<data>\\n'
            - 멀티 라인:  '<data> \\n<data> \\n...<data>\\n'
                         (마지막 라인 외에는 ' \\n' = 스페이스+LF로 이어짐)
        """
        self._gateway.send(cmd + '\n')
        deadline = time.time() + timeout
        buf = ""
        while time.time() < deadline:
            try:
                raw = self._gateway.read()
                if raw:
                    buf += raw
            except Exception:
                pass
            if buf.endswith('\n') and not buf.endswith(' \n'):
                return buf.strip()
            time.sleep(0.02)
        raise TimeoutError(f"{cmd} 응답 타임아웃 (받은 데이터: {buf!r})")

    @property
    def x(self) -> float:
        return self.get_position()[0]

    @property
    def y(self) -> float:
        return self.get_position()[1]

    @property
    def z(self) -> float:
        return self.get_position()[2]

    # ──────────────────────────────────────────────────
    # 스캔 헬퍼
    # ──────────────────────────────────────────────────

    def scan_axis(
        self,
        axis: str,
        start: float,
        stop: float,
        n_points: int,
        settle_time: float = 0.01,
    ):
        """
        1축 스캔 제너레이터

        사용 예시:
            for pos in piezo.scan_axis('X', 0, 50, 101):
                counts = apd.read()
        """
        axis_num = {'X': 1, 'Y': 2, 'Z': 3}[axis]
        positions = np.linspace(start, stop, n_points)

        for pos in positions:
            self._check_soft_limit(axis, pos)
            self._gateway.send(f'MOV {axis_num} {pos:.6f}\n')
            time.sleep(settle_time)
            yield pos

    def scan_xy(
        self,
        x_start: float, x_stop: float, x_points: int,
        y_start: float, y_stop: float, y_points: int,
        settle_time: float = 0.01,
    ):
        """
        XY 2D 래스터 스캔 제너레이터 (스네이크 패턴)

        사용 예시:
            for ix, iy, x, y in piezo.scan_xy(0, 50, 101, 0, 50, 101):
                image[iy, ix] = apd.read()
        """
        x_positions = np.linspace(x_start, x_stop, x_points)
        y_positions = np.linspace(y_start, y_stop, y_points)

        for iy, y in enumerate(y_positions):
            self._gateway.send(f'MOV 2 {y:.6f}\n')
            time.sleep(settle_time * 3)

            row = x_positions if iy % 2 == 0 else x_positions[::-1]

            for ix_raw, x in enumerate(row):
                ix = ix_raw if iy % 2 == 0 else x_points - 1 - ix_raw
                self._gateway.send(f'MOV 1 {x:.6f}\n')
                time.sleep(settle_time)
                yield ix, iy, x, y

    # ──────────────────────────────────────────────────
    # 내부 헬퍼
    # ──────────────────────────────────────────────────

    def _wait_on_target(self, timeout: float = 10.0) -> None:
        """이동 완료 대기 (폴링 방식). 모든 축 ONT=1 되면 종료."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = self._query('ONT?', timeout=1.0)
                # 응답 형식: '1=1 \n2=1 \n3=1' — 1=1 패턴이 3번이면 모두 ON-TARGET
                if resp.count('=1') >= 3:
                    return
            except TimeoutError:
                pass
            time.sleep(0.05)
        logger.warning("이동 타임아웃!")

    def _parse_position(self, raw: str) -> tuple:
        """
        POS? 응답 파싱

        응답 형식:
            1=3.708969116e+00 \n 2=-1.223215485e+01 \n 3=-5.284927368e+00
        """
        try:
            lines = raw.strip().split('\n')
            pos = {}
            for line in lines:
                line = line.strip()
                if '=' in line:
                    axis, val = line.split('=')
                    pos[int(axis.strip())] = float(val.strip())
            return pos[1], pos[2], pos[3]
        except Exception as e:
            logger.error("위치 파싱 실패: %s (raw: %s)", e, raw)
            raise

    def _check_soft_limit(self, axis: str, value: float) -> None:
        """소프트 리밋 확인"""
        lo, hi = self._soft_limits[axis]
        if not (lo <= value <= hi):
            raise ValueError(
                f"목표 {axis}={value:.3f}µm 이 소프트 리밋 "
                f"({lo}, {hi}) 범위를 벗어났습니다."
            )

    def _check_connected(self) -> None:
        """연결 확인"""
        if not self._connected:
            raise RuntimeError(
                "피에조가 연결되지 않았습니다. connect()를 먼저 호출하세요."
            )

    # ──────────────────────────────────────────────────
    # 컨텍스트 매니저
    # ──────────────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
