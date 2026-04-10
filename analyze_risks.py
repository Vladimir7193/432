"""
=============================================================
analyze_risks.py — Быстрый анализ рисков торговой системы
=============================================================
Запуск: python analyze_risks.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from risk_calculator import RiskAnalyzer, kelly_criterion


def main():
    """Основная функция анализа."""
    print("\n" + "=" * 70)
    print("  🔍 АНАЛИЗ РИСКОВ ТОРГОВОЙ СИСТЕМЫ")
    print("=" * 70)
    
    # Загрузить сделки
    trades_path = cfg.TRADE_LOG_CSV
    
    if not Path(trades_path).exists():
        print(f"\n  ❌ Файл сделок не найден: {trades_path}")
        print("  Запустите бота и совершите несколько сделок перед анализом.\n")
        return
    
    trades = pd.read_csv(trades_path)
    
    if len(trades) < 10:
        print(f"\n  ⚠️  Недостаточно сделок для анализа: {len(trades)}")
        print("  Минимум 10 сделок требуется для статистической значимости.\n")
        return
    
    print(f"\n  📊 Загружено сделок: {len(trades)}")
    
    # Создать анализатор
    analyzer = RiskAnalyzer(trades)
    
    # Вывести полный отчёт
    analyzer.print_report()
    
    # Дополнительная информация
    analysis = analyzer.get_full_analysis()
    
    print("=" * 70)
    print("  💡 РЕКОМЕНДАЦИИ")
    print("=" * 70)
    
    # Рекомендация по размеру позиции
    kelly = analysis['kelly_fraction']
    current_risk = cfg.RISK_PCT
    
    print(f"\n  Текущий RISK_PCT:        {current_risk*100:.2f}%")
    print(f"  Kelly Criterion:         {kelly*100:.2f}%")
    
    if kelly > current_risk * 1.5:
        print(f"  ✅ Можно увеличить риск до {kelly*100:.1f}%")
    elif kelly < current_risk * 0.5:
        print(f"  ⚠️  Рекомендуется снизить риск до {kelly*100:.1f}%")
    else:
        print(f"  ✅ Текущий риск оптимален")
    
    # Оценка качества системы
    print("\n  Оценка торговой системы:")
    
    sharpe = analysis['sharpe_ratio']
    if sharpe > 2.0:
        print(f"  ⭐⭐⭐ Отличная система (Sharpe={sharpe:.2f})")
    elif sharpe > 1.0:
        print(f"  ⭐⭐ Хорошая система (Sharpe={sharpe:.2f})")
    elif sharpe > 0.5:
        print(f"  ⭐ Приемлемая система (Sharpe={sharpe:.2f})")
    else:
        print(f"  ⚠️  Слабая система (Sharpe={sharpe:.2f})")
    
    # Risk of Ruin
    ror = analysis['risk_of_ruin_50']
    if ror < 0.01:
        print(f"  ✅ Низкий риск разорения ({ror*100:.2f}%)")
    elif ror < 0.05:
        print(f"  ⚠️  Умеренный риск разорения ({ror*100:.2f}%)")
    else:
        print(f"  🚨 Высокий риск разорения ({ror*100:.2f}%)")
    
    # VaR
    var = analysis['var_95']
    print(f"\n  Максимальный ожидаемый убыток (VaR 95%): {var*100:.2f}%")
    print(f"  На капитал $1000 это: ${var*1000:.2f}")
    
    print("\n" + "=" * 70)
    print("  📝 Для изменения RISK_PCT отредактируйте config.py")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
