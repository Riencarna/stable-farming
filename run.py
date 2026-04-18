"""
Stable Farming - 스테이블코인 고수익 상품 모니터링 시스템

사용법:
    # 한 번 스캔 (GitHub Actions / cron)
    python run.py

    # 반복 스캔 (로컬 실행)
    python run.py --mode loop

    # 대시보드 실행
    python run.py --dashboard

    # 특정 거래소만 테스트
    python run.py --test binance
"""

import argparse
import asyncio
import logging
import sys
import os

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("stable-farming")


async def test_exchange(name: str) -> None:
    """특정 거래소 연결 테스트"""
    from src.exchanges import ALL_EXCHANGES
    from src.config import MIN_APR

    exchange_map = {ex.name: ex for ex in ALL_EXCHANGES}

    if name not in exchange_map:
        print(f"지원하지 않는 거래소: {name}")
        print(f"지원 목록: {', '.join(exchange_map.keys())}")
        return

    ExchangeClass = exchange_map[name]
    exchange = ExchangeClass()

    print(f"\n{'='*50}")
    print(f"  {name.upper()} 연결 테스트")
    print(f"  API 키: {'설정됨' if exchange.has_credentials else '미설정 (공개 API 사용)'}")
    print(f"{'='*50}\n")

    products = await exchange.safe_fetch()
    print(f"\n총 {len(products)}개 스테이블코인 상품 발견\n")

    qualified = [p for p in products if p.apr >= MIN_APR]
    print(f"APR {MIN_APR}% 이상: {len(qualified)}개\n")

    if qualified:
        print(f"{'코인':<8} {'APR':>8} {'유형':<12} {'기간':<10} {'상품명'}")
        print("-" * 60)
        for p in sorted(qualified, key=lambda x: x.apr, reverse=True):
            print(
                f"{p.coin:<8} {p.apr:>7.2f}% {p.apr_type_label:<12} "
                f"{p.duration_label:<10} {p.product_name}"
            )
    else:
        print("APR 기준을 만족하는 상품이 없습니다.")


async def test_telegram() -> None:
    """텔레그램 연결 테스트"""
    from src.notifier import send_telegram

    print("\n텔레그램 연결 테스트...")
    ok = await send_telegram("\u2705 Stable Farming 텔레그램 연결 테스트 성공!")
    if ok:
        print("전송 성공! 텔레그램을 확인하세요.")
    else:
        print("전송 실패. TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID를 확인하세요.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stable Farming - 스테이블코인 수익 상품 모니터",
    )
    parser.add_argument(
        "--mode",
        choices=["once", "loop"],
        default="once",
        help="once: 한 번 스캔 / loop: 반복 스캔",
    )
    parser.add_argument(
        "--test",
        type=str,
        metavar="EXCHANGE",
        help="특정 거래소 연결 테스트 (예: binance, bybit, okx)",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="웹 대시보드 실행 (기본: http://localhost:8000)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="대시보드 포트 (기본: 8000)",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="텔레그램 연결 테스트",
    )
    args = parser.parse_args()

    if args.dashboard:
        import uvicorn
        from src.dashboard import app
        print(f"\n  Stable Farming Dashboard")
        print(f"  http://localhost:{args.port}\n")
        uvicorn.run(app, host="0.0.0.0", port=args.port)
        return

    if args.test_telegram:
        asyncio.run(test_telegram())
        return

    if args.test:
        asyncio.run(test_exchange(args.test.lower()))
        return

    from src.main import main as app_main
    app_main()


if __name__ == "__main__":
    main()
