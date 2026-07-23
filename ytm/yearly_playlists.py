#!/usr/bin/env python3.12
"""
年度新番歌單產生器 — 從 pool.json 按年份建立 YouTube Music 歌單

用法:
  python3 yearly_playlists.py                 # 建立所有年份歌單
  python3 yearly_playlists.py --year 2026     # 只建特定年份
  python3 yearly_playlists.py --dry-run       # 只列出不寫入
  python3 yearly_playlists.py --update        # 重新整理已存在的歌單
"""

import json
import os
import sys
import argparse
import re

from .blocklist import load_blocked_ids
from .matcher import resolve_video_id
from .config import POOL_FILE, AUTH_FILE, YEARLY_STATE as STATE_FILE


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


def group_by_year(songs: list[dict]) -> dict[int, list[dict]]:
    """按年份分組（從 season 欄位萃取年份）"""
    years = {}
    for s in songs:
        if s.get("source") != "anime":
            continue
        season = s.get("season", "")
        m = re.match(r"(\d{4})", season)
        if not m:
            continue
        year = int(m.group(1))
        years.setdefault(year, []).append(s)
    return dict(sorted(years.items(), reverse=True))


def create_or_update_playlist(yt, year: int, songs: list[dict],
                               state: dict, update: bool) -> str | None:
    """為某一年份建立/更新 YTM 歌單"""
    playlist_id = state.get(str(year)) if update else None
    playlist_title = f"{year} 新番 OP/ED"
    playlist_desc = f"{year} 年動畫 OP/ED 歌單（共 {len(songs)} 首）\n自動產自 AnimeThemes.moe"

    if playlist_id:
        # 更新：清空 + 重新加
        try:
            existing = yt.get_playlist(playlist_id, limit=1000)
            existing_items = existing.get("tracks", [])
            if existing_items:
                videos = [{"videoId": t["videoId"], "setVideoId": t["setVideoId"]}
                          for t in existing_items if t.get("videoId") and t.get("setVideoId")]
                if videos:
                    yt.remove_playlist_items(playlist_id, videos)
            print(f"🔄 已清空 {playlist_title}")
        except Exception as e:
            print(f"⚠️  無法更新 {playlist_title}: {e}，將建立新的")
            playlist_id = None

    if not playlist_id:
        playlist_id = yt.create_playlist(playlist_title, playlist_desc)
        print(f"✅ 已建立: {playlist_title}")

    # 加歌
    blocked = load_blocked_ids()
    added = 0
    skipped = 0
    not_found = []
    for s in songs:
        vid = resolve_video_id(yt, s)
        if not vid:
            not_found.append(s.get("title", "?"))
            continue
        if vid in blocked:
            skipped += 1
            continue
        try:
            yt.add_playlist_items(playlist_id, [vid])
            added += 1
        except Exception:
            not_found.append(s.get("title", "?"))

    print(f"  → {added}/{len(songs)} 首已加入")
    if skipped:
        print(f"  🚫 跳過 {skipped} 首（黑名單）")
    if not_found:
        print(f"  ⚠️  找不到 {len(not_found)} 首")

    return playlist_id


# ─── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="年度新番歌單產生器")
    parser.add_argument("--year", type=int, help="只建特定年份")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update", action="store_true", help="重新整理已存在的歌單")
    args = parser.parse_args()

    songs = load_pool()
    by_year = group_by_year(songs)

    if not by_year:
        print("⚠️  pool 中沒有新番歌曲")
        return

    # 過濾年份
    if args.year:
        by_year = {args.year: by_year.get(args.year, [])}
        if not by_year[args.year]:
            print(f"⚠️  沒有 {args.year} 年的資料")
            return

    if args.dry_run:
        print(f"\n📋 將建立的年度歌單:\n")
        for year, songs in by_year.items():
            print(f"  {year} 新番 OP/ED — {len(songs)} 首")
        print()
        return

    state = load_state()
    from ytmusicapi import YTMusic
    yt = YTMusic(AUTH_FILE)

    for year, songs in by_year.items():
        print(f"\n📺 {year} ({len(songs)} 首)...")
        pid = create_or_update_playlist(yt, year, songs, state, args.update)
        if pid:
            state[str(year)] = pid
            print(f"  🔗 https://music.youtube.com/playlist?list={pid}")

    save_state(state)
    print(f"\n✅ 完成！共處理 {len(by_year)} 個年份歌單")


if __name__ == "__main__":
    main()
