from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import SEEN_PRODUCTS_PATH
from src.models import EarnProduct

logger = logging.getLogger(__name__)


class ProductStore:
    """이미 알림을 보낸 상품을 추적하는 저장소"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SEEN_PRODUCTS_PATH
        self._seen: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._seen = json.loads(self.path.read_text(encoding="utf-8"))
                logger.info(f"저장소 로드: {len(self._seen)}개 상품 추적 중")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"저장소 로드 실패, 초기화: {e}")
                self._seen = {}
        else:
            self._seen = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._seen, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_new(self, product: EarnProduct) -> bool:
        """이전에 알림을 보내지 않은 새 상품인지 확인"""
        key = product.unique_key
        if key not in self._seen:
            return True

        # APR이 크게 변경된 경우에도 다시 알림 (2%p 이상 상승)
        old_apr = self._seen[key].get("apr", 0)
        if product.apr - old_apr >= 2.0:
            return True

        return False

    def mark_seen(self, product: EarnProduct) -> None:
        """상품을 알림 완료로 표시"""
        self._seen[product.unique_key] = {
            "exchange": product.exchange,
            "coin": product.coin,
            "apr": product.apr,
            "product_name": product.product_name,
            "first_seen": self._seen.get(product.unique_key, {}).get(
                "first_seen",
                datetime.now(timezone.utc).isoformat(),
            ),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    def cleanup_old(self, days: int = 30) -> int:
        """오래된 기록 정리"""
        cutoff = datetime.now(timezone.utc)
        to_remove = []
        for key, info in self._seen.items():
            last_seen = info.get("last_seen", "")
            if last_seen:
                try:
                    seen_dt = datetime.fromisoformat(last_seen)
                    if (cutoff - seen_dt).days > days:
                        to_remove.append(key)
                except ValueError:
                    pass
        for key in to_remove:
            del self._seen[key]
        if to_remove:
            logger.info(f"{len(to_remove)}개 오래된 기록 정리")
        return len(to_remove)
