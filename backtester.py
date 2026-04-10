"""
=============================================================
backtester.py — Backtest on real historical Bybit data
=============================================================
Запуск:
    python backtester.py                  # все 30 пар
    python backtester.py BTCUSDT ETHUSDT  # конкретные пары
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as cfg
from market_data import fetch_klines
from signal_engine import compute_features, make_labels, FEATURE_COLS
from multi_model_manager import MultiModelManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backtester")


@dataclass
class Trade:
    symbol: str
    side: str           # "Buy" / "Sell"
    entry_price: float
    exit_price: float
    qty: float
    sl: float
    tp: float
    exit_reason: str    # "TP" / "SL" / "EOD"
    entry_bar: int
    exit_bar: int
    pnl: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.pnl > 0) / len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        eq = np.array(self.equity_curve)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / (peak + 1e-9)
        return float(dd.min())

    @property
    def sharpe(self) -> float:
        if len(self.trades) < 2:
            return 0.0
        pnls = np.array([t.pnl_pct for t in self.trades])
        return float(pnls.mean() / (pnls.std() + 1e-9) * np.sqrt(252))

    @property
    def profit_factor(self) -> float:
        wins  = sum(t.pnl for t in self.trades if t.pnl > 0)
        losses = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        return wins / losses if losses > 0 else float("inf")


def _run_symbol(
    symbol: str,
    df: pd.DataFrame,
    model_mgr: MultiModelManager,
    initial_equity: float = cfg.BACKTEST_INITIAL_EQUITY,
) -> BacktestResult:
    """Run backtest for one symbol on provided OHLCV DataFrame."""
    result = BacktestResult(symbol=symbol)
    equity = initial_equity
    result.equity_curve.append(equity)

    feat_df = compute_features(df.copy())
    feat_df["label"] = make_labels(feat_df)
    feat_df = feat_df.dropna(subset=FEATURE_COLS + ["atr"])

    if len(feat_df) < cfg.LOOKBACK + 10:
        logger.warning("%s: not enough data for backtest (%d bars)", symbol, len(feat_df))
        return result

    model = model_mgr.models.get(symbol)
    if model is None or not model.is_fitted():
        logger.warning("%s: model not trained, skipping", symbol)
        return result

    in_trade = False
    side = ""
    entry_price = 0.0
    sl = tp = qty = 0.0
    entry_bar = 0

    bars = feat_df.reset_index(drop=True)

    for i in range(cfg.LOOKBACK, len(bars)):
        row = bars.iloc[i]
        price = float(row["close"])
        high  = float(row["high"])
        low   = float(row["low"])
        atr   = float(row.get("atr", price * 0.01))

        # ── Manage open trade ────────────────────────────────────────────────
        if in_trade:
            closed = False
            exit_reason = ""
            exit_price = price

            if side == "Buy":
                if low <= sl:
                    exit_price, exit_reason = sl, "SL"
                    closed = True
                elif high >= tp:
                    exit_price, exit_reason = tp, "TP"
                    closed = True
            else:
                if high >= sl:
                    exit_price, exit_reason = sl, "SL"
                    closed = True
                elif low <= tp:
                    exit_price, exit_reason = tp, "TP"
                    closed = True

            if closed:
                raw_pnl = (exit_price - entry_price) * qty * (1 if side == "Buy" else -1)
                fee = (entry_price + exit_price) * qty * cfg.TAKER_FEE
                pnl = raw_pnl - fee
                pnl_pct = pnl / (entry_price * qty + 1e-9) * 100
                equity += pnl
                result.equity_curve.append(equity)

                result.trades.append(Trade(
                    symbol=symbol, side=side,
                    entry_price=entry_price, exit_price=exit_price,
                    qty=qty, sl=sl, tp=tp,
                    exit_reason=exit_reason,
                    entry_bar=entry_bar, exit_bar=i,
                    pnl=round(pnl, 4), pnl_pct=round(pnl_pct, 4),
                ))
                in_trade = False

                # Drawdown protection
                peak = max(result.equity_curve)
                if (equity - peak) / peak < -cfg.MAX_DRAWDOWN_PCT:
                    logger.info("%s: drawdown limit hit, stopping backtest", symbol)
                    break

        # ── Generate signal ──────────────────────────────────────────────────
        if not in_trade:
            missing = [c for c in FEATURE_COLS if pd.isna(row.get(c, np.nan))]
            if missing:
                continue

            feats = np.array([row[c] for c in FEATURE_COLS], dtype=np.float32)
            proba = model.predict_proba(feats.reshape(1, -1))[0]
            p_hold, p_long, p_short = float(proba[0]), float(proba[1]), float(proba[2])

            if p_long >= cfg.LONG_PROB_THRESH:
                signal = 1
            elif p_short >= cfg.SHORT_PROB_THRESH:
                signal = 2
            else:
                signal = 0

            if signal == 0:
                continue

            # Dynamic position sizing
            risk_usdt = equity * cfg.RISK_PCT
            if cfg.DYNAMIC_SIZING:
                vol_window = bars["close"].iloc[max(0, i - cfg.VOL_SCALE_WINDOW):i].pct_change().std()
                if vol_window > 0:
                    scale = cfg.VOL_SCALE_TARGET / (vol_window + 1e-9)
                    scale = min(max(scale, 0.3), 2.0)
                    risk_usdt *= scale

            sl_dist = cfg.SL_ATR_MULT * atr
            qty_new = risk_usdt / (sl_dist + 1e-9)
            max_qty = equity * cfg.LEVERAGE / price
            qty_new = min(qty_new, max_qty)

            if signal == 1:
                side = "Buy"
                sl = price - cfg.SL_ATR_MULT * atr
                tp = price + cfg.TP_ATR_MULT * atr
            else:
                side = "Sell"
                sl = price + cfg.SL_ATR_MULT * atr
                tp = price - cfg.TP_ATR_MULT * atr

            entry_price = price
            qty = round(qty_new, 6)
            entry_bar = i
            in_trade = True

    return result


def run_backtest(
    symbols: list[str] | None = None,
    limit: int = 1000,
) -> dict[str, BacktestResult]:
    """
    Run backtest for all (or specified) symbols.

    Args:
        symbols: list of symbols, defaults to cfg.SYMBOLS
        limit:   number of bars to fetch per symbol

    Returns:
        dict {symbol: BacktestResult}
    """
    if symbols is None:
        symbols = cfg.SYMBOLS

    logger.info("Loading models...")
    model_mgr = MultiModelManager()

    results: dict[str, BacktestResult] = {}

    for symbol in symbols:
        logger.info("Backtesting %s...", symbol)
        try:
            df = fetch_klines(symbol=symbol, interval=cfg.INTERVAL, limit=limit)
            if df is None or len(df) < cfg.LOOKBACK + 50:
                logger.warning("%s: insufficient data", symbol)
                continue
            res = _run_symbol(symbol, df, model_mgr)
            results[symbol] = res
            logger.info(
                "%s: trades=%d win=%.0f%% PnL=%.2f$ DD=%.1f%% Sharpe=%.2f PF=%.2f",
                symbol, res.n_trades, res.win_rate * 100,
                res.total_pnl, res.max_drawdown * 100,
                res.sharpe, res.profit_factor,
            )
        except Exception as exc:
            logger.error("%s: backtest error: %s", symbol, exc)

    return results


def print_summary(results: dict[str, BacktestResult]):
    """Print a summary table of all backtest results."""
    print("\n" + "=" * 75)
    print(f"  {'Symbol':<14} {'Trades':>6} {'Win%':>6} {'PnL$':>8} {'DD%':>7} {'Sharpe':>7} {'PF':>6}")$':>8} {'DD%':>7} {'Sharpe':>7} {'PF':>6}")$':>8} {'DD%':>7} {'Sharpe':>7} {'PF':>6}")
    print("=" * 75)

    total_pnl = 0.0
    total_trades = 0
    all_wins = 0
    
    for sym, r in sorted(results.items(), key=lambda x: -x[1].total_pnl):
        total_pnl += r.total_pnl
        total_trades += r.n_trades
        all_wins += sum(1 for t in r.trades if t.pnl > 0)
        print(
            f"  {sym:<14} {r.n_trades:>6} {r.win_rate*100:>5.0f}%"
            f" {r.total_pnl:>8.2f} {r.max_drawdown*100:>6.1f}%"
            f" {r.sharpe:>7.2f} {r.profit_factor:>6.2f}"
        )

    print("=" * 75)
    overall_wr = (all_wins / total_trades * 100) if total_trades > 0 else 0
    print(f"  {'TOTAL':<14} {total_trades:>6} {overall_wr:>5.0f}% {total_pnl:>8.2f}$")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    syms = sys.argv[1:] if len(sys.argv) > 1 else None
    res = run_backtest(symbols=syms, limit=1000)
    print_summary(res)
