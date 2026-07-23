"""OAuth 認證（給官方 YouTube Data API v3 用，與 ytmusicapi 的 browser auth 無關）。

- `python -m ytm.oauth` 跑一次 device flow 取得並存 token（data/oauth.json）。
- `get_access_token()` 供其他模組取用；過期自動 refresh。

client_id/secret 來源：環境變數 YTM_OAUTH_CLIENT_ID / YTM_OAUTH_CLIENT_SECRET，
或 data/oauth_client.json（皆 gitignored）。
"""
import json
import os
import time

import requests

from .config import DATA_DIR

OAUTH_FILE = os.path.join(DATA_DIR, "oauth.json")
CLIENT_FILE = os.path.join(DATA_DIR, "oauth_client.json")

CODE_URL = "https://www.youtube.com/o/oauth2/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0) Gecko/20100101 Firefox/88.0 Cobalt/Version"
DEVICE_GRANT = "http://oauth.net/grant_type/device/1.0"


def _client() -> tuple[str, str]:
    cid = os.environ.get("YTM_OAUTH_CLIENT_ID")
    csec = os.environ.get("YTM_OAUTH_CLIENT_SECRET")
    if cid and csec:
        return cid, csec
    if os.path.exists(CLIENT_FILE):
        d = json.load(open(CLIENT_FILE))
        return d["client_id"], d["client_secret"]
    raise SystemExit("找不到 OAuth client：設 YTM_OAUTH_CLIENT_ID/SECRET 或建 data/oauth_client.json")


def _save_token(tok: dict):
    data = {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "expires_at": int(time.time()) + int(tok.get("expires_in", 0)) - 60,
        "scope": tok.get("scope"),
    }
    if not data["refresh_token"] and os.path.exists(OAUTH_FILE):  # refresh 通常不回 refresh_token
        data["refresh_token"] = json.load(open(OAUTH_FILE)).get("refresh_token")
    with open(OAUTH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def setup():
    cid, csec = _client()
    r = requests.post(CODE_URL, data={"client_id": cid, "scope": SCOPE}, headers={"User-Agent": UA})
    r.raise_for_status()
    code = r.json()
    print(f"AUTH_URL: {code['verification_url']}", flush=True)
    print(f"AUTH_CODE: {code['user_code']}", flush=True)
    interval = int(code.get("interval", 5))
    deadline = time.time() + int(code.get("expires_in", 1800))
    tok = None
    while time.time() < deadline:
        rr = requests.post(TOKEN_URL, headers={"User-Agent": UA}, data={
            "client_id": cid, "client_secret": csec,
            "grant_type": DEVICE_GRANT, "code": code["device_code"]})
        j = rr.json()
        if j.get("access_token"):
            tok = j
            break
        if j.get("error") == "slow_down":
            interval += 2
        time.sleep(interval)
    if not tok:
        raise SystemExit("授權逾時")
    _save_token(tok)
    print(f"TOKEN_SAVED: {OAUTH_FILE}", flush=True)


def get_access_token() -> str:
    if not os.path.exists(OAUTH_FILE):
        raise SystemExit("尚未 OAuth 授權，請先執行： python -m ytm.oauth")
    t = json.load(open(OAUTH_FILE))
    if time.time() < t.get("expires_at", 0):
        return t["access_token"]
    cid, csec = _client()
    r = requests.post(TOKEN_URL, headers={"User-Agent": UA}, data={
        "client_id": cid, "client_secret": csec,
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    j = r.json()
    if not j.get("access_token"):
        raise SystemExit(f"token refresh 失敗：{j}")
    _save_token(j)
    return j["access_token"]


if __name__ == "__main__":
    setup()
