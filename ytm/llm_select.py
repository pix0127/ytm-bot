"""依使用者訊息從 pool 挑歌：先便宜地 metadata 預篩縮小候選，再用 LLM 依語意/氛圍挑 N 首。

LLM 走 OpenAI 相容的 /chat/completions 端點（url/key/model 由 bot_config 提供），
只准回候選清單裡的編號，避免挑到 pool 以外的歌（幻覺）。
沒有曲風標籤時，氛圍判斷靠 LLM 對動畫/歌手的既有知識——名歌手準、冷門曲較弱。
"""
import json
import re

import requests

CANDIDATE_CAP = 600  # 丟給 LLM 的候選上限（控制 token/成本）


def _prefilter(message: str, pool: list[dict]) -> list[dict]:
    """用訊息裡的明確 metadata 線索縮小候選；抓不到線索就回全部。"""
    msg = message.lower()
    cands = [s for s in pool if s.get("video_id")]

    years = re.findall(r"20\d{2}", message)
    if years:
        cands = [s for s in cands if any(y in str(s.get("season", "")) for y in years)]

    if re.search(r"\bop\b|片頭|opening", msg):
        cands = [s for s in cands if s.get("type") == "OP"]
    elif re.search(r"\bed\b|片尾|ending", msg):
        cands = [s for s in cands if s.get("type") == "ED"]

    hit = [s for s in cands
           if (s.get("artist") and s["artist"].lower() in msg)
           or (s.get("anime") and s["anime"].lower() in msg)]
    if hit:
        cands = hit

    return cands


def _compact(songs: list[dict]) -> str:
    lines = []
    for i, s in enumerate(songs):
        yr = str(s.get("season", "") or "").split(" ")[0]
        tag = s.get("type") or s.get("source") or ""
        lines.append(f"{i}\t{s.get('title','?')} — {s.get('artist','') or '?'}"
                     f"\t[{s.get('anime','') or ''} {yr} {tag}]".rstrip())
    return "\n".join(lines)


def select(message: str, pool: list[dict], count: int,
           llm_url: str, api_key: str, model: str) -> list[dict]:
    """回傳挑中的 song dict 清單（順序即 LLM 給的順序）。"""
    import random

    cands = _prefilter(message, pool)
    if not cands:
        return []
    if len(cands) > CANDIDATE_CAP:
        cands = random.sample(cands, CANDIDATE_CAP)

    prompt = (
        f"使用者想要的歌單：「{message}」\n\n"
        f"候選歌曲（編號<TAB>歌名 — 歌手<TAB>[作品 年份 類型]）：\n{_compact(cands)}\n\n"
        f"請從上面挑出最符合使用者要求的 {count} 首。只能用候選清單裡的編號。"
        f"只回 JSON,格式:{{\"ids\": [編號, ...]}},不要多餘文字。"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是動畫音樂選曲助手,依使用者的語意/氛圍/條件從候選清單挑歌。只回 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 800,
        "temperature": 0.7,
        # 關掉 thinking:挑歌沒有推理步驟可走,reasoning 只是空轉(實測 27s → 2s),
        # 且候選一多時 reasoning 會吃光 max_tokens 讓 content 回空字串(選曲整個失敗)。
        "thinking": {"type": "disabled"},
    }
    r = requests.post(llm_url, headers={"Authorization": f"Bearer {api_key}",
                                        "Content-Type": "application/json"},
                      json=payload, timeout=90)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    ids = json.loads(m.group(0)).get("ids", [])
    return [cands[i] for i in ids if isinstance(i, int) and 0 <= i < len(cands)]
