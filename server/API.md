# vboxd HTTP API

Everything a reading client needs to pull agent sessions off a vboxd server. The
write side — how a `vbox connect` client ships transcripts up — is in
[session history](../docs/sessions.md).

Base path is `/v1`. Bodies and responses are JSON, except a transcript download,
which is newline-delimited JSON. Every route but `/v1/healthz` requires the
server's token:

```
Authorization: Bearer <token>
```

Run `vboxd token` on the server to print it.

## Reading

| | |
|---|---|
| `GET /v1/healthz` | `{"ok":true,"version":"1.0.0"}` — no token needed |
| `GET /v1/sessions` | the session list, filtered and paged (below) |
| `GET /v1/sessions/<id>` | one session's metadata |
| `GET /v1/sessions/<id>/transcript` | the stored transcript as `application/x-ndjson` |
| `GET /v1/machines` | one row per machine that has sessions here |
| `GET /v1/accounts` | one row per agent account that has sessions here |

### `GET /v1/sessions`

All parameters are optional and combine with AND.

| Parameter | |
|---|---|
| `agent` | `claude` or `codex` |
| `account` | an account uuid, as `/v1/accounts` reports it |
| `hostname` | a machine's short name or its FQDN; either matches |
| `project` | case-insensitive substring of the session's working directory |
| `branch` | exact git branch |
| `status` | `open` (still running) or `closed` |
| `since`, `until` | inclusive bounds, ISO 8601 (`2026-11-16T09:00:00Z`) or epoch seconds |
| `time` | which timestamp `since`/`until`/ordering use: `updated` (default), `opened`, `closed` |
| `limit` | page size, 1–1000, default 100 |
| `cursor` | the `next_cursor` of the previous page |

```json
{
  "sessions": [ { "...": "one metadata object per session" } ],
  "next_cursor": "MjAyNi0xMS0xNlQwOTowMDowMC4xMjMrMDA6MDB8YWJjMTIz",
  "total_matched": 418
}
```

Results are newest first by the chosen timestamp, with the session id breaking
ties, so the ordering is total and paging never skips or repeats a session. Page
until `next_cursor` is `null`.

```bash
# everything one machine did this week, newest first
curl -sH "Authorization: Bearer $TOKEN" \
  "$BASE/v1/sessions?hostname=buildbox&since=2026-11-09T00:00:00Z"

# codex sessions of one account that are still running
curl -sH "Authorization: Bearer $TOKEN" \
  "$BASE/v1/sessions?agent=codex&account=8f280439-e627-4d0a-a9fe-1ad66186b2b4&status=open"

# page through one project's history
curl -sH "Authorization: Bearer $TOKEN" \
  "$BASE/v1/sessions?project=vibebox&limit=50"
```

### Session metadata

```json
{
  "agent": "claude",
  "session_id": "a07f54b5-e80e-467c-a551-073b8b330911",
  "account": {
    "uuid": "db2496cb-e9db-4b5b-810e-124951f9e4e1",
    "email": "dev@example.com",
    "label": "000de34f-64a9-48cc-b41f-573492cf4a70"
  },
  "machine": {
    "hostname": "buildbox",
    "fqdn": "buildbox.lan",
    "os": "Linux",
    "os_release": "6.8.0-45-generic",
    "arch": "x86_64",
    "user": "dev"
  },
  "cwd": "/srv/vibebox",
  "git_branch": "main",
  "cli_version": null,
  "transcript_path": "/home/dev/.claude/projects/-srv-vibebox/a07f54b5-....jsonl",
  "opened_at": "2026-11-16T09:00:00.123+00:00",
  "updated_at": "2026-11-16T09:42:10.004+00:00",
  "closed_at": null
}
```

`account.label` is the organization uuid for Claude Code and the codex user id for
codex. `account.uuid` is `anonymous` when the client could not determine one.
`email` is `null` if the client connected with `--no-email`.

### `GET /v1/sessions/<id>/transcript`

Returns the raw bytes vibebox stored, which are byte-for-byte the agent's own
JSONL transcript — one JSON object per line, in the agent's own schema.

| Parameter | |
|---|---|
| `offset` | start at this byte; default 0 |
| `limit` | return at most this many bytes |

| Response header | |
|---|---|
| `X-Vbox-Offset` | the byte the body starts at |
| `X-Vbox-Total` | the whole stored transcript's length |

A transcript only grows, so a client that remembers `X-Vbox-Total` can poll for
new output without re-downloading:

```bash
# first fetch
curl -sD headers -H "Authorization: Bearer $TOKEN" \
  "$BASE/v1/sessions/$ID/transcript" -o session.jsonl

# later, only what was appended since
# (tr strips the padding BSD wc writes, which would otherwise land in the URL)
HAVE=$(wc -c < session.jsonl | tr -d ' ')
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/v1/sessions/$ID/transcript?offset=$HAVE" >> session.jsonl
```

If the agent rewrote its transcript rather than appending to it, the server moved
the old one aside and started over, so `X-Vbox-Total` comes back smaller than
what the client holds. Re-fetch from offset 0 when that happens.

### `GET /v1/machines`, `GET /v1/accounts`

The two lists a UI needs to build its filters, busiest first.

```json
{
  "machines": [
    { "hostname": "buildbox", "fqdn": "buildbox.lan", "os": "Linux",
      "arch": "x86_64", "session_count": 212, "last_seen": "2026-11-16T09:42:10.004+00:00" }
  ]
}
```

```json
{
  "accounts": [
    { "agent": "claude", "uuid": "db2496cb-...", "email": "dev@example.com",
      "label": "000de34f-...", "session_count": 180,
      "last_seen": "2026-11-16T09:42:10.004+00:00" }
  ]
}
```

## Writing

A client that uploads transcripts uses these. They are documented for
completeness; `vbox connect` is the supported way to drive them.

| | |
|---|---|
| `POST /v1/sessions/<id>/begin` | register or refresh a session; returns its stored length |
| `PUT /v1/sessions/<id>/data?offset=N[&reset=1]` | append the bytes that follow `N` |
| `POST /v1/sessions/<id>/close` | mark the session finished |

`begin` takes the metadata object above minus the timestamps, and answers
`{"cursor": N, "prefix_sha256": "..."}`: the stored length, and the SHA-256 of the
first `min(4096, N)` stored bytes. A client hashes the same byte range of its own
copy; a mismatch means the transcript was rewritten, and it re-uploads with
`reset=1`.

`PUT` bodies must carry a `Content-Length`; chunked bodies are refused.

## Errors

Every failure answers with the status below and a body of
`{"error": "<class>", "message": "<what went wrong>"}`.

| | |
|---|---|
| `400` | a parameter or body field is missing, malformed or unsafe |
| `401` | no bearer token, or one that does not match |
| `404` | no such route, or no such session |
| `409` | a `PUT` offset disagrees with the stored length; the body carries the real `cursor` |
| `413` | the body is over the server's per-request limit |
| `500` | the store could not be read or written |
