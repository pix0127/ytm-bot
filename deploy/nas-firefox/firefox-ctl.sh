#!/bin/sh
# ytm-firefox 容器的生命週期管理。裝進 NAS 的 crontab（見檔尾）。
#
# 為什麼要這支：那個 5800 網頁裝著一個已登入的 Google session，不該長時間開著。
# 而 cookie 續期又必須讓真瀏覽器實際載入頁面（Google 只認當初登入的那個瀏覽器，
# 純 HTTP 請求不會讓它補發 __Secure-1PSIDTS）。所以做法是「短暫開、用完就關」。
#
# warm  : 定期開一下讓 Firefox 自己向 Google 續期，然後關掉。
#         容器的 FF_OPEN_URL 會自動載入 music.youtube.com，所以只要 start 就好。
# reap  : 看門狗。你手動開來登入之後忘了關，超過 MAX_UP 分鐘就幫你關掉。
#
# 用法: firefox-ctl.sh warm | reap | status

DOCKER=/usr/local/bin/docker
NAME=ytm-firefox
WARM_SECONDS=180      # 開著多久才夠讓頁面載完、cookie 輪替
MAX_UP=60             # reap：開超過這麼多分鐘就關（夠你從容登入）

uptime_min() {
    started=$($DOCKER inspect "$NAME" --format '{{.State.StartedAt}}' 2>/dev/null) || return 1
    [ -n "$started" ] || return 1
    s=$(date -d "$started" +%s 2>/dev/null) || return 1
    echo $(( ($(date +%s) - s) / 60 ))
}

running() {
    [ "$($DOCKER inspect "$NAME" --format '{{.State.Status}}' 2>/dev/null)" = "running" ]
}

case "$1" in
  warm)
    running && { echo "已在執行中，跳過 warm（reap 會負責關）"; exit 0; }
    $DOCKER start "$NAME" >/dev/null || exit 1
    sleep "$WARM_SECONDS"
    $DOCKER stop "$NAME" >/dev/null
    echo "warm 完成：開了 ${WARM_SECONDS}s 讓 Firefox 續期後關閉"
    ;;
  reap)
    running || exit 0
    up=$(uptime_min) || exit 0
    if [ "$up" -ge "$MAX_UP" ]; then
        $DOCKER stop "$NAME" >/dev/null
        echo "reap：已開 ${up} 分鐘（上限 ${MAX_UP}），已關閉"
    fi
    ;;
  status)
    if running; then echo "running，已開 $(uptime_min) 分鐘"; else echo "stopped"; fi
    ;;
  *)
    echo "用法: $0 warm|reap|status"; exit 1;;
esac

# 安裝到 NAS 的 /etc/crontab（tab 分隔，DSM 要求）：
#   0	5	*	*	1	root	/volume1/docker/ytm-tools/deploy/nas-firefox/firefox-ctl.sh warm
#   */10	*	*	*	*	root	/volume1/docker/ytm-tools/deploy/nas-firefox/firefox-ctl.sh reap
# 改完 crontab 要 synoservice --restart crond。
# 注意：DSM 在使用者於「控制台 → 任務排程」增刪任務時會重寫 /etc/crontab，
# 這兩行可能被清掉，屆時重新加回即可。
