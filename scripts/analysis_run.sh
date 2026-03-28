#!/bin/bash
# Threads自動運用 - 分析バッチ実行スクリプト
# cron: 0 11 * * * /path/to/threads-auto/scripts/analysis_run.sh

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
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/analysis_$TODAY.log"
}

log "=== Analysis batch started ==="

# 1. フェッチャー: 24h前投稿のメトリクス取得
log "Step 1: Fetcher"
cd "$AGENTS_DIR" && "$PYTHON" fetcher.py

# 2. アナリスト: メトリクス分析・フィードバック生成
log "Step 2: Analyst"
cd "$AGENTS_DIR" && "$PYTHON" analyst.py

# 3. スーパーバイザー: 健康チェック・日次レポート
log "Step 3: Supervisor check"
cd "$AGENTS_DIR" && "$PYTHON" supervisor.py --check

log "=== Analysis batch completed ==="
