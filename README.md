# ytm-tools

從新番片頭/片尾曲與訂閱歌手，自動產生 YouTube Music 歌單。主要介面是一個 Telegram bot，
也可以純 CLI 使用。設計目標是丟在 NAS 上長期無人值守。

```
Telegram: /agent 放鬆的睡前歌
   → LLM 從歌曲池選 20 首 → 建好 YT Music 歌單 → 回連結（約 20 秒）
```

## 核心是「歌曲池」

`data/pool.json` 是一個本地索引，每首歌都已經對應到**真實的 YouTube videoId**：

- **動畫歌**來自 [AnimeThemes.moe](https://animethemes.moe)（作品名、季別、OP/ED、歌手）
- **歌手歌**來自你在 YouTube Music 訂閱的歌手熱門曲
- videoId 由 `resolve_pool` 事先解析好，並保證一支影片只對應一首歌

事先解析是整個架構的關鍵。因為 videoId 已經有了，`daily_pick` 才能走**官方 Data API v3 + OAuth**
（自動 refresh、免 cookie、適合排程）；查詢時也不必再打搜尋 API。

## 介面

### Telegram bot（主要）

| 指令 | 行為 |
|---|---|
| `/rand` | 隨機抽，最快，不用 AI |
| `/pool` | 按鈕選年份 → 片頭/片尾 → 數量 |
| `/agent` | 用 LLM 依心情/風格找歌，會查 YT Music 電台 |
| `/cookie` | 檢查 YT Music 登入狀態，失效時給一鍵修復按鈕 |
| `/help` | 說明 |

也支援帶參數：`/rand 30`、`/pool 2024 OP 15`、`/agent 放鬆的`。
只回應設定檔裡的 `allowed_chat_id`，走 long-poll 所以不需對外開埠。

```bash
python -m ytm.setup               # 互動式產生 data/bot_config.json（可重複執行）
python -m ytm.telegram_bot
```

`allowed_chat_id` 不用手填——第一次對 bot 說話時它會自己記住並寫檔。

### CLI

```bash
python -m ytm.collect                    # 收集新番 + 訂閱歌手 → pool.json
python -m ytm.collect --fill-anime-jp    # 補日文作品名（resolve 時會用到）
python -m ytm.resolve_pool               # 把沒有 videoId 的歌解析出來
python -m ytm.resolve_pool --repair      # 驗證既有 videoId，不對的重解（見下）
python -m ytm.cookie --check             # YT Music 登入狀態
python -m ytm.daily_pick --count 20      # 每日隨選歌單（OAuth，免 cookie）
python -m ytm.yearly_playlists           # 各年度歌單（--year 2026 / --update）
python -m ytm.anime_playlist_gen         # 本季新番歌單
python -m ytm.prune_disliked             # 把按爛的歌從歌單與 pool 移除
```

資料目錄可用 `YTM_DATA_DIR` 覆蓋（Docker / NAS 共享資料夾用）。

## 歌名比對：為什麼需要 pykakasi

pool 的歌名是羅馬字（AnimeThemes 的格式），但 YT Music 目錄以日文為主。早期版本只用「歌手」
當比對條件，導致同一位歌手的不同曲子全部配到同一支影片——實測有 91 支影片被 213 首歌共用。

`ytm/matcher.py` 現在做四件事：

1. 用 pykakasi 把日文轉羅馬字再比（「決意の唄」→ `ketsuinota`）
2. 標題按「日文名 - 羅馬字名」分段比對（`Nekohi` 對整串「猫日 - Catdays」只有 0.55，對第一段是 1.00）
3. 歌手名也先羅馬字化（否則「鈴木このみ」的 token 是空集合，所有日文歌手名都會被閘門擋掉）
4. 有日文作品名時多搜一輪「作品名 + OP/ED」——羅馬字歌名搜不到的曲子常常這樣才撈得到

歌名不夠像就**放棄**，不會退回「搜尋結果第一名」。那個 fallback 正是錯配的來源：
YT Music 沒有該曲時，第一名必然是同歌手的別首歌，收下來只是把錯誤藏起來。

`resolve_pool --repair` 用同一套規則驗證既有資料：先用 Data API 批次抓標題（一次 50 支）篩掉
明顯正常的，剩下的才逐支問 ytmusicapi（它會給羅馬字標題），真的不像才重解、解不出來才刪。

## 兩套認證，各有原因

| 用途 | 認證 | 為什麼 |
|---|---|---|
| `daily_pick` | OAuth + Data API v3 | 每日用量小、只用存好的 videoId、token 自動 refresh，最適合無人值守 |
| 其餘全部 | browser cookie | 需要 YT Music 的歌曲目錄搜尋，而那個內部 API **不吃 OAuth**（回 HTTP 400） |

細節與配額計算見 [docs/OAUTH.md](docs/OAUTH.md)。

### cookie 只能由原本登入的瀏覽器續期

`data/browser.json` 幾天到幾週就會失效，而且**沒有純程式的續命辦法**。實測結論：

- 認證必要的是 `__Secure-1PSIDTS`（拿掉它 library 端點就回 0 筆；`SIDCC` 家族拿掉沒差）
- 任何 HTTP 請求都不會讓 Google 補發它，帶完整導覽 header 也一樣
- 把 cookie 餵進另一個瀏覽器會被視為盜用，**整個 session 立刻失效**

Google 只信任當初完成登入的那個瀏覽器。所以做法是在 NAS 上放一個按需啟動的 Firefox 容器
（[deploy/nas-firefox/](deploy/nas-firefox/)），登入一次之後：

```
每週一        容器自己開一下 → 載入 YT Music → Firefox 向 Google 續期 → 自己關
每 10 分鐘    cookie 壞了就把容器開起來等你登入；開超過 60 分鐘自動關
bot 每 6 小時  profile 有新 cookie 就自動同步到 browser.json
真的失效      Telegram 通知你 → 手機開網頁登入 → 按一顆按鈕
```

排程本身也有心跳監控——`firefox-ctl.sh` 每次執行會寫時間戳，bot 發現心跳停了會通知你。
（DSM 重寫 `/etc/crontab` 時會把排程清掉，那是靜默失敗。）

部署步驟見 [docs/SETUP.md](docs/SETUP.md)。

## 結構

```
ytm/
  telegram_bot.py    bot：指令、按鈕、cookie 生命週期的背景監看
  agent_select.py    LLM agent 選曲（ReAct + 歌曲池/電台/搜尋三個工具）
  llm_select.py      單次 LLM 選曲（不用工具）
  collect.py         從 AnimeThemes 與 YT Music 訂閱收集歌曲
  resolve_pool.py    歌名 → videoId，含 --repair 驗證修補
  matcher.py         比對邏輯（羅馬字轉寫、歌名分段、歌手閘門）
  cookie.py          browser.json 的健康檢查與從 Firefox profile 擷取
  dataapi.py         官方 Data API v3 的歌單寫入
  oauth.py           device flow 授權與 token refresh
  setup.py           互動式產生 bot_config.json
  config.py  blocklist.py  daily_pick.py  yearly_playlists.py
  anime_playlist_gen.py  prune_disliked.py
deploy/
  Dockerfile  run_daily.sh  bot_config.example.json
  nas-firefox/       按需 Firefox 容器 + 生命週期腳本（firefox-ctl.sh）
data/                執行時資料與機密（gitignored）
  pool.json  browser.json  bot_config.json  oauth.json  blocklist.json
  state/  backups/
docs/                SETUP.md（部署）、OAUTH.md（兩個 API 的差異）
```

## 需要知道的限制

- **用的是 YT Music 的非公開 API**（透過 [ytmusicapi](https://github.com/sigma67/ytmusicapi)）。它隨時可能改，也不在 YouTube 服務條款的鼓勵範圍內。自行斟酌。
- **`data/browser.json` 等於半個 Google 帳號。** 它在 `.gitignore` 裡，別讓它離開你的機器。NAS 上那個 Firefox 網頁 GUI 記得設密碼（compose 裡的 `WEB_AUTHENTICATION`）。
- **LLM 是用來判斷語意，不是判斷曲風。** 實測它對「哪首歌放鬆」的判斷主要來自歌名字面與歌手印象，不是真的認得曲子——連播放數千萬的紅歌，遮掉歌名後判斷就會漂掉。所以氛圍類需求的結果可用但不精確。
- 沒有測試、沒有 CI。個人自用工具。

## 依賴

```bash
pip install -r requirements.txt   # ytmusicapi, pykakasi, requests
```

Python 3.12。`deploy/Dockerfile` 用 `python:3.12-slim`；程式碼與 `data/` 以 volume 掛載，
所以改 script 不用重 build，只有改依賴才需要。

## 授權

MIT，見 [LICENSE](LICENSE)。
