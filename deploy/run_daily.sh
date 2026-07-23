#!/bin/bash
# 由 Synology 任務排程器每天呼叫，在 Docker 容器內跑 ytm-tools 的 daily_pick。
#
# 前置：
#   1. 已 build image：  docker build -t ytm-tools:latest /volume1/docker/ytm-tools
#   2. PC 端每天把 browser.json 刷新到下面 BROWSER_JSON 指的私人共享資料夾。
#
# 下面兩個路徑請改成你 NAS 上的實際路徑。

set -e

IMAGE="ytm-tools:latest"
PROJECT_DIR="/volume1/docker/ytm-tools"    # ← 專案根目錄（含 ytm/、data/）

# ytm/=程式、data/=pool.json + browser.json + state；browser.json 由 PC 每天刷新同步進 data/
docker run --rm \
  -v "$PROJECT_DIR/ytm":/app/ytm \
  -v "$PROJECT_DIR/data":/app/data \
  "$IMAGE" \
  python -m ytm.daily_pick --count 20
