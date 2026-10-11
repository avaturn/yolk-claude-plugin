#!/usr/bin/env python3
"""PreToolUse hook of the manager's read-only collector run (see yolk/claude.py).

Reads the hook JSON on stdin; exit 0 if `tool_name` is in READONLY_TOOLS, else exit 2 (blocks the
call). Fail closed: any error also exits 2, because any other non-zero exit does NOT block the call.
"""
import json
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from yolk import claude
    name = json.load(sys.stdin)["tool_name"]
    if name in claude.READONLY_TOOLS:
        return 0
    print(f"yolk: {name} is not on the read-only list", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001 — fail closed
        code = 2
    sys.exit(code)
