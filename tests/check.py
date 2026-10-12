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
import check_manager, check_poll, check_privacy, check_profile, check_schedule, check_status, check_store  # noqa: E402,E401


def run_all():
    check_privacy.check_contract()
    check_privacy.check_gate_rules()
    check_privacy.check_answers_payload()
    check_activity.check_missing_days()
    check_cli.check_claim()
    check_store.check_latest_brief()
    check_poll.check_sync_briefs_and_heartbeat_glob()
    scripts_dir = check_activity.check_collectors()
    check_checkin.check_heartbeat_and_answers_flush()
    check_activity.check_summarize_and_investigate(scripts_dir)
    check_checkin.check_atomic_and_corrupt_outbox()
    check_checkin.check_send_path_redacts()
    check_checkin.check_failed_investigation_retry_pace()
    check_activity.check_attempt_cap(scripts_dir)
    check_poll.check_poll_order(scripts_dir)
    check_claude.check_claude_flags()
    check_manager.check_claim_role()
    check_manager.check_context_step()
    check_manager.check_gate_and_clip()
    check_manager.check_rejected_and_attempts()
    check_manager.check_poll_roles()
    check_manager.check_manager_consent()
    check_manager.check_context_action()
    check_manager.check_member_report()
    check_manager.check_manual_context()
    check_manager.check_readonly_flags()
    check_manager.check_readonly_gate()
    check_profile.check_send_profile()
    check_profile.check_profile_sources()
    check_config.check_day_boundary()
    check_store.check_prune()
    check_schedule.check_heal_schedule()
    check_schedule.check_heal_installs_missing_plist()
    check_schedule.check_install_script_plist()
    check_launcher.check_launcher()
    check_poll.check_status_phases(scripts_dir)
    check_poll.check_full_disk_access_probe()
    check_status.check_status_and_wait()
    check_schedule.check_heal_action_and_hook()


if __name__ == "__main__":
    real_stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # the agent's log() prints
            run_all()
    finally:
        shutil.rmtree(support.HOME, ignore_errors=True)
    print("check: all checks passed", file=real_stdout)
