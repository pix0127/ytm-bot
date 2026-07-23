#!/usr/bin/env python3.12
"""每日隨選歌單（OAuth / 官方 YouTube Data API v3 版）。

與 daily_pick 選歌相同，但用官方 API → 免 cookie、OAuth 自動 refresh，適合 NAS 無人值守。
只用 pool 已解析好的 video_id（免搜尋，配額極省：建歌單 50 + 每首 50 + 刪舊 50）。

需先： python -m ytm.oauth 完成一次授權。

用法:
  python3 -m ytm.daily_pick_oauth --count 20
  python3 -m ytm.daily_pick_oauth --dry-run
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime

import requests

from .config import POOL_FILE, STATE_DIR
from .blocklist import load_blocked_ids
from .oauth import get_access_token

V3 = "https://www.googleapis.com/youtube/v3"
STATE_FILE = os.path.join(STATE_DIR, "daily_pick_oauth_state.json")
PLAYLIST_DESC = "每日自動從歌曲大池隨機抽選（Data API v3）"


def load_pool() -> list[dict]:
    if not os.path.exists(POOL_FILE):
        print("❌ pool.json 不存在，請先執行 collect / resolve_pool")
        sys.exit(1)
    with open(POOL_FILE) as f:
        return json.load(f).get("songs", [])


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="每日隨選歌單（Data API v3）")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    songs = [s for s in load_pool() if s.get("video_id")]  # 只用已解析出 videoId 的
    if not songs:
        print("❌ pool 中沒有含 video_id 的歌，請先執行 resolve_pool")
        sys.exit(1)
    picked = random.sample(songs, min(args.count, len(songs)))

    print("\n" + "=" * 40)
    print(f"🎵 共抽 {len(picked)} 首")
    for i, s in enumerate(picked, 1):
        print(f"  {i:2d}. {s.get('title', '?')} — {s.get('artist', '') or '?'}")

    if args.dry_run:
        print("\n🔍 乾跑模式，未寫入")
        return

    H = {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}
    state = load_state()

    old = state.get("playlist_id")
    if old:
        r = requests.delete(f"{V3}/playlists", headers=H, params={"id": old})
        print("🗑️  已刪除舊歌單" if r.status_code == 204 else f"⚠️  刪舊歌單 HTTP {r.status_code}")

    name = f"今日隨選 ({datetime.now().strftime('%m/%d')})"
    r = requests.post(f"{V3}/playlists", headers=H, params={"part": "snippet,status"},
                      json={"snippet": {"title": name, "description": PLAYLIST_DESC},
                            "status": {"privacyStatus": "private"}})
    r.raise_for_status()
    pid = r.json()["id"]
    print(f"✅ 已建立: {name}")

    blocked = load_blocked_ids()
    added = skipped = failed = 0
    for s in picked:
        vid = s["video_id"]
        if vid in blocked:
            skipped += 1
            continue
        rr = requests.post(f"{V3}/playlistItems", headers=H, params={"part": "snippet"},
                           json={"snippet": {"playlistId": pid,
                                             "resourceId": {"kind": "youtube#video", "videoId": vid}}})
        if rr.ok:
            added += 1
        else:
            failed += 1

    print(f"✅ 已加入 {added}/{len(picked)} 首")
    if skipped:
        print(f"🚫 跳過 {skipped} 首（黑名單）")
    if failed:
        print(f"⚠️  {failed} 首加入失敗（多為 Music-only／已下架，跳過）")

    state["playlist_id"] = pid
    save_state(state)
    url = f"https://music.youtube.com/playlist?list={pid}"
    print(f"\n🔗 {url}")
    print(f"\nJSON:{json.dumps({'url': url, 'count': len(picked), 'added': added}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
