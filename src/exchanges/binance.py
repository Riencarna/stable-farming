from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import urlencode

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# Binance Simple Earn API (인증 필요)
# Docs: https://developers.binance.com/docs/simple_earn


class BinanceExchange(BaseExchange):
    name = "binance"
    base_url = "https://api.binance.com"

    def _sign_params(self, params: dict) -> dict:
        params["timestamp"] = self._timestamp_ms()
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _signed_get(self, path: str, params: dict | None = None) -> dict:
        client = await self._get_client()
        params = self._sign_params(params or {})
        resp = await client.get(
            f"{self.base_url}{path}",
            params=params,
            headers={"X-MBX-APIKEY": self.api_key},
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch_products(self) -> list[EarnProduct]:
        if not self.has_credentials:
            logger.info("[binance] API 키 필요 - 건너뜀 (공개 API 미지원)")
            return []

        products: list[EarnProduct] = []
        products.extend(await self._fetch_flexible())
        products.extend(await self._fetch_locked())
        return products

    async def _fetch_flexible(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        current = 1

        while True:
            try:
                data = await self._signed_get(
                    "/sapi/v1/simple-earn/flexible/list",
                    {"size": 100, "current": current},
                )
                rows = data.get("rows", [])
                if not rows:
                    break

                for row in rows:
                    asset = row.get("asset", "")
                    if not self._is_stablecoin(asset):
                        continue

                    apr = float(row.get("latestAnnualPercentageRate", 0))
                    if 0 < apr < 1:
                        apr *= 100

                    product = EarnProduct(
                        exchange=self.name,
                        product_id=str(row.get("productId", "")),
                        coin=asset,
                        product_name=f"{asset} 유동성 예치",
                        product_type=ProductType.FLEXIBLE,
                        apr=round(apr, 2),
                        apr_type=AprType.VARIABLE,
                        duration_days=0,
                        min_amount=float(row.get("minPurchaseAmount", 0) or 0),
                        is_sold_out=row.get("status") != "PURCHASING",
                        url="https://www.binance.com/en/simple-earn/flexible",
                        raw_data=row,
                    )
                    products.append(product)

                total = data.get("total", 0)
                if current * 100 >= total:
                    break
                current += 1
            except Exception as e:
                logger.error(f"[binance] Flexible 페이지 {current} 실패: {e}")
                break

        return products

    async def _fetch_locked(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        current = 1

        while True:
            try:
                data = await self._signed_get(
                    "/sapi/v1/simple-earn/locked/list",
                    {"size": 100, "current": current},
                )
                rows = data.get("rows", [])
                if not rows:
                    break

                for row in rows:
                    asset = row.get("asset", "")
                    if not self._is_stablecoin(asset):
                        continue

                    detail = row.get("detail", row)
                    quota = row.get("quota", {})
                    duration = int(detail.get("duration", 0) or 0)

                    apr = float(detail.get("apr", detail.get("apy", 0)) or 0)
                    if 0 < apr < 1:
                        apr *= 100

                    product = EarnProduct(
                        exchange=self.name,
                        product_id=str(row.get("projectId", row.get("productId", ""))),
                        coin=asset,
                        product_name=f"{asset} {duration}일 고정 예치",
                        product_type=ProductType.LOCKED,
                        apr=round(apr, 2),
                        apr_type=AprType.FIXED,
                        duration_days=duration,
                        min_amount=float(quota.get("minimum", detail.get("minPurchaseAmount", 0)) or 0),
                        max_amount=float(quota.get("totalPersonalQuota", 0) or 0),
                        is_sold_out=row.get("status", detail.get("status")) == "SOLD_OUT",
                        url="https://www.binance.com/en/simple-earn/locked",
                        raw_data=row,
                    )
                    products.append(product)

                total = data.get("total", 0)
                if current * 100 >= total:
                    break
                current += 1
            except Exception as e:
                logger.error(f"[binance] Locked 페이지 {current} 실패: {e}")
                break

        return products
