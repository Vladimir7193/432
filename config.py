"""
=============================================================
config.py — Centralized configuration for CatBoost HFT Bot
=============================================================
"""
import os

# ── Exchange ───────────────────────────────────────────────
# ВАЖНО: Замени на свои MAINNET ключи с правами Contract Trade (Read + Write)
API_KEY    = os.getenv("BYBIT_API_KEY",    "3zvAfoVSsOeF4dajgU")
API_SECRET = os.getenv("BYBIT_API_SECRET", "UC2xpqvh6Th7LQYRMuAgsmkoiXrTx8DcXPW5")
TESTNET    = False          # False → mainnet (РЕАЛЬНАЯ ТОРГОВЛЯ)

# ── Market ────────────────────────────────────────────────
SYMBOL     = "BTCUSDT"    # основная пара (используется в edge_tester и whale_logger)
CATEGORY   = "linear"     # linear = USDT-margined perpetual
INTERVAL   = "1"          # 1-minute bars (Bybit API value)
LEVERAGE   = 5

# ── 30 ликвидных пар Bybit для мультисканера ──────────────
SYMBOLS = [
    "BTCUSDT",  "ETHUSDT",  "SOLUSDT",  "BNBUSDT",  "XRPUSDT",
    "DOGEUSDT", "ADAUSDT",  "AVAXUSDT", "LINKUSDT",  "DOTUSDT",
    "MATICUSDT","LTCUSDT",  "UNIUSDT",  "ATOMUSDT",  "NEARUSDT",
    "OPUSDT",   "ARBUSDT",  "APTUSDT",  "SUIUSDT",   "SEIUSDT",
    "TIAUSDT",  "INJUSDT",  "WLDUSDT",  "FETUSDT",   "RENDERUSDT",
    "JUPUSDT",  "PYTHUSDT", "STRKUSDT", "ONDOUSDT",  "ENAUSDT",
]

# ── Risk management ───────────────────────────────────────
RISK_PCT            = 0.01   # 1 % of equity per trade
MAX_POSITIONS       = 1
SL_ATR_MULT         = 1.5
TP_ATR_MULT         = 2.5
TRAIL_ACTIVATE_ATR  = 1.0    # trailing SL activates after 1×ATR in profit
MAX_DAILY_LOSS_PCT  = 0.03   # halt trading if daily loss > 3%

# ── Portfolio Management (Multi-pair) ─────────────────────
MAX_TOTAL_EXPOSURE      = 0.80   # max 80% of capital in positions
MAX_EXPOSURE_PER_PAIR   = 0.25   # max 25% per single pair
MAX_CORRELATED_PAIRS    = 3      # max pairs with correlation > threshold
CORRELATION_THRESHOLD   = 0.70   # pairs with corr > 0.7 considered correlated
CORRELATION_WINDOW      = 100    # bars for correlation calculation
MIN_PAIR_SPACING_SEC    = 5      # min seconds between opening positions
ENABLE_MULTI_MODEL      = True   # separate model per pair (vs single shared model)

# ── Feature engineering ───────────────────────────────────
LOOKBACK        = 200        # candles needed to compute all features
ATR_PERIOD      = 14
EMA_FAST        = 9
EMA_SLOW        = 21
EMA_TREND       = 50
RSI_PERIOD      = 14
BB_PERIOD       = 20
BB_STD          = 2.0
MACD_FAST       = 12
MACD_SLOW       = 26
MACD_SIGNAL     = 9
OBV_EMA_PERIOD  = 20
CMF_PERIOD      = 20
STOCH_K         = 14
STOCH_D         = 3
ADX_PERIOD      = 14
VWAP_PERIOD     = 20        # rolling VWAP window (bars)

# ── Signal thresholds ────────────────────────────────────
LONG_PROB_THRESH  = 0.62    # CatBoost probability → open long
SHORT_PROB_THRESH = 0.62    # CatBoost probability → open short

# ── Exchange fees ────────────────────────────────────────
TAKER_FEE = 0.00055   # Bybit taker fee 0.055% (linear perpetuals)
MAKER_FEE = 0.00020   # Bybit maker fee 0.020%
# Суммарная комиссия за сделку (вход + выход, оба taker):
ROUND_TRIP_FEE = TAKER_FEE * 2  # 0.11%

# ── CatBoost model ───────────────────────────────────────
MODEL_PATH          = "models/catboost_model.cbm"  # legacy single model
MODEL_META_PATH     = "models/model_meta.json"
MODELS_DIR          = "models/pairs/"  # directory for per-pair models
RETRAIN_EVERY_N     = 500        # bars between retrain attempts
RETRAIN_MIN_SAMPLES = 2000       # minimum rows for training
TRAIN_WINDOW_BARS   = 10_000     # rolling training window
VALIDATION_SPLIT    = 0.15
MIN_IMPROVEMENT_PCT = 0.002      # only save model if F1 improves ≥ 0.2 %
CATBOOST_PARAMS = {
    "iterations":        800,
    "learning_rate":     0.05,
    "depth":             6,
    "l2_leaf_reg":       3,
    "loss_function":     "MultiClass",   # 0=hold 1=long 2=short
    "eval_metric":       "TotalF1",
    "random_seed":       42,
    "thread_count":      -1,
    "verbose":           False,
    "early_stopping_rounds": 50,
    "task_type":         "CPU",
}
LABEL_FUTURE_BARS   = 3          # look-ahead bars to create label
LABEL_THRESH_ATR    = 0.5        # movement > 0.5×ATR → directional label

# ── Whale / smart-money detection ───────────────────────
WHALE_VOL_MULT      = 3.0        # volume > 3× rolling mean → whale bar
WHALE_LOOKBACK      = 50
ABSORPTION_THRESH   = 0.30       # body/range < 30 % → potential absorption

# ── Logging ──────────────────────────────────────────────
LOG_PATH            = "logs/"
SIGNAL_LOG_CSV      = "logs/signals.csv"
TRADE_LOG_CSV       = "logs/trades.csv"
WHALE_LOG_CSV       = "logs/whale_events.csv"

# ── API Rate Limiting ────────────────────────────────────
API_RATE_LIMIT      = 100    # max requests per minute
API_BURST_LIMIT     = 10     # max burst requests

# ── Streamlit dashboard ──────────────────────────────────
DASHBOARD_REFRESH_SEC = 5

# ── Multi-timeframe analysis ─────────────────────────────
MTF_INTERVALS = ["5", "15", "60", "240"]   # 5m, 15m, 1h, 4h
MTF_INTERVAL_LABELS = {"5": "5m", "15": "15m", "60": "1h", "240": "4h"}
MTF_LOOKBACK  = 250   # bars to fetch per TF

# ── Dynamic position sizing ──────────────────────────────
DYNAMIC_SIZING         = True    # scale size by volatility
VOL_SCALE_WINDOW       = 20      # bars for volatility normalisation
VOL_SCALE_TARGET       = 0.01    # target daily vol (1 %)

# ── Drawdown protection ──────────────────────────────────
MAX_DRAWDOWN_PCT       = 0.08    # halt if equity drops 8 % from peak
DRAWDOWN_COOLDOWN_SEC  = 3600    # resume after 1 h

# ── Backtest ─────────────────────────────────────────────
BACKTEST_INITIAL_EQUITY = 1000.0
BACKTEST_COMMISSION     = ROUND_TRIP_FEE   # reuse fee constant
BACKTEST_DEFAULT_BARS   = 1000             # default bars to fetch for backtest
