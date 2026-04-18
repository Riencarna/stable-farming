from src.exchanges.binance import BinanceExchange
from src.exchanges.bybit import BybitExchange
from src.exchanges.okx import OkxExchange
from src.exchanges.gateio import GateioExchange
from src.exchanges.kucoin import KucoinExchange
from src.exchanges.htx import HtxExchange

ALL_EXCHANGES = [
    BinanceExchange,
    BybitExchange,
    OkxExchange,
    GateioExchange,
    KucoinExchange,
    HtxExchange,
]
