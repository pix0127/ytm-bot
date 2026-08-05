#!/usr/bin/env python3.12
"""browser.json 的生命週期:健康檢查 + 從 Firefox profile 重新擷取。

為什麼需要這支:cookie 幾天就會失效,而失效是**靜默的**——collect --artists-only
會回報「完成」然後什麼都沒收。所以要能主動檢查。

為什麼只能靠瀏覽器重新擷取:認證必要的 __Secure-1PSIDTS 由 Chrome 的
/RotateCookies(綁定裝置)輪替,任何請求都不會讓 Google 補發它(實測過:
GET 首頁只會回新的 SIDCC 家族)。所以沒有純 Python 續命的辦法,
只能從一個真的有登入的瀏覽器 profile 撈。

搭配 deploy/nas-firefox/ 的容器使用:在那個網頁版 Firefox 登入一次,
之後跑 --extract 讀它 profile 的 sqlite（不需啟動瀏覽器）。

用法:
  python3 -m ytm.cookie --check                    # 檢查現有 cookie 還活著嗎
  python3 -m ytm.cookie --extract <profile 或 sqlite 路徑>   # 重新擷取並寫入
"""
import argparse
import json
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime

from .config import AUTH_FILE, BACKUP_DIR

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "X-Goog-AuthUser": "0",
    "x-origin": "https://music.youtube.com",
    "Origin": "https://music.youtube.com",
    # ytmusicapi 靠這個 key 判定是 browser auth;真正的 SAPISIDHASH 每次請求重算。
    "authorization": "SAPISIDHASH placeholder_recomputed_at_runtime",
}
# 認證必要的 cookie。缺 __Secure-1PSIDTS 會直接失敗(實測:拿掉就回 0 筆)。
REQUIRED = ("SAPISID", "__Secure-1PSID", "__Secure-1PSIDTS")


def default_profile() -> str:
    """預設去 deploy/nas-firefox/ff-profile 找，免得還要設定一次路徑。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("YTM_FF_PROFILE") or os.path.join(root, "deploy", "nas-firefox", "ff-profile")


def sync_if_newer(path: str | None = None) -> str | None:
    """profile 的 cookie 比 browser.json 新就撈過來。

    這是平常的主要路徑:讓那個 Firefox 定期自己開一下 music.youtube.com,
    它會替自己輪替憑證,我們只要跟著同步——不必等失效、不必人工介入。
    回同步後的訊息;沒事做或沒得同步就回 None。
    """
    path = path or default_profile()
    src = find_sqlite(path)
    if not src:
        return None
    if os.path.exists(AUTH_FILE) and os.path.getmtime(src) <= os.path.getmtime(AUTH_FILE):
        return None
    try:
        headers = extract(src)
    except ValueError:
        return None  # profile 自己也沒登入,交給 check() 去觸發通知
    save(headers)
    return f"已從 Firefox profile 同步新的 cookie（{os.path.basename(src)}）"


def find_sqlite(path: str) -> str | None:
    """給 sqlite 檔就直接用;給目錄就往下找 cookies.sqlite（取最新的那個 profile）。

    用 os.walk 而不是 glob:Firefox profile 在 .mozilla/ 底下,
    而 glob 預設不匹配以點開頭的隱藏目錄。
    """
    if os.path.isfile(path):
        return path
    hits = [os.path.join(root, "cookies.sqlite")
            for root, _, files in os.walk(path) if "cookies.sqlite" in files]
    return max(hits, key=os.path.getmtime) if hits else None


def extract(path: str) -> dict:
    """從 Firefox profile 的 cookies.sqlite 產生 browser.json 的內容。"""
    src = find_sqlite(path)
    if not src:
        raise FileNotFoundError(f"找不到 cookies.sqlite：{path}")
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name
    shutil.copy(src, tmp)  # Firefox 在跑時會鎖住 DB，複製一份再讀
    try:
        con = sqlite3.connect(tmp)
        rows = con.execute("SELECT name, value FROM moz_cookies WHERE host LIKE '%youtube.com'").fetchall()
        con.close()
    finally:
        os.unlink(tmp)

    names = {n for n, _ in rows}
    missing = [n for n in REQUIRED if n not in names]
    if missing:
        raise ValueError(f"profile 裡缺少 {', '.join(missing)}（共 {len(rows)} 個 youtube cookie）"
                         f"；請先在該瀏覽器登入 music.youtube.com")
    return {**HEADERS, "Cookie": "; ".join(f"{n}={v}" for n, v in rows)}


def save(headers: dict) -> str:
    """寫入 AUTH_FILE，舊的先備份。回備份路徑。"""
    backup = ""
    if os.path.exists(AUTH_FILE):
        backup = os.path.join(BACKUP_DIR, f"browser.json.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy(AUTH_FILE, backup)
    with open(AUTH_FILE, "w") as f:
        json.dump(headers, f, ensure_ascii=False, indent=2)
    return backup


def check(attempts: int = 3) -> tuple[bool, str]:
    """cookie 還能認證嗎。用 library 端點判斷——search 免登入,測不出失效。

    傳輸失敗要重試:實測從 NAS 連過去,同一組有效 cookie 連跑 8 次有 6 次在
    10～14 秒後回空 body(JSONDecodeError),成功的那幾次只要 5 秒——是線路慢
    導致回應截斷,不是認證問題。一次失敗就判失效的後果是 scheduler 的 ensure
    每 10 分鐘誤判一次,把 Firefox 整天開在那裡等一個不需要的登入。

    「請求成功但 library 回 0 筆」才是真的失效,那種重試不會變好,直接回報。
    """
    if not os.path.exists(AUTH_FILE):
        return False, f"找不到 {os.path.basename(AUTH_FILE)}"
    last = ""
    for i in range(attempts):
        try:
            from ytmusicapi import YTMusic
            yt = YTMusic(AUTH_FILE)
            # 歌單有東西就已經證明是登入狀態,不必再查訂閱——這條路徑省掉一半請求。
            # 慢線路上每個 library 查詢動輒 10 秒以上(還要加初始化抓首頁的 329KB),
            # 少一個請求就少一次被截斷的機會。
            pls = yt.get_library_playlists(limit=25)
            if pls:
                return True, f"歌單 {len(pls)} 個"
            subs = yt.get_library_subscriptions(limit=100)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            if i < attempts - 1:
                time.sleep(2 ** i)          # 1s → 2s
            continue
        if not subs:
            return False, "library 端點回 0 筆（cookie 已失去登入狀態）"
        return True, f"訂閱 {len(subs)} 位"
    return False, last


def main():
    ap = argparse.ArgumentParser(description="browser.json 健康檢查與重新擷取")
    ap.add_argument("--check", action="store_true", help="檢查現有 cookie")
    ap.add_argument("--extract", metavar="PATH", help="從 Firefox profile 或 cookies.sqlite 擷取")
    args = ap.parse_args()

    if args.extract:
        headers = extract(args.extract)
        backup = save(headers)
        print(f"✅ 已寫入 {AUTH_FILE}" + (f"（舊檔備份於 {os.path.basename(backup)}）" if backup else ""))
    if args.check or not args.extract:
        alive, msg = check()
        print(f"{'✅ cookie 正常' if alive else '❌ cookie 失效'}：{msg}")
        raise SystemExit(0 if alive else 1)  # 給 firefox-ctl.sh ensure 判斷用


if __name__ == "__main__":
    main()
