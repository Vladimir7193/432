"""
=============================================================
multi_model_manager.py — Multi-pair model management
=============================================================
Manages separate CatBoost models for each trading pair.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import f1_score

import config as cfg
from signal_engine import compute_features, make_labels, FEATURE_COLS

logger = logging.getLogger(__name__)


class MultiModelManager:
    """
    Manages separate CatBoost models for each trading pair.
    
    Each pair gets:
    - Its own trained model
    - Independent retraining schedule
    - Separate performance tracking
    """
    
    def __init__(self):
        Path(cfg.MODELS_DIR).mkdir(parents=True, exist_ok=True)
        self.models: Dict[str, Optional[CatBoostClassifier]] = {}
        self.best_f1s: Dict[str, float] = {}
        self.bars_since_retrain: Dict[str, int] = {}
        
        # Load existing models for all symbols
        for symbol in cfg.SYMBOLS:
            self._load_or_init(symbol)
    
    def _get_model_path(self, symbol: str) -> str:
        """Get file path for a symbol's model."""
        return os.path.join(cfg.MODELS_DIR, f"{symbol}_model.cbm")
    
    def _get_meta_path(self, symbol: str) -> str:
        """Get file path for a symbol's metadata."""
        return os.path.join(cfg.MODELS_DIR, f"{symbol}_meta.json")
    
    def _load_or_init(self, symbol: str):
        """Load existing model from disk, or create a fresh untrained one."""
        model_path = self._get_model_path(symbol)
        
        if os.path.exists(model_path):
            model = CatBoostClassifier()
            model.load_model(model_path)
            self.models[symbol] = model
            
            meta = self._load_meta(symbol)
            self.best_f1s[symbol] = meta.get("best_f1", 0.0)
            self.bars_since_retrain[symbol] = 0
            
            logger.info("Loaded model for %s (F1=%.4f)", symbol, self.best_f1s[symbol])
        else:
            self.models[symbol] = CatBoostClassifier(**cfg.CATBOOST_PARAMS)
            self.best_f1s[symbol] = 0.0
            self.bars_since_retrain[symbol] = 0
            logger.info("Initialized new model for %s", symbol)
    
    def _save(self, symbol: str, f1: float):
        """Save model and metadata to disk."""
        model = self.models[symbol]
        if model is None:
            return
        
        model_path = self._get_model_path(symbol)
        meta_path = self._get_meta_path(symbol)
        
        model.save_model(model_path)
        meta = {"best_f1": f1, "feature_cols": FEATURE_COLS, "symbol": symbol}
        with open(meta_path, "w") as fp:
            json.dump(meta, fp, indent=2)
        
        self.best_f1s[symbol] = f1
        logger.info("✅ Model saved for %s (F1=%.4f)", symbol, f1)
    
    def _load_meta(self, symbol: str) -> dict:
        """Load metadata for a symbol."""
        meta_path = self._get_meta_path(symbol)
        if os.path.exists(meta_path):
            with open(meta_path) as fp:
                return json.load(fp)
        return {}
    
    def try_retrain(self, symbol: str, df_full: pd.DataFrame) -> bool:
        """
        Attempt retraining for a specific symbol.
        
        Args:
            symbol: Trading pair symbol
            df_full: OHLCV DataFrame for this symbol
        
        Returns:
            True if new model was accepted
        """
        self.bars_since_retrain[symbol] = 0
        
        # Prepare dataset
        df = compute_features(df_full.copy())
        df["label"] = make_labels(df)
        
        # Drop NaN and look-ahead rows
        df = df.dropna(subset=FEATURE_COLS + ["label", "atr"])
        df = df.iloc[: -cfg.LABEL_FUTURE_BARS]
        
        if len(df) < cfg.RETRAIN_MIN_SAMPLES:
            logger.warning(
                "%s: Not enough data (%d rows, need %d)",
                symbol, len(df), cfg.RETRAIN_MIN_SAMPLES
            )
            return False
        
        # Rolling window
        if len(df) > cfg.TRAIN_WINDOW_BARS:
            df = df.iloc[-cfg.TRAIN_WINDOW_BARS:]
        
        X = df[FEATURE_COLS].values.astype(np.float32)
        y = df["label"].values.astype(int)
        
        # Time-ordered split
        split = int(len(X) * (1 - cfg.VALIDATION_SPLIT))
        X_tr, X_val = X[:split], X[split:]
        y_tr, y_val = y[:split], y[split:]
        
        if len(np.unique(y_tr)) < 3:
            logger.warning("%s: Missing classes in training set", symbol)
            return False
        
        logger.info(
            "%s: Retraining on %d samples (train=%d, val=%d)",
            symbol, len(X), split, len(X_val)
        )
        
        # Train
        candidate = CatBoostClassifier(**cfg.CATBOOST_PARAMS)
        train_pool = Pool(X_tr, y_tr, feature_names=FEATURE_COLS)
        eval_pool = Pool(X_val, y_val, feature_names=FEATURE_COLS)
        candidate.fit(train_pool, eval_set=eval_pool, use_best_model=True)
        
        # Evaluate
        y_pred = candidate.predict(X_val).flatten().astype(int)
        new_f1 = f1_score(y_val, y_pred, average="weighted", zero_division=0.0)
        
        best_f1 = self.best_f1s.get(symbol, 0.0)
        logger.info(
            "%s: new_f1=%.4f | best_f1=%.4f | threshold=%.4f",
            symbol, new_f1, best_f1, best_f1 + cfg.MIN_IMPROVEMENT_PCT
        )
        
        # Accept only if improves
        if new_f1 >= best_f1 + cfg.MIN_IMPROVEMENT_PCT:
            self.models[symbol] = candidate
            self._save(symbol, new_f1)
            return True
        else:
            logger.info("%s: ❌ Model NOT accepted (no improvement)", symbol)
            return False
    
    def predict_signal(
        self, symbol: str, df: pd.DataFrame
    ) -> Tuple[int, float, float, float]:
        """
        Predict signal for a specific symbol.
        
        Returns:
            (signal, p_hold, p_long, p_short)
            signal: 0=hold, 1=long, 2=short
        """
        model = self.models.get(symbol)
        if model is None or not self.is_trained(symbol):
            return 0, 1.0, 0.0, 0.0
        
        feat_df = compute_features(df)
        row = feat_df.iloc[-1]
        
        # Check for NaN features
        missing = [c for c in FEATURE_COLS if pd.isna(row.get(c, np.nan))]
        if missing:
            logger.debug("%s: NaN features: %s → hold", symbol, missing)
            return 0, 1.0, 0.0, 0.0
        
        # Extract features
        feats = np.array([row[c] for c in FEATURE_COLS], dtype=np.float32)
        X = feats.reshape(1, -1)
        
        # Predict
        proba = model.predict_proba(X)[0]
        p_hold, p_long, p_short = proba[0], proba[1], proba[2]
        
        # Determine signal
        if p_long >= cfg.LONG_PROB_THRESH:
            signal = 1
        elif p_short >= cfg.SHORT_PROB_THRESH:
            signal = 2
        else:
            signal = 0
        
        return signal, p_hold, p_long, p_short
    
    def is_trained(self, symbol: str) -> bool:
        """Check if model for symbol is trained."""
        model = self.models.get(symbol)
        if model is None:
            return False
        return model.is_fitted()
    
    def increment_bar_count(self, symbol: str):
        """Increment bar counter for retrain scheduling."""
        if symbol not in self.bars_since_retrain:
            self.bars_since_retrain[symbol] = 0
        self.bars_since_retrain[symbol] += 1
    
    def should_retrain(self, symbol: str) -> bool:
        """Check if symbol is due for retraining."""
        bars = self.bars_since_retrain.get(symbol, 0)
        return bars >= cfg.RETRAIN_EVERY_N
    
    def get_stats(self) -> dict:
        """Get statistics about all models."""
        trained = sum(1 for s in cfg.SYMBOLS if self.is_trained(s))
        avg_f1 = np.mean([f1 for f1 in self.best_f1s.values() if f1 > 0]) if self.best_f1s else 0.0
        
        return {
            "total_models": len(cfg.SYMBOLS),
            "trained_models": trained,
            "avg_f1": avg_f1,
            "best_pair": max(self.best_f1s.items(), key=lambda x: x[1])[0] if self.best_f1s else None,
        }
