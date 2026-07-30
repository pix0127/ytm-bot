#!/usr/bin/env python3.12
"""YT Music 歌單寫入（ytmusicapi / browser cookie）。

取代先前的 dataapi.py（官方 Data API v3 + OAuth）。改用 ytmusicapi 的理由:

- **快得多**:add_playlist_items 接受整批 video_id,一次呼叫加完;Data API 的
  playlistItems.insert 一次只能加一首,而且 YouTube 對同一歌單的寫入有鎖,
  無法並行(並行會回 409 SERVICE_UNAVAILABLE 而靜默掉歌)。
  實測 20 首:ytmusicapi 約 3.2s(0.67 刪 + 0.90 建 + 1.63 加),Data API 約 20s。
- **只有一套認證**:不必再維護 OAuth client、device flow 與 token refresh。

代價:cookie 失效時建歌單也會失敗（先前走 OAuth 時不受影響）。cookie 的自動
續期與失效通知見 ytm/cookie.py 與 deploy/nas-firefox/。
"""
from .config import AUTH_FILE


def _client():
    from ytmusicapi import YTMusic
    return YTMusic(AUTH_FILE)


def new_playlist(old_id: str | None, title: str, description: str = "") -> str:
    """刪掉舊歌單、開一個新的，回新的 playlist_id。

    不重用舊歌單是因為清空是 O(N)（要逐首移除），整個刪掉是 O(1)。
    代價是歌單 URL 每次都會變。不需要曲目清單，所以可以跟選曲並行跑。
    """
    yt = _client()
    if old_id:
        try:
            yt.delete_playlist(old_id)
        except Exception:
            pass          # 已經不存在就算了，不該讓它擋住建新的
    return yt.create_playlist(title, description or title, privacy_status="PRIVATE")


def fill_playlist(pid: str, video_ids: list[str], skip: set | None = None) -> dict:
    """把曲目加進歌單（去重、跳過 skip）。整批一次呼叫。"""
    skip = skip or set()
    seen: set[str] = set()
    wanted, skipped, dups = [], 0, 0
    for vid in video_ids:
        if vid in skip:
            skipped += 1
        elif vid in seen:
            dups += 1
        else:
            seen.add(vid)
            wanted.append(vid)

    added, failed = 0, 0
    if wanted:
        try:
            _client().add_playlist_items(pid, wanted, duplicates=False)
            added = len(wanted)
        except Exception:
            # 整批失敗（多為其中有下架/Music-only 的曲目）→ 退回逐首，把壞的挑出來
            yt = _client()
            for vid in wanted:
                try:
                    yt.add_playlist_items(pid, [vid], duplicates=False)
                    added += 1
                except Exception:
                    failed += 1
    return {"playlist_id": pid,
            "url": f"https://music.youtube.com/playlist?list={pid}",
            "added": added, "failed": failed, "skipped": skipped, "dups": dups}
