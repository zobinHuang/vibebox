# Playbooks

A playbook is a named set of skills plus the standing instructions that go with
them. The server holds the index; the skill files are fetched from their own git
repositories, pinned to a commit, and installed into the project you are working
in.

```bash
# the server holds the index — see server/API.md for the full CRUD surface
curl -sH "Authorization: Bearer $TOKEN" "$BASE/v1/skills" \
  -d '{"source": "owner/repo@review/security", "name": "security-review",
       "description": "what to check before shipping"}'
curl -sH "Authorization: Bearer $TOKEN" "$BASE/v1/playbooks" \
  -d '{"name": "Backend review", "prompt": "Follow the loaded conventions.",
       "skills": ["github-com-owner-repo-review-security"]}'

# on a client
vbox-playbook list
vbox-playbook install backend-review
vbox-playbook uninstall
```

Starting an agent in a vbox pane offers you a playbook in a popup; `Ctrl+t` `p`
raises the same picker, so you can change a pane's playbook with an agent already
running in it. The agent is told on its next turn.

## Where skills are installed

Per project, never globally, because the agents disagree about where to look and
there is no one directory all of them scan.

| Agent | Directory | |
|---|---|---|
| Claude Code | `<project>/.claude/skills/` | |
| codex | `<project>/.agents/skills/` | scanned from the working directory up to the repository root; `.codex/skills` is its old path and is no longer read |
| gemini-cli | `<project>/.agents/skills/` | its documented alias for the same standard, so it shares codex's copy |

`<project>` is the git repository root when there is one, so a skill installed
from a subdirectory is visible from anywhere in the project. Switching playbooks
removes the previous set; a skill you put there by hand is never touched.

## How the agent is told

Claude Code and codex both inject a hook's `additionalContext` into the model at
`SessionStart` and `UserPromptSubmit`, and that is what carries the playbook's
prompt and the roster of what was loaded. **gemini-cli has no hook system**, so it
gets the skill files and nothing else — it will discover them, but it is not told
the playbook's instructions.

The roster names each skill's on-disk directory rather than relying on the agent
noticing a directory that changed after it started.

## Versions

Each skill entry records the commits its ref has pointed at. A playbook pins each
skill one of three ways, and the difference matters:

| Pin | |
|---|---|
| `latest` (the default) | re-resolved every time the playbook is loaded, so you get what upstream has now |
| a branch or tag | re-resolved every time, because a ref is a pointer that moves |
| a commit | never moves |

Upstream is consulted when a playbook is loaded — when an agent session starts, or
when you pick one with `Ctrl+t` `p`. Nothing polls in the background.

Fetched trees are cached by commit under `~/.local/share/vibebox/skills/`, so
switching back to a playbook you have used before costs no network at all.
