from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# KuCoin Earn API
# GET /api/v1/earn/saving/products  (Savings)
# GET /api/v1/earn/staking/products (Staking)
# GET /api/v1/earn/promotion/products (프로모션)
# 공개 시도 → 실패 시 인증 사용


class KucoinExchange(BaseExchange):
    name = "kucoin"
    base_url = "https://api.kucoin.com"

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        ts = str(self._timestamp_ms())
        sign_str = f"{ts}{method.upper()}{path}{body}"
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), sign_str.encode(), hashlib.sha256).digest()
        ).decode()
        passphrase_sign = base64.b64encode(
            hmac.new(
                self.api_secret.encode(), self.passphrase.encode(), hashlib.sha256
            ).digest()
        ).decode()
        return {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": signature,
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": passphrase_sign,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    async def _fetch_endpoint(self, path: str) -> dict | None:
        """공개 시도 → 인증 폴백"""
        client = await self._get_client()
        url = f"{self.base_url}{path}"

        # 1차: 인증 없이 시도
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "200000":
                return data

        # 2차: API 키가 있으면 인증 시도
        if self.has_credentials:
            headers = self._sign_headers("GET", path)
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "200000":
                    return data

        if resp.status_code in (401, 403):
            logger.info(f"[kucoin] {path}: 인증 필요 - API 키를 설정하세요")
        else:
            logger.warning(f"[kucoin] {path}: HTTP {resp.status_code} - {resp.text[:200]}")

        return None

    async def fetch_products(self) -> list[EarnProduct]:
        if not self.has_credentials:
            logger.info("[kucoin] API 키 필요 - 건너뜀 (공개 API 미지원)")
            return []

        products: list[EarnProduct] = []

        endpoints = {
            "SAVING": "/api/v1/earn/saving/products",
            "STAKING": "/api/v1/earn/staking/products",
            "PROMOTION": "/api/v1/earn/promotion/products",
        }

        for product_type, path in endpoints.items():
            products.extend(await self._fetch_type(product_type, path))

        return products

    async def _fetch_type(self, product_type: str, path: str) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        try:
            data = await self._fetch_endpoint(path)
            if not data:
                return []

            items = data.get("data", {})
            if isinstance(items, dict):
                items = items.get("items", items.get("list", []))
            if not isinstance(items, list):
                items = []

            for item in items:
                currency = item.get("currency", item.get("coin", ""))
                if not self._is_stablecoin(currency):
                    continue

                apr = self._parse_apr(item)
                duration = int(item.get("duration", item.get("lockPeriod", item.get("term", 0))) or 0)
                is_flexible = product_type == "SAVING" and duration == 0

                total_size = float(item.get("totalSize", item.get("totalAmount", 0)) or 0)
                remain_size = float(item.get("remainSize", item.get("remainAmount", 0)) or 0)

                ptype = ProductType.FLEXIBLE if is_flexible else ProductType.LOCKED
                if product_type == "STAKING":
                    ptype = ProductType.STAKING

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("id", item.get("productId", ""))),
                    coin=currency,
                    product_name=item.get("productName", f"{currency} {product_type}"),
                    product_type=ptype,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE if is_flexible else AprType.FIXED,
                    duration_days=duration,
                    min_amount=float(item.get("minInvestSize", item.get("minAmount", 0)) or 0),
                    max_amount=float(item.get("maxInvestSize", item.get("maxAmount", 0)) or 0),
                    total_quota=total_size,
                    remaining_quota=remain_size,
                    is_limited=total_size > 0,
                    is_sold_out=remain_size == 0 and total_size > 0,
                    url="https://www.kucoin.com/earn",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[kucoin] {product_type} 조회 실패: {e}")
        return products

    def _parse_apr(self, item: dict) -> float:
        for key in ["recentAnnualInterestRate", "apr", "apy", "interestRate", "annualRate"]:
            val = item.get(key)
            if val is not None:
                apr = float(val)
                if 0 < apr < 1:
                    apr *= 100
                return apr
        return 0.0
