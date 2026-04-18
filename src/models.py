from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AprType(str, Enum):
    FIXED = "fixed"
    VARIABLE = "variable"


class ProductType(str, Enum):
    FLEXIBLE = "flexible"       # 유동성 예치 (언제든 출금)
    LOCKED = "locked"           # 고정 기간 예치
    STAKING = "staking"         # 스테이킹
    LAUNCHPOOL = "launchpool"   # 런치풀
    DUAL = "dual"               # 듀얼 투자
    STRUCTURED = "structured"   # 구조화 상품


class EarnProduct(BaseModel):
    """스테이블코인 수익 상품"""

    exchange: str                                       # 거래소 이름
    product_id: str                                     # 고유 식별자
    coin: str                                           # 예치 코인 (USDT, USDC 등)
    product_name: str = ""                              # 상품명
    product_type: ProductType = ProductType.FLEXIBLE     # 상품 유형
    apr: float = 0.0                                    # 연 수익률 (%)
    apr_type: AprType = AprType.VARIABLE                # 고정/변동
    duration_days: int = 0                              # 예치 기간 (0=유동)
    min_amount: float = 0.0                             # 최소 예치 금액
    max_amount: float = 0.0                             # 최대 예치 금액 (0=무제한)
    total_quota: float = 0.0                            # 총 한도 (0=무제한)
    remaining_quota: float = 0.0                        # 잔여 한도
    is_limited: bool = False                            # 한정 수량 여부
    is_sold_out: bool = False                           # 매진 여부
    start_time: datetime | None = None                  # 시작 시간
    end_time: datetime | None = None                    # 종료 시간
    url: str = ""                                       # 상품 링크
    raw_data: dict = Field(default_factory=dict, exclude=True)

    @property
    def unique_key(self) -> str:
        """중복 체크용 고유 키"""
        return f"{self.exchange}:{self.product_id}:{self.coin}:{self.duration_days}"

    @property
    def apr_type_label(self) -> str:
        return "고정금리" if self.apr_type == AprType.FIXED else "변동금리"

    @property
    def product_type_label(self) -> str:
        labels = {
            ProductType.FLEXIBLE: "유동성 예치",
            ProductType.LOCKED: "고정 예치",
            ProductType.STAKING: "스테이킹",
            ProductType.LAUNCHPOOL: "런치풀",
            ProductType.DUAL: "듀얼 투자",
            ProductType.STRUCTURED: "구조화 상품",
        }
        return labels.get(self.product_type, str(self.product_type))

    @property
    def duration_label(self) -> str:
        if self.duration_days == 0:
            return "유동 (언제든 출금)"
        return f"{self.duration_days}일"
