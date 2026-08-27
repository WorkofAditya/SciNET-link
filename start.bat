@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt --disable-pip-version-check

if not exist "storage" mkdir storage

echo.
echo ========================================
echo          SCINET LINK // CORE
echo ========================================
echo.
echo Web:       http://scinet.local:8000
echo FTP:       ftp://scinet.local:2121
echo.
echo If scinet.local is unavailable on your phone,
echo use the local IPv4 address printed by server.py.
echo.
echo Press Ctrl+C to stop SciNET Link.
echo.
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
