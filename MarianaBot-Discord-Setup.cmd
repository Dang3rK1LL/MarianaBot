@echo off
title MarianaBot Discord token setup
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\discord-setup-windows.ps1"
if errorlevel 1 pause
