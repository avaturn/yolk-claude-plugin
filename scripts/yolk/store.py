"""Everything the device writes to disk, except agent.json (config) and the answers' content rules.

Writes are atomic and private (0600): a crash or a full disk never leaves a half-written outbox body
or config. status.json holds only phases, dates, class names and the Full Disk Access flag, never content.
"""
import contextlib
import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config


def log(msg, echo=True):
    line = f"[yolk {datetime.now().strftime('%H:%M:%S')}] {msg}"
    if echo:
        print(line, flush=True)
    config.AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.AUDIT_DIR / "agent.log", "a") as f:
        f.write(line + "\n")


def write_private(path, text):
    """Atomic: temp file in the same dir, then os.replace; 0600 (mkstemp's mode)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ensure_dirs():
    for d in (config.YOLK_HOME, config.BRIEFS_DIR, config.OUTBOX_DIR, config.AUDIT_DIR, config.DAYS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(config.DAYS_DIR, 0o700)  # raw collector output: prompt previews, commands, page titles


# ---- status.json -------------------------------------------------------------------------------

def read_status():
    try:
        d = json.loads(config.STATUS_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def set_status(**fields):
    """Merge `fields` into status.json."""
    write_private(config.STATUS_FILE, json.dumps({**read_status(), **fields}, indent=1) + "\n")


def set_phase(phase, date=None):
    set_status(phase=phase, date=date, since=config.now_iso())


@contextlib.contextmanager
def tracked_run():
    """A poll or a summarize run: status.json says what it is doing, and `idle` when it ends."""
    set_status(phase="collecting", date=None, since=config.now_iso(), last_poll_started=config.now_iso(),
               last_error=None)
    try:
        yield
    except Exception as e:  # noqa: BLE001 — SystemExit is how summarize reports its own error
        set_status(last_error=e.__class__.__name__)
        raise
    finally:
        set_status(phase="idle", date=None, since=config.now_iso(), last_poll_ended=config.now_iso())


# ---- day files ---------------------------------------------------------------------------------

def day_done(d):
    """A day needs no (more) work once it has a local summary or was given up on."""
    return (config.DAYS_DIR / f"{d}.summary.json").exists() or (config.AUDIT_DIR / f"summary-{d}.failed.json").exists()


def cooling(d):
    """True while `d` is cooling down: claude exited non-zero for it less than COOLDOWN_S ago."""
    try:
        return time.time() - (config.DAYS_DIR / f"{d}.cooldown").stat().st_mtime < config.COOLDOWN_S
    except OSError:
        return False


def prune_days(cfg):
    """Delete days/* files whose date is older than RAW_KEEP_DAYS: raw collector output must
    not pile up on disk, and nothing older is summarized. Names without a date stay."""
    cutoff = config.summary_today(cfg) - timedelta(days=config.RAW_KEEP_DAYS)
    gone = 0
    for f in config.DAYS_DIR.glob("*"):
        d = config.parse_date(f.name[:10])
        if d and d < cutoff and f.is_file():
            f.unlink()
            gone += 1
    if gone:
        log(f"Removed {gone} raw day file(s) older than {config.RAW_KEEP_DAYS} days.")


# ---- briefs and the answers outbox -------------------------------------------------------------

def latest_brief():
    return max((p.stem for p in config.BRIEFS_DIR.glob("*.json") if config.DATE_RE.fullmatch(p.stem)), default=None)


def load_outbox(f, rejected_name):
    """The body of an outbox file. An unreadable one is moved to audit/<name>.rejected.json
    and None is returned: one bad file must not abort the poll."""
    try:
        body = json.loads(f.read_text())
        if isinstance(body, dict):
            return body
    except (OSError, ValueError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        pass
    f.replace(config.AUDIT_DIR / f"{rejected_name}.rejected.json")
    log(f"Outbox file {f.name} is unreadable; moved to audit/{rejected_name}.rejected.json.")
    return None
