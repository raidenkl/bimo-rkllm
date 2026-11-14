#!/usr/bin/env bash
# 把 RKLLM 模型与运行时库推到 RK3588 板卡
#
# 用法:
#   BOARD_HOST=root@192.168.1.50 BOARD_DIR=/data/bimo ./push_to_board.sh
#   BOARD_HOST=root@192.168.1.50 ./push_to_board.sh --adb     # 走 adb
#
# 依赖: rsync 或 scp（ssh 方式）；adb（--adb 方式）

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
MODEL="$ROOT/out/Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm"
LIB="$HERE/librkllmrt.so"
DEMO="$HERE/rkllm_api_demo"
TAR="$HERE/bimo_deploy.tar.gz"

BOARD_HOST="${BOARD_HOST:-}"
BOARD_DIR="${BOARD_DIR:-/data/bimo}"
MODE="ssh"
[ "${1:-}" = "--adb" ] && MODE="adb"

[ -f "$MODEL" ] || { echo "ERROR: 模型不存在: $MODEL"; exit 1; }
[ -f "$LIB" ]   || { echo "ERROR: 运行时库不存在: $LIB"; exit 1; }

# 只打包小文件；模型单独传（支持断点续传）
tar czf "$TAR" -C "$HERE" librkllmrt.so rkllm_api_demo
echo "payload: $(du -h "$TAR" | cut -f1)  model: $(du -h "$MODEL" | cut -f1)"

if [ "$MODE" = "adb" ]; then
    echo "== adb 方式 =="
    adb devices
    adb shell "mkdir -p $BOARD_DIR/lib"
    adb push "$TAR" "$BOARD_DIR/"
    adb push "$MODEL" "$BOARD_DIR/"
    adb shell "cd $BOARD_DIR && tar xzf bimo_deploy.tar.gz && cp librkllmrt.so lib/ && rm -f bimo_deploy.tar.gz"
    echo "done -> $BOARD_DIR"
    exit 0
fi

[ -n "$BOARD_HOST" ] || { echo "ERROR: 请设置 BOARD_HOST，例如 BOARD_HOST=root@192.168.1.50"; exit 1; }

echo "== ssh/rsync 方式 -> $BOARD_HOST:$BOARD_DIR =="
ssh "$BOARD_HOST" "mkdir -p $BOARD_DIR/lib"

# rsync 支持断点续传（8GB 模型建议务必用 rsync）
if command -v rsync >/dev/null 2>&1; then
    rsync -avP "$TAR"   "$BOARD_HOST:$BOARD_DIR/"
    rsync -avP --partial "$MODEL" "$BOARD_HOST:$BOARD_DIR/"
else
    scp "$TAR" "$BOARD_HOST:$BOARD_DIR/"
    scp "$MODEL" "$BOARD_HOST:$BOARD_DIR/"
fi

ssh "$BOARD_HOST" "cd $BOARD_DIR && tar xzf bimo_deploy.tar.gz && cp librkllmrt.so lib/ && rm -f bimo_deploy.tar.gz && ls -lh ."

echo
echo "下一步（在板上执行）:"
echo "  cd $BOARD_DIR && bash rkllm_api_demo/build_on_board.sh"
echo "  详见 smoke_test.md"
