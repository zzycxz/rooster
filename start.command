#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PIDFILE=".rooster/guardian.pid"

# Resolve python command
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[Guardian] 错误: 找不到 Python，请先安装 Python 3.12+"
    read -rp "按回车退出..."
    exit 1
fi

# Check PID file
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null || true)

    if [ -z "$OLD_PID" ] || ! [[ "$OLD_PID" =~ ^[0-9]+$ ]]; then
        echo "[Guardian] 发现无效锁文件，正在清理..."
        rm -f "$PIDFILE"
    elif kill -0 "$OLD_PID" 2>/dev/null; then
        # Process is alive — ask user
        echo ""
        echo "[Guardian] 检测到已有 Guardian 进程在运行 (PID=$OLD_PID)"
        echo ""
        read -rp "是否终止该进程并重新启动？(y=终止并重启, n=退出): " answer
        case "$answer" in
            [Yy]*)
                echo "[Guardian] 正在终止进程 PID=$OLD_PID ..."
                kill "$OLD_PID" 2>/dev/null || true
                sleep 1
                # Force kill if still alive
                if kill -0 "$OLD_PID" 2>/dev/null; then
                    kill -9 "$OLD_PID" 2>/dev/null || true
                    sleep 1
                fi
                rm -f "$PIDFILE"
                ;;
            *)
                echo "[Guardian] 已取消，退出。"
                exit 0
                ;;
        esac
    else
        # Process is dead — clean up stale lock
        echo "[Guardian] 发现残留锁文件 (PID=$OLD_PID 已退出)，正在清理..."
        rm -f "$PIDFILE"
    fi
fi

echo ""
echo "[Guardian] 启动中..."
echo ""
"$PYTHON" guardian.py

echo ""
read -rp "按回车退出..."
