"""
자동 예치 기반 인터페이스

현재는 알림만 사용하지만, 향후 자동 예치 기능을 위한 기반 코드입니다.
각 거래소별로 이 클래스를 상속하여 구현하면 됩니다.

사용 예시 (향후):
    depositor = BinanceDepositor(api_key, api_secret)
    result = await depositor.subscribe(product_id="USDT001", amount=1000)
    print(result)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class DepositStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SOLD_OUT = "sold_out"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    LIMIT_EXCEEDED = "limit_exceeded"


@dataclass
class DepositResult:
    status: DepositStatus
    exchange: str
    product_id: str
    coin: str
    amount: float
    message: str = ""
    order_id: str = ""


class BaseDepositor(ABC):
    """자동 예치 베이스 클래스"""

    name: str = ""

    @abstractmethod
    async def get_balance(self, coin: str) -> float:
        """예치 가능 잔액 조회"""
        ...

    @abstractmethod
    async def subscribe(self, product_id: str, amount: float) -> DepositResult:
        """상품에 예치 (구독)"""
        ...

    @abstractmethod
    async def redeem(self, product_id: str, amount: float) -> DepositResult:
        """예치 해제 (상환)"""
        ...

    async def safe_subscribe(
        self,
        product_id: str,
        coin: str,
        amount: float,
        max_ratio: float = 0.5,
    ) -> DepositResult:
        """
        안전한 자동 예치

        - 잔액 확인
        - 최대 비율 제한 (기본: 잔액의 50%까지만)
        - 에러 핸들링
        """
        try:
            balance = await self.get_balance(coin)

            if balance <= 0:
                return DepositResult(
                    status=DepositStatus.INSUFFICIENT_BALANCE,
                    exchange=self.name,
                    product_id=product_id,
                    coin=coin,
                    amount=0,
                    message=f"잔액 부족: {balance} {coin}",
                )

            # 최대 예치 비율 적용
            max_amount = balance * max_ratio
            actual_amount = min(amount, max_amount)

            logger.info(
                f"[{self.name}] 예치 시도: {actual_amount} {coin} "
                f"(잔액: {balance}, 최대: {max_amount})"
            )

            return await self.subscribe(product_id, actual_amount)

        except Exception as e:
            logger.error(f"[{self.name}] 예치 실패: {e}")
            return DepositResult(
                status=DepositStatus.FAILED,
                exchange=self.name,
                product_id=product_id,
                coin=coin,
                amount=0,
                message=str(e),
            )
