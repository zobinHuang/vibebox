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

VBOXD_HOST="0.0.0.0"
VBOXD_PORT="8787"
VBOXD_DATA_ROOT="$HOME/.local/share/vboxd/data"
VBOXD_TLS_CERT=""
VBOXD_TLS_KEY=""
VBOXD_DO_SERVICE=1

usage() {
  cat >&2 <<'EOF'
Usage: server/setup.sh [options]

  --host <addr>        address to listen on (default 0.0.0.0)
  --port <port>        port to listen on (default 8787)
  --data-root <dir>    where transcripts are stored
  --tls-cert <file>    serve HTTPS with this certificate chain
  --tls-key <file>     private key matching --tls-cert
  --no-service         install and configure only; don't start anything
EOF
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)       VBOXD_HOST="${2:-}"; shift 2 ;;
    --port)       VBOXD_PORT="${2:-}"; shift 2 ;;
    --data-root)  VBOXD_DATA_ROOT="${2:-}"; shift 2 ;;
    --tls-cert)   VBOXD_TLS_CERT="${2:-}"; shift 2 ;;
    --tls-key)    VBOXD_TLS_KEY="${2:-}"; shift 2 ;;
    --no-service) VBOXD_DO_SERVICE=0; shift ;;
    -h|--help)    usage ;;
    *)            err "unknown option: $1"; usage ;;
  esac
done

if { [ -n "$VBOXD_TLS_CERT" ] && [ -z "$VBOXD_TLS_KEY" ]; } ||
   { [ -z "$VBOXD_TLS_CERT" ] && [ -n "$VBOXD_TLS_KEY" ]; }; then
  err "--tls-cert and --tls-key must be given together"
  exit 1
fi

# ─── fetch a file from this repo's raw API (avoids CDN cache) ────────
VBOX_REPO_API="https://api.github.com/repos/zobinHuang/vibebox/contents"
VBOXD_SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  VBOXD_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
fi

vboxd_install_file() {
  # vboxd_install_file <repo-path> <dest>
  local src="$1" dest="$2" http_code
  if [ -n "$VBOXD_SCRIPT_DIR" ] && [ -f "$VBOXD_SCRIPT_DIR/../$src" ]; then
    cp "$VBOXD_SCRIPT_DIR/../$src" "$dest"
    return 0
  fi
  http_code="$(curl -sSL -w '%{http_code}' \
                -H 'Accept: application/vnd.github.v3.raw' \
                "${VBOX_REPO_API}/${src}" -o "$dest" 2>/dev/null)" || return 1
  case "$http_code" in
    2??) return 0 ;;
    *) rm -f "$dest"; err "HTTP $http_code fetching $src"; return 1 ;;
  esac
}

echo ""
echo "══════════════════════════════════════════════════"
echo "  VibeBox session server"
echo "══════════════════════════════════════════════════"

# ─── 1. python ───────────────────────────────────────────────────────
echo ""
echo "── python ────────────────────────────────────────"

if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found. Install Python 3.10 or newer first."
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  err "python3 is $(python3 -V 2>&1), but 3.10 or newer is required."
  exit 1
fi
info "python3 $(python3 -VV | head -1 | cut -d' ' -f2)"

# ─── 2. install the package ──────────────────────────────────────────
echo ""
echo "── install ───────────────────────────────────────"

VBOXD_APP_DIR="$HOME/.local/share/vboxd/app"
VBOXD_CONFIG="$HOME/.config/vboxd/config.json"
VBOXD_BIN="$HOME/.local/bin/vboxd"

mkdir -p "$VBOXD_APP_DIR/vboxd" "$HOME/.local/bin"
for MODULE in __init__.py __main__.py config.py errors.py http.py store.py; do
  if ! vboxd_install_file "server/vboxd/$MODULE" "$VBOXD_APP_DIR/vboxd/$MODULE"; then
    err "Failed to install vboxd/$MODULE"
    exit 1
  fi
done
# Stale bytecode from a previous version shadows the modules we just wrote.
rm -rf "$VBOXD_APP_DIR/vboxd/__pycache__"
info "Installed vboxd to $VBOXD_APP_DIR"

cat > "$VBOXD_BIN" <<LAUNCHER
#!/bin/sh
# [vibebox]
PYTHONPATH="$VBOXD_APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" \\
  exec python3 -m vboxd --config "$VBOXD_CONFIG" "\$@"
LAUNCHER
chmod +x "$VBOXD_BIN"
info "Installed vboxd command to $VBOXD_BIN"

# ─── 3. configure ────────────────────────────────────────────────────
echo ""
echo "── configure ─────────────────────────────────────"

# `init` keeps the token of an existing config, so re-running this script
# upgrades a server without invalidating the clients already connected to it.
VBOXD_INIT_ARGS=(init --host "$VBOXD_HOST" --port "$VBOXD_PORT" --data-root "$VBOXD_DATA_ROOT")
if [ -n "$VBOXD_TLS_CERT" ]; then
  VBOXD_INIT_ARGS+=(--tls-cert "$VBOXD_TLS_CERT" --tls-key "$VBOXD_TLS_KEY")
fi
"$VBOXD_BIN" "${VBOXD_INIT_ARGS[@]}" >/dev/null
VBOXD_TOKEN="$("$VBOXD_BIN" token)"
info "Config at $VBOXD_CONFIG, data under $VBOXD_DATA_ROOT"

# ─── 4. service ──────────────────────────────────────────────────────
echo ""
echo "── service ───────────────────────────────────────"

VBOXD_STARTED=0
if [ "$VBOXD_DO_SERVICE" -eq 0 ]; then
  info "Skipped starting the service (--no-service)"
elif command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  VBOXD_UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$VBOXD_UNIT_DIR"
  cat > "$VBOXD_UNIT_DIR/vboxd.service" <<UNIT
[Unit]
Description=VibeBox session server
After=network-online.target

[Service]
ExecStart=$VBOXD_BIN serve
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now vboxd.service
  # Without lingering, a user unit dies the moment you log out.
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 ||
    warn "Could not enable lingering; vboxd will stop when you log out."
  info "Started systemd user unit vboxd.service"
  VBOXD_STARTED=1
elif [ "$(uname -s)" = "Darwin" ]; then
  VBOXD_PLIST="$HOME/Library/LaunchAgents/com.vibebox.vboxd.plist"
  mkdir -p "$(dirname "$VBOXD_PLIST")" "$HOME/.local/share/vboxd"
  cat > "$VBOXD_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.vibebox.vboxd</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VBOXD_BIN</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.local/share/vboxd/vboxd.log</string>
  <key>StandardErrorPath</key><string>$HOME/.local/share/vboxd/vboxd.log</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/com.vibebox.vboxd" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$VBOXD_PLIST"
  info "Started launchd agent com.vibebox.vboxd"
  VBOXD_STARTED=1
else
  warn "No systemd or launchd found. Start the server by hand:"
  warn "  nohup $VBOXD_BIN serve > ~/.local/share/vboxd/vboxd.log 2>&1 &"
fi

# ─── 5. verify ───────────────────────────────────────────────────────
if [ "$VBOXD_STARTED" -eq 1 ]; then
  echo ""
  echo "── verify ────────────────────────────────────────"
  VBOXD_SCHEME="http"
  [ -n "$VBOXD_TLS_CERT" ] && VBOXD_SCHEME="https"
  VBOXD_PROBE_OK=0
  for _ in 1 2 3 4 5; do
    if curl -fsSk "$VBOXD_SCHEME://127.0.0.1:$VBOXD_PORT/v1/healthz" >/dev/null 2>&1; then
      VBOXD_PROBE_OK=1
      break
    fi
    sleep 1
  done
  if [ "$VBOXD_PROBE_OK" -eq 1 ]; then
    info "Health check passed on $VBOXD_SCHEME://127.0.0.1:$VBOXD_PORT"
  else
    err "Health check failed. Logs: ~/.local/share/vboxd/vboxd.log or journalctl --user -u vboxd"
  fi
fi

# ─── done ─────────────────────────────────────────────────────────────
VBOXD_ADDR="$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo '<host>')"
echo ""
echo "══════════════════════════════════════════════════"
echo "  Server ready"
echo ""
echo "  Run this on each client:"
echo ""
echo "    vbox connect $VBOXD_ADDR $VBOXD_PORT --token $VBOXD_TOKEN"
echo ""
if [ -z "$VBOXD_TLS_CERT" ]; then
  echo "  This server speaks plain HTTP. Transcripts carry your source code,"
  echo "  paths and anything an agent printed — only expose it on a trusted"
  echo "  network, or re-run with --tls-cert/--tls-key."
  echo ""
fi
echo "══════════════════════════════════════════════════"
echo ""
