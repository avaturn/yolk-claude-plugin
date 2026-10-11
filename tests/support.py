"""Shared setup for the checks: a temp YOLK_HOME (set BEFORE yolk is imported, which reads it once),
the config every check uses, and the helpers they share. Not a check itself."""
import contextlib
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="yolk-check-"))
os.environ["YOLK_HOME"] = str(HOME)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from yolk import activity, claude, config, store  # noqa: E402

store.ensure_dirs()
REAL_RUN_CLAUDE = claude.run_claude
CFG = {"server": "http://localhost:1", "device_token": "dvc_x", "employee_id": "e1",
       "timezone": "Asia/Jerusalem"}
TODAY = date(2026, 10, 8)


def clean_dirs():
    for d in (config.OUTBOX_DIR, config.AUDIT_DIR, config.DAYS_DIR, config.BRIEFS_DIR):
        for f in d.glob("*"):
            if f.is_file():
                f.unlink()


@contextlib.contextmanager
def fake_collectors(scripts, **mapping):
    old_dir, old_map = config.COLLECTORS_DIR, dict(activity.COLLECTORS)
    config.COLLECTORS_DIR = scripts
    activity.COLLECTORS.update(claude_sessions="empty.py", screen_time="empty.py", git="empty.py", browser="empty.py")
    activity.COLLECTORS.update(mapping)
    try:
        yield
    finally:
        config.COLLECTORS_DIR = old_dir
        activity.COLLECTORS.clear()
        activity.COLLECTORS.update(old_map)


def agent_log():
    return (config.AUDIT_DIR / "agent.log").read_text() if (config.AUDIT_DIR / "agent.log").exists() else ""


def hold_lock(seconds):
    """A helper process that holds .lock like a running poll; returns once it holds it."""
    import subprocess
    code = ("import fcntl,sys,time\nf=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);"
            "print('held',flush=True);time.sleep(float(sys.argv[2]))")
    p = subprocess.Popen([sys.executable, "-c", code, str(config.YOLK_HOME / ".lock"), str(seconds)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p
