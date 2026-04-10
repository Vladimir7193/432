"""
=============================================================
market_data.py — Real-time OHLCV fetcher for Bybit (v5 API)
=============================================================
"""
import time
import logging
import pandas as pd
from pybit.unified_trading import HTTP
import config as cfg

logger = logging.getLogger(__name__)


def _get_session() -> HTTP:
    return HTTP(
        testnet=cfg.TESTNET,
        api_key=cfg.API_KEY,
        api_secret=cfg.API_SECRET,
    )


def fetch_klines(
    symbol: str = cfg.SYMBOL,
    interval: str = cfg.INTERVAL,
    limit: int = cfg.LOOKBACK + 50,
    retries: int = 5,
) -> pd.DataFrame:
    """
    Fetch OHLCV from Bybit and return a clean DataFrame
    sorted ascending by timestamp with a DatetimeIndex.
    """
    session = _get_session()
    for attempt in range(retries):
        try:
            resp = session.get_kline(
                category=cfg.CATEGORY,
                symbol=symbol,
                interval=interval,
                limit=limit,
            )
            rows = resp["result"]["list"]  # newest-first
            df = pd.DataFrame(
                rows,
                columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype('int64'), unit="ms", utc=True)
            for col in ["open", "high", "low", "close", "volume", "turnover"]:
                df[col] = df[col].astype(float)
            df = df.sort_values("timestamp").reset_index(drop=True)
            df = df.set_index("timestamp")
            logger.debug("Fetched %d klines for %s @%s", len(df), symbol, interval)
            return df
        except Exception as exc:
            logger.warning("kline fetch error (attempt %d/%d): %s", attempt + 1, retries, exc)
            time.sleep(2 ** attempt)
    raise RuntimeError("Failed to fetch klines after %d retries" % retries)


def get_orderbook_imbalance(symbol: str = cfg.SYMBOL, depth: int = 5) -> float:
    """
    Return simple bid/ask imbalance in [-1, +1]:
      +1 = fully bid-side, -1 = fully ask-side.
    """
    try:
        session = _get_session()
        ob = session.get_orderbook(category=cfg.CATEGORY, symbol=symbol, limit=depth)
        bids = sum(float(b[1]) for b in ob["result"]["b"])
        asks = sum(float(a[1]) for a in ob["result"]["a"])
        total = bids + asks
        return (bids - asks) / total if total > 0 else 0.0
    except Exception as exc:
        logger.warning("orderbook fetch error: %s", exc)
        return 0.0


def get_ticker(symbol: str = cfg.SYMBOL) -> dict:
    """Return latest ticker dict with lastPrice, markPrice, fundingRate, etc."""
    try:
        session = _get_session()
        resp = session.get_tickers(category=cfg.CATEGORY, symbol=symbol)
        return resp["result"]["list"][0]
    except Exception as exc:
        logger.warning("ticker fetch error: %s", exc)
        return {}


def fetch_klines_multi_tf(
    symbol: str,
    intervals: list[str] | None = None,
    limit: int = cfg.MTF_LOOKBACK,
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for multiple timeframes at once.
    Returns dict {interval_str: DataFrame}.
    """
    if intervals is None:
        intervals = cfg.MTF_INTERVALS
    result: dict[str, pd.DataFrame] = {}
    for iv in intervals:
        try:
            df = fetch_klines(symbol=symbol, interval=iv, limit=limit)
            if df is not None and len(df) > 0:
                result[iv] = df
        except Exception as exc:
            logger.warning("MTF fetch error %s @%s: %s", symbol, iv, exc)
    return result


def get_funding_rate(symbol: str = cfg.SYMBOL) -> float:
    """Return current funding rate for a symbol (0.0 on error)."""
    try:
        ticker = get_ticker(symbol)
        return float(ticker.get("fundingRate", 0.0))
    except Exception:
        return 0.0


def get_open_interest(symbol: str = cfg.SYMBOL) -> float:
    """Return open interest in USD (0.0 on error)."""
    try:
        session = _get_session()
        resp = session.get_open_interest(
            category=cfg.CATEGORY, symbol=symbol, intervalTime="5min", limit=1
        )
        rows = resp["result"]["list"]
        if rows:
            return float(rows[0].get("openInterest", 0.0))
        return 0.0
    except Exception as exc:
        logger.warning("open interest fetch error: %s", exc)
        return 0.0
