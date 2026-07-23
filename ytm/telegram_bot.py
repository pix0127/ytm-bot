#!/usr/bin/env python3.12
"""Telegram bot：發訊息 → 依語意從 pool 挑歌 → 建 YouTube Music 歌單 → 回連結。

long-poll（不需對外開埠），只回應設定的 chat_id。挑歌用 LLM（見 llm_select），
建歌單走 Data API v3（免 cookie，見 dataapi）。

設定檔 data/bot_config.json（gitignored）：
  {
    "telegram_token": "...",           # @BotFather 給的
    "allowed_chat_id": 12345678,       # 只有這個 chat 能下指令；先設 null，對 bot 說話它會回你的 id
    "llm_url": "https://opencode.ai/zen/go/v1/chat/completions",  # OpenAI 相容端點
    "llm_api_key": "...",
    "model": "deepseek-v4-flash",
    "count_default": 20
  }

用法: python3 -m ytm.telegram_bot
"""
import json
import os
import sys
import time

import requests

from .config import POOL_FILE, BOT_CONFIG_FILE, BOT_STATE
from .blocklist import load_blocked_ids
from . import agent_select, dataapi

API = "https://api.telegram.org/bot{token}/{method}"


def _cfg() -> dict:
    if not os.path.exists(BOT_CONFIG_FILE):
        sys.exit(f"找不到 {BOT_CONFIG_FILE}，請依 docstring 建立設定檔")
    return json.load(open(BOT_CONFIG_FILE))


def _pool() -> list[dict]:
    return json.load(open(POOL_FILE)).get("songs", [])


def _bot_state() -> dict:
    if os.path.exists(BOT_STATE):
        return json.load(open(BOT_STATE))
    return {}


def _save_bot_state(state: dict):
    with open(BOT_STATE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _send(token: str, chat_id: int, text: str):
    requests.post(API.format(token=token, method="sendMessage"),
                  json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False})


def _wanted_count(msg: str, default: int) -> int:
    import re
    m = re.search(r"(\d{1,2})\s*(首|songs?|首歌)?", msg)
    n = int(m.group(1)) if m else default
    return max(1, min(n, 50))


def handle(cfg: dict, chat_id: int, text: str):
    token = cfg["telegram_token"]
    count = _wanted_count(text, cfg.get("count_default", 20))
    _send(token, chat_id, f"🤖 agent 依「{text}」查 YTM 電台/搜尋選 {count} 首中…(約 1–3 分鐘)")
    try:
        picks = agent_select.select(text, _pool(), count, cfg)
    except Exception as e:
        _send(token, chat_id, f"⚠️ 選曲失敗：{e}")
        return
    if not picks:
        _send(token, chat_id, "找不到符合的歌，換個說法試試(例如指定年份/OP/歌手/氛圍)。")
        return
    title = f"🤖 Agent 歌單 — {text[:70]}"
    try:
        pid = _bot_state().get("playlist_id")
        res = dataapi.upsert_playlist(pid, title, [s["video_id"] for s in picks],
                                      description=f"由 Telegram 依「{text}」挑選(每次覆蓋)",
                                      skip=load_blocked_ids())
        _save_bot_state({"playlist_id": res["playlist_id"]})
    except Exception as e:
        _send(token, chat_id, f"⚠️ 建歌單失敗：{e}")
        return
    lines = "\n".join(f"{i}. {s.get('title','?')} — {s.get('artist','') or '?'}"
                      for i, s in enumerate(picks, 1))
    _send(token, chat_id, f"✅ 已更新歌單({res['added']} 首)\n{res['url']}\n\n{lines}")


def main():
    cfg = _cfg()
    token = cfg["telegram_token"]
    allowed = cfg.get("allowed_chat_id")
    offset = None
    print("bot 啟動，long-poll 中… (Ctrl-C 停止)")
    while True:
        try:
            r = requests.get(API.format(token=token, method="getUpdates"),
                             params={"timeout": 50, "offset": offset}, timeout=60)
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                if not chat_id or not text:
                    continue
                if not allowed:  # 尚未設定 → 回報 chat_id 供填入
                    _send(token, chat_id, f"你的 chat_id 是 {chat_id}，填進 bot_config.json 的 allowed_chat_id 後重啟。")
                    continue
                if chat_id != allowed:
                    continue  # 非授權者，忽略
                handle(cfg, chat_id, text)
        except Exception as e:
            print("loop error:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
