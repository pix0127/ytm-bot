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
    parser = argparse.ArgumentParser(description="每日隨選歌單")
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

    state = load_state()
    name = f"今日隨選 ({datetime.now().strftime('%m/%d')})"
    pid = playlist.new_playlist(state.get("playlist_id"), name, PLAYLIST_DESC)
    print(f"✅ 已建立: {name}")

    res = playlist.fill_playlist(pid, [s["video_id"] for s in picked], skip=load_blocked_ids())
    added, skipped, failed = res["added"], res["skipped"], res["failed"]
    print(f"✅ 已加入 {added}/{len(picked)} 首")
    if skipped:
        print(f"🚫 跳過 {skipped} 首（黑名單）")
    if failed:
        print(f"⚠️  {failed} 首加入失敗（多為 Music-only／已下架，跳過）")

    state["playlist_id"] = pid
    save_state(state)
    url = res["url"]
    print(f"\n🔗 {url}")
    print(f"\nJSON:{json.dumps({'url': url, 'count': len(picked), 'added': added}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
