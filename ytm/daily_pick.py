#!/usr/bin/env python3.12
"""每日隨選歌單。

從 pool 隨機抽 N 首（只用已解析好的 video_id，不必搜尋），刪掉昨天的歌單、建今天的。
需要有效的 browser cookie（見 ytm/cookie.py）。

用法:
  python3 -m ytm.daily_pick --count 20
  python3 -m ytm.daily_pick --dry-run
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime

from . import playlist
from .config import POOL_FILE, STATE_DIR
from .blocklist import load_blocked_ids

STATE_FILE = os.path.join(STATE_DIR, "daily_pick_state.json")
PLAYLIST_DESC = "每日自動從歌曲大池隨機抽選"


def _load_songs() -> list[dict]:
    if not os.path.exists(POOL_FILE):
        raise RuntimeError("pool.json 不存在，請先執行 collect / resolve_pool")
    with open(POOL_FILE) as f:
        pool = json.load(f).get("songs", [])
    songs = [s for s in pool if s.get("video_id")]   # 只用已解析出 videoId 的
    if not songs:
        raise RuntimeError("pool 中沒有含 video_id 的歌，請先執行 resolve_pool")
    return songs


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run(count: int) -> dict:
    songs = _load_songs()
    picked = random.sample(songs, min(count, len(songs)))
    state = load_state()
    name = f"今日隨選 ({datetime.now().strftime('%m/%d')})"
    pid = playlist.new_playlist(state.get("playlist_id"), name, PLAYLIST_DESC)
    res = playlist.fill_playlist(pid, [s["video_id"] for s in picked], skip=load_blocked_ids())
    state["playlist_id"] = pid
    save_state(state)
    return {"url": res["url"], "count": len(picked), "added": res["added"],
            "skipped": res["skipped"], "failed": res["failed"], "name": name}


def main():
    parser = argparse.ArgumentParser(description="每日隨選歌單")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        if args.dry_run:
            songs = _load_songs()
            picked = random.sample(songs, min(args.count, len(songs)))
            print("\n" + "=" * 40)
            print(f"🎵 共抽 {len(picked)} 首")
            for i, s in enumerate(picked, 1):
                print(f"  {i:2d}. {s.get('title', '?')} — {s.get('artist', '') or '?'}")
            print("\n🔍 乾跑模式，未寫入")
            return

        info = run(args.count)
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print("\n" + "=" * 40)
    print(f"🎵 共抽 {info['count']} 首")
    print(f"✅ 已建立: {info['name']}")
    print(f"✅ 已加入 {info['added']}/{info['count']} 首")
    if info["skipped"]:
        print(f"🚫 跳過 {info['skipped']} 首（黑名單）")
    if info["failed"]:
        print(f"⚠️  {info['failed']} 首加入失敗（多為 Music-only／已下架，跳過）")
    print(f"\n🔗 {info['url']}")
    print(f"\nJSON:{json.dumps({'url': info['url'], 'count': info['count'], 'added': info['added']}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
