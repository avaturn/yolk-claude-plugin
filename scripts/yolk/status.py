"""What is happening now: the `status` action, and `--wait` for the session to be told when it is done.

`running` is a non-blocking flock on `.lock` that is released at once. It can never keep a poll from
starting; at worst a poll that starts in that instant logs "already in progress" and retries in 20
minutes.
"""
import fcntl
import time

from . import api, config, poll, store


def lock_held():
    """True while a poll holds .lock."""
    with open(config.YOLK_HOME / ".lock", "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def _dates(directory, suffix):
    """The dates of `<date><suffix>` files in `directory`, newest first."""
    names = (f.name[:-len(suffix)] for f in directory.glob("*" + suffix))
    return sorted((n for n in names if config.DATE_RE.fullmatch(n)), reverse=True)


def report(cfg):
    st = store.read_status()
    try:
        open_dates = sorted({str(q.get("date")) for q in api.pending(cfg)})
    except api.NETWORK_ERRORS:
        open_dates = st.get("open_check_ins", [])  # offline: the last fetch
    answered = {d: t for d, t in (st.get("answered") or {}).items() if time.time() - t < config.ANSWERED_KEEP_S}
    return {"running": lock_held(), "phase": st.get("phase", "idle"), "date": st.get("date"),
            "since": st.get("since"), "last_poll_ended": st.get("last_poll_ended"),
            "last_error": st.get("last_error"), "full_disk_access": st.get("full_disk_access"),
            "reports_ready": _dates(config.DAYS_DIR, ".summary.json"),
            "briefs": _dates(config.BRIEFS_DIR, ".json"), "newest_brief": store.latest_brief(),
            "open_check_ins": open_dates, "answered_recently": sorted(answered)}


def wait(cfg, seconds):
    """Wait until no poll is running, sync briefs, then, while a check-in is open or was just
    answered, keep syncing every WAIT_SYNC_S until its brief arrives or `seconds` pass."""
    deadline = time.monotonic() + seconds

    def sync():
        try:
            poll.sync_briefs(cfg)
        except api.NETWORK_ERRORS:
            pass
    while lock_held() and time.monotonic() < deadline:
        time.sleep(min(config.WAIT_POLL_S, max(0, deadline - time.monotonic())))
    sync()
    while time.monotonic() < deadline:
        st = report(cfg)
        awaiting = [d for d in set(st["open_check_ins"]) | set(st["answered_recently"]) if d not in st["briefs"]]
        if not awaiting:
            break
        time.sleep(min(config.WAIT_SYNC_S, max(0, deadline - time.monotonic())))
        sync()
    return report(cfg)
