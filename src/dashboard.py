from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.config import MIN_APR, STABLECOINS
from src.exchanges import ALL_EXCHANGES
from src.models import EarnProduct
from src.scanner import fetch_all_products, filter_products
from src.store import ProductStore

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Stable Farming Dashboard")

# 캐시: 마지막 스캔 결과
_cache: dict = {
    "products": [],
    "last_scan": None,
    "scanning": False,
}


async def _do_scan() -> list[EarnProduct]:
    """스캔 실행 후 캐시 업데이트"""
    if _cache["scanning"]:
        return _cache["products"]

    _cache["scanning"] = True
    try:
        all_products = await fetch_all_products()
        _cache["products"] = all_products
        _cache["last_scan"] = datetime.now(timezone.utc)
        return all_products
    finally:
        _cache["scanning"] = False


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """메인 대시보드 페이지"""
    products = _cache["products"]
    last_scan = _cache["last_scan"]

    # 거래소별 통계
    exchange_stats: dict[str, dict] = {}
    for ex_cls in ALL_EXCHANGES:
        name = ex_cls.name
        ex_products = [p for p in products if p.exchange == name]
        qualified = [p for p in ex_products if p.apr >= MIN_APR and not p.is_sold_out]
        best = max((p.apr for p in ex_products), default=0)
        exchange_stats[name] = {
            "total": len(ex_products),
            "qualified": len(qualified),
            "best_apr": best,
        }

    # APR 기준 이상 상품 (매진 제외)
    qualified_products = sorted(
        [p for p in products if p.apr >= MIN_APR and not p.is_sold_out],
        key=lambda p: p.apr,
        reverse=True,
    )

    # 전체 상품 (APR 내림차순)
    all_sorted = sorted(products, key=lambda p: p.apr, reverse=True)

    # 코인별 통계
    coin_stats: dict[str, dict] = {}
    for p in products:
        if p.coin not in coin_stats:
            coin_stats[p.coin] = {"count": 0, "best_apr": 0, "exchanges": set()}
        coin_stats[p.coin]["count"] += 1
        coin_stats[p.coin]["best_apr"] = max(coin_stats[p.coin]["best_apr"], p.apr)
        coin_stats[p.coin]["exchanges"].add(p.exchange)

    # set을 리스트로 변환 (JSON 직렬화용)
    for coin in coin_stats:
        coin_stats[coin]["exchanges"] = list(coin_stats[coin]["exchanges"])

    # 알림 이력
    store = ProductStore()
    alert_history = []
    for key, info in sorted(store._seen.items(), key=lambda x: x[1].get("last_seen", ""), reverse=True)[:50]:
        alert_history.append(info)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "products": qualified_products,
            "all_products": all_sorted,
            "exchange_stats": exchange_stats,
            "coin_stats": coin_stats,
            "alert_history": alert_history,
            "last_scan": last_scan,
            "min_apr": MIN_APR,
            "total_count": len(products),
            "qualified_count": len(qualified_products),
            "scanning": _cache["scanning"],
        },
    )


@app.post("/scan", response_class=HTMLResponse)
async def trigger_scan(request: Request):
    """수동 스캔 트리거"""
    await _do_scan()
    return HTMLResponse(
        content='<script>window.location.href="/";</script>',
        status_code=200,
    )


@app.get("/api/products")
async def api_products():
    """상품 목록 API (JSON)"""
    return {
        "products": [p.model_dump() for p in _cache["products"]],
        "last_scan": _cache["last_scan"].isoformat() if _cache["last_scan"] else None,
        "total": len(_cache["products"]),
    }


@app.get("/api/scan")
async def api_scan():
    """스캔 트리거 API"""
    products = await _do_scan()
    qualified = [p for p in products if p.apr >= MIN_APR and not p.is_sold_out]
    return {
        "total": len(products),
        "qualified": len(qualified),
        "last_scan": _cache["last_scan"].isoformat() if _cache["last_scan"] else None,
    }
