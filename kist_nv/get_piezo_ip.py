"""
get_piezo_ip.py
---------------
피에조 컨트롤러 IP 등 네트워크 정보 조회.

내부적으로 Piezo.get_network_info() (IFC? 기반)를 호출.
USB 큐가 어긋난 상태에서도 동작하도록 사전 usbreset 수행.

실행:
    python -m kist_nv.get_piezo_ip
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
log = logging.getLogger("piezo_ip")

VID = 0x1a72
PID = 0x101e


def usb_reset() -> bool:
    """lsusb로 bus/device 찾아서 usbreset 실행"""
    try:
        out = subprocess.check_output(
            ['lsusb', '-d', f'{VID:04x}:{PID:04x}']
        ).decode()
    except subprocess.CalledProcessError:
        log.error("lsusb: 디바이스(1a72:101e) 못 찾음")
        return False

    m = re.match(r'Bus (\d+) Device (\d+):', out)
    if not m:
        log.error("lsusb 출력 파싱 실패: %r", out)
        return False
    bus, dev = m.group(1), m.group(2)
    log.info("USB 디바이스 위치: bus=%s device=%s", bus, dev)

    try:
        result = subprocess.run(
            ['usbreset', f'{bus}/{dev}'],
            capture_output=True, text=True, timeout=5,
        )
        log.info("usbreset: %s", result.stdout.strip())
        return result.returncode == 0
    except Exception as e:
        log.error("usbreset 실행 실패: %s", e)
        return False


def main() -> int:
    log.info("=" * 60)
    log.info("피에조 네트워크 정보 조회")
    log.info("=" * 60)

    log.info("[1/3] USB 디바이스 리셋...")
    usb_reset()
    time.sleep(2)

    log.info("[2/3] 피에조 연결...")
    from kist_nv.layer0.piezo import Piezo
    piezo = Piezo()
    try:
        piezo.connect()
    except Exception as e:
        log.error("connect() 실패: %s", e)
        return 1

    try:
        log.info("[3/3] get_network_info() 호출...")
        params = piezo.get_network_info()
    except Exception as e:
        log.error("get_network_info() 실패: %s", e)
        piezo.close()
        return 2

    piezo.close()

    log.info("─" * 60)
    log.info("인터페이스 파라미터:")
    for k, v in params.items():
        log.info("  %-15s = %s", k, v)
    log.info("─" * 60)

    if 'IPADR' in params:
        log.info("[발견] IP:   %s", params['IPADR'])
    if 'IPMASK' in params:
        log.info("[발견] 마스크: %s", params['IPMASK'])
    if 'MACADR' in params:
        log.info("[발견] MAC:   %s", params['MACADR'])
    if 'IPSTART' in params:
        mode = 'DHCP' if params['IPSTART'] == '1' else 'Static'
        log.info("[발견] 모드:  %s (%s)", params['IPSTART'], mode)

    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
