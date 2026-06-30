@echo off
chcp 65001 >nul
setlocal
set TESS_URL=https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe
set TESSDATA_URL=https://github.com/tesseract-ocr/tessdata/raw/main/chi_sim.traineddata

echo ========================================
echo  jhh-ad-bot Windows 打包脚本
echo ========================================
echo.

:: 安装构建依赖
echo [1/5] 检查 PyInstaller...
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo 未安装，正在安装 PyInstaller...
    pip install pyinstaller
) else (
    echo 已安装，继续...
)

:: 检查/下载 Tesseract 便携版
echo [2/5] 检查 Tesseract OCR...
if not exist "tesseract_portable\tesseract.exe" (
    echo 下载 Tesseract 安装包...
    powershell -Command "Invoke-WebRequest -Uri '%TESS_URL%' -OutFile '%TEMP%\tesseract_setup.exe'"
    echo 静默安装 Tesseract...
    start /wait "" "%TEMP%\tesseract_setup.exe" /S
    echo 复制为便携版...
    if exist "tesseract_portable" rmdir /s /q "tesseract_portable"
    mkdir tesseract_portable
    xcopy /E /I "C:\Program Files\Tesseract-OCR\*" "tesseract_portable\"
    echo 下载中文语言包...
    powershell -Command "Invoke-WebRequest -Uri '%TESSDATA_URL%' -OutFile 'tesseract_portable\tessdata\chi_sim.traineddata'"
) else (
    echo Tesseract 便携版已存在，跳过安装
)

:: 清理旧构建
echo [3/5] 清理旧构建...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

:: 打包
echo [4/5] 正在打包...
pyinstaller --onefile ^
    --name "jhh-ad-bot" ^
    --add-data "config.yaml;." ^
    --add-data "templates;templates" ^
    --add-data "tesseract_portable;tesseract_portable" ^
    --collect-all cv2 ^
    --collect-all pyautogui ^
    --hidden-import pygetwindow ^
    --hidden-import pytesseract ^
    --hidden-import core.action ^
    --hidden-import core.capture ^
    --hidden-import core.vision ^
    --hidden-import core.engine ^
    main.py

echo [5/5] 打包完成
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
endlocal
