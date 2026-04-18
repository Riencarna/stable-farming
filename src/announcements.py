"""
거래소 공지사항에서 스테이블코인 Earn/프로모션 특판 추적

- Binance: /bapi/composite/v1/public/cms/article/catalog/list/query (catalogId=93)
- Bybit: /v5/announcements/index
- OKX: /api/v5/support/announcements
- KuCoin: /_api/cms/articles

모든 엔드포인트는 공개 API (API 키 불필요)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.config import DATA_DIR, STABLECOINS
from src.notifier import send_telegram

logger = logging.getLogger(__name__)

SEEN_ANNOUNCEMENTS_PATH = DATA_DIR / "seen_announcements.json"

# Earn/수익 관련 키워드
EARN_KEYWORDS = [
    "earn", "apr", "apy", "yield", "staking", "savings",
    "simple earn", "locked", "flexible", "promotion",
    "special", "limited", "bonus", "reward",
    "subscribe", "deposit", "interest",
]

# 스테이블코인 키워드 (소문자)
STABLE_KEYWORDS = [s.lower() for s in STABLECOINS] + ["stablecoin", "stable coin"]

# --- 필터링: 제외 대상 ---

# 1) 지역 제한 이벤트 (한국 사용자에게 해당 안 되는 지역)
EXCLUDED_REGIONS = [
    "south asia", "southeast asia", "latam", "latin america",
    "balkans", "balkan", "africa", "middle east", "mena",
    "cis region", "cis countries", "central asia",
    "india", "pakistan", "bangladesh", "sri lanka",
    "brazil", "mexico", "argentina", "colombia",
    "nigeria", "turkey", "türkiye",
    "indonesia", "philippines", "vietnam", "thailand",
    "russia", "ukraine", "kazakhstan",
]

# 2) Earn/예치와 무관한 주제
IRRELEVANT_KEYWORDS = [
    "p2p", "kyb", "kyc", "merchant",
    "trading competition", "trade competition", "trading challenge",
    "futures", "perpetual", "margin", "leverage",
    "listing", "delist", "list ",  # "list " 뒤 공백으로 "listing"과 구분
    "airdrop", "launchpad", "launchpool",
    "maintenance", "upgrade", "suspension",
    "copy trading", "copy trade", "bot",
    "referral", "affiliate",
    "convert", "swap",
    "nft", "web3",
]

# 3) 비스테이블코인 자산 (이 코인의 예치 이벤트는 제외)
NON_STABLE_ASSETS = [
    "btc", "bitcoin", "eth", "ethereum",
    "xrp", "xrpfi", "sol", "solana",
    "bnb", "ada", "cardano", "dot", "polkadot",
    "avax", "avalanche", "matic", "polygon",
    "link", "chainlink", "atom", "cosmos",
    "doge", "dogecoin", "shib", "shiba",
    "ton", "near", "apt", "aptos", "sui",
    "trx", "tron", "ltc", "litecoin",
    "fil", "filecoin", "arb", "arbitrum",
    "op", "optimism", "sei", "manta",
]


@dataclass
class Announcement:
    exchange: str
    title: str
    url: str
    ann_id: str
    published_at: str = ""
    category: str = ""

    @property
    def unique_key(self) -> str:
        return f"{self.exchange}:{self.ann_id}"

    def is_earn_related(self) -> bool:
        """Earn/수익 관련 공지인지 확인"""
        text = f"{self.title} {self.category}".lower()
        return any(kw in text for kw in EARN_KEYWORDS)

    def mentions_stablecoin(self) -> bool:
        """스테이블코인 언급 여부"""
        text = self.title.lower()
        return any(kw in text for kw in STABLE_KEYWORDS)

    def extract_apr(self) -> str | None:
        """공지 제목에서 APR/APY 수치 추출"""
        match = re.search(r'(\d+(?:\.\d+)?)\s*%?\s*(?:APR|APY|apr|apy)', self.title)
        if match:
            return f"{match.group(1)}%"
        return None

    def is_region_restricted(self) -> bool:
        """한국 사용자에게 해당되지 않는 지역 한정 이벤트인지 확인"""
        text = self.title.lower()
        return any(region in text for region in EXCLUDED_REGIONS)

    def is_irrelevant_topic(self) -> bool:
        """Earn/예치와 무관한 주제인지 확인"""
        text = f"{self.title} {self.category}".lower()
        return any(kw in text for kw in IRRELEVANT_KEYWORDS)

    def mentions_non_stable_asset(self) -> bool:
        """비스테이블코인 자산의 예치 이벤트인지 확인
        (스테이블코인도 함께 언급하면 통과)"""
        text = self.title.lower()
        has_non_stable = any(asset in text for asset in NON_STABLE_ASSETS)
        has_stable = self.mentions_stablecoin()
        # 비스테이블코인만 언급하고 스테이블코인은 없으면 제외
        return has_non_stable and not has_stable


class AnnouncementStore:
    """이미 알림 보낸 공지 추적"""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._load()

    def _load(self) -> None:
        if SEEN_ANNOUNCEMENTS_PATH.exists():
            try:
                data = json.loads(SEEN_ANNOUNCEMENTS_PATH.read_text(encoding="utf-8"))
                self._seen = set(data)
            except (json.JSONDecodeError, OSError):
                self._seen = set()

    def save(self) -> None:
        # 최근 500개만 유지
        items = list(self._seen)[-500:]
        SEEN_ANNOUNCEMENTS_PATH.write_text(
            json.dumps(items, ensure_ascii=False),
            encoding="utf-8",
        )

    def is_new(self, ann: Announcement) -> bool:
        return ann.unique_key not in self._seen

    def mark_seen(self, ann: Announcement) -> None:
        self._seen.add(ann.unique_key)


def _exchange_emoji(exchange: str) -> str:
    emojis = {
        "binance": "\U0001f7e1",
        "bybit": "\U0001f7e0",
        "okx": "\u26aa",
        "kucoin": "\U0001f7e2",
    }
    return emojis.get(exchange, "\u26aa")


def format_announcement_message(ann: Announcement) -> str:
    """공지사항 텔레그램 알림 메시지"""
    emoji = _exchange_emoji(ann.exchange)
    apr_str = ann.extract_apr()

    lines = [
        f"\U0001f4e2 <b>특판 공지 발견!</b>",
        "",
        f"{emoji} <b>거래소:</b> {ann.exchange.upper()}",
        f"\U0001f4cb <b>제목:</b> {ann.title}",
    ]

    if apr_str:
        lines.append(f"\U0001f4c8 <b>APR:</b> {apr_str}")

    if ann.category:
        lines.append(f"\U0001f3f7 <b>카테고리:</b> {ann.category}")

    if ann.url:
        lines.append(f'\n\U0001f517 <a href="{ann.url}">공지 바로가기</a>')

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines.append(f"\n\U0001f552 {now_str}")

    return "\n".join(lines)


async def _fetch_binance(client: httpx.AsyncClient) -> list[Announcement]:
    """Binance Latest Activities 공지"""
    announcements: list[Announcement] = []
    try:
        resp = await client.get(
            "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query",
            params={"catalogId": 93, "pageNo": 1, "pageSize": 20},
        )
        resp.raise_for_status()
        data = resp.json()

        for article in data.get("data", {}).get("articles", []):
            ann = Announcement(
                exchange="binance",
                title=article.get("title", ""),
                url=f"https://www.binance.com/en/support/announcement/{article.get('code', '')}",
                ann_id=str(article.get("code", article.get("id", ""))),
                published_at=str(article.get("releaseDate", "")),
                category="Latest Activities",
            )
            announcements.append(ann)
    except Exception as e:
        logger.error(f"[announcements] Binance 공지 조회 실패: {e}")
    return announcements


async def _fetch_bybit(client: httpx.AsyncClient) -> list[Announcement]:
    """Bybit 공지"""
    announcements: list[Announcement] = []
    try:
        resp = await client.get(
            "https://api.bybit.com/v5/announcements/index",
            params={"locale": "en-US", "limit": 20},
        )
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("result", {}).get("list", []):
            category = item.get("type", {}).get("title", "")
            ann = Announcement(
                exchange="bybit",
                title=item.get("title", ""),
                url=item.get("url", ""),
                ann_id=str(item.get("id", item.get("annId", ""))),
                published_at=str(item.get("publishTime", item.get("dateTimestamp", ""))),
                category=category,
            )
            announcements.append(ann)
    except Exception as e:
        logger.error(f"[announcements] Bybit 공지 조회 실패: {e}")
    return announcements


async def _fetch_okx(client: httpx.AsyncClient) -> list[Announcement]:
    """OKX 공지"""
    announcements: list[Announcement] = []
    try:
        resp = await client.get(
            "https://www.okx.com/api/v5/support/announcements",
            params={"page": "1", "limit": "20"},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "0":
            return []

        for item in data.get("data", []):
            # data가 리스트 안에 리스트인 경우 처리
            if isinstance(item, list):
                for sub in item:
                    ann = Announcement(
                        exchange="okx",
                        title=sub.get("title", sub.get("annTitle", "")),
                        url=sub.get("url", sub.get("annUrl", "")),
                        ann_id=str(sub.get("id", sub.get("annId", ""))),
                        published_at=str(sub.get("pTime", "")),
                        category=sub.get("annType", ""),
                    )
                    announcements.append(ann)
            elif isinstance(item, dict):
                ann = Announcement(
                    exchange="okx",
                    title=item.get("title", item.get("annTitle", "")),
                    url=item.get("url", item.get("annUrl", "")),
                    ann_id=str(item.get("id", item.get("annId", ""))),
                    published_at=str(item.get("pTime", "")),
                    category=item.get("annType", ""),
                )
                announcements.append(ann)
    except Exception as e:
        logger.error(f"[announcements] OKX 공지 조회 실패: {e}")
    return announcements


async def _fetch_kucoin(client: httpx.AsyncClient) -> list[Announcement]:
    """KuCoin 공지"""
    announcements: list[Announcement] = []
    try:
        resp = await client.get(
            "https://www.kucoin.com/_api/cms/articles",
            params={"page": 1, "pageSize": 20, "lang": "en_US"},
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items and isinstance(data.get("data"), dict):
            items = data["data"].get("items", [])

        for item in items:
            ann = Announcement(
                exchange="kucoin",
                title=item.get("title", ""),
                url=item.get("path", item.get("url", "")),
                ann_id=str(item.get("id", "")),
                published_at=str(item.get("publishTime", "")),
                category=item.get("category", ""),
            )
            if ann.url and not ann.url.startswith("http"):
                ann.url = f"https://www.kucoin.com/announcement{ann.url}"
            announcements.append(ann)
    except Exception as e:
        logger.error(f"[announcements] KuCoin 공지 조회 실패: {e}")
    return announcements


async def scan_announcements() -> int:
    """
    모든 거래소 공지사항 스캔 → Earn/스테이블코인 관련 신규 공지 알림

    반환: 신규 알림 수
    """
    store = AnnouncementStore()
    new_count = 0

    async with httpx.AsyncClient(
        timeout=15.0,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        follow_redirects=True,
    ) as client:
        # 모든 거래소 공지 수집
        import asyncio
        results = await asyncio.gather(
            _fetch_binance(client),
            _fetch_bybit(client),
            _fetch_okx(client),
            _fetch_kucoin(client),
            return_exceptions=True,
        )

        all_announcements: list[Announcement] = []
        for result in results:
            if isinstance(result, list):
                all_announcements.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"[announcements] 조회 에러: {result}")

        logger.info(f"[announcements] 전체 {len(all_announcements)}개 공지 수집")

        # 필터링 (4단계)
        relevant: list[Announcement] = []
        for ann in all_announcements:
            # 1단계: Earn 관련 키워드 포함 필수
            if not ann.is_earn_related():
                continue
            # 2단계: 지역 제한 이벤트 제외
            if ann.is_region_restricted():
                logger.debug(f"[announcements] 지역 제외: {ann.title}")
                continue
            # 3단계: Earn과 무관한 주제 제외
            if ann.is_irrelevant_topic():
                logger.debug(f"[announcements] 주제 제외: {ann.title}")
                continue
            # 4단계: 비스테이블코인 전용 이벤트 제외
            if ann.mentions_non_stable_asset():
                logger.debug(f"[announcements] 비스테이블 제외: {ann.title}")
                continue
            # 통과: 스테이블코인 언급 또는 APR이 있는 일반 공지
            if ann.mentions_stablecoin() or ann.extract_apr():
                relevant.append(ann)

        logger.info(f"[announcements] 필터 통과: {len(relevant)}개")

        # 신규 공지만 알림
        for ann in relevant:
            if store.is_new(ann):
                msg = format_announcement_message(ann)
                if await send_telegram(msg):
                    new_count += 1
                store.mark_seen(ann)

    store.save()
    logger.info(f"[announcements] 신규 알림: {new_count}건")
    return new_count
