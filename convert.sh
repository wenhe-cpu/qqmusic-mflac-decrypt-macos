#!/bin/bash
#
# QQ 音乐加密文件批量解密（macOS）
#
# 用法：
#   ./convert.sh                          # 用默认输入/输出目录
#   ./convert.sh "输入目录" "输出目录"      # 自定义
#   ./convert.sh "输入目录" "输出目录" --verbose
#
# 所有路径都必须用双引号包起来 —— 本脚本内部已经全部加引号，
# 无论路径里有多少空格、括号、中文、emoji 都不会出错。
#
set -uo pipefail

# 定位脚本自身所在目录（兼容软链接、空格路径）
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
PY_SRC="$SCRIPT_DIR/qmc2dec.py"

# 默认目录
DEFAULT_IN="$HOME/Library/Containers/com.tencent.QQMusicMac/Data/Library/Application Support/QQMusicMac/iQmc"
DEFAULT_OUT="$HOME/Desktop/Decoded_Music"

IN_DIR="${1:-$DEFAULT_IN}"
OUT_DIR="${2:-$DEFAULT_OUT}"

# 找一个可用的 python3（优先 PATH，其次 Homebrew，最后系统自带）
PYTHON_BIN=""
for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done

echo "=============================================="
echo " QQ 音乐加密文件解密"
echo "=============================================="

if [ -z "$PYTHON_BIN" ]; then
    echo "✗ 没有找到 python3。"
    echo "  请先安装：brew install python   （或 xcode-select --install）"
    exit 1
fi

if [ ! -f "$PY_SRC" ]; then
    echo "✗ 找不到解密程序：$PY_SRC"
    exit 1
fi

if [ ! -d "$IN_DIR" ]; then
    echo "✗ 输入目录不存在："
    echo "  $IN_DIR"
    echo "  请确认 QQ 音乐已下载过歌曲，或把加密文件放到该目录。"
    exit 1
fi

echo "输入目录：$IN_DIR"
echo "输出目录：$OUT_DIR"
echo "Python  ：$PYTHON_BIN"
echo "----------------------------------------------"

# 传参时全部加引号，后两个参数原样透传
"$PYTHON_BIN" "$PY_SRC" "$IN_DIR" -o "$OUT_DIR" "${@:3}"
STATUS=$?

echo "----------------------------------------------"
if [ "$STATUS" -eq 0 ]; then
    echo "输出位置：$OUT_DIR"
else
    echo "有文件未能转换，请查看上面的提示。"
fi

# 双击 .command 运行时，窗口不要立刻关闭
if [ "${KEEP_OPEN:-0}" = "1" ]; then
    echo
    read -r -p "按回车键关闭窗口…" _
fi

exit "$STATUS"
