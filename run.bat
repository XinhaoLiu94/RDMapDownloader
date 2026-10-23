@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import PyQt5, requests" >nul 2>nul
if errorlevel 1 (
    echo 正在安装依赖...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo 依赖安装失败，请检查 Python 与网络环境。
        pause
        exit /b 1
    )
)
python main.py
if errorlevel 1 pause
