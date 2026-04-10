"""
=============================================================
paper_position.py — Shared paper-trading position class
=============================================================
"""
from __future__ import annotations

import logging
import time

import config as cfg
from signal_logger import log_trade

logger = logging.getLogger(__name__)


class PaperPosition:
    """Paper trading position (shared between async and single-pair bots)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.side: str | None = None
        self.qty: float = 0.0
        self.entry_price: float = 0.0
        self.sl: float = 0.0
        self.tp: float = 0.0
        self.trail_sl: float | None = None
        self.entry_ts: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.side is not None

    def open(self, side: str, price: float, qty: float, sl: float, tp: float):
        self.side = side
        self.qty = qty
        self.entry_price = price
        self.sl = sl
        self.tp = tp
        self.trail_sl = None
        self.entry_ts = time.time()

    def close(self, symbol: str, exit_price: float, reason: str):
        if self.side is None:
            return

        duration = time.time() - self.entry_ts
        # Комиссия: taker fee на вход + taker fee на выход
        fee = (self.entry_price + exit_price) * self.qty * cfg.TAKER_FEE
        raw_pnl = (exit_price - self.entry_price) * self.qty * (1 if self.side == "Buy" else -1)
        pnl = raw_pnl - fee
        log_trade(
            symbol=symbol,
            side=self.side,
            qty=self.qty,
            entry_price=self.entry_price,
            exit_price=exit_price,
            sl=self.sl,
            tp=self.tp,
            exit_reason=reason,
            duration_sec=duration,
        )
        logger.info(
            "%s: Close %s @ %.2f | %s | raw_PnL=%.2f fee=%.2f net_PnL=%.2f",
            symbol, self.side, exit_price, reason, raw_pnl, fee, pnl,
        )
        self.reset()

    def unrealized_pnl(self, current_price: float) -> float:
        if not self.is_open:
            return 0.0
        raw = (current_price - self.entry_price) * self.qty * (1 if self.side == "Buy" else -1)
        # Вычитаем уже уплаченную комиссию входа + ожидаемую комиссию выхода
        fee = (self.entry_price + current_price) * self.qty * cfg.TAKER_FEE
        return raw - fee
