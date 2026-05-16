"""스테이블코인 페그 자동 검증

CoinGecko API로 STABLECOINS 후보 각각을 검증:
  1) `Stablecoins` 카테고리에 등록되어 있는가
  2) 시가가 $0.95 ~ $1.05 범위인가

두 조건을 모두 만족해야 verified 집합에 포함. 결과는 24시간 캐시.
API 호출 실패 시 원본 STABLECOINS 유지 (안전 폴백).
"""

from __future__ import annotations

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
    "MKUSD": "prisma-mkusd",
    "SUSD": "nusd",
    "USD0": "usual-usd",
    "EUSD": "electronic-usd",
    "USDB": "usdb",
    # CUSD = USDM (Celo Dollar는 Mento Dollar로 리브랜딩됨, 같은 CoinGecko ID 공유)
    "CUSD": "celo-dollar",
    "USDM": "celo-dollar",
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


async def _fetch_stablecoin_market(
    client: httpx.AsyncClient,
) -> dict[str, float | None]:
    """Stablecoins 카테고리 + 가격을 한 번에 일괄 조회.

    /coins/markets?category=stablecoins 로 page 1+2 (~500개) 수집.
    개별 카테고리 호출 제거로 rate limit 회피.

    반환: {coingecko_id: price_usd}
    """
    result: dict[str, float | None] = {}
    for page in (1, 2):
        resp = await client.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "category": "stablecoins",
                "per_page": 250,
                "page": page,
            },
        )
        resp.raise_for_status()
        for item in resp.json():
            cid = item.get("id")
            if cid:
                result[cid] = item.get("current_price")
    return result


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
            market = await _fetch_stablecoin_market(client)

            for ticker, coin_id in mapped.items():
                status = CoinStatus(ticker=ticker, coingecko_id=coin_id)

                if coin_id in market:
                    status.in_stable_category = True
                    status.price_usd = market[coin_id]
                else:
                    status.in_stable_category = False
                    status.price_usd = None

                if not status.in_stable_category:
                    status.reason = "Stablecoins 카테고리 아님"
                    status.verified = False
                elif status.price_usd is None:
                    status.reason = "가격 조회 실패 → 원본 유지"
                    status.verified = True
                    verified.add(ticker)
                elif not (PEG_MIN <= status.price_usd <= PEG_MAX):
                    status.reason = f"페그 이탈 (${status.price_usd:.4f})"
                    status.verified = False
                else:
                    status.reason = f"통과 (${status.price_usd:.4f})"
                    status.verified = True
                    verified.add(ticker)

                statuses.append(status)

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
