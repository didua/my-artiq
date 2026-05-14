"""
set_piezo_dhcp.py
-----------------
피에조 컨트롤러(E-727.3CDA) 네트워크 설정을 DHCP로 영구 전환.

순서:
    1. USB 리셋 + 연결
    2. 현재 영구 저장된 인터페이스 설정 확인 (IFS?)
    3. IFS 100 IPSTART 1 → DHCP 영구 저장 (100은 PI 표준 패스워드)
    4. ERR? 로 명령 수락 여부 확인
    5. RBT 재부팅 → USB 끊김 → 재연결
    6. IFC? 로 라우터가 할당한 IP 확인

실행:
    python -m kist_nv.set_piezo_dhcp
"""

import sys
import time
import logging
import subprocess
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger("piezo_dhcp")

VID = 0x1a72
PID = 0x101e


def usb_reset() -> bool:
    try:
        out = subprocess.check_output(
            ['lsusb', '-d', f'{VID:04x}:{PID:04x}']
        ).decode()
    except subprocess.CalledProcessError:
        return False
    m = re.match(r'Bus (\d+) Device (\d+):', out)
    if not m:
        return False
    bus, dev = m.group(1), m.group(2)
    log.info("USB 리셋: bus=%s device=%s", bus, dev)
    try:
        r = subprocess.run(
            ['usbreset', f'{bus}/{dev}'],
            capture_output=True, text=True, timeout=5,
        )
        log.info("usbreset: %s", r.stdout.strip())
        return r.returncode == 0
    except Exception as e:
        log.error("usbreset 실패: %s", e)
        return False


def connect_piezo():
    """USB 리셋 + 새 Piezo 인스턴스 연결"""
    usb_reset()
    time.sleep(2)
    from kist_nv.layer0.piezo import Piezo
    piezo = Piezo()
    piezo.connect()
    return piezo


def parse_kv(raw: str) -> dict:
    result = {}
    for line in raw.replace(' \n', '\n').strip().split('\n'):
        if '=' in line:
            k, v = line.split('=', 1)
            result[k.strip()] = v.strip()
    return result


def main() -> int:
    log.info("=" * 60)
    log.info("피에조 DHCP 전환")
    log.info("=" * 60)

    # 1. 연결
    log.info("[1/6] 연결...")
    try:
        piezo = connect_piezo()
    except Exception as e:
        log.error("연결 실패: %s", e)
        return 1
    gw = piezo._gateway

    try:
        # 2. 현재 영구 저장값 확인
        log.info("[2/6] 현재 영구 저장값 (IFS?)...")
        try:
            ifs_before = piezo._query('IFS?', timeout=3.0)
            log.info("IFS? 응답:\n%s", ifs_before)
        except TimeoutError:
            # IFS? 미지원 펌웨어일 수도 있음 → IFC? 로 폴백
            log.warning("IFS? 응답 없음. IFC? 로 현재값만 확인:")
            log.info("IFC? 응답:\n%s", piezo._query('IFC?', timeout=3.0))

        # 3. DHCP 활성화 (영구 저장)
        log.info("[3/6] IFS 100 IPSTART 1 전송 (DHCP 영구 저장)...")
        gw.send('IFS 100 IPSTART 1\n')
        time.sleep(0.5)

        # 4. 에러 큐 확인
        log.info("[4/6] ERR? 확인...")
        try:
            err = piezo._query('ERR?', timeout=2.0)
            log.info("ERR? = %s", err)
            if err != '0':
                log.error("→ 명령 거부됨. 패스워드(100)가 틀렸거나 IPSTART 미지원.")
                piezo.close()
                return 2
        except TimeoutError:
            log.warning("ERR? 응답 없음 — 그래도 진행")

        # 5. 변경 확인
        log.info("[5/6] 영구 저장값 재확인...")
        try:
            ifs_after = piezo._query('IFS?', timeout=3.0)
            log.info("IFS? 응답:\n%s", ifs_after)
            params = parse_kv(ifs_after)
            if params.get('IPSTART') == '1':
                log.info("✓ IPSTART=1 (DHCP) 저장 확인")
            else:
                log.warning("IPSTART 변경 안 됨: %s", params.get('IPSTART'))
        except TimeoutError:
            log.warning("IFS? 재확인 응답 없음 — 그래도 RBT 진행")

        # 6. 재부팅
        log.info("[6/6] RBT 재부팅...")
        log.info("    → USB 일시 끊김. ~10초 대기 후 재연결 시도.")
        gw.send('RBT\n')

    finally:
        try:
            piezo.close()
        except Exception:
            pass

    # 재부팅 대기 (USB re-enumeration)
    time.sleep(10)

    # 재연결 + IP 확인
    log.info("재부팅 후 재연결 시도...")
    try:
        piezo2 = connect_piezo()
    except Exception as e:
        log.error("재부팅 후 연결 실패: %s", e)
        log.error("→ 컨트롤러가 부팅 중일 수 있음. 잠시 후 get_piezo_ip 수동 실행.")
        return 3

    try:
        params = piezo2.get_network_info()
        log.info("─" * 60)
        log.info("재부팅 후 인터페이스 파라미터:")
        for k, v in params.items():
            log.info("  %-15s = %s", k, v)
        log.info("─" * 60)

        ipstart = params.get('IPSTART', '?')
        ipadr = params.get('IPADR', '?')
        if ipstart == '1':
            log.info("✓ DHCP 모드 활성")
            log.info("✓ 라우터 할당 IP: %s", ipadr)
        else:
            log.warning("IPSTART=%s (DHCP 아님)", ipstart)
    finally:
        piezo2.close()

    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
