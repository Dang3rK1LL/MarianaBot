@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The local MarianaBot Python environment is missing.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m marianabot.bootstrap --connect-server
if errorlevel 1 pause
