"""privacy: the gate and the shapes."""
import json

from yolk import privacy


def check_contract():
    block = {"start": "09:00", "end": "10:00", "kind": "work", "what": "w", "where": "x", "how": "y",
             "confidence": "high", "secret": "z"}
    out = privacy.normalize_summary({"overview": "o", "evil": 1, "active_minutes": 5, "personal_minutes": 1,
                                 "blocks": [block],
                                 "work_items": [{"title": "t", "outcome": "o", "evidence": "e", "extra": 1}]})
    assert set(out) == {"overview", "active_minutes", "personal_minutes", "blocks", "work_items"}, out
    assert set(out["blocks"][0]) == {"start", "end", "kind", "what", "where", "how", "confidence"}
    assert set(out["work_items"][0]) == {"title", "outcome", "evidence"}

    # limits
    big = "a" * 2000
    out = privacy.normalize_summary({
        "overview": big,
        "blocks": [{**block, "what": big, "where": big, "how": big, "start": f"{i // 4:02d}:{i % 4 * 15:02d}",
                    "end": f"{i // 4:02d}:{i % 4 * 15 + 14:02d}"} for i in range(60)],
        "work_items": [{"title": big, "outcome": big, "evidence": big}] * 30})
    assert len(out["overview"]) == 1500
    assert len(out["blocks"]) == 48 and len(out["work_items"]) == 20
    assert (len(out["blocks"][0]["what"]), len(out["blocks"][0]["where"]), len(out["blocks"][0]["how"])) == (300, 200, 300)
    assert [len(out["work_items"][0][k]) for k in ("title", "outcome", "evidence")] == [200, 500, 300]

    # enums, times, minutes
    out = privacy.normalize_summary({"blocks": [
        {**block, "kind": " Meeting ", "start": "9:40", "confidence": "certain"},
        {**block, "kind": "lunch"},                      # unknown kind: dropped
        {**block, "start": "25:00"},                     # not a time: dropped
        {**block, "start": "10:00", "end": "09:00"},     # ends before it starts: dropped
        {**block, "what": ""},                           # a work block that says nothing: dropped
        "junk", None],
        "active_minutes": "410", "personal_minutes": -5})
    assert [(b["kind"], b["start"], b["confidence"]) for b in out["blocks"]] == [("meeting", "09:40", "low")], out
    assert (out["active_minutes"], out["personal_minutes"]) == (410, 0)
    for v, want in ((12.6, 13), ("abc", 0), (99999, 1440), (float("nan"), 0), (True, 0), (None, 0)):
        assert privacy.normalize_summary({"active_minutes": v})["active_minutes"] == want, v

    # personal (and away) detail is stripped
    out = privacy.normalize_summary({"blocks": [
        {**block, "kind": "personal", "what": "watching X on YouTube", "where": "youtube.com", "how": "scrolling"},
        {**block, "kind": "away", "start": "11:00", "end": "12:00", "what": "doctor appointment", "where": "clinic"}]})
    for b in out["blocks"]:
        assert (b["what"], b["where"], b["how"]) == (b["kind"], "", ""), b
    assert "YouTube" not in json.dumps(out) and "doctor" not in json.dumps(out)

    # wrapped output and garbage in
    assert privacy.normalize_summary({"summary": {"overview": "wrapped"}})["overview"] == "wrapped"
    assert privacy.normalize_summary(None) == {"overview": "", "active_minutes": 0, "personal_minutes": 0,
                                           "blocks": [], "work_items": []}

    # the privacy gate runs on the full text, before the limits: a cut secret must not slip past it
    fence = "```\na\nb\nc\nd\n```"
    out, violations = privacy.gate_and_normalize({"overview": "k " + "sk-ant-abcdefghijkl", "blocks": [
        {**block, "what": fence * 20, "where": "w" * 190 + " sk-ant-abcdefghijkl"}]})
    assert "code_block" in violations and "secret_pattern" in violations, violations
    assert len(out["blocks"][0]["what"]) <= 300 and len(out["blocks"][0]["where"]) <= 200
    assert "sk-ant" not in json.dumps(out)


def check_gate_rules():
    secrets = ["sk-ant-abcdefghijkl", "ghp_" + "a1" * 8, "AKIA" + "ABCDEFGHIJKLMNOP", "xoxb-1234567890-abc",
               "-----BEGIN RSA PRIVATE KEY-----\nMIIBOg\n-----END RSA PRIVATE KEY-----",
               "eyJ" + "a" * 20 + "." + "b" * 10 + "." + "c" * 10, "password: hunter2"]
    assert len(secrets) == len(privacy.SECRET_RES)               # one sample per pattern, in order
    for rx, sample in zip(privacy.SECRET_RES, secrets):
        assert rx.search(sample), sample
        out, v = privacy.sanitize_text(f"see {sample} here")
        assert v == ["secret_pattern"] and "[secret redacted]" in out and sample not in out, (sample, out, v)
    assert privacy.sanitize_text("plain business text") == ("plain business text", [])

    out, v = privacy.sanitize_text("a ```\n1\n2\n3\n4\n``` b")               # fenced: more than 3 lines
    assert v == ["code_block"] and out == "a [code block redacted by privacy gate] b", (out, v)
    short = "a ```\n1\n2\n```"
    assert privacy.sanitize_text(short) == (short, [])                       # a short fence stays
    out, v = privacy.sanitize_text("intro\n" + "import os\n" * 4 + "outro")    # unfenced: 4+ code-looking lines
    assert v == ["unfenced_code_dump"] and "import os" not in out and "outro" in out, (out, v)
    assert privacy.sanitize_text("import os\n" * 3)[1] == []
    out, v = privacy.sanitize_text("a" * 6001)
    assert v == ["oversized_answer_truncated"] and out == "a" * 6000 + " …[truncated by privacy gate]"
    assert privacy.sanitize_text("a" * 6000)[1] == []

    clean, v = privacy.sanitize_answers({"answers": [{"answer": "k sk-ant-abcdefghijkl"}], "n": 3})   # walks nested values
    assert clean == {"answers": [{"answer": "k [secret redacted]"}], "n": 3} and v == ["secret_pattern"]


def check_answers_payload():
    out = privacy.answers_payload({"answers": [
        {"question_id": "q1", "answer": "a", "evidence": "e", "evidence_basis": "old", "confidence": "HIGH", "extra": "x"},
        {"question_id": "q2", "answer": "b", "evidence_basis": "old name", "confidence": "certain"},
        {"question_id": "q3"},
        {"answer": "no id"}, "junk"],
        "volunteered_context": "ctx", "evil": 1})
    assert out == {"answers": [
        {"question_id": "q1", "answer": "a", "evidence": "e", "confidence": "high"},     # one evidence key, no extras
        {"question_id": "q2", "answer": "b", "evidence": "old name", "confidence": None},  # evidence_basis renamed
        {"question_id": "q3", "answer": "", "confidence": None}],
        "volunteered_context": "ctx"}, out
    assert privacy.answers_payload({}) == {"answers": [], "volunteered_context": None}
    assert [privacy.answers_payload({"answers": [{"question_id": "q", "confidence": c}]})["answers"][0]["confidence"]
            for c in ("high", "Medium", "LOW", "")] == ["high", "medium", "low", None]
