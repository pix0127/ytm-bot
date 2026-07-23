# 家用部署（NAS 跑排程 + PC 供 cookie）

適用：NAS 24h 常開負責排程，PC 有登入 music.youtube.com 的瀏覽器負責產生 cookie。

## 資料流

```
[PC · Windows] 工作排程器每天跑 tools/refresh_ytm_cookie.py
   → 讀 Edge/Chrome cookie → 寫 browser.json 到 NAS 共享的 data/
[NAS · DSM] 任務排程器每天跑 deploy/run_daily.sh（Docker 容器內 python -m ytm.daily_pick）
   → 讀 data/ 的 browser.json + pool.json → 更新歌單
```

只有 Google 把你登出時（幾個月一次）才需在瀏覽器重登；PC 的每日刷新會自動把新 cookie 同步過去。

## NAS 端（Synology DSM）

1. 專案放 `/volume1/docker/ytm-tools`（含 `ytm/`、`data/`）。`data/` 設成**只有你自己**能讀的私人共享資料夾（含 browser.json）。
2. Build 映像：`docker build -t ytm-tools:latest /volume1/docker/ytm-tools`（build context = 專案根，會讀 `deploy/Dockerfile`：`docker build -f deploy/Dockerfile ...` 或把 Dockerfile 複製到根目錄擇一）。
3. 改 `deploy/run_daily.sh` 的 `PROJECT_DIR` 為實際路徑。
4. 控制台 → 任務排程器 → 使用者定義指令碼（使用者 root）→ 每天 → `bash /volume1/docker/ytm-tools/deploy/run_daily.sh`。

## PC 端（Windows）

1. `pip install browser_cookie3`
2. 把 NAS 的 `data/` 共享資料夾掛成網路磁碟機（例如 `Z:`）。
3. 工作排程器每天跑：`python <專案>\tools\refresh_ytm_cookie.py Z:\browser.json`

## 更簡單的替代：全跑 PC

若 PC 幾乎都開著，直接在 PC 上 `python -m ytm.daily_pick`，`browser_cookie3` 自動讀本機 cookie，免 NAS、免搬運。代價：排程時間點 PC 要開機。
