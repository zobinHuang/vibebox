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

# ─── fetch a file from this repo's raw API (avoids CDN cache) ────────
VBOX_REPO_API="https://api.github.com/repos/zobinHuang/vibebox/contents"
vbox_fetch() {
  # vbox_fetch <repo-path> <local-dest>
  # On failure, captures curl's stderr + http status into the global
  # VBOX_FETCH_ERR so the caller can surface it to the user.
  local src="$1" dest="$2" curl_err http_code curl_rc
  VBOX_FETCH_ERR=""
  curl_err="$(mktemp)"
  http_code="$(curl -sSL -w '%{http_code}' \
                -H 'Accept: application/vnd.github.v3.raw' \
                "${VBOX_REPO_API}/${src}" \
                -o "$dest" 2>"$curl_err")"
  curl_rc=$?
  if [ "$curl_rc" -ne 0 ]; then
    VBOX_FETCH_ERR="curl exit=$curl_rc: $(tr '\n' ' ' <"$curl_err")"
    rm -f "$curl_err"
    return 1
  fi
  rm -f "$curl_err"
  case "$http_code" in
    2??) return 0 ;;
    *)
      # On non-2xx the body was written to $dest — peek at it for the
      # actual GitHub error (rate-limit, 404, etc). Truncate so we don't
      # spam the terminal with a full HTML page.
      VBOX_FETCH_ERR="HTTP $http_code from ${VBOX_REPO_API}/${src} — $(head -c 200 "$dest" 2>/dev/null | tr '\n' ' ')"
      rm -f "$dest"
      return 1
      ;;
  esac
}

# When running `bash setup.sh` from a clone, prefer the local file. When
# running via `curl|bash`, BASH_SOURCE points to /dev/fd/N or main and the
# sibling check fails, so we fall back to fetching from GitHub.
VBOX_SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  VBOX_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
fi
VBOX_INSTALL_ERR=""

vbox_install_file() {
  # vbox_install_file <repo-path> <dest>
  # On failure, sets VBOX_INSTALL_ERR with a human-readable reason.
  local src="$1" dest="$2"
  VBOX_INSTALL_ERR=""
  if [ -n "$VBOX_SCRIPT_DIR" ] && [ -f "$VBOX_SCRIPT_DIR/$src" ]; then
    if ! cp "$VBOX_SCRIPT_DIR/$src" "$dest" 2>/dev/null; then
      VBOX_INSTALL_ERR="cp $VBOX_SCRIPT_DIR/$src -> $dest failed"
      return 1
    fi
    return 0
  fi
  if vbox_fetch "$src" "$dest"; then
    return 0
  fi
  VBOX_INSTALL_ERR="$VBOX_FETCH_ERR"
  return 1
}

# ──────────────────────────────────────────────────────────────────────
COMMIT_HASH="$(curl -fsSL https://api.github.com/repos/zobinHuang/vibebox/commits/main 2>/dev/null | grep -m1 '"sha"' | cut -d'"' -f4 | cut -c1-7)" || true
COMMIT_HASH="${COMMIT_HASH:-unknown}"

echo ""
echo "══════════════════════════════════════════════════"
echo "  VibeBox Setup"
echo "  commit: $COMMIT_HASH"
echo "══════════════════════════════════════════════════"

# ─── 1. tmux ─────────────────────────────────────────────────────────
echo ""
echo "── tmux ─────────────────────────────────────────"

if command -v tmux &>/dev/null; then
  info "tmux already installed ($(tmux -V))"
else
  err "tmux not found. Please install tmux first (e.g. sudo apt install tmux)"
fi

# ─── patch tmux config ────────────────────────────────────────────────
TMUX_CONF="$HOME/.tmux.conf"

cat > "$TMUX_CONF" <<'TMUX'
# [vibebox] patched

# ─── clipboard & input ───────────────────────────────────────────────
set -g default-terminal "xterm-256color"
set -g set-clipboard on
if-shell "tmux -V | awk '{if($2+0 >= 3.3) exit 0; else exit 1}'" "set -g allow-passthrough on" ""
set -g mouse on
set -g mode-keys vi

# ─── mouse/vi copy → pipe through osc52-copy for clipboard ──────────
bind -T copy-mode-vi MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "~/.local/bin/osc52-copy"
bind -T copy-mode MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "~/.local/bin/osc52-copy"
bind -T copy-mode-vi y send-keys -X copy-pipe-and-cancel "~/.local/bin/osc52-copy"

# ─── smart navigation: Alt + arrows (pane first, then the bar) ──────
# Left/right walk panes until you hit an edge, then keep going along the
# status bar. Once agents are running the bar has two rows, and they are
# walked as one sequence — the whole READY row, then the whole BUSY row —
# so stepping right off the end of the top row continues onto the bottom
# one. That is the order you read the bar in.
#
# Up/down stay pane-only. Vertical row-crossing was tried and dropped:
# with one tab per row every up/down press is a legitimate no-op, which
# is indistinguishable from a broken keybinding, and Alt+Up/Down is eaten
# outright by some terminals (VS Code binds it to move-line).
#
# With no agent running every tab sits in the ready row, so left/right
# behave exactly like previous-window/next-window did before the bar.
#
# -b is required, not an optimisation: run-shell without it blocks the
# server until the command returns, and vbox-agent calls back into tmux.
bind -n M-Left  if-shell -F "#{pane_at_left}"   'run-shell -b "$HOME/.local/bin/vbox-agent nav left #{window_id}"'  "select-pane -L"
bind -n M-Right if-shell -F "#{pane_at_right}"  'run-shell -b "$HOME/.local/bin/vbox-agent nav right #{window_id}"' "select-pane -R"
bind -n M-Up    if-shell -F "#{pane_at_top}"    "" "select-pane -U"
bind -n M-Down  if-shell -F "#{pane_at_bottom}" "" "select-pane -D"

# ─── tab mode: Ctrl+t → action ───────────────────────────────────────
bind -n C-t switch-client -T tab_mode
bind -T tab_mode n new-window -c "#{pane_current_path}"
bind -T tab_mode r command-prompt -I "#W" "rename-window '%%'"
bind -T tab_mode x kill-window
# Clear a tab stuck showing an agent state — an agent SIGKILLed with its
# pane left open never runs SessionEnd, and no tmux hook fires either.
bind -T tab_mode c run-shell -b "$HOME/.local/bin/vbox-agent clear #{window_id}"
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
# Nested-tmux passthrough: Ctrl+t Ctrl+t forwards Ctrl+t to the pane.
# Use case: `vbox` running locally with an inner `vbox` over SSH.
bind -T tab_mode C-t send-keys C-t
# Alt+arrows aren't chord triggers, so the outer always intercepts them.
# `Ctrl+t Alt+<dir>` forwards the chord to the pane for quick inner-side
# pane/window navigation. For longer stretches of inner-side work, use
# F12 to fully disable outer bindings (see below).
bind -T tab_mode M-Left  send-keys M-Left
bind -T tab_mode M-Right send-keys M-Right
bind -T tab_mode M-Up    send-keys M-Up
bind -T tab_mode M-Down  send-keys M-Down

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
# Nested passthrough — Ctrl+p Ctrl+p forwards Ctrl+p
bind -T pane_mode C-p send-keys C-p

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
# Nested passthrough — Ctrl+n Ctrl+n forwards Ctrl+n
bind -T resize_mode C-n send-keys C-n

# ─── F12: nested-tmux passthrough toggle ─────────────────────────────
# When you're inside an inner tmux (e.g. SSH'd into a remote vbox),
# the outer tmux intercepts Ctrl+t/p/n and Alt+arrows. F12 swaps the
# active key-table to "off" so every outer binding becomes inert and
# keystrokes pass straight through to the pane (= the inner tmux).
# Press F12 again to restore the outer's bindings. status-left shows
# `[PASSTHRU]` while in off-mode so you don't forget you're in it.
bind -T root F12 set key-table off \; refresh-client -S
bind -T off  F12 set -u key-table  \; refresh-client -S

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
set -g status-left-length 80
# One attribute per #[...] here, as in the agent rows below: a comma
# inside #[bg=x,fg=y] sits inside a #{?...}, and escaping it as "#," is
# only understood by newer tmux. Older builds split the conditional at
# that comma instead and print the rest of the style as literal text
# (seen on CentOS as `fg=#1e1e2e#,bold] PASSTHRU ,` in the bar).
#
# `#h` is tmux's short hostname (no domain). A bright yellow [PASSTHRU]
# block appears when F12 has switched the active key-table to "off"
# (nested-tmux passthrough mode).
set -g status-left "#[bg=#cba6f7,fg=#1e1e2e,bold] VibeBox [#h] #[default] #[bg=#89b4fa,fg=#1e1e2e,bold] ◆ #S #[default]#{?#{==:#{client_key_table},off}, #[bg=#f9e2af]#[fg=#1e1e2e]#[bold] PASSTHRU #[default],} "
set -g status-right-length 80
# Per-tab uptime (#(vbox-uptime)) + clock. vbox-uptime prints the elapsed
# time since the current window (tab) was created, falling back to session
# creation for windows that pre-date the install.
set -g status-right "#[fg=#a6e3a1]running #($HOME/.local/bin/vbox-uptime)#[default] #[fg=#a6adc8]│ %Y-%m-%d %H:%M "
set -g status-interval 1
# #{=14:...} is the plain trim, understood far back; the #{=/14/…:...}
# form that appends an ellipsis is newer and renders empty on old tmux.
setw -g window-status-format "#[fg=#a6adc8] #I:#{=14:window_name} "
setw -g window-status-current-format "#[bg=#45475a,fg=#89b4fa,bold] ▸ #I:#{=14:window_name} "
setw -g window-status-separator ""

# ─── agent bar: READY / BUSY rows ────────────────────────────────────
# Each window carries a @vbox-agent option (busy | wait | idle | unset)
# written by `vbox-agent`, which code-agent hooks call. The rows below
# filter the window list on it, entirely in tmux's format language — no
# #(), so the 1s redraw stays free of subprocesses (see commit 1f15349
# for what a second #() did to refresh behaviour).
#
# Two conventions keep these strings readable:
#   * one attribute per #[...] — a comma inside #[bg=x,fg=y] would be
#     read as a #{?...} argument separator and split the conditional.
#   * the row templates live here as user options; vbox-agent only ever
#     writes `status-format[N] = #{E:@vbox-row-<name>}` into a session,
#     so it never has to re-quote any of this.

# READY row items — anything not busy: waiting on you, idle, or a plain
# tab that has never run an agent (dim, no glyph).
set -g @vbox-ready-item "#{?#{==:#{@vbox-agent},busy},,#[range=window|#{window_index}]#{?#{==:#{@vbox-agent},wait},#[fg=#f9e2af] ! #I:#{=14:window_name} ,#{?#{==:#{@vbox-agent},idle},#[fg=#a6e3a1] ○ #I:#{=14:window_name} ,#[fg=#6c7086]   #I:#{=14:window_name} }}#[default]#[norange]}"
set -g @vbox-ready-cur  "#{?#{==:#{@vbox-agent},busy},,#[range=window|#{window_index}]#[list=focus]#[bg=#45475a]#[fg=#89b4fa]#[bold] ▸ #I:#{=14:window_name} #[default]#[norange]#[list=on]}"

# BUSY row items — agent currently working.
set -g @vbox-busy-item "#{?#{==:#{@vbox-agent},busy},#[range=window|#{window_index}]#[fg=#fab387] ● #I:#{=14:window_name} #[default]#[norange],}"
set -g @vbox-busy-cur  "#{?#{==:#{@vbox-agent},busy},#[range=window|#{window_index}]#[list=focus]#[bg=#45475a]#[fg=#fab387]#[bold] ▸ ● #I:#{=14:window_name} #[default]#[norange]#[list=on],}"

# Row templates. #{W:normal,current} loops the window list and picks the
# second format for the current window, so neither item format has to
# test for it.
set -g @vbox-row-ready "#[align=left]#[bg=#a6e3a1]#[fg=#1e1e2e]#[bold] READY #[default] #[list=on]#[list=left-marker]<#[list=right-marker]>#[list=on]#{W:#{E:@vbox-ready-item},#{E:@vbox-ready-cur}}#[nolist]"
set -g @vbox-row-busy  "#[align=left]#[bg=#fab387]#[fg=#1e1e2e]#[bold] BUSY  #[default] #[list=on]#[list=left-marker]<#[list=right-marker]>#[list=on]#{W:#{E:@vbox-busy-item},#{E:@vbox-busy-cur}}#[nolist]"
# Chrome row — the default status line minus the window list, which the
# agent rows have taken over. Always the bottom row, so the clock stays
# put as rows appear and disappear above it.
set -g @vbox-row-chrome "#[align=left range=left #{E:status-left-style}]#[push-default]#{T;=/#{status-left-length}:status-left}#[pop-default]#[norange default]#[align=right range=right #{E:status-right-style}]#[push-default]#{T;=/#{status-right-length}:status-right}#[pop-default]#[norange default]"

# ─── pane borders ────────────────────────────────────────────────────
set -g pane-border-style "fg=#45475a"
set -g pane-active-border-style "fg=#89b4fa"

# ─── per-tab uptime: stamp window creation time so vbox-uptime can show
#     "running 5m 30s" relative to when this tab opened, not when the
#     session opened. Uses run-shell + date because #{T:%s} is empty on
#     recent tmux builds (the strftime %s format isn't reliably compiled in).
set-hook -g after-new-window  'run-shell -b "tmux set-option -w -t #{window_id} @vbox-window-created \"$(date +%s)\""'
set-hook -g after-new-session 'run-shell -b "tmux set-option -w -t #{window_id} @vbox-window-created \"$(date +%s)\""'

# ─── agent bar: keep the row count in step with windows and panes ────
# These must come last. set-hook -g *replaces* a hook array, so an
# appended entry placed above the uptime stamps would be silently wiped
# by them; -ga below appends to what those two just set.
set-hook -ga after-new-window  'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
set-hook -ga after-new-session 'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
# -g, not -ga, for these: nothing else writes them, and sourcing this
# file again (which is exactly what a hot upgrade does) would otherwise
# append a duplicate every time, so each event would fire sync N times.
# The two above must stay -ga because the uptime stamps own index 0.
set-hook -g  session-created   'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
set-hook -g  client-attached   'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
set-hook -g  window-unlinked   'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
# A pane going away takes its agent with it; without these, a tab whose
# agent was killed mid-run would sit in BUSY forever. Both are needed and
# they don't overlap: a pane whose command exits fires pane-exited, while
# `Ctrl+p x` runs kill-pane, which fires only after-kill-pane.
set-hook -g  pane-exited       'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
set-hook -g  after-kill-pane   'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
set-hook -g  pane-died         'run-shell -b "$HOME/.local/bin/vbox-agent sync"'
TMUX
info "Patched .tmux.conf (tabs, panes, Alt keybindings, status bar)"

# ─── install OSC 52 clipboard helper ─────────────────────────────────
mkdir -p "$HOME/.local/bin"
OSC52_BIN="$HOME/.local/bin/osc52-copy"
printf '%s' '#!/usr/bin/env bash
data=$(base64 | tr -d '"'"'\n'"'"')
PANE_TTY=$(tmux display-message -p "#{pane_tty}" 2>/dev/null || true)
if [ -n "$PANE_TTY" ] && [ -e "$PANE_TTY" ]; then
  printf '"'"'\033]52;c;%s\a'"'"' "$data" > "$PANE_TTY"
else
  printf '"'"'\033]52;c;%s\a'"'"' "$data" > /dev/tty
fi
' > "$OSC52_BIN"
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

# ─── 3. helper scripts ───────────────────────────────────────────────
echo ""
echo "── helpers ───────────────────────────────────────"

mkdir -p "$HOME/.local/bin"

# Always overwrite so re-running setup.sh upgrades these
_vbox_install_or_fail() {
  # _vbox_install_or_fail <repo-path> <dest> <label>
  if vbox_install_file "$1" "$2"; then
    return 0
  fi
  err "Failed to install $3 — ${VBOX_INSTALL_ERR:-<no error captured>}"
  return 1
}

if _vbox_install_or_fail bin/vbox-uptime "$HOME/.local/bin/vbox-uptime" "vbox-uptime"; then
  chmod +x "$HOME/.local/bin/vbox-uptime"
  info "Installed vbox-uptime"
fi

if _vbox_install_or_fail bin/vbox-agent "$HOME/.local/bin/vbox-agent" "vbox-agent"; then
  chmod +x "$HOME/.local/bin/vbox-agent"
  info "Installed vbox-agent"
fi

# ─── 4. install vbox command ─────────────────────────────────────
echo ""
echo "── vbox command ──────────────────────────────"

VBOX_BIN="$HOME/.local/bin/vbox"

mkdir -p "$HOME/.local/bin"
cat > "$VBOX_BIN" <<'VBOX_SCRIPT'
#!/usr/bin/env bash
# [vibebox]
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  vbox new <session-name>     Create and attach to a new vbox session"
  echo "  vbox attach <name>          Attach to an existing session"
  echo "  vbox ls                     List all vbox sessions"
  echo "  vbox kill <name>            Kill a session by name"
  echo "  vbox exit                   Kill current session"
  exit 1
}

if [ $# -lt 1 ]; then
  usage
fi

CMD="$1"
shift

case "$CMD" in
  exit)
    if [ -n "${TMUX:-}" ]; then
      tmux kill-session
    else
      echo "Not inside a tmux session."
    fi
    ;;
  attach)
    if [ $# -lt 1 ]; then
      echo "Usage: vbox attach <session-name>"
      exit 1
    fi
    SESSION_NAME="$(whoami)-$1"
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      tmux attach-session -t "$SESSION_NAME"
    else
      echo "Session '$SESSION_NAME' not found."
      echo "Available sessions:"
      tmux list-sessions 2>/dev/null || echo "  (none)"
      exit 1
    fi
    ;;
  ls)
    PREFIX="$(whoami)-"
    FOUND=0
    NOW=$(date +%s)
    while IFS="|" read -r SNAME CREATED; do
      if [[ "$SNAME" == "$PREFIX"* ]]; then
        SHORT="${SNAME#$PREFIX}"
        ELAPSED=$(( NOW - CREATED ))
        DAYS=$(( ELAPSED / 86400 ))
        HOURS=$(( (ELAPSED % 86400) / 3600 ))
        MINS=$(( (ELAPSED % 3600) / 60 ))
        if [ "$DAYS" -gt 0 ]; then
          DUR="${DAYS}d ${HOURS}h"
        elif [ "$HOURS" -gt 0 ]; then
          DUR="${HOURS}h ${MINS}m"
        else
          DUR="${MINS}m"
        fi
        CREATED_FMT=$(date -d "@$CREATED" "+%Y-%m-%d %H:%M" 2>/dev/null || date -r "$CREATED" "+%Y-%m-%d %H:%M" 2>/dev/null || echo "unknown")
        printf "  %-20s created: %s  uptime: %s\n" "$SHORT" "$CREATED_FMT" "$DUR"
        FOUND=1
      fi
    done < <(tmux list-sessions -F "#{session_name}|#{session_created}" 2>/dev/null || true)
    if [ "$FOUND" -eq 0 ]; then
      echo "No vbox sessions."
    fi
    ;;
  kill)
    if [ $# -lt 1 ]; then
      echo "Usage: vbox kill <session-name>"
      exit 1
    fi
    SESSION_NAME="$(whoami)-$1"
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      tmux kill-session -t "$SESSION_NAME"
      echo "Killed session: $1"
    else
      echo "Session '$1' not found."
    fi
    ;;
  new)
    NAME=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --) shift; NAME="${1:-}"; break ;;
        -*) echo "vbox new: unknown flag $1" >&2; exit 1 ;;
        *)  NAME="$1"; shift ;;
      esac
    done
    if [ -z "$NAME" ]; then
      echo "Usage: vbox new <session-name>"
      exit 1
    fi
    SESSION_NAME="$(whoami)-$NAME"
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "Session '$SESSION_NAME' already exists. Use 'vbox attach $NAME' instead."
      exit 1
    fi
    tmux new-session -d -s "$SESSION_NAME" -c "$HOME"
    tmux attach-session -t "$SESSION_NAME"
    ;;
  -h|--help) usage ;;
  *)         usage ;;
esac
VBOX_SCRIPT
chmod +x "$VBOX_BIN"
info "Installed vbox command to $VBOX_BIN"

# ensure ~/.local/bin is in PATH
SHELL_RC="$HOME/.bashrc"
[ -n "${ZSH_VERSION:-}" ] && SHELL_RC="$HOME/.zshrc"
PATH_MARKER="# [vibebox] path"

# remove old vibebox path line if exists, then write fresh.
# Brackets in PATH_MARKER are regex metacharacters; literal-match them.
sed -i.bak '/# \[vibebox\] path/,+1d' "$SHELL_RC" 2>/dev/null || true
rm -f "${SHELL_RC}.bak"

PATH_LINE="export PATH=\"\$HOME/.local/bin:\$PATH\""
printf '\n%s\n%s\n' "$PATH_MARKER" "$PATH_LINE" >> "$SHELL_RC"
info "Added vibebox PATH to $(basename "$SHELL_RC")"
warn "Run 'source $SHELL_RC' or restart your shell"

# Strip any vibe-music shell-hook line left by older installs (idempotent).
for RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
  [ -f "$RC" ] || continue
  # Brackets in the marker are regex metacharacters; literal-match them.
  sed -i.bak '/# \[vibebox\] vibe-hooks/,+1d' "$RC" 2>/dev/null || true
  rm -f "${RC}.bak"
done

# ─── 5. code-agent hooks ─────────────────────────────────────────────
echo ""
echo "── code-agent hooks ──────────────────────────────"

CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CODEX_HOOKS="${CODEX_HOME:-$HOME/.codex}/hooks.json"
VBOX_CODEX_NEEDS_TRUST=0

# Quoted heredoc on purpose: $HOME and $TMUX_PANE must survive into the
# settings file and be expanded by the hook's own shell at fire time, not
# baked in here.
#
# Event choices worth recording:
#   * UserPromptSubmit is the primary busy signal. PreToolUse alone
#     leaves a gap between submitting a prompt and the first tool call,
#     during which the tab would still read as idle.
#   * PostToolUse restores busy after you approve a permission prompt,
#     which is what clears the `wait` state.
#   * Notification's matcher matches the notification *type*, so it is
#     scoped to the ones that actually block on you. Plain idle_prompt is
#     deliberately excluded — it fires just because you stepped away, and
#     would eventually paint every idle tab yellow.
#   * StopFailure matters: a turn killed by an API error never fires
#     Stop, so without it a rate-limited tab sits in BUSY forever.
#   * SubagentStop is deliberately absent — a subagent finishing does not
#     mean the main agent stopped working. (The old vibe-music config got
#     this wrong.)
#   * Stop / UserPromptSubmit / PostToolBatch take no matcher at all, so
#     none is set; the ".*" in the old config was silently ignored.
#   * The two tool hooks are async so they add no latency to every single
#     tool call — measured at ~18ms each otherwise, and they fire twice
#     per call. The turn-boundary hooks stay synchronous, where ordering
#     against each other is what actually matters.
VBOX_CLAUDE_HOOKS="$(cat <<'JSON'
{
  "SessionStart":     [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" idle" } ] } ],
  "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" busy" } ] } ],
  "PreToolUse":       [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" busy", "async": true } ] } ],
  "PostToolUse":      [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" busy", "async": true } ] } ],
  "Notification":     [ { "matcher": "permission_prompt|elicitation_dialog",
                          "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" wait" } ] } ],
  "Stop":             [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" idle" } ] } ],
  "StopFailure":      [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" idle" } ] } ],
  "SessionEnd":       [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" none" } ] } ]
}
JSON
)"

# Codex 0.132+ has its own hook engine, and its hooks.json uses the same
# shape as Claude's "hooks" key, so the same merge works on both files.
# The event set differs though:
#   * PermissionRequest replaces Claude's Notification as the "blocked on
#     you" signal — it is the event codex fires when a tool call needs a
#     decision, which is exactly what `wait` means.
#   * No StopFailure — codex has no such event, so a turn killed by an API
#     error is caught by the SessionEnd / pane-exit paths instead.
#   * No "async": codex 0.132 prints `skipping async hook ... async hooks
#     are not supported yet` and drops the hook entirely, so the tool
#     hooks here are synchronous even though the Claude ones aren't.
VBOX_CODEX_HOOKS="$(cat <<'JSON'
{
  "SessionStart":      [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" idle" } ] } ],
  "UserPromptSubmit":  [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" busy" } ] } ],
  "PreToolUse":        [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" busy" } ] } ],
  "PostToolUse":       [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" busy" } ] } ],
  "PermissionRequest": [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" wait" } ] } ],
  "Stop":              [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" idle" } ] } ],
  "SessionEnd":        [ { "hooks": [ { "type": "command", "command": "$HOME/.local/bin/vbox-agent set \"$TMUX_PANE\" none" } ] } ]
}
JSON
)"

# Strip every hook we have ever owned, then re-add — that keeps re-runs
# idempotent and cleans up the dead `vbox-music` hooks left behind by
# installs that predate its removal. Matching on the command text is the
# marker; nothing else in the file is touched.
vbox_merge_hooks() {
  local file="$1" add="$2" tmp rc
  mkdir -p "$(dirname "$file")"
  [ -f "$file" ] || printf '{}\n' > "$file"
  tmp="$(mktemp)" || return 3

  if command -v jq >/dev/null 2>&1; then
    jq --argjson add "$add" '
      def strip:
        with_entries(
          .value |= ( map(.hooks |= map(select((.command // "") | test("vbox-(agent|music)") | not)))
                    | map(select((.hooks | length) > 0)) )
        )
        | with_entries(select((.value | length) > 0));
      .hooks = ((.hooks // {}) | strip)
      | reduce ($add | to_entries)[] as $e (.; .hooks[$e.key] = ((.hooks[$e.key] // []) + $e.value))
    ' "$file" > "$tmp" 2>/dev/null
    rc=$?
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$file" "$add" "$tmp" <<'PY'
import json, re, sys
src, add, dest = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
with open(src) as f:
    doc = json.load(f)
owned = re.compile(r"vbox-(agent|music)")
kept = {}
for event, groups in (doc.get("hooks") or {}).items():
    survivors = []
    for group in groups:
        handlers = [h for h in group.get("hooks", []) if not owned.search(h.get("command", ""))]
        if handlers:
            group = dict(group, hooks=handlers)
            survivors.append(group)
    if survivors:
        kept[event] = survivors
for event, groups in add.items():
    kept[event] = kept.get(event, []) + groups
doc["hooks"] = kept
with open(dest, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PY
    rc=$?
  else
    rm -f "$tmp"
    return 2
  fi

  # No backup copy is kept. The merge is already all-or-nothing: it is
  # built in a temp file and only swapped in once it has been produced
  # cleanly and is non-empty, so a failed or malformed merge leaves the
  # original in place untouched rather than needing to be restored.
  if [ "$rc" -ne 0 ] || [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    return 1
  fi
  # mktemp is 0600; settings.json holds credentials-adjacent config, so
  # keep it that way rather than inheriting the umask.
  chmod 600 "$tmp" 2>/dev/null || true
  mv "$tmp" "$file"
  return 0
}

if command -v jq >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
  if vbox_merge_hooks "$CLAUDE_SETTINGS" "$VBOX_CLAUDE_HOOKS"; then
    info "Merged Claude Code hooks into $CLAUDE_SETTINGS"
  else
    err "Could not merge Claude Code hooks — $CLAUDE_SETTINGS is not valid JSON. Left it untouched."
  fi

  # Only wire up codex if it is actually installed, rather than creating
  # config for a tool that isn't there.
  if command -v codex >/dev/null 2>&1; then
    if vbox_merge_hooks "$CODEX_HOOKS" "$VBOX_CODEX_HOOKS"; then
      info "Merged Codex hooks into $CODEX_HOOKS"
      VBOX_CODEX_NEEDS_TRUST=1
    else
      err "Could not merge Codex hooks — $CODEX_HOOKS is not valid JSON. Left it untouched."
    fi
  fi
else
  warn "Neither jq nor python3 found; skipped code-agent hook setup."
  warn "Add the \"hooks\" entries from the vibebox README to $CLAUDE_SETTINGS by hand."
fi

# ─── 6. hot reload ───────────────────────────────────────────────────
echo ""
echo "── reload ────────────────────────────────────────"

# Everything under ~/.local/bin re-executes from scratch on every call,
# so those are live the moment they're written. The tmux config is the
# exception: a running server holds its own copy of every binding,
# format and hook, so sessions open right now would keep the old ones
# until they were restarted. Push the new config in instead.
#
# Repeating this is safe. Every line in the config replaces rather than
# appends — which is exactly why the sync hooks use `set-hook -g`; with
# `-ga` each re-source would stack another copy and every event would
# fire sync N times. The agent rows survive too, because status-format
# holds an indirection (`#{E:@vbox-row-...}`) rather than the template
# itself, so live sessions pick up new row rendering for free.
#
# Code-agent hooks need nothing here: both Claude Code and codex re-read
# their hook config mid-session (codex asks you to re-trust it first).
if command -v tmux >/dev/null 2>&1 && tmux list-sessions >/dev/null 2>&1; then
  VBOX_LIVE="$(tmux list-sessions 2>/dev/null | wc -l | tr -d ' ')"
  if tmux source-file "$TMUX_CONF" 2>/dev/null; then
    "$HOME/.local/bin/vbox-agent" sync >/dev/null 2>&1 || true
    info "Reloaded into $VBOX_LIVE running session(s) — no restart needed"
  else
    warn "Could not reload the running tmux server."
    warn "Run this by hand to finish the upgrade: tmux source-file ~/.tmux.conf"
  fi
else
  info "No running tmux session to reload"
fi

# ─── done ─────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Sessions:"
echo "    vbox new <name>          Create a new session"
echo "    vbox attach <name>       Attach to existing session"
echo "    vbox ls                  List all sessions"
echo "    vbox kill <name>         Kill a session"
echo "    vbox exit                Kill current session"
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
echo "  Alt+left/right:   panes first, then along the status bar,"
echo "                    walking the READY row then the BUSY row."
echo "  Alt+up/down:      panes only."
echo ""
if [ "$VBOX_CODEX_NEEDS_TRUST" -eq 1 ]; then
  echo ""
  echo "  ─────────────────────────────────────────────"
  echo "  ONE MANUAL STEP for codex:"
  echo "    Codex will not run a newly written hook until you have"
  echo "    reviewed it. Open codex and run  /hooks  to trust them,"
  echo "    or the BUSY row will stay empty for codex tabs."
  echo "  ─────────────────────────────────────────────"
fi
echo "══════════════════════════════════════════════════"
echo ""
