"""
reconnect_piezo.py
------------------
피에조 USB 연결 복구 스크립트 (1단계: 소프트 재연결)

실행:
    python reconnect_piezo.py
"""

import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger("reconnect")


def main() -> int:
    from kist_nv.layer0.piezo import Piezo

    log.info("=" * 50)
    log.info("피에조 소프트 재연결 시작")
    log.info("=" * 50)

    piezo = Piezo()

    # 1. 연결
    try:
        piezo.connect()
    except Exception as e:
        log.error("connect() 실패: %s", e)
        log.error("→ 2단계 필요: 파이썬 종료 후 재시작, 또는 컨트롤러 전원 재인가")
        return 1

    # 2. sanity check — POS? 응답
    try:
        x, y, z = piezo.get_position()
        log.info("위치 읽기 OK: X=%.4f  Y=%.4f  Z=%.4f µm", x, y, z)
    except Exception as e:
        log.error("get_position() 실패: %s", e)
        log.error("→ USB 큐가 아직 어긋난 상태. 2단계 필요.")
        try:
            piezo.close()
        except Exception:
            pass
        return 2

    # 3. 정상 종료 (다음 세션을 위해 깔끔하게 닫음)
    piezo.close()
    log.info("=" * 50)
    log.info("복구 성공! 이제 정상 사용 가능합니다.")
    log.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
