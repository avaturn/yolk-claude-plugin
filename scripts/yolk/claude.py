"""The only code that starts `claude -p`. Two modes:

  investigator  full local freedom (`--dangerously-skip-permissions`), prompt in argv
  toolless      the summarizer: prompt over stdin, NO tools, so text inside the data it reads (page
                titles, prompts) cannot make it act. `--tools ""` alone leaves the user's MCP
                connectors (Gmail, Slack, ...) loaded; `--strict-mcp-config` with no `--mcp-config`
                drops them (checked: 0 tools in the init event).

Output and stderr go to files or a pipe that is always drained, so nothing can block `claude`.
"""
import json
import os
import re
import signal
import subprocess
import tempfile
import threading

from . import config, store


class ClaudeUnavailable(RuntimeError):
    """`claude` cannot be started (not installed, not on PATH)."""


class ClaudeFailed(RuntimeError):
    """`claude` ran and failed: non-zero exit or no result (logged out, usage limit, network)."""


class ClaudeTimeout(ClaudeFailed):
    """`claude` ran past its timeout."""


class BadReply(RuntimeError):
    """`claude` answered, but not with usable JSON."""


def extract_json(text):
    m = re.search(r"\{[\s\S]*\}", text)
    return json.loads(m.group(0)) if m else None


def _command(prompt, session_id, toolless, max_turns):
    return ["claude", "-p"] + ([] if toolless else [prompt]) \
        + ["--output-format", "stream-json", "--verbose", "--no-session-persistence",
           "--model", config.CLAUDE_MODEL, "--effort", config.CLAUDE_EFFORT] \
        + (["--tools", "", "--strict-mcp-config"] if toolless else ["--dangerously-skip-permissions"]) \
        + ["--session-id", session_id, "--max-turns", str(max_turns)]


def _read_result(proc, verbose):
    """The final result text of a stream-json run (None if there was none); with `verbose`, the
    tool calls are logged as they happen."""
    result_text = None
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant" and verbose:
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    store.log(f"  · {b.get('name')} {json.dumps(b.get('input') or {})[:90]}")
        elif ev.get("type") == "result":
            result_text = ev.get("result", "")
    return result_text


def run_claude(prompt, session_id, verbose, what, toolless=False, max_turns=60, timeout=config.CLAUDE_TIMEOUT_S):
    """One headless run; returns its final result text. `what` names the run in error messages.
    Raises ClaudeUnavailable, ClaudeFailed or ClaudeTimeout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["ANTHROPIC_API_KEY"] = ""  # local subscription auth only
    cmd = _command(prompt, session_id, toolless, max_turns)
    with tempfile.TemporaryFile("w+") as err, \
            tempfile.TemporaryFile("w+", encoding="utf-8") as stdin:
        if toolless:
            stdin.write(prompt)
            stdin.flush()
            stdin.seek(0)
        # own process group: children that inherited stdout must die too, or the read never ends
        try:
            proc = subprocess.Popen(cmd, cwd=config.PLUGIN_ROOT, env=env, text=True, start_new_session=True,
                                    stdin=stdin if toolless else None, stdout=subprocess.PIPE, stderr=err)
        except OSError as e:
            raise ClaudeUnavailable(f"{what}: cannot start claude ({e.__class__.__name__})") from None

        def kill_group():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(timeout, kill_group)  # a hung claude must not keep .lock
        timer.start()
        try:
            result_text = _read_result(proc, verbose)
            proc.wait()
        finally:
            timer.cancel()
            kill_group()  # leftover children of a finished or interrupted run
        if proc.returncode != 0 or result_text is None:
            err.seek(0)
            reason = "timed out" if proc.returncode == -signal.SIGKILL else f"rc={proc.returncode}"
            raise (ClaudeTimeout if reason == "timed out" else ClaudeFailed)(
                f"{what} failed ({reason}): {err.read()[-400:]}")
    return result_text
