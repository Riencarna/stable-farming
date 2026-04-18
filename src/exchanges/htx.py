from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# HTX (Huobi) Earn API
# 공개 시도 → 실패 시 인증 사용


class HtxExchange(BaseExchange):
    name = "htx"
    base_url = "https://api.huobi.pro"

    def _sign_params(self, method: str, path: str, params: dict) -> dict:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        params.update({
            "AccessKeyId": self.api_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": ts,
        })
        sorted_params = sorted(params.items())
        query = urlencode(sorted_params)
        host = "api.huobi.pro"
        sign_str = f"{method}\n{host}\n{path}\n{query}"
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), sign_str.encode(), hashlib.sha256).digest()
        ).decode()
        params["Signature"] = signature
        return params

    async def _fetch_endpoint(self, path: str) -> dict | None:
        """공개 시도 → 인증 폴백"""
        client = await self._get_client()
        url = f"{self.base_url}{path}"

        # 1차: 인증 없이 시도
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 200 or data.get("status") == "ok":
                return data

        # 2차: API 키가 있으면 인증 시도
        if self.has_credentials:
            params = self._sign_params("GET", path, {})
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200 or data.get("status") == "ok":
                    return data

        if resp.status_code in (401, 403):
            logger.info(f"[htx] {path}: 인증 필요 - API 키를 설정하세요")
        else:
            logger.warning(f"[htx] {path}: HTTP {resp.status_code}")

        return None

    async def fetch_products(self) -> list[EarnProduct]:
        if not self.has_credentials:
            logger.info("[htx] API 키 필요 - 건너뜀 (공개 API 미지원)")
            return []

        products: list[EarnProduct] = []
        products.extend(await self._fetch_savings())
        products.extend(await self._fetch_defi())
        return products

    async def _fetch_savings(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        try:
            data = await self._fetch_endpoint("/v2/earn/saving/project/list")
            if not data:
                return []

            for item in data.get("data", []):
                currency = item.get("currency", item.get("coin", "")).upper()
                if not self._is_stablecoin(currency):
                    continue

                apr = self._parse_apr(item)
                duration = int(item.get("lockPeriod", item.get("duration", 0)) or 0)
                total = float(item.get("totalSize", item.get("totalAmount", 0)) or 0)
                remain = float(item.get("remainSize", item.get("remainAmount", 0)) or 0)

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("id", item.get("productId", ""))),
                    coin=currency,
                    product_name=item.get("productName", f"{currency} Savings"),
                    product_type=ProductType.FLEXIBLE if duration == 0 else ProductType.LOCKED,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE if duration == 0 else AprType.FIXED,
                    duration_days=duration,
                    min_amount=float(item.get("minAmount", 0) or 0),
                    max_amount=float(item.get("maxAmount", 0) or 0),
                    total_quota=total,
                    remaining_quota=remain,
                    is_limited=total > 0,
                    is_sold_out=remain == 0 and total > 0,
                    url="https://www.htx.com/en-us/earn/",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[htx] Savings 조회 실패: {e}")
        return products

    async def _fetch_defi(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        try:
            data = await self._fetch_endpoint("/v2/earn/defi/project/list")
            if not data:
                return []

            for item in data.get("data", []):
                currency = item.get("currency", "").upper()
                if not self._is_stablecoin(currency):
                    continue

                apr = self._parse_apr(item)
                duration = int(item.get("lockPeriod", item.get("duration", 0)) or 0)
                total = float(item.get("totalSize", 0) or 0)
                remain = float(item.get("remainSize", 0) or 0)

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("id", item.get("productId", ""))),
                    coin=currency,
                    product_name=item.get("productName", f"{currency} DeFi"),
                    product_type=ProductType.STAKING,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE,
                    duration_days=duration,
                    min_amount=float(item.get("minAmount", 0) or 0),
                    total_quota=total,
                    remaining_quota=remain,
                    is_limited=total > 0,
                    url="https://www.htx.com/en-us/earn/",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[htx] DeFi 조회 실패: {e}")
        return products

    def _parse_apr(self, item: dict) -> float:
        for key in ["annualRate", "apr", "apy", "interestRate"]:
            val = item.get(key)
            if val is not None:
                apr = float(val)
                if 0 < apr < 1:
                    apr *= 100
                return apr
        return 0.0
