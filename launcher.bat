cd "C:\Users\slepk\OneDrive\Рабочий стол\Новая папка (20)"

@'
@echo off
REM === Crypto Scanner: Live + Edge Test ===
cd /d "C:\Users\slepk\OneDrive\Рабочий стол\Новая папка (20)"

REM Активируем виртуальное окружение
call .venv\Scripts\activate.bat

echo.
echo === Запуск LIVE-сканера (Streamlit) ===
echo Открой в браузере: http://localhost:8501
echo Закрой это окно Streamlit (Ctrl+C), когда захочешь запустить бэктест.
echo.

REM Запуск сканера в этой же консоли
streamlit run app.py

echo.
echo === LIVE-сканер остановлен. Запускаю edge_tester.py ===
python edge_tester.py

echo.
echo Готово. Отчёт по edge выведен выше, детали сделок в edge_trades.csv
echo Нажми любую клавишу, чтобы закрыть окно.
pause >nul
'@ | Set-Content -Path ".\run_scanner_and_edge.bat" -Encoding OEM