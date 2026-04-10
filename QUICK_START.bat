@echo off
chcp 65001 >nul
title CatBoost Bot v2 - Quick Start

echo ================================================================
echo   BYBIT CATBOOST BOT v2 - БЫСТРЫЙ СТАРТ
echo ================================================================
echo.

echo [1/3] Проверка Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не установлен!
    echo.
    echo Установите Python 3.12+:
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo ✅ Python установлен

echo.
echo [2/3] Установка зависимостей...
pip install -r requirements.txt --quiet

echo.
echo [3/3] Проверка конфигурации...
if not exist "config.py" (
    echo ❌ Файл config.py не найден!
    pause
    exit /b 1
)

echo ✅ Конфигурация найдена
echo.
echo ================================================================
echo   ВАЖНО: НАСТРОЙТЕ API КЛЮЧИ!
echo ================================================================
echo.
echo Откройте config.py и вставьте ваши Bybit testnet ключи:
echo.
echo   API_KEY = "ваш_testnet_key"
echo   API_SECRET = "ваш_testnet_secret"
echo   TESTNET = True
echo.
echo Получить testnet ключи: https://testnet.bybit.com
echo.
echo ================================================================
echo.
echo После настройки API ключей запустите:
echo   python bybit_async_bot.py
echo.
echo Для дашборда (в другом терминале):
echo   streamlit run app.py
echo.
pause
