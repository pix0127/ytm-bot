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


def new_playlist(old_id: str | None, title: str, description: str = "") -> str:
    """刪掉舊歌單、開一個新的,回新的 playlist_id。

    不重用舊歌單是為了速度:逐首清空是 O(N)(每首約 0.8s,20 首要 16s),
    整個刪掉是 O(1)(約 1s)。代價是歌單 URL 每次都會變。
    不需要 picks,所以可以跟選曲並行跑。
    """
    if old_id:
        delete_playlist(old_id)
    return create_playlist(title, description)


def fill_playlist(pid: str, video_ids: list[str], skip: set | None = None) -> dict:
    """把曲目加進歌單(去重、跳過 skip)。

    只能序列加:YouTube 對同一歌單的寫入有鎖,並行 insert 會回 409 SERVICE_UNAVAILABLE
    而靜默掉歌(實測 8 worker 只成功 1/8)。
    """
    return _add_all(pid, video_ids, skip or set())
