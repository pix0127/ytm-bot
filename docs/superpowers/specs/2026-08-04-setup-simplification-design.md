# 部署流程簡化設計

日期：2026-08-04
狀態：待審

## 目標與動機

現行部署要照 SETUP.md 做 8 步：放檔案、build、臨時容器跑互動式 `ytm.setup`、
啟動 bot、`/update`、Firefox 登入、手改 `/etc/crontab` 三行、選配 daily。
痛點（使用者自述）：互動式 setup 難用、步驟太多太散、crontab 要手改且會被 DSM 重寫。

目標使用者是作者本人；優化對象是「重建成本」：換 NAS 或重灌時，
流程收斂為 **`git clone` → 還原 `data/` 備份 → `docker compose up -d --build`**。

明確的取捨（已確認）：**容器數量最少優先**——不引入 Ofelia 等排程 sidecar，
排程全部內建進 bot，代價是 bot 容器掛 `docker.sock`（風險討論見「安全考量」）。

## 現況（相關部分）

- 兩個容器：`ytm-bot`（手刻 long-poll bot，threads 模型，無框架）、
  `ytm-firefox`（jlesage/firefox，平時停用，按需開關）。
- 開關 Firefox 的邏輯在 `deploy/nas-firefox/firefox-ctl.sh`（host cron 執行）：
  - `warm`：每週一 05:00 開容器 180s 讓 Firefox 向 Google 續期 cookie 後關閉
  - `ensure`：每 10 分鐘檢查 cookie（`docker exec ytm-bot python -m ytm.cookie --check`），
    失效就開容器等使用者登入
  - `reap`：每 10 分鐘檢查，容器連續開超過 60 分鐘就關
- `firefox-ctl.sh` 每次執行寫心跳檔 `data/state/ffctl_heartbeat`；
  bot 的 `_cookie_watch` thread（每 6h）偵測心跳停擺並推播（防 DSM 重寫 crontab 的靜默失敗）。
- 每日歌單：host cron 08:00 跑 `deploy/run_daily.sh`（臨時容器執行 `ytm.daily_pick`）。
- 設定檔 `data/bot_config.json` 由互動式 `python -m ytm.setup` 產生（需 `-it` 臨時容器）。

## 設計

### 1. 根目錄 `docker-compose.yml` 統包兩個容器

```yaml
services:
  ytm-bot:
    build: { context: ., dockerfile: deploy/Dockerfile }
    image: ytm-bot:latest
    container_name: ytm-bot
    restart: unless-stopped
    command: python -m ytm.telegram_bot
    volumes:
      - ./ytm:/app/ytm
      - ./data:/app/data
      - ./deploy/nas-firefox/ff-profile:/app/ff-profile:ro
      - /var/run/docker.sock:/var/run/docker.sock

  ytm-firefox:
    # 內容自 deploy/nas-firefox/docker-compose.yml 併入（image、5800、ff-profile、
    # shm_size、FF_OPEN_URL、WEB_AUTHENTICATION）
    restart: "no"
```

- `ytm-firefox` 用 `restart: "no"` 併入同一份 compose：`compose up -d` 會把它
  create + start 一次，隨後由 bot 的 reap 在 60 分鐘內關閉，之後開關全由 bot 控制。
  首次啟動即開著反而是好事——正好留給使用者做首次登入。
- 舊 `deploy/nas-firefox/docker-compose.yml` 刪除，`ff-profile/` 目錄與說明保留原位。
- Dockerfile 增裝 `docker-cli`（僅 client，不含 daemon），供 bot 開關 sibling 容器。

### 2. bot 內建 scheduler（新模組 `ytm/scheduler.py`）

一條 daemon thread，維護固定 job 表，每分鐘醒來檢查誰到期（比照現有
`_cookie_watch` 的手刻 thread 風格，不引入 APScheduler）：

| job | 週期 | 內容 |
|---|---|---|
| `warm` | 每週一 05:00 | `docker start ytm-firefox` → sleep 180s → `docker stop` |
| `ensure` | 每 10 分鐘 | cookie 失效（直接呼叫 `cookie.check()`，不再 docker exec）且容器沒開 → `docker start` |
| `reap` | 每 10 分鐘 | 容器 `StartedAt` 距今 ≥ 60 分鐘 → `docker stop` |
| `daily_pick` | 每日 08:00 | 呼叫 `daily_pick` 的挑歌邏輯（in-process，不再開臨時容器） |

- 開關容器用 `subprocess` 呼叫 `docker` CLI（沿用 shell 版語意），不引入 docker SDK。
- `warm` 的 sleep 在 job thread 內進行，不阻塞其他 job（scheduler 每個 job 觸發時
  spawn thread，或至少 warm 獨立 spawn）。
- 時間判斷用本地時（容器 TZ=Asia/Taipei，Dockerfile/compose 需設）。
  cron 式「錯過就算了」語意：bot 重啟橫跨觸發點的 job 不補跑（與 host cron 行為一致）。
- `daily_pick` 是否啟用：`bot_config.json` 新增選用欄位 `daily_pick_count`
  （未設定＝不跑，取代原本「要不要裝第 8 步 cron」的選擇）。

### 3. 刪除的東西

- `firefox-ctl.sh`、心跳檔機制、`_sched_stale()` 與 `_cookie_watch` 內的心跳警告——
  排程進到 bot 行程內，「cron 被 DSM 清掉」這個失敗模式不存在了。
  （bot 自己掛掉時排程當然也停,但那時 Telegram 不回話，本來就會被發現。）
- `deploy/run_daily.sh`、`deploy/daily_pick.sh`（`ytm/daily_pick.py` 模組保留，
  scheduler 直接呼叫，CLI 入口也保留供手動跑）。
- SETUP.md 的第 7 節（crontab）、第 8 節（daily cron），以及「排程心跳」維護項。

### 4. `ytm.setup` 降級為首次安裝專用

- 保留現有互動式模組不動（修過 `__main__` guard，功能正常）。
- compose 加一個 profile 服務讓首次設定變一條指令：
  ```yaml
  setup:
    profiles: ["setup"]
    build: { context: ., dockerfile: deploy/Dockerfile }   # 首次 run 時 image 還沒 build,要能自己 build
    image: ytm-bot:latest
    volumes: [ "./data:/app/data" ]
    command: python -m ytm.setup
    stdin_open: true
    tty: true
  ```
  用法：`docker compose run --rm setup`。
- bot 啟動時若無 `bot_config.json`，印出指引訊息後 `sleep` 等待（每 60s 重試載入），
  而不是立刻退出——消除「無限重啟導致 exec 被拒」這個文件裡特別警告的坑，
  也讓「先 compose up 再 setup」的順序錯誤不再致命。

### 5. SETUP.md 改寫

新流程：

```bash
cd /volume1/docker/ytm-bot
docker compose run --rm setup      # 首次才需要；重建時 data/ 已有設定，跳過
docker compose up -d --build
# → Telegram 說句話綁定 → /update 建池 → :5800 登入 → /cookie
```

重建（data/ 有備份）就是兩條指令。文件長度預期砍半以上。

## 安全考量

`docker.sock` 掛進 bot 容器等於給該容器 host root 等級能力，而 bot 對外
（Telegram long-poll、LLM API）。已知悉並接受，理由：個人 NAS、單一 allowed_chat_id、
token 外洩本來就等於 bot 被接管。緩解：bot 內所有 docker 操作集中在 `scheduler.py`
一個模組、只允許 `start/stop/inspect ytm-firefox` 三個動作,不提供任意指令路徑。

## 錯誤處理

- scheduler 的 job 例外一律 catch + log,不讓單次失敗殺掉 scheduler thread。
- `docker` CLI 不存在或 socket 沒掛（例如在非 Docker 環境開發時）：
  scheduler 啟動時偵測,warm/ensure/reap 停用並 log 警告,bot 其他功能照常。
- `ensure` 開了容器後仍沿用現行通知路徑（`_cookie_watch` 偵測 cookie 失效推播）,
  行為與現在一致。

## 測試

- `scheduler.py` 的到期判斷（週期、每週/每日觸發點、重啟不補跑）寫單元測試,
  docker 呼叫 mock 掉。
- 手動驗證清單：
  1. 全新目錄 `compose run --rm setup` → `compose up -d --build` 能跑起來
  2. `/update`、`/cookie` 等既有指令不受影響
  3. reap 會在 60 分鐘內關掉 compose up 時啟動的 ytm-firefox
  4. 模擬 cookie 失效 → ensure 於 10 分鐘內開起 Firefox 容器
  5. `daily_pick_count` 設定後隔日 08:00 有產歌單（或把觸發點暫調近測試）

## 不做的事（YAGNI）

- 不引入 Ofelia / APScheduler / python-telegram-bot / docker SDK。
- 不做網頁設定介面、不改用環境變數（機密可見性理由見 setup.py docstring）。
- 不動 Firefox 登入 / cookie 擷取流程（使用者未列為痛點）。
- 排程時間不做成可設定（固定值,要改就改 code——單一使用者專案）。
