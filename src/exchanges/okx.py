from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# OKX Finance API (v5)
# Savings(lending-rate-summary) = 공개 API (키 불필요)
# Staking(staking-defi/offers) = 인증 필요


class OkxExchange(BaseExchange):
    name = "okx"
    base_url = "https://www.okx.com"

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        sign_str = f"{ts}{method.upper()}{path}{body}"
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), sign_str.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    async def fetch_products(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        # 공개 API - 항상 사용 가능
        products.extend(await self._fetch_savings())
        # 인증 API - 키가 있을 때만
        products.extend(await self._fetch_staking())
        return products

    async def _fetch_savings(self) -> list[EarnProduct]:
        """공개 API - 인증 불필요"""
        products: list[EarnProduct] = []
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/api/v5/finance/savings/lending-rate-summary")
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != "0":
                logger.warning(f"[okx] Savings: code={data.get('code')}")
                return []

            for item in data.get("data", []):
                ccy = item.get("ccy", "")
                if not self._is_stablecoin(ccy):
                    continue

                apr = float(item.get("avgRate", item.get("estRate", 0)) or 0)
                if 0 < apr < 1:
                    apr *= 100

                product = EarnProduct(
                    exchange=self.name,
                    product_id=f"okx_savings_{ccy}",
                    coin=ccy,
                    product_name=f"{ccy} Simple Earn",
                    product_type=ProductType.FLEXIBLE,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE,
                    duration_days=0,
                    url="https://www.okx.com/earn/simple-earn",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[okx] Savings 조회 실패: {e}")
        return products

    async def _fetch_staking(self) -> list[EarnProduct]:
        """인증 API - 키가 있을 때만"""
        if not self.has_credentials:
            logger.info("[okx] Staking/DeFi: API 키 필요 - Savings만 조회")
            return []

        products: list[EarnProduct] = []
        try:
            path = "/api/v5/finance/staking-defi/offers"
            headers = self._sign_headers("GET", path)
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}{path}", headers=headers)
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != "0":
                logger.warning(f"[okx] Staking: code={data.get('code')}")
                return []

            for item in data.get("data", []):
                ccy = item.get("ccy", item.get("investCcy", ""))
                if not self._is_stablecoin(ccy):
                    continue

                apr = float(item.get("rate", item.get("apy", 0)) or 0)
                if 0 < apr < 1:
                    apr *= 100

                duration = int(item.get("term", item.get("lockPeriod", 0)) or 0)

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("productId", item.get("offerId", ""))),
                    coin=ccy,
                    product_name=item.get("productName", item.get("offerName", f"{ccy} Staking")),
                    product_type=ProductType.LOCKED if duration > 0 else ProductType.STAKING,
                    apr=round(apr, 2),
                    apr_type=AprType.FIXED if duration > 0 else AprType.VARIABLE,
                    duration_days=duration,
                    min_amount=float(item.get("minAmt", item.get("minInvestAmt", 0)) or 0),
                    max_amount=float(item.get("maxAmt", item.get("maxInvestAmt", 0)) or 0),
                    total_quota=float(item.get("totalAmount", 0) or 0),
                    remaining_quota=float(item.get("availableAmount", 0) or 0),
                    is_limited=bool(item.get("totalAmount")),
                    url="https://www.okx.com/earn/staking",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[okx] Staking 조회 실패: {e}")
        return products
