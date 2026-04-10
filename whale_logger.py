"""
=============================================================
whale_logger.py — Standalone whale-event detection + logging
=============================================================
Can be imported by bybit_paper_bot.py or run independently:
    python whale_logger.py   (polls every minute, appends to CSV)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd

import config as cfg
from market_data import fetch_klines
from signal_engine import compute_features
from smart_money import detect_whale_bars, detect_absorption, detect_stop_hunt
from signal_logger import log_whale_event

logger = logging.getLogger(__name__)


class WhaleLogger:
    """
    Watches for whale / smart-money events on each new bar and
    persists them to WHALE_LOG_CSV.
    """

    def __init__(self, symbol: str = cfg.SYMBOL):
        self.symbol        = symbol
        self.last_bar_ts   = None

    def check_and_log(self, df: pd.DataFrame) -> list[dict]:
        """
        Inspect the latest bar. If it's new (different timestamp),
        run detection and log any events. Returns list of events fired.
        """
        if df is None or len(df) < cfg.WHALE_LOOKBACK + 5:
            return []

        latest_ts = df.index[-1]
        if latest_ts == self.last_bar_ts:
            return []   # same bar as last check
        self.last_bar_ts = latest_ts

        feat_df    = compute_features(df)
        last       = feat_df.iloc[-1]
        price      = float(last["close"])
        volume     = float(df["volume"].iloc[-1])
        vol_mean   = df["volume"].iloc[-cfg.WHALE_LOOKBACK:].mean()
        vol_mult   = volume / (vol_mean + 1e-9)

        events_fired = []

        # ── Whale bar ────────────────────────────────────────────────────────
        whale_series = detect_whale_bars(df)
        if whale_series.iloc[-1]:
            log_whale_event(
                symbol=self.symbol,
                event_type="whale_bar",
                price=price,
                volume=volume,
                vol_mult=vol_mult,
            )
            logger.info("🐋 WHALE BAR | %s @ %.2f | vol_mult=%.1f×", self.symbol, price, vol_mult)
            events_fired.append({"type": "whale_bar", "price": price, "vol_mult": vol_mult})

        # ── Absorption ───────────────────────────────────────────────────────
        absorption_series = detect_absorption(df)
        if absorption_series.iloc[-1]:
            log_whale_event(
                symbol=self.symbol,
                event_type="absorption",
                price=price,
                volume=volume,
                vol_mult=vol_mult,
            )
            logger.info("🧲 ABSORPTION | %s @ %.2f", self.symbol, price)
            events_fired.append({"type": "absorption", "price": price, "vol_mult": vol_mult})

        # ── Stop hunt ────────────────────────────────────────────────────────
        stop_hunt_series = detect_stop_hunt(df)
        if stop_hunt_series.iloc[-1]:
            log_whale_event(
                symbol=self.symbol,
                event_type="stop_hunt",
                price=price,
                volume=volume,
                vol_mult=vol_mult,
            )
            logger.info("🎯 STOP HUNT | %s @ %.2f", self.symbol, price)
            events_fired.append({"type": "stop_hunt", "price": price, "vol_mult": vol_mult})

        return events_fired


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE MODE
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger.info("Whale logger started for %s", cfg.SYMBOL)
    wl = WhaleLogger()

    while True:
        try:
            df = fetch_klines(limit=cfg.WHALE_LOOKBACK + 20)
            events = wl.check_and_log(df)
            if not events:
                logger.debug("No whale events this bar.")
        except KeyboardInterrupt:
            logger.info("Whale logger stopped.")
            break
        except Exception as exc:
            logger.warning("Error in whale logger: %s", exc)
        time.sleep(60)


if __name__ == "__main__":
    main()
