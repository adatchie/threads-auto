#!/bin/bash
# ローカル検証用のcronジョブのセットアップ
# GitHub Actions を主経路にする前提で、必要な場合だけ実行する

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$PROJECT_DIR/agents"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
if [ -z "$PYTHON" ]; then
    echo "python3/python が見つかりません"
    exit 1
fi

echo "Setting up cron jobs..."
echo "Project dir: $PROJECT_DIR"
echo "Python: $PYTHON"

# 既存のcron設定をバックアップ
crontab -l 2>/dev/null > /tmp/crontab_backup.txt
echo "Backup saved to /tmp/crontab_backup.txt"

# 新しいcron設定
CRON_JOBS="
# Threads自動運用
# 毎朝11時: 分析（メトリクス取得・分析・日次レポート）
0 11 * * * bash \"$PROJECT_DIR/scripts/analysis_run.sh\" >> \"$PROJECT_DIR/logs/cron_analysis.log\" 2>&1

# 毎日14時: 生成（同日内レビューを反映してリサーチ・ライター実行）
0 14 * * * bash \"$PROJECT_DIR/scripts/generation_run.sh\" >> \"$PROJECT_DIR/logs/cron_generation.log\" 2>&1

# 投稿スロット（1日3枠: 朝出勤前・昼休み・夜帰宅後）
0 8 * * * cd \"$AGENTS_DIR\" && \"$PYTHON\" poster.py >> \"$PROJECT_DIR/logs/cron_poster.log\" 2>&1
0 12 * * * cd \"$AGENTS_DIR\" && \"$PYTHON\" poster.py >> \"$PROJECT_DIR/logs/cron_poster.log\" 2>&1
0 20 * * * cd \"$AGENTS_DIR\" && \"$PYTHON\" poster.py >> \"$PROJECT_DIR/logs/cron_poster.log\" 2>&1
"

# 既存の設定 + 新設定をマージ
(crontab -l 2>/dev/null | grep -v "Threads自動運用" | grep -v "analysis_run.sh" | grep -v "generation_run.sh" | grep -v "daily_run.sh" | grep -v "poster.py"; echo "$CRON_JOBS") | crontab -

echo "Cron jobs set up:"
crontab -l | grep -A 20 "Threads"
