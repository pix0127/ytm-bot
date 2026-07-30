#!/usr/bin/env python3.12
"""互動式產生 data/bot_config.json。

為什麼不用環境變數或網頁:機密放環境變數的話,任何能跑 docker inspect 的人都看得到
(在 DSM 上＝任何能開 Container Manager 的人);網頁則會把金鑰顯示出來,得再加一套認證。
掛載的檔案本來就是 Docker 建議的 secrets 模式,這支只是讓那個檔案不用手刻。

可以重複執行——會讀入現有設定當預設值,直接按 Enter 就保留原值。

用法:
  python -m ytm.setup                      # 本機
  docker exec -it ytm-bot python -m ytm.setup   # 容器內（-it 是必要的）
"""
import json
import os
import stat
import sys

from .config import BOT_CONFIG_FILE, DATA_DIR

# (key, 說明, 預設值, 是否機密)
FIELDS = [
    ("telegram_token", "Telegram bot token（BotFather 給的）", None, True),
    ("llm_url", "LLM 的 OpenAI 相容端點", "https://opencode.ai/zen/go/v1/chat/completions", False),
    ("llm_api_key", "LLM API key", None, True),
    ("model", "模型名稱", "deepseek-v4-flash", False),
    ("count_default", "沒指定數量時預設挑幾首", 20, False),
    ("firefox_url", "NAS 上 Firefox 容器的網址（cookie 失效時用來登入）", "http://你的NAS:5800", False),
    ("firefox_profile", "Firefox profile 在容器內的掛載路徑", "/app/ff-profile", False),
]


def _mask(v: str) -> str:
    s = str(v)
    return s if len(s) <= 12 else f"{s[:6]}…{s[-4:]}"


def _ask(label: str, default, secret: bool):
    shown = "" if default in (None, "") else (_mask(default) if secret else default)
    suffix = f" [{shown}]" if shown != "" else ""
    while True:
        got = input(f"  {label}{suffix}: ").strip()
        if got:
            return got
        if default not in (None, ""):
            return default
        print("    ← 這項是必填的")


def main():
    if not sys.stdin.isatty():
        raise SystemExit("需要互動式終端機。容器內請用: docker exec -it ytm-bot python -m ytm.setup")

    existing = {}
    if os.path.exists(BOT_CONFIG_FILE):
        try:
            existing = json.load(open(BOT_CONFIG_FILE))
            print(f"已讀取現有設定 {BOT_CONFIG_FILE}（直接 Enter 保留原值）\n")
        except json.JSONDecodeError:
            print(f"⚠️  {BOT_CONFIG_FILE} 不是合法 JSON，將重新建立\n")

    cfg = dict(existing)
    for key, label, fallback, secret in FIELDS:
        default = existing.get(key, fallback)
        value = _ask(label, default, secret)
        if key == "count_default":
            try:
                value = int(value)
            except ValueError:
                print(f"    ← 不是數字，用 {fallback}")
                value = fallback
        cfg[key] = value

    # 第一次對話時 bot 會自己寫入,不必在這裡問
    cfg.setdefault("allowed_chat_id", None)

    with open(BOT_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(BOT_CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600，裡面有金鑰

    print(f"\n✅ 已寫入 {BOT_CONFIG_FILE}（權限 0600）")
    if not cfg.get("allowed_chat_id"):
        print("   allowed_chat_id 還是空的——對 bot 說第一句話時它會自己記住你。")

    missing = [n for n in ("oauth_client.json", "oauth.json")
               if not os.path.exists(os.path.join(DATA_DIR, n))]
    if missing:
        print(f"\n還缺 {', '.join(missing)}（建歌單要用）：")
        if "oauth_client.json" in missing:
            print("   1. Google Cloud 建「TV and Limited Input devices」型 OAuth client，啟用 YouTube Data API v3")
            print(f"   2. 存成 {os.path.join(DATA_DIR, 'oauth_client.json')}：{{\"client_id\":…,\"client_secret\":…}}")
        print("   3. python -m ytm.oauth   ← 一次性授權，之後自動 refresh")
        print("   細節見 docs/OAUTH.md")


if __name__ == "__main__":
    main()
