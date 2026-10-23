@echo off
REM ==========================================================================
REM  One-click: login (in the Chromium window) + capture the web protocol.
REM  IMPORTANT: keep this file 100% ASCII (see the start script for the reason).
REM ==========================================================================
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PYEXE=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"

"%PYEXE%" "%~dp0scripts\launcher.py" capture %*
if errorlevel 9009 goto :nopython
goto :end

:nopython
echo.
echo [X] Python not found. Install Python 3.11+ and tick "Add python.exe to PATH".
echo     Download: https://www.python.org/downloads/
echo.

:end
echo.
pause
