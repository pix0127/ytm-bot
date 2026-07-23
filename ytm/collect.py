#!/usr/bin/env python3.12
"""
收集歌曲入池 — 爬新番 OP/ED + 訂閱歌手熱門歌 → 寫入 pool.json

用法:
  python3 collect.py                     # 收集本季新番 + 歌手
  python3 collect.py --all-seasons       # 收集所有歷史季
  python3 collect.py --artists-only      # 只收集歌手
  python3 collect.py --anime-only        # 只收集新番
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime

from .config import POOL_FILE, AUTH_FILE


# ─── API helpers ──────────────────────────────────────────────────

def api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "anime-playlist-gen/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_current_season() -> tuple[str, int]:
    m = datetime.now().month
    if 1 <= m <= 3:
        s = "Winter"
    elif 4 <= m <= 6:
        s = "Spring"
    elif 7 <= m <= 9:
        s = "Summer"
    else:
        s = "Fall"
    return s, datetime.now().year


# ─── Load / Save pool ────────────────────────────────────────────

def load_pool() -> dict:
    if os.path.exists(POOL_FILE) and os.path.getsize(POOL_FILE) > 0:
        with open(POOL_FILE) as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return {"songs": [], "artists": []}


def save_pool(pool: dict):
    # 去重：同一首歌只保留一筆
    seen = set()
    unique = []
    for s in pool["songs"]:
        key = (s.get("title", ""), s.get("artist", ""))
        if key not in seen:
            seen.add(key)
            unique.append(s)
    pool["songs"] = unique
    with open(POOL_FILE, "w") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    print(f"💾  pool.json 已更新: {len(pool['songs'])} 首歌, {len(pool['artists'])} 位歌手")


# ─── Anime Themes ────────────────────────────────────────────────

def _all_seasons_since(start_year: int = 2020) -> list[tuple[str, int]]:
    """產生從 start_year 到現在的每季列表（由近到遠, 不含當前季）"""
    from datetime import datetime
    now = datetime.now()
    m = now.month
    if 1 <= m <= 3:
        current = ("Winter", now.year)
    elif 4 <= m <= 6:
        current = ("Spring", now.year)
    elif 7 <= m <= 9:
        current = ("Summer", now.year)
    else:
        current = ("Fall", now.year)

    seasons = []
    for y in range(now.year, start_year - 1, -1):
        for s in ["Winter", "Spring", "Summer", "Fall"]:
            if (s, y) == current:
                continue  # 當前季另外處理
            if y == now.year and s in ("Summer", "Fall"):
                continue  # 尚未發生的未來季也跳過
            seasons.append((s, y))
    return seasons

ANIME_SEASONS = _all_seasons_since(2020)


def collect_anime_themes(season: str, year: int) -> list[dict]:
    """從 AnimeThemes.moe 抓某一季的 OP/ED"""
    songs = []
    page = 1
    while True:
        params = {
            "filter[year]": str(year),
            "filter[season]": season,
            "include": "animethemes.song.artists",
            "page[size]": "50",
            "page[number]": str(page),
            "sort": "id",
        }
        url = "https://api.animethemes.moe/anime?" + urllib.parse.urlencode(params)
        data = api_get(url)
        for anime in data.get("anime", []):
            anime_name = anime["name"]
            for theme in anime.get("animethemes", []):
                if theme["type"] not in ("OP", "ED"):
                    continue
                song = theme.get("song") or {}
                artists = song.get("artists") or []
                artist_names = ", ".join(a.get("name", "") for a in artists)
                if not song.get("title"):
                    continue
                songs.append({
                    "title": song["title"],
                    "artist": artist_names,
                    "source": "anime",
                    "anime": anime_name,
                    "season": f"{year} {season}",
                    "type": theme["type"],
                    "video_id": None,
                })
        if not data.get("links", {}).get("next"):
            break
        page += 1
    return songs


def collect_all_anime() -> list[dict]:
    """收集所有季番的 OP/ED"""
    all_songs = []
    current = get_current_season()
    for season, year in ANIME_SEASONS:
        if (year, season) == (current[1], current[0]):
            continue  # 當前季另外處理
        print(f"  📺 {year} {season}...", end=" ", flush=True)
        songs = collect_anime_themes(season, year)
        print(f"{len(songs)} 首")
        all_songs.extend(songs)
    return all_songs


# ─── Artist Songs ────────────────────────────────────────────────

def collect_artist_songs(yt, channel_id: str, artist_name: str, max_songs: int = 10) -> list[dict]:
    """抓一個歌手的前 N 首熱門歌"""
    try:
        artist = yt.get_artist(channel_id)
    except Exception as e:
        print(f"    ⚠️  {artist_name}: {e}")
        return []

    songs = []
    # 熱門歌曲
    top_songs = artist.get("songs", {}).get("results", [])
    for s in top_songs[:max_songs]:
        songs.append({
            "title": s.get("title", ""),
            "artist": artist_name,
            "source": "artist",
            "anime": None,
            "season": None,
            "type": None,
            "video_id": s.get("videoId"),
        })
    return songs


def collect_all_artists(yt) -> list[dict]:
    """收集所有已訂閱歌手的熱門歌"""
    all_songs = []
    subs = yt.get_library_subscriptions(limit=100)
    for sub in subs:
        name = sub.get("artist", "?")
        browse_id = sub.get("browseId", "")
        if not browse_id:
            continue
        print(f"  🎤 {name}...", end=" ", flush=True)
        songs = collect_artist_songs(yt, browse_id, name)
        print(f"{len(songs)} 首")
        all_songs.extend(songs)
    return all_songs


# ─── Main ─────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="收集歌曲入池")
    parser.add_argument("--all-seasons", action="store_true", help="收集所有歷史季")
    parser.add_argument("--artists-only", action="store_true")
    parser.add_argument("--anime-only", action="store_true")
    args = parser.parse_args()

    pool = load_pool()

    current_season, current_year = get_current_season()

    # ── 新番 OP/ED ──
    if not args.artists_only:
        print(f"📺 收集新番 OP/ED...")
        # 當前季
        print(f"  📺 {current_year} {current_season} (當前)...", end=" ", flush=True)
        current_songs = collect_anime_themes(current_season, current_year)
        print(f"{len(current_songs)} 首")
        pool["songs"].extend(current_songs)

        # 歷史季
        if args.all_seasons:
            past_songs = collect_all_anime()
            pool["songs"].extend(past_songs)

    # ── 歌手歌曲 ──
    if not args.anime_only:
        print(f"🎤 收集訂閱歌手熱門歌...")
        from ytmusicapi import YTMusic
        yt = YTMusic(AUTH_FILE)
        artist_songs = collect_all_artists(yt)
        pool["songs"].extend(artist_songs)

        # 記錄歌手列表
        artists = set(s["artist"] for s in artist_songs)
        pool["artists"] = sorted(artists)

    save_pool(pool)

    print(f"\n✅ 完成！pool 現有 {len(pool['songs'])} 首歌")
    sources = {}
    for s in pool["songs"]:
        src = s.get("source", "?")
        sources[src] = sources.get(src, 0) + 1
    for src, cnt in sources.items():
        print(f"   {src}: {cnt} 首")


if __name__ == "__main__":
    main()
