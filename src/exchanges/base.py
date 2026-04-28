from __future__ import annotations

import hashlib
import hmac
import logging
import time
from abc import ABC, abstractmethod

import httpx

from src.config import EXCHANGE_KEYS
from src.models import EarnProduct
from src.peg_verify import VERIFIED_STABLECOINS

logger = logging.getLogger(__name__)


class BaseExchange(ABC):
    """거래소 베이스 클래스"""

    name: str = ""
    base_url: str = ""

    def __init__(self) -> None:
        keys = EXCHANGE_KEYS.get(self.name, {})
        self.api_key: str = keys.get("api_key", "")
        self.api_secret: str = keys.get("api_secret", "")
        self.passphrase: str = keys.get("passphrase", "")
        self._client: httpx.AsyncClient | None = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _hmac_sha256(self, message: str) -> str:
        return hmac.new(
            self.api_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()

    def _timestamp_ms(self) -> int:
        return int(time.time() * 1000)

    def _is_stablecoin(self, coin: str) -> bool:
        return coin.upper() in VERIFIED_STABLECOINS

    @abstractmethod
    async def fetch_products(self) -> list[EarnProduct]:
        """거래소의 스테이블코인 Earn 상품 목록을 가져옵니다."""
        ...

    async def safe_fetch(self) -> list[EarnProduct]:
        """에러 핸들링 포함 fetch"""
        try:
            products = await self.fetch_products()
            logger.info(f"[{self.name}] {len(products)}개 상품 조회 완료")
            return products
        except Exception as e:
            logger.error(f"[{self.name}] 조회 실패: {e}")
            return []
        finally:
            await self.close()
