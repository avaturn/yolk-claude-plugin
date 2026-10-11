"""store: day files, pruning, briefs, no reports/."""
import shutil
from datetime import timedelta

from support import CFG, clean_dirs
from yolk import config, store


def check_latest_brief():
    clean_dirs()
    assert store.latest_brief() is None
    for name in ("2026-10-05.json", "2026-10-07.json", "notes.json", "latest.json", "2026-10-09.summary.json"):
        (config.BRIEFS_DIR / name).write_text("{}")
    assert store.latest_brief() == "2026-10-07"

def check_prune():
    clean_dirs()
    today = config.summary_today(CFG)
    old, edge, new = ((today - timedelta(days=n)).isoformat() for n in (15, 14, 1))
    names = [f"{old}.json", f"{old}.summary.json", f"{old}.attempts", f"{old}.cooldown", f"{edge}.json", f"{new}.json",
             "notes.txt", "short"]
    for n in names:
        (config.DAYS_DIR / n).write_text("x")
    store.prune_days(CFG)
    assert sorted(p.name for p in config.DAYS_DIR.iterdir()) == sorted([f"{edge}.json", f"{new}.json", "notes.txt", "short"])

def check_no_reports_dir():
    shutil.rmtree(config.REPORTS_DIR, ignore_errors=True)
    store.ensure_dirs()
    assert not config.REPORTS_DIR.exists()                       # not created any more
    config.REPORTS_DIR.mkdir()
    store.ensure_dirs()
    assert not config.REPORTS_DIR.exists()                       # an empty one from an older version goes
    config.REPORTS_DIR.mkdir()
    (config.REPORTS_DIR / "2026-10-04.md").write_text("old")
    store.ensure_dirs()
    assert (config.REPORTS_DIR / "2026-10-04.md").read_text() == "old"   # old reports are never deleted
    shutil.rmtree(config.REPORTS_DIR)
