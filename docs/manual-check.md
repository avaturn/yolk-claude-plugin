# Manual end-to-end check (local)

The real plugin in Claude Code against the real `yolk-plugin-server` on this Mac. S-ids refer to
`yolk-plugin-server/docs/device-behaviour.md`. Automated equivalent: `cd yolk-plugin-server && just test`.

**Cost.** Step 4 bills the Manager AI (Anthropic API). `/yolk:answer` reads your real local data
for `$D`, for up to 25 minutes.

## Prerequisites

- Docker, `uv`, `just`, Python 3, and `claude` on PATH.
- `yolk-plugin-server` and `yolk-plugin-agent` side by side in `yolk-agentic/`.
- `yolk-plugin-server/override.env` with `ANTHROPIC__API_KEY=sk-ant-...`. Optional:
  `CLAUDE__OAUTH_TOKEN` (the company claude.ai account for goals and the leave lookup). Without it,
  the worker's `claude -p` keeps your `HOME`, so it most likely runs as your own claude.ai login
  and reads company sources through your connectors.
- The API runs on 8081, because Keycloak holds 8080 on this Mac.

## 1. Variables (in every terminal)

```bash
S=~/Development/yolk/yolk-agentic/yolk-plugin-server
Y=http://localhost:8081
J='Content-Type: application/json'
ADMIN="Authorization: Bearer $(cd $S && just dev-admin-token 2>/dev/null | tail -1)"
R=~/.claude/plugins/cache/yolk/yolk/1.3.0/scripts/employee_runner.py
D=$(date -v-1d +%F)
```

## 2. Server, plugin, employee

```bash
cd $S && uv sync
just services-start   # Postgres on localhost:54329
just db-upgrade
uv run uvicorn yolk_plugin_server.main:create_app --factory --port 8081   # terminal A, leave it running
cd $S && just run-worker                                                                      # terminal B, leave it running
curl -s $Y/api/health                                                                         # terminal C: {"status":"ok"}
```

1. Export `YOLK_SERVER=$Y`, then start Claude Code from that shell (the plugin has no other way to find
   a local server). Install the plugin: [README → From a local checkout](../README.md#from-a-local-checkout-development).
   Open a new Claude Code session.
2. Create the employee and note `invite_code`:
   ```bash
   curl -s -X POST $Y/admin/employees -H "$ADMIN" -H "$J" \
     -d '{"id":"greg","name":"Gregory","role":"Backend engineer","timezone":"Asia/Jerusalem","profile_md":"Name: Gregory\nRole: Backend engineer\nWhat real work looks like: reviewed, tested code; small PRs."}'
   ```
   On `409`, get a new invite: `curl -s -X POST $Y/admin/employees/greg/invites -H "$ADMIN"`.

## 3. Link (S1, S2, S3, S14)

| Do | Expect |
|---|---|
| `/yolk:setup <invite_code>`, accept the consent, decline the poller | `~/.claude/yolk/agent.json` is `-rw-------`; `profile.md` holds the profile |
| `curl -s $Y/admin/employees -H "$ADMIN"` | `greg`: `active`, `last_seen_at` set |
| `YOLK_SERVER=$Y python3 $R claim <invite_code>` | "already used or expired" (S3); `agent.json` unchanged |
| `YOLK_SERVER=$Y python3 $R claim ZZZZ-9999` | "isn't valid" (S2); `agent.json` unchanged |

## 4. Run and answer (S5, S6, S10)

1. Queue the run. A `409` means the run is already queued (e.g. by the daily task).
   ```bash
   curl -s -X POST $Y/admin/runs -H "$ADMIN" -H "$J" -d "{\"date\":\"$D\",\"employee_id\":\"greg\"}"
   ```
2. Watch `curl -s "$Y/admin/runs?date=$D" -H "$ADMIN" | python3 -m json.tool`: `created` → `awaiting_r1` → (`following_up` → `awaiting_r2`) → `reporting` → `published`.
   On `failed`, read `failure_reason` and `failed_in`.
3. At `awaiting_r1`, and again at `awaiting_r2`, run `/yolk:answer`. Expect
   `~/.claude/yolk/audit/<set id>.sent.json` and an empty `outbox/`. With nothing pending, it
   says so (S5).

## 5. Brief and report (S13, S14)

When the run is `published`:

1. Run `/yolk:brief` and `/yolk:report`.
2. Check that the report is the manager's copy:
   ```bash
   curl -s $Y/admin/employees/greg/runs/$D -H "$ADMIN" \
     | python3 -c 'import json,sys; print(json.load(sys.stdin)["report_markdown"])' \
     | diff - ~/.claude/yolk/reports/$D.md && echo same
   ```
3. `last_seen_at` in `/admin/employees` moves forward after `/yolk:brief` (S14).

## 6. Repeated post (S7)

Only with nothing pending, because this poll also investigates pending sets:

```bash
cd ~/.claude/yolk && f=$(ls audit/*.sent.json | head -1) && cp "$f" "outbox/$(basename "$f" .sent.json).json" && python3 $R
```

Expect "server already had them" and an empty `outbox/`.

## 7. Isolation (S9, S18)

```bash
L=$(curl -s -X POST $Y/admin/employees -H "$ADMIN" -H "$J" \
  -d '{"id":"ana","name":"Ana","role":"PM","timezone":"Asia/Jerusalem","profile_md":"Name: Ana"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["invite_code"])')
YOLK_SERVER=$Y YOLK_HOME=/tmp/yolk-ana python3 $R claim "$L"
ANA="Authorization: Bearer $(python3 -c 'import json; print(json.load(open("/tmp/yolk-ana/agent.json"))["device_token"])')"
SET=$(basename "$(ls ~/.claude/yolk/audit/*.sent.json | head -1)" .sent.json)

curl -s $Y/v1/questions/pending -H "$ANA"    # {"question_sets":[]}
curl -s $Y/v1/briefs -H "$ANA"               # {"briefs":[]}
curl -s -o /dev/null -w '%{http_code}\n' -X POST $Y/v1/questions/$SET/answers -H "$ANA" -H "$J" \
  -d "{\"employee_id\":\"ana\",\"date\":\"$D\",\"round\":1,\"timezone\":\"Asia/Jerusalem\",\"generated_at\":\"${D}T12:00:00+03:00\",\"answers\":{\"answers\":[]}}"   # 404

curl -s -X DELETE $Y/admin/employees/ana/device -H "$ADMIN"; rm -rf /tmp/yolk-ana
```

## 8. Revoke and leave (S15, S16)

| Do | Expect |
|---|---|
| `curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $Y/admin/employees/greg/device -H "$ADMIN"` prints `204`, then `/yolk:brief` | "no longer linked"; `agent.json` gone; briefs, reports and audit kept (S16) |
| New invite (`curl -s -X POST $Y/admin/employees/greg/invites -H "$ADMIN"`), `/yolk:setup <invite_code>`, then `/yolk:leave` | `agent.json` gone; `greg` device `revoked` (S15) |

## 9. Clean up

Stop the API and the worker with Ctrl-C. Optional: `cd $S && just services-cleanup` (drops the
local database) and `rm -rf ~/.claude/yolk`.

## Known limits

- **D5.** A brief for a date older than your newest local brief never arrives. Check dates
  oldest first.
- The invite code carries no server address; without `YOLK_SERVER` the plugin uses `https://plugin.dev.yolk.coach`.
- `R` is pinned to plugin version 1.3.0.
