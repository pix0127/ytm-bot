"""YTM 搜尋結果配對 — 信任 YTM 相關性排序，用歌手 token 當閘門擋掉錯配。

避免像「Monologue Agemasu / Hinaki Yano」被塞成別人（Ayane）的同名曲：
取 YTM 排序中第一個「歌手對得上」的結果；都對不上就回 None（視為找不到）。"""
import re
import unicodedata

# 收合日文羅馬拼音長音變體（Oohara↔Ohara、Kenshou↔Kensho…），兩邊同樣處理故不影響原本相等的比對
_LONG_VOWEL = (("ou", "o"), ("oo", "o"), ("uu", "u"), ("ii", "i"), ("ee", "e"), ("aa", "a"))


def _canon(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    for a, b in _LONG_VOWEL:
        s = s.replace(a, b)
    return s


def _artist_tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", _canon(s)))


def _artist_joined(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _canon(s))


def _artist_ok(target_artist: str, r: dict) -> bool:
    tt = _artist_tokens(target_artist)
    tj = _artist_joined(target_artist)
    for a in (r.get("artists") or []):
        at = _artist_tokens(a.get("name", ""))
        if not at:
            continue
        inter = tt & at
        if inter and (inter == tt or inter == at or len(inter) / len(tt | at) >= 0.5):
            return True
        aj = _artist_joined(a.get("name", ""))
        if len(tj) >= 6 and (tj in aj or aj in tj):  # 去空格後包含（Regal Lily↔Regallily）
            return True
    return False


def pick_result(target_artist: str, results: list[dict]) -> dict | None:
    if not _artist_tokens(target_artist):  # pool 無歌手資訊 → 無法判別，信任第一名
        return results[0] if results else None
    for r in results[:5]:
        if _artist_ok(target_artist, r):
            return r
    return None


def resolve_video_id(yt, song: dict) -> str | None:
    """優先用 pool 存好的 video_id；沒有才 fallback 去搜（歌手閘門過濾）。"""
    vid = song.get("video_id")
    if vid:
        return vid
    query = f"{song.get('title', '')} {song.get('artist', '')}"
    match = pick_result(song.get("artist", ""), yt.search(query, filter="songs", limit=5))
    return match["videoId"] if match else None
