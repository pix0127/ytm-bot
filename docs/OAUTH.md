# OAuth 與兩個 YouTube API

## 兩個 API，別搞混

| | ytmusicapi 走的 | OAuth 版 daily_pick 走的 |
|---|---|---|
| 端點 | `https://music.youtube.com/youtubei/v1/`（YT Music **內部**私有 API） | `https://www.googleapis.com/youtube/v3/`（官方 **Data API v3**） |
| 認證 | browser cookie（`data/browser.json`） | OAuth 2.0 |
| OAuth 可用？ | ❌ 對 OAuth token 回 HTTP 400 | ✅ 完全支援 |

**實測（2026-07-23）**：同一顆 scope=`.../auth/youtube` 的 OAuth token —— 打 ytmusicapi 內部端點
search/library/create 全 400；直接打官方 v3 的 playlists.list/insert、search.list、playlistItems.insert、
playlists.delete **全部 2xx**。結論：「OAuth 不能管歌單」是錯的，正確是「ytmusicapi 的內部端點不吃 OAuth」。

## 為什麼只有 daily_pick 用 OAuth

Data API v3 預設配額 **10,000 units/天**，`search.list`=100、`playlistItems.insert`=50、`playlists.insert/delete`=50。

- **daily_pick**（20 首、用 pool 存好的 video_id 免搜尋）≈ 建 50 + 20×50 + 刪 50 ≈ **1,100 units** → 塞得下 ✅
- **yearly_playlists**（單一歌單 ~600 首插入）= 30,000 units ≈ 3 天配額 → ❌
- **resolve_pool / collect**（數千次 search）= 數十萬 units → ❌，且需 YT Music 歌曲目錄（Data API 只有影片）

故 daily_pick 以外一律維持 browser auth。

## 設定

1. 在 Google Cloud 建「TV and Limited Input devices」型 OAuth client，啟用 YouTube Data API v3。
2. 憑證放 `data/oauth_client.json`（`{"client_id":..., "client_secret":...}`）或環境變數 `YTM_OAUTH_CLIENT_ID`/`YTM_OAUTH_CLIENT_SECRET`。
3. `python -m ytm.oauth` 跑一次 device flow（瀏覽器輸入代碼授權）→ token 存 `data/oauth.json`，之後自動 refresh。
4. 排程用 `deploy/run_daily.sh`。

`data/` 全 gitignored（含 client_secret、token）。若曾外流 client_secret，到 Google Cloud 憑證頁重設後更新 `data/oauth_client.json`。
