@echo off
rem Start the hub (web app + machine hub) from the .venv that setup.bat created.
rem
rem   start.bat                  http://localhost:8000
rem   set PORT=9000 ^& start.bat  another port
rem
rem The first start prints a one-time setup code in this window: open the web app and enter it to create the admin
rem account (with demo data, sign in as admin / autopilot-demo instead). Stop with Ctrl+C.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  exit /b 1
)
if not defined PORT set PORT=8000
.venv\Scripts\python.exe -m aiauto server --port %PORT% %*
