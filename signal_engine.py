"""
=============================================================
signal_engine.py — Feature engineering + CatBoost model
                   with "only retrain if improving" logic
=============================================================
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split

import config as cfg

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  FEATURE ENGINEERING
# ─────────────────────────────────────────────────────────────────────────────

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all technical features on an OHLCV DataFrame.
    Returns a copy with feature columns appended.
    NaN rows from indicators are kept; caller decides whether to drop them.
    """
    d = df.copy()
    c, h, l, v, o = d["close"], d["high"], d["low"], d["volume"], d["open"]

    # ── Trend ────────────────────────────────────────────────────────────────
    d["ema_fast"]  = c.ewm(span=cfg.EMA_FAST,  adjust=False).mean()
    d["ema_slow"]  = c.ewm(span=cfg.EMA_SLOW,  adjust=False).mean()
    d["ema_trend"] = c.ewm(span=cfg.EMA_TREND, adjust=False).mean()
    d["ema_cross"] = d["ema_fast"] - d["ema_slow"]
    d["price_vs_trend"] = (c - d["ema_trend"]) / d["ema_trend"]

    # ── Volatility ───────────────────────────────────────────────────────────
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    d["atr"] = tr.ewm(span=cfg.ATR_PERIOD, adjust=False).mean()
    d["atr_pct"] = d["atr"] / c

    # Bollinger Bands
    rolling_c  = c.rolling(cfg.BB_PERIOD)
    d["bb_mid"]   = rolling_c.mean()
    d["bb_std"]   = rolling_c.std()
    d["bb_upper"] = d["bb_mid"] + cfg.BB_STD * d["bb_std"]
    d["bb_lower"] = d["bb_mid"] - cfg.BB_STD * d["bb_std"]
    d["bb_width"] = (d["bb_upper"] - d["bb_lower"]) / d["bb_mid"]
    d["bb_pos"]   = (c - d["bb_lower"]) / (d["bb_upper"] - d["bb_lower"] + 1e-9)

    # ── Momentum ─────────────────────────────────────────────────────────────
    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=cfg.RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(span=cfg.RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    d["rsi"] = 100 - 100 / (1 + rs)

    # MACD
    ema12 = c.ewm(span=cfg.MACD_FAST,   adjust=False).mean()
    ema26 = c.ewm(span=cfg.MACD_SLOW,   adjust=False).mean()
    d["macd"]        = ema12 - ema26
    d["macd_signal"] = d["macd"].ewm(span=cfg.MACD_SIGNAL, adjust=False).mean()
    d["macd_hist"]   = d["macd"] - d["macd_signal"]

    # Stochastic
    low_min  = l.rolling(cfg.STOCH_K).min()
    high_max = h.rolling(cfg.STOCH_K).max()
    d["stoch_k"] = 100 * (c - low_min) / (high_max - low_min + 1e-9)
    d["stoch_d"] = d["stoch_k"].rolling(cfg.STOCH_D).mean()

    # Rate of change
    d["roc_5"]  = c.pct_change(5)
    d["roc_10"] = c.pct_change(10)
    d["roc_20"] = c.pct_change(20)

    # ── Volume / Microstructure ───────────────────────────────────────────────
    # OBV
    obv = (np.sign(c.diff()) * v).fillna(0).cumsum()
    d["obv"]     = obv
    d["obv_ema"] = obv.ewm(span=cfg.OBV_EMA_PERIOD, adjust=False).mean()
    d["obv_div"] = d["obv"] - d["obv_ema"]   # divergence from OBV-EMA

    # CMF (Chaikin Money Flow)
    mfm    = ((c - l) - (h - c)) / (h - l + 1e-9)
    mfv    = mfm * v
    d["cmf"] = mfv.rolling(cfg.CMF_PERIOD).sum() / (v.rolling(cfg.CMF_PERIOD).sum() + 1e-9)

    # Volume Z-score
    vol_mean = v.rolling(50).mean()
    vol_std  = v.rolling(50).std()
    d["vol_zscore"] = (v - vol_mean) / (vol_std + 1e-9)

    # Candle anatomy
    d["body_ratio"] = (c - o).abs() / (h - l + 1e-9)   # body / range
    d["upper_wick"] = (h - pd.concat([c, o], axis=1).max(axis=1)) / (h - l + 1e-9)
    d["lower_wick"] = (pd.concat([c, o], axis=1).min(axis=1) - l) / (h - l + 1e-9)
    d["close_pct"]  = (c - o) / (o + 1e-9)             # candle return

    # ── ADX / DMI ─────────────────────────────────────────────────────────────
    plus_dm  = (h - h.shift()).clip(lower=0)
    minus_dm = (l.shift() - l).clip(lower=0)
    tr14  = tr.ewm(span=cfg.ADX_PERIOD, adjust=False).mean()
    di_p  = 100 * plus_dm.ewm(span=cfg.ADX_PERIOD,  adjust=False).mean() / (tr14 + 1e-9)
    di_m  = 100 * minus_dm.ewm(span=cfg.ADX_PERIOD, adjust=False).mean() / (tr14 + 1e-9)
    dx    = 100 * (di_p - di_m).abs() / (di_p + di_m + 1e-9)
    d["adx"]  = dx.ewm(span=cfg.ADX_PERIOD, adjust=False).mean()
    d["di_p"] = di_p
    d["di_m"] = di_m

    # ── Rolling VWAP ──────────────────────────────────────────────────────────
    tp = (h + l + c) / 3
    vwap_num = (tp * v).rolling(cfg.VWAP_PERIOD).sum()
    vwap_den = v.rolling(cfg.VWAP_PERIOD).sum()
    d["vwap"]      = vwap_num / (vwap_den + 1e-9)
    d["vwap_dist"] = (c - d["vwap"]) / d["vwap"]    # signed distance

    # ── Whale bar detection ───────────────────────────────────────────────────
    vol_rolling_mean = v.rolling(cfg.WHALE_LOOKBACK).mean()
    d["is_whale_bar"] = (v > cfg.WHALE_VOL_MULT * vol_rolling_mean).astype(int)

    # ── Lag features (t-1, t-2) ───────────────────────────────────────────────
    for col in ["rsi", "macd_hist", "cmf", "vol_zscore", "bb_pos"]:
        d[f"{col}_lag1"] = d[col].shift(1)
        d[f"{col}_lag2"] = d[col].shift(2)

    return d


FEATURE_COLS = [
    "ema_cross", "price_vs_trend",
    "atr_pct", "bb_width", "bb_pos",
    "rsi", "macd_hist", "stoch_k", "stoch_d",
    "roc_5", "roc_10", "roc_20",
    "obv_div", "cmf", "vol_zscore",
    "body_ratio", "upper_wick", "lower_wick", "close_pct",
    "adx", "di_p", "di_m",
    "vwap_dist",
    "is_whale_bar",
    # lags
    "rsi_lag1", "rsi_lag2",
    "macd_hist_lag1", "macd_hist_lag2",
    "cmf_lag1", "cmf_lag2",
    "vol_zscore_lag1", "vol_zscore_lag2",
    "bb_pos_lag1", "bb_pos_lag2",
]


# ─────────────────────────────────────────────────────────────────────────────
#  LABEL CREATION
# ─────────────────────────────────────────────────────────────────────────────

def make_labels(df: pd.DataFrame) -> pd.Series:
    """
    Forward-looking label for classification:
      0 = hold/noise
      1 = long (price rises > LABEL_THRESH_ATR × ATR + round-trip fee in next N bars)
      2 = short (price falls > LABEL_THRESH_ATR × ATR + round-trip fee in next N bars)

    Порог включает комиссию биржи (cfg.ROUND_TRIP_FEE × close),
    чтобы модель обучалась только на движениях, реально прибыльных после fees.
    """
    n = cfg.LABEL_FUTURE_BARS
    # ATR-порог + стоимость round-trip комиссии в единицах цены
    fee_cost = cfg.ROUND_TRIP_FEE * df["close"]
    thresh = cfg.LABEL_THRESH_ATR * df["atr"] + fee_cost

    # Align properly: we want max/min over next n bars
    # rolling(n).max() on shifted gives us wrong direction; use .rolling on reversed
    future_max = df["high"].iloc[::-1].rolling(n).max().iloc[::-1].shift(1)
    future_min = df["low"].iloc[::-1].rolling(n).min().iloc[::-1].shift(1)

    label = pd.Series(0, index=df.index)
    label[future_max - df["close"] > thresh] = 1
    label[df["close"] - future_min > thresh] = 2
    # if both conditions met (wide range bar), keep 0 (ambiguous)
    both = (future_max - df["close"] > thresh) & (df["close"] - future_min > thresh)
    label[both] = 0
    return label


# ─────────────────────────────────────────────────────────────────────────────
#  MODEL MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class ModelManager:
    """
    Wraps a CatBoostClassifier with:
    - Persistent save/load to disk
    - Retraining only when validation F1 improves
    - Thread-safe in-process use
    """

    def __init__(self):
        Path(cfg.MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.model: Optional[CatBoostClassifier] = None
        self.best_f1: float = 0.0
        self.bars_since_retrain: int = 0
        self._load_or_init()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_or_init(self):
        """Load existing model from disk, or create a fresh untrained one."""
        if os.path.exists(cfg.MODEL_PATH):
            self.model = CatBoostClassifier()
            self.model.load_model(cfg.MODEL_PATH)
            meta = self._load_meta()
            self.best_f1 = meta.get("best_f1", 0.0)
            logger.info("Loaded CatBoost model from %s (best_f1=%.4f)", cfg.MODEL_PATH, self.best_f1)
        else:
            self.model = CatBoostClassifier(**cfg.CATBOOST_PARAMS)
            logger.info("No saved model found — will train on first retrain cycle.")

    def _save(self, f1: float):
        self.model.save_model(cfg.MODEL_PATH)
        meta = {"best_f1": f1, "feature_cols": FEATURE_COLS}
        with open(cfg.MODEL_META_PATH, "w") as fp:
            json.dump(meta, fp, indent=2)
        self.best_f1 = f1
        logger.info("✅ Model saved (new best_f1=%.4f)", f1)

    @staticmethod
    def _load_meta() -> dict:
        if os.path.exists(cfg.MODEL_META_PATH):
            with open(cfg.MODEL_META_PATH) as fp:
                return json.load(fp)
        return {}

    # ── Training ──────────────────────────────────────────────────────────────

    def try_retrain(self, df_full: pd.DataFrame) -> bool:
        """
        Attempt retraining on df_full (OHLCV with feature cols).
        Returns True if a new model was accepted.

        Strategy:
          1. Compute features + labels
          2. Time-ordered train/val split
          3. Train fresh CatBoostClassifier
          4. Evaluate weighted F1 on val set
          5. Accept new model ONLY if F1 > best_F1 + MIN_IMPROVEMENT_PCT
        """
        self.bars_since_retrain = 0

        # ── Prepare dataset ──────────────────────────────────────────────────
        df = compute_features(df_full.copy())
        df["label"] = make_labels(df)

        # Drop rows with NaN features or look-ahead leakage
        df = df.dropna(subset=FEATURE_COLS + ["label", "atr"])
        # Drop the last N rows whose labels are not yet reliable
        df = df.iloc[: -cfg.LABEL_FUTURE_BARS]

        if len(df) < cfg.RETRAIN_MIN_SAMPLES:
            logger.warning(
                "Not enough data to retrain: %d rows (need %d)",
                len(df), cfg.RETRAIN_MIN_SAMPLES,
            )
            return False

        # Rolling window
        if len(df) > cfg.TRAIN_WINDOW_BARS:
            df = df.iloc[-cfg.TRAIN_WINDOW_BARS:]

        X = df[FEATURE_COLS].values.astype(np.float32)
        y = df["label"].values.astype(int)

        # Time-ordered split (no shuffle!)
        split = int(len(X) * (1 - cfg.VALIDATION_SPLIT))
        X_tr, X_val = X[:split], X[split:]
        y_tr, y_val = y[:split], y[split:]

        if len(np.unique(y_tr)) < 3:
            logger.warning("Training set missing some classes — skipping retrain.")
            return False

        logger.info(
            "Retraining on %d samples (train=%d, val=%d), class dist train=%s",
            len(X),
            split,
            len(X_val),
            dict(zip(*np.unique(y_tr, return_counts=True))),
        )

        # ── Train ────────────────────────────────────────────────────────────
        candidate = CatBoostClassifier(**cfg.CATBOOST_PARAMS)
        train_pool = Pool(X_tr, y_tr, feature_names=FEATURE_COLS)
        eval_pool  = Pool(X_val, y_val,  feature_names=FEATURE_COLS)
        candidate.fit(train_pool, eval_set=eval_pool, use_best_model=True)

        # ── Evaluate ─────────────────────────────────────────────────────────
        y_pred = candidate.predict(X_val).flatten().astype(int)
        new_f1 = f1_score(y_val, y_pred, average="weighted", zero_division=0)

        logger.info(
            "Retrain result: new_f1=%.4f | best_f1=%.4f | threshold=%.4f",
            new_f1, self.best_f1, self.best_f1 + cfg.MIN_IMPROVEMENT_PCT,
        )

        # ── Accept only if improves ───────────────────────────────────────────
        if new_f1 >= self.best_f1 + cfg.MIN_IMPROVEMENT_PCT:
            self.model = candidate
            self._save(new_f1)
            return True
        else:
            logger.info("❌ New model NOT accepted (no improvement). Keeping old model.")
            return False

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """
        features: 1-D array of FEATURE_COLS values for the latest bar.
        Returns probability vector [p_hold, p_long, p_short].
        """
        if self.model is None or not hasattr(self.model, "predict_proba"):
            return np.array([1.0, 0.0, 0.0])
        X = features.reshape(1, -1).astype(np.float32)
        return self.model.predict_proba(X)[0]

    def predict_signal(
        self, df: pd.DataFrame
    ) -> Tuple[int, float, float, float]:
        """
        Run feature engineering on the tail of df, get latest bar features,
        and return (signal, p_hold, p_long, p_short) where:
          signal: 0=hold, 1=long, 2=short
        """
        feat_df = compute_features(df)
        row = feat_df.iloc[-1]
        missing = [c for c in FEATURE_COLS if pd.isna(row.get(c, np.nan))]
        if missing:
            logger.debug("NaN features: %s → hold", missing)
            return 0, 1.0, 0.0, 0.0

        feats = np.array([row[c] for c in FEATURE_COLS], dtype=np.float32)
        proba = self.predict_proba(feats)
        p_hold, p_long, p_short = proba[0], proba[1], proba[2]

        if p_long >= cfg.LONG_PROB_THRESH:
            signal = 1
        elif p_short >= cfg.SHORT_PROB_THRESH:
            signal = 2
        else:
            signal = 0

        return signal, p_hold, p_long, p_short

    def is_trained(self) -> bool:
        return self.model is not None and self.model.is_fitted()

    def feature_importance(self) -> pd.DataFrame:
        if not self.is_trained():
            return pd.DataFrame()
        imp = self.model.get_feature_importance(type="FeatureImportance")
        return pd.DataFrame({"feature": FEATURE_COLS, "importance": imp}).sort_values(
            "importance", ascending=False
        )
