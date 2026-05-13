"""
piezo.py
--------
layer0/piezo.py

E-727.3CDA + P-562.3CD 피에조 나노 스테이지 제어 모듈
PIUSB 직접 통신 방식 (libpi_pi_gcs2.so 불필요)

사용 예시:
    from kist_nv.layer0.piezo import Piezo

    piezo = Piezo()
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
        serial: str = SERIAL,
        soft_limits: dict = None,
        settle_time: float = 0.02,
    ):
        self._serial = serial
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
        """USB 연결"""
        logger.info("피에조 USB 연결 중... (시리얼: %s)", self._serial)
        self._gateway = PIUSB()
        self._gateway._timeout = 10000
        self._gateway.connect(serialnumber=self._serial, pid=PID, vid=VID)
        self._connected = True
        logger.info("피에조 연결 성공!")

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
        self._gateway.send('POS?\n')
        time.sleep(1.0)
        raw = self._gateway.read()
        return self._parse_position(raw)

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
        """이동 완료 대기"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._gateway.send('ONT?\n')
            time.sleep(0.1)
            try:
                resp = self._gateway.read()
                if resp.count('1') >= 3:
                    return
            except Exception:
                pass
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
