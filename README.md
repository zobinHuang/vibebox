# Oh-my-Boy

One-line setup for a vibe-coding terminal environment.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/zobinHuang/a-terminal/main/setup.sh | bash
```

## What it installs

- **Zellij** — terminal multiplexer for pane management
- **Yazi** — terminal file manager
- **Claude Code** — AI coding assistant
- **boy** — command to launch a tmux+zellij session

## Usage

```bash
boy <name>            # Create and attach to a new tmux+zellij session
boy attach <name>     # Attach to an existing session
boy exit              # Kill current zellij+tmux session
```

Sessions are named `<username>-<name>`. Running `boy <name>` on an existing session reattaches to it.

Re-running the install script is safe — it skips anything already installed.
