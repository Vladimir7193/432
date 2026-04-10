"""
=============================================================
ws_client.py — WebSocket client for real-time Bybit data
=============================================================
Subscribes to kline updates for multiple pairs simultaneously.
Much more efficient than REST polling.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

import pandas as pd
from pybit.unified_trading import WebSocket

import config as cfg

logger = logging.getLogger(__name__)


class BybitWSClient:
    """
    Async WebSocket client for Bybit real-time kline data.
    
    Features:
    - Subscribe to multiple pairs simultaneously
    - Automatic reconnection
    - Callback-based architecture
    - Thread-safe data storage
    """
    
    def __init__(self, symbols: list[str], interval: str = "1"):
        self.symbols = symbols
        self.interval = interval
        self.ws: Optional[WebSocket] = None
        self.callbacks: Dict[str, list[Callable]] = defaultdict(list)
        self.latest_bars: Dict[str, dict] = {}
        self.running = False
        
    def on_kline(self, symbol: str, callback: Callable):
        """
        Register callback for kline updates.
        
        Args:
            symbol: Trading pair
            callback: Function(symbol, kline_dict) to call on update
        """
        self.callbacks[symbol].append(callback)
    
    def _handle_message(self, message):
        """Handle incoming WebSocket message."""
        try:
            if not isinstance(message, dict):
                return
            
            topic = message.get("topic", "")
            if not topic.startswith("kline."):
                return
            
            data = message.get("data", [])
            if not data:
                return
            
            # Extract symbol from topic: "kline.1.BTCUSDT"
            parts = topic.split(".")
            if len(parts) < 3:
                return
            
            symbol = parts[2]
            kline = data[0] if isinstance(data, list) else data
            
            # Store latest bar
            self.latest_bars[symbol] = {
                "timestamp": int(kline.get("start", 0)),
                "open": float(kline.get("open", 0)),
                "high": float(kline.get("high", 0)),
                "low": float(kline.get("low", 0)),
                "close": float(kline.get("close", 0)),
                "volume": float(kline.get("volume", 0)),
                "confirm": kline.get("confirm", False),
            }
            
            # Call registered callbacks
            for callback in self.callbacks.get(symbol, []):
                try:
                    callback(symbol, self.latest_bars[symbol])
                except Exception as exc:
                    logger.error("Callback error for %s: %s", symbol, exc)
        
        except Exception as exc:
            logger.error("Error handling WS message: %s", exc)
    
    async def connect(self):
        """Connect to Bybit WebSocket and subscribe to klines."""
        try:
            self.ws = WebSocket(
                testnet=cfg.TESTNET,
                channel_type="linear",
            )
            
            # Subscribe to klines for all symbols
            for symbol in self.symbols:
                topic = f"kline.{self.interval}.{symbol}"
                self.ws.kline_stream(
                    interval=self.interval,
                    symbol=symbol,
                    callback=self._handle_message
                )
                logger.info("Subscribed to %s", topic)
            
            self.running = True
            logger.info("WebSocket connected for %d pairs", len(self.symbols))
            
        except Exception as exc:
            logger.error("WebSocket connection error: %s", exc)
            self.running = False
    
    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False
        if self.ws:
            try:
                # pybit doesn't have explicit close, just stop using it
                self.ws = None
                logger.info("WebSocket disconnected")
            except Exception as exc:
                logger.error("Error disconnecting: %s", exc)
    
    def get_latest_bar(self, symbol: str) -> Optional[dict]:
        """Get latest kline bar for a symbol."""
        return self.latest_bars.get(symbol)
    
    async def run_forever(self):
        """Keep connection alive and handle reconnections."""
        while self.running:
            try:
                if not self.ws:
                    await self.connect()
                
                # Keep alive
                await asyncio.sleep(1)
                
            except Exception as exc:
                logger.error("WebSocket error: %s", exc)
                await asyncio.sleep(5)
                # Reconnect
                await self.connect()


class RateLimiter:
    """
    Token bucket rate limiter for API requests.
    """
    
    def __init__(self, rate: int = 100, burst: int = 10):
        """
        Args:
            rate: Max requests per minute
            burst: Max burst requests
        """
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last_update = 0.0
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        """Wait until a token is available."""
        async with self.lock:
            now = asyncio.get_event_loop().time()
            if self.last_update == 0.0:
                self.last_update = now
            elapsed = now - self.last_update
            
            # Refill tokens based on elapsed time
            self.tokens = min(
                self.burst,
                self.tokens + elapsed * (self.rate / 60.0)
            )
            self.last_update = now
            
            # Wait if no tokens available
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / (self.rate / 60.0)
                await asyncio.sleep(wait_time)
                self.tokens = 1
            
            self.tokens -= 1


# Global rate limiter instance - created lazily inside async context
_rate_limiter: "RateLimiter | None" = None

def get_rate_limiter() -> "RateLimiter":
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(rate=cfg.API_RATE_LIMIT, burst=cfg.API_BURST_LIMIT)
    return _rate_limiter

# Keep backward-compatible name
rate_limiter = RateLimiter(rate=cfg.API_RATE_LIMIT, burst=cfg.API_BURST_LIMIT)
