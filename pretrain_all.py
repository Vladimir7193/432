"""
=============================================================
pretrain_all.py — Pre-training individual models for all 30 pairs
=============================================================
Запусти один раз перед стартом мультипарного бота:
    python pretrain_all.py

Что делает:
  1. Для каждой пары из cfg.SYMBOLS генерирует синтетические OHLCV
     с уникальным seed и стартовой ценой (имитирует разные активы)
  2. Обучает отдельный CatBoostClassifier
  3. Сохраняет модель в models/pairs/<SYMBOL>_model.cbm
     и метаданные в models/pairs/<SYMBOL>_meta.json

После этого MultiModelManager сразу загрузит все 30 моделей.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import f1_score

sys.path.insert(0, str(Path(__file__).parent))
import config as cfg
from signal_engine import compute_features, make_labels, FEATURE_COLS
from pretrain import generate_synthetic_ohlcv, build_dataset

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pretrain_all")

# Стартовые цены, приближённые к реальным (для реалистичной синтетики)
START_PRICES: dict[str, float] = {
    "BTCUSDT":    60_000.0,
    "ETHUSDT":     3_000.0,
    "SOLUSDT":       150.0,
    "BNBUSDT":       400.0,
    "XRPUSDT":         0.6,
    "DOGEUSDT":        0.15,
    "ADAUSDT":         0.5,
    "AVAXUSDT":       35.0,
    "LINKUSDT":       15.0,
    "DOTUSDT":         7.0,
    "MATICUSDT":       0.8,
    "LTCUSDT":        80.0,
    "UNIUSDT":        10.0,
    "ATOMUSDT":       10.0,
    "NEARUSDT":        5.0,
    "OPUSDT":          2.5,
    "ARBUSDT":         1.2,
    "APTUSDT":         8.0,
    "SUIUSDT":         1.5,
    "SEIUSDT":         0.5,
    "TIAUSDT":         8.0,
    "INJUSDT":        25.0,
    "WLDUSDT":         5.0,
    "FETUSDT":         2.0,
    "RENDERUSDT":      8.0,
    "JUPUSDT":         1.0,
    "PYTHUSDT":        0.4,
    "STRKUSDT":        1.5,
    "ONDOUSDT":        1.0,
    "ENAUSDT":         1.0,
}


def train_and_save(symbol: str, seed: int) -> float:
    """Обучает модель для одной пары и сохраняет на диск. Возвращает F1."""
    start_price = START_PRICES.get(symbol, 100.0)

    logger.info("=" * 55)
    logger.info("  [%d/30] %s  (seed=%d, start_price=%.4f)", seed - 41, symbol, seed, start_price)
    logger.info("=" * 55)

    # 1. Синтетические данные
    df = generate_synthetic_ohlcv(n_bars=15_000, start_price=start_price, seed=seed)

    # 2. Датасет
    X, y = build_dataset(df)

    if len(X) < cfg.RETRAIN_MIN_SAMPLES:
        logger.error("%s: недостаточно данных (%d), пропускаем.", symbol, len(X))
        return 0.0

    # 3. Финальное обучение (85% train / 15% val)
    split = int(len(X) * (1 - cfg.VALIDATION_SPLIT))
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]

    model = CatBoostClassifier(**cfg.CATBOOST_PARAMS)
    train_pool = Pool(X_tr, y_tr, feature_names=FEATURE_COLS)
    eval_pool  = Pool(X_val, y_val, feature_names=FEATURE_COLS)

    t0 = time.time()
    model.fit(train_pool, eval_set=eval_pool, use_best_model=True)
    elapsed = time.time() - t0

    y_pred = model.predict(X_val).flatten().astype(int)
    f1 = f1_score(y_val, y_pred, average="weighted", zero_division=0)
    logger.info("%s: F1=%.4f | время=%.1fs", symbol, f1, elapsed)

    # 4. Сохранение
    models_dir = Path(cfg.MODELS_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)

    model_path = models_dir / f"{symbol}_model.cbm"
    meta_path  = models_dir / f"{symbol}_meta.json"

    model.save_model(str(model_path))
    meta = {
        "best_f1":      f1,
        "feature_cols": FEATURE_COLS,
        "symbol":       symbol,
        "trained_on":   "synthetic_pretrain",
        "n_features":   len(FEATURE_COLS),
    }
    with open(meta_path, "w") as fp:
        json.dump(meta, fp, indent=2)

    logger.info("✅ Сохранено: %s  (F1=%.4f)", model_path, f1)
    return f1


def main():
    print("\n" + "=" * 55)
    print("  CatBoost Multi-Pair Pre-training")
    print(f"  Пар: {len(cfg.SYMBOLS)} | Папка: {cfg.MODELS_DIR}")
    print("=" * 55 + "\n")

    t_total = time.time()
    results: dict[str, float] = {}

    for i, symbol in enumerate(cfg.SYMBOLS):
        seed = 42 + i  # уникальный seed для каждой пары
        f1 = train_and_save(symbol, seed)
        results[symbol] = f1

    elapsed = time.time() - t_total
    trained = sum(1 for f in results.values() if f > 0)

    print("\n" + "=" * 55)
    print(f"  ✅ Готово: {trained}/{len(cfg.SYMBOLS)} моделей за {elapsed:.0f}s")
    print(f"  Средний F1: {np.mean(list(results.values())):.4f}")
    print("\n  Результаты по парам:")
    for sym, f1 in results.items():
        status = "✅" if f1 > 0 else "❌"
        print(f"    {status} {sym:<14} F1={f1:.4f}")
    print("=" * 55)
    print("\nТеперь запускай бота:")
    print("  python bybit_async_bot.py")
    print("  streamlit run app.py\n")


if __name__ == "__main__":
    main()
