@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Создаю виртуальное окружение...
  python -m venv .venv || (echo Нужен Python 3.10+ & pause & exit /b 1)
  .venv\Scripts\python -m pip install --disable-pip-version-check -q -r requirements.txt || (pause & exit /b 1)
)
echo Mnemo: http://127.0.0.1:8765
start "" http://127.0.0.1:8765
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
pause
