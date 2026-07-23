"""被按爛 (DISLIKE) 的歌曲黑名單 — 由 prune_disliked.py 寫入，
daily_pick / yearly_playlists 重建歌單時讀取並跳過這些 videoId。"""
import json
import os

from .config import BLOCKLIST_FILE


def _load_entries() -> list[dict]:
    if not os.path.exists(BLOCKLIST_FILE):
        return []
    with open(BLOCKLIST_FILE) as f:
        return json.load(f).get("disliked", [])


def load_blocked_ids() -> set:
    return {e["videoId"] for e in _load_entries() if e.get("videoId")}


def add_blocked(entries: list[dict]) -> int:
    """entries: [{videoId, title, artist}]；回傳實際新增的筆數（去重）。"""
    existing = _load_entries()
    have = {e["videoId"] for e in existing if e.get("videoId")}
    added = 0
    for e in entries:
        vid = e.get("videoId")
        if vid and vid not in have:
            existing.append({
                "videoId": vid,
                "title": e.get("title", ""),
                "artist": e.get("artist", ""),
            })
            have.add(vid)
            added += 1
    with open(BLOCKLIST_FILE, "w") as f:
        json.dump({"disliked": existing}, f, ensure_ascii=False, indent=2)
    return added
