"""
=============================================================
mtf_analyzer.py — Multi-timeframe signal analysis
=============================================================
Для каждой монеты и каждого ТФ (5m/15m/1h/4h):
  - Загружает OHLCV
  - Прогоняет через существующую модель пары
  - Возвращает сигнал + TP/SL уровни на основе ATR этого ТФ
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from market_data import fetch_klines_multi_tf
from signal_engine import compute_features, FEATURE_COLS

logger = logging.getLogger(__name__)


@dataclass
class TFSignal:
    interval: str
    label: str          # "5m", "15m", "1h", "4h"
    signal: int         # 0=hold 1=long 2=short
    p_long: float
    p_short: float
    p_hold: float
    price: float
    atr: float
    tp: float           # absolute price level
    sl: float           # absolute price level
    tp_pct: float       # % from entry
    sl_pct: float       # % from entry
    rr: float           # reward/risk ratio


@dataclass
class MTFResult:
    symbol: str
    signals: list[TFSignal] = field(default_factory=list)
    best_tf: Optional[str] = None      # interval with highest confidence
    best_signal: int = 0
    best_prob: float = 0.0
    confluence: int = 0                # how many TFs agree on direction


def analyze_symbol_mtf(symbol: str, model_mgr) -> MTFResult:
    """
    Fetch multi-TF data and run model predictions for one symbol.

    Args:
        symbol:    e.g. "BTCUSDT"
        model_mgr: MultiModelManager instance (already loaded)

    Returns:
        MTFResult with per-TF signals and summary
    """
    result = MTFResult(symbol=symbol)

    tf_data = fetch_klines_multi_tf(symbol, cfg.MTF_INTERVALS, limit=cfg.MTF_LOOKBACK)
    if not tf_data:
        return result

    for iv in cfg.MTF_INTERVALS:
        df = tf_data.get(iv)
        if df is None or len(df) < 50:
            continue

        try:
            feat_df = compute_features(df)
            last = feat_df.iloc[-1]

            missing = [c for c in FEATURE_COLS if pd.isna(last.get(c, np.nan))]
            if missing:
                continue

            feats = np.array([last[c] for c in FEATURE_COLS], dtype=np.float32)
            model = model_mgr.models.get(symbol)
            if model is None or not model.is_fitted():
                continue

            proba = model.predict_proba(feats.reshape(1, -1))[0]
            p_hold, p_long, p_short = float(proba[0]), float(proba[1]), float(proba[2])

            if p_long >= cfg.LONG_PROB_THRESH:
                signal = 1
            elif p_short >= cfg.SHORT_PROB_THRESH:
                signal = 2
            else:
                signal = 0

            price = float(last["close"])
            atr   = float(last.get("atr", price * 0.01))

            if signal == 1:
                tp = price + cfg.TP_ATR_MULT * atr
                sl = price - cfg.SL_ATR_MULT * atr
            elif signal == 2:
                tp = price - cfg.TP_ATR_MULT * atr
                sl = price + cfg.SL_ATR_MULT * atr
            else:
                tp = price + cfg.TP_ATR_MULT * atr
                sl = price - cfg.SL_ATR_MULT * atr

            tp_pct = abs(tp - price) / price * 100
            sl_pct = abs(sl - price) / price * 100
            rr     = tp_pct / sl_pct if sl_pct > 0 else 0.0

            tf_sig = TFSignal(
                interval=iv,
                label=cfg.MTF_INTERVAL_LABELS.get(iv, iv),
                signal=signal,
                p_long=round(p_long, 3),
                p_short=round(p_short, 3),
                p_hold=round(p_hold, 3),
                price=price,
                atr=round(atr, 6),
                tp=round(tp, 6),
                sl=round(sl, 6),
                tp_pct=round(tp_pct, 2),
                sl_pct=round(sl_pct, 2),
                rr=round(rr, 2),
            )
            result.signals.append(tf_sig)

        except Exception as exc:
            logger.warning("MTF analysis error %s @%s: %s", symbol, iv, exc)

    # ── Summary ──────────────────────────────────────────────────────────────
    if result.signals:
        # Best TF = highest max(p_long, p_short) when signal != 0
        active = [s for s in result.signals if s.signal != 0]
        if active:
            best = max(active, key=lambda s: max(s.p_long, s.p_short))
            result.best_tf     = best.label
            result.best_signal = best.signal
            result.best_prob   = max(best.p_long, best.p_short)

            # Confluence: count TFs with same direction
            result.confluence = sum(
                1 for s in result.signals if s.signal == best.signal
            )

    return result
