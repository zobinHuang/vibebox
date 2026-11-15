# vibebox

@humanize/git-conventions.md
@humanize/python-coding-conventions.md
@humanize/readme-conventions.md

## Hard constraints

- Never touch the user's live tmux server, `~/.tmux.conf`, `~/.claude/settings.json` or
  `$CODEX_HOME` while testing. Use an isolated socket (`tmux -L <name>`) and a fake
  `$HOME` / `$CODEX_HOME` / `$CLAUDE_CONFIG_DIR`.
- Never run `vbox-agent` from outside tmux: its bare `tmux` calls land on the default
  socket and write agent state onto live sessions.
- The status bar renders in tmux's own format language. Adding a `#()` to it breaks the
  1s redraw (see commit 1f15349); keep rendering free of subprocesses.
- `setup.sh` fetches from `main`, so a change only reaches users once it is pushed there.

## Verification

- tmux config changes: source into an isolated socket and assert `show-options` /
  `list-keys` output, then replay the real keypresses with `send-keys`.
- Hook merges: run against a fake `$HOME`, assert the resulting JSON, and re-run to
  prove idempotence.
- Server: `python3 -m unittest` against the request handler, no network.
