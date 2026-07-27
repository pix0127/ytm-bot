"""YTM 搜尋結果配對 — 歌手 token 當閘門，再用歌名相似度在通過的候選裡排序。

兩層的分工:
- 歌手閘門擋掉別人的同名曲(「Monologue Agemasu / Hinaki Yano」被塞成 Ayane 的同名曲)。
- 歌名相似度擋掉同一歌手的不同曲——光靠歌手閘門時,無職轉生的 9 首歌(都是 Yuiko Ohara
  唱的)會全部被配到同一支影片,因為第一名的歌手永遠對得上。

pool 的歌名是羅馬字、YTM 常是日文,所以比對前先用 pykakasi 轉寫
(「決意の唄」→ ketsuinota)。但相似度只用來**排序**,不當硬門檻:官方英譯
(「泡」→ Bubbles、「たられば」→ if)相似度會是 0,當門檻會誤殺。
"""
import difflib
import re
import unicodedata

import pykakasi

_kks = pykakasi.kakasi()

STRONG_TITLE = 0.75  # 歌名證據要夠強才推翻 YTM 的相關性排序;低分時的排序是雜訊

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
        # 拼寫差一兩個字母（Macaroni Enpitsu↔Macaroni Empitsu）token 交集會低到擋掉,改看整串相似度
        if len(tj) >= 6 and len(aj) >= 6 and difflib.SequenceMatcher(None, tj, aj).ratio() >= 0.85:
            return True
    return False


def _romaji(s: str) -> str:
    """日文轉羅馬字後正規化;本來就是羅馬字的字串等同只做正規化。"""
    r = "".join(x["hepburn"] for x in _kks.convert(s or "")).lower()
    return re.sub(r"[^a-z0-9]", "", _canon(r))


def title_score(target_title: str, result_title: str) -> float:
    a, b = _romaji(target_title), _romaji(result_title)
    if not a or not b:          # 例如「○✕△□」轉寫後是空的,無從比較
        return 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    # 包含要夠長才算數:8 字以上的子串幾乎不會巧合(「seishunohka」),短的會誤中
    # (「awa」⊂「shiwaawase」)。不用長度比例當條件——YTM 標題常有 feat./括號等裝飾,
    # 比例會把正確的包含否決掉。完全相同的字串由 SequenceMatcher 給 1.0,不需特例。
    if short in long and len(short) >= 8:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def pick_result(target_artist: str, results: list[dict], target_title: str = "",
                taken: set | None = None) -> dict | None:
    """歌手閘門過濾後,歌名夠像就選它,否則沿用 YTM 的相關性排序。

    低分時不做排序也不拒絕:歌名對不上有兩種可能——配錯了,或是官方英譯
    (「泡」→ Bubbles)。這兩種在分數上分不出來,而 resolve_pool 會刪掉配不到的歌,
    所以寧可維持既有行為,由 taken(撞號偵測)當最後一道防線。
    """
    taken = taken or set()
    # 看到第 10 名:正解常常不在前 5(「Seishun Ohka」的正版排第 9,前面全是卡拉OK版)
    cands = [r for r in results[:10] if r.get("videoId") and r["videoId"] not in taken]
    if _artist_tokens(target_artist):  # 有歌手資訊才過閘門;沒有就全部當候選
        cands = [r for r in cands if _artist_ok(target_artist, r)]
    if not cands:
        return None
    best = max(cands, key=lambda r: title_score(target_title, r.get("title", "")))
    if title_score(target_title, best.get("title", "")) >= STRONG_TITLE:
        return best
    return cands[0]


def resolve_video_id(yt, song: dict, taken: set | None = None) -> str | None:
    """優先用 pool 存好的 video_id;沒有才 fallback 去搜(歌手閘門 + 歌名排序)。

    taken: 已被 pool 其他歌佔用的 videoId。一支影片只能是一首歌,傳進來可避免撞號。
    """
    vid = song.get("video_id")
    if vid:
        return vid
    query = f"{song.get('title', '')} {song.get('artist', '')}"
    match = pick_result(song.get("artist", ""), yt.search(query, filter="songs", limit=5),
                        target_title=song.get("title", ""), taken=taken)
    return match["videoId"] if match else None
