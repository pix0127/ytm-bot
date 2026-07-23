"""官方 YouTube Data API v3 的播放清單寫入helpers（OAuth，免 cookie）。
供 daily_pick / telegram_bot 共用。"""
import requests

from .oauth import get_access_token

V3 = "https://www.googleapis.com/youtube/v3"


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}


def create_playlist(title: str, description: str = "", privacy: str = "private") -> str:
    r = requests.post(f"{V3}/playlists", headers=_headers(), params={"part": "snippet,status"},
                      json={"snippet": {"title": title, "description": description},
                            "status": {"privacyStatus": privacy}})
    r.raise_for_status()
    return r.json()["id"]


def add_video(playlist_id: str, video_id: str) -> bool:
    r = requests.post(f"{V3}/playlistItems", headers=_headers(), params={"part": "snippet"},
                      json={"snippet": {"playlistId": playlist_id,
                                        "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
    return r.ok


def delete_playlist(playlist_id: str) -> bool:
    r = requests.delete(f"{V3}/playlists", headers=_headers(), params={"id": playlist_id})
    return r.status_code in (200, 204)


def build_playlist(title: str, video_ids: list[str], description: str = "",
                   skip: set | None = None) -> dict:
    """建歌單並加入 video_ids（skip 內的跳過）。回 {playlist_id, url, added, failed, skipped}."""
    skip = skip or set()
    pid = create_playlist(title, description)
    added = failed = skipped = 0
    for vid in video_ids:
        if vid in skip:
            skipped += 1
            continue
        if add_video(pid, vid):
            added += 1
        else:
            failed += 1
    return {
        "playlist_id": pid,
        "url": f"https://music.youtube.com/playlist?list={pid}",
        "added": added, "failed": failed, "skipped": skipped,
    }
