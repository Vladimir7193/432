"""
=============================================================
bybit_live_bot.py — РЕАЛЬНАЯ торговля на Bybit mainnet
=============================================================
Основан на bybit_paper_bot.py, но вместо in-memory позиций
отправляет настоящие ордера через Bybit API v5.

Настройки для малого депозита ($10-50):
  LIVE_BALANCE   — твой реальный баланс USDT
  LIVE_LEVERAGE  — плечо (10x для малого депо)
  LIVE_RISK_PCT  — риск на сделку (2%)
  MAX_POSITIONS  — макс позиций (1 для $10, 2-3 для $50+)

Запуск: py -3.12 bybit_live_bot.py
=============================================================
"""
from __future__ import annotations

import io
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pybit.unified_trading import HTTP

import config as cfg
from market_data import fetch_klines, get_orderbook_imbalance, get_ticker
from signal_engine import ModelManager, compute_features
from smart_money import get_bias_from_smart_money, detect_whale_bars
from signal_logger import log_signal, log_trade, log_whale_event

# UTF-8 fix для Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
#  LIVE НАСТРОЙКИ  (меняй здесь)
# ─────────────────────────────────────────────────────────────────────────────

LIVE_API_KEY    = os.getenv("BYBIT_API_KEY",    cfg.API_KEY)
LIVE_API_SECRET = os.getenv("BYBIT_API_SECRET", cfg.API_SECRET)

LIVE_BALANCE    = 10.0    # твой депозит USDT (обновляется автоматически)
LIVE_LEVERAGE   = 10      # плечо (10x для $10, можно 15x для $50+)
LIVE_RISK_PCT   = 0.02    # 2% риска на сделку
LIVE_MAX_POS    = 1       # макс позиций одновременно ($10 → 1, $50+ → 2-3)
LIVE_SL_MULT    = cfg.SL_ATR_MULT   # 1.5×ATR
LIVE_TP_MULT    = cfg.TP_ATR_MULT   # 2.5×ATR
MIN_NOTIONAL    = 5.0     # минимальный ордер Bybit ($5)

# Пары для торговли (выбраны по ликвидности и волатильности)
LIVE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "ARBUSDT", "APTUSDT", "BNBUSDT",
]

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────

Path(cfg.LOG_PATH).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(cfg.LOG_PATH + "live_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bybit_live_bot")


# ─────────────────────────────────────────────────────────────────────────────
#  BYBIT SESSION
# ─────────────────────────────────────────────────────────────────────────────

def _session() -> HTTP:
    return HTTP(testnet=False, api_key=LIVE_API_KEY, api_secret=LIVE_API_SECRET)


# ─────────────────────────────────────────────────────────────────────────────
#  ACCOUNT
# ─────────────────────────────────────────────────────────────────────────────

def get_balance() -> float:
    """Получить доступный баланс USDT."""
    try:
        resp = _session().get_wallet_balance(accountType="UNIFIED", coin="USDT")
        ret_code = resp.get("retCode", -1)
        if ret_code != 0:
            logger.error("Balance API error: retCode=%s msg=%s", ret_code, resp.get("retMsg"))
            return 0.0
        account_list = resp["result"]["list"]
        if not account_list:
            logger.error("Balance: empty account list. Check Unified Trading Account.")
            return 0.0
        account = account_list[0]
        for c in account.get("coin", []):
            if c["coin"] == "USDT":
                # availableToWithdraw can be '' in Unified accounts — use fallback chain
                raw = (c.get("availableToWithdraw") or
                       c.get("walletBalance") or
                       account.get("totalAvailableBalance") or "0")
                val = float(raw) if raw not in ("", None) else 0.0
                logger.info("Balance OK: $%.4f USDT", val)
                return val
        # USDT coin not in list — use account-level balance
        raw = account.get("totalAvailableBalance") or account.get("totalWalletBalance") or "0"
        val = float(raw) if raw not in ("", None) else 0.0
        logger.warning("USDT coin not found, using account balance: $%.4f", val)
        return val
    except Exception as e:
        logger.error("Balance error: %s", e)
    return 0.0


def get_positions() -> dict[str, dict]:
    """Получить все открытые позиции {symbol: data}."""
    try:
        resp = _session().get_positions(category="linear", settleCoin="USDT")
        result = {}
        for p in resp["result"]["list"]:
            size = float(p.get("size", 0))
            if size > 0:
                result[p["symbol"]] = {
                    "side":        p["side"],          # "Buy" | "Sell"
                    "size":        size,
                    "entry_price": float(p["avgPrice"]),
                    "unreal_pnl":  float(p.get("unrealisedPnl", 0)),
                    "liq_price":   float(p.get("liqPrice", 0)),
                    "sl":          float(p.get("stopLoss", 0)),
                    "tp":          float(p.get("takeProfit", 0)),
                }
        return result
    except Exception as e:
        logger.error("Positions error: %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
#  INSTRUMENT INFO
# ─────────────────────────────────────────────────────────────────────────────

_instrument_cache: dict[str, dict] = {}

def get_instrument_info(symbol: str) -> dict:
    """Получить min qty, qty step, tick size для символа."""
    if symbol in _instrument_cache:
        return _instrument_cache[symbol]
    try:
        resp = _session().get_instruments_info(category="linear", symbol=symbol)
        item = resp["result"]["list"][0]
        lot  = item["lotSizeFilter"]
        pf   = item["priceFilter"]
        info = {
            "min_qty":   float(lot["minOrderQty"]),
            "qty_step":  float(lot["qtyStep"]),
            "tick_size": float(pf["tickSize"]),
        }
        _instrument_cache[symbol] = info
        return info
    except Exception as e:
        logger.warning("Instrument info error [%s]: %s", symbol, e)
        return {"min_qty": 0.001, "qty_step": 0.001, "tick_size": 0.0001}


def _round_qty(qty: float, step: float) -> float:
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(qty / step) * step, decimals)


def _round_price(price: float, tick: float) -> float:
    decimals = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return round(round(price / tick) * tick, decimals)


# ─────────────────────────────────────────────────────────────────────────────
#  LEVERAGE
# ─────────────────────────────────────────────────────────────────────────────

def set_leverage(symbol: str) -> bool:
    try:
        _session().set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(LIVE_LEVERAGE),
            sellLeverage=str(LIVE_LEVERAGE),
        )
        return True
    except Exception as e:
        if "110043" in str(e) or "not modified" in str(e).lower():
            return True  # уже выставлено
        logger.warning("Leverage error [%s]: %s", symbol, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────

def calc_qty(price: float, atr: float, balance: float, info: dict) -> float | None:
    """Рассчитать размер позиции с учётом min notional и qty step."""
    risk_usdt = balance * LIVE_RISK_PCT
    sl_dist   = LIVE_SL_MULT * atr
    qty       = risk_usdt / (sl_dist + 1e-9)

    # Ограничение по плечу
    max_qty = (balance * LIVE_LEVERAGE) / (price + 1e-9)
    qty     = min(qty, max_qty)

    # Минимальный notional $5
    if qty * price < MIN_NOTIONAL:
        qty = (MIN_NOTIONAL * 1.1) / price

    qty = _round_qty(qty, info["qty_step"])

    if qty < info["min_qty"]:
        logger.warning("Qty %.6f < min %.6f [%s]", qty, info["min_qty"], "symbol")
        return None
    if qty * price < MIN_NOTIONAL:
        logger.warning("Notional $%.2f < min $%.2f", qty * price, MIN_NOTIONAL)
        return None

    return qty


# ─────────────────────────────────────────────────────────────────────────────
#  ORDERS
# ─────────────────────────────────────────────────────────────────────────────

def open_position(symbol: str, side: str, price: float,
                  atr: float, balance: float) -> bool:
    """Открыть позицию с SL и TP."""
    info = get_instrument_info(symbol)
    qty  = calc_qty(price, atr, balance, info)
    if qty is None:
        return False

    tick = info["tick_size"]
    if side == "Buy":
        sl = _round_price(price - LIVE_SL_MULT * atr, tick)
        tp = _round_price(price + LIVE_TP_MULT * atr, tick)
    else:
        sl = _round_price(price + LIVE_SL_MULT * atr, tick)
        tp = _round_price(price - LIVE_TP_MULT * atr, tick)

    notional = qty * price
    logger.info("Placing %s %s | price=%.4f | qty=%.6f | notional=$%.2f | SL=%.4f | TP=%.4f",
                side, symbol, price, qty, notional, sl, tp)

    try:
        resp = _session().place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=str(sl),
            takeProfit=str(tp),
            slTriggerBy="MarkPrice",
            tpTriggerBy="MarkPrice",
            timeInForce="IOC",
            reduceOnly=False,
        )
        if resp.get("retCode") == 0:
            oid = resp["result"].get("orderId", "?")
            logger.info("✅ OPENED %s %s | orderId=%s | SL=%.4f | TP=%.4f",
                        side, symbol, oid, sl, tp)
            log_trade(
                symbol=symbol, side=side, qty=qty,
                entry_price=price, exit_price=0,
                sl=sl, tp=tp, exit_reason="OPEN", duration_sec=0,
            )
            return True
        else:
            logger.error("❌ Order failed [%s]: retCode=%s msg=%s",
                         symbol, resp.get("retCode"), resp.get("retMsg"))
            return False
    except Exception as e:
        logger.error("❌ Order exception [%s]: %s", symbol, e)
        return False


def close_position(symbol: str, side: str, qty: float, reason: str = "MANUAL") -> bool:
    """Закрыть позицию рыночным ордером."""
    close_side = "Sell" if side == "Buy" else "Buy"
    try:
        resp = _session().place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Market",
            qty=str(qty),
            reduceOnly=True,
            timeInForce="IOC",
        )
        if resp.get("retCode") == 0:
            logger.info("✅ CLOSED %s %s | reason=%s", side, symbol, reason)
            return True
        else:
            logger.error("❌ Close failed [%s]: %s", symbol, resp.get("retMsg"))
            return False
    except Exception as e:
        logger.error("❌ Close exception [%s]: %s", symbol, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  DAILY LOSS GUARD
# ─────────────────────────────────────────────────────────────────────────────

class DailyLossGuard:
    def __init__(self):
        self.start_balance: float = 0.0
        self.date: str = ""
        self.halted: bool = False

    def update(self, balance: float) -> bool:
        """Вернуть True если торговля разрешена."""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if today != self.date:
            self.date = today
            self.start_balance = balance
            self.halted = False
            logger.info("New trading day. Start balance: $%.4f", balance)

        if self.start_balance > 0:
            loss_pct = (self.start_balance - balance) / self.start_balance
            if loss_pct >= cfg.MAX_DAILY_LOSS_PCT:
                if not self.halted:
                    logger.warning("🛑 Daily loss limit %.1f%% hit. Halted for today.", loss_pct * 100)
                self.halted = True
                return False

        return True


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("CatBoost LIVE BOT — Bybit Mainnet")
    logger.info("Leverage: %dx | Risk/trade: %.0f%% | Max positions: %d",
                LIVE_LEVERAGE, LIVE_RISK_PCT * 100, LIVE_MAX_POS)
    logger.info("Symbols: %s", ", ".join(LIVE_SYMBOLS))
    logger.info("=" * 60)

    # Проверка подключения
    balance = get_balance()
    if balance == 0.0:
        logger.error("Failed to get balance. Check API keys.")
        logger.error("  Required permissions: Contract Trade -> Read + Write")
        logger.error("  Account Type: Unified Trading Account")
        logger.error("  Make sure keys are MAINNET (not testnet)")
        return

    logger.info("✅ Подключено | Баланс: $%.4f USDT", balance)

    if balance < MIN_NOTIONAL:
        logger.error("Balance $%.2f is below minimum order $%.2f. Please deposit more USDT.", balance, MIN_NOTIONAL)
        return

    model_mgr  = ModelManager()
    guard      = DailyLossGuard()
    histories: dict[str, pd.DataFrame | None] = {s: None for s in LIVE_SYMBOLS}
    bar_count  = 0

    # Выставить плечо для всех пар
    logger.info("Выставляю плечо %dx для всех пар...", LIVE_LEVERAGE)
    for sym in LIVE_SYMBOLS:
        set_leverage(sym)
        time.sleep(0.3)

    while True:
        loop_start = time.time()
        bar_count += 1

        try:
            balance = get_balance()
            if not guard.update(balance):
                logger.info("Торговля приостановлена. Жду 60с...")
                time.sleep(60)
                continue

            open_pos = get_positions()
            logger.info(
                "--- Bar #%d | Balance=$%.4f | Positions=%s ---",
                bar_count, balance, list(open_pos.keys()) if open_pos else "none",
            )

            # Логируем открытые позиции
            for sym, pos in open_pos.items():
                logger.info(
                    "  [OPEN] %s %s | entry=%.4f | PnL=%.4f | SL=%.4f | TP=%.4f | liq=%.4f",
                    pos["side"], sym, pos["entry_price"],
                    pos["unreal_pnl"], pos["sl"], pos["tp"], pos["liq_price"],
                )

            # Сканируем пары для новых входов
            for sym in LIVE_SYMBOLS:
                # Уже есть позиция по этой паре
                if sym in open_pos:
                    continue

                # Достигнут лимит позиций
                if len(open_pos) >= LIVE_MAX_POS:
                    break

                try:
                    df = fetch_klines(symbol=sym, limit=cfg.LOOKBACK + 50)
                    if df is None or len(df) < cfg.LOOKBACK:
                        continue

                    # Обновляем историю
                    hist = histories[sym]
                    histories[sym] = (
                        pd.concat([hist, df]).drop_duplicates().sort_index()
                        .iloc[-(cfg.TRAIN_WINDOW_BARS + 200):]
                        if hist is not None else df
                    )

                    feat_df = compute_features(df)
                    last    = feat_df.iloc[-1]
                    price   = float(last["close"])
                    atr     = float(last.get("atr", price * 0.001))

                    # Smart money bias
                    sm_bias = get_bias_from_smart_money(df)

                    # Whale detection
                    whale_ser = detect_whale_bars(df)
                    if whale_ser.iloc[-1]:
                        vol_mult = df["volume"].iloc[-1] / (
                            df["volume"].iloc[-cfg.WHALE_LOOKBACK:].mean() + 1e-9
                        )
                        log_whale_event(sym, "whale_bar", price,
                                        float(df["volume"].iloc[-1]), vol_mult)

                    # CatBoost сигнал
                    ob_imb = get_orderbook_imbalance(symbol=sym)
                    signal, p_hold, p_long, p_short = model_mgr.predict_signal(df)
                    log_signal(sym, signal, p_hold, p_long, p_short, price, atr, sm_bias, ob_imb)

                    label = {0: "HOLD", 1: "LONG", 2: "SHORT"}.get(signal, "?")
                    logger.info(
                        "  %-14s | %10.4f | %-5s | L=%.2f S=%.2f | sm=%+d",
                        sym, price, label, p_long, p_short, sm_bias,
                    )

                    # Открываем позицию
                    if not model_mgr.is_trained():
                        continue

                    if signal == 1 and sm_bias >= 0:
                        set_leverage(sym)
                        time.sleep(0.3)
                        success = open_position(sym, "Buy", price, atr, balance)
                        if success:
                            open_pos[sym] = {"side": "Buy", "size": 0, "entry_price": price,
                                             "unreal_pnl": 0, "liq_price": 0, "sl": 0, "tp": 0}
                            time.sleep(1)

                    elif signal == 2 and sm_bias <= 0:
                        set_leverage(sym)
                        time.sleep(0.3)
                        success = open_position(sym, "Sell", price, atr, balance)
                        if success:
                            open_pos[sym] = {"side": "Sell", "size": 0, "entry_price": price,
                                             "unreal_pnl": 0, "liq_price": 0, "sl": 0, "tp": 0}
                            time.sleep(1)

                except Exception as e:
                    logger.warning("  %s error: %s", sym, e)

            # Переобучение модели
            model_mgr.bars_since_retrain += 1
            if model_mgr.bars_since_retrain >= cfg.RETRAIN_EVERY_N:
                ref = histories.get(cfg.SYMBOL) or histories.get(LIVE_SYMBOLS[0])
                if ref is not None and len(ref) >= cfg.RETRAIN_MIN_SAMPLES:
                    logger.info("Retrain triggered...")
                    accepted = model_mgr.try_retrain(ref)
                    logger.info("Retrain: %s", "ACCEPTED" if accepted else "REJECTED")

        except Exception as e:
            logger.error("Cycle error: %s", e, exc_info=True)

        elapsed    = time.time() - loop_start
        sleep_time = max(5, 60 - elapsed)
        logger.info("Cycle done in %.1fs. Next in %.0fs.", elapsed, sleep_time)
        try:
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            logger.info("Bot stopped.")
            break


if __name__ == "__main__":
    main()
