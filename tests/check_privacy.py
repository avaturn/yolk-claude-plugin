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
