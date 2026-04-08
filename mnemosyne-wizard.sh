#!/usr/bin/env bash
# ==============================================================================
#  mnemosyne-wizard.sh
#  Interactive post-install setup wizard for Mnemosyne.
#
#  Walks through:
#    1. LLM backend (Ollama host + model, validated against running daemon)
#    2. Telegram channel (bot token validated via api.telegram.org/getMe,
#       chat ID auto-detected from /getUpdates)
#    3. Obsidian vault path (env-var slot for the upcoming skill — preview only)
#    4. Writes ~/projects/mnemosyne/.env (mode 600) with backup of any prior file
#
#  Safe to re-run: reads existing .env, offers current values as defaults,
#  preserves any keys it doesn't manage (so Discord/Slack/REST creds you add
#  by hand survive a re-run).
#
#  Usage:
#    bash mnemosyne-wizard.sh
#    PROJECTS_DIR=$HOME/code/mnemosyne bash mnemosyne-wizard.sh
# ==============================================================================

set -euo pipefail

# bash 4+ required for associative arrays
if (( BASH_VERSINFO[0] < 4 )); then
  echo "bash >= 4 required (you have $BASH_VERSION)" >&2
  exit 1
fi

PROJECTS_DIR="${PROJECTS_DIR:-$HOME/projects/mnemosyne}"
ENV_FILE="$PROJECTS_DIR/.env"
VENV="$PROJECTS_DIR/.venv"

# ---- pretty output ------------------------------------------------------------
c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'; c_yellow=$'\033[1;33m'
c_red=$'\033[1;31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf "%s==>%s %s\n" "$c_blue"  "$c_off" "$*"; }
ok()   { printf "%s✓%s   %s\n" "$c_green" "$c_off" "$*"; }
warn() { printf "%s!%s   %s\n" "$c_yellow" "$c_off" "$*"; }
err()  { printf "%s✗%s   %s\n" "$c_red"   "$c_off" "$*" 1>&2; }
die()  { err "$*"; exit 1; }
hr()   { printf "%s──────────────────────────────────────────────────────────────%s\n" "$c_dim" "$c_off"; }

# ---- preflight ----------------------------------------------------------------
[ -d "$PROJECTS_DIR" ] || die "$PROJECTS_DIR not found. Run install-mnemosyne.sh first."
command -v curl    >/dev/null || die "curl required"
command -v python3 >/dev/null || die "python3 required"

# ---- load existing .env (preserves unknown keys) ------------------------------
declare -A CFG
if [ -f "$ENV_FILE" ]; then
  log "Found existing .env at $ENV_FILE — values offered as defaults"
  while IFS=$'\t' read -r k v; do
    [ -z "$k" ] && continue
    CFG[$k]="$v"
  done < <(python3 - "$ENV_FILE" <<'PY'
import sys
path = sys.argv[1]
for line in open(path):
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, v = s.split("=", 1)
    k = k.strip()
    v = v.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    print(f"{k}\t{v}")
PY
)
fi

cur() { printf '%s' "${CFG[$1]:-${2:-}}"; }

# ---- prompt helpers -----------------------------------------------------------
ask() {
  local prompt="$1" default="${2:-}" reply
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " reply
    printf '%s' "${reply:-$default}"
  else
    read -r -p "$prompt: " reply
    printf '%s' "$reply"
  fi
}

ask_secret() {
  local prompt="$1" default="${2:-}" reply hint=""
  [ -n "$default" ] && hint=" (current: ${default:0:6}…, enter to keep)"
  read -r -s -p "$prompt$hint: " reply
  echo
  printf '%s' "${reply:-$default}"
}

ask_yn() {
  local prompt="$1" default="${2:-n}" reply
  read -r -p "$prompt [$default]: " reply
  reply="${reply:-$default}"
  [[ "$reply" =~ ^[Yy] ]]
}

# ---- header -------------------------------------------------------------------
clear 2>/dev/null || true
hr
printf "%s  Mnemosyne setup wizard%s\n" "$c_green" "$c_off"
hr
echo
echo "Configures channel credentials and writes them to:"
echo "  $ENV_FILE"
echo
echo "Existing values are reused unless you overwrite them. ^C anytime to abort."
echo

# ---- step 1: LLM backend ------------------------------------------------------
log "Step 1/4: LLM backend"
OLLAMA_HOST=$(ask "Ollama host" "$(cur OLLAMA_HOST http://localhost:11434)")
OLLAMA_MODEL=$(ask "Model name" "$(cur OLLAMA_MODEL qwen3:8b)")

if curl -fsS --max-time 5 "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
  ok "Ollama responding at $OLLAMA_HOST"
  if curl -fsS --max-time 5 "$OLLAMA_HOST/api/tags" 2>/dev/null \
    | python3 -c "
import sys, json
target = '$OLLAMA_MODEL'
try:
    d = json.load(sys.stdin)
    names = [m.get('name','') for m in d.get('models',[])]
    sys.exit(0 if target in names else 1)
except Exception:
    sys.exit(2)
" 2>/dev/null
  then
    ok "Model $OLLAMA_MODEL present"
  else
    warn "Model $OLLAMA_MODEL not in ollama list — pull with: ollama pull $OLLAMA_MODEL"
  fi
else
  warn "Ollama at $OLLAMA_HOST not responding (continuing — config will still be written)"
fi
echo

# ---- step 2: Telegram channel -------------------------------------------------
log "Step 2/4: Telegram channel"
echo "Mnemosyne supports Telegram, Discord, Slack, and a local REST channel."
echo "This wizard configures Telegram only — others coming."
echo

TELEGRAM_BOT_TOKEN=""
TELEGRAM_ALLOWED_CHAT_IDS=""
BOT_NAME=""
TG_DEFAULT="n"
[ -n "$(cur TELEGRAM_BOT_TOKEN)" ] && TG_DEFAULT="y"

if ask_yn "Enable Telegram?" "$TG_DEFAULT"; then
  echo
  printf "%sGet a token from @BotFather on Telegram (/newbot).%s\n" "$c_dim" "$c_off"
  TELEGRAM_BOT_TOKEN=$(ask_secret "Bot token" "$(cur TELEGRAM_BOT_TOKEN)")

  if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    warn "No token entered — skipping Telegram"
  else
    log "Validating token via api.telegram.org/getMe"
    if BOTINFO=$(curl -fsS --max-time 8 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" 2>/dev/null); then
      BOT_NAME=$(printf '%s' "$BOTINFO" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    if d.get("ok"):
        print(d["result"].get("username",""))
except Exception:
    pass
' 2>/dev/null || true)
    fi

    if [ -n "$BOT_NAME" ]; then
      ok "Bot validated: @$BOT_NAME"
      echo
      echo "Mnemosyne only responds to chat IDs in TELEGRAM_ALLOWED_CHAT_IDS."
      echo "If you don't know your chat ID:"
      echo "  1. Open Telegram and message @$BOT_NAME (any text)"
      echo "  2. Press enter here to scan recent updates"
      echo "Or type a chat ID directly (e.g. 12345678 or 12345678,87654321)."
      echo
      read -r -p "chat ID(s) or [enter] to scan: " CHAT_INPUT

      if [[ "$CHAT_INPUT" =~ ^-?[0-9]+(,-?[0-9]+)*$ ]]; then
        TELEGRAM_ALLOWED_CHAT_IDS="$CHAT_INPUT"
        ok "Using chat ID(s): $TELEGRAM_ALLOWED_CHAT_IDS"
      else
        log "Polling /getUpdates"
        UPDATES=$(curl -fsS --max-time 8 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" 2>/dev/null || echo "")
        DETECTED=$(printf '%s' "$UPDATES" | python3 - <<'PY' 2>/dev/null || true
import sys, json
try:
    d = json.loads(sys.stdin.read())
    if not d.get("ok"):
        sys.exit(1)
    seen = {}
    for u in d.get("result", []):
        msg = u.get("message") or u.get("channel_post") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        label = chat.get("username") or chat.get("title") or chat.get("first_name") or "?"
        seen[cid] = label
    for cid, label in seen.items():
        print(f"{cid}\t{label}")
except Exception:
    sys.exit(1)
PY
)
        if [ -n "$DETECTED" ]; then
          echo
          echo "Recent chats that messaged @$BOT_NAME:"
          printf '%s\n' "$DETECTED" | awk -F'\t' '{printf "  %s\t%s\n", $1, $2}'
          echo
          DEFAULT_ID=$(printf '%s' "$DETECTED" | head -1 | cut -f1)
          TELEGRAM_ALLOWED_CHAT_IDS=$(ask "Allowed chat IDs (comma-separated)" "$DEFAULT_ID")
        else
          warn "No updates found. Message @$BOT_NAME first, then re-run the wizard,"
          warn "or enter a chat ID manually:"
          TELEGRAM_ALLOWED_CHAT_IDS=$(ask "chat ID(s)" "$(cur TELEGRAM_ALLOWED_CHAT_IDS)")
        fi
      fi
    else
      err "Token rejected by api.telegram.org. Double-check it and re-run."
      TELEGRAM_BOT_TOKEN=""
    fi
  fi
fi
echo

# ---- step 3: Obsidian skill (preview) -----------------------------------------
log "Step 3/4: Obsidian skill (preview)"
echo "Mnemosyne will eventually expose your Obsidian vault as a skill."
echo "This step only writes the path to .env — the skill module isn't wired up yet."
echo
OBSIDIAN_VAULT_PATH=""
OBS_DEFAULT="n"
[ -n "$(cur OBSIDIAN_VAULT_PATH)" ] && OBS_DEFAULT="y"
if ask_yn "Configure Obsidian vault path now?" "$OBS_DEFAULT"; then
  OBSIDIAN_VAULT_PATH=$(ask "Vault path (absolute, accessible from WSL)" \
    "$(cur OBSIDIAN_VAULT_PATH /mnt/c/Users/austi/Documents/Obsidian)")
  if [ -d "$OBSIDIAN_VAULT_PATH" ]; then
    ok "Vault directory exists"
  else
    warn "Path does not exist or is not accessible — saving anyway"
  fi
fi
echo

# ---- step 4: write .env -------------------------------------------------------
log "Step 4/4: write $ENV_FILE"

# Merge new values into preserved CFG
update() {
  local k="$1" v="$2"
  if [ -n "$v" ]; then
    CFG[$k]="$v"
  else
    unset 'CFG['"$k"']' 2>/dev/null || true
  fi
}
update OLLAMA_HOST "$OLLAMA_HOST"
update OLLAMA_MODEL "$OLLAMA_MODEL"
update TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
update TELEGRAM_ALLOWED_CHAT_IDS "$TELEGRAM_ALLOWED_CHAT_IDS"
update OBSIDIAN_VAULT_PATH "$OBSIDIAN_VAULT_PATH"

# Preview (with masked token)
echo
echo "Preview:"
hr
echo "OLLAMA_HOST=${CFG[OLLAMA_HOST]:-}"
echo "OLLAMA_MODEL=${CFG[OLLAMA_MODEL]:-}"
if [ -n "${CFG[TELEGRAM_BOT_TOKEN]:-}" ]; then
  echo "TELEGRAM_BOT_TOKEN=${CFG[TELEGRAM_BOT_TOKEN]:0:6}…(hidden)"
  echo "TELEGRAM_ALLOWED_CHAT_IDS=${CFG[TELEGRAM_ALLOWED_CHAT_IDS]:-}"
fi
if [ -n "${CFG[OBSIDIAN_VAULT_PATH]:-}" ]; then
  echo "OBSIDIAN_VAULT_PATH=${CFG[OBSIDIAN_VAULT_PATH]}"
fi
hr

if ! ask_yn "Write to $ENV_FILE?" "y"; then
  warn "Aborted. No file written."
  exit 0
fi

# Backup existing
if [ -f "$ENV_FILE" ]; then
  ts=$(date +%Y%m%d-%H%M%S)
  cp "$ENV_FILE" "$ENV_FILE.bak.$ts"
  ok "Backed up to $ENV_FILE.bak.$ts"
fi

# Write
{
  echo "# Mnemosyne credentials — NEVER commit this file"
  echo "# Written by mnemosyne-wizard.sh on $(date -Iseconds)"
  echo
  echo "# --- LLM backend ---"
  echo "OLLAMA_HOST=${CFG[OLLAMA_HOST]:-http://localhost:11434}"
  echo "OLLAMA_MODEL=${CFG[OLLAMA_MODEL]:-qwen3:8b}"
  echo
  echo "# --- Telegram ---"
  if [ -n "${CFG[TELEGRAM_BOT_TOKEN]:-}" ]; then
    echo "TELEGRAM_BOT_TOKEN=${CFG[TELEGRAM_BOT_TOKEN]}"
    echo "TELEGRAM_ALLOWED_CHAT_IDS=${CFG[TELEGRAM_ALLOWED_CHAT_IDS]:-}"
  else
    echo "# TELEGRAM_BOT_TOKEN="
    echo "# TELEGRAM_ALLOWED_CHAT_IDS="
  fi
  echo
  echo "# --- Obsidian skill (preview, not yet wired) ---"
  if [ -n "${CFG[OBSIDIAN_VAULT_PATH]:-}" ]; then
    echo "OBSIDIAN_VAULT_PATH=${CFG[OBSIDIAN_VAULT_PATH]}"
  else
    echo "# OBSIDIAN_VAULT_PATH="
  fi
  echo
  # Preserve any other keys (Discord/Slack/REST/whatever the user added)
  printed_other=0
  for k in "${!CFG[@]}"; do
    case "$k" in
      OLLAMA_HOST|OLLAMA_MODEL|TELEGRAM_BOT_TOKEN|TELEGRAM_ALLOWED_CHAT_IDS|OBSIDIAN_VAULT_PATH) ;;
      *)
        if [ "$printed_other" = 0 ]; then
          echo "# --- Other (preserved from previous .env) ---"
          printed_other=1
        fi
        printf '%s=%s\n' "$k" "${CFG[$k]}"
        ;;
    esac
  done
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok "Wrote $ENV_FILE (mode 600)"

echo
hr
ok "Wizard complete"
hr
echo
echo "To boot the agent with these settings:"
echo "  source $VENV/bin/activate"
echo "  set -a; . $ENV_FILE; set +a"
echo "  cd $PROJECTS_DIR/eternal-context/skills/eternal-context"
echo "  python -m eternalcontext"
echo
if [ -n "${CFG[TELEGRAM_BOT_TOKEN]:-}" ]; then
  bot_label="${BOT_NAME:-your-bot}"
  echo "Telegram channel is configured. The agent should pick up the env vars"
  echo "and start listening on @$bot_label after launch."
fi
