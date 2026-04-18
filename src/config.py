from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- 경로 ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
SEEN_PRODUCTS_PATH = DATA_DIR / "seen_products.json"

# --- 텔레그램 ---
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# --- 알림 조건 ---
MIN_APR: float = float(os.getenv("MIN_APR", "8.0"))

# --- 모니터링 간격 ---
SCAN_INTERVAL_MINUTES: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "10"))

# --- 스테이블코인 목록 ---
STABLECOINS: set[str] = {
    "USDT", "USDC", "USDE", "USDS", "USDP", "PYUSD",
    "DAI", "FDUSD", "TUSD", "BUSD", "FRAX", "LUSD",
    "USAT", "EUSD", "GUSD", "SUSD", "CUSD", "USDD",
    "CRVUSD", "GHO", "MKUSD", "USDB", "USD0",
}

# --- 거래소 API 키 ---
EXCHANGE_KEYS: dict[str, dict[str, str]] = {
    "binance": {
        "api_key": os.getenv("BINANCE_API_KEY", ""),
        "api_secret": os.getenv("BINANCE_API_SECRET", ""),
    },
    "bybit": {
        "api_key": os.getenv("BYBIT_API_KEY", ""),
        "api_secret": os.getenv("BYBIT_API_SECRET", ""),
    },
    "okx": {
        "api_key": os.getenv("OKX_API_KEY", ""),
        "api_secret": os.getenv("OKX_API_SECRET", ""),
        "passphrase": os.getenv("OKX_PASSPHRASE", ""),
    },
    "gateio": {
        "api_key": os.getenv("GATEIO_API_KEY", ""),
        "api_secret": os.getenv("GATEIO_API_SECRET", ""),
    },
    "kucoin": {
        "api_key": os.getenv("KUCOIN_API_KEY", ""),
        "api_secret": os.getenv("KUCOIN_API_SECRET", ""),
        "passphrase": os.getenv("KUCOIN_PASSPHRASE", ""),
    },
    "htx": {
        "api_key": os.getenv("HTX_API_KEY", ""),
        "api_secret": os.getenv("HTX_API_SECRET", ""),
    },
}


def has_keys(exchange: str) -> bool:
    """해당 거래소의 API 키가 설정되어 있는지 확인"""
    keys = EXCHANGE_KEYS.get(exchange, {})
    return bool(keys.get("api_key") and keys.get("api_secret"))
