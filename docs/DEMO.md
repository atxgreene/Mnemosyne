# Mnemosyne — end-to-end demo transcript

*Regenerate with `bash demo.sh > docs/DEMO.md 2>&1` (wrap in the markdown preamble below). This is the captured output of `demo.sh` — 11 sections, no hand-editing, no faking. Re-runnable in any fresh clone with zero API keys and zero network access.*

**What the demo exercises:**

1. `pip install -e .` into a clean venv — verifies 9 console entry points on `$PATH` and every library surface imports with no `sys.path` hacks
2. `mnemosyne-models list` + `current` — 19 supported providers enumerated
3. `environment-snapshot` — first-turn preamble (Meta-Harness Terminal-Bench-2 pattern)
4. Memory layer — writes across L1/L2/L3, FTS5 search, `--tier-max`, stats
5. **Identity lock** — filter rewrites 5 slip patterns / keeps 3 legitimate references; brain turn with a mock "I am Claude" model shows the lock HOLDS ✓
6. Skills — `@register_python`, `$PATH` command discovery, `record_learned_skill()`
7. Full pipeline — 8-point parameter sweep × 10 scenarios, Pareto frontier
8. Aggregate stats — per-tool call counts, latency p50/p95/p99
9. **Triage engine** — reads events.jsonl across recent runs, clusters errors by `(event_type, tool, error_type)`, scores severity on six dimensions, emits a markdown health report
10. Live dashboard frame (`--once --plain`)
11. Test suite — integration + unit, all green

---

```
Mnemosyne end-to-end demo
Generated:   2026-04-15T00:57:42+00:00
Commit:      ad5a84d
Branch:      claude/setup-mnemosyne-consciousness-NZqQE
Python:      Python 3.11.15

────────────────────────────────────────────────────────────────
 1/11  pip install -e . into a fresh venv
────────────────────────────────────────────────────────────────
Successfully built mnemosyne-harness
Installing collected packages: mnemosyne-harness
Successfully installed mnemosyne-harness-0.1.0

── Installed console entry points on $PATH:
  environment-snapshot
  harness-telemetry
  mnemosyne-experiments
  mnemosyne-memory
  mnemosyne-models
  mnemosyne-pipeline
  mnemosyne-triage
  notion-search
  obsidian-search

── Library imports (no sys.path hacks):
  ✓ all 7 library surfaces import cleanly

────────────────────────────────────────────────────────────────
 2/11  Model providers — 19 backends detected
────────────────────────────────────────────────────────────────

── mnemosyne-models list
provider        kind    status         env var                 endpoint
------------------------------------------------------------------------------------------------------------------------
anthropic       cloud   unauthorized   ANTHROPIC_API_KEY       https://api.anthropic.com/v1/messages
cerebras        cloud   unauthorized   CEREBRAS_API_KEY        https://api.cerebras.ai/v1/chat/completions
cohere          cloud   unauthorized   COHERE_API_KEY          https://api.cohere.ai/compatibility/v1/chat/completions
deepseek        cloud   unauthorized   DEEPSEEK_API_KEY        https://api.deepseek.com/v1/chat/completions
fireworks       cloud   unauthorized   FIREWORKS_API_KEY       https://api.fireworks.ai/inference/v1/chat/completions
google          cloud   unauthorized   GOOGLE_API_KEY          https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
groq            cloud   unauthorized   GROQ_API_KEY            https://api.groq.com/openai/v1/chat/completions
hyperbolic      cloud   unauthorized   HYPERBOLIC_API_KEY      https://api.hyperbolic.xyz/v1/chat/completions
lmstudio        local   local/unreachable  -                       http://localhost:1234/v1/chat/completions
mistral         cloud   unauthorized   MISTRAL_API_KEY         https://api.mistral.ai/v1/chat/completions
nous            cloud   unauthorized   NOUS_PORTAL_API_KEY     https://inference-api.nousresearch.com/v1/chat/completions
novita          cloud   unauthorized   NOVITA_API_KEY          https://api.novita.ai/v3/openai/chat/completions
ollama          local   local/unreachable  -                       http://localhost:11434/api/chat
openai          cloud   unauthorized   OPENAI_API_KEY          https://api.openai.com/v1/chat/completions
openrouter      cloud   unauthorized   OPENROUTER_API_KEY      https://openrouter.ai/api/v1/chat/completions
perplexity      cloud   unauthorized   PERPLEXITY_API_KEY      https://api.perplexity.ai/chat/completions
tgi             local   local/unreachable  -                       http://localhost:8080/v1/chat/completions
together        cloud   unauthorized   TOGETHER_API_KEY        https://api.together.xyz/v1/chat/completions
vllm            local   local/unreachable  -                       http://localhost:8000/v1/chat/completions
xai             cloud   unauthorized   XAI_API_KEY             https://api.x.ai/v1/chat/completions

── mnemosyne-models current   (no auth configured → falls back)
provider:       ollama
endpoint:       http://localhost:11434/api/chat
default_model:  qwen3:8b
has_api_key:    False

────────────────────────────────────────────────────────────────
 3/11  Environment snapshot  (first-turn preamble, Meta-Harness Terminal-Bench 2 pattern)
────────────────────────────────────────────────────────────────

── environment-snapshot  (human-readable markdown)
# Mnemosyne environment snapshot

**Projects dir:** /tmp/mnemo-demo-vOf55z/projects (0 entries)

**.env:** not found (run mnemosyne-wizard.sh)

**Ollama:** NOT reachable at http://localhost:11434 (URLError)

**GPU:** none detected (CPU inference)

**venv:** NOT FOUND at /tmp/mnemo-demo-vOf55z/projects/.venv

**Skills available:** notion-search, obsidian-search

**Obsidian vault:** not configured (.env missing)

**Disk:** 32.0 GB free of 270.6 GB (11.8% free)

**Platform:** Linux 6.18.5, Python 3.11.15

────────────────────────────────────────────────────────────────
 4/11  Memory layer — SQLite+FTS5 with ICMS 3-tier
────────────────────────────────────────────────────────────────

── Writing 4 memories across all three tiers
1
2
3
4

── search 'gemma' — FTS5-accelerated

── search 'rust' with --tier-max 2 (excludes cold memories)
[L2] Project alpha uses rust and tokio  (project, cli)

── Stats:
{
  "total": 4,
  "by_tier": {
    "L1_hot": 2,
    "L2_warm": 1,
    "L3_cold": 1
  },
  "by_kind": {
    "fact": 1,
    "preference": 2,
    "project": 1
  },
  "fts5_enabled": true,
  "db_path": "/tmp/mnemo-demo-vOf55z/projects/memory.db",
  "schema_version": 1
}

────────────────────────────────────────────────────────────────
 5/11  Identity lock — regardless of underlying model, agent says Mnemosyne
────────────────────────────────────────────────────────────────

── Testing enforce_identity() against 5 slip patterns + 3 legitimate uses
  ✓ [SLIP] CHANGED: I am Claude, an AI assistant made by Anthropic.
                   → I am Mnemosyne, an AI assistant made by Anthropic.
  ✓ [SLIP] CHANGED: I'm ChatGPT, created by OpenAI.
                   → I am Mnemosyne, created by OpenAI.
  ✓ [SLIP] CHANGED: My name is Gemini.
                   → My name is Mnemosyne.
  ✓ [SLIP] CHANGED: I was trained by Anthropic to be helpful.
                   → I was built from the Mnemosyne framework to be helpful.
  ✓ [SLIP] CHANGED: As an AI language model, I cannot help with that.
                   → I cannot help with that.
  ✓ [KEEP] kept   : The difference between Claude and GPT-4 is context windo
  ✓ [KEEP] kept   : You can call the Anthropic API or the OpenAI API for thi
  ✓ [KEEP] kept   : Mnemosyne supports Claude, Gemini, Qwen, and many other 

── Brain end-to-end (mock LLM that slips to "I am Claude")
  user        : Who are you?
  model said  : I am Claude, an AI assistant made by Anthropic. How can I help you today?
  brain output: I am Mnemosyne, an AI assistant made by Anthropic. How can I help you today?
  identity lock: HELD ✓

────────────────────────────────────────────────────────────────
 6/11  Skills — agentskills.io-compatible registry + self-improvement
────────────────────────────────────────────────────────────────
  Registered skills: ['add']
  OpenAI tool-spec shape:
{
  "type": "function",
  "function": {
    "name": "add",
    "description": "add two integers",
    "parameters": {
      "type": "object",
      "properties": {
        "a": {
          "type": "integer",
          "description": ""
        },
        "b": {
          "type": "integer",
          "description": ""
        }
      },
      "required": [
        "a",
        "b"
      ]
    }
  }

  Discovered 2 $PATH skills: ['notion_search', 'obsidian_search']

  Learned skill written to: projects/skills/learned/search-and-summarize-20260415-005750.md
  Parsed back:  name=search-and-summarize  learned=True

────────────────────────────────────────────────────────────────
 7/11  Full pipeline — OBSERVE → EVALUATE → SWEEP → COMPARE → INSPECT
────────────────────────────────────────────────────────────────

── Running examples/sweep_demo.py (8-point sweep, fake harness, ~6 seconds)
sweep complete: 8 runs in 8.9s

Demo sweep finished: 8 runs created.

Inspect the results:

  MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo-vOf55z/projects ./mnemosyne-experiments.py list
  MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo-vOf55z/projects ./mnemosyne-experiments.py top-k 3 --metric accuracy
  MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo-vOf55z/projects ./mnemosyne-experiments.py top-k 3 --metric latency_ms_avg --direction min
  MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo-vOf55z/projects ./mnemosyne-experiments.py pareto \
      --axes accuracy,latency_ms_avg --directions max,min --plot
  MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo-vOf55z/projects ./mnemosyne-experiments.py aggregate run_20260415-005750-mode-qwen3-8b-retr-5-temp-00

── mnemosyne-experiments list  (newest first)
run_20260415-005758-mode-gemma4-e-retr-15-temp-05  [completed]  gemma4:e4b          2026-04-15 00:57:58  events=42     tags=sweep,demo,example
run_20260415-005757-mode-gemma4-e-retr-15-temp-00  [completed]  gemma4:e4b          2026-04-15 00:57:57  events=42     tags=sweep,demo,example
run_20260415-005756-mode-gemma4-e-retr-5-temp-05  [completed]  gemma4:e4b          2026-04-15 00:57:56  events=42     tags=sweep,demo,example
run_20260415-005755-mode-gemma4-e-retr-5-temp-00  [completed]  gemma4:e4b          2026-04-15 00:57:55  events=42     tags=sweep,demo,example
run_20260415-005754-mode-qwen3-8b-retr-15-temp-05  [completed]  qwen3:8b            2026-04-15 00:57:54  events=42     tags=sweep,demo,example
run_20260415-005753-mode-qwen3-8b-retr-15-temp-00  [completed]  qwen3:8b            2026-04-15 00:57:53  events=42     tags=sweep,demo,example
run_20260415-005751-mode-qwen3-8b-retr-5-temp-05  [completed]  qwen3:8b            2026-04-15 00:57:51  events=42     tags=sweep,demo,example
run_20260415-005750-mode-qwen3-8b-retr-5-temp-00  [completed]  qwen3:8b            2026-04-15 00:57:50  events=42     tags=sweep,demo,example

── Top 3 by accuracy:
Top 3 runs by accuracy (max):
  run_20260415-005757-mode-gemma4-e-retr-15-temp-00  accuracy=0.5  model=gemma4:e4b
  run_20260415-005755-mode-gemma4-e-retr-5-temp-00  accuracy=0.5  model=gemma4:e4b
  run_20260415-005753-mode-qwen3-8b-retr-15-temp-00  accuracy=0.5  model=qwen3:8b

── Pareto frontier on accuracy × latency  (ASCII plot):
Pareto frontier on (accuracy, latency_ms_avg) with directions (max, min):
  run_20260415-005757-mode-gemma4-e-retr-15-temp-00  accuracy=0.5  latency_ms_avg=54.188795812514456  model=gemma4:e4b

  latency_ms_avg
    85.40 |.                         .                         
    83.32 |                                                    
    81.24 |                                                    
    79.16 |                                                    
    77.08 |                                                   .
    75.00 |                                                   .
    72.92 |                                                    
    70.84 |                                                    
    68.76 |                                                    
    66.67 |                                                    
    64.59 |                                                    
    62.51 |                          .                         
    60.43 |                          .                         
    58.35 |                                                    
    56.27 |                                                    
    54.19 |                                                   #
          +----------------------------------------------------
          0.38                                            0.50
                                accuracy

  legend:  * = on Pareto frontier   . = dominated   # = overlap

────────────────────────────────────────────────────────────────
 8/11  Aggregate statistics — per-tool call counts, latency percentiles
────────────────────────────────────────────────────────────────

── aggregate for run_20260415-005758-mode-gemma4-e-retr-15-temp-05
# aggregate for run_20260415-005758-mode-gemma4-e-retr-15-temp-05

total events: 42
  scenario_end   16
  scenario_start 16
  scenario_summary 1
  session_end    1
  session_start  1
  tool_call      7

## overall tool_call stats
  calls:        7
  ok:           7
  errors:       0
  success_rate: 100.00%
  duration_ms:  avg=20.8  p50=23.1  p95=30.8  p99=30.8  total=145.9

## per-tool
  tool                           calls      ok     err     rate    avg_ms    p95_ms
  notion_search                      3       3       0  100.0%      26.5      30.8
  obsidian_search                    4       4       0  100.0%      16.6      28.8

────────────────────────────────────────────────────────────────
 9/11  Self-healing triage engine (Peter Pang / CREAO pattern, local-first)
────────────────────────────────────────────────────────────────

── mnemosyne-triage scan --window-days 30  (reads events.jsonl from our demo runs)
Mnemosyne health — grade D
  window:      30d
  runs:        8
  events:      336
  errors:      69
  identity slip rate:  0.0 per 1000 events
  tool failure rate:   0.0 per 1000 tool_calls
  clusters:    1

  top 1 clusters:
    [ 61.2]  scenario_end            -                   -  (n=69, runs=8)

  report written: /tmp/mnemo-demo-vOf55z/projects/health/2026-04-15.md

── Daily health report was written to:
  2026-04-15.md

── First 20 lines of the report:
  # Mnemosyne health report — 2026-04-15
  
  **Grade: D**  ·  window: 30d  ·  runs: 8  ·  events: 336  ·  generated: 2026-04-15T00:57:59.815193Z
  
  ## Headline metrics
  
  - Error events:           69
  - Identity-slip rate:     0.0 per 1000 events
  - Tool-failure rate:      0.0 per 1000 tool_calls
  - Distinct clusters:      1
  
  ## Top 1 clusters (by severity)
  
  ### cluster `14aeda1c0b83` — severity 61.2
  
  - event_type: `scenario_end`
  - tool: `-`
  - error_type: `-`
  - count: 69  ·  runs: 8
  - first seen: `2026-04-15T00:57:51.209913Z`

────────────────────────────────────────────────────────────────
 10/11  Live dashboard (single frame via --once --plain)
────────────────────────────────────────────────────────────────
Mnemosyne dashboard   2026-04-15T00:57:59+00:00
$PROJECTS_DIR: /tmp/mnemo-demo-vOf55z/projects
────────────────────────────────────────────────────────────────
Ollama: not reachable at http://localhost:11434
Experiments: 8 runs, 292K on disk
────────────────────────────────────────────────────────────────
Last 5 runs:
  run_20260415-005758-mode-gemma4-e-retr-15-temp-05  [completed]  gemma4:e4b          2026-04-15 00:57:58  events=42     tags=sweep,demo,example
  run_20260415-005757-mode-gemma4-e-retr-15-temp-00  [completed]  gemma4:e4b          2026-04-15 00:57:57  events=42     tags=sweep,demo,example
  run_20260415-005756-mode-gemma4-e-retr-5-temp-05  [completed]  gemma4:e4b          2026-04-15 00:57:56  events=42     tags=sweep,demo,example
  run_20260415-005755-mode-gemma4-e-retr-5-temp-00  [completed]  gemma4:e4b          2026-04-15 00:57:55  events=42     tags=sweep,demo,example
  run_20260415-005754-mode-qwen3-8b-retr-15-temp-05  [completed]  qwen3:8b            2026-04-15 00:57:54  events=42     tags=sweep,demo,example
────────────────────────────────────────────────────────────────
Memory:
  {
    "total": 4,
    "by_tier": {
      "L1_hot": 2,
      "L2_warm": 1,
      "L3_cold": 1
    },
    "by_kind": {
      "fact": 1,
      "preference": 2,
      "project": 1
    },
    "fts5_enabled": true,
    "db_path": "/tmp/mnemo-demo-vOf55z/projects/memory.db",
    "schema_version": 1
  }
────────────────────────────────────────────────────────────────
Recent events (latest run):
  scenario_end    -                        51.9ms  ok
  scenario_start  -                              -  ok
  scenario_end    -                        51.9ms  error
  scenario_summary  -                              -  ok
  session_end     -                              -  ok
────────────────────────────────────────────────────────────────
Disk: /dev/vda        252G  7.3G   30G  20% /

────────────────────────────────────────────────────────────────
 11/11  Test suite
────────────────────────────────────────────────────────────────

── bash test-harness.sh (integration)

────────────────────────────────────────────────
  ✓ 29 checks passed, 0 failed
────────────────────────────────────────────────

── python3 tests/test_all.py (unit)

106/106 tests passed in 1.17s

────────────────────────────────────────────────────────────────
 Demo complete.
────────────────────────────────────────────────────────────────

All 11 sections exercised. Identity lock holds across slip attempts.
Triage engine clusters real events from the demo runs into a grade.
Full pipeline produces real experiments in the fake PROJECTS_DIR and
the CLI tools read them back without sys.path shims. All tests pass.

Re-run this demo anytime with: bash demo.sh
Transcript regenerated with:   bash demo.sh > docs/DEMO.md 2>&1
```
