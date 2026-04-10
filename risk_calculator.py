"""
=============================================================
risk_calculator.py — Расширенный калькулятор рисков
=============================================================
Дополнительные функции управления рисками:
- Расчёт оптимального размера позиции по Kelly Criterion
- Анализ Value at Risk (VaR)
- Расчёт Conditional VaR (CVaR)
- Оценка вероятности разорения
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    max_fraction: float = 0.25
) -> float:
    """
    Расчёт оптимального размера позиции по критерию Келли.
    
    Args:
        win_rate: процент прибыльных сделок (0-1)
        avg_win: средняя прибыль на прибыльную сделку
        avg_loss: средний убыток на убыточную сделку (положительное число)
        max_fraction: максимальная доля капитала (ограничение)
    
    Returns:
        Оптимальная доля капитала для риска (0-1)
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0
    
    # Kelly formula: f = (p*b - q) / b
    # где p = win_rate, q = 1-p, b = avg_win/avg_loss
    b = avg_win / avg_loss
    q = 1 - win_rate
    
    kelly_fraction = (win_rate * b - q) / b
    
    # Ограничиваем максимум и используем половину Kelly (более консервативно)
    kelly_fraction = max(0, min(kelly_fraction * 0.5, max_fraction))
    
    return kelly_fraction


def calculate_var(
    returns: pd.Series,
    confidence: float = 0.95,
    method: str = "historical"
) -> float:
    """
    Расчёт Value at Risk (VaR) - максимальный ожидаемый убыток.
    
    Args:
        returns: серия доходностей
        confidence: уровень доверия (0.95 = 95%)
        method: метод расчёта ("historical" или "parametric")
    
    Returns:
        VaR как положительное число (потенциальный убыток)
    """
    if len(returns) < 10:
        return 0.0
    
    if method == "historical":
        # Исторический VaR: квантиль распределения
        var = -returns.quantile(1 - confidence)
    else:
        # Параметрический VaR: предполагаем нормальное распределение
        mean = returns.mean()
        std = returns.std()
        from scipy import stats
        z_score = stats.norm.ppf(confidence)
        var = -(mean - z_score * std)
    
    return max(0, var)


def calculate_cvar(
    returns: pd.Series,
    confidence: float = 0.95
) -> float:
    """
    Расчёт Conditional VaR (CVaR) / Expected Shortfall.
    Средний убыток в худших (1-confidence)% случаев.
    
    Args:
        returns: серия доходностей
        confidence: уровень доверия
    
    Returns:
        CVaR как положительное число
    """
    if len(returns) < 10:
        return 0.0
    
    var = calculate_var(returns, confidence, method="historical")
    # CVaR = среднее значение убытков, превышающих VaR
    worst_returns = returns[returns <= -var]
    
    if len(worst_returns) == 0:
        return var
    
    cvar = -worst_returns.mean()
    return max(0, cvar)


def risk_of_ruin(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    risk_per_trade: float,
    target_drawdown: float = 0.50
) -> float:
    """
    Оценка вероятности разорения (Risk of Ruin).
    
    Args:
        win_rate: процент прибыльных сделок (0-1)
        avg_win: средняя прибыль
        avg_loss: средний убыток (положительное число)
        risk_per_trade: риск на сделку как доля капитала
        target_drawdown: целевая просадка для расчёта (0.5 = 50%)
    
    Returns:
        Вероятность разорения (0-1)
    """
    if win_rate <= 0 or win_rate >= 1 or avg_loss <= 0:
        return 1.0
    
    # Упрощённая формула Risk of Ruin
    # RoR = ((1-W)/W)^(U/A)
    # где W = win_rate, U = units to lose, A = advantage per trade
    
    advantage = win_rate * avg_win - (1 - win_rate) * avg_loss
    
    if advantage <= 0:
        return 1.0  # Отрицательное матожидание = гарантированное разорение
    
    # Количество "единиц" до разорения
    units_to_lose = target_drawdown / risk_per_trade
    
    # Вероятность разорения
    ror = ((1 - win_rate) / win_rate) ** (units_to_lose * advantage / avg_win)
    
    return min(1.0, max(0.0, ror))


def calculate_sharpe_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252
) -> float:
    """
    Расчёт коэффициента Шарпа.
    
    Args:
        returns: серия доходностей
        risk_free_rate: безрисковая ставка (годовая)
        periods_per_year: количество периодов в году
    
    Returns:
        Коэффициент Шарпа (годовой)
    """
    if len(returns) < 2:
        return 0.0
    
    excess_returns = returns - (risk_free_rate / periods_per_year)
    
    if excess_returns.std() == 0:
        return 0.0
    
    sharpe = excess_returns.mean() / excess_returns.std() * np.sqrt(periods_per_year)
    return sharpe


def calculate_sortino_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252
) -> float:
    """
    Расчёт коэффициента Сортино (учитывает только downside volatility).
    
    Args:
        returns: серия доходностей
        risk_free_rate: безрисковая ставка
        periods_per_year: количество периодов в году
    
    Returns:
        Коэффициент Сортино
    """
    if len(returns) < 2:
        return 0.0
    
    excess_returns = returns - (risk_free_rate / periods_per_year)
    
    # Downside deviation: только отрицательные отклонения
    downside_returns = excess_returns[excess_returns < 0]
    
    if len(downside_returns) == 0:
        return float('inf')
    
    downside_std = downside_returns.std()
    
    if downside_std == 0:
        return 0.0
    
    sortino = excess_returns.mean() / downside_std * np.sqrt(periods_per_year)
    return sortino


def calculate_calmar_ratio(
    returns: pd.Series,
    periods_per_year: int = 252
) -> float:
    """
    Расчёт коэффициента Калмара (доходность / максимальная просадка).
    
    Args:
        returns: серия доходностей
        periods_per_year: количество периодов в году
    
    Returns:
        Коэффициент Калмара
    """
    if len(returns) < 2:
        return 0.0
    
    # Годовая доходность
    total_return = (1 + returns).prod() - 1
    years = len(returns) / periods_per_year
    annual_return = (1 + total_return) ** (1 / years) - 1
    
    # Максимальная просадка
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_dd = abs(drawdown.min())
    
    if max_dd == 0:
        return float('inf')
    
    calmar = annual_return / max_dd
    return calmar


class RiskAnalyzer:
    """
    Комплексный анализатор рисков для торговой системы.
    """
    
    def __init__(self, trades_df: pd.DataFrame):
        """
        Args:
            trades_df: DataFrame со сделками (должен содержать колонку pnl_pct)
        """
        self.trades_df = trades_df
        
        if "pnl_pct" in trades_df.columns:
            self.returns = trades_df["pnl_pct"] / 100
        else:
            self.returns = pd.Series(dtype=float)
    
    def get_full_analysis(self) -> dict:
        """
        Получить полный анализ рисков.
        
        Returns:
            Словарь с метриками рисков
        """
        if len(self.returns) < 10:
            return {
                "error": "Недостаточно данных для анализа (минимум 10 сделок)"
            }
        
        wins = self.returns[self.returns > 0]
        losses = self.returns[self.returns < 0]
        
        win_rate = len(wins) / len(self.returns) if len(self.returns) > 0 else 0
        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
        
        analysis = {
            # Базовые метрики
            "total_trades": len(self.returns),
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            
            # Коэффициенты
            "sharpe_ratio": round(calculate_sharpe_ratio(self.returns), 3),
            "sortino_ratio": round(calculate_sortino_ratio(self.returns), 3),
            "calmar_ratio": round(calculate_calmar_ratio(self.returns), 3),
            
            # Risk metrics
            "var_95": round(calculate_var(self.returns, 0.95), 4),
            "cvar_95": round(calculate_cvar(self.returns, 0.95), 4),
            
            # Kelly & Risk of Ruin
            "kelly_fraction": round(kelly_criterion(win_rate, avg_win, avg_loss), 4),
            "risk_of_ruin_50": round(risk_of_ruin(win_rate, avg_win, avg_loss, 0.01, 0.50), 4),
        }
        
        return analysis
    
    def print_report(self):
        """Вывести отчёт по рискам в консоль."""
        analysis = self.get_full_analysis()
        
        if "error" in analysis:
            logger.warning(analysis["error"])
            return
        
        print("\n" + "=" * 60)
        print("  АНАЛИЗ РИСКОВ ТОРГОВОЙ СИСТЕМЫ")
        print("=" * 60)
        print(f"  Всего сделок:        {analysis['total_trades']}")
        print(f"  Win Rate:            {analysis['win_rate']*100:.1f}%")
        print(f"  Средняя прибыль:     {analysis['avg_win']*100:.2f}%")
        print(f"  Средний убыток:      {analysis['avg_loss']*100:.2f}%")
        print("-" * 60)
        print(f"  Sharpe Ratio:        {analysis['sharpe_ratio']:.2f}")
        print(f"  Sortino Ratio:       {analysis['sortino_ratio']:.2f}")
        print(f"  Calmar Ratio:        {analysis['calmar_ratio']:.2f}")
        print("-" * 60)
        print(f"  VaR (95%):           {analysis['var_95']*100:.2f}%")
        print(f"  CVaR (95%):          {analysis['cvar_95']*100:.2f}%")
        print("-" * 60)
        print(f"  Kelly Fraction:      {analysis['kelly_fraction']*100:.1f}%")
        print(f"  Risk of Ruin (50%):  {analysis['risk_of_ruin_50']*100:.2f}%")
        print("=" * 60 + "\n")
