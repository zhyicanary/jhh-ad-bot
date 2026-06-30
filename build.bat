@echo off
chcp 65001 >nul
echo ========================================
echo  jhh-ad-bot Windows 打包脚本
echo ========================================
echo.

:: 安装构建依赖
echo [1/3] 检查 PyInstaller...
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo 未安装，正在安装 PyInstaller...
    pip install pyinstaller
) else (
    echo 已安装，继续...
)

:: 清理旧构建
echo [2/3] 清理旧构建...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

:: 打包
echo [3/3] 正在打包...
pyinstaller --onefile ^
    --name "jhh-ad-bot" ^
    --add-data "config.yaml;." ^
    --add-data "templates;templates" ^
    --collect-all cv2 ^
    --collect-all pyautogui ^
    --collect-all rapidocr_onnxruntime ^
    --collect-all yaml ^
    --hidden-import pygetwindow ^
    --hidden-import yaml ^
    --hidden-import core.action ^
    --hidden-import core.capture ^
    --hidden-import core.vision ^
    --hidden-import core.engine ^
    main.py

echo.
if %errorlevel% equ 0 (
    echo ========================================
    echo  ✅ 打包成功！
    echo  输出: dist\jhh-ad-bot.exe
    echo.
    echo  📝 使用说明：
    echo   1. 将 exe 放到一个空文件夹中
    echo   2. 在同目录放 config.yaml 和 templates\
    echo   3. 双击 jhh-ad-bot.exe 运行
    echo ========================================
) else (
    echo ========================================
    echo  ❌ 打包失败，请检查上方错误信息
    echo ========================================
)

pause
