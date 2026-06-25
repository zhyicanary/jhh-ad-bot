#!/usr/bin/env bash
set -euo pipefail

echo "========================================"
echo " jhh-ad-bot 打包脚本"
echo "========================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYINSTALLER="$VENV_DIR/bin/pyinstaller"

# 检查依赖
echo "[1/3] 检查依赖..."
if [ ! -f "$PYINSTALLER" ]; then
    uv pip install pyinstaller
fi
uv pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# 清理旧构建
echo "[2/3] 清理旧构建..."
rm -rf "$SCRIPT_DIR/dist" "$SCRIPT_DIR/build"

# 打包
echo "[3/3] 正在打包..."
# 注意: Linux/macOS 用 : 作为路径分隔符，Windows 用 ;
"$PYINSTALLER" --onefile \
    --name "jhh-ad-bot" \
    --add-data "config.yaml:." \
    --add-data "templates:templates" \
    --collect-all cv2 \
    --hidden-import pygetwindow \
    main.py && {
    echo ""
    echo "========================================"
    echo "  ✅ 打包成功！"
    echo "  输出: dist/jhh-ad-bot"
    echo ""
    echo "  📝 使用说明："
    echo "    将 config.yaml 和 templates/ 放在"
    echo "    与可执行文件相同的目录下"
    echo "========================================"
} || {
    echo ""
    echo "========================================"
    echo "  ❌ 打包失败，请检查上方错误信息"
    echo "========================================"
    exit 1
}
