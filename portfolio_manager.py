"""
=============================================================
portfolio_manager.py — Portfolio risk management for multi-pair trading
=============================================================
Manages:
- Correlation matrix between pairs
- Total portfolio exposure
- Per-pair exposure limits
- Position approval based on correlation
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

import numpy as np
import pandas as pd

import config as cfg

logger = logging.getLogger(__name__)


class PortfolioRiskManager:
    """
    Manages portfolio-level risk across multiple trading pairs.
    
    Key features:
    - Tracks correlation matrix between pairs
    - Enforces total exposure limits
    - Prevents over-concentration in correlated assets
    - Rate-limits position opening
    """
    
    def __init__(self, equity: float = 1000.0):
        self.equity = equity
        self.price_histories: Dict[str, pd.Series] = {}
        self.last_position_time: float = 0.0
        self.correlation_matrix: Optional[pd.DataFrame] = None
        
    def update_price_history(self, symbol: str, price: float, timestamp: pd.Timestamp):
        """Update rolling price history for correlation calculation."""
        if symbol not in self.price_histories:
            self.price_histories[symbol] = pd.Series(dtype=float)
        
        # Keep only last CORRELATION_WINDOW bars
        new_data = pd.Series([price], index=[timestamp])
        if len(self.price_histories[symbol]) == 0:
            self.price_histories[symbol] = new_data
        else:
            self.price_histories[symbol] = pd.concat([
                self.price_histories[symbol],
                new_data
            ]).iloc[-cfg.CORRELATION_WINDOW:]
    
    def calculate_correlation_matrix(self) -> pd.DataFrame:
        """
        Calculate correlation matrix from price returns.
        Returns empty DataFrame if insufficient data.
        """
        if len(self.price_histories) < 2:
            return pd.DataFrame()
        
        # Build DataFrame of prices
        price_df = pd.DataFrame(self.price_histories)
        
        # Need at least 30 bars for meaningful correlation
        if len(price_df) < 30:
            return pd.DataFrame()
        
        # Calculate returns
        returns = price_df.pct_change().dropna()
        
        if len(returns) < 20:
            return pd.DataFrame()
        
        # Correlation matrix
        self.correlation_matrix = returns.corr()
        return self.correlation_matrix
    
    def get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Get correlation between two symbols. Returns 0 if not available."""
        if self.correlation_matrix is None or self.correlation_matrix.empty:
            return 0.0
        
        if symbol1 not in self.correlation_matrix.columns or symbol2 not in self.correlation_matrix.columns:
            return 0.0
        
        return float(self.correlation_matrix.loc[symbol1, symbol2])
    
    def get_total_exposure(self, positions: Dict[str, any]) -> float:
        """
        Calculate total portfolio exposure as fraction of equity.
        
        Args:
            positions: Dict[symbol -> PaperPosition]
        
        Returns:
            Total exposure as fraction (0.0 to 1.0+)
        """
        total_notional = 0.0
        for symbol, pos in positions.items():
            if pos.is_open:
                notional = pos.qty * pos.entry_price
                total_notional += notional
        
        return total_notional / self.equity if self.equity > 0 else 0.0
    
    def get_pair_exposure(self, symbol: str, positions: Dict[str, any]) -> float:
        """Get exposure for a specific pair as fraction of equity."""
        pos = positions.get(symbol)
        if pos is None or not pos.is_open:
            return 0.0
        
        notional = pos.qty * pos.entry_price
        return notional / self.equity if self.equity > 0 else 0.0
    
    def count_correlated_positions(self, symbol: str, positions: Dict[str, any]) -> int:
        """
        Count how many open positions are highly correlated with this symbol.
        
        Args:
            symbol: Symbol to check
            positions: Dict of all positions
        
        Returns:
            Number of correlated open positions
        """
        if self.correlation_matrix is None or self.correlation_matrix.empty:
            return 0
        
        count = 0
        for other_symbol, pos in positions.items():
            if other_symbol == symbol or not pos.is_open:
                continue
            
            corr = abs(self.get_correlation(symbol, other_symbol))
            if corr >= cfg.CORRELATION_THRESHOLD:
                count += 1
        
        return count
    
    def can_open_position(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        positions: Dict[str, any]
    ) -> tuple[bool, str]:
        """
        Check if a new position can be opened based on portfolio constraints.
        
        Returns:
            (can_open: bool, reason: str)
        """
        # 1. Check rate limiting
        now = time.time()
        if now - self.last_position_time < cfg.MIN_PAIR_SPACING_SEC:
            return False, "rate_limit"
        
        # 2. Check total exposure
        current_exposure = self.get_total_exposure(positions)
        new_notional = qty * price
        new_exposure = current_exposure + (new_notional / self.equity)
        
        if new_exposure > cfg.MAX_TOTAL_EXPOSURE:
            return False, f"total_exposure ({new_exposure:.2%} > {cfg.MAX_TOTAL_EXPOSURE:.2%})"
        
        # 3. Check per-pair exposure
        pair_exposure = self.get_pair_exposure(symbol, positions)
        new_pair_exposure = pair_exposure + (new_notional / self.equity)
        
        if new_pair_exposure > cfg.MAX_EXPOSURE_PER_PAIR:
            return False, f"pair_exposure ({new_pair_exposure:.2%} > {cfg.MAX_EXPOSURE_PER_PAIR:.2%})"
        
        # 4. Check correlation limits
        correlated_count = self.count_correlated_positions(symbol, positions)
        if correlated_count >= cfg.MAX_CORRELATED_PAIRS:
            return False, f"too_many_correlated ({correlated_count} >= {cfg.MAX_CORRELATED_PAIRS})"
        
        return True, "approved"
    
    def approve_and_record(self, symbol: str) -> None:
        """Record that a position was opened (for rate limiting)."""
        self.last_position_time = time.time()
        logger.debug("Position opened for %s, rate limit timer reset", symbol)
    
    def calculate_adjusted_qty(
        self,
        base_qty: float,
        price: float,
        positions: Dict[str, any]
    ) -> float:
        """
        Adjust position size based on current portfolio exposure.
        Reduces size if approaching limits.
        
        Args:
            base_qty: Initial calculated quantity
            price: Entry price
            positions: Current positions
        
        Returns:
            Adjusted quantity
        """
        current_exposure = self.get_total_exposure(positions)
        
        # If we're above 60% exposure, start scaling down new positions
        if current_exposure > 0.60:
            scale_factor = max(0.3, 1.0 - (current_exposure - 0.60) / 0.20)
            adjusted_qty = base_qty * scale_factor
            logger.info(
                "Scaling down position size: exposure=%.2f%%, scale=%.2f, qty: %.4f -> %.4f",
                current_exposure * 100, scale_factor, base_qty, adjusted_qty
            )
            return adjusted_qty
        
        return base_qty
    
    def get_portfolio_stats(self, positions: Dict[str, any]) -> dict:
        """Get current portfolio statistics for monitoring."""
        total_exposure = self.get_total_exposure(positions)
        open_positions = sum(1 for p in positions.values() if p.is_open)
        
        # Get list of open symbols
        open_symbols = [sym for sym, p in positions.items() if p.is_open]
        
        return {
            "total_exposure": total_exposure,
            "open_positions": open_positions,
            "equity": self.equity,
            "correlation_pairs": len(self.price_histories),
            "open_symbols": open_symbols,
        }
