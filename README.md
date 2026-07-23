# ytm-tools

從新番 OP/ED 與訂閱歌手自動產生 YouTube Music 歌單的一組 CLI 工具。

- 資料來源：[AnimeThemes.moe](https://animethemes.moe)（新番 OP/ED 的歌名/歌手 metadata）+ 你在 YouTube Music 訂閱的歌手熱門歌。
- 認證：**browser auth**（`data/browser.json`，貼 music.youtube.com 的 request headers）。OAuth 因 YouTube 封鎖內部 API 而不可用，詳見下方。

## 結構

```
ytm/            程式 package（python -m ytm.<script> 執行）
data/           執行時資料與機密（gitignored）
  pool.json       歌曲大池（每首含真實 videoId）
  browser.json    認證 cookie（= 半個 Google 帳號，勿外流）
  blocklist.json  按爛歌曲黑名單
  state/          各歌單的 playlistId 狀態
  backups/        pool.json 覆寫前的備份
deploy/         Dockerfile、Synology 排程 wrapper
tools/          refresh_ytm_cookie.py（在 Windows 端跑，產生 browser.json）
docs/           SETUP.md（家用 NAS + PC 部署）
```

## 指令（於專案根目錄執行）

```bash
python -m ytm.collect                 # 收集新番 + 訂閱歌手 → data/pool.json
python -m ytm.resolve_pool            # 把 pool 每首對應到真實 videoId，查不到的刪除
python -m ytm.daily_pick --count 20   # 每日隨選歌單
python -m ytm.yearly_playlists        # 各年度新番歌單（--year 2026 / --update）
python -m ytm.anime_playlist_gen      # 本季新番歌單
python -m ytm.prune_disliked          # 把按爛(DISLIKE)的歌從歌單 + pool 移除、加黑名單
```

資料目錄可用環境變數 `YTM_DATA_DIR` 覆蓋（Docker / NAS 共享資料夾用）。

## 認證更新

cookie 幾個月失效一次。在**登入了 music.youtube.com 的 Windows 機器**上：

```bash
pip install browser_cookie3
python tools/refresh_ytm_cookie.py data/browser.json
```

## OAuth 為何不用

ytmusicapi 走的是 YouTube Music 內部 `youtubei/v1` API，Google 於 2024/11 封掉其 OAuth token（每個請求回 HTTP 400，issue #676）。官方 **YouTube Data API v3** 的 OAuth 雖可用，但配額（10,000 units/天、search 100 units、加歌 50 units）撐不起批次建歌單，且操作的是 YouTube 影片而非 YT Music 歌曲目錄。故 browser auth 為唯一可行方案。
