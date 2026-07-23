#!/usr/bin/env python3.12
"""
新番歌單產生器 — 自動抓本季新番 OP/ED，產生成 YouTube Music 歌單

用法:
  # 第一次先做 browser 認證 (貼上 music.youtube.com 的 request headers)
  python3 anime_playlist_gen.py --auth

  # 產生本季歌單
  python3 anime_playlist_gen.py

  # 指定季別
  python3 anime_playlist_gen.py --season Winter --year 2026

  # 只列出不寫入 YTM
  python3 anime_playlist_gen.py --dry-run

資料來源:
  - AnimeThemes.moe API (OP/ED 歌曲)
  - YouTube Music via ytmusicapi
"""

import argparse
import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime

from .config import AUTH_FILE

# ─── API helpers ──────────────────────────────────────────────────

def api_get(url: str) -> dict:
    """Generic GET 請求"""
    req = urllib.request.Request(url, headers={"User-Agent": "anime-playlist-gen/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_current_season() -> tuple[str, int]:
    """計算當前季別"""
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


def fetch_anime_themes(season: str, year: int) -> list[dict]:
    """
    從 AnimeThemes.moe API 取得該季所有番劇的 OP/ED

    回傳: [{anime, type, sequence, song_title, artist, slug}]
    """
    results = []
    page = 1

    while True:
        params = {
            "filter[year]": str(year),
            "filter[season]": season,
            "include": "animethemes.song.artists,animethemes.animethemeentries.videos",
            "page[size]": "50",
            "page[number]": str(page),
            "sort": "id",
        }
        url = "https://api.animethemes.moe/anime?" + urllib.parse.urlencode(params)
        data = api_get(url)

        for anime in data.get("anime", []):
            anime_name = anime["name"]
            for theme in anime.get("animethemes", []):
                song = theme.get("song") or {}
                artists = song.get("artists") or []
                artist_names = ", ".join(a.get("name", "") for a in artists)

                results.append({
                    "anime": anime_name,
                    "type": theme["type"],  # OP / ED / IN
                    "sequence": theme.get("sequence", 1),
                    "slug": theme.get("slug", ""),
                    "song_title": song.get("title", ""),
                    "artist": artist_names,
                    "search_query": f"{song.get('title', '')} {artist_names} {anime_name}",
                })

        # 檢查是否有下一頁
        links = data.get("links", {})
        if not links.get("next"):
            break
        page += 1

    return results


def print_playlist(songs: list[dict], title: str):
    """列出歌曲清單（乾跑模式）"""
    print(f"\n🎵 {title}")
    print(f"   共 {len(songs)} 首\n")
    for i, s in enumerate(songs, 1):
        print(f"  {i:2d}. [{s['type']}] {s['song_title']}")
        print(f"       Artist: {s['artist']}")
        print(f"       Anime:  {s['anime']}")
        print(f"       Search: {s['search_query']}")
        print()


def create_ytm_playlist(songs: list[dict], season: str, year: int,
                         auth_file: str = AUTH_FILE) -> str | None:
    """使用 ytmusicapi 建立 YouTube Music 歌單並加歌"""
    from ytmusicapi import YTMusic

    yt = YTMusic(auth_file)

    playlist_title = f"{year} {season} 新番歌單"
    playlist_desc = (
        f"自動產生的 {year} {season} 新番 OP/ED 歌單\n"
        f"資料來源: AnimeThemes.moe"
    )

    # 建立歌單
    playlist_id = yt.create_playlist(playlist_title, playlist_desc)
    print(f"✅ 已建立歌單: {playlist_title} (ID: {playlist_id})")

    # 逐首搜尋並加入
    added = 0
    not_found = []

    for s in songs:
        query = s["search_query"]
        results = yt.search(query, filter="songs", limit=3)

        if results:
            # 取第一筆
            video_id = results[0]["videoId"]
            yt.add_playlist_items(playlist_id, [video_id])
            added += 1
            print(f"  ✅ + {s['song_title']} — {results[0]['title']} / {results[0].get('artists',[{}])[0].get('name','?')}")
        else:
            not_found.append(s["search_query"])
            print(f"  ❌ 找不到: {s['search_query']}")

    print(f"\n📊 結果: {added}/{len(songs)} 首已加入")
    if not_found:
        print(f"   未找到 ({len(not_found)} 首):")
        for q in not_found:
            print(f"     - {q}")

    playlist_url = f"https://music.youtube.com/playlist?list={playlist_id}"
    return playlist_url


# ─── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="新番 OP/ED 歌單產生器")
    parser.add_argument("--season", choices=["Winter", "Spring", "Summer", "Fall"],
                        help="季別 (預設: 當前)")
    parser.add_argument("--year", type=int, help="年份 (預設: 當前)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列出歌單，不寫入 YouTube Music")
    parser.add_argument("--auth", action="store_true",
                        help="執行 ytmusicapi browser 認證 (產生 browser.json)")
    args = parser.parse_args()

    if args.auth:
        from ytmusicapi import setup
        print("🔐 啟動 browser 認證流程...")
        print("   請在瀏覽器登入 music.youtube.com，從 DevTools 複製一個")
        print("   /youtubei/v1/ 請求的 request headers，貼到下方後按兩次 Enter")
        setup(filepath=AUTH_FILE)
        print(f"✅ 已產生 {AUTH_FILE}")
        return

    season, year = args.season or get_current_season()[0], args.year or get_current_season()[1]
    print(f"📺 取得 {year} {season} 新番 OP/ED...")

    songs = fetch_anime_themes(season, year)

    if not songs:
        print("⚠️  沒有找到任何歌曲")
        return

    title = f"{year} {season} 新番 OP/ED（共 {len(songs)} 首）"

    if args.dry_run:
        print_playlist(songs, title)
        return

    # 實際建立 YTM 歌單
    try:
        url = create_ytm_playlist(songs, season, year)
        if url:
            print(f"\n🔗 歌單連結: {url}")
    except FileNotFoundError:
        print("\n⚠️  找不到 browser.json，請先執行:")
        print("   python3 anime_playlist_gen.py --auth")
        print("\n然後再次執行產生歌單。")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
