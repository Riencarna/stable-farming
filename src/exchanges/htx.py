from __future__ import annotations

import logging

import httpx

from src.models import AprType, EarnProduct, ProductType
from src.exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# HTX (Huobi) Earn API - 공개 엔드포인트 (API 키 불필요!)
# GET /-/x/hbg/v3/saving/mining/project/steady_financial/list → 유동 예치 이율
# GET /-/x/hbg/v1/saving/mining/index/fixed/list → 고정 예치 상품
# GET /-/x/hbg/v1/saving/mining/index/limitTime/list → 한정 특판


class HtxExchange(BaseExchange):
    name = "htx"
    base_url = "https://www.htx.com"

    async def _get_client(self) -> httpx.AsyncClient:
        """HTX SSL 인증서 문제 대응"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
                follow_redirects=True,
                verify=False,
            )
        return self._client

    async def fetch_products(self) -> list[EarnProduct]:
        products: list[EarnProduct] = []
        products.extend(await self._fetch_flexible())
        products.extend(await self._fetch_fixed())
        products.extend(await self._fetch_limited())
        return products

    async def _fetch_flexible(self) -> list[EarnProduct]:
        """유동 예치 상품 (계층 이율 포함)"""
        products: list[EarnProduct] = []
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/-/x/hbg/v3/saving/mining/project/steady_financial/list",
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 200:
                logger.warning(f"[htx] 유동 조회 에러: {data.get('message', '')}")
                return []

            for item in data.get("data", []):
                currency = item.get("currency", "").upper()
                if not self._is_stablecoin(currency):
                    continue

                # flexibleViewYearRate: 소수 형태 (0.1 = 10%)
                rate = float(item.get("flexibleViewYearRate", 0) or 0)
                apr = rate * 100 if rate < 1 else rate

                max_rate = float(item.get("maxViewYearRate", 0) or 0)
                max_apr = max_rate * 100 if max_rate < 1 else max_rate

                # 최고 이율이 기본보다 높으면 표시
                if max_apr > apr:
                    product_name = f"{currency} 유동 예치 (최대 {max_apr:.1f}%)"
                else:
                    product_name = f"{currency} 유동 예치"

                product = EarnProduct(
                    exchange=self.name,
                    product_id=f"htx_flex_{currency}",
                    coin=currency,
                    product_name=product_name,
                    product_type=ProductType.FLEXIBLE,
                    apr=round(apr, 2),
                    apr_type=AprType.VARIABLE,
                    duration_days=0,
                    url="https://www.htx.com/en-us/earn/",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[htx] 유동 예치 조회 실패: {e}")
        return products

    async def _fetch_fixed(self) -> list[EarnProduct]:
        """고정 기간 예치 상품"""
        products: list[EarnProduct] = []
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/-/x/hbg/v1/saving/mining/index/fixed/list",
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 200:
                return []

            for item in data.get("data", []):
                currency = item.get("currency", "").upper()
                if not self._is_stablecoin(currency):
                    continue

                # 하위 프로젝트별로 처리
                sub_projects = item.get("projects", [])
                if sub_projects:
                    for proj in sub_projects:
                        apr = self._parse_rate(proj.get("viewYearRate", 0))
                        duration = int(proj.get("term", 0) or 0)
                        total = float(proj.get("totalAmount", 0) or 0)
                        remain = total - float(proj.get("finishAmount", 0) or 0)

                        product = EarnProduct(
                            exchange=self.name,
                            product_id=str(proj.get("projectId", "")),
                            coin=currency,
                            product_name=f"{currency} 고정 {duration}일",
                            product_type=ProductType.LOCKED,
                            apr=round(apr, 2),
                            apr_type=AprType.FIXED,
                            duration_days=duration,
                            total_quota=total,
                            remaining_quota=max(remain, 0),
                            is_limited=total > 0,
                            is_sold_out=remain <= 0 and total > 0,
                            url="https://www.htx.com/en-us/earn/",
                            raw_data=proj,
                        )
                        products.append(product)
                else:
                    apr = self._parse_rate(item.get("viewYearRate", 0))
                    duration = int(item.get("term", 0) or 0)

                    product = EarnProduct(
                        exchange=self.name,
                        product_id=str(item.get("projectId", "")),
                        coin=currency,
                        product_name=f"{currency} 고정 {duration}일",
                        product_type=ProductType.LOCKED,
                        apr=round(apr, 2),
                        apr_type=AprType.FIXED,
                        duration_days=duration,
                        url="https://www.htx.com/en-us/earn/",
                        raw_data=item,
                    )
                    products.append(product)
        except Exception as e:
            logger.error(f"[htx] 고정 예치 조회 실패: {e}")
        return products

    async def _fetch_limited(self) -> list[EarnProduct]:
        """한정 특판 상품"""
        products: list[EarnProduct] = []
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/-/x/hbg/v1/saving/mining/index/limitTime/list",
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 200:
                return []

            for item in data.get("data", []):
                currency = item.get("currency", "").upper()
                if not self._is_stablecoin(currency):
                    continue

                apr = self._parse_rate(item.get("viewYearRate", 0))
                duration = int(item.get("term", 0) or 0)
                total = float(item.get("totalAmount", 0) or 0)
                remain = total - float(item.get("finishAmount", 0) or 0)

                product = EarnProduct(
                    exchange=self.name,
                    product_id=str(item.get("projectId", "")),
                    coin=currency,
                    product_name=f"{currency} 한정 특판 {duration}일",
                    product_type=ProductType.LOCKED,
                    apr=round(apr, 2),
                    apr_type=AprType.FIXED,
                    duration_days=duration,
                    total_quota=total,
                    remaining_quota=max(remain, 0),
                    is_limited=True,
                    is_sold_out=remain <= 0 and total > 0,
                    url="https://www.htx.com/en-us/earn/",
                    raw_data=item,
                )
                products.append(product)
        except Exception as e:
            logger.error(f"[htx] 한정 특판 조회 실패: {e}")
        return products

    def _parse_rate(self, val) -> float:
        """이율 파싱: 소수(0.1=10%) 또는 백분율(10=10%)"""
        if val is None:
            return 0.0
        rate = float(val)
        if 0 < rate < 1:
            rate *= 100
        return rate
