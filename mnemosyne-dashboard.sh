#!/usr/bin/env bash
# ==============================================================================
#  mnemosyne-dashboard.sh
#
#  Live terminal dashboard for an active Mnemosyne deployment.
#
#  Auto-refreshes every N seconds (default 3) showing:
#    - Ollama daemon status + loaded models
#    - Current experiments directory size + run count
#    - Last 5 runs (status, model, tags, event count)
#    - Per-tier memory statistics (L1 hot / L2 warm / L3 cold)
#    - Recent telemetry events from the most recent run
#    - Disk usage on $PROJECTS_DIR
#
#  Three display modes:
#    - ANSI color TUI (default, when $TERM is a real terminal)
#    - --plain: no color, plain text (pipeable, loggable)
#    - --once:  one frame then exit (suitable for cron / scripts)
#
#  Environment:
#    MNEMOSYNE_PROJECTS_DIR    default: ~/projects/mnemosyne
#    MNEMOSYNE_DASHBOARD_INTERVAL  default: 3
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECTS_DIR="${MNEMOSYNE_PROJECTS_DIR:-$HOME/projects/mnemosyne}"
INTERVAL="${MNEMOSYNE_DASHBOARD_INTERVAL:-3}"
PLAIN=0
ONCE=0

for arg in "$@"; do
  case "$arg" in
    --plain) PLAIN=1 ;;
    --once)  ONCE=1 ;;
    --interval=*) INTERVAL="${arg#--interval=}" ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# ANSI codes (disabled under --plain)
if [ "$PLAIN" = 1 ] || [ ! -t 1 ]; then
  c_red=""; c_green=""; c_yellow=""; c_blue=""; c_cyan=""; c_dim=""; c_bold=""; c_off=""
else
  c_red=$'\033[1;31m'; c_green=$'\033[1;32m'; c_yellow=$'\033[1;33m'
  c_cyan=$'\033[1;36m'; c_dim=$'\033[2m'
  c_bold=$'\033[1m'; c_off=$'\033[0m'
  # c_blue reserved for future sections — prevent SC2034 noise
  : "${c_blue:=}"
fi

hr() { printf '%s%s%s\n' "$c_dim" "────────────────────────────────────────────────────────────────" "$c_off"; }

render_frame() {
  # Clear screen on interactive mode
  if [ "$PLAIN" = 0 ] && [ -t 1 ]; then
    printf '\033[H\033[2J'
  fi

  printf '%s%sMnemosyne dashboard%s   %s%s%s\n' \
    "$c_bold" "$c_cyan" "$c_off" "$c_dim" "$(date -Iseconds)" "$c_off"
  # shellcheck disable=SC2016  # the literal $PROJECTS_DIR is intentional
  printf '%s$PROJECTS_DIR:%s %s\n' "$c_dim" "$c_off" "$PROJECTS_DIR"
  hr

  # ---- 1. Ollama ---------------------------------------------------------
  ollama_status="$(_ollama_status)"
  printf '%sOllama:%s %s\n' "$c_bold" "$c_off" "$ollama_status"

  # ---- 2. Experiments tree ----------------------------------------------
  exp_dir="$PROJECTS_DIR/experiments"
  if [ -d "$exp_dir" ]; then
    run_count=$(find "$exp_dir" -maxdepth 1 -type d -name 'run_*' 2>/dev/null | wc -l | tr -d ' ')
    exp_size=$(du -sh "$exp_dir" 2>/dev/null | cut -f1)
    printf '%sExperiments:%s %s runs, %s on disk\n' "$c_bold" "$c_off" "$run_count" "$exp_size"
  else
    printf '%sExperiments:%s %s(directory missing)%s\n' \
      "$c_bold" "$c_off" "$c_yellow" "$c_off"
  fi

  # ---- 3. Last 5 runs ----------------------------------------------------
  hr
  printf '%sLast 5 runs:%s\n' "$c_bold" "$c_off"
  if [ -d "$exp_dir" ]; then
    # Prefer the installed mnemosyne-experiments CLI; fall back to python3
    if command -v mnemosyne-experiments >/dev/null 2>&1; then
      mnemosyne-experiments list --limit 5 2>/dev/null \
        | sed 's/^/  /' || echo "  (list failed)"
    elif [ -f "$SCRIPT_DIR/mnemosyne_experiments.py" ]; then
      python3 "$SCRIPT_DIR/mnemosyne_experiments.py" list --limit 5 2>/dev/null \
        | sed 's/^/  /' || echo "  (list failed)"
    else
      echo "  (mnemosyne-experiments not available)"
    fi
  else
    echo "  (no experiments directory)"
  fi

  # ---- 4. Memory stats ---------------------------------------------------
  hr
  printf '%sMemory:%s\n' "$c_bold" "$c_off"
  mem_db="$PROJECTS_DIR/memory.db"
  if [ -f "$mem_db" ]; then
    # Shell out to the memory module's stats subcommand
    if [ -f "$SCRIPT_DIR/mnemosyne_memory.py" ]; then
      python3 "$SCRIPT_DIR/mnemosyne_memory.py" --db "$mem_db" stats 2>/dev/null \
        | sed 's/^/  /' || echo "  (stats failed)"
    else
      mem_size=$(du -sh "$mem_db" 2>/dev/null | cut -f1)
      echo "  (size: $mem_size, detailed stats unavailable)"
    fi
  else
    printf '  %s(memory.db not yet created)%s\n' "$c_dim" "$c_off"
  fi

  # ---- 5. Recent events from latest run ----------------------------------
  hr
  printf '%sRecent events (latest run):%s\n' "$c_bold" "$c_off"
  latest_run="$exp_dir/latest"
  if [ -L "$latest_run" ] || [ -d "$latest_run" ]; then
    events_file="$latest_run/events.jsonl"
    if [ -f "$events_file" ]; then
      tail -5 "$events_file" 2>/dev/null \
        | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        e = json.loads(line)
        et = e.get('event_type', '?')
        tool = e.get('tool') or '-'
        dur = e.get('duration_ms')
        dur_str = f'{dur:7.1f}ms' if isinstance(dur, (int, float)) else '         -'
        status = e.get('status', '?')
        print(f'  {et:<14}  {tool:<20}  {dur_str}  {status}')
    except json.JSONDecodeError:
        pass
" 2>/dev/null || echo "  (parse failed)"
    else
      printf '  %s(no events.jsonl in latest run)%s\n' "$c_dim" "$c_off"
    fi
  else
    printf '  %s(no runs yet)%s\n' "$c_dim" "$c_off"
  fi

  # ---- 6. Disk usage -----------------------------------------------------
  hr
  if command -v df >/dev/null 2>&1; then
    disk_line=$(df -h "$PROJECTS_DIR" 2>/dev/null | tail -1)
    if [ -n "$disk_line" ]; then
      printf '%sDisk:%s %s\n' "$c_bold" "$c_off" "$disk_line"
    fi
  fi

  if [ "$ONCE" = 0 ] && [ "$PLAIN" = 0 ]; then
    hr
    printf '%sAuto-refresh every %ss. Press Ctrl-C to exit.%s\n' \
      "$c_dim" "$INTERVAL" "$c_off"
  fi
}

_ollama_status() {
  if command -v curl >/dev/null 2>&1; then
    tags=$(curl -sf --max-time 2 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" 2>/dev/null || true)
    if [ -n "$tags" ]; then
      models=$(echo "$tags" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    names = [m.get('name','?') for m in d.get('models', [])]
    print(', '.join(names) if names else '(no models pulled)')
except Exception:
    print('(parse error)')
" 2>/dev/null)
      printf '%sreachable%s  — models: %s' "$c_green" "$c_off" "$models"
      return
    fi
  fi
  printf '%snot reachable%s at %s' "$c_red" "$c_off" "${OLLAMA_HOST:-http://localhost:11434}"
}

# ---- main loop ---------------------------------------------------------------

trap 'printf "\n"; exit 0' INT TERM

if [ "$ONCE" = 1 ]; then
  render_frame
  exit 0
fi

while true; do
  render_frame
  sleep "$INTERVAL"
done
