#!/usr/bin/env python3.12
"""把 pool.json 裡沒有 video_id 的歌（主要是新番）對應到真實的 YTM videoId。

用歌手閘門搜尋比對；搜不到可靠對應的就從 pool 刪掉（已同意）。
跑完後 pool 幾乎每首都有真實 videoId，daily_pick / yearly_playlists 重建時就能直接用、不必再搜。

用法:
  python3 resolve_pool.py                # 正式跑：填 video_id、刪找不到的（會先備份 pool.json）
  python3 resolve_pool.py --dry-run      # 只統計會填/刪多少，不改檔
  python3 resolve_pool.py --sample 30    # 只處理前 N 首待解析的（配 --dry-run 看命中率）
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

from .matcher import resolve_video_id
from .config import AUTH_FILE, POOL_FILE, BACKUP_DIR


def main():
    parser = argparse.ArgumentParser(description="把 pool 的歌對應到真實 YTM videoId")
    parser.add_argument("--dry-run", action="store_true", help="只統計，不改檔")
    parser.add_argument("--sample", type=int, help="只處理前 N 首待解析的")
    args = parser.parse_args()

    with open(POOL_FILE) as f:
        pool = json.load(f)
    songs = pool.get("songs", [])

    todo = [s for s in songs if not s.get("video_id")]
    print(f"待解析（無 video_id）: {len(todo)} / 全部 {len(songs)} 首")
    if args.sample:
        todo = todo[:args.sample]
        print(f"（--sample：只處理前 {len(todo)} 首）")

    from ytmusicapi import YTMusic
    yt = YTMusic(AUTH_FILE)

    filled = 0
    drop_titles = []
    for i, s in enumerate(todo, 1):
        vid = resolve_video_id(yt, s)  # video_id 為 null，故實際會去搜尋
        if vid:
            s["video_id"] = vid
            filled += 1
        else:
            s["_drop"] = True
            drop_titles.append(f"{s.get('title', '?')} / {s.get('artist', '') or '?'}")
        if i % 50 == 0:
            print(f"  ...{i}/{len(todo)}（已填 {filled}、待刪 {len(drop_titles)}）", flush=True)

    print(f"\n📊 填上 video_id: {filled} 首；找不到將刪除: {len(drop_titles)} 首")
    for t in drop_titles[:15]:
        print(f"    ✂️  {t}")
    if len(drop_titles) > 15:
        print(f"    …還有 {len(drop_titles) - 15} 首")

    if args.dry_run:
        for s in todo:
            s.pop("_drop", None)
        print("\n🔍 乾跑模式：未變更 pool.json")
        return

    backup = os.path.join(BACKUP_DIR, f"pool.json.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy(POOL_FILE, backup)
    kept = [s for s in songs if not s.get("_drop")]
    for s in kept:
        s.pop("_drop", None)
    pool["songs"] = kept
    with open(POOL_FILE, "w") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已覆寫 pool.json（備份於 {os.path.basename(backup)}）")
    print(f"   pool 現有 {len(kept)} 首（刪除了 {len(drop_titles)} 首找不到的）")


if __name__ == "__main__":
    main()
