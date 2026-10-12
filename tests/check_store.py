"""store: day files, pruning, briefs."""
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

