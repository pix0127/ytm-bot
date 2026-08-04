# ytm-bot

用 Telegram 指令產生 YouTube Music 歌單，歌來自新番片頭/片尾曲與你訂閱的歌手。
自架、設計成丟在 NAS 上長期無人值守。

```
你:  /agent 放鬆的睡前歌
bot: 🤖「放鬆的睡前歌」step 2：查 YTM 電台…(候選 40 首)
bot: ✅ 已更新歌單(20 首)
     https://music.youtube.com/playlist?list=...
     1. Aporia — Yorushika
     2. Oyasumi Orange — Miho Okasaki
     ...
```

約 10 秒完成（選曲 6.5s + 建歌單 3.5s）。

## 能做什麼

| 指令 | 行為 | 需要 AI |
|---|---|---|
| `/rand 30` | 從歌曲池隨機抽 30 首 | — |
| `/pool 2024 OP 15` | 挑 2024 年的片頭曲 15 首 | — |
| `/agent 像 YOASOBI 那種` | 依心情/風格找歌，會查 YT Music 電台 | ✔ |
| `/update` | 更新歌曲池：本季新番／全部歷史季／訂閱歌手／只重新解析 | — |
| `/cookie` | 檢查 YT Music 登入狀態，失效時給一鍵修復按鈕 | — |
| `/help` | 說明 | |

不帶參數的話會跳按鈕讓你選（年份 → 片頭/片尾 → 數量）。只回應設定檔裡的
`allowed_chat_id`，走 long-poll 所以不需對外開埠。

歌來自兩個地方：[AnimeThemes.moe](https://animethemes.moe) 的新番片頭/片尾（含作品名、
季別、OP/ED），以及你在 YouTube Music 訂閱的歌手熱門曲。兩者都預先解析成真實的 videoId
存在 `data/pool.json`，所以查詢時不必再打搜尋 API。

## 快速開始

需要：Docker、一個 [BotFather](https://t.me/botfather) 給的 Telegram bot token、
一個 OpenAI 相容的 LLM API key（`/agent` 才需要）。

```bash
git clone https://github.com/pix0127/ytm-bot.git && cd ytm-bot

# 1. 產生設定檔（互動式，會問你每一項）
docker compose run --rm setup

# 2. 啟動 bot + 按需 Firefox，然後在 Telegram 對 bot 說句話 → 它會自己綁定你的聊天室
docker compose up -d --build
```

剩下的都在 Telegram 裡做：

```
/update  → 選「全部歷史季」→ 開始建立歌曲池（十幾分鐘，會回報進度）
/rand 20 → 試試看
```

要建歌單、用 `/agent`、或收訂閱歌手的歌，還需要 YT Music 的登入憑證——那部分與 NAS 上的
完整部署步驟與設計筆記見 **[CLAUDE.md](CLAUDE.md)**。

### 設定項目

`ytm.setup` 會逐項詢問並寫入 `data/bot_config.json`（權限 0600）。可以重複執行，
直接 Enter 保留原值。

| 欄位 | 說明 |
|---|---|
| `telegram_token` | BotFather 給的 token |
| `llm_url` | LLM 的 OpenAI 相容端點 |
| `llm_api_key` | LLM API key |
| `model` | 模型名稱 |
| `count_default` | 沒指定數量時預設挑幾首 |
| `firefox_url` | NAS 上 Firefox 容器的網址（cookie 失效時用來登入） |
| `firefox_profile` | Firefox profile 在容器內的掛載路徑 |
| `allowed_chat_id` | **不用填**——第一次對 bot 說話時它會自己記住並寫檔 |

`data/` 底下全部 gitignored，包含 `browser.json`（YT Music 登入憑證，**等於半個
Google 帳號**）。

## 維護指令

```bash
python -m ytm.collect --all-seasons      # 新一季上線後補抓
python -m ytm.collect --artists-only     # 更新訂閱歌手（需要登入）
python -m ytm.collect --fill-anime-jp    # 補日文作品名（resolve 時用得到）
python -m ytm.resolve_pool               # 把沒有 videoId 的歌解析出來
python -m ytm.resolve_pool --repair      # 驗證既有 videoId，不對的重解
python -m ytm.cookie --check             # 登入狀態
python -m ytm.daily_pick --count 20      # 每日隨選歌單（設定 daily_pick_count 讓 bot 每天自動跑）
python -m ytm.prune_disliked             # 把按爛的歌從歌單與 pool 移除
```

資料目錄可用 `YTM_DATA_DIR` 覆蓋（Docker / NAS 共享資料夾用）。

## cookie 會過期，但有自動化

YT Music 的登入憑證幾天到幾週就失效，而且只有當初登入的那個瀏覽器能替自己續期
（實測記錄見 [CLAUDE.md](CLAUDE.md)）。所以部署時會在 NAS 上放一個按需啟動的
Firefox 容器，登入一次之後：

```
每週一        容器自己開一下 → 載入 YT Music → 續期 → 自己關
每 10 分鐘    cookie 壞了就把容器開起來等你登入；開超過 60 分鐘自動關
bot 每 6 小時  profile 有新 cookie 就自動同步
真的失效      Telegram 通知你 → 手機開網頁登入 → 按一顆按鈕
```

這些排程都內建在 bot 行程裡，不依賴 host cron（[為什麼](CLAUDE.md)）。

## 限制

- **用的是 YT Music 的非公開 API**（透過 [ytmusicapi](https://github.com/sigma67/ytmusicapi)），
  它隨時可能改，也不在 YouTube 服務條款的鼓勵範圍內。自行斟酌。
- **`data/browser.json` 等於半個 Google 帳號。** 別讓它離開你的機器；NAS 上那個 Firefox
  網頁 GUI 記得設密碼。
- **LLM 判斷的是語意，不是曲風。** 實測它對「哪首歌放鬆」主要是從歌名字面與歌手印象猜的，
  不是真的認得曲子。氛圍類需求的結果可用但不精確（[為什麼](CLAUDE.md)）。
- 歌單每次會換一個新的 URL（重用舊歌單要逐首清空，太慢）。
- 測試只覆蓋排程核心（`tests/`），沒有 CI。個人自用工具。

## 結構

```
ytm/
  telegram_bot.py    指令、按鈕、cookie 生命週期的背景監看
  scheduler.py       內建排程（Firefox 容器開關、每日歌單）
  setup.py           互動式產生設定檔
  agent_select.py    LLM agent 選曲（ReAct + 歌曲池/電台/搜尋三個工具）
  llm_select.py      單次 LLM 選曲（不用工具）
  collect.py         從 AnimeThemes 與 YT Music 訂閱收集歌曲
  resolve_pool.py    歌名 → videoId，含 --repair 驗證修補
  matcher.py         比對邏輯（羅馬字轉寫、歌名分段、歌手閘門）
  cookie.py          browser.json 的健康檢查與從 Firefox profile 擷取
  playlist.py        YT Music 歌單寫入（建/刪/整批加曲）
  config.py  blocklist.py  daily_pick.py  yearly_playlists.py
  anime_playlist_gen.py  prune_disliked.py
docker-compose.yml   兩個容器＋setup 一份統包
deploy/
  Dockerfile  bot_config.example.json
  nas-firefox/       按需 Firefox 容器的執行時資料（ff-profile/，gitignored）
tests/               排程核心的單元測試
data/                執行時資料與機密（gitignored）
CLAUDE.md            部署步驟、硬規則、設計決策——人和 AI 共用的知識庫
```

Python 3.12，依賴只有 `ytmusicapi`、`pykakasi`、`requests`。程式碼同時打包在 image 裡
與用 volume 掛載，所以改 script 只要 `docker restart`，改依賴才要重 build。

## 授權

MIT，見 [LICENSE](LICENSE)。
