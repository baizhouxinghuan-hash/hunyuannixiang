@echo off
REM ==========================================================================
REM  One-click launcher: check environment + start the local OpenAI API.
REM  IMPORTANT: keep this file 100% ASCII.
REM  cmd.exe breaks on UTF-8 batch files that mix non-ASCII text with
REM  goto/call: it re-reads the file by byte offset and splits multibyte chars
REM  (observed: a Chinese word got cut in half and executed as a command).
REM  All Chinese UI and logic live in scripts\launcher.py instead.
REM ==========================================================================
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PYEXE=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"

"%PYEXE%" "%~dp0scripts\launcher.py" start %*
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
