@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo [*] Creating virtual environment...
  python -m venv .venv || goto :err
)
rem Re-install only when requirements.txt changed since the last successful install
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>&1
if errorlevel 1 (
  echo [*] Installing / updating Python packages...
  .venv\Scripts\python -m pip install --no-cache-dir -r requirements.txt || goto :err
  copy /y requirements.txt .venv\installed-requirements.txt >nul
)
if not exist .env copy .env.example .env >nul
echo [*] Open http://127.0.0.1:8000 in your browser
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :eof
:err
echo [!] Setup failed. See README.md
pause
