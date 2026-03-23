# VibeBox

One-line setup for a vibe-coding terminal environment.

## Installation

```bash
curl -fsSL -H 'Cache-Control: no-cache' https://raw.githubusercontent.com/zobinHuang/vibebox/main/setup.sh | bash
```

## What it installs

- **Zellij** — terminal multiplexer for pane management
- **Yazi** — terminal file manager
- **Claude Code** — AI coding assistant
- **vbox** — command to launch a tmux+zellij session

## Usage

```bash
vbox new <name>        # Create and attach to a new tmux+zellij session
vbox attach <name>     # Attach to an existing session
vbox exit              # Kill current zellij+tmux session
```

Sessions are named `<username>-<name>`. Running `vbox attach <name>` on an existing session reattaches to it.

Re-running the install script is safe — it skips anything already installed.
