#!/usr/bin/env python3.12
"""Telegram bot(互動式):用指令 + 按鈕從 pool / YTM 選歌 → 建歌單 → 回連結。

指令:
  /rand  → 跳按鈕選數量 → 純隨機(最快,免 AI)
  /pool  → 跳按鈕選 年份 → OP/ED → 數量 → 篩選(免 AI)
  /agent → 追問描述(回覆一句)→ AI 依氛圍/相似找歌(會查 YTM 電台,較慢)
  /update → 更新歌曲池（本季新番／全部歷史季／訂閱歌手／只重新解析）
  /cookie → 檢查 YTM cookie 是否還有效（失效時給重新擷取的按鈕）
  /help  → 說明
也可直接帶參數快速執行:/rand 30、/pool 2024 OP 15、/agent 放鬆的。
沒有指令的純文字 → 回錯誤提示(agent 需用 /agent 或回覆追問)。

long-poll(不需對外開埠),只回應設定的 chat_id。

設定檔 data/bot_config.json(gitignored):
  {"telegram_token":"...","allowed_chat_id":123,
   "llm_url":"https://opencode.ai/zen/go/v1/chat/completions",
   "llm_api_key":"...","model":"deepseek-v4-flash","count_default":20,
   "firefox_url":"http://nas:5800","firefox_profile":"/app/ff-profile"}
用法: python3 -m ytm.telegram_bot
"""
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import POOL_FILE, BOT_CONFIG_FILE, BOT_STATE, STATE_DIR
from .blocklist import load_blocked_ids
from . import agent_select, cookie, llm_select, playlist

API = "https://api.telegram.org/bot{token}/{method}"
COUNTS = [10, 20, 30, 50]
AGENT_PROMPT = "🎧 想要什麼樣的歌單?請「回覆」這則訊息描述(例:放鬆的、像 YOASOBI、適合讀書)"
HELP = (
    "🎵 用這些指令(按 / 或選單,選了會跳按鈕):\n"
    "/rand — 隨機來一批(最快)\n"
    "/pool — 挑指定年份、片頭(OP)或片尾(ED)的歌\n"
    "/agent — 用 AI 依心情/風格找歌(較慢,約 1–3 分)\n"
    "/update — 更新歌曲池(抓新番、訂閱歌手、重新解析)\n"
    "/cookie — 檢查 YTM 登入狀態\n"
    "/help — 這個說明\n\n"
    "老手也可直接打:/rand 30、/pool 2024 OP 15、/agent 放鬆的"
)


def _cfg() -> dict:
    if not os.path.exists(BOT_CONFIG_FILE):
        raise SystemExit(f"找不到 {BOT_CONFIG_FILE}")
    return json.load(open(BOT_CONFIG_FILE))


def _claim_chat(cfg: dict, chat_id: int) -> bool:
    """第一次有人對 bot 說話就把 allowed_chat_id 記下來(first-run pairing)。

    以前是回覆「你的 chat_id 是 X,填進設定檔後重啟」——但 bot 已經知道了,
    沒必要叫使用者手改檔案再重啟。只有知道 token 的人找得到這個 bot,
    所以「先來的人綁定」在這個情境可接受。
    """
    cfg["allowed_chat_id"] = chat_id
    try:
        on_disk = json.load(open(BOT_CONFIG_FILE))
        on_disk["allowed_chat_id"] = chat_id
        with open(BOT_CONFIG_FILE, "w") as f:
            json.dump(on_disk, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print("寫入 allowed_chat_id 失敗:", e, flush=True)
        return False


_TOKEN_RE = re.compile(r"\d{8,}:[A-Za-z0-9_-]{30,}")


def _redact(text, token: str = "") -> str:
    """把 bot token 從輸出中遮掉。

    Telegram 的 token 在 URL 裡(見 API 常數),所以 requests 的例外訊息必定含它。
    實測:2026-07-27 一次 5 小時的 DNS 異常,在容器 log 留下 1095 行含完整 token
    的訊息——docker logs 會一直留著,等於長期外洩。
    """
    s = str(text)
    if token:
        s = s.replace(token, "<TOKEN>")
    return _TOKEN_RE.sub("<TOKEN>", s)


_prep_pool = ThreadPoolExecutor(2)
_pool_cache: tuple[float, list[dict]] | None = None


NO_POOL_HINT = ("⚠️ 還沒有歌曲池。打 /update 選「全部歷史季」就會開始建立"
                "（十幾分鐘，會回報進度）。")


def _pool() -> list[dict]:
    """pool.json 數千首、近 1MB,每則訊息重 parse 太浪費;依 mtime 快取。"""
    global _pool_cache
    mt = os.path.getmtime(POOL_FILE)      # 檔案不存在 → FileNotFoundError,由呼叫端處理
    if _pool_cache is None or _pool_cache[0] != mt:
        _pool_cache = (mt, json.load(open(POOL_FILE)).get("songs", []))
    return _pool_cache[1]


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
    r = requests.post(API.format(token=token, method="sendMessage"), json=p, timeout=20)
    try:
        return r.json()["result"]["message_id"]
    except Exception:
        return None


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

def _prepare_playlist(title, note):
    """先把新歌單開好(刪舊+開新,約 2s)。不需要 picks,所以能跟選曲並行。"""
    return _prep_pool.submit(playlist.new_playlist, _bot_state().get("playlist_id"), title, note)


def _publish(token, chat_id, title, picks, note, prep=None):
    if not picks:
        _send(token, chat_id, "找不到符合的歌,換個條件試試。")
        return
    try:
        pid = prep.result() if prep else playlist.new_playlist(_bot_state().get("playlist_id"), title, note)
        _save_bot_state({"playlist_id": pid})
        res = playlist.fill_playlist(pid, [s["video_id"] for s in picks], skip=load_blocked_ids())
    except Exception as e:
        _send(token, chat_id, f"⚠️ 建歌單失敗:{_redact(e, token)}")
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
    mid = _send(token, chat_id, f"🤖 agent 依「{query}」找歌中…")
    title, note = f"🤖 {query[:60]}", f"Telegram agent:{query}"
    prep = _prepare_playlist(title, note)
    names = {"filter_pool": "篩選歌曲池", "radio": "查 YTM 電台", "search_ytm": "搜尋 YTM"}

    def on_step(step, action, n):
        if mid:
            _edit(token, chat_id, mid, f"🤖「{query}」\nstep {step}:{names.get(action, action)}…(候選 {n} 首)")

    try:
        picks = agent_select.select(query, _pool(), count, cfg, on_step=on_step)
    except Exception as e:
        _send(token, chat_id, f"⚠️ 選曲失敗:{_redact(e, token)}")
        return
    if mid:
        _edit(token, chat_id, mid, f"🤖「{query}」選出 {len(picks)} 首,建立歌單中…")
    _publish(token, chat_id, title, picks, note, prep=prep)


def _spawn(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True).start()


# ─── 更新歌曲池 ──────────────────────────────────────────────────

UPDATE_KINDS = {
    "season":  "本季新番",
    "all":     "全部歷史季（首次建池，十幾分鐘）",
    "artists": "訂閱歌手（需要登入）",
    "resolve": "只重新解析（補上缺的 video_id）",
}


def _do_update(cfg, chat_id, kind):
    """從 Telegram 更新歌曲池,不必 ssh 進去跑 docker exec。

    解析階段可能很久(首次建池數千首,每首要搜尋),所以放背景執行、定期編輯訊息回報進度。
    中斷了也沒關係:resolve 只挑沒有 video_id 的歌,重跑會從斷點續下去。
    """
    from . import collect, resolve_pool
    token = cfg["telegram_token"]
    mid = _send(token, chat_id, f"🔄 {UPDATE_KINDS[kind]}：開始…")

    def say(text):
        if mid:
            _edit(token, chat_id, mid, f"🔄 {UPDATE_KINDS[kind]}\n{text}")

    try:
        pool = collect.load_pool()
        before = len(pool["songs"])
        yt = None
        if kind in ("artists", "resolve"):
            from ytmusicapi import YTMusic
            from .config import AUTH_FILE
            yt = YTMusic(AUTH_FILE)

        if kind == "season":
            season, year = collect.get_current_season()
            say(f"抓 {year} {season}…")
            pool["songs"].extend(collect.collect_anime_themes(season, year))
        elif kind == "all":
            say("從 AnimeThemes 抓歷史各季，這段最久…")
            pool["songs"].extend(collect.collect_all_anime())
            say("補日文作品名…")
            collect.fill_anime_jp(pool)
        elif kind == "artists":
            say("讀訂閱清單…")
            songs = collect.collect_all_artists(yt)
            pool["songs"].extend(songs)
            pool["artists"] = sorted({s["artist"] for s in songs})

        collect.save_pool(pool)
        pool = collect.load_pool()          # 讀回去重後的結果
        pending = len([s for s in pool["songs"] if not s.get("video_id")])
        say(f"歌曲池 {before} → {len(pool['songs'])} 首；待解析 {pending} 首")

        if pending:
            if yt is None:
                from ytmusicapi import YTMusic
                from .config import AUTH_FILE
                yt = YTMusic(AUTH_FILE)
            say(f"解析 video_id（{pending} 首，每首約 1 秒）…")
            filled, dropped = resolve_pool.resolve_all(
                yt, pool["songs"],
                on_progress=lambda d, t, f, dr: say(f"解析 {d}/{t}（成功 {f}、解不到 {dr}）"))
            pool["songs"] = [s for s in pool["songs"] if not s.pop("_drop", False)]
            collect.save_pool(pool)
            tail = f"\n解析：成功 {filled} 首、解不到 {len(dropped)} 首（已移除）"
        else:
            tail = ""

        final = collect.load_pool()["songs"]
        usable = sum(1 for s in final if s.get("video_id"))
        _send(token, chat_id, f"✅ {UPDATE_KINDS[kind]} 完成\n"
                              f"歌曲池 {before} → {len(final)} 首（可用 {usable} 首）{tail}")
    except Exception as e:
        _send(token, chat_id, f"⚠️ 更新失敗：{_redact(e, token)}\n\n"
                              f"（解析可中斷續傳，再跑一次會從斷點繼續）")


# ─── cookie 生命週期 ─────────────────────────────────────────────

COOKIE_CHECK_EVERY = 6 * 3600
SCHED_STALE_AFTER = 2 * 3600      # 排程最密的是每 10 分鐘,兩小時沒動就是壞了


def _sched_stale() -> str | None:
    """檢查 firefox-ctl.sh 的排程還活著嗎。

    它每次執行都會更新 data/state/ffctl_heartbeat。心跳停掉代表:DSM 重寫了
    /etc/crontab 把我們的行清掉、腳本被刪、或 crond 掛了——三種都是靜默失敗,
    沒有這個檢查就沒人會發現(這個專案已經吃過一次:cookie 死了五天沒人知道)。
    """
    p = os.path.join(STATE_DIR, "ffctl_heartbeat")
    if not os.path.exists(p):
        return "找不到排程心跳檔（firefox-ctl.sh 沒執行過）"
    age = time.time() - os.path.getmtime(p)
    if age > SCHED_STALE_AFTER:
        return f"排程心跳已 {age / 3600:.1f} 小時沒更新（正常應每 10 分鐘一次）"
    return None


def _cookie_status(cfg, chat_id, verbose=True):
    alive, msg = cookie.check()
    if alive:
        if verbose:
            _send(cfg["telegram_token"], chat_id, f"✅ cookie 正常：{msg}")
        return True
    url = cfg.get("firefox_url", "（未設定 firefox_url）")
    _send(cfg["telegram_token"], chat_id,
          f"⚠️ YTM cookie 失效了（{msg}）\n\n"
          f"1. 開 {url} 登入 music.youtube.com\n"
          f"2. 登入完成後按下面的按鈕",
          _kb([[("我登入好了，重新擷取", "c:extract")]]))
    return False


def _cookie_extract(cfg, chat_id):
    token = cfg["telegram_token"]
    prof = cfg.get("firefox_profile") or cookie.default_profile()
    try:
        cookie.save(cookie.extract(prof))
    except Exception as e:
        _send(token, chat_id, f"⚠️ 擷取失敗：{_redact(e, token)}")
        return
    alive, msg = cookie.check()
    _send(token, chat_id, f"{'✅ cookie 已更新' if alive else '⚠️ 擷取到了但仍無法認證'}：{msg}")


def _cookie_watch(cfg, chat_id):
    """定期同步 + 檢查。

    先看 Firefox profile 有沒有更新的 cookie 可以撈(平常的主要路徑,不必通知你),
    撈完才檢查;只在「從正常變失效」時推播一次,避免每 6 小時洗一次訊息。
    """
    was_alive = True
    sched_warned = False
    while True:
        time.sleep(COOKIE_CHECK_EVERY)
        try:
            synced = cookie.sync_if_newer(cfg.get("firefox_profile"))
            if synced:
                print("[cookie]", synced, flush=True)
            alive, _ = cookie.check()
            if was_alive and not alive:
                _cookie_status(cfg, chat_id, verbose=False)
            was_alive = alive

            stale = _sched_stale()
            if stale and not sched_warned:
                _send(cfg["telegram_token"], chat_id,
                      f"⚠️ 瀏覽器排程可能失效了：{stale}\n\n"
                      f"最可能的原因是 DSM 改動「任務排程」時重寫了 /etc/crontab。"
                      f"修法見 deploy/nas-firefox/firefox-ctl.sh 檔尾註解。")
                sched_warned = True
            elif not stale:
                sched_warned = False
        except Exception as e:
            print("cookie watch error:", _redact(e, cfg.get("telegram_token", "")), flush=True)


# ─── 訊息 / 按鈕處理 ──────────────────────────────────────────────

def handle_message(cfg, chat_id, text, msg):
    token = cfg["telegram_token"]
    try:
        pool = _pool()
    except FileNotFoundError:
        # 以前這裡會讓例外冒到主迴圈,只印一行 loop error 就重試——
        # 使用者看到的是「bot 完全不回應」,查不出原因。
        _send(token, chat_id, NO_POOL_HINT)
        return
    low = text.lower().strip()
    rest = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""

    # 回覆 agent 追問 → 直接當描述跑 agent
    if (msg.get("reply_to_message") or {}).get("text", "").startswith(AGENT_PROMPT):
        _spawn(_run_agent, cfg, chat_id, text)
        return

    if low.startswith("/update"):
        rows = [[(v.split("（")[0], f"u:{k}")] for k, v in UPDATE_KINDS.items()]
        _send(token, chat_id, "🔄 要更新哪一部分？\n\n"
              + "\n".join(f"・{v}" for v in UPDATE_KINDS.values()), _kb(rows))
        return

    if low.startswith("/cookie"):
        _spawn(_cookie_status, cfg, chat_id)
        return

    if low.startswith("/help") or low in ("/start", "help"):
        _send(token, chat_id, HELP)
        return

    if low.startswith("/rand"):
        if re.search(r"(?<!\d)\d{1,2}(?!\d)", text):
            _spawn(_do_rand, token, chat_id, pool, _wanted_count(text, 20))
        else:
            _send(token, chat_id, "🎲 隨機幾首?", _kb([[(str(n), f"r:{n}") for n in COUNTS]]))
        return

    if low.startswith("/pool"):
        if rest:  # 帶參數 → 直接
            cand = llm_select._prefilter(rest, pool)
            picks = random.sample(cand, min(_wanted_count(text, 20), len(cand))) if cand else []
            _spawn(_publish, token, chat_id, f"🎯 {rest[:50]}", picks, f"Telegram /pool {rest}")
        else:     # 互動:先選年份
            ys = _pool_years(pool)[:8]
            rows = [[(y, f"p:y:{y}") for y in ys[i:i + 4]] for i in range(0, len(ys), 4)]
            rows.append([("不限年份", "p:y:all")])
            _send(token, chat_id, "🎯 要哪一年的動畫歌?", _kb(rows))
        return

    if low.startswith(("/agent", "/vibe")):
        if rest:
            _spawn(_run_agent, cfg, chat_id, rest)
        else:
            _send(token, chat_id, AGENT_PROMPT, {"force_reply": True,
                                                 "input_field_placeholder": "例:放鬆的、像 YOASOBI"})
        return

    # 沒有指令的純文字 → 錯誤提示
    _send(token, chat_id, "❓ 請用指令(按 / 或選單)。\n\n" + HELP)


def handle_callback(cfg, chat_id, msg_id, data, cb_id):
    token = cfg["telegram_token"]
    _answer_cb(token, cb_id)
    try:
        pool = _pool()
    except FileNotFoundError:
        _send(token, chat_id, NO_POOL_HINT)
        return
    p = data.split(":")
    if p[0] == "r":                                   # r:N → 隨機
        _edit(token, chat_id, msg_id, f"🎲 隨機 {p[1]} 首,建立中…")
        _spawn(_do_rand, token, chat_id, pool, int(p[1]))
    elif p[0] == "u":                          # u:<kind> → 更新歌曲池
        kind = p[1]
        _edit(token, chat_id, msg_id, f"🔄 {UPDATE_KINDS.get(kind, kind)}…")
        _spawn(_do_update, cfg, chat_id, kind)
    elif p[0] == "c" and p[1] == "extract":     # c:extract → 重新擷取 cookie
        _edit(token, chat_id, msg_id, "🍪 從 Firefox profile 擷取中…")
        _spawn(_cookie_extract, cfg, chat_id)
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
        _spawn(_do_pool, token, chat_id, pool, y, t, n)


def _set_commands(token):
    cmds = [
        {"command": "rand", "description": "隨機來一批(最快)"},
        {"command": "pool", "description": "挑年份・片頭(OP)/片尾(ED)"},
        {"command": "agent", "description": "用 AI 依心情/風格找歌(較慢)"},
        {"command": "update", "description": "更新歌曲池（新番／歌手／重新解析）"},
        {"command": "cookie", "description": "檢查 YTM cookie 狀態"},
        {"command": "help", "description": "使用說明"},
    ]
    try:
        requests.post(API.format(token=token, method="setMyCommands"), json={"commands": cmds}, timeout=15)
    except Exception as e:
        print("setMyCommands 失敗:", _redact(e, token))


def main():
    cfg = _cfg()
    token = cfg["telegram_token"]
    allowed = cfg.get("allowed_chat_id")
    _set_commands(token)
    if allowed:
        _spawn(_cookie_watch, cfg, allowed)
    offset = None
    if not os.path.exists(POOL_FILE):
        print(f"⚠️  找不到 {POOL_FILE}——選曲指令會無法使用。\n{NO_POOL_HINT}", flush=True)
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
                    saved = _claim_chat(cfg, cid)
                    allowed = cid
                    _spawn(_cookie_watch, cfg, allowed)   # 啟動時沒有 chat_id，到現在才能開監看
                    _send(token, cid, f"👋 已綁定這個聊天室（chat_id {cid}）"
                                      + ("" if saved else "\n⚠️ 但寫入設定檔失敗，重啟後要再綁一次")
                                      + "\n\n" + HELP)
                    continue
                if cid != allowed:
                    continue
                handle_message(cfg, cid, text, msg)
        except Exception as e:
            print("loop error:", _redact(e, token), flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
