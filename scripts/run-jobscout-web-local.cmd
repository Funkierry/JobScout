@echo off
setlocal
for %%I in ("%~dp0..") do set "DEER_FLOW_PROJECT_ROOT=%%~fI"
if not exist "%DEER_FLOW_PROJECT_ROOT%\logs" mkdir "%DEER_FLOW_PROJECT_ROOT%\logs"
cd /d "%DEER_FLOW_PROJECT_ROOT%\jobscout-web"
python -m http.server 5500 --bind 127.0.0.1 1>>"%DEER_FLOW_PROJECT_ROOT%\logs\jobscout-web.stdout.log" 2>>"%DEER_FLOW_PROJECT_ROOT%\logs\jobscout-web.stderr.log"
