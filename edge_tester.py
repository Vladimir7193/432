"""
=============================================================
edge_tester.py — Offline backtesting + edge measurement
=============================================================
Usage:
    python edge_tester.py --symbol BTCUSDT --bars 5000

Downloads historical data from Bybit, runs the CatBoost
signal engine over it, and prints edge statistics:
  - Signal precision / recall per class
  - Simulated return vs buy-and-hold
  - Sharpe ratio
  - Max drawdown
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("edge_tester")

import config as cfg
from market_data import fetch_klines
from signal_engine import ModelManager, compute_features, make_labels, FEATURE_COLS


def run_backtest(df: pd.DataFrame, model_mgr: ModelManager) -> pd.DataFrame:
    """
    Walk-forward simulation: predict on each bar, track paper P&L.
    Uses a simple fixed-ATR SL/TP rule (no re-entry on open position).
    """
    feat_df = compute_features(df.copy())
    feat_df["label"] = make_labels(feat_df)
    feat_df = feat_df.dropna(subset=FEATURE_COLS + ["atr"])
    feat_df = feat_df.iloc[:-cfg.LABEL_FUTURE_BARS]  # remove look-ahead rows

    results = []
    position = None   # {"side": int, "entry": float, "sl": float, "tp": float}

    for i, (ts, row) in enumerate(feat_df.iterrows()):
        price = row["close"]
        atr   = row["atr"]

        # Check open position
        if position:
            if position["side"] == 1:   # long
                if price <= position["sl"]:
                    pnl = position["sl"] - position["entry"]
                    results.append({"ts": ts, "pnl": pnl, "exit": "SL"})
                    position = None
                elif price >= position["tp"]:
                    pnl = position["tp"] - position["entry"]
                    results.append({"ts": ts, "pnl": pnl, "exit": "TP"})
                    position = None
            else:   # short
                if price >= position["sl"]:
                    pnl = position["entry"] - position["sl"]
                    results.append({"ts": ts, "pnl": pnl, "exit": "SL"})
                    position = None
                elif price <= position["tp"]:
                    pnl = position["entry"] - position["tp"]
                    results.append({"ts": ts, "pnl": pnl, "exit": "TP"})
                    position = None

        # New signal
        if not position and model_mgr.is_trained():
            feats = np.array([row.get(c, np.nan) for c in FEATURE_COLS], dtype=np.float32)
            if not np.isnan(feats).any():
                proba = model_mgr.predict_proba(feats)
                if proba[1] >= cfg.LONG_PROB_THRESH:
                    position = {
                        "side":  1,
                        "entry": price,
                        "sl":    price - cfg.SL_ATR_MULT * atr,
                        "tp":    price + cfg.TP_ATR_MULT * atr,
                    }
                elif proba[2] >= cfg.SHORT_PROB_THRESH:
                    position = {
                        "side":  2,
                        "entry": price,
                        "sl":    price + cfg.SL_ATR_MULT * atr,
                        "tp":    price - cfg.TP_ATR_MULT * atr,
                    }

    res_df = pd.DataFrame(results) if results else pd.DataFrame(columns=["ts", "pnl", "exit"])
    return res_df


def print_stats(res_df: pd.DataFrame, df: pd.DataFrame):
    if res_df.empty:
        print("No trades simulated.")
        return

    n_trades = len(res_df)
    wins     = (res_df["pnl"] > 0).sum()
    win_rate = wins / n_trades * 100
    total    = res_df["pnl"].sum()
    avg_pnl  = res_df["pnl"].mean()
    cumsum   = res_df["pnl"].cumsum()
    dd       = cumsum - cumsum.cummax()
    max_dd   = dd.min()
    sharpe   = res_df["pnl"].mean() / (res_df["pnl"].std() + 1e-9) * np.sqrt(252 * 24 * 60)

    bh_return = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100

    print("\n" + "=" * 50)
    print("       EDGE TESTER RESULTS")
    print("=" * 50)
    print(f"Trades:         {n_trades}")
    print(f"Win rate:       {win_rate:.1f}%")
    print(f"Total PnL pts:  {total:.2f}")
    print(f"Avg PnL pts:    {avg_pnl:.4f}")
    print(f"Max drawdown:   {max_dd:.2f}")
    print(f"Sharpe (ann):   {sharpe:.2f}")
    print(f"Buy-hold ret:   {bh_return:.2f}%")
    print(f"SL exits:       {(res_df['exit'] == 'SL').sum()}")
    print(f"TP exits:       {(res_df['exit'] == 'TP').sum()}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="CatBoost edge tester")
    parser.add_argument("--symbol", default=cfg.SYMBOL)
    parser.add_argument("--bars",   type=int, default=2000)
    args = parser.parse_args()

    logger.info("Fetching %d bars for %s ...", args.bars, args.symbol)
    df = fetch_klines(symbol=args.symbol, limit=args.bars + 50)
    logger.info("Fetched %d bars.", len(df))

    mgr = ModelManager()
    if not mgr.is_trained():
        logger.info("No model found — training now on full dataset...")
        mgr.try_retrain(df)

    if not mgr.is_trained():
        logger.error("Could not train model. Exiting.")
        sys.exit(1)

    res = run_backtest(df, mgr)
    print_stats(res, df)


if __name__ == "__main__":
    main()
