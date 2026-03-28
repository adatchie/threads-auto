#!/bin/bash
# Threads自動運用 - 手動フルバッチ実行スクリプト
# 旧 daily batch の代替。cron は analysis_run / generation_run に分割済み。

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date '+%Y-%m-%d')

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/batch_$TODAY.log"
}

log "=== Daily full pipeline started ==="

# 1. 分析バッチ
log "Step 1: Analysis batch"
bash "$SCRIPT_DIR/analysis_run.sh"

# 2. 生成バッチ
log "Step 2: Generation batch"
bash "$SCRIPT_DIR/generation_run.sh"

log "=== Daily full pipeline completed ==="
