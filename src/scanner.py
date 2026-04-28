from __future__ import annotations

import asyncio
import logging

from src.config import MIN_APR
from src.exchanges import ALL_EXCHANGES
from src.models import EarnProduct
from src.notifier import notify_products, send_telegram
from src.peg_verify import refresh_verification
from src.store import ProductStore

logger = logging.getLogger(__name__)


async def fetch_all_products() -> list[EarnProduct]:
    """모든 거래소에서 상품을 병렬로 가져옵니다."""
    exchanges = [ExchangeClass() for ExchangeClass in ALL_EXCHANGES]
    tasks = [ex.safe_fetch() for ex in exchanges]
    results = await asyncio.gather(*tasks)

    all_products: list[EarnProduct] = []
    for products in results:
        all_products.extend(products)

    logger.info(f"전체 {len(all_products)}개 스테이블코인 상품 조회")
    return all_products


def filter_products(products: list[EarnProduct], min_apr: float = MIN_APR) -> list[EarnProduct]:
    """APR 기준 이상의 상품만 필터링 (매진 상품 제외)"""
    filtered = []
    for p in products:
        if p.is_sold_out:
            continue
        if p.apr >= min_apr:
            filtered.append(p)
    logger.info(f"APR {min_apr}% 이상: {len(filtered)}개 / 전체 {len(products)}개")
    return filtered


async def scan_and_notify() -> int:
    """
    메인 스캔 로직:
    1. 모든 거래소에서 상품 조회 + 공지사항 스캔 (병렬)
    2. APR 필터링
    3. 신규 상품 판별
    4. 텔레그램 알림 전송
    반환: 신규 상품 + 공지 알림 수
    """
    from src.announcements import scan_announcements

    store = ProductStore()

    # 0. 스테이블코인 페그 검증 (캐시 24h, API 실패 시 원본 폴백)
    await refresh_verification()

    # 1. 상품 조회 + 공지사항 스캔 (병렬)
    product_task = fetch_all_products()
    announcement_task = scan_announcements()
    all_products, ann_count = await asyncio.gather(product_task, announcement_task)

    if ann_count > 0:
        logger.info(f"공지사항 알림: {ann_count}건")

    if not all_products:
        logger.warning("조회된 상품이 없습니다.")
        return ann_count

    # 2. APR 필터링
    qualified = filter_products(all_products)

    # 3. 신규 상품 판별
    new_products: list[EarnProduct] = []
    for product in qualified:
        if store.is_new(product):
            new_products.append(product)

    logger.info(f"신규 상품: {len(new_products)}개")

    # 4. 알림 전송
    if new_products:
        sent = await notify_products(new_products)
        logger.info(f"상품 알림 전송: {sent}건")

    # 5. 상태 업데이트 (신규/기존 모두)
    for product in qualified:
        store.mark_seen(product)

    # 6. 오래된 기록 정리 및 저장
    store.cleanup_old(days=30)
    store.save()

    return len(new_products) + ann_count


async def send_startup_message() -> None:
    """시작 알림"""
    from src.config import has_keys

    # 공개 API 지원 거래소
    public_exchanges = ["bybit", "okx", "gateio", "htx"]
    # API 키 필요 거래소
    key_required = ["binance", "kucoin"]

    public_active = [e.upper() for e in public_exchanges]
    key_active = [e.upper() for e in key_required if has_keys(e)]
    key_inactive = [e.upper() for e in key_required if not has_keys(e)]

    msg_lines = [
        "\u2705 <b>Stable Farming 모니터링 시작</b>",
        "",
        f"\U0001f310 <b>공개 API:</b> {', '.join(public_active)} (키 불필요)",
    ]
    if key_active:
        msg_lines.append(f"\U0001f511 <b>인증 API:</b> {', '.join(key_active)}")
    if key_inactive:
        msg_lines.append(f"\u23f8 <b>대기 (API 키 필요):</b> {', '.join(key_inactive)}")

    msg_lines.extend([
        "",
        f"\U0001f4ca <b>최소 APR:</b> {MIN_APR}%",
        f"\U0001f4e2 <b>공지사항 추적:</b> BINANCE, BYBIT, OKX, KUCOIN, HTX",
        "\U0001f50d 스캔을 시작합니다...",
    ])

    await send_telegram("\n".join(msg_lines))
