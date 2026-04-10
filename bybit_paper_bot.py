"""
=============================================================
bybit_paper_bot.py -- Мультипарный paper-trading бот
=============================================================
Сканирует 30 пар каждую минуту:
1. Для каждой пары: скачивает OHLCV, считает сигнал
2. Логирует сигналы в signals.csv (для дашборда)
3. Управляет открытыми позициями (SL/TP/trailing)
4. Открывает новые позиции при сильных сигналах
5. Каждые 500 баров -- попытка переобучения модели

Запуск: py -3.12 bybit_paper_bot.py
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pybit.unified_trading import HTTP

import config as cfg
from market_data import fetch_klines, get_orderbook_imbalance, get_ticker
from paper_position import PaperPosition
from signal_engine import ModelManager, compute_features, FEATURE_COLS
from smart_money import get_bias_from_smart_money, detect_whale_bars
from signal_logger import log_signal, log_trade, log_whale_event

# FIX: Force UTF-8 to prevent UnicodeEncodeError on Windows (cp1252 console)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# -- Logging setup -------------------------------------------------------------
Path(cfg.LOG_PATH).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(cfg.LOG_PATH + "bot.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("bybit_paper_bot")

# -----------------------------------------------------------------------------
# POSITION SIZING
# -----------------------------------------------------------------------------

def calc_qty(price: float, atr: float, equity_usdt: float = 1000.0) -> float:
    risk_usdt = equity_usdt * cfg.RISK_PCT
    sl_dist = cfg.SL_ATR_MULT * atr
    qty = risk_usdt / (sl_dist + 1e-9)
    max_qty = equity_usdt * cfg.LEVERAGE / price
    return round(min(qty, max_qty), 4)

# -----------------------------------------------------------------------------
# POSITION MANAGEMENT
# -----------------------------------------------------------------------------

def manage_position(symbol: str, pos: PaperPosition, current_price: float, atr: float) -> bool:
    if not pos.is_open:
        return False

    profit = pos.unrealized_pnl(current_price)
    trail_trigger = cfg.TRAIL_ACTIVATE_ATR * atr * pos.qty
    if profit >= trail_trigger and pos.trail_sl is None:
        if pos.side == "Buy":
            pos.trail_sl = current_price - atr * cfg.SL_ATR_MULT
        else:
            pos.trail_sl = current_price + atr * cfg.SL_ATR_MULT
        logger.info("Trailing SL activated: %.2f", pos.trail_sl)

    if pos.trail_sl is not None:
        if pos.side == "Buy":
            new_trail = current_price - atr * cfg.SL_ATR_MULT
            pos.trail_sl = max(pos.trail_sl, new_trail)
        else:
            new_trail = current_price + atr * cfg.SL_ATR_MULT
            pos.trail_sl = min(pos.trail_sl, new_trail)

    effective_sl = pos.trail_sl if pos.trail_sl is not None else pos.sl

    if pos.side == "Buy":
        if current_price <= effective_sl:
            pos.close(symbol, effective_sl, "SL")
            return True
        if current_price >= pos.tp:
            pos.close(symbol, pos.tp, "TP")
            return True
    else:
        if current_price >= effective_sl:
            pos.close(symbol, effective_sl, "SL")
            return True
        if current_price <= pos.tp:
            pos.close(symbol, pos.tp, "TP")
            return True

    return False

# -----------------------------------------------------------------------------
# DAILY LOSS GUARD
# -----------------------------------------------------------------------------

class DailyLossGuard:
    def __init__(self, equity: float = 1000.0):
        self.equity = equity
        self.day_start = datetime.now(tz=timezone.utc).date()
        self.day_pnl = 0.0
        self.halted = False

    def record_pnl(self, pnl: float):
        today = datetime.now(tz=timezone.utc).date()
        if today != self.day_start:
            self.day_start = today
            self.day_pnl = 0.0
            self.halted = False
        self.day_pnl += pnl
        if self.day_pnl < -self.equity * cfg.MAX_DAILY_LOSS_PCT:
            logger.warning("Daily loss limit hit (%.2f USDT). Trading halted for today.", self.day_pnl)
            self.halted = True

# -----------------------------------------------------------------------------
# MAIN LOOP
# -----------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("Bybit CatBoost Scanner -- %d pairs", len(cfg.SYMBOLS))
    logger.info("=" * 60)

    model_mgr = ModelManager()
    loss_guard = DailyLossGuard(equity=1000.0)

    positions: dict[str, PaperPosition] = {s: PaperPosition() for s in cfg.SYMBOLS}
    histories: dict[str, pd.DataFrame | None] = {s: None for s in cfg.SYMBOLS}
    bar_count = 0

    while True:
        loop_start = time.time()
        bar_count += 1
        n_long = n_short = 0

        logger.info("--- Bar #%d (%s) ---",
                    bar_count, datetime.now(tz=timezone.utc).strftime("%H:%M:%S"))

        for sym in cfg.SYMBOLS:
            try:
                # -- 1. Данные ------------------------------------------------
                df = fetch_klines(symbol=sym, limit=cfg.LOOKBACK + 50)
                if df is None or len(df) < cfg.LOOKBACK:
                    continue

                hist = histories[sym]
                histories[sym] = (
                    pd.concat([hist, df]).drop_duplicates().sort_index().iloc[-(cfg.TRAIN_WINDOW_BARS + 200):]
                    if hist is not None else df
                )

                feat_df = compute_features(df)
                last = feat_df.iloc[-1]
                price = float(last["close"])
                atr = float(last.get("atr", price * 0.001))

                # -- 2. Whale / smart-money -----------------------------------
                sm_bias = get_bias_from_smart_money(df)
                whale_ser = detect_whale_bars(df)
                if whale_ser.iloc[-1]:
                    vol_mult = df["volume"].iloc[-1] / (df["volume"].iloc[-cfg.WHALE_LOOKBACK:].mean() + 1e-9)
                    log_whale_event(sym, "whale_bar", price, float(df["volume"].iloc[-1]), vol_mult)

                # -- 3. Сигнал модели -----------------------------------------
                ob_imb = get_orderbook_imbalance(symbol=sym)
                signal, p_hold, p_long, p_short = model_mgr.predict_signal(df)

                log_signal(sym, signal, p_hold, p_long, p_short, price, atr, sm_bias, ob_imb)

                if signal == 1:
                    n_long += 1
                if signal == 2:
                    n_short += 1

                signal_label = {0: "HOLD", 1: "LONG", 2: "SHORT"}.get(signal, "?")
                logger.info(
                    "  %-14s | %8.2f | %-5s | L=%.2f S=%.2f | sm=%+d",
                    sym, price, signal_label, p_long, p_short, sm_bias,
                )

                # -- 4. Управление позицией -----------------------------------
                pos = positions[sym]
                if pos.is_open:
                    manage_position(sym, pos, price, atr)

                # -- 5. Открытие новой позиции --------------------------------
                if not pos.is_open and not loss_guard.halted and model_mgr.is_trained():
                    qty = calc_qty(price, atr, equity_usdt=1000.0)
                    if signal == 1 and sm_bias >= 0:
                        pos.open("Buy", price, qty,
                                 sl=price - cfg.SL_ATR_MULT * atr,
                                 tp=price + cfg.TP_ATR_MULT * atr)
                    elif signal == 2 and sm_bias <= 0:
                        pos.open("Sell", price, qty,
                                 sl=price + cfg.SL_ATR_MULT * atr,
                                 tp=price - cfg.TP_ATR_MULT * atr)

            except Exception as exc:
                logger.warning("  %s error: %s", sym, exc)

        # -- 6. Переобучение модели -------------------------------------------
        model_mgr.bars_since_retrain += 1
        if model_mgr.bars_since_retrain >= cfg.RETRAIN_EVERY_N:
            ref_hist = histories.get(cfg.SYMBOL)
            if ref_hist is not None and len(ref_hist) >= cfg.RETRAIN_MIN_SAMPLES:
                logger.info("Retrain triggered (bar #%d)...", bar_count)
                accepted = model_mgr.try_retrain(ref_hist)
                logger.info("Retrain result: %s", "ACCEPTED" if accepted else "REJECTED (no improvement)")
            else:
                logger.info("Not enough data for retrain, skipping.")

        logger.info("  Total: LONG=%d SHORT=%d | %.1fs per cycle",
                    n_long, n_short, time.time() - loop_start)

        # -- Ждём следующего бара --------------------------------------------
        elapsed = time.time() - loop_start
        sleep_for = max(5, 60 - elapsed)
        logger.debug("Sleeping %.0fs until next bar...", sleep_for)
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break


if __name__ == "__main__":
    main()
