@echo off
REM ---------------------------------------------------------------------------
REM AI Studio OpenAI-Compatible Proxy (Windows)
REM OpenAI Base URL: http://127.0.0.1:1010/v1
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] 未发现 .venv，将直接使用系统 Python。
)

set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

echo [*] 启动 AI Studio OpenAI Proxy ...
%PY% run.py %*
endlocal
