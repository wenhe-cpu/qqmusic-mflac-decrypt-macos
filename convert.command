#!/bin/bash
# 双击即可运行（窗口结束后会停留，不会闪退）
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
export KEEP_OPEN=1
exec "$SCRIPT_DIR/convert.sh" "$@"
