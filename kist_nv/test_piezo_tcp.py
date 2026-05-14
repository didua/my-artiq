"""
test_piezo_tcp.py
-----------------
Piezo 클래스를 TCP transport로 사용하는 통합 테스트.

실행:
    python -m kist_nv.test_piezo_tcp
"""

import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger("piezo_tcp_test")

HOST = '192.168.50.63'
PORT = 50000


def main() -> int:
    from kist_nv.layer0.piezo import Piezo

    log.info("=" * 60)
    log.info("Piezo TCP transport 통합 테스트")
    log.info("=" * 60)

    piezo = Piezo(transport='tcp', host=HOST, port=PORT)

    try:
        piezo.connect()
    except Exception as e:
        log.error("connect() 실패: %s", e)
        return 1

    try:
        # 위치 읽기
        x, y, z = piezo.get_position()
        log.info("현재 위치: X=%.4f  Y=%.4f  Z=%.4f µm", x, y, z)

        # 네트워크 정보 확인
        net = piezo.get_network_info()
        log.info("네트워크 정보: %s", net)

        log.info("✓ TCP transport 동작 OK")

    finally:
        piezo.close()

    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
