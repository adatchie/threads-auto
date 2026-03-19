#!/bin/bash
# cronジョブのセットアップ
# 実行: bash scripts/setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$PROJECT_DIR/agents"
PYTHON=$(which python3 || which python)

echo "Setting up cron jobs..."
echo "Project dir: $PROJECT_DIR"
echo "Python: $PYTHON"

# 既存のcron設定をバックアップ
crontab -l 2>/dev/null > /tmp/crontab_backup.txt
echo "Backup saved to /tmp/crontab_backup.txt"

# 新しいcron設定
CRON_JOBS="
# Threads自動運用
# 毎朝7時: バッチ（リサーチ〜ライター）
0 7 * * * cd $AGENTS_DIR && $PYTHON $(dirname $AGENTS_DIR)/scripts/daily_run.sh >> $PROJECT_DIR/logs/cron_batch.log 2>&1

# 投稿スロット（1日10枠）
0 8 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
0 10 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
0 12 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
0 14 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
0 16 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
0 18 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
0 20 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
30 21 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
30 23 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
30 0 * * * cd $AGENTS_DIR && $PYTHON poster.py >> $PROJECT_DIR/logs/cron_poster.log 2>&1
"

# 既存の設定 + 新設定をマージ
(crontab -l 2>/dev/null | grep -v "Threads自動運用" | grep -v "daily_run.sh" | grep -v "poster.py"; echo "$CRON_JOBS") | crontab -

echo "Cron jobs set up:"
crontab -l | grep -A 20 "Threads"
