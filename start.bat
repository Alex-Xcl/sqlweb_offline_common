@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

where python >nul 2>nul
if errorlevel 1 if not exist "%~dp0.venv\Scripts\python.exe" (
    echo 错误：找不到 python 命令
    echo 请先安装 Python 3.8+ 并勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo SQLWeb 正在启动，浏览器打开 http://127.0.0.1:8899
"%PY%" sqlweb.py %*
pause
