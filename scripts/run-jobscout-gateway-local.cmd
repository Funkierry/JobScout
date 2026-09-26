@echo off
setlocal
for %%I in ("%~dp0..") do set "DEER_FLOW_PROJECT_ROOT=%%~fI"
set "PYTHONPATH=."
if not exist "%DEER_FLOW_PROJECT_ROOT%\logs" mkdir "%DEER_FLOW_PROJECT_ROOT%\logs"
cd /d "%DEER_FLOW_PROJECT_ROOT%\backend"
"%DEER_FLOW_PROJECT_ROOT%\backend\.venv\Scripts\python.exe" -m uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001 1>>"%DEER_FLOW_PROJECT_ROOT%\logs\jobscout-gateway.stdout.log" 2>>"%DEER_FLOW_PROJECT_ROOT%\logs\jobscout-gateway.stderr.log"
