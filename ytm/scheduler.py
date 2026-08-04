"""bot 內建排程,取代 host cron + firefox-ctl.sh。

為什麼內建:host crontab 會被 DSM 的「任務排程」重寫(靜默失敗,本專案吃過一次
cookie 死五天沒人發現),而獨立排程容器(Ofelia)又多一個容器。收進 bot 之後
排程與 bot 共生死——bot 掛了 Telegram 不回話,本來就會被發現。

語意比照 cron:每分鐘 tick、錯過的觸發點不補跑(bot 重啟橫跨觸發點就算了)。
"""
import datetime as dt
import threading
import time

TICK_SECONDS = 60


class Every:
    def __init__(self, minutes: int):
        self.minutes = minutes

    def next_run(self, after: dt.datetime) -> dt.datetime:
        return after + dt.timedelta(minutes=self.minutes)


class Daily:
    def __init__(self, hour: int, minute: int = 0):
        self.hour, self.minute = hour, minute

    def next_run(self, after: dt.datetime) -> dt.datetime:
        cand = after.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if cand <= after:
            cand += dt.timedelta(days=1)
        return cand


class Weekly:
    def __init__(self, weekday: int, hour: int, minute: int = 0):
        self.weekday, self.hour, self.minute = weekday, hour, minute

    def next_run(self, after: dt.datetime) -> dt.datetime:
        cand = after.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        cand += dt.timedelta(days=(self.weekday - cand.weekday()) % 7)
        if cand <= after:
            cand += dt.timedelta(days=7)
        return cand


class Scheduler:
    def __init__(self):
        self._jobs = []
        self._running: list[threading.Thread] = []

    def add(self, name, schedule, fn):
        self._jobs.append({"name": name, "schedule": schedule, "fn": fn, "next": None})

    def _prime(self, now: dt.datetime):
        for j in self._jobs:
            j["next"] = j["schedule"].next_run(now)

    def _tick(self, now: dt.datetime):
        for j in self._jobs:
            if now >= j["next"]:
                j["next"] = j["schedule"].next_run(now)
                # 各 job 自己一條 thread:warm 會 sleep 180s,不能卡住別的 job
                t = threading.Thread(target=self._run_job, args=(j,), daemon=True,
                                     name=f"sched-{j['name']}")
                t.start()
                self._running.append(t)

    def _run_job(self, j):
        try:
            j["fn"]()
        except Exception as e:
            print(f"[sched] {j['name']} 失敗: {e}", flush=True)

    def _join_running(self):
        for t in self._running:
            t.join(timeout=5)
        self._running.clear()

    def start(self):
        def loop():
            self._prime(dt.datetime.now())
            while True:
                time.sleep(TICK_SECONDS)
                self._tick(dt.datetime.now())
        threading.Thread(target=loop, daemon=True, name="scheduler").start()


# ─── Firefox 容器控制(取代 firefox-ctl.sh) ─────────────────────
#
# 為什麼非得有個真瀏覽器、為什麼「短暫開、用完就關」:見 docs/DESIGN.md 的
# cookie 一節。這裡只允許對 ytm-firefox 做 start/stop/inspect——bot 掛了
# docker.sock 等於 host root,把攻擊面收在這一個模組、三個動作內。

import os
import re
import shutil
import subprocess

from . import cookie

FIREFOX_NAME = "ytm-firefox"
WARM_SECONDS = 180    # 開這麼久才夠頁面載完、cookie 輪替
MAX_UP_MIN = 60       # reap:開超過這麼多分鐘就關(夠從容登入)


def docker_available() -> bool:
    return bool(shutil.which("docker")) and os.path.exists("/var/run/docker.sock")


def _docker(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)


def _inspect(fmt: str) -> str | None:
    r = _docker("inspect", FIREFOX_NAME, "--format", fmt)
    return r.stdout.strip() if r.returncode == 0 else None


def firefox_status() -> str | None:
    return _inspect("{{.State.Status}}")


def _parse_docker_time(s: str) -> dt.datetime:
    # StartedAt 帶 9 位小數(ns),fromisoformat 只吃到 6 位——直接丟掉小數
    return dt.datetime.fromisoformat(re.sub(r"\.\d+", "", s).replace("Z", "+00:00"))


def firefox_uptime_min() -> int | None:
    raw = _inspect("{{.State.StartedAt}}")
    if not raw:
        return None
    started = _parse_docker_time(raw)
    return int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() // 60)


def warm():
    """定期開一下讓 Firefox 向 Google 續期 cookie,然後關掉。
    容器的 FF_OPEN_URL 會自動載入 music.youtube.com,所以 start 就夠。"""
    if firefox_status() == "running":
        print("[sched] warm:已在執行中,跳過(reap 會負責關)", flush=True)
        return
    if _docker("start", FIREFOX_NAME).returncode != 0:
        print("[sched] warm:start 失敗", flush=True)
        return
    time.sleep(WARM_SECONDS)
    _docker("stop", FIREFOX_NAME)
    print(f"[sched] warm 完成:開了 {WARM_SECONDS}s 續期後關閉", flush=True)


def ensure():
    """cookie 壞了就把容器開起來等使用者登入——收到 Telegram 通知時
    5800 已經在聽了。cookie 正常時什麼都不做。(通知由 _cookie_watch 發,這裡不發)"""
    if firefox_status() == "running":
        return
    alive, _ = cookie.check()
    if alive:
        return
    if _docker("start", FIREFOX_NAME).returncode == 0:
        print("[sched] ensure:cookie 失效,已開啟瀏覽器等待登入", flush=True)


def reap():
    """看門狗:手動開來登入之後忘了關,超過 MAX_UP_MIN 分鐘就幫忙關。"""
    if firefox_status() != "running":
        return
    up = firefox_uptime_min()
    if up is not None and up >= MAX_UP_MIN:
        _docker("stop", FIREFOX_NAME)
        print(f"[sched] reap:已開 {up} 分鐘(上限 {MAX_UP_MIN}),已關閉", flush=True)
