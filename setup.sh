#!/usr/bin/env bash
set -euo pipefail

# ─── colors ───────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*"; }

# ─── install via conda ────────────────────────────────────────────────
install_pkg() {
  if ! command -v conda &>/dev/null; then
    err "conda not found. Please install Miniconda or Anaconda first."
    return 1
  fi
  conda install -y -c conda-forge "$@"
}

# ──────────────────────────────────────────────────────────────────────
COMMIT_HASH="$(curl -fsSL https://api.github.com/repos/zobinHuang/vibebox/commits/main 2>/dev/null | grep -m1 '"sha"' | cut -d'"' -f4 | cut -c1-7)" || true
COMMIT_HASH="${COMMIT_HASH:-unknown}"

echo ""
echo "══════════════════════════════════════════════════"
echo "  VibeBox Setup"
echo "  commit: $COMMIT_HASH"
echo "══════════════════════════════════════════════════"

# ─── 1. zellij ────────────────────────────────────────────────────────
echo ""
echo "── Zellij ───────────────────────────────────────"

if command -v zellij &>/dev/null; then
  info "Zellij already installed ($(zellij --version))"
else
  warn "Installing Zellij …"
  install_pkg zellij
  info "Zellij installed"
fi

# ─── 2. yazi ─────────────────────────────────────────────────────────
echo ""
echo "── Yazi ─────────────────────────────────────────"

if command -v yazi &>/dev/null; then
  info "Yazi already installed ($(yazi --version))"
else
  warn "Installing Yazi …"
  install_pkg yazi
  info "Yazi installed"
fi

# Nerd Font (required for yazi icons — not available via conda)
if command -v brew &>/dev/null; then
  if brew list --cask font-symbols-only-nerd-font &>/dev/null 2>&1; then
    info "Nerd Font already installed"
  else
    warn "Installing Nerd Font …"
    brew install --cask font-symbols-only-nerd-font
    info "Nerd Font installed"
  fi
else
  warn "Please install a Nerd Font manually: https://www.nerdfonts.com/"
fi

# ─── patch yazi config ────────────────────────────────────────────────
YAZI_DIR="$HOME/.config/yazi"
YAZI_CONFIG="$YAZI_DIR/yazi.toml"
YAZI_KEYMAP="$YAZI_DIR/keymap.toml"
YAZI_MARKER="# [vibebox] patched"

mkdir -p "$YAZI_DIR"

if [ -f "$YAZI_CONFIG" ] && grep -qF "$YAZI_MARKER" "$YAZI_CONFIG" 2>/dev/null; then
  info "yazi.toml already patched"
else
  cat > "$YAZI_CONFIG" <<'TOML'
# [vibebox] patched
[mgr]
show_hidden = true
ratio = [0, 1, 3]
TOML
  info "Patched yazi.toml"
fi

if [ -f "$YAZI_KEYMAP" ] && grep -qF "$YAZI_MARKER" "$YAZI_KEYMAP" 2>/dev/null; then
  info "keymap.toml already patched"
else
  cat > "$YAZI_KEYMAP" <<'TOML'
# [vibebox] patched
[[mgr.prepend_keymap]]
on = [ "c", "r" ]
desc = "Copy relative path (from git root) to clipboard"
run = "shell -- python3 -c \"import os,sys,subprocess,base64; root=subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=os.path.dirname(sys.argv[1]),stderr=subprocess.DEVNULL).decode().strip(); p=os.path.relpath(sys.argv[1],root); b=base64.b64encode(p.encode()).decode(); sys.stdout.write('\\x1b]52;c;'+b+'\\x07'); sys.stdout.flush()\" \"$0\""

[[mgr.prepend_keymap]]
on = [ "s" ]
desc = "Search file contents (grep via ripgrep)"
run = "search --via=rg"
TOML
  info "Patched keymap.toml"
fi

# ─── patch tmux config ────────────────────────────────────────────────
TMUX_CONF="$HOME/.tmux.conf"
TMUX_MARKER="# [vibebox] patched"

if [ -f "$TMUX_CONF" ] && grep -qF "$TMUX_MARKER" "$TMUX_CONF" 2>/dev/null; then
  info ".tmux.conf already patched"
else
  cat > "$TMUX_CONF" <<'TMUX'
# [vibebox] patched
set -g set-clipboard on
set -g allow-passthrough on
set -g mouse on
set -g mode-keys vi
TMUX
  info "Patched .tmux.conf (OSC 52 clipboard, mouse, vi copy mode)"
fi

# ─── install OSC 52 clipboard helper ─────────────────────────────────
OSC52_BIN="$HOME/.local/bin/osc52-copy"
printf '%s\n' '#!/usr/bin/env bash' \
  'data=$(base64 | tr -d '\''\n'\'')' \
  'if [ -n "${TMUX:-}" ]; then' \
  '  TMUX_PANE_TTY=$(tmux display-message -p "#{pane_tty}")' \
  '  printf '\''\033]52;c;%s\a'\'' "$data" > "$TMUX_PANE_TTY"' \
  'else' \
  '  printf '\''\033]52;c;%s\a'\'' "$data" > /dev/tty' \
  'fi' > "$OSC52_BIN"
chmod +x "$OSC52_BIN"
info "Installed osc52-copy helper"

# ─── patch zellij config ─────────────────────────────────────────────
ZELLIJ_DIR="$HOME/.config/zellij"
ZELLIJ_CONFIG="$ZELLIJ_DIR/config.kdl"
ZELLIJ_MARKER="// [vibebox] patched"

mkdir -p "$ZELLIJ_DIR"

if [ -f "$ZELLIJ_CONFIG" ] && grep -qF "$ZELLIJ_MARKER" "$ZELLIJ_CONFIG" 2>/dev/null; then
  info "zellij config already patched"
else
  printf '%s\n' \
    '// [vibebox] patched' \
    'copy_on_select true' \
    "copy_command \"$HOME/.local/bin/osc52-copy\"" \
    'scrollback_editor "vim"' > "$ZELLIJ_CONFIG"
  info "Patched zellij config (OSC 52 clipboard via osc52-copy)"
fi

# ─── patch vimrc ──────────────────────────────────────────────────────
VIMRC="$HOME/.vimrc"
VIMRC_MARKER="\" [vibebox] patched"

if [ -f "$VIMRC" ] && grep -qF "$VIMRC_MARKER" "$VIMRC" 2>/dev/null; then
  info ".vimrc already patched"
else
  cat > "$VIMRC" <<'VIM'
" [vibebox] patched
syntax on
set number
VIM
  info "Patched .vimrc (line numbers + syntax highlighting)"
fi

# ─── 3. claude code ──────────────────────────────────────────────────
echo ""
echo "── Claude Code ────────────────────────────────────"

if command -v claude &>/dev/null; then
  info "Claude Code already installed ($(claude --version 2>/dev/null || echo 'unknown version'))"
else
  warn "Installing Claude Code …"
  curl -fsSL https://claude.ai/install.sh | bash
  info "Claude Code installed"
fi

# ─── 4. install vbox command ─────────────────────────────────────
echo ""
echo "── vbox command ──────────────────────────────"

VBOX_BIN="$HOME/.local/bin/vbox"

mkdir -p "$HOME/.local/bin"
printf '%s\n' '#!/usr/bin/env bash' \
  '# [vibebox]' \
  'set -euo pipefail' \
  '' \
  'usage() {' \
  '  echo "Usage:"' \
  '  echo "  vbox new <session-name>   Create and attach to a new tmux+zellij session"' \
  '  echo "  vbox attach <name>        Attach to an existing session"' \
  '  echo "  vbox ls                   List all vbox sessions"' \
  '  echo "  vbox exit                 Kill current zellij and tmux session"' \
  '  exit 1' \
  '}' \
  '' \
  'if [ $# -lt 1 ]; then' \
  '  usage' \
  'fi' \
  '' \
  'CMD="$1"' \
  '' \
  'case "$CMD" in' \
  '  exit)' \
  '    if [ -n "${TMUX:-}" ]; then' \
  '      tmux kill-session' \
  '    else' \
  '      echo "Not inside a tmux session."' \
  '    fi' \
  '    ;;' \
  '  attach)' \
  '    if [ $# -lt 2 ]; then' \
  '      echo "Usage: vbox attach <session-name>"' \
  '      exit 1' \
  '    fi' \
  '    SESSION_NAME="$(whoami)-$2"' \
  '    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then' \
  '      tmux attach-session -t "$SESSION_NAME"' \
  '    else' \
  '      echo "Session '\''$SESSION_NAME'\'' not found."' \
  '      echo "Available sessions:"' \
  '      tmux list-sessions 2>/dev/null || echo "  (none)"' \
  '      exit 1' \
  '    fi' \
  '    ;;' \
  '  ls)' \
  '    PREFIX="$(whoami)-"' \
  '    FOUND=0' \
  '    while IFS= read -r line; do' \
  '      NAME="${line%%:*}"' \
  '      if [[ "$NAME" == "$PREFIX"* ]]; then' \
  '        echo "  ${NAME#$PREFIX}"' \
  '        FOUND=1' \
  '      fi' \
  '    done < <(tmux list-sessions 2>/dev/null || true)' \
  '    if [ "$FOUND" -eq 0 ]; then' \
  '      echo "No vbox sessions."' \
  '    fi' \
  '    ;;' \
  '  new)' \
  '    if [ $# -lt 2 ]; then' \
  '      echo "Usage: vbox new <session-name>"' \
  '      exit 1' \
  '    fi' \
  '    SESSION_NAME="$(whoami)-$2"' \
  '    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then' \
  '      echo "Session '\''$SESSION_NAME'\'' already exists. Use '\''vbox attach $2'\'' instead."' \
  '      exit 1' \
  '    else' \
  '      tmux new-session -d -s "$SESSION_NAME" '\''zellij'\''' \
  '      tmux set-option -t "$SESSION_NAME" status-left " $2 "' \
  '      tmux attach-session -t "$SESSION_NAME"' \
  '    fi' \
  '    ;;' \
  '  -h|--help)' \
  '    usage' \
  '    ;;' \
  '  *)' \
  '    usage' \
  '    ;;' \
  'esac' > "$VBOX_BIN"
chmod +x "$VBOX_BIN"
info "Installed vbox command to $VBOX_BIN"

# ensure ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
  warn "Add ~/.local/bin to your PATH. For example:"
  warn "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
fi

# ─── done ─────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo "  Setup complete!"
echo "  vbox new <name>       Create a new tmux+zellij session"
echo "  vbox attach <name>   Attach to an existing session"
echo "  vbox ls              List all vbox sessions"
echo "  vbox exit            Kill current zellij+tmux session"
echo ""
echo "  yazi                 Browse files"
echo "  claude               Start Claude Code"
echo "══════════════════════════════════════════════════"
echo ""
