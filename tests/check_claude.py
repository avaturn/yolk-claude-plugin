"""claude: command lines of the two modes."""
import json
import os
import tempfile
from pathlib import Path

from support import REAL_RUN_CLAUDE


def check_claude_flags():
    bindir = Path(tempfile.mkdtemp(prefix="yolk-bin-"))
    out = bindir / "argv.json"
    fake = bindir / "claude"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys\nopen(%r,'w').write(json.dumps(sys.argv[1:]))\n"
                    "print(json.dumps({'type':'result','result':'{}'}))\n" % str(out))
    fake.chmod(0o755)
    old_path, os.environ["PATH"] = os.environ["PATH"], f"{bindir}:{os.environ['PATH']}"
    try:
        REAL_RUN_CLAUDE("PROMPT", "11111111-1111-1111-1111-111111111111", False, "Investigation")
        full = json.loads(out.read_text())
        REAL_RUN_CLAUDE("PROMPT", "11111111-1111-1111-1111-111111111111", False, "Summarization", toolless=True)
        bare = json.loads(out.read_text())
    finally:
        os.environ["PATH"] = old_path
    assert "--no-session-persistence" in full and "--no-session-persistence" in bare
    assert "--dangerously-skip-permissions" in full and "PROMPT" in full and "--tools" not in full
    assert "--dangerously-skip-permissions" not in bare and "PROMPT" not in bare            # stdin, not argv
    assert bare[bare.index("--tools") + 1] == "" and "--strict-mcp-config" in bare
    for argv in (full, bare):  # both runs: Opus 5.5, high effort
        assert argv[argv.index("--model") + 1] == "claude-opus-5-5" and argv[argv.index("--effort") + 1] == "high"
