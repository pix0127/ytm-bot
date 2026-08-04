# CLAUDE.md

ytm-bot：Telegram 指令產生 YouTube Music 歌單（新番 OP/ED + 訂閱歌手），自架於 NAS，
單一使用者。Python 3.12，依賴只有 `ytmusicapi`、`pykakasi`、`requests`。

## 地圖

- `ytm/telegram_bot.py` — 指令/按鈕、long-poll 主迴圈、cookie 背景監看
- `ytm/scheduler.py` — 內建排程（週一 05:00 warm、每 10 分 ensure/reap、每日 08:00 daily_pick）
  ＋ Firefox 容器開關（docker CLI）
- `ytm/matcher.py` — 歌名比對（羅馬字轉寫），錯配防線都在這
- `ytm/collect.py` / `resolve_pool.py` — 建 `data/pool.json`（歌 → videoId 索引）
- `ytm/cookie.py` — browser.json 健康檢查與從 Firefox profile 擷取
- `docker-compose.yml` — ytm-bot + ytm-firefox + setup(profile) 一份統包
- `tests/` — 排程核心單元測試：`python3.12 -m pytest tests/ -v`

## 部署（NAS）

```bash
cd /volume1/docker/ytm-bot
docker compose run --rm setup     # 首次才需要;重建時 data/ 已有設定
docker compose up -d --build
```
之後：Telegram 說句話綁定 → `/update` 建池 → `http://<NAS>:5800` 登入 YTM → `/cookie`。
重建 = clone + 還原 `data/`（唯一要備份的東西；連 `deploy/nas-firefox/ff-profile/` 一起備
可免重新登入）+ `up -d --build`。改 `ytm/` 只要 `docker compose restart ytm-bot`
（code 用 volume 掛載覆蓋 image 內那份），改 `requirements.txt` 才要重 build。
每日歌單：`bot_config.json` 加 `"daily_pick_count": 20` 後 restart。

## 硬規則（實測踩坑，違反會直接壞）

1. **絕不把 `data/browser.json` 餵進任何瀏覽器**。Google 把「別處複製來的 cookie」視為
   盜用，會直接殺掉整個 session。驗證有效性只能用 `python -m ytm.cookie --check`。
   cookie 也沒有純程式續命法（實測過 GET、RotateCookies、拔 PSIDTS 全失敗）——
   只有當初登入的那個 Firefox 容器能替自己續期，這就是 ytm-firefox 存在的唯一理由。
2. **同一個 Telegram token 只能有一個實例在跑**。兩個實例會互搶 getUpdates，且本 bot 對
   409 Conflict **靜默**（log 完全看不到），症狀是訊息時好時壞、兩邊各回各的。
3. **bot 容器掛了 `/var/run/docker.sock`**（等於 host root）。docker 操作必須集中在
   `scheduler.py`，只允許 `start/stop/inspect ytm-firefox` 三個動作，不得擴大。
4. **歌名比對失敗就放棄，不退回搜尋第一名**——第一名必然是同歌手別首歌，收下等於錯配。
   寧可少一首。

## 已知行為（不是 bug，別「修」它們）

- 每次 `/update` 會重試先前「YTM 找不到」的歌（約 20 秒白工）。刻意不加黑名單：
  YT Music 會後補上架，黑名單會讓那些歌永遠救不回來。完成訊息重複出現同一批
  「解不到（已移除）」是預期行為。
- 歌單每次換新 URL：重用舊歌單要 O(N) 逐首清空，刪掉重開是 O(1)。4000 首規模下必要。
- 排程錯過觸發點不補跑（比照 cron）；bot 重啟橫跨觸發點就是跳過。
- compose `up` 會把 ytm-firefox 帶起來一次，60 分鐘內 reap 自動關——首次安裝正好用來登入。

## 設計決策速查（為什麼是現在這樣）

- **只有一套認證（cookie）**：先前的 Data API v3 + OAuth 已移除。ytmusicapi 建 20 首歌單
  3.5s vs API v3 的 20s（`add_playlist_items` 整批 vs 逐首＋寫入鎖、並行必 409）。
  代價：cookie 失效時建歌單也會失敗，所以自動續期機制是命脈。
- **排程內建進 bot 而非 host cron / Ofelia sidecar**：DSM 改「任務排程」時會重寫
  `/etc/crontab` 靜默清掉我們的行（吃過一次虧，cookie 死五天沒人發現）；內建後這個
  失敗模式與心跳偵測機制整個消失，容器也少一個。代價是 bot 死排程跟著死——單人自用可接受。
- **LLM 呼叫要帶 `"thinking":{"type":"disabled"}`**：選曲沒有推理可走，reasoning 空轉
  10.7s→1.7s，還會吃光 max_tokens 害 content 變空字串觸發重試。`reasoning_effort` gateway 不吃。
- **LLM 選曲判斷的是「歌名字面＋歌手刻板印象」，不是曲風**（消融實驗驗證）。氛圍類需求
  結果可用但不精確，不必試圖用 prompt 修到精確。
- **matcher 用 pykakasi**：pool 是羅馬字、YTM 目錄是日文。歌手名也要轉寫（否則日文歌手
  token 空集合、全被閘門擋掉）；標題按「日文 - 羅馬字」分段比；純符號歌名退回原字串比；
  子串命中有長度下限（`Awa` 會誤中 `Shiwaawase`）。改 matcher 前先想清楚對應哪個案例。

## 慣例

- 文件與註解用繁體中文；註解只寫「為什麼」，密度比照現有檔案。
- 不加 runtime 依賴；排程時間寫死不做設定（單人專案，YAGNI）。
- bot token 會出現在 requests 例外訊息裡（URL 含 token），對外輸出一律過 `_redact()`。
