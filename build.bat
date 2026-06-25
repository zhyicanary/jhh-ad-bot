@echo off
chcp 65001 >nul
echo ========================================
echo  jhh-ad-bot Windows 打包脚本
echo ========================================
echo.

:: 安装构建依赖（如已安装可跳过）
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
    --hidden-import pygetwindow ^
    main.py

echo.
if %errorlevel% equ 0 (
    echo ========================================
    echo  ✅ 打包成功！
    echo  输出: dist\jhh-ad-bot.exe
    echo.
    echo  📝 使用说明：
    echo   1. 将 config.yaml 和 templates\ 放在
    echo      与 exe 相同的目录下
    echo   2. 双击 jhh-ad-bot.exe 运行
    echo   3. 如需调试，先在 cmd 中运行查看错误:
    echo      dist\jhh-ad-bot.exe
    echo ========================================
) else (
    echo ========================================
    echo  ❌ 打包失败，请检查上方错误信息
    echo ========================================
)

pause
