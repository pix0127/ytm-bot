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


def playlist_exists(playlist_id: str) -> bool:
    r = requests.get(f"{V3}/playlists", headers=_headers(), params={"part": "id", "id": playlist_id})
    return r.ok and bool(r.json().get("items"))


def _item_ids(playlist_id: str) -> list[str]:
    ids, page = [], None
    while True:
        params = {"part": "id", "playlistId": playlist_id, "maxResults": 50}
        if page:
            params["pageToken"] = page
        r = requests.get(f"{V3}/playlistItems", headers=_headers(), params=params)
        if not r.ok:
            break
        j = r.json()
        ids += [it["id"] for it in j.get("items", [])]
        page = j.get("nextPageToken")
        if not page:
            break
    return ids


def clear_playlist(playlist_id: str):
    for iid in _item_ids(playlist_id):
        requests.delete(f"{V3}/playlistItems", headers=_headers(), params={"id": iid})


def update_meta(playlist_id: str, title: str, description: str = "") -> bool:
    r = requests.put(f"{V3}/playlists", headers=_headers(), params={"part": "snippet"},
                     json={"id": playlist_id, "snippet": {"title": title, "description": description}})
    return r.ok


def _add_all(pid: str, video_ids: list[str], skip: set) -> dict:
    added = failed = skipped = dups = 0
    seen = set()
    for vid in video_ids:
        if vid in skip:
            skipped += 1
            continue
        if vid in seen:
            dups += 1
            continue
        seen.add(vid)
        if add_video(pid, vid):
            added += 1
        else:
            failed += 1
    return {"playlist_id": pid, "url": f"https://music.youtube.com/playlist?list={pid}",
            "added": added, "failed": failed, "skipped": skipped, "dups": dups}


def upsert_playlist(existing_id: str | None, title: str, video_ids: list[str],
                    description: str = "", skip: set | None = None) -> dict:
    """重用同一個歌單:存在則清空+更新標題,不存在(或沒給)則新建;再加入去重後的曲目。"""
    skip = skip or set()
    if existing_id and playlist_exists(existing_id):
        clear_playlist(existing_id)
        update_meta(existing_id, title, description)
        pid = existing_id
    else:
        pid = create_playlist(title, description)
    return _add_all(pid, video_ids, skip)


def build_playlist(title: str, video_ids: list[str], description: str = "",
                   skip: set | None = None) -> dict:
    """建新歌單並加入 video_ids（skip 內的跳過）。回 {playlist_id, url, added, failed, skipped}."""
    skip = skip or set()
    pid = create_playlist(title, description)
    added = failed = skipped = dups = 0
    seen = set()
    for vid in video_ids:
        if vid in skip:
            skipped += 1
            continue
        if vid in seen:      # 去重:同一 videoId 不重複加入
            dups += 1
            continue
        seen.add(vid)
        if add_video(pid, vid):
            added += 1
        else:
            failed += 1
    return {
        "playlist_id": pid,
        "url": f"https://music.youtube.com/playlist?list={pid}",
        "added": added, "failed": failed, "skipped": skipped, "dups": dups,
    }
