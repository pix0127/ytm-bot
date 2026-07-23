#!/bin/bash
# 每日隨選歌單 wrapper — 用於 no_agent cronjob
DIR="$(cd "$(dirname "$0")/.." && pwd)"   # 專案根目錄（deploy/ 的上一層）
cd "$DIR" || exit 1

python3.12 -m ytm.daily_pick --count 20 2>&1
EXIT=$?

if [ $EXIT -eq 0 ]; then
    echo ""
    echo "✅ 今日隨選已更新"
    grep "^🔗\|^JSON:" | tail -1
fi

exit $EXIT
