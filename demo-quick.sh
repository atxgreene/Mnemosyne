#!/usr/bin/env bash
# ==============================================================================
#  demo-quick.sh
#
#  Short, screen-recordable walkthrough — ~45 seconds.
#  Used to generate docs/demo.gif via asciinema + agg.
#
#  The full narrative demo lives in demo.sh (18 sections, ~60 seconds).
#  This one is tuned for a GIF: fewer sections, less scrolling, bigger
#  visual payoff per second.
# ==============================================================================

set -euo pipefail

# Pause helper — makes the recording readable.
pause() { sleep "${1:-0.8}"; }

hr() { printf '\033[2m─────────────────────────────────────────────────────────────\033[0m\n'; }
say() { printf '\033[1;36m▶\033[0m \033[1m%s\033[0m\n' "$1"; pause 1.2; }
dim() { printf '\033[2m%s\033[0m\n' "$1"; }

clear
printf '\033[1;35m'
cat <<'BANNER'

   ╔═══════════════════════════════════════════════════╗
   ║   Mnemosyne — local-first agent framework         ║
   ║   v0.2.1 — observable, tunable, identity-stable   ║
   ╚═══════════════════════════════════════════════════╝

BANNER
printf '\033[0m'
pause 2

# ---- 1. identity lock -------------------------------------------------------
say "1.  Identity lock — the model says it's Claude; Mnemosyne says otherwise"
hr
python3 <<'PY' 2>&1 | head -8
from mnemosyne_identity import enforce_identity
for txt in ["I am Claude, an AI assistant made by Anthropic.",
            "I'm ChatGPT, created by OpenAI.",
            "My name is Gemini."]:
    out, slips = enforce_identity(txt)
    print(f"  in:  {txt}")
    print(f"  out: {out}")
    print()
PY
pause 1.5

# ---- 2. memory layer --------------------------------------------------------
say "2.  ICMS memory — L1 hot / L2 warm / L3 cold, with FTS5 search"
hr
DB=$(mktemp /tmp/mnemo-demo-XXXXXX.db)
mnemosyne-memory --db "$DB" write "Project uses Rust + tokio for the API"     --kind project --tier 1 >/dev/null
mnemosyne-memory --db "$DB" write "User prefers dark mode in terminal apps"   --kind preference --tier 2 >/dev/null
mnemosyne-memory --db "$DB" write "Deprecated: python3.8 support was dropped" --kind fact --tier 3 >/dev/null
dim "  3 memories written across tiers 1/2/3"
mnemosyne-memory --db "$DB" search rust --limit 2
pause 1.2

# ---- 3. triage → proposer → apply ------------------------------------------
say "3.  Self-healing loop: triage → proposer → apply (Meta-Harness closure)"
hr
DEMO_PROJ=$(mktemp -d /tmp/mnemo-demo-proj-XXXXXX)
export MNEMOSYNE_PROJECTS_DIR="$DEMO_PROJ"

python3 <<'PY' 2>&1 | sed 's/^/  /'
import harness_telemetry as ht
rid = ht.create_run(model="demo", tags=["gif-demo"])
with ht.TelemetrySession(rid) as s:
    for _ in range(10):
        s.log("identity_slip_detected", status="error",
              metadata={"slips": ["I am Claude"], "count": 1})
ht.finalize_run(rid, metrics={"turns_total": 10, "turns_failed": 10})
print(f"seeded run: {rid}")
PY
pause 0.8
dim "  running proposer..."
mnemosyne-proposer --min-severity 0 --window-days 30 2>&1 | head -6
pause 1.2

# ---- 4. training bridge -----------------------------------------------------
say "4.  Training bridge: events → Hermes ShareGPT → LoRA-ready JSONL"
hr
# Seed one training_turn event to demonstrate export
python3 <<'PY' 2>&1 | sed 's/^/  /'
import json, os
from pathlib import Path
pd = Path(os.environ['MNEMOSYNE_PROJECTS_DIR'])
rd = pd / 'experiments' / 'run_gif_demo'
rd.mkdir(parents=True, exist_ok=True)
events = [
    {"event_id": "t1", "event_type": "turn_start",  "metadata": {"turn_number": 1}},
    {"event_id": "t2", "event_type": "training_turn", "parent_event_id": "t1",
     "metadata": {"system_prompt": "You are Mnemosyne.",
                  "user_message": "What is the capital of France?",
                  "assistant_text": "Paris is the capital of France.",
                  "tool_calls": [], "model": "qwen3.5:9b", "provider": "ollama"}},
    {"event_id": "t3", "event_type": "turn_end",    "parent_event_id": "t1", "status": "ok"},
]
with (rd / 'events.jsonl').open('w') as f:
    for e in events:
        f.write(json.dumps(e) + '\n')
print("seeded training_turn event")
PY
mnemosyne-train export --out /tmp/gif-trajs.jsonl 2>&1 | sed 's/^/  /'
pause 0.6
dim "  first trajectory (Hermes-compatible schema):"
head -1 /tmp/gif-trajs.jsonl | python3 -m json.tool | head -18 | sed 's/^/  /'
pause 1.5

# ---- 5. tests ---------------------------------------------------------------
say "5.  Verify: 156 unit tests, pyflakes clean, pip-installable"
hr
dim "  python3 tests/test_all.py"
python3 /home/user/sturdy-doodle/tests/test_all.py 2>&1 | tail -1 | sed 's/^/  /'
pause 0.4
dim "  python3 -m pyflakes *.py"
python3 -m pyflakes $(find /home/user/sturdy-doodle -maxdepth 1 -name '*.py') 2>&1 \
    && printf '  \033[1;32m✓\033[0m pyflakes clean\n' \
    || printf '  ✗ pyflakes found issues\n'
pause 2

# ---- done -------------------------------------------------------------------
printf '\n\033[1;35m'
cat <<'BANNER'
   ───────────────────────────────────────────────────
         github.com/atxgreene/sturdy-doodle
         docs/ROADMAP.md · docs/BENCHMARKS.md
         156/156 tests · 15 console scripts · stdlib-only core
   ───────────────────────────────────────────────────
BANNER
printf '\033[0m\n'
pause 3
