@echo off
chcp 65001 >nul
title CatBoost Bot - Дашборд

echo ================================================================
echo   ЗАПУСК ДАШБОРДА CATBOOST BOT
echo ================================================================
echo.

cd /d "%~dp0"

echo Запуск Streamlit дашборда...
echo.
echo Дашборд откроется в браузере: http://localhost:8501
echo.
echo Для остановки нажмите Ctrl+C
echo.

streamlit run app.py

pause
