#!/usr/bin/env python3.12
"""偵測你在 YouTube Music 按爛 (DISLIKE) 的歌，從所有管理中的歌單移除，並記進黑名單。

黑名單 (blocklist.json) 會讓 daily_pick / yearly_playlists 之後重建歌單時自動跳過這些歌，
所以按爛的歌不只從現有歌單踢掉，也不會再被抽/加回來。

用法:
  python3 prune_disliked.py            # 掃所有管理中的歌單並清理
  python3 prune_disliked.py --dry-run  # 只列出會踢哪些，不動歌單、不寫黑名單
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

from .blocklist import add_blocked, load_blocked_ids
from .config import AUTH_FILE, DAILY_STATE, YEARLY_STATE, POOL_FILE, BACKUP_DIR


def _read(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def managed_playlists() -> dict:
    """回傳 {顯示名稱: playlistId} —— 今日隨選 + 各年度歌單。"""
    ids = {}
    daily = _read(DAILY_STATE).get("playlist_id")
    if daily:
        ids["今日隨選"] = daily
    for year, pid in _read(YEARLY_STATE).items():
        if pid:
            ids[f"{year} 新番"] = pid
    return ids


def _artist_str(track: dict) -> str:
    return ", ".join(a.get("name", "") for a in (track.get("artists") or []))


def remove_from_pool(disliked_ids: set) -> int:
    """把 pool.json 裡 video_id 在 disliked_ids 中的歌 row 刪掉；回傳刪除數。"""
    if not os.path.exists(POOL_FILE):
        return 0
    with open(POOL_FILE) as f:
        pool = json.load(f)
    songs = pool.get("songs", [])
    kept = [s for s in songs if s.get("video_id") not in disliked_ids]
    removed = len(songs) - len(kept)
    if removed:
        shutil.copy(POOL_FILE, os.path.join(BACKUP_DIR, f"pool.json.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
        pool["songs"] = kept
        with open(POOL_FILE, "w") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)
    return removed


def main():
    parser = argparse.ArgumentParser(description="踢掉按爛的歌並加入黑名單")
    parser.add_argument("--dry-run", action="store_true", help="只列出，不動歌單、不寫黑名單")
    args = parser.parse_args()

    playlists = managed_playlists()
    if not playlists:
        print("⚠️  找不到任何管理中的歌單（daily_pick_state.json / yearly_state.json 都沒有）")
        sys.exit(1)

    from ytmusicapi import YTMusic
    yt = YTMusic(AUTH_FILE)

    to_block = []
    total_removed = 0

    for label, pid in playlists.items():
        try:
            pl = yt.get_playlist(pid, limit=2000)
        except Exception as e:
            print(f"⚠️  {label} ({pid}) 讀取失敗: {e}")
            continue

        disliked = [t for t in pl.get("tracks", []) if t.get("likeStatus") == "DISLIKE"]
        if not disliked:
            print(f"✅ {label}: 沒有按爛的歌")
            continue

        print(f"👎 {label}: 發現 {len(disliked)} 首按爛")
        for t in disliked:
            print(f"    - {t.get('title', '?')} / {_artist_str(t) or '?'}")
            to_block.append({
                "videoId": t.get("videoId"),
                "title": t.get("title", ""),
                "artist": _artist_str(t),
            })

        if not args.dry_run:
            videos = [{"videoId": t["videoId"], "setVideoId": t["setVideoId"]}
                      for t in disliked if t.get("videoId") and t.get("setVideoId")]
            if videos:
                yt.remove_playlist_items(pid, videos)
                total_removed += len(videos)
                print(f"    🗑️  已從 {label} 移除 {len(videos)} 首")

    if args.dry_run:
        print(f"\n🔍 乾跑模式：共 {len(to_block)} 首按爛，未變更任何歌單、黑名單或 pool")
        return

    n = add_blocked(to_block)
    disliked_ids = {e["videoId"] for e in to_block if e.get("videoId")}
    pool_removed = remove_from_pool(disliked_ids)
    print(f"\n📊 完成：歌單移除 {total_removed} 首、黑名單新增 {n} 首"
          f"（現共 {len(load_blocked_ids())} 首）、pool 刪除 {pool_removed} 首")


if __name__ == "__main__":
    main()
