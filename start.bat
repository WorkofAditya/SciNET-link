@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)

if not exist "storage" mkdir storage

echo.
echo ========================================
echo          SCINET LINK // CORE
 echo ========================================
echo.
echo Open on this PC:  http://127.0.0.1:8000
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do echo Open on phone:   http://%%a:8000
 echo.
echo Press Ctrl+C to stop SciNET Link.
echo.
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
