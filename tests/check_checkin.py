"""checkin: the answers outbox."""
import json
import os
from datetime import date

from support import CFG, clean_dirs, agent_log
from yolk import activity, api, checkin, config, poll, store


def check_heartbeat_and_answers_flush():
    clean_dirs()
    posts = []
    real, api.request = api.request, lambda cfg, m, path, body=None, auth=True: (posts.append(path), (201, None))[1]
    try:
        (config.OUTBOX_DIR / "qs_1.json").write_text("{}")
        checkin.flush_outbox(CFG)
    finally:
        api.request = real
    assert posts == ["/v1/questions/qs_1/answers"] and (config.AUDIT_DIR / "qs_1.sent.json").exists()
    os.utime(config.AUDIT_DIR / "qs_1.sent.json", (1_700_000_000, 1_700_000_000))
    sent = []
    api.request = lambda cfg, m, path, body=None, auth=True: (sent.append(body), (204, None))[1]
    try:
        poll.heartbeat(CFG)
    finally:
        api.request = real
    assert sent[0]["last_answered_at"].startswith("2023-11-1"), sent[0]
    assert not any(hasattr(m, "flush_summaries") or hasattr(m, "SUMMARY_OUTBOX_DIR")
                   for m in (activity, api, checkin, config, poll, store))

def check_atomic_and_corrupt_outbox():
    clean_dirs()
    f = config.AUDIT_DIR / "x.json"
    store.write_private(f, "old")
    store.write_private(f, "new")
    assert f.read_text() == "new" and f.stat().st_mode & 0o777 == 0o600
    try:
        store.write_private(f, 12345)  # fails halfway: the old content and no temp file must survive
    except TypeError:
        pass
    assert f.read_text() == "new"
    assert [p.name for p in config.AUDIT_DIR.iterdir() if p.name != "agent.log"] == ["x.json"], list(config.AUDIT_DIR.iterdir())
    f.unlink()

    # unreadable outbox files are set aside; the poll goes on and the good ones are sent
    posts = []
    real, api.request = api.request, lambda cfg, m, path, body=None, auth=True: (posts.append(path), (201, None))[1]
    try:
        (config.OUTBOX_DIR / "qs_bad.json").write_text("{not json")
        (config.OUTBOX_DIR / "qs_ok.json").write_text("{}")
        (config.OUTBOX_DIR / "qs_utf8.json").write_bytes(b"\xff\xfe\x00")  # not UTF-8
        (config.OUTBOX_DIR / "qs_list.json").write_text("[1]")               # not an object
        checkin.flush_outbox(CFG)
    finally:
        api.request = real
    assert posts == ["/v1/questions/qs_ok/answers"], posts
    for name in ("qs_bad", "qs_utf8", "qs_list"):
        assert (config.AUDIT_DIR / f"{name}.rejected.json").exists(), name
    assert (config.AUDIT_DIR / "qs_ok.sent.json").exists() and not list(config.OUTBOX_DIR.glob("*.json"))
    assert "unreadable" in agent_log()

    # an outbox body written by the poller is atomic and private too
    store.write_private(config.OUTBOX_DIR / "qs_1.json", "{}")
    assert (config.OUTBOX_DIR / "qs_1.json").stat().st_mode & 0o777 == 0o600


def check_send_path_redacts():
    """A fake investigator answer with a secret and a code fence: the outbox file and the POST body are redacted."""
    clean_dirs()
    dirty = "key sk-ant-abcdefghijkl and ```\n1\n2\n3\n4\n``` done"
    posts = []
    real_api, real_inv = api.request, checkin.investigate
    api.request = lambda cfg, m, path, body=None, auth=True: (posts.append((path, body)), (201, None))[1]
    checkin.investigate = lambda *a: {"answers": [{"question_id": "q1", "answer": dirty, "evidence": dirty, "confidence": "high",
                                                   "extra": "x"}]}
    try:
        ok, _, _ = checkin.answer(CFG, {"id": "qs_priv", "date": "2020-01-01", "round": 1, "questions": []},
                                  "role", date.today(), None, False)
    finally:
        api.request, checkin.investigate = real_api, real_inv
    assert ok and [p for p, _ in posts] == ["/v1/questions/qs_priv/answers"], posts
    body = posts[0][1]
    assert body["privacy_redactions"] == ["code_block", "secret_pattern"], body["privacy_redactions"]
    item = body["answers"]["answers"][0]
    assert item["answer"] == "key [secret redacted] and [code block redacted by privacy gate] done" and item["evidence"] == item["answer"]
    assert "extra" not in item
    sent = (config.AUDIT_DIR / "qs_priv.sent.json").read_text()
    assert "sk-ant" not in sent and "1\\n2" not in sent and json.loads(sent) == body
