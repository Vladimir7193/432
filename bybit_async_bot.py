"""
=============================================================
bybit_async_bot.py — Async multi-pair trading bot
=============================================================
Features:
- WebSocket real-time data (no REST polling)
- Separate model per pair
- Portfolio risk management with correlation control
- Async architecture for parallel processing
- Rate limiting

Запуск: py -3.12 bybit_async_bot.py
"""
from __future__ import annotations

import asyncio
import io
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config as cfg
from market_data import fetch_klines, get_orderbook_imbalance
from multi_model_manager import MultiModelManager
from paper_position import PaperPosition
from portfolio_manager import PortfolioRiskManager
from signal_logger import log_signal, log_trade, log_whale_event
from smart_money import detect_whale_bars, get_bias_from_smart_money
from ws_client import BybitWSClient, rate_limiter

# FIX: Force UTF-8 for Windows console
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Logging setup
Path(cfg.LOG_PATH).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(cfg.LOG_PATH + "async_bot.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("bybit_async_bot")


# -----------------------------------------------------------------------------
# POSITION MANAGEMENT
# -----------------------------------------------------------------------------

def manage_position(symbol: str, pos: PaperPosition, current_price: float, atr: float) -> bool:
    """Manage open position (SL/TP/trailing). Returns True if closed."""
    if not pos.is_open:
        return False
    
    profit = pos.unrealized_pnl(current_price)
    trail_trigger = cfg.TRAIL_ACTIVATE_ATR * atr * pos.qty
    
    # Activate trailing SL
    if profit >= trail_trigger and pos.trail_sl is None:
        if pos.side == "Buy":
            pos.trail_sl = current_price - atr * cfg.SL_ATR_MULT
        else:
            pos.trail_sl = current_price + atr * cfg.SL_ATR_MULT
        logger.info("%s: Trailing SL activated: %.2f", symbol, pos.trail_sl)
    
    # Update trailing SL
    if pos.trail_sl is not None:
        if pos.side == "Buy":
            new_trail = current_price - atr * cfg.SL_ATR_MULT
            pos.trail_sl = max(pos.trail_sl, new_trail)
        else:
            new_trail = current_price + atr * cfg.SL_ATR_MULT
            pos.trail_sl = min(pos.trail_sl, new_trail)
    
    effective_sl = pos.trail_sl if pos.trail_sl is not None else pos.sl
    
    # Check exit conditions
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


def calc_qty(price: float, atr: float, equity: float, portfolio_mgr: PortfolioRiskManager, positions: dict) -> float:
    """Calculate position size with portfolio adjustment and dynamic volatility scaling."""
    risk_usdt = equity * cfg.RISK_PCT

    # Dynamic sizing: scale by volatility if enabled
    if cfg.DYNAMIC_SIZING and atr > 0 and price > 0:
        daily_vol = atr / price  # ATR as fraction of price ≈ daily vol proxy
        if daily_vol > 0:
            scale = cfg.VOL_SCALE_TARGET / (daily_vol + 1e-9)
            scale = min(max(scale, 0.3), 2.0)   # clamp to [0.3, 2.0]
            risk_usdt *= scale

    sl_dist = cfg.SL_ATR_MULT * atr
    qty = risk_usdt / (sl_dist + 1e-9)
    max_qty = equity * cfg.LEVERAGE / price
    base_qty = min(qty, max_qty)

    # Adjust based on portfolio exposure
    adjusted_qty = portfolio_mgr.calculate_adjusted_qty(base_qty, price, positions)

    return round(adjusted_qty, 4)


# -----------------------------------------------------------------------------
# ASYNC MAIN BOT
# -----------------------------------------------------------------------------

class AsyncTradingBot:
    """Main async trading bot."""
    
    def __init__(self):
        self.equity = 1000.0
        self.equity_peak = 1000.0          # track peak for drawdown protection
        self.drawdown_halt_until: float = 0.0  # timestamp when halt expires
        self.model_mgr = MultiModelManager()
        self.portfolio_mgr = PortfolioRiskManager(equity=self.equity)
        self.positions: dict[str, PaperPosition] = {s: PaperPosition() for s in cfg.SYMBOLS}
        self.histories: dict[str, pd.DataFrame | None] = {s: None for s in cfg.SYMBOLS}
        self.bar_count = 0
        self.running = False
    
    async def initialize_histories(self):
        """Fetch initial historical data for all pairs."""
        logger.info("Fetching initial history for %d pairs...", len(cfg.SYMBOLS))
        
        tasks = []
        for symbol in cfg.SYMBOLS:
            tasks.append(self._fetch_history(symbol))
        
        await asyncio.gather(*tasks)
        logger.info("Initial history loaded")
    
    async def _fetch_history(self, symbol: str):
        """Fetch history for one symbol with rate limiting."""
        try:
            await rate_limiter.acquire()
            df = await asyncio.to_thread(fetch_klines, symbol, cfg.INTERVAL, cfg.LOOKBACK + 50)
            if df is not None and len(df) >= cfg.LOOKBACK:
                self.histories[symbol] = df
                logger.info("%s: Loaded %d bars", symbol, len(df))
        except Exception as exc:
            logger.error("%s: History fetch error: %s", symbol, exc)
    
    async def process_bar(self, symbol: str):
        """Process one bar for a symbol."""
        try:
            df = self.histories.get(symbol)
            if df is None or len(df) < cfg.LOOKBACK:
                return
            
            # Get latest data
            from signal_engine import compute_features
            feat_df = compute_features(df)
            last = feat_df.iloc[-1]
            price = float(last["close"])
            atr = float(last.get("atr", price * 0.001))
            timestamp = df.index[-1]
            
            # Update portfolio price history
            self.portfolio_mgr.update_price_history(symbol, price, timestamp)
            
            # Whale detection
            sm_bias = get_bias_from_smart_money(df)
            whale_ser = detect_whale_bars(df)
            if whale_ser.iloc[-1]:
                vol_mult = df["volume"].iloc[-1] / (df["volume"].iloc[-cfg.WHALE_LOOKBACK:].mean() + 1e-9)
                log_whale_event(symbol, "whale_bar", price, float(df["volume"].iloc[-1]), vol_mult)
            
            # Model signal
            signal, p_hold, p_long, p_short = self.model_mgr.predict_signal(symbol, df)
            
            # Orderbook imbalance (with rate limiting)
            await rate_limiter.acquire()
            ob_imb = await asyncio.to_thread(get_orderbook_imbalance, symbol)
            
            # Log signal
            log_signal(symbol, signal, p_hold, p_long, p_short, price, atr, sm_bias, ob_imb)
            
            signal_label = {0: "HOLD", 1: "LONG", 2: "SHORT"}.get(signal, "?")
            logger.info(
                "%s | %.2f | %s | L=%.2f S=%.2f | sm=%+d",
                symbol, price, signal_label, p_long, p_short, sm_bias
            )
            
            # Manage existing position
            pos = self.positions[symbol]
            if pos.is_open:
                manage_position(symbol, pos, price, atr)
            
            # Open new position
            if not pos.is_open and self.model_mgr.is_trained(symbol):
                # Drawdown protection check
                if time.time() < self.drawdown_halt_until:
                    logger.debug("%s: trading halted (drawdown protection)", symbol)
                    return

                qty = calc_qty(price, atr, self.equity, self.portfolio_mgr, self.positions)
                
                # Check portfolio constraints
                can_open, reason = self.portfolio_mgr.can_open_position(
                    symbol, "Buy" if signal == 1 else "Sell", qty, price, self.positions
                )
                
                if can_open:
                    if signal == 1 and sm_bias >= 0:
                        pos.open("Buy", price, qty,
                                sl=price - cfg.SL_ATR_MULT * atr,
                                tp=price + cfg.TP_ATR_MULT * atr)
                        self.portfolio_mgr.approve_and_record(symbol)
                        logger.info("%s: Opened LONG @ %.2f | qty=%.4f", symbol, price, qty)
                    
                    elif signal == 2 and sm_bias <= 0:
                        pos.open("Sell", price, qty,
                                sl=price + cfg.SL_ATR_MULT * atr,
                                tp=price - cfg.TP_ATR_MULT * atr)
                        self.portfolio_mgr.approve_and_record(symbol)
                        logger.info("%s: Opened SHORT @ %.2f | qty=%.4f", symbol, price, qty)
                else:
                    logger.debug("%s: Position blocked: %s", symbol, reason)
            
            # Increment bar counter for retraining
            self.model_mgr.increment_bar_count(symbol)
        
        except Exception as exc:
            logger.error("%s: Processing error: %s", symbol, exc)
    
    async def retrain_models(self):
        """Check and retrain models that are due."""
        tasks = []
        for symbol in cfg.SYMBOLS:
            if self.model_mgr.should_retrain(symbol):
                hist = self.histories.get(symbol)
                if hist is not None and len(hist) >= cfg.RETRAIN_MIN_SAMPLES:
                    logger.info("%s: Retraining...", symbol)
                    tasks.append(self._retrain_symbol(symbol, hist))
        
        if tasks:
            results = await asyncio.gather(*tasks)
            accepted = sum(results)
            logger.info("Retrain cycle: %d/%d models accepted", accepted, len(tasks))
    
    async def _retrain_symbol(self, symbol: str, hist: pd.DataFrame) -> bool:
        """Retrain model for one symbol."""
        try:
            return await asyncio.to_thread(self.model_mgr.try_retrain, symbol, hist)
        except Exception as exc:
            logger.error("%s: Retrain error: %s", symbol, exc)
            return False
    
    async def update_correlation_matrix(self):
        """Update correlation matrix periodically."""
        try:
            corr_matrix = self.portfolio_mgr.calculate_correlation_matrix()
            if not corr_matrix.empty:
                logger.debug("Correlation matrix updated: %d pairs", len(corr_matrix))
        except Exception as exc:
            logger.error("Correlation update error: %s", exc)
    
    async def main_loop(self):
        """Main trading loop - runs every minute."""
        self.running = True
        
        while self.running:
            try:
                loop_start = time.time()
                self.bar_count += 1
                
                logger.info("=" * 60)
                logger.info("Bar #%d | %s", self.bar_count, datetime.now(tz=timezone.utc).strftime("%H:%M:%S"))
                
                # Fetch latest bars for all pairs
                await self.initialize_histories()
                
                # Process all pairs in parallel
                tasks = [self.process_bar(symbol) for symbol in cfg.SYMBOLS]
                await asyncio.gather(*tasks)
                
                # Update correlation matrix
                await self.update_correlation_matrix()
                
                # Retrain models if needed
                await self.retrain_models()
                
                # Portfolio stats
                stats = self.portfolio_mgr.get_portfolio_stats(self.positions)
                logger.info(
                    "Portfolio: exposure=%.1f%% | positions=%d/%d",
                    stats["total_exposure"] * 100,
                    stats["open_positions"],
                    len(cfg.SYMBOLS)
                )
                
                # Model stats
                model_stats = self.model_mgr.get_stats()
                logger.info(
                    "Models: trained=%d/%d | avg_F1=%.3f | best=%s",
                    model_stats["trained_models"],
                    model_stats["total_models"],
                    model_stats["avg_f1"],
                    model_stats.get("best_pair", "N/A")
                )

                # ── Drawdown protection ──────────────────────────────────────
                open_pnl = sum(
                    p.unrealized_pnl(0) for p in self.positions.values() if p.is_open
                )
                current_equity = self.equity + open_pnl
                self.equity_peak = max(self.equity_peak, current_equity)
                dd = (current_equity - self.equity_peak) / (self.equity_peak + 1e-9)
                if dd < -cfg.MAX_DRAWDOWN_PCT and time.time() >= self.drawdown_halt_until:
                    self.drawdown_halt_until = time.time() + cfg.DRAWDOWN_COOLDOWN_SEC
                    logger.warning(
                        "⚠ Drawdown %.1f%% exceeded limit %.1f%% — halting new trades for %ds",
                        dd * 100, cfg.MAX_DRAWDOWN_PCT * 100, cfg.DRAWDOWN_COOLDOWN_SEC,
                    )
                elif time.time() >= self.drawdown_halt_until and self.drawdown_halt_until > 0:
                    logger.info("✅ Drawdown cooldown expired — resuming trading")
                
                # Sleep until next bar
                elapsed = time.time() - loop_start
                sleep_for = max(5, 60 - elapsed)
                logger.info("Cycle: %.1fs | sleeping %.0fs", elapsed, sleep_for)
                await asyncio.sleep(sleep_for)
            
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                self.running = False
                break
            except Exception as exc:
                logger.error("Main loop error: %s", exc)
                await asyncio.sleep(5)
    
    async def run(self):
        """Start the bot."""
        logger.info("=" * 60)
        logger.info("Bybit Async Multi-Pair Bot")
        logger.info("Pairs: %d | Models: separate | Portfolio: managed", len(cfg.SYMBOLS))
        logger.info("=" * 60)
        
        # Initialize
        await self.initialize_histories()
        
        # Start main loop
        await self.main_loop()


# -----------------------------------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------------------------------

async def main():
    if not cfg.API_KEY or not cfg.API_SECRET:
        logging.getLogger(__name__).error(
            "API keys not configured! Set BYBIT_API_KEY and BYBIT_API_SECRET "
            "env vars or edit config.py"
        )
        sys.exit(1)
    bot = AsyncTradingBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
