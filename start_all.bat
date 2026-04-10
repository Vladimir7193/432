@echo off
chcp 65001 >nul
title Bybit CatBoost v2 - Start All
cd /d "%~dp0"

echo ========================================
echo   Bybit CatBoost v2 - Enhanced Bot
echo ========================================
echo.
echo  Новое в v2:
echo  - Мультитаймфрейм (5m/15m/1h/4h)
echo  - Динамический размер позиции
echo  - Защита от просадки (8%%)
echo  - Бэктест на реальных данных
echo  - Equity curve в дашборде
echo ========================================
echo.

echo Checking Python...
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.12 not found!
    pause
    exit /b 1
)

:: Проверяем наличие моделей
set MODELS_DIR=%~dp0models\pairs
set MODEL_COUNT=0
for %%f in ("%MODELS_DIR%\*_model.cbm") do set /a MODEL_COUNT+=1

if %MODEL_COUNT% LSS 30 (
    echo [INFO] Найдено моделей: %MODEL_COUNT% из 30
    echo [INFO] Запускаем предобучение всех 30 пар...
    echo.
    py -3.12 pretrain_all.py
    if errorlevel 1 (
        echo [ERROR] Ошибка предобучения!
        pause
        exit /b 1
    )
) else (
    echo [OK] Все 30 моделей найдены.
)

echo.
echo Starting async multi-pair trading bot...
start "Trading Bot v2" cmd /k "chcp 65001 >nul && cd /d "%~dp0" && py -3.12 bybit_async_bot.py"

ping 127.0.0.1 -n 3 >nul

echo Starting dashboard...
start "Dashboard v2" cmd /k "chcp 65001 >nul && cd /d "%~dp0" && py -3.12 -m streamlit run app.py"

echo.
echo ========================================
echo   All components started!
echo ========================================
echo.
echo Bot:       "Trading Bot v2"
echo Dashboard: http://localhost:8501
echo.
pause
