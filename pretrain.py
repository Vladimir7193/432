"""
=============================================================
pretrain.py — Offline pre-training on synthetic OHLCV data
=============================================================
Запусти один раз перед стартом бота:
    python pretrain.py

Что делает:
  1. Генерирует реалистичные OHLCV (~15 000 баров) на основе:
     - Геометрического броуновского движения (GBM)
     - Сменяющихся режимов волатильности (VIX-like)
     - Случайных трендовых фаз (bull/bear/sideways)
     - Реалистичного объёма с whale-всплесками
  2. Вычисляет все технические фичи (те же, что в signal_engine.py)
  3. Создаёт метки (hold/long/short) по ATR-правилу
  4. Обучает CatBoostClassifier с кросс-валидацией
  5. Сохраняет модель в models/catboost_model.cbm
  6. Печатает метрики качества

После этого bybit_paper_bot.py сразу стартует с рабочей моделью.
Каждые 500 реальных баров модель будет пробовать переобучиться
на реальных данных (и принимать только если F1 улучшается).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import StratifiedKFold

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pretrain")

# Импортируем конфиг и фичи из нашего проекта
sys.path.insert(0, str(Path(__file__).parent))
import config as cfg
from signal_engine import compute_features, make_labels, FEATURE_COLS


# ─────────────────────────────────────────────────────────────────────────────
#  СИНТЕТИЧЕСКИЙ ГЕНЕРАТОР OHLCV
# ─────────────────────────────────────────────────────────────────────────────

def generate_synthetic_ohlcv(
    n_bars: int = 15_000,
    start_price: float = 50_000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Генерирует реалистичные OHLCV данные.

    Модель цены:
      - Сменяющиеся режимы: bull / bear / sideways
      - Кластеризация волатильности (GARCH-like)
      - Intraday mean-reversion внутри тренда
      - Whale-всплески объёма (Pareto-распределение)
    """
    rng = np.random.default_rng(seed)
    logger.info("Генерируем %d синтетических баров...", n_bars)

    # ── Режимы рынка ──────────────────────────────────────────────────────────
    # Каждый режим длится от 50 до 400 баров
    regimes = []  # (drift_per_bar, base_vol)
    regime_params = {
        "bull":     (+0.00025, 0.0018),
        "bear":     (-0.00020, 0.0020),
        "sideways": (+0.00001, 0.0010),
    }
    bar = 0
    while bar < n_bars:
        regime   = rng.choice(["bull", "bear", "sideways"], p=[0.35, 0.30, 0.35])
        duration = int(rng.integers(50, 400))
        drift, vol = regime_params[regime]
        regimes.extend([(drift, vol, regime)] * min(duration, n_bars - bar))
        bar += duration

    regimes = regimes[:n_bars]
    drifts   = np.array([r[0] for r in regimes])
    base_vols= np.array([r[1] for r in regimes])
    regime_names = [r[2] for r in regimes]

    # ── Кластеризация волатильности (GARCH-like) ──────────────────────────────
    vol_series = np.zeros(n_bars)
    vol_series[0] = base_vols[0]
    alpha, beta = 0.10, 0.85   # GARCH(1,1) коэффициенты
    for i in range(1, n_bars):
        shock = rng.normal(0, base_vols[i])
        vol_series[i] = np.sqrt(
            (1 - alpha - beta) * base_vols[i] ** 2
            + alpha * shock ** 2
            + beta * vol_series[i - 1] ** 2
        )
    vol_series = np.clip(vol_series, 0.0005, 0.015)

    # ── Цена закрытия ─────────────────────────────────────────────────────────
    returns  = drifts + vol_series * rng.standard_normal(n_bars)
    log_price = np.log(start_price) + np.cumsum(returns)
    close     = np.exp(log_price)

    # ── OHLC из close ────────────────────────────────────────────────────────
    hl_range = close * vol_series * rng.uniform(1.5, 3.5, n_bars)
    high     = close + hl_range * rng.uniform(0.3, 0.7, n_bars)
    low      = close - hl_range * rng.uniform(0.3, 0.7, n_bars)
    open_    = np.roll(close, 1)
    open_[0] = start_price
    # Небольшой gap на открытии
    gap = rng.normal(0, vol_series) * close
    open_ = open_ + gap
    open_ = np.clip(open_, low, high)

    # ── Объём: base + whale-всплески ─────────────────────────────────────────
    base_vol_size = rng.uniform(500, 2000, n_bars)

    # Whale bars: ~2% баров имеют объём в 3-10× от среднего
    whale_mask = rng.random(n_bars) < 0.025
    whale_mult = rng.pareto(1.5, n_bars) + 3.0   # Pareto tail
    volume = base_vol_size.copy()
    volume[whale_mask] *= whale_mult[whale_mask]

    # Объём коррелирует с волатильностью
    volume *= (1 + vol_series / vol_series.mean() * 0.5)
    volume = np.abs(volume)

    # Объём выше в трендовых барах
    trend_vol_boost = np.where(np.array(regime_names) != "sideways", 1.3, 1.0)
    volume *= trend_vol_boost

    # ── Сборка DataFrame ─────────────────────────────────────────────────────
    timestamps = pd.date_range(
        start="2022-01-01 00:00:00",
        periods=n_bars,
        freq="1min",
        tz="UTC",
    )
    df = pd.DataFrame({
        "open":     open_,
        "high":     high,
        "low":      low,
        "close":    close,
        "volume":   volume,
        "turnover": close * volume,
    }, index=timestamps)

    # Гарантируем корректность OHLC
    df["high"]  = df[["open", "high", "close"]].max(axis=1)
    df["low"]   = df[["open", "low",  "close"]].min(axis=1)

    logger.info(
        "Синтетика готова: %d баров | цена %.0f→%.0f | vol_regime=%s",
        len(df),
        df["close"].iloc[0],
        df["close"].iloc[-1],
        {r: regime_names.count(r) for r in set(regime_names)},
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  ПОДГОТОВКА ДАТАСЕТА
# ─────────────────────────────────────────────────────────────────────────────

def build_dataset(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Вычисляет фичи и метки, возвращает (X, y)."""
    logger.info("Вычисляем технические индикаторы...")
    feat_df = compute_features(df.copy())
    feat_df["label"] = make_labels(feat_df)

    # Удаляем строки с NaN и look-ahead хвост
    feat_df = feat_df.dropna(subset=FEATURE_COLS + ["label", "atr"])
    feat_df = feat_df.iloc[: -cfg.LABEL_FUTURE_BARS]

    X = feat_df[FEATURE_COLS].values.astype(np.float32)
    y = feat_df["label"].values.astype(int)

    class_counts = dict(zip(*np.unique(y, return_counts=True)))
    logger.info(
        "Датасет: %d строк | классы: hold=%d long=%d short=%d",
        len(X),
        class_counts.get(0, 0),
        class_counts.get(1, 0),
        class_counts.get(2, 0),
    )
    return X, y


# ─────────────────────────────────────────────────────────────────────────────
#  ОБУЧЕНИЕ С ВРЕМЕННОЙ КРОСС-ВАЛИДАЦИЕЙ
# ─────────────────────────────────────────────────────────────────────────────

def train_with_timeseries_cv(
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 4,
) -> tuple[CatBoostClassifier, float]:
    """
    Walk-forward временная кросс-валидация (не перемешиваем данные!).
    Возвращает лучшую модель и средний F1.
    """
    logger.info("Запускаем временную кросс-валидацию (%d фолдов)...", n_splits)

    fold_size = len(X) // (n_splits + 1)
    f1_scores = []
    best_model = None
    best_f1    = -1.0

    for fold in range(n_splits):
        train_end = (fold + 1) * fold_size
        val_start = train_end
        val_end   = val_start + fold_size

        X_tr, y_tr = X[:train_end],       y[:train_end]
        X_val, y_val = X[val_start:val_end], y[val_start:val_end]

        if len(np.unique(y_tr)) < 3:
            logger.warning("Фолд %d: не все классы в train — пропускаем.", fold + 1)
            continue

        model = CatBoostClassifier(**cfg.CATBOOST_PARAMS)
        train_pool = Pool(X_tr,  y_tr,  feature_names=FEATURE_COLS)
        eval_pool  = Pool(X_val, y_val, feature_names=FEATURE_COLS)

        t0 = time.time()
        model.fit(train_pool, eval_set=eval_pool, use_best_model=True)
        elapsed = time.time() - t0

        y_pred = model.predict(X_val).flatten().astype(int)
        f1     = f1_score(y_val, y_pred, average="weighted", zero_division=0)
        f1_scores.append(f1)

        logger.info(
            "  Фолд %d/%d | train=%d val=%d | F1=%.4f | время=%.1fs",
            fold + 1, n_splits, len(X_tr), len(X_val), f1, elapsed,
        )

        if f1 > best_f1:
            best_f1    = f1
            best_model = model

    mean_f1 = float(np.mean(f1_scores)) if f1_scores else 0.0
    logger.info("Средний F1 по фолдам: %.4f | лучший фолд: %.4f", mean_f1, best_f1)
    return best_model, mean_f1


# ─────────────────────────────────────────────────────────────────────────────
#  ФИНАЛЬНОЕ ОБУЧЕНИЕ НА ВСЁМ ДАТАСЕТЕ
# ─────────────────────────────────────────────────────────────────────────────

def train_final_model(X: np.ndarray, y: np.ndarray) -> tuple[CatBoostClassifier, float]:
    """
    Обучаем финальную модель на 85% данных (временной порядок),
    валидируем на последних 15%.
    """
    logger.info("Финальное обучение на полном датасете...")
    split = int(len(X) * (1 - cfg.VALIDATION_SPLIT))

    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]

    model = CatBoostClassifier(**cfg.CATBOOST_PARAMS)
    train_pool = Pool(X_tr,  y_tr,  feature_names=FEATURE_COLS)
    eval_pool  = Pool(X_val, y_val, feature_names=FEATURE_COLS)

    t0 = time.time()
    model.fit(train_pool, eval_set=eval_pool, use_best_model=True)
    elapsed = time.time() - t0

    y_pred = model.predict(X_val).flatten().astype(int)
    f1     = f1_score(y_val, y_pred, average="weighted", zero_division=0)

    logger.info("Финальная модель обучена за %.1fs | val F1=%.4f", elapsed, f1)

    print("\n── Classification Report (hold-out 15%) ─────────────────")
    print(classification_report(
        y_val, y_pred,
        target_names=["hold", "long", "short"],
        zero_division=0,
    ))
    print("── Confusion Matrix ──────────────────────────────────────")
    cm = confusion_matrix(y_val, y_pred)
    cm_df = pd.DataFrame(
        cm,
        index=["true_hold", "true_long", "true_short"],
        columns=["pred_hold", "pred_long", "pred_short"],
    )
    print(cm_df.to_string())

    return model, f1


# ─────────────────────────────────────────────────────────────────────────────
#  FEATURE IMPORTANCE
# ─────────────────────────────────────────────────────────────────────────────

def print_feature_importance(model: CatBoostClassifier):
    imp = model.get_feature_importance(type="FeatureImportance")
    fi  = pd.DataFrame({"feature": FEATURE_COLS, "importance": imp})
    fi  = fi.sort_values("importance", ascending=False)
    print("\n── Top-15 Feature Importances ────────────────────────────")
    print(fi.head(15).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
#  СОХРАНЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────

def save_model(model: CatBoostClassifier, f1: float):
    Path(cfg.MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
    model.save_model(cfg.MODEL_PATH)
    meta = {
        "best_f1":       f1,
        "feature_cols":  FEATURE_COLS,
        "trained_on":    "synthetic_pretrain",
        "n_features":    len(FEATURE_COLS),
        "catboost_params": cfg.CATBOOST_PARAMS,
    }
    with open(cfg.MODEL_META_PATH, "w") as fp:
        json.dump(meta, fp, indent=2)
    logger.info("✅ Модель сохранена: %s  (F1=%.4f)", cfg.MODEL_PATH, f1)
    logger.info("✅ Метаданные:       %s", cfg.MODEL_META_PATH)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  CatBoost Pre-training on Synthetic OHLCV")
    print("  Symbol:", cfg.SYMBOL, "| Interval:", cfg.INTERVAL, "min")
    print("=" * 60 + "\n")

    t_total = time.time()

    # 1. Генерация синтетических данных
    df = generate_synthetic_ohlcv(n_bars=15_000, start_price=50_000.0, seed=42)

    # 2. Построение датасета
    X, y = build_dataset(df)

    if len(X) < cfg.RETRAIN_MIN_SAMPLES:
        logger.error(
            "Недостаточно данных после обработки: %d (минимум %d)",
            len(X), cfg.RETRAIN_MIN_SAMPLES,
        )
        sys.exit(1)

    # 3. Временная кросс-валидация (для оценки стабильности)
    cv_model, cv_f1 = train_with_timeseries_cv(X, y, n_splits=4)

    # 4. Финальное обучение на всём датасете
    final_model, final_f1 = train_final_model(X, y)

    # 5. Feature importance
    print_feature_importance(final_model)

    # 6. Сохранение лучшей модели
    # Берём финальную модель (обучена на максимуме данных)
    save_model(final_model, final_f1)

    elapsed = time.time() - t_total
    print("\n" + "=" * 60)
    print(f"  ✅ Pre-training завершён за {elapsed:.0f}s")
    print(f"  CV mean F1:    {cv_f1:.4f}")
    print(f"  Final val F1:  {final_f1:.4f}")
    print(f"  Модель:        {cfg.MODEL_PATH}")
    print("=" * 60)
    print("\nТеперь запускай бота:")
    print("  python bybit_paper_bot.py")
    print("  streamlit run app.py\n")


if __name__ == "__main__":
    main()
