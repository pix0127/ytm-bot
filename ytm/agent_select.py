"""Agent 式選曲：LLM 透過工具(篩 pool / YTM 電台 / YTM 搜尋)實際取得真實候選,
多步推理後挑出 N 首。用 ReAct JSON 動作協定(不依賴 gateway 的原生 function-calling)。

工具的 radio/search 走 youtubei → 需要 cookie(browser.json)。
最終建歌單仍走 Data API v3(見 dataapi),video_id 通用。
"""
import json
import re

import requests

from .config import AUTH_FILE

MAX_STEPS = 8
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
- 想做「氛圍/曲風」挑選時,靠 radio 從對味的種子擴展出真實相似曲,再從中選,不要空想。
- 盡量在 3~6 步內完成。final 的 ids 數量 = 使用者要求的數量。"""


def _chat(messages: list, url: str, key: str, model: str) -> str:
    r = requests.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": model, "messages": messages, "max_tokens": 4000, "temperature": 0.5},
                      timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def select(message: str, pool: list[dict], count: int, cfg: dict) -> list[dict]:
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

    for _ in range(MAX_STEPS):
        text = _chat(messages, url, key, model)
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            messages.append({"role": "user", "content": "請只回一個合法 JSON 動作。"})
            continue
        try:
            act = json.loads(m.group(0))
        except Exception:
            messages.append({"role": "user", "content": "JSON 解析失敗,請只回一個合法 JSON 動作。"})
            continue
        action, args = act.get("action"), act.get("args", {})
        if action == "final":
            ids = args.get("ids", [])
            return [catalog[i] for i in ids if isinstance(i, int) and 0 <= i < len(catalog)]
        try:
            observation = do(action, args)
        except Exception as e:
            observation = f"工具執行錯誤:{e}"
        messages.append({"role": "assistant", "content": m.group(0)})
        messages.append({"role": "user", "content": observation})

    # 用完步數還沒 final:回目前 catalog 的前 count 首(至少有東西)
    return catalog[:count]
