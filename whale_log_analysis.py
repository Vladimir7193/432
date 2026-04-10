"""
=============================================================
whale_log_analysis.py — Offline analysis of whale_events.csv
=============================================================
Usage:
    python whale_log_analysis.py
    python whale_log_analysis.py --csv logs/whale_events.csv --plot

Outputs:
  - Summary statistics per event type
  - Correlation between whale events and subsequent price moves
  - Optional matplotlib chart
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

import config as cfg
from market_data import fetch_klines
from signal_engine import compute_features


def load_whale_log(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}")
        sys.exit(1)
    df = pd.read_csv(path, parse_dates=["ts"])
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def correlate_with_price(whale_df: pd.DataFrame, ohlcv: pd.DataFrame, fwd_bars: int = 5):
    """
    For each whale event, look up the next fwd_bars candles and compute
    the forward return. Returns an enriched DataFrame.
    """
    ohlcv = ohlcv.copy()
    ohlcv.index = pd.to_datetime(ohlcv.index, utc=True)

    results = []
    for _, row in whale_df.iterrows():
        ts = pd.Timestamp(row["ts"]).tz_convert("UTC")
        # Find the closest bar at or after the event
        future = ohlcv[ohlcv.index >= ts]
        if len(future) < fwd_bars:
            continue
        entry_close = future["close"].iloc[0]
        fwd_close   = future["close"].iloc[fwd_bars - 1]
        fwd_ret     = (fwd_close - entry_close) / entry_close * 100
        results.append({
            "ts":         row["ts"],
            "event_type": row["event_type"],
            "price":      row["price"],
            "vol_mult":   row["vol_mult"],
            "fwd_ret_%":  round(fwd_ret, 4),
        })

    return pd.DataFrame(results)


def print_analysis(enriched: pd.DataFrame):
    print("\n" + "=" * 55)
    print("          WHALE LOG ANALYSIS")
    print("=" * 55)

    if enriched.empty:
        print("No whale events to analyse.")
        return

    print(f"\nTotal events:  {len(enriched)}")
    print(f"Date range:    {enriched['ts'].min()} → {enriched['ts'].max()}")

    print("\n── By event type ─────────────────────────────────────")
    for etype, grp in enriched.groupby("event_type"):
        avg_ret = grp["fwd_ret_%"].mean()
        med_ret = grp["fwd_ret_%"].median()
        pos_pct = (grp["fwd_ret_%"] > 0).mean() * 100
        print(
            f"  {etype:<18} n={len(grp):>4}  "
            f"avg_fwd={avg_ret:+.3f}%  "
            f"med={med_ret:+.3f}%  "
            f"pos={pos_pct:.0f}%"
        )

    print("\n── Volume multiplier vs forward return (correlation) ──")
    corr = enriched["vol_mult"].corr(enriched["fwd_ret_%"])
    print(f"  Pearson corr (vol_mult vs fwd_ret): {corr:.4f}")

    print("\n── Percentile distribution of forward returns ─────────")
    for pct in [10, 25, 50, 75, 90]:
        val = enriched["fwd_ret_%"].quantile(pct / 100)
        print(f"  P{pct:02d}: {val:+.4f}%")

    print("=" * 55)


def plot_analysis(enriched: pd.DataFrame):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot.")
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    fig.suptitle("Whale Event Analysis", fontsize=14)

    # Forward return distribution
    ax = axes[0]
    ax.hist(enriched["fwd_ret_%"], bins=40, color="#636efa", edgecolor="white")
    ax.axvline(0, color="red", linestyle="--", linewidth=1.5)
    ax.set_title("Forward Return Distribution")
    ax.set_xlabel("5-bar forward return (%)")
    ax.set_ylabel("Count")

    # Vol multiplier vs fwd return scatter
    ax2 = axes[1]
    colors = enriched["fwd_ret_%"].apply(lambda x: "green" if x > 0 else "red")
    ax2.scatter(enriched["vol_mult"], enriched["fwd_ret_%"], c=colors, alpha=0.5, s=15)
    ax2.axhline(0, color="white", linestyle="--", linewidth=1)
    ax2.set_title("Volume Multiplier vs Forward Return")
    ax2.set_xlabel("Volume multiplier (vs rolling mean)")
    ax2.set_ylabel("5-bar forward return (%)")

    plt.tight_layout()
    out = "logs/whale_analysis.png"
    plt.savefig(out, dpi=120)
    print(f"\nChart saved to {out}")
    plt.show()


def main():
    parser = argparse.ArgumentParser(description="Whale log analyser")
    parser.add_argument("--csv",     default=cfg.WHALE_LOG_CSV, help="Path to whale_events.csv")
    parser.add_argument("--fwd",     type=int, default=5,        help="Forward bars for return calc")
    parser.add_argument("--plot",    action="store_true",         help="Show matplotlib chart")
    parser.add_argument("--bars",    type=int, default=2000,      help="OHLCV bars to fetch for context")
    args = parser.parse_args()

    print(f"Loading whale log: {args.csv}")
    whale_df = load_whale_log(args.csv)
    print(f"  {len(whale_df)} events loaded.")

    print(f"Fetching {args.bars} OHLCV bars from Bybit...")
    try:
        ohlcv = fetch_klines(limit=args.bars)
    except Exception as e:
        print(f"[WARN] Could not fetch live data: {e}. Using price from log only.")
        enriched = whale_df.copy()
        enriched["fwd_ret_%"] = np.nan
        print_analysis(enriched)
        return

    enriched = correlate_with_price(whale_df, ohlcv, fwd_bars=args.fwd)
    print_analysis(enriched)

    if args.plot:
        plot_analysis(enriched)


if __name__ == "__main__":
    main()
