@echo off
setlocal
cd /d "%~dp0"
title Mnemo
if not defined MNEMO_PORT set "MNEMO_PORT=8765"
set "VPY=.venv\Scripts\python.exe"

rem --- find Python 3.10+ (the "py" launcher first, then "python" from PATH)
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if defined PY goto :venv
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
if defined PY goto :venv
if exist "%VPY%" goto :deps
goto :nopython

:venv
if exist "%VPY%" goto :deps
echo [Mnemo] First run: creating a virtual environment...
%PY% -m venv .venv
if errorlevel 1 goto :venverror

:deps
"%VPY%" -c "import fastapi, uvicorn, httpx, yaml, numpy" >nul 2>nul
if not errorlevel 1 goto :run
echo [Mnemo] Installing dependencies, this takes about a minute...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :piperror

:run
echo.
echo [Mnemo] Running at http://127.0.0.1:%MNEMO_PORT%  -  close this window to stop.
echo.
if not defined MNEMO_NO_BROWSER start "" "http://127.0.0.1:%MNEMO_PORT%"
"%VPY%" -m uvicorn backend.main:app --host 127.0.0.1 --port %MNEMO_PORT%
echo.
echo [Mnemo] Server stopped.
pause
exit /b 0

:nopython
echo [Mnemo] Python 3.10 or newer was not found.
echo         Install it from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" in the installer, then run this file again.
pause
exit /b 1

:venverror
echo [Mnemo] Could not create the virtual environment in:
echo         %CD%\.venv
echo         Make sure the folder is writable and not inside a synced/protected location.
pause
exit /b 1

:piperror
echo [Mnemo] Could not install dependencies. Check your internet connection or proxy,
echo         then run this file again. To start from scratch, delete the .venv folder.
pause
exit /b 1
