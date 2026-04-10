@echo off
chcp 65001 >nul
title Bybit CatBoost v2 - Backtest
cd /d "%~dp0"

echo ========================================
echo   Backtest на реальных данных
echo ========================================
echo.
echo Запускаем бэктест для всех 30 пар...
echo (последние 1000 баров с Bybit)
echo.

py -3.12 backtester.py

echo.
pause
