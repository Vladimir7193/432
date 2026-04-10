"""
=============================================================
signal_logger.py — CSV-based signal and trade logger
=============================================================
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import config as cfg

logger = logging.getLogger(__name__)

_SIGNAL_FIELDS = [
    "ts", "symbol", "signal", "p_hold", "p_long", "p_short",
    "close", "atr", "sm_bias", "ob_imbalance",
]
_TRADE_FIELDS = [
    "ts", "symbol", "side", "qty", "entry_price", "exit_price",
    "pnl_usdt", "pnl_pct", "sl", "tp", "exit_reason", "duration_sec",
]
_WHALE_FIELDS = [
    "ts", "symbol", "event_type", "price", "volume", "vol_mult",
]


def _ensure_csv(path: str, fields: list[str]):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="") as fp:
            csv.DictWriter(fp, fieldnames=fields).writeheader()


def _append_row(path: str, fields: list[str], row: dict):
    _ensure_csv(path, fields)
    with open(path, "a", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        w.writerow(row)


def log_signal(
    symbol: str,
    signal: int,
    p_hold: float,
    p_long: float,
    p_short: float,
    close: float,
    atr: float,
    sm_bias: int = 0,
    ob_imbalance: float = 0.0,
):
    row = {
        "ts":           datetime.now(tz=timezone.utc).isoformat(),
        "symbol":       symbol,
        "signal":       signal,
        "p_hold":       round(p_hold, 4),
        "p_long":       round(p_long, 4),
        "p_short":      round(p_short, 4),
        "close":        close,
        "atr":          round(atr, 4),
        "sm_bias":      sm_bias,
        "ob_imbalance": round(ob_imbalance, 4),
    }
    _append_row(cfg.SIGNAL_LOG_CSV, _SIGNAL_FIELDS, row)
    logger.debug("Signal logged: %s", row)


def log_trade(
    symbol: str,
    side: str,
    qty: float,
    entry_price: float,
    exit_price: float,
    sl: float,
    tp: float,
    exit_reason: str,
    duration_sec: float,
):
    pnl_usdt = (exit_price - entry_price) * qty * (1 if side == "Buy" else -1)
    pnl_pct  = pnl_usdt / (entry_price * qty + 1e-9) * 100
    row = {
        "ts":           datetime.now(tz=timezone.utc).isoformat(),
        "symbol":       symbol,
        "side":         side,
        "qty":          qty,
        "entry_price":  entry_price,
        "exit_price":   exit_price,
        "pnl_usdt":     round(pnl_usdt, 4),
        "pnl_pct":      round(pnl_pct, 4),
        "sl":           sl,
        "tp":           tp,
        "exit_reason":  exit_reason,
        "duration_sec": round(duration_sec, 1),
    }
    _append_row(cfg.TRADE_LOG_CSV, _TRADE_FIELDS, row)
    logger.info("Trade logged: %s %s PnL=%.2f USDT (%.2f%%)", side, symbol, pnl_usdt, pnl_pct)


def log_whale_event(
    symbol: str,
    event_type: str,
    price: float,
    volume: float,
    vol_mult: float,
):
    row = {
        "ts":         datetime.now(tz=timezone.utc).isoformat(),
        "symbol":     symbol,
        "event_type": event_type,
        "price":      price,
        "volume":     volume,
        "vol_mult":   round(vol_mult, 2),
    }
    _append_row(cfg.WHALE_LOG_CSV, _WHALE_FIELDS, row)
