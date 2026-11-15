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

## Skills

vboxd keeps an index of skills, never their files: an entry records which git
repository a skill lives in, which path inside it, and which commits its refs
have pointed at. A client fetches the files itself from the repository.

| | |
|---|---|
| `GET /v1/skills` | the index, filtered by `q` (name, description or id) and `repo` |
| `POST /v1/skills` | add a skill, or refresh the entry that already names it |
| `GET /v1/skills/<id>` | one entry |
| `PATCH /v1/skills/<id>` | change `name`, `description` or `default_ref` |
| `DELETE /v1/skills/<id>` | remove the entry |
| `GET /v1/skills/<id>/versions` | every commit this server has seen on the tracked ref |
| `POST /v1/skills/<id>/refresh` | re-resolve the tracked ref and record what upstream has now |

`POST` takes either a `source` spec or the fields spelled out:

```bash
# owner/repo, a path inside it after @, and a ref after #
curl -sH "Authorization: Bearer $TOKEN" "$BASE/v1/skills" \
  -d '{"source": "anthropics/skills@frontend-design#main",
       "description": "house frontend conventions"}'

# the same, spelled out; any https, ssh or scp-style git URL works
curl -sH "Authorization: Bearer $TOKEN" "$BASE/v1/skills" \
  -d '{"repo_url": "https://gitlab.com/team/skills",
       "skill_path": "review/security", "ref": "v2"}'
```

A skill's id is derived from its host, repository and path — never from its ref —
so registering the same skill at another version updates one entry rather than
creating a second. Registering resolves the ref to a commit straight away, with
`git ls-remote`, so an entry is never stored without a usable version.

`name` and `description` are whatever the registering client supplies; vboxd does
not read `SKILL.md`, because it does not fetch skill files at all. Without them,
`name` falls back to the last path segment.

Only repositories vboxd is willing to contact are accepted: `https`, `http`,
`ssh` and scp-style URLs, plus the `owner/repo` shorthand. Local paths and the
`ext::` and `file::` transports are refused, and a `..` segment in a skill path
is refused — the client turns that path into a checkout directory.

## Playbooks

A playbook is a named set of skills, drawn from as many repositories as you like,
plus the standing instructions that go with them. It too is an index: it stores
pins, not files.

| | |
|---|---|
| `GET /v1/playbooks` | the index, filtered by `q` |
| `POST /v1/playbooks` | create one |
| `GET /v1/playbooks/<id>` | one playbook |
| `PATCH /v1/playbooks/<id>` | change `name`, `description`, `prompt` or `skills` |
| `DELETE /v1/playbooks/<id>` | remove it |
| `GET /v1/playbooks/<id>/resolve` | ★ every skill pinned to a commit, ready to install |

```bash
curl -sH "Authorization: Bearer $TOKEN" "$BASE/v1/playbooks" \
  -d '{"name": "Backend review",
       "description": "what to load when reviewing the backend",
       "prompt": "Follow the loaded conventions exactly; they override local habit.",
       "skills": ["github-com-anthropics-skills-frontend-design",
                  {"skill_id": "gitlab-com-team-skills-review-security", "pin": "v2"}]}'
```

A skill entry is either a bare id, which tracks the latest version, or an object
with a `pin`. A `PATCH` changes only the fields it carries, so sending `skills`
replaces the list while leaving the prompt alone.

### What a pin means

| Pin | |
|---|---|
| `latest` (the default) | re-resolved against the tracked ref every time the playbook is resolved |
| a branch or tag | re-resolved against the repository every time, because a ref is a pointer that moves |
| a commit | never moves, and is answered without contacting the repository once it has been recorded |

### `GET /v1/playbooks/<id>/resolve`

This is the endpoint a client installs from. It turns every pin into a concrete
commit at this moment and returns, in one response, the prompt and everything
needed to fetch each skill.

```json
{
  "id": "backend-review",
  "name": "Backend review",
  "description": "what to load when reviewing the backend",
  "prompt": "Follow the loaded conventions exactly; they override local habit.",
  "skills": [
    {
      "skill_id": "github-com-anthropics-skills-frontend-design",
      "name": "frontend-design",
      "description": "house frontend conventions",
      "repo_url": "https://github.com/anthropics/skills",
      "skill_path": "frontend-design",
      "ref": "main",
      "commit": "e1c9489ca95b5fc960acc4809dfb84613bdf24fd",
      "pin": "latest"
    }
  ],
  "unresolved": [],
  "resolved_at": "2026-11-16T09:42:10.004+00:00"
}
```

There is no background polling: upstream is consulted exactly when a playbook is
resolved, which is when a client is about to install it.

A skill that has since been removed from the index, or whose repository cannot be
reached, lands in `unresolved` with the reason instead of failing the whole
playbook — a client installs what it can and shows the rest.

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
