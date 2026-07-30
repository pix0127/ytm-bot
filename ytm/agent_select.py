"""Agent 式選曲：LLM 透過工具(篩 pool / YTM 電台 / YTM 搜尋)實際取得真實候選,
多步推理後挑出 N 首。用 ReAct JSON 動作協定(不依賴 gateway 的原生 function-calling)。

工具的 radio/search 走 youtubei → 需要 cookie(browser.json)。
最終建歌單見 playlist 模組,video_id 通用。
"""
import json
import re
import time

import requests

from .config import AUTH_FILE

MAX_STEPS = 6
OBS_LIMIT = 20        # 每次工具回傳給 LLM 的最多筆數
CATALOG_CAP = 400     # 整場 catalog 上限

SYSTEM = """你是動畫音樂選曲 agent。目標:依使用者要求,用工具找出真實歌曲,最後挑出指定數量。

每一步只回**一個** JSON(不要多餘文字),從以下擇一:
{"action":"filter_pool","args":{"year":"2024","type":"OP","artist":"","anime":""}}  // 條件皆可省略,篩本地歌曲池
{"action":"radio","args":{"id":3}}          // 用 catalog 中第 id 首當種子,開 YTM 電台找相似歌(真實推薦)
{"action":"search_ytm","args":{"query":"..."}}  // 在 YouTube Music 搜尋
{"action":"final","args":{"ids":[1,5,9]}}   // 完成,交出 catalog 中這些編號

規則:
- 工具回傳的每首歌都有 catalog 編號;radio 與 final 只能用**已出現過的**編號。
- 由你自己決定用哪些工具:若使用者給的是**明確 metadata**(年份/OP-ED/指定歌手或作品),直接 filter_pool 選完就 final,**不必開電台**(較快);只有**氛圍/相似/發現類**需求(如「放鬆的」「像X那種」「推薦新歌」)才用 radio 從對味種子擴展出真實相似曲,不要空想。
- **候選收集到目標數的約 2 倍就要 final,不要一直 radio/search**。盡量 3~5 步內 final。
- final 前確認每首都**符合原始需求的語言/主題**(例如要日本歌手就別放中文/其他語言的歌),離題的不要選。
- final 的 ids 數量 = 使用者要求的數量。"""


def _dedupe(songs: list[dict]) -> list[dict]:
    """依 video_id 去重,保留順序。"""
    seen, out = set(), []
    for s in songs:
        v = s.get("video_id")
        if v and v not in seen:
            seen.add(v)
            out.append(s)
    return out


def _chat(messages: list, url: str, key: str, model: str) -> str:
    # thinking 關掉:選曲/挑動作沒有推理步驟可走,reasoning 只是空轉(實測 10s → 1.7s),
    # 且 reasoning 會把 max_tokens 吃光導致 content 回空字串。關掉後 800 tokens 綽綽有餘。
    r = requests.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": model, "messages": messages, "max_tokens": 800, "temperature": 0.5,
                            "thinking": {"type": "disabled"}},
                      timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def select(message: str, pool: list[dict], count: int, cfg: dict, on_step=None) -> list[dict]:
    from ytmusicapi import YTMusic
    yt = YTMusic(AUTH_FILE)
    pool = [s for s in pool if s.get("video_id")]

    catalog: list[dict] = []

    def add(title, artist, video_id, extra="") -> int:
        catalog.append({"title": title, "artist": artist, "video_id": video_id, "extra": extra})
        return len(catalog) - 1

    def obs(idxs: list[int]) -> str:
        if not idxs:
            return "(沒有結果)"
        return "\n".join(f"{i}: {catalog[i]['title']} — {catalog[i]['artist']} {catalog[i]['extra']}".rstrip()
                         for i in idxs)

    def do(action: str, args: dict) -> str:
        if len(catalog) >= CATALOG_CAP:
            return "catalog 已滿,請直接 final。"
        if action == "filter_pool":
            res = pool
            if args.get("year"):
                res = [s for s in res if str(args["year"]) in str(s.get("season", ""))]
            if args.get("type"):
                res = [s for s in res if (s.get("type") or "").upper() == str(args["type"]).upper()]
            if args.get("artist"):
                res = [s for s in res if str(args["artist"]).lower() in (s.get("artist") or "").lower()]
            if args.get("anime"):
                res = [s for s in res if str(args["anime"]).lower() in (s.get("anime") or "").lower()]
            idxs = [add(s.get("title", "?"), s.get("artist", "") or "?", s["video_id"],
                        f"[{s.get('anime','') or ''} {str(s.get('season','') or '').split(' ')[0]} {s.get('type','') or ''}]".strip())
                    for s in res[:OBS_LIMIT]]
            return f"filter_pool 命中 {len(res)} 首(顯示前 {len(idxs)}):\n{obs(idxs)}"
        if action == "radio":
            seed = catalog[int(args["id"])]
            w = yt.get_watch_playlist(videoId=seed["video_id"], radio=True, limit=OBS_LIMIT + 5)
            idxs = []
            for t in w.get("tracks", []):
                if t.get("videoId") and len(idxs) < OBS_LIMIT:
                    a = ", ".join(x.get("name", "") for x in (t.get("artists") or []))
                    idxs.append(add(t.get("title", "?"), a or "?", t["videoId"]))
            return f"radio(種子:{seed['title']}) 找到:\n{obs(idxs)}"
        if action == "search_ytm":
            r = yt.search(args["query"], filter="songs", limit=OBS_LIMIT)
            idxs = [add(t.get("title", "?"),
                        ", ".join(x.get("name", "") for x in (t.get("artists") or [])) or "?",
                        t["videoId"]) for t in r if t.get("videoId")]
            return f"search「{args['query']}」找到:\n{obs(idxs)}"
        return f"未知動作 {action}"

    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"使用者要求:「{message}」。請挑 {count} 首。"}]
    url, key, model = cfg["llm_url"], cfg["llm_api_key"], cfg.get("model", "deepseek-v4-flash")

    t_start = time.time()
    print(f"[agent] 開始:「{message}」(挑 {count})", flush=True)
    for step in range(1, MAX_STEPS + 1):
        t0 = time.time()
        text = _chat(messages, url, key, model)
        llm_dt = time.time() - t0
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            print(f"[agent] step{step}: LLM {llm_dt:.1f}s → 無 JSON,重試", flush=True)
            messages.append({"role": "user", "content": "請只回一個合法 JSON 動作。"})
            continue
        try:
            act = json.loads(m.group(0))
        except Exception:
            print(f"[agent] step{step}: LLM {llm_dt:.1f}s → JSON 壞,重試", flush=True)
            messages.append({"role": "user", "content": "JSON 解析失敗,請只回一個合法 JSON 動作。"})
            continue
        action, args = act.get("action"), act.get("args", {})
        if action == "final":
            print(f"[agent] step{step}: LLM {llm_dt:.1f}s → final | 總計 {time.time()-t_start:.1f}s", flush=True)
            ids = args.get("ids", [])
            return _dedupe([catalog[i] for i in ids if isinstance(i, int) and 0 <= i < len(catalog)])
        t1 = time.time()
        try:
            observation = do(action, args)
        except Exception as e:
            observation = f"工具執行錯誤:{e}"
        tool_dt = time.time() - t1
        print(f"[agent] step{step}: LLM {llm_dt:.1f}s → {action} | 工具 {tool_dt:.1f}s (catalog={len(catalog)})", flush=True)
        if on_step:
            on_step(step, action, len(catalog))
        messages.append({"role": "assistant", "content": m.group(0)})
        messages.append({"role": "user", "content": observation})
    # 步數用盡:強制 LLM 從已收集候選中做最終挑選(排除離題),而非直接倒出原始 catalog
    messages.append({"role": "user", "content":
        f"步數用盡,現在**必須**輸出 final:從先前工具回傳過的候選編號中,挑 {count} 首最符合"
        f"「{message}」的,**排除語言/主題不符或離題的**。只回 JSON {{\"ids\":[...]}}。"})
    picks = []
    try:
        text = _chat(messages, url, key, model)
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            ids = json.loads(m.group(0)).get("ids", [])
            picks = [catalog[i] for i in ids if isinstance(i, int) and 0 <= i < len(catalog)]
    except Exception:
        pass
    print(f"[agent] 強制 final:{len(picks)} 首 | 總計 {time.time()-t_start:.1f}s", flush=True)
    return _dedupe(picks) if picks else _dedupe(catalog)[:count]
