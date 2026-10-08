"""Helpers shared by the collectors."""
import json
import re
import sys
from pathlib import Path


def emit_error(date, code):
    """Source unreadable: exit 0 with a short code, never raw exception text."""
    json.dump({"date": date, "error": code}, sys.stdout)
    sys.exit(0)


def tilde(path):
    s, home = str(path), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


def _walk(cur, rest):
    if not rest:
        return cur
    try:
        kids = [(re.sub(r"[^A-Za-z0-9]", "-", k.name), k) for k in cur.iterdir()]
    except OSError:
        return None
    for enc, k in sorted(kids, key=lambda t: -len(t[0])):
        if (rest == enc or rest.startswith(enc + "-")) and k.is_dir():
            found = _walk(k, rest[len(enc) + 1:])
            if found:
                return found
    return None


def decode_project_dir(name, root=Path("/")):
    """~/.claude/projects dir name -> real path.

    Claude Code turns every non-alphanumeric char of the path into '-', so dashes are
    ambiguous; walk the filesystem and match children by their encoded name instead.
    Unresolvable names fall back to the lossy dash-to-slash guess.
    """
    found = _walk(root, name[1:] if name.startswith("-") else name)
    return found or Path("/" + name.lstrip("-").replace("-", "/"))
