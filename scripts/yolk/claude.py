"""The only code that starts `claude -p`. Three modes:

  investigator  full local freedom (`--dangerously-skip-permissions`), prompt in argv
  toolless      the summarizer: prompt over stdin, NO tools, so text inside the data it reads (page
                titles, prompts) cannot make it act. `--tools ""` alone leaves the user's MCP
                connectors (Gmail, Slack, ...) loaded; `--strict-mcp-config` with no `--mcp-config`
                drops them (checked: 0 tools in the init event).
  readonly     the manager's context collector: prompt over stdin, NO built-in tools, the manager's own
                MCP connectors kept (no `--strict-mcp-config`), and only the READONLY_TOOLS names
                allowed. `--permission-mode dontAsk` denies what is not allowed, but still honours the user's own
                allow rules and hooks, so a user-level allow rule for a write tool would still apply. Hence a PreToolUse hook
                (scripts/readonly_gate.py, passed with `--settings`) enforces READONLY_TOOLS itself and
                fails closed. (`--setting-sources project` would drop the connectors: 0 MCP servers.) Never
                `--dangerously-skip-permissions`: it reads untrusted Slack/Linear text while the same
                connectors can post messages.

Output and stderr go to files or a pipe that is always drained, so nothing can block `claude`.
"""
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading

from . import config, store


# Every name is a read (list/get/search/read/fetch), never a write. Start: the server's GOALS_TOOLS,
# plus GitHub commits/PR reads and Linear user lookups for per-employee facts.
READONLY_TOOLS = (
    *(f"mcp__claude_ai_Linear__{t}" for t in (
        "list_projects", "get_project", "list_milestones", "get_milestone", "list_cycles", "list_issues",
        "get_issue", "list_documents", "get_document", "get_status_updates", "list_teams",
        "list_users", "get_user", "list_comments")),
    # Slack: public search only. slack_read_channel/thread/canvas can read private channels the
    # manager belongs to, and the charter forbids using those.
    *(f"mcp__claude_ai_Slack__{t}" for t in ("slack_search_public", "slack_search_channels", "slack_search_users")),
    *(f"mcp__claude_ai_Notion__{t}" for t in ("notion-search", "notion-fetch")),
    *(f"mcp__claude_ai_monday_com__{t}" for t in (
        "search", "get_board_info", "get_board_items_page", "read_docs", "workspace_info", "list_workspaces")),
    *(f"mcp__claude_ai_GithubMCP__{t}" for t in (
        "search_issues", "list_issues", "search_pull_requests", "list_pull_requests", "get_file_contents",
        "search_repositories", "list_commits", "search_commits", "get_commit", "pull_request_read",
        "issue_read")),
    *(f"mcp__claude_ai_Fireflies_MCP_Server__{t}" for t in (
        "fireflies_search", "fireflies_get_summary", "fireflies_get_transcripts")),
    *(f"mcp__claude_ai_HubSpot__{t}" for t in ("search_crm_objects", "get_crm_objects")),
    *(f"mcp__claude_ai_Google_Drive__{t}" for t in ("search_files", "read_file_content", "list_recent_files")),
)


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


def _gate_settings():
    """--settings JSON: a PreToolUse hook on every tool that blocks what is not in READONLY_TOOLS."""
    cmd = f"{shlex.quote(sys.executable)} -I {shlex.quote(str(config.SCRIPTS_DIR / 'readonly_gate.py'))}"
    return json.dumps({"disableAllHooks": False, "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": cmd, "timeout": 10}]}]}})


def _command(prompt, session_id, toolless, max_turns, readonly=False):
    if readonly:
        mode = ["--tools", "", "--permission-mode", "dontAsk", "--settings", _gate_settings(),
                "--allowedTools", ",".join(READONLY_TOOLS)]
    else:
        mode = ["--tools", "", "--strict-mcp-config"] if toolless else ["--dangerously-skip-permissions"]
    return ["claude", "-p"] + ([] if toolless or readonly else [prompt]) \
        + ["--output-format", "stream-json", "--verbose", "--no-session-persistence",
           "--model", config.CLAUDE_MODEL, "--effort", config.CLAUDE_EFFORT] + mode \
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


def run_claude(prompt, session_id, verbose, what, toolless=False, max_turns=60, timeout=config.CLAUDE_TIMEOUT_S,
               readonly=False):
    """One headless run; returns its final result text. `what` names the run in error messages.
    Raises ClaudeUnavailable, ClaudeFailed or ClaudeTimeout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["ANTHROPIC_API_KEY"] = ""  # local subscription auth only
    stdin_prompt = toolless or readonly
    cmd = _command(prompt, session_id, toolless, max_turns, readonly)
    with tempfile.TemporaryFile("w+") as err, \
            tempfile.TemporaryFile("w+", encoding="utf-8") as stdin:
        if stdin_prompt:
            stdin.write(prompt)
            stdin.flush()
            stdin.seek(0)
        # own process group: children that inherited stdout must die too, or the read never ends
        try:
            proc = subprocess.Popen(cmd, cwd=config.PLUGIN_ROOT, env=env, text=True, start_new_session=True,
                                    stdin=stdin if stdin_prompt else None, stdout=subprocess.PIPE, stderr=err)
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
