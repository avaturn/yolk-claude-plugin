#!/usr/bin/env python3
"""Self-check for _common.decode_project_dir. Run: python3 scripts/collectors/check_paths.py"""
import tempfile
from pathlib import Path

from _common import decode_project_dir

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    dirs = ["Users/x/Development/yolk/yolk-agentic/yolk-plugin-agent",
            "Users/x/Development/yolk/yolk",           # shorter sibling prefix must not win
            "Users/x/Library/Application Support/Claude",
            "Users/x/.config/my.app",
            "Users/x/proj/yolk-agentic",               # longest match dead-ends -> backtrack
            "Users/x/proj/yolk/agentic-x"]
    for d in dirs:
        (root / d).mkdir(parents=True)

    def decode(name):
        return decode_project_dir(name, root)

    assert decode("-Users-x-Development-yolk-yolk-agentic-yolk-plugin-agent") == root / dirs[0]
    assert decode("-Users-x-Development-yolk-yolk-agentic") == root / "Users/x/Development/yolk/yolk-agentic"
    assert decode("-Users-x-Library-Application-Support-Claude") == root / dirs[2]
    assert decode("-Users-x--config-my-app") == root / dirs[3]
    assert decode("-Users-x-proj-yolk-agentic-x") == root / dirs[5]
    # unresolvable: lossy fallback, no crash
    assert decode("-no-such-dir") == Path("/no/such/dir")
    assert decode("") == root

print("ok")
