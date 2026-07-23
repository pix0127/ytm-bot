#!/usr/bin/env python3
"""從 Firefox 的 cookies.sqlite 直接擷取 YouTube cookie → browser.json。

不需啟動瀏覽器（讀磁碟上的 sqlite 檔即可），純標準庫。
搭配 NAS 上的 jlesage/firefox 容器 profile 使用。

用法:
    python3 extract_cookie.py <cookies.sqlite 路徑> [輸出 browser.json 路徑]
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

SRC = sys.argv[1] if len(sys.argv) > 1 else None
OUT = sys.argv[2] if len(sys.argv) > 2 else "browser.json"

if not SRC or not os.path.exists(SRC):
    sys.exit(f"找不到 cookies.sqlite：{SRC}")

# 複製一份再讀，避免 Firefox 正在跑時鎖住 DB
tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name
shutil.copy(SRC, tmp)
try:
    con = sqlite3.connect(tmp)
    rows = con.execute(
        "SELECT name, value FROM moz_cookies WHERE host LIKE '%youtube.com'"
    ).fetchall()
    con.close()
finally:
    os.unlink(tmp)

names = {n for n, _ in rows}
if "SAPISID" not in names:
    sys.exit(f"cookies.sqlite 裡沒有 SAPISID（找到 {len(rows)} 筆 youtube cookie）；"
             "請先在該 Firefox 登入 music.youtube.com")

cookie_str = "; ".join(f"{n}={v}" for n, v in rows)
headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "X-Goog-AuthUser": "0",
    "x-origin": "https://music.youtube.com",
    "Origin": "https://music.youtube.com",
    "authorization": "SAPISIDHASH placeholder_recomputed_at_runtime",
    "Cookie": cookie_str,
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(headers, f, ensure_ascii=False, indent=2)
print(f"已寫出 {OUT}（{len(rows)} 個 youtube cookie，SAPISID=有）")
