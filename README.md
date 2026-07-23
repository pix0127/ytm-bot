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
python -m ytm.daily_pick --count 20   # 每日隨選歌單（OAuth / Data API v3，需先 ytm.oauth 授權）
python -m ytm.yearly_playlists        # 各年度新番歌單（--year 2026 / --update）
python -m ytm.anime_playlist_gen      # 本季新番歌單
python -m ytm.prune_disliked          # 把按爛(DISLIKE)的歌從歌單 + pool 移除、加黑名單
```

資料目錄可用環境變數 `YTM_DATA_DIR` 覆蓋（Docker / NAS 共享資料夾用）。

### daily_pick 的 OAuth 設定（一次性）

`daily_pick` 走**官方 YouTube Data API v3 + OAuth**：自動 refresh、免維護 cookie，適合 NAS 無人值守；
只用 pool 已解析好的 video_id（免搜尋），每日配額約 1,100 units（遠低於 10,000/天）。

```bash
# client 憑證放 data/oauth_client.json（或環境變數 YTM_OAUTH_CLIENT_ID/SECRET）
python -m ytm.oauth                    # 一次性 device-flow 授權 → data/oauth.json（之後自動 refresh）
```

詳見 [docs/OAUTH.md](docs/OAUTH.md)（兩個 API 的差異與配額）。

## 認證更新

cookie 幾個月失效一次。在**登入了 music.youtube.com 的 Windows 機器**上：

```bash
pip install browser_cookie3
python tools/refresh_ytm_cookie.py data/browser.json
```

## 認證架構：為何 daily_pick 用 OAuth、其餘用 browser

- **daily_pick → OAuth（官方 Data API v3）**：每日小量（~1,100 units），且直接用 pool 存好的 video_id，
  免搜尋、免 cookie、token 自動 refresh，最適合無人值守。
- **collect / resolve_pool / yearly_playlists / prune / anime → browser auth**：它們需要 YT Music
  歌曲目錄搜尋，且批次量大（數千次 search / insert）遠超 Data API v3 的 10,000 units/天配額。這些只能走
  ytmusicapi 的內部 `youtubei/v1` API，而該內部 API 不吃 OAuth（回 HTTP 400，issue #676），故用 cookie。

兩個 API 的實測比較與配額細節見 [docs/OAUTH.md](docs/OAUTH.md)。
