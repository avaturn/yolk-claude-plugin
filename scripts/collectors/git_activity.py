#!/usr/bin/env python3
"""Collect the day's git activity (commits + diffstats) across local repos.

Repos are discovered from the Claude Code projects list (dirs the developer
actually worked in) plus bounded scans of common roots. Only commits authored
with the user's own emails (global + per-repo user.email) are counted.

Usage: git_activity.py [--date YYYY-MM-DD]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from _common import decode_project_dir, emit_error, tilde

SCAN_NAMES = {"Documents", "Downloads", "Projects", "dev", "code", "Development",
              "src", "repos", "work", "workspace", "Code"}
SCAN_DEPTH = 3  # reaches ~/Development/yolk/yolk-agentic/yolk-plugin-agent
SKIP_DIRS = {"node_modules", "venv"}
PROJECTS_DIR = Path.home() / ".claude" / "projects"


def run(cmd, cwd=None):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def find_repos(d, depth):
    if os.path.exists(d / ".git"):
        yield d
    elif depth:
        try:
            with os.scandir(d) as it:
                kids = sorted(e.path for e in it if e.name[0] != "." and e.name not in SKIP_DIRS
                              and e.is_dir(follow_symlinks=False))
        except OSError:
            return
        for k in kids:
            yield from find_repos(Path(k), depth - 1)


def discover_repos():
    repos = set()
    # dirs Claude Code was used in (encoded as -Users-name-path)
    for d in PROJECTS_DIR.iterdir() if PROJECTS_DIR.exists() else []:
        p = decode_project_dir(d.name)
        if os.path.exists(p / ".git"):
            repos.add(p)
    home = Path.home()
    for root in (p for p in home.iterdir() if p.name in SCAN_NAMES):
        repos.update(find_repos(root, SCAN_DEPTH))
    repos.update(g.parent for g in home.glob("*/.git"))
    return sorted(repos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    since, until = f"{args.date} 00:00", f"{args.date} 23:59"
    if not run(["git", "--version"]):
        emit_error(args.date, "no_git")
    me = run(["git", "config", "--global", "user.email"]).lower()

    out = []
    seen = set()  # worktrees share refs: count each commit once
    for repo in discover_repos():
        emails = {e for e in (me, run(["git", "config", "user.email"], cwd=repo).lower()) if e}
        log = run(["git", "log", "--all", f"--since={since}", f"--until={until}",
                   "--pretty=%H|%ae|%ad|%an|%s", "--date=format:%H:%M"], cwd=repo)
        dirty = run(["git", "status", "--porcelain"], cwd=repo)
        commits = []
        for line in log.splitlines():
            parts = line.split("|", 4)
            if len(parts) != 5:
                continue
            h, email, time_, author, subj = parts
            if email.lower() not in emails or h in seen:
                continue
            seen.add(h)
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
                "repo": tilde(repo),
                "commits_today": commits,
                "uncommitted_files_modified_today": touched_today,
            })

    json.dump({"date": args.date, "repos_with_activity": out}, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
