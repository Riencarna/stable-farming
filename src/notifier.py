from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.models import EarnProduct

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _format_number(n: float) -> str:
    """숫자를 읽기 쉽게 포맷"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:,.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:,.1f}K"
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def _exchange_emoji(exchange: str) -> str:
    emojis = {
        "binance": "\U0001f7e1",   # 노란 원
        "bybit": "\U0001f7e0",     # 주황 원
        "okx": "\u26aa",           # 흰 원
        "gateio": "\U0001f535",    # 파란 원
        "kucoin": "\U0001f7e2",    # 초록 원
        "htx": "\U0001f534",       # 빨간 원
    }
    return emojis.get(exchange, "\u26aa")


def format_product_message(product: EarnProduct) -> str:
    """텔레그램 알림 메시지 포맷"""
    emoji = _exchange_emoji(product.exchange)
    exchange_upper = product.exchange.upper()

    lines = [
        f"\U0001f6a8 <b>새로운 고수익 상품 발견!</b>",
        "",
        f"{emoji} <b>거래소:</b> {exchange_upper}",
        f"\U0001f4b0 <b>코인:</b> {product.coin}",
        f"\U0001f4cb <b>상품:</b> {product.product_name}",
        f"\U0001f4c8 <b>APR:</b> {product.apr}% ({product.apr_type_label})",
        f"\U0001f4c5 <b>유형:</b> {product.product_type_label}",
        f"\u23f0 <b>기간:</b> {product.duration_label}",
    ]

    if product.min_amount > 0:
        lines.append(f"\U0001f4b5 <b>최소 예치:</b> {_format_number(product.min_amount)} {product.coin}")

    if product.max_amount > 0:
        lines.append(f"\U0001f4b5 <b>최대 예치:</b> {_format_number(product.max_amount)} {product.coin}")

    if product.is_limited:
        lines.append("")
        if product.total_quota > 0:
            lines.append(f"\U0001f4e6 <b>총 한도:</b> {_format_number(product.total_quota)} {product.coin}")
        if product.remaining_quota > 0:
            pct = (product.remaining_quota / product.total_quota * 100) if product.total_quota > 0 else 0
            lines.append(f"\u26a0\ufe0f <b>잔여:</b> {_format_number(product.remaining_quota)} ({pct:.0f}%)")
        lines.append(f"\u203c\ufe0f <b>조기 마감 가능! 서두르세요!</b>")

    if product.end_time:
        lines.append(f"\U0001f4c5 <b>마감:</b> {product.end_time.strftime('%Y-%m-%d %H:%M UTC')}")

    if product.url:
        lines.append("")
        lines.append(f'\U0001f517 <a href="{product.url}">바로가기</a>')

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines.append(f"\n\U0001f552 {now_str}")

    return "\n".join(lines)


def format_summary_message(products: list[EarnProduct]) -> str:
    """여러 상품 요약 메시지"""
    lines = [
        f"\U0001f4ca <b>스캔 완료 - {len(products)}개 신규 상품 발견</b>",
        "",
    ]
    for p in sorted(products, key=lambda x: x.apr, reverse=True):
        emoji = _exchange_emoji(p.exchange)
        limited = " \u26a0\ufe0f한정" if p.is_limited else ""
        lines.append(
            f"{emoji} {p.exchange.upper()} | {p.coin} | "
            f"{p.apr}% {p.apr_type_label} | "
            f"{p.duration_label}{limited}"
        )

    return "\n".join(lines)


async def send_telegram(text: str) -> bool:
    """텔레그램 메시지 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정 없음 - 콘솔에만 출력")
        print(text)
        return False

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            if not result.get("ok"):
                logger.error(f"텔레그램 전송 실패: {result}")
                return False
            return True
    except Exception as e:
        logger.error(f"텔레그램 전송 에러: {e}")
        return False


async def notify_products(products: list[EarnProduct]) -> int:
    """새 상품들에 대한 알림 전송. 전송 성공 수 반환."""
    if not products:
        return 0

    sent = 0

    # 5개 이상이면 요약 먼저 전송
    if len(products) >= 5:
        await send_telegram(format_summary_message(products))

    # 개별 상품 알림
    for product in sorted(products, key=lambda x: x.apr, reverse=True):
        msg = format_product_message(product)
        if await send_telegram(msg):
            sent += 1

    return sent
