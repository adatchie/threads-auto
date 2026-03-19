#!/bin/bash
# Threads自動運用 - 毎朝バッチ実行スクリプト
# cron: 0 7 * * * /path/to/threads-auto/scripts/daily_run.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$PROJECT_DIR/agents"
LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date '+%Y-%m-%d')

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/batch_$TODAY.log"
}

log "=== Daily batch started ==="

# 1. スーパーバイザー: 前日のエラーチェック・レポート
log "Step 1: Supervisor check"
cd "$AGENTS_DIR" && python supervisor.py --check

# 2. フェッチャー: 24h前投稿のメトリクス取得
log "Step 2: Fetcher"
cd "$AGENTS_DIR" && python fetcher.py

# 3. アナリスト: メトリクス分析・フィードバック生成
log "Step 3: Analyst"
cd "$AGENTS_DIR" && python analyst.py

# 4. リサーチャー: 不足ノードへのネタ補充
log "Step 4: Researcher"
cd "$AGENTS_DIR" && python researcher.py

# 5. ライター: 投稿10本生成・キューへ追加
log "Step 5: Writer"
cd "$AGENTS_DIR" && python writer.py --count 10

log "=== Daily batch completed ==="
