#!/bin/bash
# Synology 任務排程器每天呼叫：OAuth / Data API v3 版 daily_pick（免 cookie）。
# 前置：先在任一機器跑過 `python -m ytm.oauth` 完成授權，token 存在 data/oauth.json。
# data/ 內含 oauth.json + oauth_client.json，OAuth 自動 refresh，不需 browser.json。

set -e

IMAGE="ytm-tools:latest"
PROJECT_DIR="/volume1/docker/ytm-tools"    # ← 專案根目錄（含 ytm/、data/）

docker run --rm \
  -v "$PROJECT_DIR/ytm":/app/ytm \
  -v "$PROJECT_DIR/data":/app/data \
  "$IMAGE" \
  python -m ytm.daily_pick --count 20
