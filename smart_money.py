"""
=============================================================
smart_money.py — Whale / Institutional flow detection
=============================================================
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
import config as cfg

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  WHALE BAR DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_whale_bars(df: pd.DataFrame) -> pd.Series:
    """
    Returns a boolean Series: True if the bar qualifies as a whale bar.
    Conditions:
      - Volume > WHALE_VOL_MULT × rolling mean
      - Significant price displacement (close ≠ open)
    """
    vol_mean = df["volume"].rolling(cfg.WHALE_LOOKBACK).mean()
    high_vol  = df["volume"] > cfg.WHALE_VOL_MULT * vol_mean
    significant = (df["close"] - df["open"]).abs() / (df["high"] - df["low"] + 1e-9) > 0.3
    return high_vol & significant


def detect_absorption(df: pd.DataFrame) -> pd.Series:
    """
    Absorption bar: high volume but small candle body — price held.
    Suggests institutional absorption of supply/demand.
    """
    vol_mean = df["volume"].rolling(cfg.WHALE_LOOKBACK).mean()
    high_vol  = df["volume"] > cfg.WHALE_VOL_MULT * vol_mean
    small_body = (df["close"] - df["open"]).abs() / (df["high"] - df["low"] + 1e-9) < cfg.ABSORPTION_THRESH
    return high_vol & small_body


def detect_stop_hunt(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """
    Stop hunt: wick pierces the rolling high/low, then reverses.
    Long wick + candle closes back within prior range.
    """
    rolling_high = df["high"].rolling(lookback).max().shift(1)
    rolling_low  = df["low"].rolling(lookback).min().shift(1)
    upper_pierce = (df["high"] > rolling_high) & (df["close"] < rolling_high)
    lower_pierce = (df["low"]  < rolling_low)  & (df["close"] > rolling_low)
    return upper_pierce | lower_pierce


def compute_smart_money_score(df: pd.DataFrame) -> pd.Series:
    """
    Composite smart-money score in [0, 3]:
      1 point for whale bar
      1 point for absorption (nearby)
      1 point for stop hunt
    Returns a rolling-sum proxy.
    """
    whale      = detect_whale_bars(df).astype(int)
    absorption = detect_absorption(df).astype(int)
    stop_hunt  = detect_stop_hunt(df).astype(int)
    score = whale + absorption + stop_hunt
    return score


def get_bias_from_smart_money(df: pd.DataFrame) -> int:
    """
    Last-bar smart-money directional bias:
      +1 = bullish footprint (whale buy bar or long-wick stop hunt from below)
      -1 = bearish footprint
       0 = neutral
    """
    if len(df) < cfg.WHALE_LOOKBACK + 10:
        return 0

    last = df.iloc[-1]
    vol_mean = df["volume"].iloc[-cfg.WHALE_LOOKBACK:].mean()
    is_whale = last["volume"] > cfg.WHALE_VOL_MULT * vol_mean
    bullish  = last["close"] > last["open"]
    bearish  = last["close"] < last["open"]

    # Stop hunt from low (bullish reversal)
    prior_low  = df["low"].iloc[-6:-1].min()
    prior_high = df["high"].iloc[-6:-1].max()
    stop_hunt_bull = (last["low"] < prior_low) and (last["close"] > prior_low)
    stop_hunt_bear = (last["high"] > prior_high) and (last["close"] < prior_high)

    if is_whale and bullish:
        return 1
    if is_whale and bearish:
        return -1
    if stop_hunt_bull:
        return 1
    if stop_hunt_bear:
        return -1
    return 0
