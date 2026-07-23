#!/usr/bin/env python3
"""在 Windows 上跑：讀 Edge 的 YouTube Music cookie，產生 browser.json。

用法（在你登入了 music.youtube.com 的 Windows 機器上）：
    pip install browser_cookie3
    python refresh_ytm_cookie.py                # 產生到當前目錄
    python refresh_ytm_cookie.py "Z:\\ytm\\browser.json"   # 直接寫進共享資料夾

不用開 DevTools、不用 Copy as cURL。cookie 失效時重跑這支即可。
"""
import json
import sys

import browser_cookie3

OUT = sys.argv[1] if len(sys.argv) > 1 else "browser.json"

# Edge 讀不到就 fallback 到 Chrome
for loader in (browser_cookie3.edge, browser_cookie3.chrome):
    try:
        cj = loader(domain_name="youtube.com")
        cookies = list(cj)
        if any(c.name == "SAPISID" for c in cookies):
            break
    except Exception:
        cookies = []
else:
    sys.exit("讀不到含 SAPISID 的 YouTube cookie；請確認已在 Edge/Chrome 登入 music.youtube.com")

cookie_str = "; ".join(f"{c.name}={c.value}" for c in cookies)

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "X-Goog-AuthUser": "0",
    "x-origin": "https://music.youtube.com",
    "Origin": "https://music.youtube.com",
    # ytmusicapi 1.12 靠 authorization 內含 SAPISIDHASH 才判成 browser auth；
    # 真正的值會在每次請求時用 cookie 裡的 SAPISID 重算，這裡放 placeholder 即可。
    "authorization": "SAPISIDHASH placeholder_recomputed_at_runtime",
    "Cookie": cookie_str,
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(headers, f, ensure_ascii=False, indent=2)

print(f"已寫出 {OUT}（{len(cookies)} 個 cookie，SAPISID={'有' if 'SAPISID=' in cookie_str else '無'}）")
