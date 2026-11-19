@echo off
rem Connect this computer to a hub as an agent (run setup.bat first).
rem
rem   agent.bat --server http://HUB:8000 --token <token> [--name my-pc] [--slots 2]
rem
rem Or put the settings in agent.env (copy agent.env.example) and run agent.bat with no options; that is also what
rem the autostart task in docs\deployment.md runs. Stop with Ctrl+C. The agent reconnects by itself if the hub
rem restarts or the network drops.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  exit /b 1
)
if exist agent.env (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("agent.env") do set "%%a=%%b"
)
set NAMEARG=
if defined AGENT_NAME set NAMEARG=--name "%AGENT_NAME%"
.venv\Scripts\python.exe -m aiauto agent %NAMEARG% %*
endlocal
