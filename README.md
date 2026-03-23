# a-terminal

One-line setup for a vibe-coding terminal environment.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/zobinHuang/a-terminal/main/setup.sh | bash
```

## What it installs

- **Zellij** — terminal multiplexer for pane management
- **Yazi** — terminal file manager
- **Claude Code** — AI coding assistant
- **aterminal** — command to launch a tmux+zellij session

## Usage

```bash
aterminal <session-name>
```

Opens a tmux session named `<username>-<session-name>` with zellij running inside. Re-running the same command reattaches to the existing session.

Re-running the install script is safe — it skips anything already installed.
