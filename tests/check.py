#!/usr/bin/env python3
"""All checks, one process, one temp YOLK_HOME. No pytest, no network, no real claude.

    python3 tests/check.py

Order matters where checks share the temp home. `check_baseline.py` (a black-box run of the whole
poll against tests/baseline.json) is run separately: `python3 tests/check_baseline.py`.
"""
import contextlib
import io
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
import check_activity, check_checkin, check_claude, check_cli, check_config, check_launcher  # noqa: E402,E401
import check_poll, check_privacy, check_schedule, check_status, check_store  # noqa: E402,E401


def run_all():
    check_privacy.check_contract()
    check_activity.check_missing_days()
    check_cli.check_claim()
    check_store.check_latest_brief()
    check_poll.check_sync_briefs_and_heartbeat_glob()
    scripts_dir = check_activity.check_collectors()
    check_checkin.check_heartbeat_and_answers_flush()
    check_activity.check_summarize_and_investigate(scripts_dir)
    check_checkin.check_atomic_and_corrupt_outbox()
    check_activity.check_attempt_cap(scripts_dir)
    check_poll.check_poll_order(scripts_dir)
    check_claude.check_claude_flags()
    check_config.check_day_boundary()
    check_store.check_prune()
    check_schedule.check_heal_schedule()
    check_schedule.check_heal_installs_missing_plist()
    check_schedule.check_install_script_plist()
    check_launcher.check_launcher()
    check_poll.check_status_phases(scripts_dir)
    check_status.check_status_and_wait()
    check_schedule.check_heal_action_and_hook()
    check_store.check_no_reports_dir()


if __name__ == "__main__":
    real_stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # the agent's log() prints
            run_all()
    finally:
        shutil.rmtree(support.HOME, ignore_errors=True)
    print("check: all checks passed", file=real_stdout)
