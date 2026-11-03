@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Сайт отдела автоперевозок

if exist "avto.exe" (
  avto.exe
  pause
  exit /b
)

if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: создаю окружение и ставлю библиотеки. Нужен интернет, 1-2 минуты...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Не найден Python 3.11+. Установите его с python.org и отметьте "Add Python to PATH".
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
)

".venv\Scripts\python.exe" run.py
pause
