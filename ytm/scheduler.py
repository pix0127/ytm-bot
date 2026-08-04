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
