from __future__ import annotations

import logging

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# Bybit Earn API (v5) - 공개 엔드포인트 (API 키 불필요!)
# GET /v5/earn/product
# 응답에 계층형 APR(tierAprDetails) 포함


class BybitExchange(BaseExchange):
    name = "bybit"
    base_url = "https://api.bybit.com"

    async def fetch_products(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []

        for category in ["FlexibleSaving"]:
            products.extend(await self._fetch_category(category))

        return products

    async def _fetch_category(self, category: str) -> list[EarnProduct]:
        products: list[EarnProduct] = []

        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/v5/earn/product",
                params={"category": category},
            )

            if resp.status_code in (401, 403):
                logger.info(f"[bybit] {category}: 인증 필요")
                return []

            resp.raise_for_status()
            data = resp.json()

            if data.get("retCode") != 0:
                logger.warning(f"[bybit] {category}: {data.get('retMsg')}")
                return []

            items = data.get("result", {}).get("list", [])
            for item in items:
                coin = item.get("coin", "")
                if not self._is_stablecoin(coin):
                    continue

                # 계층형 APR 처리
                has_tiered = item.get("hasTieredApr", False)
                tiers = item.get("tierAprDetails", [])
                base_apr = self._parse_apr_str(item.get("estimateApr", "0"))

                if has_tiered and tiers:
                    # 1단계(최고) APR과 한도 추출
                    best_tier = tiers[0]
                    best_apr = self._parse_apr_str(best_tier.get("estimateApr", "0"))
                    tier_max = best_tier.get("max", "0")
                    tier_max_amount = float(tier_max) if tier_max != "-1" else 0

                    # 1단계 이율이 기본보다 높으면 그걸 표시
                    if best_apr > base_apr:
                        apr = best_apr
                        tier_info = f" (최대 {int(tier_max_amount)} {coin}까지)"
                        product_name = f"{coin} 유동 예치 {apr}%{tier_info}"
                        max_amount = tier_max_amount
                    else:
                        apr = base_apr
                        product_name = f"{coin} 유동 예치"
                        max_amount = 0
                else:
                    apr = base_apr
                    product_name = f"{coin} 유동 예치"
                    max_amount = 0

                duration = int(item.get("term", 0) or 0)
                is_flexible = category == "FlexibleSaving" or duration == 0

                remaining_str = item.get("remainingPoolAmount", "-1")
                remaining = float(remaining_str) if remaining_str != "-1" else 0
                is_limited = remaining_str != "-1" and remaining > 0

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("productId", "")),
                    coin=coin,
                    product_name=product_name,
                    product_type=ProductType.FLEXIBLE if is_flexible else ProductType.LOCKED,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE if is_flexible else AprType.FIXED,
                    duration_days=duration,
                    min_amount=float(item.get("minStakeAmount", 0) or 0),
                    max_amount=max_amount,
                    remaining_quota=remaining,
                    is_limited=is_limited,
                    url="https://www.bybit.com/en/earn",
                    raw_data=item,
                )
                products.append(product)

        except Exception as e:
            logger.error(f"[bybit] {category} 조회 실패: {e}")

        return products

    def _parse_apr_str(self, val: str | float | None) -> float:
        """APR 문자열 파싱 - '5.8%' → 5.8, '0.0058' → 0.58"""
        if val is None:
            return 0.0

        val_str = str(val).strip()

        # "5.8%" 형태 → % 포함이면 이미 퍼센트 값
        if "%" in val_str:
            try:
                return float(val_str.replace("%", ""))
            except ValueError:
                return 0.0

        # 숫자만 있는 경우
        try:
            num = float(val_str)
            # 0.058 같은 소수는 퍼센트로 변환 (5.8%)
            # 단, 5.8 같은 값은 이미 퍼센트
            # 기준: 1 미만이면 소수 형태로 판단
            if 0 < num < 0.5:
                return num * 100
            return num
        except ValueError:
            return 0.0
