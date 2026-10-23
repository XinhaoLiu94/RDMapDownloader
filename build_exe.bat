@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo 正在安装 PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo PyInstaller 安装失败。
        pause
        exit /b 1
    )
)

python -m PyInstaller --noconfirm --clean --windowed ^
    --name RDMapDownloader ^
    --icon "assets\icon.ico" ^
    --add-data "assets;assets" ^
    main.py

if errorlevel 1 (
    echo 打包失败。
    pause
    exit /b 1
)

echo.
echo 打包完成：dist\RDMapDownloader\RDMapDownloader.exe
pause
