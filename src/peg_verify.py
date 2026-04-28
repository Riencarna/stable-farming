"""스테이블코인 페그 자동 검증

CoinGecko API로 STABLECOINS 후보 각각을 검증:
  1) `Stablecoins` 카테고리에 등록되어 있는가
  2) 시가가 $0.95 ~ $1.05 범위인가

두 조건을 모두 만족해야 verified 집합에 포함. 결과는 24시간 캐시.
API 호출 실패 시 원본 STABLECOINS 유지 (안전 폴백).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from src.config import DATA_DIR, STABLECOINS

logger = logging.getLogger(__name__)

CACHE_PATH = DATA_DIR / "stablecoin_verified.json"
CACHE_TTL_HOURS = 24
PEG_MIN = 0.95
PEG_MAX = 1.05

# STABLECOINS 후보의 CoinGecko coin id.
# 매핑이 없는 티커는 검증 없이 자동 통과 (보수적 폴백).
COINGECKO_IDS: dict[str, str] = {
    "USDT": "tether",
    "USAT": "usat",
    "USDC": "usd-coin",
    "USDE": "ethena-usde",
    "USDS": "usds",
    "USDP": "paxos-standard",
    "PYUSD": "paypal-usd",
    "DAI": "dai",
    "FDUSD": "first-digital-usd",
    "TUSD": "true-usd",
    "BUSD": "binance-usd",
    "LUSD": "liquity-usd",
    "GUSD": "gemini-dollar",
    "USDD": "usdd",
    "CRVUSD": "crvusd",
    "GHO": "gho",
}

# 다른 모듈이 import 해서 참조. 시작 시 STABLECOINS 원본으로 초기화.
VERIFIED_STABLECOINS: set[str] = set(STABLECOINS)


@dataclass
class CoinStatus:
    ticker: str
    coingecko_id: str
    in_stable_category: bool = False
    price_usd: float | None = None
    verified: bool = False
    reason: str = ""


def _load_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(cache["verified_at"])
        if datetime.now(timezone.utc) - ts < timedelta(hours=CACHE_TTL_HOURS):
            return cache
    except (json.JSONDecodeError, ValueError, KeyError, OSError) as e:
        logger.warning(f"[peg_verify] 캐시 로드 실패: {e}")
    return None


def _save_cache(verified: set[str], statuses: list[CoinStatus]) -> None:
    cache = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verified": sorted(verified),
        "statuses": [
            {
                "ticker": s.ticker,
                "coingecko_id": s.coingecko_id,
                "in_stable_category": s.in_stable_category,
                "price_usd": s.price_usd,
                "verified": s.verified,
                "reason": s.reason,
            }
            for s in statuses
        ],
    }
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8",
    )


async def _fetch_prices(client: httpx.AsyncClient, ids: list[str]) -> dict[str, float]:
    if not ids:
        return {}
    resp = await client.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ",".join(ids), "vs_currencies": "usd"},
    )
    resp.raise_for_status()
    data = resp.json()
    return {k: v["usd"] for k, v in data.items() if isinstance(v, dict) and "usd" in v}


async def _fetch_categories(client: httpx.AsyncClient, coin_id: str) -> list[str]:
    resp = await client.get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "false",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        },
    )
    resp.raise_for_status()
    return resp.json().get("categories") or []


def _apply_verified(verified: set[str]) -> None:
    VERIFIED_STABLECOINS.clear()
    VERIFIED_STABLECOINS.update(verified)


async def refresh_verification(force: bool = False) -> set[str]:
    """STABLECOINS 후보를 검증하여 VERIFIED_STABLECOINS 갱신."""
    if not force:
        cache = _load_cache()
        if cache:
            verified = set(cache["verified"])
            _apply_verified(verified)
            logger.info(f"[peg_verify] 캐시 사용: verified {len(verified)}개")
            return verified

    statuses: list[CoinStatus] = []
    verified: set[str] = set()

    # 매핑 없는 티커는 자동 통과 (수동 검토 대상)
    for ticker in STABLECOINS - set(COINGECKO_IDS.keys()):
        verified.add(ticker)
        statuses.append(CoinStatus(
            ticker=ticker, coingecko_id="", verified=True,
            reason="CoinGecko ID 매핑 없음 → 자동 통과",
        ))

    mapped = {t: COINGECKO_IDS[t] for t in STABLECOINS if t in COINGECKO_IDS}

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        ) as client:
            prices = await _fetch_prices(client, list(mapped.values()))

            for ticker, coin_id in mapped.items():
                status = CoinStatus(ticker=ticker, coingecko_id=coin_id)
                status.price_usd = prices.get(coin_id)

                try:
                    categories = await _fetch_categories(client, coin_id)
                    status.in_stable_category = any(
                        "stablecoin" in c.lower() for c in categories
                    )
                except Exception as e:
                    logger.warning(f"[peg_verify] {ticker} 카테고리 조회 실패: {e}")
                    status.reason = "카테고리 조회 실패 → 원본 유지"
                    status.verified = True
                    verified.add(ticker)
                    statuses.append(status)
                    await asyncio.sleep(0.5)
                    continue

                if status.price_usd is None:
                    status.reason = "가격 조회 실패 → 원본 유지"
                    status.verified = True
                    verified.add(ticker)
                elif not status.in_stable_category:
                    status.reason = "Stablecoins 카테고리 아님"
                    status.verified = False
                elif not (PEG_MIN <= status.price_usd <= PEG_MAX):
                    status.reason = f"페그 이탈 (${status.price_usd:.4f})"
                    status.verified = False
                else:
                    status.reason = f"통과 (${status.price_usd:.4f})"
                    status.verified = True
                    verified.add(ticker)

                statuses.append(status)
                await asyncio.sleep(0.5)  # CoinGecko free tier rate limit

    except Exception as e:
        logger.error(f"[peg_verify] 검증 실패: {e} → 원본 STABLECOINS 사용")
        _apply_verified(set(STABLECOINS))
        return set(STABLECOINS)

    _apply_verified(verified)
    excluded = STABLECOINS - verified
    if excluded:
        logger.warning(f"[peg_verify] 검증 실패 제외: {sorted(excluded)}")
    logger.info(
        f"[peg_verify] verified {len(verified)}개 / 후보 {len(STABLECOINS)}개"
    )

    try:
        _save_cache(verified, statuses)
    except OSError as e:
        logger.warning(f"[peg_verify] 캐시 저장 실패: {e}")

    return verified
