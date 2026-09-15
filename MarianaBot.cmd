@echo off
setlocal
cd /d "%~dp0"
title MarianaBot
if not exist ".venv\Scripts\python.exe" (
    echo MarianaBot needs its one-time installation first.
    echo See README.md: python -m venv .venv, then install the project.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m marianabot chat %*
if errorlevel 1 pause
