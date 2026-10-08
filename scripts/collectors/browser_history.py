#!/usr/bin/env python3
"""Collect per-day browsing activity across all profiles of the Chromium-family browsers.

Purpose: close the biggest coverage gap — for many roles (CEO, PM, sales) the
real work happens in the browser: email, decks, meetings, Linear, Slack, docs.
Output is LOCAL ONLY. The Employee Agent summarizes it at business level
(clients, meetings, docs worked on); raw URLs never leave the machine.

Chrome epoch = microseconds since 1601-01-01 (offset 11644473600 s). All browsers
below share Chrome's History schema.

Usage: browser_history.py [--date YYYY-MM-DD]
"""
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from _common import emit_error

CHROME_EPOCH_OFFSET = 11644473600
APP_SUPPORT = Path.home() / "Library/Application Support"
# ponytail: Safari (History.db, different schema, needs Full Disk Access) is the upgrade path
BROWSERS = {
    "Chrome": "Google/Chrome",
    "Arc": "Arc/User Data",
    "Brave": "BraveSoftware/Brave-Browser",
    "Edge": "Microsoft Edge",
    "Chromium": "Chromium",
    "Vivaldi": "Vivaldi",
}
GAP_MINUTES = 10
MAX_TIMELINE = 220


def day_bounds_epoch(date_str):
    day = datetime.strptime(date_str, "%Y-%m-%d").astimezone()
    start = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return start, start + 86400


def profile_dirs():
    """[(browser name, profile dir)] for every profile that has a History file."""
    found = []
    for browser, rel in BROWSERS.items():
        base = APP_SUPPORT / rel
        if not base.is_dir():
            continue
        found += [(browser, p) for p in sorted(base.iterdir())
                  if (p / "History").exists()
                  and not p.name.startswith(("Guest", "System"))]
    return found


def read_visits(history_path, start, end):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "History"
        shutil.copy2(history_path, db)
        con = sqlite3.connect(db)
        try:
            return con.execute(
                """SELECT v.visit_time/1000000 - ?, u.url, u.title
                   FROM visits v JOIN urls u ON v.url = u.id
                   WHERE v.visit_time/1000000 - ? >= ?
                     AND v.visit_time/1000000 - ? < ?
                   ORDER BY v.visit_time""",
                (CHROME_EPOCH_OFFSET, CHROME_EPOCH_OFFSET, start,
                 CHROME_EPOCH_OFFSET, end),
            ).fetchall()
        finally:
            con.close()


def merge_blocks(epochs, gap_s=GAP_MINUTES * 60):
    if not epochs:
        return []
    epochs = sorted(epochs)
    blocks = [[epochs[0], epochs[0] + 60]]
    for t in epochs[1:]:
        if t - blocks[-1][1] <= gap_s:
            blocks[-1][1] = t + 60
        else:
            blocks.append([t, t + 60])
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    start, end = day_bounds_epoch(args.date)

    profiles = profile_dirs()
    if not profiles:
        emit_error(args.date, "no_browser")
    visits = []
    readable = denied = 0
    for browser, prof in profiles:
        try:
            rows = read_visits(prof / "History", start, end)
        except PermissionError:
            denied += 1
            continue
        except (OSError, sqlite3.Error):
            continue
        readable += 1
        visits += [(epoch, url, title or "", browser) for epoch, url, title in rows]
    if not readable:
        emit_error(args.date, "no_permission" if denied else "unreadable")
    visits.sort()

    domains = {}
    hourly = {}
    per_browser = {}
    timeline = []
    last_title = None
    for epoch, url, title, browser in visits:
        per_browser[browser] = per_browser.get(browser, 0) + 1
        host = (urlparse(url).hostname or "?").removeprefix("www.")
        domains[host] = domains.get(host, 0) + 1
        hour = datetime.fromtimestamp(epoch).strftime("%H")
        hourly[hour] = hourly.get(hour, 0) + 1
        t = title.strip()
        if t and t != last_title:
            timeline.append({
                "time": datetime.fromtimestamp(epoch).strftime("%H:%M"),
                "browser": browser,
                "domain": host,
                "title": t[:90],
            })
            last_title = t

    # thin the timeline evenly if oversized, keeping chronological coverage
    if len(timeline) > MAX_TIMELINE:
        step = len(timeline) / MAX_TIMELINE
        timeline = [timeline[int(i * step)] for i in range(MAX_TIMELINE)]

    blocks = merge_blocks([v[0] for v in visits])
    active_minutes = round(sum(b[1] - b[0] for b in blocks) / 60)
    fmt = lambda e: datetime.fromtimestamp(e).strftime("%H:%M")  # noqa: E731

    json.dump({
        "date": args.date,
        "total_visits": len(visits),
        "visits_per_browser": per_browser,
        "active_browsing_minutes": active_minutes,
        "active_blocks": [f"{fmt(b[0])}–{fmt(b[1])}" for b in blocks],
        "visits_per_hour": dict(sorted(hourly.items())),
        "top_domains": dict(sorted(domains.items(), key=lambda kv: -kv[1])[:25]),
        "title_timeline": timeline,
    }, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
