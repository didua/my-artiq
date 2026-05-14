"""
reboot_piezo_check_ip.py
------------------------
피에조 컨트롤러를 RBT로 재부팅 → DHCP 재시도 → 새 IP 확인.

전제:
    - 컨트롤러 LAN 포트에 이더넷 케이블이 꽂혀있어야 함
    - 영구 저장된 IPSTART=1 (DHCP) 이어야 함 (IFS?로 확인됨)

실행:
    python -m kist_nv.reboot_piezo_check_ip
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
log = logging.getLogger("piezo_rbt")

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
    usb_reset()
    time.sleep(2)
    from kist_nv.layer0.piezo import Piezo
    piezo = Piezo()
    piezo.connect()
    return piezo


def wait_for_usb(timeout: float = 30.0) -> bool:
    """USB 디바이스가 다시 enumerate 될 때까지 대기"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = subprocess.check_output(
                ['lsusb', '-d', f'{VID:04x}:{PID:04x}']
            ).decode()
            if '1a72:101e' in out:
                log.info("USB 디바이스 재발견: %s", out.strip())
                return True
        except subprocess.CalledProcessError:
            pass
        time.sleep(1)
    return False


def main() -> int:
    log.info("=" * 60)
    log.info("피에조 재부팅 + DHCP IP 확인")
    log.info("=" * 60)

    # 1. 연결
    log.info("[1/4] 연결...")
    try:
        piezo = connect_piezo()
    except Exception as e:
        log.error("연결 실패: %s", e)
        return 1

    # 2. RBT
    log.info("[2/4] RBT 재부팅 전송...")
    try:
        piezo._gateway.send('RBT\n')
    except Exception as e:
        log.warning("RBT 전송 중 예외 (재부팅 시 정상): %s", e)
    try:
        piezo.close()
    except Exception:
        pass

    # 3. USB 재 enumerate 대기
    log.info("[3/4] 컨트롤러 부팅 + USB 재인식 대기...")
    time.sleep(5)  # USB unplug 대기
    if not wait_for_usb(timeout=30.0):
        log.error("USB 디바이스가 30초 내 재발견 안 됨")
        return 2

    # 추가 안정화 대기 (DHCP 협상 시간)
    log.info("DHCP 협상 대기 (10초)...")
    time.sleep(10)

    # 4. 재연결 + IP 확인
    log.info("[4/4] 재연결 + IP 조회...")
    try:
        piezo2 = connect_piezo()
    except Exception as e:
        log.error("재연결 실패: %s", e)
        return 3

    try:
        params = piezo2.get_network_info()
    finally:
        piezo2.close()

    log.info("─" * 60)
    log.info("재부팅 후 인터페이스 파라미터:")
    for k, v in params.items():
        log.info("  %-15s = %s", k, v)
    log.info("─" * 60)

    ipadr = params.get('IPADR', '?')
    ipstart = params.get('IPSTART', '?')

    if ipstart == '1' and ipadr.startswith('192.168.20.'):
        log.info("✓ DHCP 성공! 라우터 할당 IP: %s", ipadr)
    elif ipadr.startswith('10.10.2.'):
        log.warning("아직 fallback static IP (%s). DHCP 실패 가능성:", ipadr)
        log.warning("  - LAN 케이블/포트 LED 확인")
        log.warning("  - 라우터 DHCP 풀 여유 확인")
    else:
        log.info("현재 IP: %s (모드: %s)", ipadr, ipstart)

    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
