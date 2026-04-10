"""
=============================================================
performance_monitor.py — Мониторинг производительности бота
=============================================================
Отслеживает метрики производительности в реальном времени:
- Скорость обработки баров
- Использование памяти
- Задержки API
- Статистика по моделям
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional

import psutil

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Метрики производительности за период."""
    avg_bar_process_time: float = 0.0  # среднее время обработки бара (сек)
    max_bar_process_time: float = 0.0  # максимальное время обработки
    total_bars_processed: int = 0      # всего обработано баров
    memory_usage_mb: float = 0.0       # использование памяти (МБ)
    cpu_percent: float = 0.0           # загрузка CPU (%)
    api_calls_per_min: float = 0.0    # API вызовов в минуту
    avg_api_latency_ms: float = 0.0   # средняя задержка API (мс)
    errors_count: int = 0              # количество ошибок


class PerformanceMonitor:
    """
    Мониторинг производительности торгового бота.
    
    Отслеживает:
    - Время обработки каждого бара
    - Использование ресурсов (CPU, память)
    - Задержки API запросов
    - Частоту ошибок
    """
    
    def __init__(self, window_size: int = 100):
        """
        Args:
            window_size: размер окна для скользящих средних
        """
        self.window_size = window_size
        self.bar_times: deque = deque(maxlen=window_size)
        self.api_latencies: deque = deque(maxlen=window_size)
        self.api_timestamps: deque = deque(maxlen=window_size)
        
        self.total_bars = 0
        self.total_errors = 0
        self.start_time = time.time()
        
        self.process = psutil.Process()
    
    def record_bar_processing(self, duration: float):
        """
        Записать время обработки бара.
        
        Args:
            duration: время обработки в секундах
        """
        self.bar_times.append(duration)
        self.total_bars += 1
    
    def record_api_call(self, latency_ms: float):
        """
        Записать задержку API вызова.
        
        Args:
            latency_ms: задержка в миллисекундах
        """
        now = time.time()
        self.api_latencies.append(latency_ms)
        self.api_timestamps.append(now)
        
        # Очистить старые записи (старше 60 сек)
        cutoff = now - 60
        while self.api_timestamps and self.api_timestamps[0] < cutoff:
            self.api_timestamps.popleft()
            if self.api_latencies:
                self.api_latencies.popleft()
    
    def record_error(self):
        """Записать ошибку."""
        self.total_errors += 1
    
    def get_metrics(self) -> PerformanceMetrics:
        """Получить текущие метрики производительности."""
        metrics = PerformanceMetrics()
        
        # Время обработки баров
        if self.bar_times:
            metrics.avg_bar_process_time = sum(self.bar_times) / len(self.bar_times)
            metrics.max_bar_process_time = max(self.bar_times)
        
        metrics.total_bars_processed = self.total_bars
        
        # Использование ресурсов
        try:
            mem_info = self.process.memory_info()
            metrics.memory_usage_mb = mem_info.rss / 1024 / 1024
            metrics.cpu_percent = self.process.cpu_percent(interval=0.1)
        except Exception as exc:
            logger.warning("Ошибка получения метрик ресурсов: %s", exc)
        
        # API метрики
        if self.api_timestamps:
            metrics.api_calls_per_min = len(self.api_timestamps)
        
        if self.api_latencies:
            metrics.avg_api_latency_ms = sum(self.api_latencies) / len(self.api_latencies)
        
        metrics.errors_count = self.total_errors
        
        return metrics
    
    def get_uptime_seconds(self) -> float:
        """Получить время работы бота в секундах."""
        return time.time() - self.start_time
    
    def log_metrics(self):
        """Вывести метрики в лог."""
        metrics = self.get_metrics()
        uptime = self.get_uptime_seconds()
        
        logger.info(
            "⚡ Performance: bars=%d | avg_time=%.2fs | mem=%.1fMB | cpu=%.1f%% | "
            "api_calls=%d/min | api_latency=%.0fms | errors=%d | uptime=%.0fs",
            metrics.total_bars_processed,
            metrics.avg_bar_process_time,
            metrics.memory_usage_mb,
            metrics.cpu_percent,
            metrics.api_calls_per_min,
            metrics.avg_api_latency_ms,
            metrics.errors_count,
            uptime,
        )
    
    def get_summary(self) -> dict:
        """Получить сводку метрик для дашборда."""
        metrics = self.get_metrics()
        uptime = self.get_uptime_seconds()
        
        return {
            "total_bars": metrics.total_bars_processed,
            "avg_bar_time": round(metrics.avg_bar_process_time, 3),
            "max_bar_time": round(metrics.max_bar_process_time, 3),
            "memory_mb": round(metrics.memory_usage_mb, 1),
            "cpu_percent": round(metrics.cpu_percent, 1),
            "api_calls_per_min": int(metrics.api_calls_per_min),
            "avg_api_latency_ms": round(metrics.avg_api_latency_ms, 1),
            "errors": metrics.errors_count,
            "uptime_hours": round(uptime / 3600, 2),
        }


class TimingContext:
    """Контекстный менеджер для измерения времени выполнения."""
    
    def __init__(self, monitor: PerformanceMonitor, metric_type: str = "bar"):
        """
        Args:
            monitor: экземпляр PerformanceMonitor
            metric_type: тип метрики ("bar" или "api")
        """
        self.monitor = monitor
        self.metric_type = metric_type
        self.start_time: Optional[float] = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is None:
            return
        
        duration = time.time() - self.start_time
        
        if self.metric_type == "bar":
            self.monitor.record_bar_processing(duration)
        elif self.metric_type == "api":
            self.monitor.record_api_call(duration * 1000)  # в миллисекундах
        
        if exc_type is not None:
            self.monitor.record_error()
