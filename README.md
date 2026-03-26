# VibeBox

One-line setup for a vibe-coding terminal environment.

## Installation

```bash
curl -fsSL -H 'Cache-Control: no-cache' https://raw.githubusercontent.com/zobinHuang/vibebox/main/setup.sh | bash
```

## What it installs

- **tmux** — session, tab, and pane management (with zellij-style keybindings)
- **Yazi** — terminal file manager
- **Claude Code** — AI coding assistant
- **vbox** — command to manage tmux sessions

## Usage

```bash
vbox new <name>        # Create a new session
vbox attach <name>     # Attach to existing session
vbox ls                # List all sessions
vbox exit              # Kill current session
```

## Keybindings

### Tabs (`Ctrl+t` then...)

| Key | Action |
|---|---|
| `n` | New tab |
| `r` | Rename tab |
| `Left` / `Right` or `h` / `l` | Switch tab |
| `1`–`9` | Jump to tab by number |
| `x` | Close tab |

### Panes (`Ctrl+p` then...)

| Key | Action |
|---|---|
| `d` | Split down |
| `n` | Split right |
| `Left/Right/Up/Down` or `h/j/k/l` | Navigate panes |
| `x` | Close pane |
| `z` | Toggle fullscreen (zoom) |

### Yazi (file manager)

| Key | Action |
|---|---|
| `cr` | Copy relative path to clipboard |
| `s` | Search file contents |

Sessions are named `<username>-<name>`. Re-running the install script is safe — it always updates configs to the latest version.
