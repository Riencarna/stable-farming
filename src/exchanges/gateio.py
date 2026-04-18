from __future__ import annotations

import logging

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# Gate.io Earn API (v4) - 공개 엔드포인트 (API 키 불필요!)
# GET /api/v4/earn/uni/rate → 현재 예상 연이율
# GET /api/v4/earn/structured/products → 구조화 상품 APR 범위


class GateioExchange(BaseExchange):
    name = "gateio"
    base_url = "https://api.gateio.ws"

    async def fetch_products(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        products.extend(await self._fetch_uni_rates())
        products.extend(await self._fetch_structured())
        return products

    async def _fetch_uni_rates(self) -> list[EarnProduct]:
        """HODL & Earn 현재 이율 (공개 API)"""
        products: list[EarnProduct] = []
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/api/v4/earn/uni/rate")
            resp.raise_for_status()
            data = resp.json()

            items = data if isinstance(data, list) else []
            for item in items:
                currency = item.get("currency", "")
                if not self._is_stablecoin(currency):
                    continue

                # est_rate는 소수 형태 (0.0084 = 0.84%)
                est_rate = float(item.get("est_rate", 0) or 0)
                apr = est_rate * 100

                product = EarnProduct(
                    exchange=self.name,
                    product_id=f"gate_uni_{currency}",
                    coin=currency,
                    product_name=f"{currency} HODL & Earn",
                    product_type=ProductType.FLEXIBLE,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE,
                    duration_days=0,
                    url="https://www.gate.io/hodl",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[gateio] Uni Rate 조회 실패: {e}")
        return products

    async def _fetch_structured(self) -> list[EarnProduct]:
        """구조화 상품 (SharkFin 등) - 공개 API"""
        products: list[EarnProduct] = []
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/api/v4/earn/structured/products")

            if resp.status_code in (401, 403, 404):
                return []

            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else []

            for item in items:
                currency = item.get("investment_coin", item.get("currency", ""))
                if not self._is_stablecoin(currency):
                    continue

                # 구조화 상품은 min/max APR 범위
                max_apr = float(item.get("max_annual_rate", 0) or 0)
                min_apr = float(item.get("min_annual_rate", 0) or 0)
                # 소수 형태일 경우 변환
                if 0 < max_apr < 1:
                    max_apr *= 100
                if 0 < min_apr < 1:
                    min_apr *= 100

                duration = int(item.get("investment_period", 0) or 0)
                status = item.get("status", "")

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("id", "")),
                    coin=currency,
                    product_name=item.get("name_en", f"{currency} 구조화 상품"),
                    product_type=ProductType.STRUCTURED,
                    apr=round(max_apr, 2),
                    apr_type=AprType.FIXED,
                    duration_days=duration,
                    is_sold_out=status != "in_process",
                    url="https://www.gate.io/structured-products",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[gateio] 구조화 상품 조회 실패: {e}")
        return products
