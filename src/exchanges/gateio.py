from __future__ import annotations

import hashlib
import hmac
import logging
import time

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# Gate.io Earn API (v4)
# 공개 API는 현재 금리를 제공하지 않음 (min/max 범위만)
# 정확한 금리 조회를 위해 API 키 필요


class GateioExchange(BaseExchange):
    name = "gateio"
    base_url = "https://api.gateio.ws"

    def _sign_headers(self, method: str, path: str, query: str = "", body: str = "") -> dict[str, str]:
        ts = str(int(time.time()))
        body_hash = hashlib.sha512(body.encode()).hexdigest()
        sign_str = f"{method}\n{path}\n{query}\n{body_hash}\n{ts}"
        signature = hmac.new(
            self.api_secret.encode(), sign_str.encode(), hashlib.sha512
        ).hexdigest()
        return {
            "KEY": self.api_key,
            "SIGN": signature,
            "Timestamp": ts,
            "Content-Type": "application/json",
        }

    async def fetch_products(self) -> list[EarnProduct]:
        if not self.has_credentials:
            logger.info("[gateio] API 키 필요 - 건너뜀 (공개 API로 정확한 금리 조회 불가)")
            return []

        products: list[EarnProduct] = []
        products.extend(await self._fetch_uni_products())
        products.extend(await self._fetch_structured())
        return products

    async def _fetch_uni_products(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        try:
            path = "/api/v4/earn/uni/currencies"
            headers = self._sign_headers("GET", path)
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}{path}", headers=headers)
            resp.raise_for_status()
            data = resp.json()

            items = data if isinstance(data, list) else data.get("list", [])
            for item in items:
                currency = item.get("currency", "")
                if not self._is_stablecoin(currency):
                    continue

                apr = self._parse_apr(item)

                product = EarnProduct(
                    exchange=self.name,
                    product_id=f"gate_uni_{currency}",
                    coin=currency,
                    product_name=f"{currency} HODL & Earn",
                    product_type=ProductType.FLEXIBLE,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE,
                    duration_days=0,
                    min_amount=float(item.get("min_lend_amount", 0) or 0),
                    max_amount=float(item.get("max_lend_amount", 0) or 0),
                    url="https://www.gate.io/hodl",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[gateio] Uni 조회 실패: {e}")
        return products

    async def _fetch_structured(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        try:
            path = "/api/v4/earn/structured/products"
            headers = self._sign_headers("GET", path)
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}{path}", headers=headers)

            if resp.status_code in (401, 403, 404):
                return []

            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else data.get("list", [])

            for item in items:
                currency = item.get("currency", item.get("investCcy", ""))
                if not self._is_stablecoin(currency):
                    continue

                apr = self._parse_apr(item)
                duration = int(item.get("lock_period", item.get("duration", 0)) or 0)
                total = float(item.get("total_amount", 0) or 0)
                remain = float(item.get("remaining_amount", 0) or 0)

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("id", item.get("productId", ""))),
                    coin=currency,
                    product_name=item.get("name", f"{currency} 구조화 상품"),
                    product_type=ProductType.STRUCTURED,
                    apr=round(apr, 2),
                    apr_type=AprType.FIXED,
                    duration_days=duration,
                    min_amount=float(item.get("min_amount", 0) or 0),
                    total_quota=total,
                    remaining_quota=remain,
                    is_limited=total > 0,
                    is_sold_out=remain == 0 and total > 0,
                    url="https://www.gate.io/structured-products",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[gateio] 구조화 상품 조회 실패: {e}")
        return products

    def _parse_apr(self, item: dict) -> float:
        for key in ["interest_rate", "rate", "apy", "apr"]:
            val = item.get(key)
            if val is not None:
                val_str = str(val).strip()
                if "%" in val_str:
                    return float(val_str.replace("%", ""))
                apr = float(val_str)
                if 0 < apr < 0.5:
                    apr *= 100
                return apr
        return 0.0
