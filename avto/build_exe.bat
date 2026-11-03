@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Сборка одного файла avto.exe, чтобы не ставить Python на компьютер-сервер.
rem Запускать на любом ПК с Python 3.11+ и интернетом. Результат: dist\avto.exe
if not exist ".venv\Scripts\python.exe" (py -3 -m venv .venv 2>nul || python -m venv .venv)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --name avto --add-data "app\static;app\static" --collect-submodules uvicorn run.py
echo.
echo Готово: dist\avto.exe. Скопируйте его вместе с start.bat в папку на компьютере-сервере.
pause
