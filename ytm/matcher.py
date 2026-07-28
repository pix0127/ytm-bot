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


def _romaji_words(s: str) -> str:
    """轉羅馬字並保留詞界(「鈴木このみ」→「suzuki konomi」)。歌手比對要靠 token,
    不能像 _romaji 那樣把空白吃掉,否則跟 pool 的「Konomi Suzuki」對不起來。"""
    return " ".join(x["hepburn"] for x in _kks.convert(s or ""))


def _artist_tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", _canon(_romaji_words(s))))


def _artist_joined(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _canon(_romaji_words(s)))


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


def _title_parts(s: str) -> list[str]:
    """YTM 標題多半是「日文名 - 羅馬字名」,有時再掛括號版本註記。
    拆開逐段比,否則「Nekohi」對上整串「猫日 - Catdays」只有 0.55——
    但它其實跟第一段「猫日」(nekohi) 完全相同。"""
    out = {s}
    for p in re.split(r"\s+[-–—/／]\s+", s):
        out.add(p)
        out.add(re.sub(r"[(（\[【].*", "", p))
    return [p.strip() for p in out if p.strip()]


def title_score(target_title: str, result_title: str) -> float:
    return max(_score_one(target_title, p) for p in _title_parts(result_title or " "))


def _score_one(target_title: str, result_title: str) -> float:
    a, b = _romaji(target_title), _romaji(result_title)
    if not a or not b:
        # 純符號的歌名(「○✕△□」「∞」)轉寫後是空的,退回比原字串,
        # 否則它們永遠拿 0 分、永遠被判定為錯配。
        ra = unicodedata.normalize("NFKC", target_title or "").strip()
        rb = unicodedata.normalize("NFKC", result_title or "").strip()
        return 1.0 if ra and ra == rb else 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    # 包含要夠長才算數:8 字以上的子串幾乎不會巧合(「seishunohka」),短的會誤中
    # (「awa」⊂「shiwaawase」)。不用長度比例當條件——YTM 標題常有 feat./括號等裝飾,
    # 比例會把正確的包含否決掉。完全相同的字串由 SequenceMatcher 給 1.0,不需特例。
    if short in long and len(short) >= 8:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _views(r: dict) -> int:
    m = re.match(r"([\d.]+)\s*([KMB])?", str(r.get("views") or "").replace(",", ""))
    return int(float(m.group(1)) * {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2) or "", 1)) if m else 0


def _seconds(r: dict) -> int:
    p = str(r.get("duration") or "").split(":")
    return int(p[0]) * 60 + int(p[1]) if len(p) == 2 and all(x.isdigit() for x in p) else 0


def _rank(target_title: str, r: dict) -> tuple:
    """歌名分優先;同分時選點閱高的(正式版 vs 各種 remix/翻唱),再同分選長的
    (「(TV SIZE)」與正版點閱四捨五入後常常一樣,長度才分得出來)。"""
    return (-title_score(target_title, r.get("title", "")), -_views(r), -_seconds(r))


def pick_result(target_artist: str, results: list[dict], target_title: str = "",
                taken: set | None = None) -> dict | None:
    """歌手閘門過濾後取歌名最像的;不夠像就回 None(視為找不到)。"""
    taken = taken or set()
    gate = bool(_artist_tokens(target_artist))
    cands = []
    for r in results:
        if not r.get("videoId") or r["videoId"] in taken:
            continue
        if r.get("_from_anime"):
            # 「日文作品名 + OP/ED」的結果已經被作品限定範圍,不再用歌手把關——
            # 動畫歌掛名的常常不是 pool 記的人(團體名、「X from Y」、聲優 vs 角色),
            # 硬比會擋掉正解。改為要求歌名夠像。
            if title_score(target_title, r.get("title", "")) >= STRONG_TITLE:
                cands.append(r)
        elif not gate or _artist_ok(target_artist, r):
            cands.append(r)
    if not cands:
        return None
    best = min(cands, key=lambda r: _rank(target_title, r))
    # 歌名不夠像就放棄。以前這裡會退回 YTM 第一名,而那正是錯配的來源:
    # YTM 沒有這首歌時,第一名就是同歌手的別首歌,收下來只是把錯誤藏起來。
    # 代價是官方英譯(「泡」→ Bubbles)也會被放棄,但寧可少一首,不要放錯一首。
    if title_score(target_title, best.get("title", "")) < STRONG_TITLE:
        return None
    return best


def search_queries(song: dict) -> list[str]:
    """有日文作品名時多搜一輪:羅馬字歌名搜不到的曲子(YTM 只有日文標題),
    用「日文作品名 + OP/ED」常常能撈到整批該作品的歌,再靠歌名分挑出來。"""
    qs = []
    jp = song.get("anime_jp")
    if jp:
        qs.append(f"{jp} {song.get('type', '')}".strip())
    qs.append(f"{song.get('title', '')} {song.get('artist', '')}".strip())
    return qs


def resolve_video_id(yt, song: dict, taken: set | None = None) -> str | None:
    """優先用 pool 存好的 video_id;沒有才 fallback 去搜(歌手閘門 + 歌名排序)。

    taken: 已被 pool 其他歌佔用的 videoId。一支影片只能是一首歌,傳進來可避免撞號。
    """
    vid = song.get("video_id")
    if vid:
        return vid
    results, seen = [], set()
    queries = search_queries(song)
    for i, q in enumerate(queries):
        from_anime = bool(song.get("anime_jp")) and i == 0
        try:
            hits = yt.search(q, filter="songs", limit=10)
        except Exception:
            continue
        # 每輪只取前 10:正解常常不在前 5(「Seishun Ohka」的正版排第 9,前面全是卡拉OK版)
        for r in hits[:10]:
            if r.get("videoId") and r["videoId"] not in seen:
                seen.add(r["videoId"])
                r["_from_anime"] = from_anime
                results.append(r)
    match = pick_result(song.get("artist", ""), results,
                        target_title=song.get("title", ""), taken=taken)
    return match["videoId"] if match else None
