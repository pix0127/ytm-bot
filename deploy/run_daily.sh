#!/bin/bash
# Synology 任務排程器每天呼叫 daily_pick。
# 前置：data/ 裡要有有效的 browser.json（見 docs/SETUP.md）。


set -e

IMAGE="ytm-bot:latest"
PROJECT_DIR="/volume1/docker/ytm-bot"    # ← 專案根目錄（含 ytm/、data/）

docker run --rm \
  -v "$PROJECT_DIR/ytm":/app/ytm \
  -v "$PROJECT_DIR/data":/app/data \
  "$IMAGE" \
  python -m ytm.daily_pick --count 20
