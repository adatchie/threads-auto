#!/bin/bash
# Threads自動運用 - 生成バッチ実行スクリプト
# cron: 0 14 * * * /path/to/threads-auto/scripts/generation_run.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$PROJECT_DIR/agents"
LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date '+%Y-%m-%d')

mkdir -p "$LOG_DIR"

PYTHON="${PYTHON:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null)}"
if [ -z "$PYTHON" ]; then
    echo "python3/python が見つかりません" >&2
    exit 1
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/generation_$TODAY.log"
}

log "=== Generation batch started ==="

# 1. リサーチャー: 不足ノードへのネタ補充
log "Step 1: Researcher"
cd "$AGENTS_DIR" && "$PYTHON" researcher.py

# 2. ライター: 投稿3本生成・キューへ追加
log "Step 2: Writer"
cd "$AGENTS_DIR" && "$PYTHON" writer.py --count 3

log "=== Generation batch completed ==="
