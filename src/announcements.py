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
    "discount buy",
    "livestream", "live stream", "webinar",
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
class DealTerms:
    """공지 본문에서 추출한 특판 조건"""
    total_cap: str = ""       # 총 한도 (예: "1,000,000 USDT")
    per_user_max: str = ""    # 인당 최대 (예: "500 USDT")
    per_user_min: str = ""    # 인당 최소 (예: "10 USDT")
    period: str = ""          # 이벤트 기간 (예: "2026-04-15 ~ 2026-04-20")
    lock_period: str = ""     # 락업 (예: "Flexible" / "7 Days Locked")

    def has_any(self) -> bool:
        return any([
            self.total_cap, self.per_user_max, self.per_user_min,
            self.period, self.lock_period,
        ])


@dataclass
class Announcement:
    exchange: str
    title: str
    url: str
    ann_id: str
    published_at: str = ""
    category: str = ""
    _path: str = ""  # KuCoin 상세 API용 원본 경로

    @property
    def unique_key(self) -> str:
        return f"{self.exchange}:{self.ann_id}"

    def is_earn_related(self) -> bool:
        """Earn/수익 관련 공지인지 확인 (단어 경계 매칭)"""
        text = f"{self.title} {self.category}".lower()
        # "learn"이 "earn"에 매칭되는 것 방지 → 단어 경계(\b) 사용
        return any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in EARN_KEYWORDS)

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
        "htx": "\U0001f535",
    }
    return emojis.get(exchange, "\u26aa")


def _extract_deal_terms(content: str) -> DealTerms:
    """공지 본문에서 총 한도, 인당 최대/최소, 기간, 락업 추출

    각 거래소 공지 형식이 제각각이라 best-effort 방식.
    추출 실패 항목은 빈 문자열로 유지.
    """
    terms = DealTerms()
    # 개행 정규화
    text = re.sub(r"\s+", " ", content)

    # --- 락업 기간 ---
    # "7-Day Locked", "30 Days Fixed", "Flexible"
    lock_match = re.search(
        r"(\d+)[\s-]?days?\s+(?:locked|fixed|subscription)", text, re.I,
    )
    if lock_match:
        terms.lock_period = f"{lock_match.group(1)} Days Locked"
    elif re.search(r"\bflexible\b", text, re.I):
        terms.lock_period = "Flexible"

    # --- 총 한도 ---
    # "total reward pool of 1,000,000 USDT"
    # "total cap: 500,000 USDT"
    # "hard cap of 2,000,000 USDT"
    # "first-come, first-served... up to 10,000 USDT"
    cap_patterns = [
        r"total\s+(?:reward\s+)?(?:pool|cap|quota|amount|limit)(?:\s+of)?[:\s]+([\d,.]+)\s*([A-Z]{3,6})",
        r"hard\s+cap(?:\s+of)?[:\s]+([\d,.]+)\s*([A-Z]{3,6})",
        r"pool\s+size[:\s]+([\d,.]+)\s*([A-Z]{3,6})",
        r"capped\s+at\s+([\d,.]+)\s*([A-Z]{3,6})",
    ]
    for pat in cap_patterns:
        m = re.search(pat, text, re.I)
        if m:
            terms.total_cap = f"{m.group(1).rstrip('.')} {m.group(2).upper()}"
            break

    # --- 인당 최대 ---
    # "maximum subscription: 500 USDT per user"
    # "up to 2,000 USDT per account"
    # "max 1,000 USDT per participant"
    max_patterns = [
        r"max(?:imum)?\s+(?:subscription|deposit|subscribe|amount|purchase)[^.]{0,30}?([\d,.]+)\s*([A-Z]{3,6})",
        r"up\s+to\s+([\d,.]+)\s*([A-Z]{3,6})\s+per\s+(?:user|account|participant)",
        r"([\d,.]+)\s*([A-Z]{3,6})\s+per\s+(?:user|account|participant)",
    ]
    for pat in max_patterns:
        m = re.search(pat, text, re.I)
        if m:
            terms.per_user_max = f"{m.group(1).rstrip('.')} {m.group(2).upper()}"
            break

    # --- 인당 최소 ---
    min_patterns = [
        r"min(?:imum)?\s+(?:subscription|deposit|subscribe|amount|purchase)[^.]{0,30}?([\d,.]+)\s*([A-Z]{3,6})",
        r"starting\s+from\s+([\d,.]+)\s*([A-Z]{3,6})",
    ]
    for pat in min_patterns:
        m = re.search(pat, text, re.I)
        if m:
            terms.per_user_min = f"{m.group(1).rstrip('.')} {m.group(2).upper()}"
            break

    # --- 이벤트 기간 ---
    # "Event Period: 2026-04-15 to 2026-04-20"
    # "from 2026-04-15 (UTC) to 2026-04-20 (UTC)"
    period_patterns = [
        r"(?:event|promotion|campaign|activity)\s+period[:\s]+([^.\n]{10,120}?)(?:\.|$)",
        r"(from\s+\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[^.]{0,60}?to\s+\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[^.]{0,30}?)(?:\.|$)",
    ]
    for pat in period_patterns:
        m = re.search(pat, text, re.I)
        if m:
            period = m.group(1).strip().rstrip(",;")
            # 길면 잘라내기
            if len(period) > 80:
                period = period[:77] + "..."
            terms.period = period
            break

    return terms


def format_announcement_message(ann: Announcement, terms: DealTerms | None = None) -> str:
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

    # 특판 조건 추가
    if terms and terms.has_any():
        lines.append("")
        if terms.lock_period:
            lines.append(f"\u23f0 <b>유형:</b> {terms.lock_period}")
        if terms.total_cap:
            lines.append(f"\U0001f4e6 <b>총 한도:</b> {terms.total_cap}")
        if terms.per_user_min and terms.per_user_max:
            lines.append(f"\U0001f4b5 <b>인당:</b> {terms.per_user_min} ~ {terms.per_user_max}")
        elif terms.per_user_max:
            lines.append(f"\U0001f4b5 <b>인당 최대:</b> {terms.per_user_max}")
        elif terms.per_user_min:
            lines.append(f"\U0001f4b5 <b>인당 최소:</b> {terms.per_user_min}")
        if terms.period:
            lines.append(f"\U0001f4c5 <b>기간:</b> {terms.period}")

    if ann.url:
        lines.append(f'\n\U0001f517 <a href="{ann.url}">공지 바로가기</a>')

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines.append(f"\n\U0001f552 {now_str}")

    return "\n".join(lines)


# ============================================================
# 본문 조회 (2단계 검증용)
# ============================================================

def _extract_binance_ast_text(node: dict) -> list[str]:
    """Binance body JSON AST에서 텍스트 추출"""
    texts = []
    if node.get("node") == "text":
        texts.append(node.get("text", ""))
    for child in node.get("child", []):
        texts.extend(_extract_binance_ast_text(child))
    return texts


def _extract_bybit_text(children: list) -> list[str]:
    """Bybit __NEXT_DATA__ children에서 텍스트 추출"""
    texts = []
    for child in children:
        if isinstance(child, dict):
            if "text" in child:
                texts.append(child["text"])
            if "children" in child:
                texts.extend(_extract_bybit_text(child["children"]))
    return texts


def _strip_html(html: str) -> str:
    """HTML 태그 제거 → 순수 텍스트"""
    return re.sub(r"<[^>]+>", " ", html).strip()


async def _fetch_content(client: httpx.AsyncClient, ann: Announcement) -> str | None:
    """공지사항 본문 텍스트 가져오기 (거래소별 분기)"""
    try:
        if ann.exchange == "binance":
            return await _fetch_binance_content(client, ann)
        elif ann.exchange == "bybit":
            return await _fetch_bybit_content(client, ann)
        elif ann.exchange == "okx":
            return await _fetch_okx_content(client, ann)
        elif ann.exchange == "kucoin":
            return await _fetch_kucoin_content(client, ann)
        elif ann.exchange == "htx":
            return await _fetch_htx_content(client, ann)
    except Exception as e:
        logger.warning(f"[announcements] 본문 조회 실패 ({ann.exchange}): {e}")
    return None


async def _fetch_binance_content(client: httpx.AsyncClient, ann: Announcement) -> str | None:
    """Binance 상세 API → seoDesc + body AST"""
    resp = await client.get(
        "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query",
        params={"articleCode": ann.ann_id},
    )
    resp.raise_for_status()
    article = resp.json().get("data", {})

    parts = []
    seo = article.get("seoDesc", "")
    if seo:
        parts.append(seo)

    body = article.get("body")
    if body:
        body_ast = json.loads(body) if isinstance(body, str) else body
        parts.extend(_extract_binance_ast_text(body_ast))

    return " ".join(parts) if parts else None


async def _fetch_bybit_content(client: httpx.AsyncClient, ann: Announcement) -> str | None:
    """Bybit HTML → __NEXT_DATA__ JSON에서 본문 추출"""
    if not ann.url:
        return None
    resp = await client.get(ann.url)
    resp.raise_for_status()

    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL,
    )
    if not match:
        return None

    data = json.loads(match.group(1))
    detail = data.get("props", {}).get("pageProps", {}).get("articleDetail", {})
    content = detail.get("content", {})

    if isinstance(content, dict):
        children = content.get("json", {}).get("children", [])
        texts = _extract_bybit_text(children)
        return " ".join(texts) if texts else None
    return None


async def _fetch_okx_content(client: httpx.AsyncClient, ann: Announcement) -> str | None:
    """OKX HTML → <meta description> + <article> 태그"""
    if not ann.url:
        return None
    resp = await client.get(ann.url)
    resp.raise_for_status()

    parts = []
    meta = re.search(r'<meta name="description" content="([^"]*)"', resp.text)
    if meta:
        parts.append(meta.group(1))

    article = re.search(r"<article[^>]*>(.*?)</article>", resp.text, re.DOTALL)
    if article:
        parts.append(_strip_html(article.group(1)))

    return " ".join(parts) if parts else None


async def _fetch_htx_content(client: httpx.AsyncClient, ann: Announcement) -> str | None:
    """HTX SSR 페이지 → window.__NUXT__에서 본문 추출"""
    if not ann.url:
        return None
    resp = await client.get(ann.url)
    resp.raise_for_status()

    # window.__NUXT__ JSON에서 content 추출
    match = re.search(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>", resp.text, re.DOTALL)
    if match:
        try:
            nuxt_data = json.loads(match.group(1))
            # details.content 경로 탐색
            details = nuxt_data.get("data", [{}])[0].get("details", {})
            if not details:
                # 다른 구조일 수 있음
                for val in nuxt_data.get("data", []):
                    if isinstance(val, dict) and "details" in val:
                        details = val["details"]
                        break
            content_html = details.get("content", "")
            if content_html:
                return _strip_html(content_html)
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

    # 폴백: HTML에서 article content 추출
    article = re.search(r"<article[^>]*>(.*?)</article>", resp.text, re.DOTALL)
    if article:
        return _strip_html(article.group(1))

    return None


async def _fetch_kucoin_content(client: httpx.AsyncClient, ann: Announcement) -> str | None:
    """KuCoin 상세 API → content HTML"""
    # _path 원본 경로 사용 (예: /en-earn-wednesday-week-113-...)
    path_slug = ann._path.lstrip("/") if ann._path else ""
    if not path_slug and "/announcement/" in ann.url:
        path_slug = ann.url.split("/announcement/")[-1]

    if not path_slug:
        return None

    resp = await client.get(
        f"https://www.kucoin.com/_api/cms/articles/{path_slug}",
        params={"lang": "en_US"},
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    # 응답이 {data: {content: ...}} 또는 {content: ...} 형태
    content_html = data.get("content", "")
    if not content_html and isinstance(data.get("data"), dict):
        content_html = data["data"].get("content", "")
    return _strip_html(content_html) if content_html else None


def _content_has_stablecoin_yield(content: str) -> bool:
    """본문에 스테이블코인 + 수익률 언급이 있는지 확인"""
    text = content.lower()
    has_stable = any(s.lower() in text for s in STABLECOINS)
    if not has_stable:
        return False
    has_yield = bool(re.search(
        r"\d+(?:\.\d+)?\s*%?\s*(?:apr|apy|interest|yield|reward)", text,
    ))
    return has_yield


# ============================================================
# 거래소별 공지 목록 조회
# ============================================================

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


async def _fetch_htx(client: httpx.AsyncClient) -> list[Announcement]:
    """HTX Earn 카테고리 공지"""
    announcements: list[Announcement] = []
    try:
        # HTX Earn 카테고리: oneLevelId=54911014605677, twoLevelId=74935418929230
        resp = await client.get(
            "https://www.htx.com/-/x/support/public/getList/v2",
            params={
                "page": 1,
                "limit": 20,
                "oneLevelId": "54911014605677",
                "twoLevelId": "74935418929230",
                "language": "en-us",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 200:
            return []

        for item in data.get("data", {}).get("list", []):
            ann = Announcement(
                exchange="htx",
                title=item.get("title", ""),
                url=f"https://www.htx.com/support/en-us/detail/{item.get('id', '')}",
                ann_id=str(item.get("id", "")),
                published_at=item.get("showTime", ""),
                category="HTX Earn",
            )
            announcements.append(ann)
    except Exception as e:
        logger.error(f"[announcements] HTX 공지 조회 실패: {e}")
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
            raw_path = item.get("path", item.get("url", ""))
            ann = Announcement(
                exchange="kucoin",
                title=item.get("title", ""),
                url=raw_path,
                ann_id=str(item.get("id", "")),
                published_at=str(item.get("publishTime", "")),
                category=item.get("category", ""),
                _path=raw_path,  # 상세 API용 원본 경로 보존
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
        verify=False,  # HTX SSL 인증서 문제 대응
    ) as client:
        # 모든 거래소 공지 수집
        import asyncio
        results = await asyncio.gather(
            _fetch_binance(client),
            _fetch_bybit(client),
            _fetch_okx(client),
            _fetch_kucoin(client),
            _fetch_htx(client),
            return_exceptions=True,
        )

        all_announcements: list[Announcement] = []
        for result in results:
            if isinstance(result, list):
                all_announcements.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"[announcements] 조회 에러: {result}")

        logger.info(f"[announcements] 전체 {len(all_announcements)}개 공지 수집")

        # === 1차: 제목 기반 필터링 ===
        confirmed: list[Announcement] = []
        needs_verification: list[Announcement] = []

        for ann in all_announcements:
            if not ann.is_earn_related():
                continue
            if ann.is_region_restricted():
                continue
            if ann.is_irrelevant_topic():
                continue
            if ann.mentions_non_stable_asset():
                continue

            if ann.mentions_stablecoin():
                # 제목에 스테이블코인 명시 → 확정
                confirmed.append(ann)
            elif ann.extract_apr():
                # APR은 있지만 스테이블코인 미언급 → 본문 검증 필요
                needs_verification.append(ann)

        logger.info(
            f"[announcements] 제목 필터: 확정 {len(confirmed)}개, "
            f"검증 필요 {len(needs_verification)}개"
        )

        # === 2차: 본문 기반 검증 (신규 공지만) ===
        # 본문은 한 번만 받아서 검증과 terms 추출에 모두 사용
        content_cache: dict[str, str] = {}

        for ann in needs_verification:
            if not store.is_new(ann):
                store.mark_seen(ann)
                continue
            content = await _fetch_content(client, ann)
            if content and _content_has_stablecoin_yield(content):
                confirmed.append(ann)
                content_cache[ann.unique_key] = content
                logger.info(f"[announcements] 본문 확인 → 스테이블 특판: {ann.title}")
            else:
                logger.debug(f"[announcements] 본문 확인 → 비해당: {ann.title}")
                store.mark_seen(ann)

        # === 신규 공지 알림 전송 ===
        for ann in confirmed:
            if not store.is_new(ann):
                continue
            # 본문 기반 terms 추출 (캐시 우선, 없으면 조회)
            content = content_cache.get(ann.unique_key)
            if content is None:
                content = await _fetch_content(client, ann) or ""
            terms = _extract_deal_terms(content) if content else DealTerms()

            msg = format_announcement_message(ann, terms)
            if await send_telegram(msg):
                new_count += 1
            store.mark_seen(ann)

    store.save()
    logger.info(f"[announcements] 신규 알림: {new_count}건")
    return new_count
