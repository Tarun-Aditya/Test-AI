@echo off
rem One-step setup on Windows.
rem
rem   setup.bat                install into .venv (Python packages + Playwright's Chromium)
rem   setup.bat --demo         the same, then fill the database with demo data (accounts, projects, real runs)
rem   setup.bat --all-browsers also Firefox and WebKit for Playwright
rem
rem Then start the hub with start.bat and open http://localhost:8000
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set DEMO=0
set BROWSERS=chromium
:args
if "%~1"=="" goto argsdone
if /i "%~1"=="--demo" (set DEMO=1& shift & goto args)
if /i "%~1"=="--all-browsers" (set "BROWSERS=chromium firefox webkit"& shift & goto args)
echo unknown option: %~1
exit /b 2
:argsdone

set PY=
where py >nul 2>nul && (py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=py -3")
if not defined PY (
  where python >nul 2>nul && (python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=python")
)
if not defined PY (
  echo Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH"^), then run setup.bat again.
  exit /b 1
)
for /f "delims=" %%v in ('%PY% --version') do echo ==^> %%v

if not exist .venv\Scripts\python.exe (
  echo ==^> Creating .venv
  %PY% -m venv .venv || exit /b 1
)
set VPY=.venv\Scripts\python.exe

echo ==^> Installing Python packages
%VPY% -m pip install --quiet --upgrade pip || exit /b 1
%VPY% -m pip install --quiet -r requirements.txt || exit /b 1

echo ==^> Installing Playwright browsers: %BROWSERS%
%VPY% -m playwright install %BROWSERS% || exit /b 1

if "%DEMO%"=="1" (
  echo ==^> Loading demo data ^(runs the example tests; a few minutes^)
  %VPY% -m aiauto demo || exit /b 1
)

echo.
echo Setup done.
echo   Start the hub:   start.bat            ^(then open http://localhost:8000^)
echo   Demo data:       setup.bat --demo     ^(or: .venv\Scripts\python -m aiauto demo^)
echo   Guide:           docs\getting-started.md
echo.
echo Selenium finds or downloads the driver for the Chrome / Edge / Firefox you have installed by itself.
endlocal
