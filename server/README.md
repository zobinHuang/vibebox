# vboxd

The server side of VibeBox. It stores the JSON transcripts that Claude Code and
codex write while you work, split by agent and by the account that was logged in,
and it keeps an index of the skills and playbooks your agents load. It is Python
standard library only — no virtualenv, no packages to install.

## Usage

```bash
# deploy, start and health-check in one shot; prints the client command to run
curl -fsSL -H 'Accept: application/vnd.github.v3.raw' https://api.github.com/repos/zobinHuang/vibebox/contents/server/setup.sh | bash -s -- --port 8787

# serve over HTTPS instead of plain HTTP
server/setup.sh --port 8787 --tls-cert /etc/ssl/vboxd.crt --tls-key /etc/ssl/vboxd.key

# show the token clients need
vboxd token

# list what has been stored
vboxd ls
```

Re-running the deploy upgrades in place and keeps the existing token.

See [API.md](API.md) to read sessions back out of a server, and
[session history](../docs/sessions.md) for the storage layout and what ends up on
disk.
