#!/usr/bin/env python3
"""Collect per-day macOS app usage from the Screen Time database (knowledgeC.db).

Core Data timestamps are seconds since 2001-01-01 (offset 978307200).
Copies the DB (+WAL/SHM) to a temp dir before reading — sqlite can't always
read a live WAL DB owned by another process.

Usage: screen_time.py [--date YYYY-MM-DD]
"""
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from _common import emit_error

CORE_DATA_EPOCH = 978307200
DB = Path.home() / "Library/Application Support/Knowledge/knowledgeC.db"

FRIENDLY = {
    "com.google.Chrome": "Chrome",
    "com.anthropic.claudefordesktop": "Claude Desktop",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm",
    "com.microsoft.VSCode": "VS Code",
    "com.todesktop.230313mzl4w4u92": "Cursor",
    "com.apple.finder": "Finder",
    "com.apple.Preview": "Preview",
    "com.tdesktop.Telegram": "Telegram",
}
DEV_APPS = {"Terminal", "iTerm", "VS Code", "Cursor", "Claude Desktop"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    day = datetime.strptime(args.date, "%Y-%m-%d").astimezone()
    start = day.replace(hour=0, minute=0, second=0).timestamp() - CORE_DATA_EPOCH
    end = start + 86400

    try:
        if not DB.exists():
            emit_error(args.date, "no_database")
        with tempfile.TemporaryDirectory() as tmp:
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(DB) + suffix)
                if src.exists():
                    shutil.copy2(src, Path(tmp) / src.name)
            con = sqlite3.connect(Path(tmp) / DB.name)
            rows = con.execute(
                """SELECT ZVALUESTRING,
                          ROUND(SUM(ZENDDATE-ZSTARTDATE)/60.0, 1) AS mins,
                          MIN(ZSTARTDATE), MAX(ZENDDATE)
                   FROM ZOBJECT
                   WHERE ZSTREAMNAME='/app/usage' AND ZSTARTDATE >= ? AND ZSTARTDATE < ?
                   GROUP BY ZVALUESTRING ORDER BY mins DESC""",
                (start, end),
            ).fetchall()
            con.close()
    except PermissionError:
        emit_error(args.date, "no_permission")
    except (OSError, sqlite3.Error):
        emit_error(args.date, "unreadable")

    apps = []
    for bundle, mins, first, last in rows:
        if not bundle or mins < 0.5:
            continue
        name = FRIENDLY.get(bundle, bundle)
        apps.append({
            "app": name,
            "minutes": mins,
            "first_used": datetime.fromtimestamp(first + CORE_DATA_EPOCH).strftime("%H:%M"),
            "last_used": datetime.fromtimestamp(last + CORE_DATA_EPOCH).strftime("%H:%M"),
        })

    dev_minutes = round(sum(a["minutes"] for a in apps if a["app"] in DEV_APPS), 1)
    total = round(sum(a["minutes"] for a in apps), 1)
    json.dump({
        "date": args.date,
        "total_screen_minutes": total,
        "dev_tool_minutes": dev_minutes,
        "apps": apps[:20],
    }, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
