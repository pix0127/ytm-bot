#!/usr/bin/env python3.12
"""
收集歌曲入池 — 爬新番 OP/ED + 訂閱歌手熱門歌 → 寫入 pool.json

用法:
  python3 collect.py                     # 收集本季新番 + 歌手
  python3 collect.py --all-seasons       # 收集所有歷史季
  python3 collect.py --artists-only      # 只收集歌手
  python3 collect.py --anime-only        # 只收集新番
  python3 collect.py --fill-anime-jp     # 只補既有資料的日文作品名
"""

import json
import os
import re
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


_JP_CHARS = re.compile(r"[぀-ヿ一-鿿]")


def _jp_synonym(anime: dict) -> str | None:
    """AnimeThemes 的 animesynonyms 裡撈日文作品名(約八成作品有)。

    pool 的歌名與作品名都是羅馬字,但 YTM 目錄以日文為主;有日文作品名,
    resolve_pool 才能多搜一輪「日文作品名 + OP/ED」,撈到只有日文標題的曲子。
    """
    for syn in anime.get("animesynonyms", []):
        text = syn.get("text") or ""
        if _JP_CHARS.search(text):
            return text
    return None


def fill_anime_jp(pool: dict) -> int:
    """替 pool 裡既有的歌補上 anime_jp（新抓的歌由 collect_anime_themes 直接帶）。"""
    anime_songs = [s for s in pool["songs"] if s.get("anime")]
    seasons = sorted({str(s["season"]) for s in anime_songs if s.get("season")})
    jp: dict[str, str] = {}
    for sea in seasons:
        parts = sea.split(" ")
        page = 1
        while True:
            params = {"filter[year]": parts[0], "include": "animesynonyms",
                      "page[size]": "100", "page[number]": str(page)}
            if len(parts) > 1:
                params["filter[season]"] = parts[1]
            data = api_get("https://api.animethemes.moe/anime?" + urllib.parse.urlencode(params))
            for anime in data.get("anime", []):
                name = _jp_synonym(anime)
                if name:
                    jp.setdefault(anime["name"], name)
            if not data.get("links", {}).get("next"):
                break
            page += 1
        print(f"  {sea}: 累計 {len(jp)} 個日文名", flush=True)
    filled = 0
    for s in anime_songs:
        if s["anime"] in jp and not s.get("anime_jp"):
            s["anime_jp"] = jp[s["anime"]]
            filled += 1
    return filled


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
    # 去重一:同名同歌手只留一筆
    seen = set()
    unique = []
    for s in pool["songs"]:
        key = (s.get("title", ""), s.get("artist", ""))
        if key not in seen:
            seen.add(key)
            unique.append(s)

    # 去重二:一支影片只能是一首歌。同名同歌手擋不掉這兩種情況——
    #   動畫來源與歌手來源是同一支影片但標題不同（"Adrena" vs「アドレナ」）
    #   合作曲在每位訂閱歌手的頁面都出現，各自掛不同的 artist（"Be the Light" x4）
    # 留 metadata 較完整的那筆（有 anime 的可支援 /pool 的年份與 OP/ED 篩選）。
    unique.sort(key=lambda s: (0 if s.get("anime") else 1))
    by_vid = {}
    out = []
    for s in unique:
        vid = s.get("video_id")
        if not vid:
            out.append(s)
        elif vid not in by_vid:
            by_vid[vid] = s
            out.append(s)
    dropped = len(unique) - len(out)
    if dropped:
        print(f"   （去重：{dropped} 筆與其他歌曲共用同一支影片，已合併）")
    pool["songs"] = out
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
            "include": "animethemes.song.artists,animesynonyms",
            "page[size]": "50",
            "page[number]": str(page),
            "sort": "id",
        }
        url = "https://api.animethemes.moe/anime?" + urllib.parse.urlencode(params)
        data = api_get(url)
        for anime in data.get("anime", []):
            anime_name = anime["name"]
            anime_jp = _jp_synonym(anime)
            for theme in anime.get("animethemes", []):
                if theme["type"] not in ("OP", "ED"):
                    continue
                song = theme.get("song") or {}
                artists = song.get("artists") or []
                artist_names = ", ".join(a.get("name", "") for a in artists)
                if not song.get("title"):
                    continue
                entry = {
                    "title": song["title"],
                    "artist": artist_names,
                    "source": "anime",
                    "anime": anime_name,
                    "season": f"{year} {season}",
                    "type": theme["type"],
                    "video_id": None,
                }
                if anime_jp:
                    entry["anime_jp"] = anime_jp
                songs.append(entry)
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

def collect_artist_songs(yt, channel_id: str, artist_name: str, max_songs: int = 50) -> list[dict]:
    """抓一個歌手的熱門歌。

    get_artist()['songs']['results'] 只是歌手頁上的**五首預覽**,不是完整清單;
    完整清單要追同一個 dict 裡的 browseId(實測 LiSA:預覽 5 首 → 追下去 31 首)。
    先前 max_songs 設 10 也沒用,因為來源本身只給 5 筆。
    """
    try:
        artist = yt.get_artist(channel_id)
    except Exception as e:
        print(f"    ⚠️  {artist_name}: {e}")
        return []

    block = artist.get("songs") or {}
    tracks = block.get("results") or []
    if block.get("browseId"):
        try:
            tracks = yt.get_playlist(block["browseId"], limit=None).get("tracks") or tracks
        except Exception as e:
            print(f"    ⚠️  {artist_name} 完整清單取得失敗（退回預覽 {len(tracks)} 首）：{type(e).__name__}")

    songs = []
    for s in tracks[:max_songs]:
        if not s.get("videoId"):
            continue
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
    parser.add_argument("--fill-anime-jp", action="store_true",
                        help="只替既有 pool 補日文作品名（resolve_pool 用它多搜一輪）")
    args = parser.parse_args()

    pool = load_pool()

    if args.fill_anime_jp:
        print("🇯🇵 補日文作品名...")
        n = fill_anime_jp(pool)
        save_pool(pool)
        have = sum(1 for s in pool["songs"] if s.get("anime_jp"))
        print(f"\n✅ 新增 {n} 首，pool 現有 {have} 首帶日文作品名")
        return

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
