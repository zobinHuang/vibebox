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
echo ""
echo "══════════════════════════════════════════════════"
echo "  Terminal Vibe-Coding Setup"
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
YAZI_MARKER="# [a-terminal] patched"

mkdir -p "$YAZI_DIR"

if [ -f "$YAZI_CONFIG" ] && grep -qF "$YAZI_MARKER" "$YAZI_CONFIG" 2>/dev/null; then
  info "yazi.toml already patched"
else
  cat > "$YAZI_CONFIG" <<'TOML'
# [a-terminal] patched
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
# [a-terminal] patched
[[mgr.prepend_keymap]]
on = [ "c", "r" ]
desc = "Copy relative path (from git root) to clipboard"
run = "shell -- python3 -c \"import os,sys,subprocess; root=subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=os.path.dirname(sys.argv[1]),stderr=subprocess.DEVNULL).decode().strip(); print(os.path.relpath(sys.argv[1],root),end='')\" \"$0\" | pbcopy"

[[mgr.prepend_keymap]]
on = [ "s" ]
desc = "Search file contents (grep via ripgrep)"
run = "search --via=rg"
TOML
  info "Patched keymap.toml"
fi

# ─── patch vimrc ──────────────────────────────────────────────────────
VIMRC="$HOME/.vimrc"
VIMRC_MARKER="\" [a-terminal] patched"

if [ -f "$VIMRC" ] && grep -qF "$VIMRC_MARKER" "$VIMRC" 2>/dev/null; then
  info ".vimrc already patched"
else
  cat > "$VIMRC" <<'VIM'
" [a-terminal] patched
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

# ─── 4. install aterminal command ─────────────────────────────────────
echo ""
echo "── aterminal command ──────────────────────────────"

ATERMINAL_BIN="$HOME/.local/bin/aterminal"
ATERMINAL_MARKER="# [a-terminal]"

if [ -f "$ATERMINAL_BIN" ] && grep -qF "$ATERMINAL_MARKER" "$ATERMINAL_BIN" 2>/dev/null; then
  info "aterminal command already installed"
else
  mkdir -p "$HOME/.local/bin"
  cat > "$ATERMINAL_BIN" <<'SCRIPT'
#!/usr/bin/env bash
# [a-terminal]
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: aterminal <session-name>"
  exit 1
fi

SESSION_NAME="$(whoami)-$1"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  tmux attach-session -t "$SESSION_NAME"
else
  tmux new-session -d -s "$SESSION_NAME" 'zellij'
  tmux attach-session -t "$SESSION_NAME"
fi
SCRIPT
  chmod +x "$ATERMINAL_BIN"
  info "Installed aterminal command to $ATERMINAL_BIN"
fi

# ensure ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
  warn "Add ~/.local/bin to your PATH. For example:"
  warn "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
fi

# ─── done ─────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo "  Setup complete!"
echo "  • Run 'aterminal <name>' to start a tmux+zellij session"
echo "  • Run 'yazi' to browse files"
echo "  • Run 'claude' to start Claude Code"
echo "══════════════════════════════════════════════════"
echo ""
