#!/usr/bin/env python3
"""Characterization of the device runner, black box: one full poll and the top-level exits are run
through `scripts/employee_runner.py` against a stub device API and a fake `claude`, and compared with
tests/baseline.json (recorded on 1.6.3, before the 1.7.0 refactor). Dates, uuids and temp paths are
normalised, so the baseline does not change from day to day.

    python3 tests/check_baseline.py            compare with the baseline
    python3 tests/check_baseline.py --update   rewrite the baseline (only when behaviour changes on purpose)
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("YOLK_AGENT_DIR", Path(__file__).resolve().parent.parent)).resolve()  # a checkout of the tree to compare
RUNNER = ROOT / "scripts" / "employee_runner.py"
BASELINE = Path(__file__).resolve().parent / "baseline.json"  # recorded from the 1.6.3 tree (git archive 1e2463b)
# the baseline must not depend on the machine: umask, timezone and day boundary are pinned
os.umask(0o022)
os.environ["TZ"] = "Asia/Jerusalem"
time.tzset()
TODAY = date.today()
YDAY = TODAY - timedelta(days=1)

FAKE_CLAUDE = '''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
toolless = "--tools" in a
prompt = sys.stdin.read() if toolless else a[a.index("-p") + 1]
d = os.environ["FAKE_CLAUDE_DIR"]
n = len(os.listdir(d))
open(os.path.join(d, "%02d.json" % n), "w").write(json.dumps(
    {"toolless": toolless, "flags": [x for x in a if x.startswith("--")], "prompt": prompt}))
if toolless:
    out = {"overview": "Synthetic day.", "active_minutes": 30, "personal_minutes": 0, "unknown": 1,
           "blocks": [{"start": "10:00", "end": "10:40", "kind": "work", "what": "Synthetic", "where": "x",
                       "how": "y", "confidence": "medium"}],
           "work_items": [{"title": "Item", "outcome": "o", "evidence": "e"}]}
else:
    out = {"answers": [{"question_id": "q1", "answer": "Focus one, two, three.", "evidence": "sim", "confidence": "high"}]}
print(json.dumps({"type": "result", "result": json.dumps(out)}))
'''


def dates(text):
    """YYYY-MM-DD -> D-n (days before today); used on values and on dict keys alike."""
    def day(m):
        try:
            d = datetime.strptime(m.group(0), "%Y-%m-%d").date()
        except ValueError:
            return m.group(0)
        return f"D-{(TODAY - d).days}"
    return re.sub(r"\d{4}-\d{2}-\d{2}", day, text)


def shape(v):
    """Key sets of a JSON value: dict keys (dates normalised) recursively, the first list element's shape."""
    if isinstance(v, dict):
        return {dates(k): shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return None


class Stub:
    """A device API: records every request and answers like the server."""

    def __init__(self, status_override=None):
        self.requests, self.override = [], status_override
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body=None):
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                stub.requests.append((method, self.path, shape(body) if body is not None else None,
                                      self.headers.get("Authorization") is not None))
                if stub.override:
                    return self._reply(stub.override, {"detail": "x"})
                if self.path.startswith("/v1/briefs"):
                    return self._reply(200, {"briefs": [{"date": YDAY.isoformat(), "brief": {"version": 1, "summary": "s"}}]})
                if self.path.startswith("/v1/questions/pending"):
                    return self._reply(200, {"question_sets": [{"id": "qs_base", "date": YDAY.isoformat(), "round": 1,
                                                                "questions": [{"question_id": "q1", "question": "What were the 3 main focuses?",
                                                                               "dimension": "focus"}]}]})
                if "/answers" in self.path:
                    return self._reply(201, {"status": "stored"})
                return self._reply(204)

            do_GET = lambda self: self._handle("GET")  # noqa: E731
            do_POST = lambda self: self._handle("POST")  # noqa: E731
            do_DELETE = lambda self: self._handle("DELETE")  # noqa: E731
        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def device(tmp, server_url):
    """A temp HOME with a linked device, a browser history with activity yesterday, and a fake claude."""
    home = tmp / "home"
    yolk = home / ".claude" / "yolk"
    yolk.mkdir(parents=True)
    (yolk / "agent.json").write_text(json.dumps({
        "server": server_url, "device_token": "dvc_base", "employee_id": "e1", "name": "Base",
        "timezone": "Asia/Jerusalem"}))
    (yolk / "profile.md").write_text("Role: tester\n")
    hist = home / "Library/Application Support/Google/Chrome/Default"
    hist.mkdir(parents=True)
    con = sqlite3.connect(hist / "History")
    con.execute("create table urls(id integer primary key, url text, title text)")
    con.execute("create table visits(id integer primary key, url integer, visit_time integer)")
    con.execute("insert into urls values (1, 'https://example.com/a', 'Base page')")
    t0 = datetime.combine(YDAY, datetime.min.time()).replace(hour=10).astimezone().timestamp()
    for i in range(30):
        con.execute("insert into visits(url, visit_time) values (1, ?)", (int((t0 + 60 * i + 11644473600) * 1e6),))
    con.commit()
    con.close()
    (home / ".claude" / "projects").mkdir()
    bindir, calls = tmp / "bin", tmp / "claude-calls"
    bindir.mkdir()
    calls.mkdir()
    (bindir / "claude").write_text(FAKE_CLAUDE)
    (bindir / "claude").chmod(0o755)
    env = {"HOME": str(home), "YOLK_HOME": str(yolk), "FAKE_CLAUDE_DIR": str(calls),
           "PATH": f"{bindir}:{Path(sys.executable).parent}:/usr/bin:/bin", "LANG": "en_US.UTF-8", "TZ": "Asia/Jerusalem"}
    return home, yolk, calls, env


def norm(text, home, yolk):
    """Dates -> D-n, uuids, temp and checkout paths, clock times and durations out."""
    text = text.replace(str(yolk), "<YOLK_HOME>").replace(str(home), "<HOME>").replace(str(ROOT), "<PLUGIN>")
    text = text.replace(str(home.resolve()), "<HOME>").replace(str(ROOT.resolve()), "<PLUGIN>")
    text = dates(text)
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", text)
    text = re.sub(r"\[yolk \d\d:\d\d:\d\d\]", "[yolk T]", text)
    text = re.sub(r"took \d+ s", "took N s", text)
    text = re.sub(r"(machine's\s+timezone is \*\*)[^*]+", r"\1<TZ>", text)  # a regex: the employee timezone may be the same
    text = re.sub(r'"agent_version": "[0-9.]+"', '"agent_version": "<v>"', text)  # a version bump is not a behaviour change
    text = re.sub(r"\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", "T+Z", text)
    return text


def tree(yolk, home):
    out = {}
    for p in sorted(yolk.rglob("*")):
        rel = norm(str(p.relative_to(yolk)), home, yolk)
        entry = {"mode": oct(p.stat().st_mode & 0o777)}
        if p.is_file() and p.suffix == ".json":
            entry["keys"] = shape(json.loads(p.read_text()))
        elif p.is_file():
            entry["kind"] = "text"
        else:
            entry["kind"] = "dir"
        out[rel] = entry
    return out


def poll_scenario():
    with tempfile.TemporaryDirectory(prefix="yolk-base-") as t:
        tmp = Path(t).resolve()
        stub = Stub()
        try:
            home, yolk, calls, env = device(tmp, stub.url)
            p = subprocess.run([sys.executable, str(RUNNER), "poll"], env=env, capture_output=True, text=True, timeout=300, cwd=tmp)
            assert p.returncode == 0, p.stderr
            claude = [json.loads(f.read_text()) for f in sorted(calls.glob("*.json"))]
            prompts = {("summarizer" if c["toolless"] else "investigator"): c["prompt"] for c in claude}
            snap = {
                "exit": p.returncode,
                "requests": [[m, norm(path, home, yolk), keys, auth] for m, path, keys, auth in stub.requests],
                "tree": tree(yolk, home),
                "log": [norm(l, home, yolk) for l in (yolk / "audit" / "agent.log").read_text().splitlines()],
                "claude_flags": [[c["toolless"]] + c["flags"] for c in claude],
                "prompt_hashes": {k: hashlib.sha256(norm(v, home, yolk).encode()).hexdigest() for k, v in sorted(prompts.items())},
            }
        finally:
            stub.close()
    return snap


def exit_scenarios():
    out = {}
    with tempfile.TemporaryDirectory(prefix="yolk-base-") as t:
        tmp = Path(t).resolve()
        stub = Stub(status_override=401)
        try:
            home, yolk, calls, env = device(tmp, stub.url)
            p = subprocess.run([sys.executable, str(RUNNER), "poll"], env=env, capture_output=True, text=True, timeout=120, cwd=tmp)
            out["unlinked"] = {"exit": p.returncode, "stderr": norm(p.stderr.strip(), home, yolk),
                               "agent_json_deleted": not (yolk / "agent.json").exists()}
        finally:
            stub.close()
    with tempfile.TemporaryDirectory(prefix="yolk-base-") as t:
        tmp = Path(t).resolve()
        home, yolk, calls, env = device(tmp, "http://127.0.0.1:1")
        p = subprocess.run([sys.executable, str(RUNNER), "poll"], env=env, capture_output=True, text=True, timeout=120, cwd=tmp)
        out["unreachable"] = {"exit": p.returncode, "stderr_prefix": norm(p.stderr.strip(), home, yolk)[:40],
                              "agent_json_kept": (yolk / "agent.json").exists()}
    return out


def collect():
    return {"poll": poll_scenario(), "exits": exit_scenarios()}


def main():
    got = json.loads(json.dumps(collect()))
    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        print("baseline written:", BASELINE)
        return
    want = json.loads(BASELINE.read_text())
    if got != want:
        import difflib
        a = json.dumps(want, indent=1, sort_keys=True).splitlines()
        b = json.dumps(got, indent=1, sort_keys=True).splitlines()
        print("\n".join(list(difflib.unified_diff(a, b, "baseline", "now", lineterm=""))[:80]))
        raise SystemExit("check_baseline: behaviour differs from the 1.6.3 baseline")
    print("check_baseline: matches the 1.6.3 baseline")


if __name__ == "__main__":
    main()
