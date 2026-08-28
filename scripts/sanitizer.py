#!/usr/bin/env python3
"""Deterministic privacy gate on employee answers before they reach the Manager AI.

Belt-and-suspenders on top of the employee agent's prompt rules: strips code
blocks, secret-shaped tokens, and oversized verbatim quotes.
"""
import json
import re
import sys

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


if __name__ == "__main__":
    data = json.load(sys.stdin)
    clean, violations = sanitize_answers(data)
    json.dump({"sanitized": clean, "violations": violations}, sys.stdout, indent=1)
