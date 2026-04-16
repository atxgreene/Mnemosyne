#!/usr/bin/env bash
# install-service.sh — register mnemosyne-serve as a per-user service.
#
# Detects Linux (systemd user) or macOS (launchd) and installs the
# appropriate unit/plist. No root required. Idempotent.
#
# Usage:
#   bash deploy/install-service.sh               # install + start
#   bash deploy/install-service.sh --uninstall   # stop + unregister
#   bash deploy/install-service.sh --status      # show status
#
# Token auth (recommended if your laptop isn't otherwise locked down):
#   MNEMOSYNE_SERVE_TOKEN=hunter2 bash deploy/install-service.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-install}"
PROJECTS_DIR="${MNEMOSYNE_PROJECTS_DIR:-$HOME/projects/mnemosyne}"
VENV_BIN="$PROJECTS_DIR/.venv/bin"

die() { printf 'error: %s\n' "$1" >&2; exit 1; }
info() { printf '  %s\n' "$1"; }

if [ ! -x "$VENV_BIN/mnemosyne-serve" ]; then
    die "mnemosyne-serve not found at $VENV_BIN/mnemosyne-serve — run install-mnemosyne.sh first"
fi

case "$(uname -s)" in
    Linux)
        USER_UNIT_DIR="$HOME/.config/systemd/user"
        UNIT_PATH="$USER_UNIT_DIR/mnemosyne.service"
        SRC="$SCRIPT_DIR/mnemosyne.service"
        case "$MODE" in
            install)
                mkdir -p "$USER_UNIT_DIR"
                # Substitute %h with literal home so it works if systemd's
                # %h expansion is unavailable (uncommon but possible).
                cp "$SRC" "$UNIT_PATH"
                if [ -n "${MNEMOSYNE_SERVE_TOKEN:-}" ]; then
                    sed -i "s|^; Environment=\"MNEMOSYNE_SERVE_TOKEN=change-me\"|Environment=\"MNEMOSYNE_SERVE_TOKEN=$MNEMOSYNE_SERVE_TOKEN\"|" "$UNIT_PATH"
                    info "auth token configured"
                fi
                systemctl --user daemon-reload
                systemctl --user enable --now mnemosyne
                info "installed: $UNIT_PATH"
                info "status:    systemctl --user status mnemosyne"
                info "logs:      journalctl --user -u mnemosyne -f"
                info "dashboard: http://127.0.0.1:8484/ui"
                ;;
            --uninstall)
                systemctl --user disable --now mnemosyne 2>/dev/null || true
                rm -f "$UNIT_PATH"
                systemctl --user daemon-reload
                info "uninstalled"
                ;;
            --status)
                systemctl --user status mnemosyne --no-pager --lines 20 || true
                ;;
            *)  die "unknown mode: $MODE" ;;
        esac
        ;;
    Darwin)
        LAUNCH_DIR="$HOME/Library/LaunchAgents"
        PLIST_PATH="$LAUNCH_DIR/com.atxgreene.mnemosyne.plist"
        SRC="$SCRIPT_DIR/com.atxgreene.mnemosyne.plist"
        case "$MODE" in
            install)
                mkdir -p "$LAUNCH_DIR"
                mkdir -p "$HOME/Library/Logs/mnemosyne"
                # Substitute user-specific paths
                sed "s|/Users/YOU|$HOME|g" "$SRC" > "$PLIST_PATH"
                if [ -n "${MNEMOSYNE_SERVE_TOKEN:-}" ]; then
                    # insert token env var; simplest: sed in before </dict> of env
                    python3 -c "
import plistlib, sys
p = plistlib.load(open('$PLIST_PATH', 'rb'))
p.setdefault('EnvironmentVariables', {})['MNEMOSYNE_SERVE_TOKEN'] = '$MNEMOSYNE_SERVE_TOKEN'
plistlib.dump(p, open('$PLIST_PATH', 'wb'))
"
                    info "auth token configured"
                fi
                launchctl unload "$PLIST_PATH" 2>/dev/null || true
                launchctl load "$PLIST_PATH"
                info "installed: $PLIST_PATH"
                info "logs:      tail -f ~/Library/Logs/mnemosyne/*.log"
                info "dashboard: http://127.0.0.1:8484/ui"
                ;;
            --uninstall)
                launchctl unload "$PLIST_PATH" 2>/dev/null || true
                rm -f "$PLIST_PATH"
                info "uninstalled"
                ;;
            --status)
                launchctl list | grep -E '^[0-9]+\s+[0-9]+\s+com\.atxgreene\.mnemosyne' \
                    || echo "  not loaded"
                ;;
            *)  die "unknown mode: $MODE" ;;
        esac
        ;;
    *)
        die "unsupported OS: $(uname -s)"
        ;;
esac
