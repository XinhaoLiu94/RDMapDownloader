@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 使用 build_exe.bat 3d 时包含可选的三维组件。
if /i "%~1"=="3d" (
    python -m pip install -r requirements-3d.txt
    if errorlevel 1 exit /b 1
)

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
