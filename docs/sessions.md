# Session history

`vbox connect` makes every Claude Code and codex session in your panes upload its
own JSON transcript to a [vboxd](../server/) server as you work.

```bash
# on the server
curl -fsSL -H 'Accept: application/vnd.github.v3.raw' https://api.github.com/repos/zobinHuang/vibebox/contents/server/setup.sh | bash

# on each client, with the token the server printed
vbox connect <host> <port> --token <token>

# don't send the login email address along with the account id
vbox connect <host> <port> --token <token> --no-email

# serve over HTTPS with a self-signed certificate
vbox connect <host> <port> --token <token> --insecure

# check the connection and the backlog
vbox sessions

# stop uploading
vbox disconnect
```

Changing an agent's hook config invalidates the trust hash codex keeps for it, so
run `/hooks` in codex once after connecting or disconnecting.

## What is uploaded

| Agent | Transcript | Account identity |
|---|---|---|
| Claude Code | `~/.claude/projects/<project>/<session-id>.jsonl` | `oauthAccount.accountUuid` from `~/.claude.json` — the transcript carries none |
| codex | `~/.codex/sessions/<y>/<m>/<d>/rollout-*-<session-id>.jsonl` | `creator_account_id` from the transcript's `session_meta` line |

Uploads happen at turn boundaries — `SessionStart`, `Stop`, `StopFailure`,
`PreCompact`, `SessionEnd` — never on tool calls. Alongside the transcript the
client sends the working directory, the git branch, the account, and the machine
the session ran on: short hostname, FQDN, OS, OS release, architecture and unix
user. The same agent account is usually logged in on several boxes, so that is
what tells their sessions apart.

To read sessions back off a server, see the [vboxd API](../server/API.md).

## Storage layout

```
<data-root>/<agent>/<account-uuid>/<project>-<cwd-digest>/<session-id>/
    transcript.jsonl
    meta.json
```

A transcript that was rewritten rather than appended to is moved aside as
`transcript.jsonl.<timestamp>` and uploaded again from the start. Nothing is ever
deleted or expired.

## Protocol

| | |
|---|---|
| `GET /v1/healthz` | liveness; the only route that needs no token |
| `POST /v1/sessions/<id>/begin` | register or refresh a session, and report its stored length |
| `PUT /v1/sessions/<id>/data?offset=N[&reset=1]` | append the bytes that follow `N` |
| `POST /v1/sessions/<id>/close` | mark the session finished |
| `GET /v1/sessions` | list what is stored |

Every request but the health check carries `Authorization: Bearer <token>`.

The server, not the client, is the authority on how much of a transcript has been
stored: each upload asks for the stored length and the digest of its leading bytes,
then sends only what follows. A stale offset is answered with `409` and the real
cursor. That is what makes a corrupt local cursor, two machines uploading one
session, a flush killed halfway, and a server that was down for a day all heal on
the next turn — the local transcript is the whole truth, and `SessionStart` pushes
whatever is still behind.

## Privacy

A transcript holds everything the agent saw: your source, your paths, command
output, and whatever you typed. Treat the server as holding the same secrets your
working copy does. It stores files `0600` under `0700` directories, and `vboxd`
over plain HTTP sends them in the clear — give it `--tls-cert`/`--tls-key`, or keep
it on a trusted network.
