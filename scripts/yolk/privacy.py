"""The privacy gate, and the shape of what the device stores or sends.

Order matters in `gate_and_normalize`: the gate runs on the model's FULL text, then the shape is
enforced. The other way round, cutting a string to its limit can leave the first characters of a
secret that are too short for the gate to recognise, and a redaction marker longer than its text
could push a string over its limit.

Deterministic belt and braces on top of the charters' own rules: strips code blocks, secret-shaped
tokens and oversized verbatim quotes.
"""
import re

ANSWER_ITEM_KEYS = ("question_id", "answer", "evidence", "confidence")
BLOCK_KINDS = ("work", "meeting", "personal", "away")
CONFIDENCES = ("high", "medium", "low")
MAX_BLOCKS, MAX_WORK_ITEMS, OVERVIEW_MAX = 48, 20, 1500
BLOCK_LIMITS = {"what": 300, "where": 200, "how": 300}
ITEM_LIMITS = {"title": 200, "outcome": 500, "evidence": 300}
HHMM_RE = re.compile(r"(\d{1,2}):(\d{2})")

# ---- the gate ----------------------------------------------------------------------------------

SECRET_RES = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
    re.compile(r"\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+", re.I),
]
CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
INLINE_PATH_DUMP_RE = re.compile(r"(?:^|\n)(?:[ \t]*(?:def |class |function |import |const |let |var |#include)[^\n]*\n){4,}")


def sanitize_text(text):
    violations = []
    out = text
    for m in CODE_FENCE_RE.findall(out):
        if m.count("\n") > 3:
            violations.append("code_block")
    out = CODE_FENCE_RE.sub(lambda m: m.group(0) if m.group(0).count("\n") <= 3
                            else "[code block redacted by privacy gate]", out)
    if INLINE_PATH_DUMP_RE.search(out):
        violations.append("unfenced_code_dump")
        out = INLINE_PATH_DUMP_RE.sub("\n[code content redacted by privacy gate]\n", out)
    for rx in SECRET_RES:
        if rx.search(out):
            violations.append("secret_pattern")
            out = rx.sub("[secret redacted]", out)
    if len(out) > 6000:
        violations.append("oversized_answer_truncated")
        out = out[:6000] + " …[truncated by privacy gate]"
    return out, violations


def sanitize_answers(payload):
    """payload: dict parsed from employee answers JSON. Returns (clean, all_violations)."""
    all_v = []

    def walk(node):
        if isinstance(node, str):
            clean, v = sanitize_text(node)
            all_v.extend(v)
            return clean
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(payload), sorted(set(all_v))


# ---- what is sent: answers --------------------------------------------------------------------

def answers_payload(raw):
    """The server refuses unknown keys (422): keep only what Device API v1 accepts."""
    items = []
    for a in raw.get("answers") or []:
        if not isinstance(a, dict) or a.get("question_id") is None:
            continue
        item = {k: str(a[k]) for k in ANSWER_ITEM_KEYS if a.get(k) is not None}
        if "evidence" not in item and a.get("evidence_basis") is not None:
            item["evidence"] = str(a["evidence_basis"])  # the charter's old name; never send both
        item["answer"] = item.get("answer", "")
        confidence = item.get("confidence", "").lower()
        item["confidence"] = confidence if confidence in CONFIDENCES else None
        items.append(item)
    context = raw.get("volunteered_context")
    return {"answers": items, "volunteered_context": str(context) if context else None}


# ---- what is stored locally: the daily activity report ----------------------------------------

def _text(v, limit):
    return "" if v is None or isinstance(v, (dict, list)) else str(v).strip()[:limit]


def _minutes(v):
    if isinstance(v, bool):
        return 0
    try:
        return max(0, min(int(round(float(v))), 1440))
    except (TypeError, ValueError, OverflowError):
        return 0


def _hhmm(v):
    m = HHMM_RE.fullmatch(str(v).strip())
    return f"{int(m[1]):02d}:{m[2]}" if m and int(m[1]) <= 23 and int(m[2]) <= 59 else None


def _block(b):
    if not isinstance(b, dict):
        return None
    kind = str(b.get("kind", "")).strip().lower()
    start, end = _hhmm(b.get("start")), _hhmm(b.get("end"))
    if kind not in BLOCK_KINDS or not start or not end or start >= end:
        return None
    confidence = str(b.get("confidence", "")).strip().lower()
    out = {"start": start, "end": end, "kind": kind,
           "confidence": confidence if confidence in CONFIDENCES else "low"}
    if kind in ("personal", "away"):  # personal time only as a total; "away" must not smuggle it in
        return {**out, "what": kind, "where": "", "how": ""}
    out.update({k: _text(b.get(k), n) for k, n in BLOCK_LIMITS.items()})
    return out if out["what"] else None


def normalize_summary(raw):
    """Keep exactly the summary keys, truncate, drop extra or invalid items, coerce enums."""
    s = raw.get("summary") if isinstance(raw, dict) and isinstance(raw.get("summary"), dict) else raw
    s = s if isinstance(s, dict) else {}
    blocks = s.get("blocks") if isinstance(s.get("blocks"), list) else []
    items = s.get("work_items") if isinstance(s.get("work_items"), list) else []
    work_items = [{k: _text(i.get(k), n) for k, n in ITEM_LIMITS.items()}
                  for i in items if isinstance(i, dict) and _text(i.get("title"), 1)]
    return {"overview": _text(s.get("overview"), OVERVIEW_MAX),
            "active_minutes": _minutes(s.get("active_minutes")),
            "personal_minutes": _minutes(s.get("personal_minutes")),
            "blocks": sorted(filter(None, map(_block, blocks)),
                             key=lambda b: (b["start"], b["end"]))[:MAX_BLOCKS],
            "work_items": work_items[:MAX_WORK_ITEMS]}


def gate_and_normalize(raw):
    """(normalized summary, violations)."""
    clean, violations = sanitize_answers(raw)
    return normalize_summary(clean), violations
