# Manual end-to-end check (local)

This check runs the real plugin in your Claude Code against the real `yolk-plugin-server` on your
Mac. Scenario ids (S1, S2, …) refer to `yolk-plugin-server/docs/device-behaviour.md`.

The automated version of the same scenarios uses a fake `claude` and a throwaway database:
`cd yolk-plugin-server && just test`.

**Cost and data.** Steps 6–8 call the Manager AI with your Anthropic API key (billed).
`/yolk:answer` investigates your real local data for the chosen date, on your Claude
subscription, for up to 25 minutes.

## 0. Prerequisites

- Docker, `uv`, `just`, Python 3, and the Claude Code CLI with `claude` on PATH.
- `yolk-plugin-server` and `yolk-plugin-agent` side by side in `yolk-agentic/`.
- `yolk-plugin-server/override.env` (not in git) with the Manager AI key:
  ```
  ANTHROPIC__API_KEY=sk-ant-...
  ```
  Optional: `CLAUDE__OAUTH_TOKEN=...` (the company claude.ai account for goals and the leave
  lookup). Without it, the worker's `claude -p` keeps your `HOME`, so it most likely runs as
  your own claude.ai login and reads company sources through your connectors.
- Port 8080 is taken by yolk-backend's Keycloak on this Mac, so this check runs the API on **8081**.

## 1. Set the shell variables

Run these in every terminal you use for this check:

```bash
SERVER_DIR=~/Development/yolk/yolk-agentic/yolk-plugin-server
Y=http://localhost:8081
J='Content-Type: application/json'
ADMIN="Authorization: Bearer $(cd $SERVER_DIR && just dev-admin-token 2>/dev/null | tail -1)"
R=~/.claude/plugins/cache/yolk/yolk/1.3.0/scripts/employee_runner.py   # the installed plugin's runner
D=$(date -v-1d +%F)                                                    # the date to check: yesterday
```

## 2. Start the API

```bash
cd $SERVER_DIR
uv sync
just services-start      # Postgres on localhost:54329
just db-upgrade          # migrations
PUBLIC_BASE_URL=$Y uv run uvicorn yolk_plugin_server.main:create_app --factory --port 8081
```

Leave it running. In a second terminal, check it: `curl -s $Y/api/health` prints `{"status":"ok"}`.

## 3. Install the plugin

Follow "From a local checkout" in the [README](../README.md#from-a-local-checkout-development).
Then open a new Claude Code session.

## 4. Create the employee

```bash
curl -s -X POST $Y/admin/employees -H "$ADMIN" -H "$J" -d '{
  "id": "greg", "name": "Gregory", "role": "Backend engineer", "timezone": "Asia/Jerusalem",
  "profile_md": "Name: Gregory\nRole: Backend engineer\nWhat real work looks like: reviewed, tested code; small PRs."}'
```

Expect `201` with `invite_url` = `http://localhost:8081/join/<CODE>`. A `409` means `greg`
exists: get a new invite with `curl -s -X POST $Y/admin/employees/greg/invites -H "$ADMIN"`.

## 5. Link the Mac (S1, S2, S3, S14)

1. In Claude Code, run `/yolk:setup <invite_url>`. Accept the consent. Decline the background
   poller for this check.
2. Check the files: `ls -l ~/.claude/yolk/`. `agent.json` must be `-rw-------`, and `profile.md`
   holds the profile from step 4.
3. Check the server side: `curl -s $Y/admin/employees -H "$ADMIN"`. `greg` has device status
   `active` and a `last_seen_at` (the heartbeat sent at setup).
4. S3, the same invite again: `python3 $R claim <invite_url>` prints "already used or expired".
5. S2, an unknown code: `python3 $R claim $Y/join/ZZZZ-9999` prints "isn't valid".
6. Neither failed claim changes `~/.claude/yolk/agent.json`.

## 6. Start the worker and queue the run

1. In a new terminal: `cd $SERVER_DIR && just run-worker`. Leave it running.
2. Queue a run for `$D`:
   ```bash
   curl -s -X POST $Y/admin/runs -H "$ADMIN" -H "$J" -d "{\"date\": \"$D\", \"employee_id\": \"greg\"}"
   ```
   A `409` is fine. After 10:00 Asia/Jerusalem, the worker's daily task may already have
   queued this run.
3. Watch the state:
   ```bash
   curl -s "$Y/admin/runs?date=$D" -H "$ADMIN" | python3 -m json.tool
   ```
   The order is `created` → `awaiting_r1` → (`following_up` → `awaiting_r2`) → `reporting` →
   `published`. If it shows `failed`, read `failure_reason` and `failed_in`.

## 7. Answer (S5, S6, S10)

1. When the state is `awaiting_r1`, run `/yolk:answer` in Claude Code.
2. Check: `ls ~/.claude/yolk/audit/` shows `<set id>.sent.json`, and `~/.claude/yolk/outbox/` is
   empty.
3. If the state moves to `awaiting_r2`, run `/yolk:answer` again (S10, round 2 with the
   round-1 answers as `context`).
4. When nothing is pending, `/yolk:answer` reports no pending questions (S5).

## 8. Brief and report (S13, one report)

1. When the state is `published`, run `/yolk:brief` and `/yolk:report`.
2. Check that the report is exactly the manager's copy:
   ```bash
   curl -s $Y/admin/employees/greg/runs/$D -H "$ADMIN" \
     | python3 -c 'import json,sys; print(json.load(sys.stdin)["report_markdown"])' \
     | diff - ~/.claude/yolk/reports/$D.md && echo "same report"
   ```

## 9. Heartbeat (S14)

Note `last_seen_at` in `curl -s $Y/admin/employees -H "$ADMIN"`, run `/yolk:brief`, then check
that `last_seen_at` is newer.

## 10. Repeated post (S7)

Do this only when nothing is pending, because this poll also investigates pending sets.

```bash
cd ~/.claude/yolk && f=$(ls audit/*.sent.json | head -1) && cp "$f" "outbox/$(basename "$f" .sent.json).json"
python3 $R
```

The log says "server already had them", and `outbox/` is empty again.

## 11. Isolation (S9, S18)

1. Simulate a second Mac for a second employee, `ana`:
   ```bash
   LINK=$(curl -s -X POST $Y/admin/employees -H "$ADMIN" -H "$J" -d '{"id":"ana","name":"Ana","role":"PM","timezone":"Asia/Jerusalem","profile_md":"Name: Ana"}' \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["invite_url"])')
   YOLK_HOME=/tmp/yolk-ana python3 $R claim "$LINK"
   ANA="Authorization: Bearer $(python3 -c 'import json; print(json.load(open("/tmp/yolk-ana/agent.json"))["device_token"])')"
   ```
2. Ana sees nothing of Greg's:
   ```bash
   curl -s $Y/v1/questions/pending -H "$ANA"    # {"question_sets":[]}
   curl -s $Y/v1/briefs -H "$ANA"               # {"briefs":[]}
   ```
3. Ana cannot post to Greg's set. Take a set id from `ls ~/.claude/yolk/audit/*.sent.json`:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST $Y/v1/questions/<greg set id>/answers -H "$ANA" -H "$J" \
     -d "{\"employee_id\":\"ana\",\"date\":\"$D\",\"round\":1,\"timezone\":\"Asia/Jerusalem\",\"generated_at\":\"${D}T12:00:00+03:00\",\"answers\":{\"answers\":[]}}"
   ```
   Expect `404`.
4. Clean up: `curl -s -X DELETE $Y/admin/employees/ana/device -H "$ADMIN"` and
   `rm -rf /tmp/yolk-ana`.

## 12. Revoke (S16)

1. `curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $Y/admin/employees/greg/device -H "$ADMIN"` prints `204`.
2. Run `/yolk:brief`. It reports that this Mac is no longer linked and tells you to run
   `/yolk:setup` again.
3. `~/.claude/yolk/agent.json` is gone. The briefs, reports and audit trail stay.

## 13. Leave (S15)

1. Get a new invite: `curl -s -X POST $Y/admin/employees/greg/invites -H "$ADMIN"`.
2. Run `/yolk:setup <invite_url>`, then `/yolk:leave`.
3. `~/.claude/yolk/agent.json` is gone, and `curl -s $Y/admin/employees -H "$ADMIN"` shows
   `greg` with device status `revoked`.

## 14. Clean up

1. Stop the API and the worker (Ctrl-C).
2. Optional: `cd $SERVER_DIR && just services-cleanup`. This deletes the local database.
3. Optional: `rm -rf ~/.claude/yolk`.

## Known limits

- **D5.** A brief for a date older than your newest local brief never arrives. If you check
  several dates, queue them oldest first.
- A bare invite code goes to `http://localhost:8080`, which is Keycloak on this Mac. Use the
  full link, or start Claude Code with `YOLK_SERVER=http://localhost:8081`.
- The server's `/join/<CODE>` page lists `/plugin install yolk@yolk` but not the
  `/plugin marketplace add` step that must come first.
- `R` points at the installed version `1.3.0`. Update the path after a version bump.
