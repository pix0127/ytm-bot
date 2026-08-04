import datetime as dt
from unittest import mock

from ytm import scheduler
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
