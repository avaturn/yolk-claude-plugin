#!/usr/bin/env python3
"""Collect the day's git activity (commits + diffstats) across local repos.

Repos are discovered from the Claude Code projects list (dirs the developer
actually worked in) plus shallow scans of common roots.

Usage: git_activity.py [--date YYYY-MM-DD]
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCAN_ROOTS = [Path.home() / d for d in ("Documents", "Downloads", "Projects", "dev", "code")]
PROJECTS_DIR = Path.home() / ".claude" / "projects"


def run(cmd, cwd=None):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def discover_repos():
    repos = set()
    # dirs Claude Code was used in (encoded as -Users-name-path)
    for d in PROJECTS_DIR.iterdir() if PROJECTS_DIR.exists() else []:
        p = Path("/" + d.name.lstrip("-").replace("-", "/"))
        # path encoding is lossy (dashes); check the literal decode and the raw guess
        for cand in (p, Path(d.name.replace("-Users-", "/Users/").replace("-", "/", 1))):
            if (cand / ".git").exists():
                repos.add(cand)
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for git_dir in root.glob("*/.git"):
            repos.add(git_dir.parent)
        for git_dir in root.glob("*/*/.git"):
            repos.add(git_dir.parent)
    home = Path.home()
    for git_dir in home.glob("*/.git"):
        repos.add(git_dir.parent)
    return sorted(repos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    since, until = f"{args.date} 00:00", f"{args.date} 23:59"

    out = []
    for repo in discover_repos():
        log = run(["git", "log", "--all", f"--since={since}", f"--until={until}",
                   "--pretty=%h|%an|%ad|%s", "--date=format:%H:%M"], cwd=repo)
        dirty = run(["git", "status", "--porcelain"], cwd=repo)
        commits = []
        for line in log.splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue
            h, author, time_, subj = parts
            stat = run(["git", "show", "--shortstat", "--format=", h], cwd=repo)
            commits.append({"time": time_, "author": author,
                            "subject": subj[:100], "stat": stat.strip()[:120]})
        # count only dirty files actually touched on the target day
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
        touched_today = 0
        for line in dirty.splitlines():
            rel = line[3:].strip().strip('"').split(" -> ")[-1]
            f = repo / rel
            try:
                if datetime.fromtimestamp(f.stat().st_mtime).date() == day:
                    touched_today += 1
            except OSError:
                continue
        if commits or touched_today:
            out.append({
                "repo": str(repo).replace(str(Path.home()), "~"),
                "commits_today": commits,
                "uncommitted_files_modified_today": touched_today,
            })

    json.dump({"date": args.date, "repos_with_activity": out}, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
