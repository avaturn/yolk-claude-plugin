"""The Device API gateway: the only HTTP code on the device (Yolk Device API v1, bearer token).

`request` is the one call; the other functions name each endpoint and parse its answer. A 401 on an
authenticated call raises `Unlinked`: the token was revoked.
"""
import json
import urllib.error
import urllib.request

# what "the server cannot be reached" looks like to callers
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)


class Unlinked(Exception):
    """The server answered 401: this device's token is no longer valid."""


def request(cfg, method, path, body=None, auth=True):
    """One call. Returns (status, parsed JSON or None); raises Unlinked on 401."""
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {cfg['device_token']}"
    req = urllib.request.Request(cfg["server"] + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        if e.code == 401 and auth:
            raise Unlinked() from None
        return e.code, None


def claim(server, invite_code, device):
    """Exchange an invite for a device token: (status, body); no token yet, so no auth."""
    return request({"server": server}, "POST", "/v1/devices/claim",
                   {"invite_code": invite_code, "device": device}, auth=False)


def pending(cfg):
    """The question sets still waiting for an answer."""
    _, body = request(cfg, "GET", "/v1/questions/pending")
    return (body or {}).get("question_sets", [])


def context_pending(cfg):
    """Manager only: the dates whose context the server still wants. Any non-200 (an older server,
    an employee token) means nothing to collect."""
    status, body = request(cfg, "GET", "/v1/context/pending")
    return (body or {}).get("dates", []) if status == 200 else []


def post_context(cfg, date, body):
    """Manager only: post one date's context; returns the HTTP status."""
    status, _ = request(cfg, "POST", f"/v1/context/{date}", body)
    return status


def send_answers(cfg, set_id, body):
    """Post one set's answers; returns the HTTP status."""
    status, _ = request(cfg, "POST", f"/v1/questions/{set_id}/answers", body)
    return status


def briefs(cfg, since=None):
    _, body = request(cfg, "GET", "/v1/briefs" + (f"?since={since}" if since else ""))
    return (body or {}).get("briefs", [])


def heartbeat(cfg, payload):
    request(cfg, "POST", "/v1/devices/heartbeat", payload)


def revoke(cfg):
    request(cfg, "DELETE", "/v1/devices/me")
