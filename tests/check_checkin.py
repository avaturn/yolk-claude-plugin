"""checkin: the answers outbox."""
import os

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
