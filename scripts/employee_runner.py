#!/usr/bin/env python3
"""Yolk employee agent: the entry point. Launchd (through bin/launcher.py), the SessionStart hook, the
/yolk:* commands and the server's end-to-end tests all run this file; the code is in scripts/yolk/.

    employee_runner.py [poll|claim|sync|leave|summarize|status|heal] [options]   (see yolk/cli.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yolk import cli  # noqa: E402

if __name__ == "__main__":
    cli.main()
