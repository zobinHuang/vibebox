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

# ─── install via conda (into dedicated vibebox env) ─────────────────
VBOX_ENV="vibebox"
install_pkg() {
  if ! command -v conda &>/dev/null; then
    err "conda not found. Please install Miniconda or Anaconda first."
    return 1
  fi
  # create env if it doesn't exist
  if ! conda env list | grep -q "^${VBOX_ENV} "; then
    conda create -y -n "$VBOX_ENV" -c conda-forge --no-default-packages < /dev/null 2>/dev/null
  fi
  conda install -y -n "$VBOX_ENV" -c conda-forge "$@" < /dev/null
}

# helper: resolve path to a binary installed in the vibebox env
vbox_bin() {
  local CONDA_PREFIX
  CONDA_PREFIX="$(conda info --envs 2>/dev/null | grep "^${VBOX_ENV} " | awk '{print $NF}')"
  echo "${CONDA_PREFIX}/bin/$1"
}

# ──────────────────────────────────────────────────────────────────────
COMMIT_HASH="$(curl -fsSL https://api.github.com/repos/zobinHuang/vibebox/commits/main 2>/dev/null | grep -m1 '"sha"' | cut -d'"' -f4 | cut -c1-7)" || true
COMMIT_HASH="${COMMIT_HASH:-unknown}"

echo ""
echo "══════════════════════════════════════════════════"
echo "  VibeBox Setup"
echo "  commit: $COMMIT_HASH"
echo "══════════════════════════════════════════════════"

# ─── 1. yazi ─────────────────────────────────────────────────────────
echo ""
echo "── Yazi ─────────────────────────────────────────"

if command -v yazi &>/dev/null; then
  info "Yazi already installed ($(yazi --version))"
elif [ -x "$(vbox_bin yazi)" ]; then
  info "Yazi already installed in vibebox env"
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

mkdir -p "$YAZI_DIR"

# create no-op previewer plugin for PDFs
NOOP_PLUGIN_DIR="$YAZI_DIR/plugins/noop.yazi"
mkdir -p "$NOOP_PLUGIN_DIR"
printf '%s\n' \
  'local M = {}' \
  'function M:peek() end' \
  'function M:seek() end' \
  'return M' > "$NOOP_PLUGIN_DIR/init.lua"

cat > "$YAZI_CONFIG" <<'TOML'
[mgr]
show_hidden = true
ratio = [0, 1, 3]

[plugin]
prepend_previewers = [
  { mime = "application/pdf", run = "noop" },
  { mime = "image/*",         run = "noop" },
]
TOML
info "Patched yazi.toml (disabled PDF/image preview)"

cat > "$YAZI_KEYMAP" <<'TOML'
[[mgr.prepend_keymap]]
on = [ "c", "r" ]
desc = "Copy relative path (from git root) to clipboard"
run = "shell -- bash -c 'ROOT=$(git -C \"$(dirname \"$1\")\" rev-parse --show-toplevel 2>/dev/null) && REL=$(python3 -c \"import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))\" \"$1\" \"$ROOT\") && printf \"%s\" \"$REL\" | $HOME/.local/bin/osc52-copy' _ \"$0\""

[[mgr.prepend_keymap]]
on = [ "s" ]
desc = "Search file contents (grep via ripgrep)"
run = "search --via=rg"
TOML
info "Patched keymap.toml"

# ─── patch tmux config ────────────────────────────────────────────────
TMUX_CONF="$HOME/.tmux.conf"

cat > "$TMUX_CONF" <<'TMUX'
# [vibebox] patched

# ─── clipboard & input ───────────────────────────────────────────────
set -g default-terminal "xterm-256color"
set -g set-clipboard on
set -g allow-passthrough on
set -g mouse on
set -g mode-keys vi

# ─── mouse/vi copy → pipe through osc52-copy for clipboard ──────────
bind -T copy-mode-vi MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "~/.local/bin/osc52-copy"
bind -T copy-mode MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "~/.local/bin/osc52-copy"
bind -T copy-mode-vi y send-keys -X copy-pipe-and-cancel "~/.local/bin/osc52-copy"

# ─── smart navigation: Alt + arrows (pane first, then tab) ──────────
bind -n M-Left if-shell -F "#{pane_at_left}" "previous-window" "select-pane -L"
bind -n M-Right if-shell -F "#{pane_at_right}" "next-window" "select-pane -R"
bind -n M-Up if-shell -F "#{pane_at_top}" "" "select-pane -U"
bind -n M-Down if-shell -F "#{pane_at_bottom}" "" "select-pane -D"

# ─── tab mode: Ctrl+t → action ───────────────────────────────────────
bind -n C-t switch-client -T tab_mode
bind -T tab_mode n new-window -c "#{pane_current_path}"
bind -T tab_mode r command-prompt -I "#W" "rename-window '%%'"
bind -T tab_mode x kill-window
bind -T tab_mode Left previous-window
bind -T tab_mode Right next-window
bind -T tab_mode h previous-window
bind -T tab_mode l next-window
bind -T tab_mode 1 select-window -t 1
bind -T tab_mode 2 select-window -t 2
bind -T tab_mode 3 select-window -t 3
bind -T tab_mode 4 select-window -t 4
bind -T tab_mode 5 select-window -t 5
bind -T tab_mode 6 select-window -t 6
bind -T tab_mode 7 select-window -t 7
bind -T tab_mode 8 select-window -t 8
bind -T tab_mode 9 select-window -t 9

# ─── pane mode: Ctrl+p → action ─────────────────────────────────────
bind -n C-p switch-client -T pane_mode
bind -T pane_mode d split-window -v -c "#{pane_current_path}"
bind -T pane_mode n split-window -h -c "#{pane_current_path}"
bind -T pane_mode x kill-pane
bind -T pane_mode Left select-pane -L
bind -T pane_mode Right select-pane -R
bind -T pane_mode Up select-pane -U
bind -T pane_mode Down select-pane -D
bind -T pane_mode h select-pane -L
bind -T pane_mode j select-pane -D
bind -T pane_mode k select-pane -U
bind -T pane_mode l select-pane -R
bind -T pane_mode z resize-pane -Z

# ─── resize mode: Ctrl+n → h/j/k/l (repeatable) ────────────────────
bind -n C-n switch-client -T resize_mode
bind -r -T resize_mode h resize-pane -L 2
bind -r -T resize_mode j resize-pane -D 2
bind -r -T resize_mode k resize-pane -U 2
bind -r -T resize_mode l resize-pane -R 2
bind -r -T resize_mode Left resize-pane -L 2
bind -r -T resize_mode Right resize-pane -R 2
bind -r -T resize_mode Up resize-pane -U 2
bind -r -T resize_mode Down resize-pane -D 2

# ─── keep custom tab names ───────────────────────────────────────────
setw -g automatic-rename off
set -g allow-rename off

# ─── windows start at 1 (not 0) ─────────────────────────────────────
set -g base-index 1
setw -g pane-base-index 1
set -g renumber-windows on

# ─── status bar (tab bar) ────────────────────────────────────────────
set -g status-position bottom
set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
set -g status-left-length 45
set -g status-left "#[bg=#cba6f7,fg=#1e1e2e,bold] VibeBox #[default] #[bg=#89b4fa,fg=#1e1e2e,bold] ◆ #S #[default] "
set -g status-right-length 65
set -g status-right "#[fg=#a6e3a1]running #(NOW=$(date +%%s); C=#{session_created}; E=$((NOW-C)); D=$((E/86400)); H=$(((E%%86400)/3600)); M=$(((E%%3600)/60)); S=$((E%%60)); [ $D -gt 0 ] && printf '%%dd %%dh %%dm' $D $H $M || { [ $H -gt 0 ] && printf '%%dh %%dm %%ds' $H $M $S || printf '%%dm %%ds' $M $S; }) #[fg=#a6adc8]│ %Y-%m-%d %H:%M "
set -g status-interval 1
setw -g window-status-format "#[fg=#a6adc8] #I:#W "
setw -g window-status-current-format "#[bg=#45475a,fg=#89b4fa,bold] ▸ #I:#W "
setw -g window-status-separator ""

# ─── pane borders ────────────────────────────────────────────────────
set -g pane-border-style "fg=#45475a"
set -g pane-active-border-style "fg=#89b4fa"
TMUX
info "Patched .tmux.conf (tabs, panes, Alt keybindings, status bar)"

# ─── install OSC 52 clipboard helper ─────────────────────────────────
OSC52_BIN="$HOME/.local/bin/osc52-copy"
printf '%s\n' '#!/usr/bin/env bash' \
  'data=$(base64 | tr -d '\''\n'\'')' \
  '# try tmux pane TTY first (works inside zellij+tmux)' \
  'PANE_TTY=$(tmux display-message -p "#{pane_tty}" 2>/dev/null || true)' \
  'if [ -n "$PANE_TTY" ] && [ -e "$PANE_TTY" ]; then' \
  '  printf '\''\033]52;c;%s\a'\'' "$data" > "$PANE_TTY"' \
  'else' \
  '  printf '\''\033]52;c;%s\a'\'' "$data" > /dev/tty' \
  'fi' > "$OSC52_BIN"
chmod +x "$OSC52_BIN"
info "Installed osc52-copy helper"

# ─── patch vimrc ──────────────────────────────────────────────────────
VIMRC="$HOME/.vimrc"

cat > "$VIMRC" <<'VIM'
" [vibebox] patched
syntax on
set number
VIM
info "Patched .vimrc (line numbers + syntax highlighting)"

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
  '  echo "  vbox kill <name>          Kill a session by name"' \
  '  echo "  vbox exit                 Kill current session"' \
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
  '    NOW=$(date +%s)' \
  '    while IFS="|" read -r SNAME CREATED; do' \
  '      if [[ "$SNAME" == "$PREFIX"* ]]; then' \
  '        SHORT="${SNAME#$PREFIX}"' \
  '        ELAPSED=$(( NOW - CREATED ))' \
  '        DAYS=$(( ELAPSED / 86400 ))' \
  '        HOURS=$(( (ELAPSED % 86400) / 3600 ))' \
  '        MINS=$(( (ELAPSED % 3600) / 60 ))' \
  '        if [ "$DAYS" -gt 0 ]; then' \
  '          DUR="${DAYS}d ${HOURS}h"' \
  '        elif [ "$HOURS" -gt 0 ]; then' \
  '          DUR="${HOURS}h ${MINS}m"' \
  '        else' \
  '          DUR="${MINS}m"' \
  '        fi' \
  '        CREATED_FMT=$(date -d "@$CREATED" "+%Y-%m-%d %H:%M" 2>/dev/null || date -r "$CREATED" "+%Y-%m-%d %H:%M" 2>/dev/null || echo "unknown")' \
  '        printf "  %-20s created: %s  uptime: %s\n" "$SHORT" "$CREATED_FMT" "$DUR"' \
  '        FOUND=1' \
  '      fi' \
  '    done < <(tmux list-sessions -F "#{session_name}|#{session_created}" 2>/dev/null || true)' \
  '    if [ "$FOUND" -eq 0 ]; then' \
  '      echo "No vbox sessions."' \
  '    fi' \
  '    ;;' \
  '  kill)' \
  '    if [ $# -lt 2 ]; then' \
  '      echo "Usage: vbox kill <session-name>"' \
  '      exit 1' \
  '    fi' \
  '    SESSION_NAME="$(whoami)-$2"' \
  '    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then' \
  '      tmux kill-session -t "$SESSION_NAME"' \
  '      echo "Killed session: $2"' \
  '    else' \
  '      echo "Session '\''$2'\'' not found."' \
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
  '      tmux new-session -d -s "$SESSION_NAME" -c "$HOME"' \
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

# ensure ~/.local/bin and vibebox env bin are in PATH
VBOX_ENV_BIN="$(conda info --envs 2>/dev/null | grep "^${VBOX_ENV} " | awk '{print $NF}')/bin"
SHELL_RC="$HOME/.bashrc"
[ -n "${ZSH_VERSION:-}" ] && SHELL_RC="$HOME/.zshrc"

PATH_LINE="export PATH=\"\$HOME/.local/bin:${VBOX_ENV_BIN}:\$PATH\""
PATH_MARKER="# [vibebox] path"

if grep -qF "$PATH_MARKER" "$SHELL_RC" 2>/dev/null; then
  info "PATH already configured in $(basename "$SHELL_RC")"
else
  printf '\n%s\n%s\n' "$PATH_MARKER" "$PATH_LINE" >> "$SHELL_RC"
  info "Added vibebox PATH to $(basename "$SHELL_RC")"
  warn "Run 'source $SHELL_RC' or restart your shell"
fi

# ─── done ─────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Sessions:"
echo "    vbox new <name>       Create a new session"
echo "    vbox attach <name>    Attach to existing session"
echo "    vbox ls               List all sessions"
echo "    vbox kill <name>      Kill a session"
echo "    vbox exit             Kill current session"
echo ""
echo "  Tabs (Ctrl+t):          Panes (Ctrl+p):"
echo "    n   new tab              d   split down"
echo "    r   rename tab           n   split right"
echo "    ←/→ switch tab           ←/→/↑/↓ navigate"
echo "    x   close tab            x   close pane"
echo "                             z   toggle fullscreen"
echo ""
echo "  Resize (Ctrl+n):  h/j/k/l or arrows (repeatable)"
echo ""
echo "  Tools:"
echo "    yazi       file manager"
echo "    claude     Claude Code"
echo "══════════════════════════════════════════════════"
echo ""
