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
