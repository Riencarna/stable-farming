from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.config import SCAN_INTERVAL_MINUTES
from src.scanner import scan_and_notify, send_startup_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def run_once() -> None:
    """한 번만 스캔 (GitHub Actions / cron 용)"""
    logger.info("=== 단발 스캔 시작 ===")
    new_count = await scan_and_notify()
    logger.info(f"=== 스캔 완료: 신규 {new_count}개 ===")


async def run_loop() -> None:
    """반복 스캔 (로컬 실행 용)"""
    await send_startup_message()
    logger.info(f"반복 스캔 모드 (간격: {SCAN_INTERVAL_MINUTES}분)")

    while True:
        try:
            new_count = await scan_and_notify()
            logger.info(f"스캔 완료: 신규 {new_count}개 | 다음 스캔: {SCAN_INTERVAL_MINUTES}분 후")
        except Exception as e:
            logger.error(f"스캔 에러: {e}", exc_info=True)

        await asyncio.sleep(SCAN_INTERVAL_MINUTES * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stable Farming - 스테이블코인 수익 상품 모니터")
    parser.add_argument(
        "--mode",
        choices=["once", "loop"],
        default="once",
        help="once: 한 번 스캔 (기본값, CI용) / loop: 반복 스캔 (로컬용)",
    )
    args = parser.parse_args()

    if args.mode == "loop":
        asyncio.run(run_loop())
    else:
        asyncio.run(run_once())


if __name__ == "__main__":
    main()
