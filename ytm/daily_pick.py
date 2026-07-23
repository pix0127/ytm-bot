#!/usr/bin/env python3.12
"""
每日隨選歌單 — 從 pool.json 隨機抽選 N 首，建立 YouTube Music 歌單

每次執行會刪除舊歌單 → 建立新歌單（不含 --dry-run）

用法:
  python3 daily_pick.py                 # 預設抽 20 首
  python3 daily_pick.py --count 30      # 抽 30 首
  python3 daily_pick.py --dry-run       # 只顯示不寫入
"""

import json
import os
import random
import sys
import argparse
from datetime import datetime

from .blocklist import load_blocked_ids
from .matcher import resolve_video_id
from .config import POOL_FILE, AUTH_FILE, DAILY_STATE as STATE_FILE

PLAYLIST_DESC = "每日自動從歌曲大池隨機抽選"


def load_pool() -> list[dict]:
    if not os.path.exists(POOL_FILE):
        print("❌ pool.json 不存在，請先執行 collect.py")
        sys.exit(1)
    with open(POOL_FILE) as f:
        data = json.load(f)
    return data.get("songs", [])


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def pick_songs(songs: list[dict], count: int) -> list[dict]:
    return random.sample(songs, min(count, len(songs)))


def print_playlist(picked: list[dict]):
    anime = [s for s in picked if s.get("source") == "anime"]
    artist = [s for s in picked if s.get("source") == "artist"]
    print(f"🎵 共抽 {len(picked)} 首")
    print(f"   新番: {len(anime)} 首 | 歌手: {len(artist)} 首\n")

    for i, s in enumerate(picked, 1):
        title = s.get("title", "?")
        artist = s.get("artist", "?") or "?"
        if s.get("source") == "anime":
            info = f" [番] {s.get('anime', '?')} ({s.get('type', '')})"
        else:
            info = " [歌手]"
        print(f"  {i:2d}. {title} — {artist}{info}")


def delete_old_playlist(yt, state: dict):
    """刪除上一次建立的歌單"""
    old_id = state.get("playlist_id")
    if not old_id:
        return
    try:
        yt.delete_playlist(old_id)
        print(f"🗑️  已刪除舊歌單")
    except Exception as e:
        print(f"⚠️  無法刪除舊歌單: {e}")


def create_playlist(yt, picked: list[dict]) -> str:
    """建立新歌單並加歌"""
    name = f"今日隨選 ({datetime.now().strftime('%m/%d')})"
    playlist_id = yt.create_playlist(name, PLAYLIST_DESC)
    print(f"✅ 已建立: {name}")

    blocked = load_blocked_ids()
    added = 0
    skipped = 0
    not_found = []
    for s in picked:
        try:
            vid = resolve_video_id(yt, s)
            if not vid:
                not_found.append(f"{s.get('title', '')} {s.get('artist', '')}")
            elif vid in blocked:
                skipped += 1
            else:
                yt.add_playlist_items(playlist_id, [vid])
                added += 1
        except Exception:
            not_found.append(s.get("title", "?"))

    print(f"✅ 已加入 {added}/{len(picked)} 首")
    if skipped:
        print(f"🚫 跳過 {skipped} 首（在黑名單中）")
    if not_found:
        print(f"⚠️  未找到 {len(not_found)} 首")
        for q in not_found[:5]:
            print(f"    - {q}")

    return playlist_id


# ─── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="每日隨選歌單")
    parser.add_argument("--count", type=int, default=20, help="抽選數量 (預設 20)")
    parser.add_argument("--dry-run", action="store_true", help="只顯示不寫入")
    args = parser.parse_args()

    songs = load_pool()
    if len(songs) < args.count:
        print(f"⚠️  池中只有 {len(songs)} 首，少於要求的 {args.count}")
        args.count = len(songs)

    state = load_state()
    picked = pick_songs(songs, args.count)

    print("\n" + "=" * 40)
    print_playlist(picked)

    if args.dry_run:
        print("\n🔍 乾跑模式，未寫入 YTM")
        return

    from ytmusicapi import YTMusic
    yt = YTMusic(AUTH_FILE)

    delete_old_playlist(yt, state)
    playlist_id = create_playlist(yt, picked)

    # 更新狀態
    state["playlist_id"] = playlist_id
    state["last_pick"] = [s.get("title", "") for s in picked]
    save_state(state)

    playlist_url = f"https://music.youtube.com/playlist?list={playlist_id}"
    print(f"\n🔗 {playlist_url}")

    result = {
        "url": playlist_url,
        "count": len(picked),
        "anime": len([s for s in picked if s.get("source") == "anime"]),
        "artists": len([s for s in picked if s.get("source") == "artist"]),
    }
    print(f"\nJSON:{json.dumps(result, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
