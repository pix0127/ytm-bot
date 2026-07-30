# NAS 部署

適用：NAS 24 小時常開，跑 Telegram bot 與排程；cookie 由同一台 NAS 上的按需 Firefox 容器提供。
不需要另一台 PC。

## 全部指令一次看完

先準備：Telegram bot token（BotFather）、一個 LLM API key。下面每一步的說明見後面各節。

```bash
cd /volume1/docker/ytm-bot        # 專案放這，data/ 設成私人共享資料夾
D="docker run --rm -it -v $PWD/data:/app/data ytm-bot:latest python -m"

# 2. build
docker build -f deploy/Dockerfile -t ytm-bot:latest .

# 3. 產設定檔（必須在啟動 bot 之前，原因見第 3 節）
$D ytm.setup

# 4. 啟動 bot，然後對它說句話讓它綁定聊天室
docker run -d --name ytm-bot --restart unless-stopped \
  -v $PWD/ytm:/app/ytm -v $PWD/data:/app/data \
  -v $PWD/deploy/nas-firefox/ff-profile:/app/ff-profile:ro \
  ytm-bot:latest python -m ytm.telegram_bot

# 5. 建歌曲池 → 在 Telegram 打 /update，選「全部歷史季」

# 6. 登入 YT Music（先在 compose 設 WEB_AUTHENTICATION 密碼）
(cd deploy/nas-firefox && docker compose up -d)
#    手機開 http://<NAS>:5800 登入 → Telegram 打 /cookie → 按「我登入好了」
#    然後在 Telegram 打 /update 選「訂閱歌手」

# 7. 裝三個排程（warm / ensure / reap，內容見第 7 節）
```

## 一次性設定（逐步說明）

### 1. 放專案與資料

```
/volume1/docker/ytm-bot/
  ytm/      程式碼
  deploy/
  data/     ← 設成只有你自己能讀的私人共享資料夾（裡面有 browser.json）
```

`data/` 需要準備的檔案：

| 檔案 | 來源 |
|---|---|
| `bot_config.json` | 第 3 步用 `ytm.setup` 互動產生（或照 `deploy/bot_config.example.json` 手填） |
| `browser.json` | 不用手動準備，第 6 步會自動產生（YT Music 登入憑證） |

### 2. Build image

```bash
cd /volume1/docker/ytm-bot
docker build -f deploy/Dockerfile -t ytm-bot:latest .
```

### 3. 產生設定檔

**要在啟動 bot 之前做。** 沒有 `bot_config.json` 的話 bot 會立刻退出，配上
`--restart unless-stopped` 就變成無限重啟，那時 `docker exec` 會被拒絕
（`Container is restarting, wait until the container is running`）。所以用一個臨時容器來產：

```bash
docker run --rm -it -v /volume1/docker/ytm-bot/data:/app/data \
  ytm-bot:latest python -m ytm.setup
```

它會逐項問你並附說明，可重複執行（Enter 保留原值）。`-it` 是必要的，
沒有的話它會拒絕執行並提醒你。

`allowed_chat_id` 留空即可——你對 bot 說第一句話時它會自己綁定並寫檔。

### 4. 啟動 bot

```bash
docker run -d --name ytm-bot --restart unless-stopped \
  -v /volume1/docker/ytm-bot/ytm:/app/ytm \
  -v /volume1/docker/ytm-bot/data:/app/data \
  -v /volume1/docker/ytm-bot/deploy/nas-firefox/ff-profile:/app/ff-profile:ro \
  ytm-bot:latest python -m ytm.telegram_bot
```

程式碼有兩份：一份打包在 image 裡（讓臨時容器不用掛載就能跑），一份用 `-v` 掛載覆蓋它。
所以之後改 script 只要 `docker restart ytm-bot`，只有改 `requirements.txt` 才需要重 build。

現在對 bot 說句話，它會回「已綁定這個聊天室」加上指令說明。

### 5. 建立歌曲池

**這步不能跳過**——沒有 `pool.json`，bot 雖然會啟動，但所有選曲指令都無法使用
（它會提示你打 `/update`）。

在 Telegram 打 **`/update`** → 選 **「全部歷史季」**。它會從 AnimeThemes 抓歷史各季、
補日文作品名、再把歌名解析成 videoId，全程在訊息裡回報進度。十幾分鐘。

動畫歌不需要登入就能抓。**訂閱歌手**的部分需要有效 cookie，所以等第 6 步登入完成後，
再打 `/update` 選「訂閱歌手」。

解析可以中斷後續傳——它只挑沒有 videoId 的歌，所以重跑會從斷點繼續。也可以用
`docker exec -w /app ytm-bot python -m ytm.resolve_pool` 在終端機跑（輸出比較詳細）。

### 6. 登入 YouTube Music（產生 cookie）

**先設密碼。** 編輯 `deploy/nas-firefox/docker-compose.yml`，把 `WEB_AUTHENTICATION` 三行的註解
拿掉並填上帳密——那個網頁裝著一個已登入的 Google 帳號，沒密碼等於同網段誰都能用。

```bash
cd /volume1/docker/ytm-bot/deploy/nas-firefox
docker compose up -d
```

用瀏覽器（手機也可以）開 `http://<你的NAS>:5800`。容器設了 `FF_OPEN_URL`，畫面會直接停在
YouTube Music，在裡面登入即可。登入完成後在 Telegram 打 `/cookie` → 按「我登入好了，重新擷取」。

之後就不用再管它：bot 每 6 小時會自己從 Firefox profile 同步新 cookie。

### 7. 裝排程

`firefox-ctl.sh` 管理那個容器的生命週期。三個排程加進 `/etc/crontab`（**tab 分隔**，DSM 要求）：

```
0	5	*	*	1	root	/volume1/docker/ytm-bot/deploy/nas-firefox/firefox-ctl.sh warm
*/10	*	*	*	*	root	/volume1/docker/ytm-bot/deploy/nas-firefox/firefox-ctl.sh ensure
*/10	*	*	*	*	root	/volume1/docker/ytm-bot/deploy/nas-firefox/firefox-ctl.sh reap
```

改完 `synoservice --restart crond`。

| 模式 | 作用 |
|---|---|
| `warm` | 每週開一下容器讓 Firefox 向 Google 續期 cookie，然後關掉 |
| `ensure` | cookie 壞了就把容器開起來等你登入 |
| `reap` | 容器開超過 60 分鐘自動關閉 |

**注意**：DSM 在你於「控制台 → 任務排程」增刪任務時會重寫 `/etc/crontab`，上面三行可能被清掉。
`firefox-ctl.sh` 每次執行都會寫心跳檔，bot 發現心跳停超過 2 小時會用 Telegram 通知你，
屆時把三行加回即可。也可以改用任務排程 UI 建三個「使用者定義的指令碼」任務（使用者選 root），
那樣不會被覆寫，但綁 Synology。

### 8.（選配）每日隨選歌單

```
0	8	*	*	*	root	bash /volume1/docker/ytm-bot/deploy/run_daily.sh
```

`run_daily.sh` 需要有效的 cookie。記得改裡面的 `PROJECT_DIR`。

## 日常維護

| 情況 | 做什麼 |
|---|---|
| Telegram 說 cookie 失效 | 開 `http://<NAS>:5800` 登入，按通知裡的按鈕 |
| Telegram 說排程心跳停了 | 檢查 `/etc/crontab` 那三行還在不在 |
| 新一季動畫上線 | `docker exec -w /app ytm-bot python -m ytm.collect --all-seasons`，之後跑 `resolve_pool` |
| 想更新訂閱歌手 | `collect --artists-only`（需要有效 cookie） |
| pool 疑似有錯配 | `resolve_pool --repair`（會先備份，刪除清單寫到 `data/backups/`） |

## 相關文件

為什麼這樣設計（cookie 為何只能靠瀏覽器續期、歌名比對為何需要羅馬字轉寫、
排程為何用 host cron）見 [DESIGN.md](DESIGN.md)。

## 換平台

只有第 7 步綁 Synology（`/etc/crontab` 與 `synoservice`）。其他都是標準 Docker：
兩個容器 + 幾個 volume。在別的 Linux 上把排程換成一般 cron 或 systemd timer 即可。
