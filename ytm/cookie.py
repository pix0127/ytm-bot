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


def check() -> tuple[bool, str]:
    """cookie 還能認證嗎。用 library 端點判斷——search 免登入,測不出失效。"""
    if not os.path.exists(AUTH_FILE):
        return False, f"找不到 {os.path.basename(AUTH_FILE)}"
    try:
        from ytmusicapi import YTMusic
        yt = YTMusic(AUTH_FILE)
        subs = yt.get_library_subscriptions(limit=100)
        pls = yt.get_library_playlists(limit=25)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if not subs and not pls:
        return False, "library 端點回 0 筆（cookie 已失去登入狀態）"
    return True, f"訂閱 {len(subs)} 位、歌單 {len(pls)} 個"


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


if __name__ == "__main__":
    main()
