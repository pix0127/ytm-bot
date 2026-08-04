# NAS 部署

適用：NAS 24 小時常開。兩個容器（bot + 按需 Firefox）由一份 compose 統包，
排程內建在 bot 裡，**不需要動 host 的 crontab**。

## 首次安裝

先準備：Telegram bot token（BotFather）、一個 LLM API key。

```bash
cd /volume1/docker/ytm-bot        # 專案放這，data/ 設成私人共享資料夾
# （建議）編輯 docker-compose.yml,打開 ytm-firefox 的 WEB_AUTHENTICATION 三行——
# 那個 5800 網頁裝著已登入的 Google 帳號
docker compose run --rm setup     # 互動式產 data/bot_config.json,可重跑(Enter 保留原值)
docker compose up -d --build
```

然後照順序完成：

1. **綁定聊天室**：對 bot 說句話，它會回「已綁定」。
2. **建歌曲池**：Telegram 打 `/update` → 選「全部歷史季」（十幾分鐘，會回報進度）。
3. **登入 YT Music**：開 `http://<NAS>:5800`（compose up 時 Firefox 已開著；畫面會停在
   YouTube Music）登入 → Telegram 打 `/cookie` → 按「我登入好了，重新擷取」→
   `/update` 選「訂閱歌手」。

之後不用再管：bot 每 6 小時同步 cookie、每週一凌晨開一下 Firefox 讓 cookie 續期、
cookie 壞了會開好瀏覽器並用 Telegram 通知你去登入、容器開超過 60 分鐘自動關。

## 重建（換 NAS / 重灌）

`data/` 是唯一需要備份的東西（設定、歌曲池、登入憑證都在裡面；
`deploy/nas-firefox/ff-profile/` 也備份的話連 YT Music 都不用重新登入）。

```bash
git clone <repo> /volume1/docker/ytm-bot && cd /volume1/docker/ytm-bot
# 還原 data/(與 ff-profile/)
docker compose up -d --build
```

## 選配：每日隨選歌單

`data/bot_config.json` 加一行 `"daily_pick_count": 20`，重啟 bot
（`docker compose restart ytm-bot`）。每天 08:00 自動建歌單並推播連結。

## 日常維護

| 情況 | 做什麼 |
|---|---|
| Telegram 說 cookie 失效 | 開 `http://<NAS>:5800` 登入，按通知裡的按鈕 |
| 新一季動畫上線 | Telegram `/update` 選「本季新番」（或 `docker exec -w /app ytm-bot python -m ytm.collect --all-seasons`） |
| 想更新訂閱歌手 | `/update` 選「訂閱歌手」（需要有效 cookie） |
| pool 疑似有錯配 | `docker exec -w /app ytm-bot python -m ytm.resolve_pool --repair` |
| 改了 ytm/ 程式碼 | `docker compose restart ytm-bot`（改 requirements.txt 才要 `up -d --build`） |

## 相關文件

為什麼 cookie 只能靠瀏覽器續期、歌名比對為何需要羅馬字轉寫，見 [DESIGN.md](DESIGN.md)。

## 換平台

整套都是標準 Docker Compose，唯一 Synology 相關的只剩路徑慣例（/volume1）。
在任何 Linux 上 `docker compose up -d --build` 即可。
