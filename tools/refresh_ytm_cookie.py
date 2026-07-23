#!/usr/bin/env python3
"""在 Windows 上跑：讀瀏覽器的 YouTube Music cookie，產生 browser.json。

用法（在你登入了 music.youtube.com 的機器上）：
    pip install browser_cookie3
    python refresh_ytm_cookie.py                # 產生到當前目錄
    python refresh_ytm_cookie.py "Z:\\ytm\\browser.json"   # 直接寫進共享資料夾

⚠️ Chrome/Edge 127+（2024/7 起）用 App-Bound Encryption，browser_cookie3 解不開 →
   請改用 **Firefox**：在 Firefox 登入 music.youtube.com 後再跑這支（本程式會優先讀 Firefox）。
   若堅持用 Chrome/Edge 且讀不到，只能退回 DevTools「Copy as cURL」手動貼。

cookie 失效時重跑這支即可。
"""
import json
import sys

import browser_cookie3

OUT = sys.argv[1] if len(sys.argv) > 1 else "browser.json"

# Firefox 沒有 App-Bound Encryption，最可靠 → 優先；Chrome/Edge 在 127+ 可能解不開
cookies = []
for name, loader in (("firefox", browser_cookie3.firefox),
                     ("edge", browser_cookie3.edge),
                     ("chrome", browser_cookie3.chrome)):
    try:
        cj = loader(domain_name="youtube.com")
        got = list(cj)
        if any(c.name == "SAPISID" for c in got):
            cookies = got
            print(f"✅ 從 {name} 讀到 cookie")
            break
    except Exception as e:
        print(f"   {name}: 讀取失敗（{type(e).__name__}）")
if not cookies:
    sys.exit(
        "讀不到含 SAPISID 的 YouTube cookie。\n"
        "多半是 Chrome/Edge 127+ 的 App-Bound Encryption 擋住了 →\n"
        "  1) 改用 Firefox 登入 music.youtube.com 後重跑本程式（最省事）；或\n"
        "  2) 用 DevTools 對 /youtubei/ 請求 Copy as cURL，手動貼 Cookie。"
    )

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
