"""status: `status` and `--wait`."""
import sys
import time as time_mod
import urllib.error

from support import CFG, clean_dirs, hold_lock
from yolk import api, cli, config, schedule, status, store


def check_status_and_wait():
    clean_dirs()
    config.STATUS_FILE.unlink(missing_ok=True)
    pending = {"sets": []}
    briefs = {"list": []}

    def fake_api(cfg, method, path, body=None, auth=True):
        if path.startswith("/v1/questions/pending"):
            return 200, {"question_sets": pending["sets"]}
        if path.startswith("/v1/briefs"):
            return 200, {"briefs": briefs["list"]}
        return 204, None
    real = api.request
    api.request = fake_api
    old = (config.WAIT_POLL_S, config.WAIT_SYNC_S)
    config.WAIT_POLL_S = config.WAIT_SYNC_S = 0.2
    try:
        # running reflects the lock, and asking never blocks (nor leaves the lock held)
        t0 = time_mod.monotonic()
        assert status.report(CFG)["running"] is False
        helper = hold_lock(30)
        try:
            assert status.report(CFG)["running"] is True
        finally:
            helper.kill()
            helper.wait()
        assert status.report(CFG)["running"] is False and time_mod.monotonic() - t0 < 5
        with open(config.YOLK_HOME / ".lock", "w") as f:  # the poller can still take the lock afterwards
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # offline: the cached open check-ins
        store.set_status(open_check_ins=["2026-10-01"])
        api.request = lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down"))
        assert status.report(CFG)["open_check_ins"] == ["2026-10-01"]
        api.request = fake_api
        store.set_status(open_check_ins=[])
        (config.DAYS_DIR / "2026-10-05.summary.json").write_text("{}")
        (config.BRIEFS_DIR / "2026-10-04.json").write_text("{}")
        s = status.report(CFG)
        assert s["reports_ready"] == ["2026-10-05"] and s["briefs"] == ["2026-10-04"] and s["newest_brief"] == "2026-10-04"

        # --wait returns once the lock is released
        helper = hold_lock(1.5)
        t0 = time_mod.monotonic()
        s = status.wait(CFG, 30)
        waited = time_mod.monotonic() - t0
        helper.wait()
        assert 1.0 < waited < 10 and s["running"] is False, waited

        # --wait returns at the timeout when a check-in never gets its brief
        pending["sets"] = [{"id": "qs_x", "date": "2026-10-07"}]
        t0 = time_mod.monotonic()
        s = status.wait(CFG, 1)
        assert 0.9 < time_mod.monotonic() - t0 < 6 and s["open_check_ins"] == ["2026-10-07"]

        # ...and returns early once the brief arrives
        calls = {"n": 0}

        def api_brief(cfg, method, path, body=None, auth=True):
            if path.startswith("/v1/briefs"):
                calls["n"] += 1
                if calls["n"] >= 3:
                    return 200, {"briefs": [{"date": "2026-10-07", "brief": {"v": 1}}]}
            return fake_api(cfg, method, path, body, auth)
        api.request = api_brief
        t0 = time_mod.monotonic()
        s = status.wait(CFG, 30)
        assert time_mod.monotonic() - t0 < 10 and "2026-10-07" in s["briefs"], s
    finally:
        api.request = real
        config.WAIT_POLL_S, config.WAIT_SYNC_S = old
    # status never heals the poller
    seen = []
    real_heal, schedule.heal_schedule = schedule.heal_schedule, lambda: seen.append(1)
    real_cfg, config.load_config = config.load_config, lambda: CFG
    real_report = status.report
    status.report = lambda cfg: {"ok": True}
    try:
        sys.argv = ["employee_runner.py", "status"]
        cli.main()
    finally:
        schedule.heal_schedule, config.load_config, status.report = real_heal, real_cfg, real_report
    assert not seen
