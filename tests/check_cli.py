"""cli: claim."""
import json

from support import clean_dirs
from yolk import api, cli, config


def check_claim():
    clean_dirs()
    body = {"device_token": "dvc_new", "employee": {"id": "e1", "name": "T", "timezone": "Asia/Jerusalem",
                                                     "profile_md": "p"}, "poll_interval_s": 1200}
    real, api.request = api.request, lambda cfg, method, path, body_=None, auth=True: (200, body if "claim" in path else {})
    try:
        cli.claim("K7QP-3FXA")
    finally:
        api.request = real
    cfg = json.loads(config.CONFIG_FILE.read_text())
    assert "linked_on" not in cfg and "poll_interval_s" not in cfg and config.CONFIG_FILE.stat().st_mode & 0o777 == 0o600
    config.CONFIG_FILE.unlink()
