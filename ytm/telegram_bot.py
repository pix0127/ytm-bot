#!/usr/bin/env python3.12
"""Telegram bot(互動式):用指令 + 按鈕從 pool / YTM 選歌 → 建歌單 → 回連結。

指令:
  /rand  → 跳按鈕選數量 → 純隨機(最快,免 AI)
  /pool  → 跳按鈕選 年份 → OP/ED → 數量 → 篩選(免 AI)
  /agent → 追問描述(回覆一句)→ AI 依氛圍/相似找歌(會查 YTM 電台,較慢)
  /help  → 說明
也可直接帶參數快速執行:/rand 30、/pool 2024 OP 15、/agent 放鬆的。
沒有指令的純文字 → 回錯誤提示(agent 需用 /agent 或回覆追問)。

long-poll(不需對外開埠),只回應設定的 chat_id。建歌單走 Data API v3(免 cookie)。

設定檔 data/bot_config.json(gitignored):
  {"telegram_token":"...","allowed_chat_id":123,
   "llm_url":"https://opencode.ai/zen/go/v1/chat/completions",
   "llm_api_key":"...","model":"deepseek-v4-flash","count_default":20}
用法: python3 -m ytm.telegram_bot
"""
import json
import os
import random
import re
import time

import requests

from .config import POOL_FILE, BOT_CONFIG_FILE, BOT_STATE
from .blocklist import load_blocked_ids
from . import agent_select, dataapi, llm_select

API = "https://api.telegram.org/bot{token}/{method}"
COUNTS = [10, 20, 30, 50]
AGENT_PROMPT = "🎧 想要什麼樣的歌單?請「回覆」這則訊息描述(例:放鬆的、像 YOASOBI、適合讀書)"
HELP = (
    "🎵 用這些指令(按 / 或選單,選了會跳按鈕):\n"
    "/rand — 隨機來一批(最快)\n"
    "/pool — 挑指定年份、片頭(OP)或片尾(ED)的歌\n"
    "/agent — 用 AI 依心情/風格找歌(較慢,約 1–3 分)\n"
    "/help — 這個說明\n\n"
    "老手也可直接打:/rand 30、/pool 2024 OP 15、/agent 放鬆的"
)


def _cfg() -> dict:
    if not os.path.exists(BOT_CONFIG_FILE):
        raise SystemExit(f"找不到 {BOT_CONFIG_FILE}")
    return json.load(open(BOT_CONFIG_FILE))


def _pool() -> list[dict]:
    return json.load(open(POOL_FILE)).get("songs", [])


def _bot_state() -> dict:
    return json.load(open(BOT_STATE)) if os.path.exists(BOT_STATE) else {}


def _save_bot_state(state: dict):
    with open(BOT_STATE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─── Telegram API helpers ─────────────────────────────────────────

def _send(token, chat_id, text, markup=None):
    p = {"chat_id": chat_id, "text": text, "disable_web_page_preview": False}
    if markup:
        p["reply_markup"] = markup
    requests.post(API.format(token=token, method="sendMessage"), json=p, timeout=20)


def _edit(token, chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text}
    if markup:
        p["reply_markup"] = markup
    requests.post(API.format(token=token, method="editMessageText"), json=p, timeout=20)


def _answer_cb(token, cb_id, text=None):
    p = {"callback_query_id": cb_id}
    if text:
        p["text"] = text
    requests.post(API.format(token=token, method="answerCallbackQuery"), json=p, timeout=20)


def _kb(rows):
    """rows: list[ list[ (text, callback_data) ] ] → inline keyboard markup。"""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for (t, d) in row] for row in rows]}


def _wanted_count(msg, default):
    m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", msg)  # 1~2 位數,避開年份
    return max(1, min(int(m.group(1)), 50)) if m else default


def _pool_years(pool):
    ys = {str(s.get("season", "")).split(" ")[0] for s in pool if s.get("video_id")}
    return sorted((y for y in ys if y.isdigit()), reverse=True)


# ─── 選曲 + 發佈 ──────────────────────────────────────────────────

def _publish(token, chat_id, title, picks, note):
    if not picks:
        _send(token, chat_id, "找不到符合的歌,換個條件試試。")
        return
    try:
        pid = _bot_state().get("playlist_id")
        res = dataapi.upsert_playlist(pid, title, [s["video_id"] for s in picks],
                                      description=note, skip=load_blocked_ids())
        _save_bot_state({"playlist_id": res["playlist_id"]})
    except Exception as e:
        _send(token, chat_id, f"⚠️ 建歌單失敗:{e}")
        return
    lines = "\n".join(f"{i}. {s.get('title','?')} — {s.get('artist','') or '?'}"
                      for i, s in enumerate(picks, 1))
    _send(token, chat_id, f"✅ 已更新歌單({res['added']} 首)\n{res['url']}\n\n{lines}")


def _do_rand(token, chat_id, pool, count):
    cand = [s for s in pool if s.get("video_id")]
    picks = random.sample(cand, min(count, len(cand))) if cand else []
    _publish(token, chat_id, f"🎲 隨機 {len(picks)} 首", picks, "Telegram /rand")


def _do_pool(token, chat_id, pool, year, typ, count):
    cand = [s for s in pool if s.get("video_id")]
    if year != "all":
        cand = [s for s in cand if str(s.get("season", "")).split(" ")[0] == year]
    if typ != "all":
        cand = [s for s in cand if (s.get("type") or "").upper() == typ]
    picks = random.sample(cand, min(count, len(cand))) if cand else []
    label = f"{'全年' if year == 'all' else year} {'' if typ == 'all' else typ}".strip()
    _publish(token, chat_id, f"🎯 {label} {len(picks)}首", picks, f"Telegram /pool {label}")


def _run_agent(cfg, chat_id, query):
    token = cfg["telegram_token"]
    count = _wanted_count(query, cfg.get("count_default", 20))
    _send(token, chat_id, f"🤖 agent 依「{query}」找歌中…(約 1–3 分鐘)")
    try:
        picks = agent_select.select(query, _pool(), count, cfg)
    except Exception as e:
        _send(token, chat_id, f"⚠️ 選曲失敗:{e}")
        return
    _publish(token, chat_id, f"🤖 {query[:60]}", picks, f"Telegram agent:{query}")


# ─── 訊息 / 按鈕處理 ──────────────────────────────────────────────

def handle_message(cfg, chat_id, text, msg):
    token = cfg["telegram_token"]
    pool = _pool()
    low = text.lower().strip()
    rest = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""

    # 回覆 agent 追問 → 直接當描述跑 agent
    if (msg.get("reply_to_message") or {}).get("text", "").startswith(AGENT_PROMPT):
        _run_agent(cfg, chat_id, text)
        return

    if low.startswith("/help") or low in ("/start", "help"):
        _send(token, chat_id, HELP)
        return

    if low.startswith("/rand"):
        if re.search(r"(?<!\d)\d{1,2}(?!\d)", text):
            _do_rand(token, chat_id, pool, _wanted_count(text, 20))
        else:
            _send(token, chat_id, "🎲 隨機幾首?", _kb([[(str(n), f"r:{n}") for n in COUNTS]]))
        return

    if low.startswith("/pool"):
        if rest:  # 帶參數 → 直接
            cand = llm_select._prefilter(rest, pool)
            picks = random.sample(cand, min(_wanted_count(text, 20), len(cand))) if cand else []
            _publish(token, chat_id, f"🎯 {rest[:50]}", picks, f"Telegram /pool {rest}")
        else:     # 互動:先選年份
            ys = _pool_years(pool)[:8]
            rows = [[(y, f"p:y:{y}") for y in ys[i:i + 4]] for i in range(0, len(ys), 4)]
            rows.append([("不限年份", "p:y:all")])
            _send(token, chat_id, "🎯 要哪一年的動畫歌?", _kb(rows))
        return

    if low.startswith(("/agent", "/vibe")):
        if rest:
            _run_agent(cfg, chat_id, rest)
        else:
            _send(token, chat_id, AGENT_PROMPT, {"force_reply": True,
                                                 "input_field_placeholder": "例:放鬆的、像 YOASOBI"})
        return

    # 沒有指令的純文字 → 錯誤提示
    _send(token, chat_id, "❓ 請用指令(按 / 或選單)。\n\n" + HELP)


def handle_callback(cfg, chat_id, msg_id, data, cb_id):
    token = cfg["telegram_token"]
    _answer_cb(token, cb_id)
    pool = _pool()
    p = data.split(":")
    if p[0] == "r":                                   # r:N → 隨機
        _edit(token, chat_id, msg_id, f"🎲 隨機 {p[1]} 首,建立中…")
        _do_rand(token, chat_id, pool, int(p[1]))
    elif p[0] == "p" and p[1] == "y":                 # p:y:<Y> → 選片頭/片尾
        y = p[2]
        _edit(token, chat_id, msg_id, f"{'不限年份' if y == 'all' else y + ' 年'} → 要片頭還是片尾?",
              _kb([[("片頭 OP", f"p:t:{y}:OP"), ("片尾 ED", f"p:t:{y}:ED"), ("都要", f"p:t:{y}:all")]]))
    elif p[0] == "p" and p[1] == "t":                 # p:t:<Y>:<T> → 選數量
        y, t = p[2], p[3]
        tname = {"OP": "片頭", "ED": "片尾", "all": "片頭+片尾"}.get(t, t)
        _edit(token, chat_id, msg_id, f"{'不限年份' if y == 'all' else y + ' 年'}・{tname} → 要幾首?",
              _kb([[(str(n), f"p:n:{y}:{t}:{n}") for n in COUNTS]]))
    elif p[0] == "p" and p[1] == "n":                 # p:n:<Y>:<T>:<N> → 建立
        y, t, n = p[2], p[3], int(p[4])
        _edit(token, chat_id, msg_id, f"🎯 {'全年' if y == 'all' else y}/{t} {n}首,建立中…")
        _do_pool(token, chat_id, pool, y, t, n)


def _set_commands(token):
    cmds = [
        {"command": "rand", "description": "隨機來一批(最快)"},
        {"command": "pool", "description": "挑年份・片頭(OP)/片尾(ED)"},
        {"command": "agent", "description": "用 AI 依心情/風格找歌(較慢)"},
        {"command": "help", "description": "使用說明"},
    ]
    try:
        requests.post(API.format(token=token, method="setMyCommands"), json={"commands": cmds}, timeout=15)
    except Exception as e:
        print("setMyCommands 失敗:", e)


def main():
    cfg = _cfg()
    token = cfg["telegram_token"]
    allowed = cfg.get("allowed_chat_id")
    _set_commands(token)
    offset = None
    print("bot 啟動,long-poll 中… (Ctrl-C 停止)", flush=True)
    while True:
        try:
            r = requests.get(API.format(token=token, method="getUpdates"),
                             params={"timeout": 50, "offset": offset,
                                     "allowed_updates": json.dumps(["message", "callback_query"])},
                             timeout=60)
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    cq = upd["callback_query"]
                    cid = ((cq.get("message") or {}).get("chat") or {}).get("id")
                    if allowed and cid == allowed:
                        handle_callback(cfg, cid, cq["message"]["message_id"], cq.get("data", ""), cq["id"])
                    else:
                        _answer_cb(token, cq["id"])
                    continue
                msg = upd.get("message") or {}
                cid = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                if not cid or not text:
                    continue
                if not allowed:
                    _send(token, cid, f"你的 chat_id 是 {cid},填進 bot_config.json 的 allowed_chat_id 後重啟。")
                    continue
                if cid != allowed:
                    continue
                handle_message(cfg, cid, text, msg)
        except Exception as e:
            print("loop error:", e, flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
