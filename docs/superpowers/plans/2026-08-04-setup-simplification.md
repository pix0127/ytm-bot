# 部署流程簡化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重建流程收斂為 `git clone → 還原 data/ → docker compose up -d --build`；排程（warm/ensure/reap/daily_pick）內建進 bot，host crontab 清空。

**Architecture:** 新模組 `ytm/scheduler.py`（純手刻 thread scheduler + docker CLI 開關 sibling 容器），由 `telegram_bot.main()` 啟動；根目錄一份 `docker-compose.yml` 統包 ytm-bot（掛 docker.sock）與 ytm-firefox；`firefox-ctl.sh`、心跳機制、`run_daily.sh` 刪除。

**Tech Stack:** Python 3.12（stdlib only：threading/subprocess/datetime）、Docker Compose、pytest（僅開發用，不進 requirements.txt）。

**Spec:** `docs/superpowers/specs/2026-08-04-setup-simplification-design.md`

## Global Constraints

- 不新增 runtime 依賴：`requirements.txt` 維持 `ytmusicapi==1.12.1`、`pykakasi==2.3.0`、`requests>=2.31`。
- 不引入 APScheduler / docker SDK / python-telegram-bot / Ofelia。
- bot 內 docker 操作只允許對 `ytm-firefox` 做 `start/stop/inspect` 三種。
- 排程時間固定寫死（單一使用者專案）：warm＝週一 05:00、ensure/reap＝每 10 分、daily＝08:00。
- 時間用容器本地時間（compose 設 `TZ=Asia/Taipei`）；bot 重啟橫跨觸發點不補跑（比照 cron）。
- 註解風格照現有檔案：解釋「為什麼」的中文註解，不寫廢話註解。
- 測試指令：`python3.12 -m pytest tests/ -v`（pytest 沒裝先 `python3.12 -m pip install pytest`）。
- 本機（開發機）沒有 docker daemon 可測 —— docker 呼叫一律 mock，不做整合測試（手動驗證清單在 Task 7）。

---

### Task 1: scheduler 核心（排程時間計算 + runner thread）

**Files:**
- Create: `ytm/scheduler.py`
- Test: `tests/test_scheduler.py`（新目錄，附空 `tests/__init__.py` 不需要——pytest rootdir 直接收）

**Interfaces:**
- Produces:
  - `class Every(minutes: int)` / `class Daily(hour, minute=0)` / `class Weekly(weekday, hour, minute=0)`，各有 `next_run(after: datetime) -> datetime`（回傳嚴格大於 `after` 的下一次觸發時間；`weekday` 同 `datetime.weekday()`，週一=0）
  - `class Scheduler`：`add(name: str, schedule, fn: Callable[[], None])`、`start()`（spawn daemon thread，每 60s tick，到期 job 各自 spawn thread 執行，例外 catch+log 不殺 scheduler）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_scheduler.py
import datetime as dt

from ytm.scheduler import Every, Daily, Weekly


def test_every_10min():
    t = dt.datetime(2026, 8, 4, 12, 0, 0)
    assert Every(10).next_run(t) == dt.datetime(2026, 8, 4, 12, 10, 0)


def test_daily_before_and_after_trigger():
    daily = Daily(8, 0)
    # 觸發點之前 → 當天 08:00
    assert daily.next_run(dt.datetime(2026, 8, 4, 7, 59)) == dt.datetime(2026, 8, 4, 8, 0)
    # 正好在觸發點 → 隔天（next_run 必須嚴格大於 after，否則同一分鐘重複觸發）
    assert daily.next_run(dt.datetime(2026, 8, 4, 8, 0)) == dt.datetime(2026, 8, 5, 8, 0)
    # 觸發點之後 → 隔天
    assert daily.next_run(dt.datetime(2026, 8, 4, 9, 0)) == dt.datetime(2026, 8, 5, 8, 0)


def test_weekly_monday_5am():
    weekly = Weekly(0, 5, 0)          # 週一 05:00
    # 2026-08-04 是週二 → 下週一 08-10
    assert weekly.next_run(dt.datetime(2026, 8, 4, 12, 0)) == dt.datetime(2026, 8, 10, 5, 0)
    # 週一 04:00 → 當天 05:00
    assert weekly.next_run(dt.datetime(2026, 8, 10, 4, 0)) == dt.datetime(2026, 8, 10, 5, 0)
    # 週一正好 05:00 → 下週一
    assert weekly.next_run(dt.datetime(2026, 8, 10, 5, 0)) == dt.datetime(2026, 8, 17, 5, 0)


def test_scheduler_runs_due_job_and_survives_exception(monkeypatch):
    """不真的 sleep：把 tick 迴圈抽成 _tick(now)，直接餵時間。"""
    from ytm.scheduler import Scheduler
    ran = []
    sched = Scheduler()
    sched.add("ok", Every(10), lambda: ran.append("ok"))
    sched.add("boom", Every(10), lambda: 1 / 0)
    t0 = dt.datetime(2026, 8, 4, 12, 0)
    sched._prime(t0)
    sched._tick(t0 + dt.timedelta(minutes=9))   # 未到期
    sched._tick(t0 + dt.timedelta(minutes=10))  # 到期：ok 執行、boom 例外不炸
    sched._join_running()                        # 等 job threads 收尾（測試用）
    assert ran == ["ok"]
    # 到期後 next 已推進，同一時間再 tick 不重跑
    sched._tick(t0 + dt.timedelta(minutes=10))
    sched._join_running()
    assert ran == ["ok"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3.12 -m pytest tests/test_scheduler.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'ytm.scheduler'`）

- [ ] **Step 3: 最小實作**

```python
# ytm/scheduler.py
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3.12 -m pytest tests/test_scheduler.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add ytm/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): bot 內建排程核心——每分鐘 tick、錯過不補跑"
```

---

### Task 2: Firefox 容器控制（warm / ensure / reap）

**Files:**
- Modify: `ytm/scheduler.py`（追加在檔尾）
- Test: `tests/test_scheduler.py`（追加）

**Interfaces:**
- Consumes: `ytm.cookie.check() -> tuple[bool, str]`（既有）
- Produces:
  - `docker_available() -> bool`
  - `firefox_status() -> str | None`（"running"/"exited"/…；容器不存在回 None）
  - `firefox_uptime_min() -> int | None`
  - `warm()` / `ensure()` / `reap()`（給 Scheduler 的 job fn）
  - 常數 `FIREFOX_NAME = "ytm-firefox"`、`WARM_SECONDS = 180`、`MAX_UP_MIN = 60`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_scheduler.py`：

```python
from unittest import mock

from ytm import scheduler


def test_parse_started_at_nanoseconds():
    # docker inspect 的 StartedAt 是 9 位小數 + Z,fromisoformat 吃不下,要先修剪
    got = scheduler._parse_docker_time("2026-08-04T02:11:00.123456789Z")
    assert got == dt.datetime(2026, 8, 4, 2, 11, 0, tzinfo=dt.timezone.utc)


def test_reap_stops_only_when_over_limit():
    with mock.patch.object(scheduler, "firefox_status", return_value="running"), \
         mock.patch.object(scheduler, "firefox_uptime_min", return_value=59), \
         mock.patch.object(scheduler, "_docker") as d:
        scheduler.reap()
        d.assert_not_called()
    with mock.patch.object(scheduler, "firefox_status", return_value="running"), \
         mock.patch.object(scheduler, "firefox_uptime_min", return_value=60), \
         mock.patch.object(scheduler, "_docker") as d:
        scheduler.reap()
        d.assert_called_once_with("stop", scheduler.FIREFOX_NAME)


def test_ensure_starts_only_when_cookie_dead_and_stopped():
    # cookie 正常 → 不動
    with mock.patch.object(scheduler, "firefox_status", return_value="exited"), \
         mock.patch("ytm.cookie.check", return_value=(True, "ok")), \
         mock.patch.object(scheduler, "_docker") as d:
        scheduler.ensure()
        d.assert_not_called()
    # cookie 失效且容器沒開 → start
    with mock.patch.object(scheduler, "firefox_status", return_value="exited"), \
         mock.patch("ytm.cookie.check", return_value=(False, "dead")), \
         mock.patch.object(scheduler, "_docker") as d:
        scheduler.ensure()
        d.assert_called_once_with("start", scheduler.FIREFOX_NAME)
    # 已在跑 → 不動(等使用者登入,reap 會收)
    with mock.patch.object(scheduler, "firefox_status", return_value="running"), \
         mock.patch.object(scheduler, "_docker") as d:
        scheduler.ensure()
        d.assert_not_called()


def test_warm_skips_when_running(monkeypatch):
    with mock.patch.object(scheduler, "firefox_status", return_value="running"), \
         mock.patch.object(scheduler, "_docker") as d:
        scheduler.warm()
        d.assert_not_called()


def test_warm_start_sleep_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "firefox_status", lambda: "exited")
    monkeypatch.setattr(scheduler, "_docker", lambda *a: calls.append(a) or mock.Mock(returncode=0))
    monkeypatch.setattr(scheduler.time, "sleep", lambda s: calls.append(("sleep", s)))
    scheduler.warm()
    assert calls == [("start", "ytm-firefox"), ("sleep", 180), ("stop", "ytm-firefox")]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3.12 -m pytest tests/test_scheduler.py -v`
Expected: 新增的 5 個測試 FAIL（AttributeError），原 4 個 PASS

- [ ] **Step 3: 實作**

追加到 `ytm/scheduler.py`：

```python
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
```

注意：`import time` / `import datetime as dt` Task 1 已有，不重複。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3.12 -m pytest tests/test_scheduler.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add ytm/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): warm/ensure/reap 移植自 firefox-ctl.sh"
```

---

### Task 3: daily_pick 抽出可重用入口

**Files:**
- Modify: `ytm/daily_pick.py`

**Interfaces:**
- Produces: `daily_pick.run(count: int) -> dict`（回傳 `{"url": str, "count": int, "added": int}`；pool 缺失時 raise `RuntimeError`，不再 `sys.exit`——scheduler in-process 呼叫不能被 exit 殺掉）
- CLI 行為不變：`python -m ytm.daily_pick --count 20 --dry-run`

- [ ] **Step 1: 重構**

`main()` 拆成兩層：挑歌 + 建歌單邏輯進 `run()`，argparse / dry-run / 印輸出留在 `main()`：

```python
def _load_songs() -> list[dict]:
    if not os.path.exists(POOL_FILE):
        raise RuntimeError("pool.json 不存在，請先執行 collect / resolve_pool")
    with open(POOL_FILE) as f:
        pool = json.load(f).get("songs", [])
    songs = [s for s in pool if s.get("video_id")]   # 只用已解析出 videoId 的
    if not songs:
        raise RuntimeError("pool 中沒有含 video_id 的歌，請先執行 resolve_pool")
    return songs


def run(count: int) -> dict:
    songs = _load_songs()
    picked = random.sample(songs, min(count, len(songs)))
    state = load_state()
    name = f"今日隨選 ({datetime.now().strftime('%m/%d')})"
    pid = playlist.new_playlist(state.get("playlist_id"), name, PLAYLIST_DESC)
    res = playlist.fill_playlist(pid, [s["video_id"] for s in picked], skip=load_blocked_ids())
    state["playlist_id"] = pid
    save_state(state)
    return {"url": res["url"], "count": len(picked), "added": res["added"],
            "skipped": res["skipped"], "failed": res["failed"], "name": name}
```

`main()` 改為：dry-run 路徑自己 sample + 印列表後 return；正式路徑 `info = run(args.count)` 再印原本那些行（`✅ 已建立`、`✅ 已加入 …`、`🔗 …`、`JSON:…`），錯誤 `except RuntimeError as e: print(f"❌ {e}"); sys.exit(1)`。刪除原本散在 `load_pool()`/`main()` 裡被取代的碼（`load_pool` 整個併入 `_load_songs`）。

- [ ] **Step 2: 驗證 CLI 不變**

Run: `cd /synosrc/projects/ytm-tools && python3.12 -m ytm.daily_pick --dry-run`
Expected: 印出抽選清單 + `🔍 乾跑模式，未寫入`（本機 data/pool.json 存在，可跑）

- [ ] **Step 3: Commit**

```bash
git add ytm/daily_pick.py
git commit -m "refactor(daily_pick): 抽出 run() 供 scheduler in-process 呼叫"
```

---

### Task 4: telegram_bot 接上 scheduler、拆心跳、設定檔等待

**Files:**
- Modify: `ytm/telegram_bot.py`

**Interfaces:**
- Consumes: `scheduler.Scheduler/Every/Daily/Weekly/warm/ensure/reap/docker_available`（Task 1、2）、`daily_pick.run(count)`（Task 3）

- [ ] **Step 1: 移除心跳檢查**

- 刪 `_sched_stale()` 函式（`telegram_bot.py:310-323`）與常數 `SCHED_STALE_AFTER`。
- `_cookie_watch()` 內刪 `sched_warned` 變數與 `stale = _sched_stale()` 起的 if/elif 區塊（`telegram_bot.py:360, 372-380`）。
- `STATE_DIR` import 若因此不再使用則從 import 行移除（先 grep 確認：`grep -n STATE_DIR ytm/telegram_bot.py`）。

- [ ] **Step 2: 設定檔缺失改等待而非退出**

`_cfg()`（`telegram_bot.py:52-55`）改成：

```python
def _cfg() -> dict:
    """沒有設定檔就等,不退出。

    立刻退出配上 restart: unless-stopped 會無限重啟,那時 docker exec 會被拒,
    使用者反而沒辦法補救(SETUP.md 以前為此警告要用臨時容器)。等著讓
    `docker compose run --rm setup` 隨時可以補設定。"""
    warned = False
    while not os.path.exists(BOT_CONFIG_FILE):
        if not warned:
            print(f"找不到 {BOT_CONFIG_FILE}——請跑 `docker compose run --rm setup`,"
                  f"完成後會自動繼續啟動", flush=True)
            warned = True
        time.sleep(60)
    return json.load(open(BOT_CONFIG_FILE))
```

- [ ] **Step 3: main() 啟動 scheduler**

`main()`（`telegram_bot.py:498` 起）在 `_set_commands(token)` 之後加：

```python
    from . import daily_pick, scheduler

    sched = scheduler.Scheduler()
    if scheduler.docker_available():
        sched.add("warm", scheduler.Weekly(0, 5), scheduler.warm)
        sched.add("ensure", scheduler.Every(10), scheduler.ensure)
        sched.add("reap", scheduler.Every(10), scheduler.reap)
    else:
        # 沒掛 docker.sock(例如本機開發)時 bot 照常跑,只是不管瀏覽器
        print("⚠️  docker 不可用,warm/ensure/reap 排程停用", flush=True)
    n = cfg.get("daily_pick_count")
    if n:
        def _daily():
            info = daily_pick.run(int(n))
            chat = cfg.get("allowed_chat_id")
            if chat:
                _send(token, chat, f"🎵 {info['name']}:已加入 {info['added']}/{info['count']} 首\n"
                                   f"🔗 {info['url']}")
        sched.add("daily_pick", scheduler.Daily(8), _daily)
    sched.start()
```

- [ ] **Step 4: 手動 smoke test**

Run: `cd /synosrc/projects/ytm-tools && timeout 5 python3.12 -m ytm.telegram_bot; echo exit=$?`
Expected: 印出 `⚠️ docker 不可用…`（本機沒 socket）與 `bot 啟動,long-poll 中…`，timeout 殺掉 exit=124。跑 `python3.12 -m pytest tests/ -v` 全綠。

- [ ] **Step 5: Commit**

```bash
git add ytm/telegram_bot.py
git commit -m "feat(bot): 內建排程取代 host cron;設定檔缺失改等待;拆心跳檢查"
```

---

### Task 5: Dockerfile + 根目錄 docker-compose.yml

**Files:**
- Modify: `deploy/Dockerfile`
- Create: `docker-compose.yml`（專案根目錄）

**Interfaces:**
- Produces: services `ytm-bot`、`ytm-firefox`、`setup`（profile）；容器名沿用 `ytm-bot`/`ytm-firefox`（scheduler 的 `FIREFOX_NAME` 依賴後者）

- [ ] **Step 1: Dockerfile 加 docker CLI 與 tzdata**

在 `pip install` 之後、`COPY ytm/` 之前插入：

```dockerfile
# docker CLI(只有 client):bot 內建排程要開關 ytm-firefox 容器。
# 用官方 static binary,不裝 docker.io(那包含整個 daemon)。
RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata \
    && arch="$(uname -m)" \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-27.5.1.tgz" \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
```

並把 `CMD` 改為 `CMD ["python", "-m", "ytm.telegram_bot"]`（bot 是主要用途；daily_pick 已內建，不再是預設進入點）。

- [ ] **Step 2: 建 docker-compose.yml**

```yaml
# 一份 compose 統包。重建流程:git clone → 還原 data/ → docker compose up -d --build
# 首次安裝多一步:docker compose run --rm setup(產 data/bot_config.json)
services:
  ytm-bot:
    build: { context: ., dockerfile: deploy/Dockerfile }
    image: ytm-bot:latest
    container_name: ytm-bot
    restart: unless-stopped
    environment:
      - TZ=Asia/Taipei                 # 排程(warm/daily)照本地時間跑
    volumes:
      - ./ytm:/app/ytm                 # 覆蓋 image 內那份,改 code 只要 restart
      - ./data:/app/data
      - ./deploy/nas-firefox/ff-profile:/app/ff-profile:ro
      # bot 內建排程要開關 ytm-firefox。這給了容器 host root 等級能力——
      # 接受理由與範圍限制見 docs/superpowers/specs/2026-08-04-setup-simplification-design.md
      - /var/run/docker.sock:/var/run/docker.sock

  # 按需 Firefox——只在登入/續期時開,平時停著(0 CPU/0 RAM),開關由 bot 排程管。
  # compose up 會把它帶起來一次:正好留給首次登入用,60 分鐘內 reap 會自動關。
  # 為什麼非得有真瀏覽器:見 docs/DESIGN.md 的 cookie 一節。
  ytm-firefox:
    image: jlesage/firefox:latest      # 有 arm64,Synology ARM 也能跑
    container_name: ytm-firefox
    restart: "no"
    ports:
      - "5800:5800"                    # 網頁 GUI(建議只開內網)
    volumes:
      - ./deploy/nas-firefox/ff-profile:/config   # 登入狀態存這
    shm_size: "1gb"                    # 2GB RAM 的機型要降到 256m,否則會 OOM
    environment:
      - TZ=Asia/Taipei
      # 一開容器就自己載入 YTM:讓「開一下續期 cookie」只要 start 就完成
      - FF_OPEN_URL=https://music.youtube.com/
      # 強烈建議開啟存取保護(它裝著你的 Google session):
      # - WEB_AUTHENTICATION=1
      # - WEB_AUTHENTICATION_USERNAME=you
      # - WEB_AUTHENTICATION_PASSWORD=set-a-password

  # 首次設定:docker compose run --rm setup
  setup:
    profiles: ["setup"]
    build: { context: ., dockerfile: deploy/Dockerfile }  # 首次 run 時 image 還沒 build,要能自己 build
    image: ytm-bot:latest
    volumes:
      - ./data:/app/data
    command: python -m ytm.setup
    stdin_open: true
    tty: true
```

- [ ] **Step 3: 驗證 compose 語法**

Run: `cd /synosrc/projects/ytm-tools && (docker compose config -q || docker-compose config -q) 2>&1 || python3.12 -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml')); print('yaml ok')"`
Expected: 無錯誤（本機沒 docker 時至少 YAML 解析通過）

- [ ] **Step 4: Commit**

```bash
git add deploy/Dockerfile docker-compose.yml
git commit -m "feat(deploy): 根目錄 compose 統包兩容器,image 內建 docker CLI"
```

---

### Task 6: 刪舊排程機制 + 改寫 SETUP.md

**Files:**
- Delete: `deploy/nas-firefox/firefox-ctl.sh`、`deploy/nas-firefox/docker-compose.yml`、`deploy/run_daily.sh`、`deploy/daily_pick.sh`
- Modify: `docs/SETUP.md`（整檔改寫）、`README.md`（快速開始若引用舊指令則同步，先 grep：`grep -n "run_daily\|firefox-ctl\|crontab\|docker run" README.md`）

- [ ] **Step 1: 刪檔**

```bash
git rm deploy/nas-firefox/firefox-ctl.sh deploy/nas-firefox/docker-compose.yml \
       deploy/run_daily.sh deploy/daily_pick.sh
```

（`ff-profile/` 是 gitignored 執行時資料，留在原位不動。）

- [ ] **Step 2: 改寫 docs/SETUP.md**

整檔取代為（保留原文件裡仍然成立的警語，砍掉 crontab/心跳/臨時容器章節）：

````markdown
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
````

- [ ] **Step 3: 同步 README 與 DESIGN.md**

- `grep -n "crontab\|firefox-ctl\|run_daily\|host cron" README.md docs/DESIGN.md`
- README 快速開始改成新三條指令；DESIGN.md「排程為何用 host cron」一節改寫為「排程為何內建進 bot」：
  保留歷史脈絡（曾用 host cron、被 DSM 重寫過、曾靠心跳偵測），說明現在收進 bot 的理由
  （少容器、消滅 DSM 重寫失敗模式）與代價（docker.sock、bot 死排程死——接受理由照 spec 安全考量一節）。

- [ ] **Step 4: Commit**

```bash
git add -A deploy/ docs/ README.md
git commit -m "docs+chore: 排程內建後刪 firefox-ctl/run_daily,SETUP 收斂成三條指令"
```

---

### Task 7: NAS 上實地驗證（手動，不寫程式）

前置：這台 NAS（10.17.20.194）上的舊 `ytm-bot` 已 `docker stop`。**先清 host crontab 的四行**
（三行 firefox-ctl + 一行 run_daily），`synoservice --restart crond`。

- [ ] **Step 1**: NAS 上 `git pull`（或 clone 新目錄），確認 `data/` 在原位。
- [ ] **Step 2**: 舊容器改名保留退路：`docker rename ytm-bot ytm-bot-old`（ytm-firefox 沿用，名字沒變）。
      註：compose 會接管既有的 `ytm-firefox` 容器需先移除：`docker rm -f ytm-firefox`（profile 在
      bind mount,不會丟）。
- [ ] **Step 3**: `docker compose up -d --build` → `docker logs ytm-bot` 看到
      `bot 啟動,long-poll 中…` 且**沒有** `docker 不可用` 警告。
- [ ] **Step 4**: Telegram `/cookie` 正常回覆（cookie 檢查路徑通）。
- [ ] **Step 5**: `docker ps` 確認 ytm-firefox 在跑（compose up 帶起）；等 60 分鐘確認 reap 自動關掉
      （或臨時把 `MAX_UP_MIN` 調成 2 驗證後改回）。
- [ ] **Step 6**: 設 `daily_pick_count` 並把 `Daily(8)` 暫改為幾分鐘後的時刻驗證推播,驗完改回、
      `docker compose restart ytm-bot`。
- [ ] **Step 7**: 都綠了 → `docker rm ytm-bot-old`,GitHub push（需使用者同意）。
