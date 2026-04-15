# Mnemosyne

**A local-first, observable, identity-stable agent framework. Zero runtime deps. Stdlib only.**

![demo](docs/demo.gif)

One sentence: Mnemosyne is the substrate that makes local-first agent research cheap — ICMS 3-tier memory, 19 model backends, a Meta-Harness-aligned observability loop, and a 4-layer identity lock that holds whether the model inside is Qwen, Claude, GPT-4, or Gemini.

```sh
git clone https://github.com/atxgreene/sturdy-doodle.git && cd sturdy-doodle
pip install -e .
./demo.sh                                  # 18-section end-to-end walkthrough, no network
./demo-quick.sh                            # 45-second version (what the GIF shows)
```

## Quickstart (10 lines)

```python
from mnemosyne_brain import Brain, BrainConfig
from mnemosyne_memory import MemoryStore
from mnemosyne_models import Backend
from mnemosyne_skills import default_registry

backend = Backend(provider="ollama", default_model="qwen3.5:9b")   # or any of 19 providers
brain = Brain(
    config=BrainConfig(inner_dialogue_enabled=True, dreams_after_n_turns=50),
    memory=MemoryStore(),
    skills=default_registry(),
    backend=backend,
)
print(brain.turn("Plan a database migration for production.", metadata={"tags": ["hard"]}).text)
```

The brain handles memory retrieval, tool dispatch, identity enforcement, inner dialogue on tagged turns, and dream consolidation on cadence. Every action is logged as a telemetry event you can inspect with `mnemosyne-experiments`.

## What's in this repo

- **Agent core.** `mnemosyne_brain` (routing), `mnemosyne_memory` (SQLite + FTS5, L1/L2/L3), `mnemosyne_skills` (agentskills.io-compatible), `mnemosyne_models` (19 providers, streaming), `mnemosyne_identity` (4-layer lock).
- **Self-improvement loop.** `mnemosyne_triage` (error clustering + severity) → `mnemosyne_proposer` (rule-based change proposals) → `mnemosyne_apply` (execute + measure). Closed end-to-end.
- **Consciousness primitives.** `mnemosyne_dreams` (offline memory consolidation), `mnemosyne_inner` (Planner → Critic → Doer → Evaluator), `mnemosyne_goals` (persistent TODO across sessions).
- **Observability substrate.** `harness_telemetry` (raw events, secret redaction), `harness_sweep`, `scenario_runner`, `mnemosyne_experiments` (Pareto frontier, cost rollup).
- **Integrations.** `mnemosyne_mcp` (Model Context Protocol, both directions), `mnemosyne_embeddings` (optional sentence-transformers), `obsidian_search`, `notion_search`.
- **Daemon.** `mnemosyne_serve` — one long-running process that owns the memory store and runs dream / proposer / apply cron threads.
- **Deployment.** `install-mnemosyne.sh`, `mnemosyne-wizard.sh`, `validate-mnemosyne.sh`, `test-harness.sh`, `mnemosyne-dashboard.sh`.

## Read these next

- [`docs/ROADMAP.md`](./docs/ROADMAP.md) — what's shipped vs experimental vs research vs aspirational. Honest.
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — system design, four-layer stack, DeltaNet inflection point, why-this-over-Langfuse.
- [`docs/BENCHMARKS.md`](./docs/BENCHMARKS.md) — methodology + instrumentation-overhead reference numbers. Run it yourself.
- [`docs/LOCAL_MODELS.md`](./docs/LOCAL_MODELS.md) — context-window math + model choice guide for Ollama.
- [`docs/DEMO.md`](./docs/DEMO.md) — captured transcript of `./demo.sh`. Regenerate anytime.
- [`CHANGELOG.md`](./CHANGELOG.md) — version-by-version record.

## Verify anything on this page

```sh
pip install -e .
python3 tests/test_all.py          # all unit tests, <2s on laptops
bash test-harness.sh                # 29 end-to-end integration assertions
./demo.sh                           # 16-section captured transcript
./validate-mnemosyne.sh             # environment health check
```

## Deployment path

```sh
bash install-mnemosyne.sh        # clones eternal-context + fantastic-disco, builds venv, pip install -e .
bash mnemosyne-wizard.sh         # interactive .env setup: LLM / Telegram / Slack / Obsidian / Notion
bash validate-mnemosyne.sh       # confirm healthy
mnemosyne-serve &                # optional: long-running daemon with dream + proposer cron
```

After install, these commands are on `$PATH`:

```
mnemosyne-experiments   list / show / top-k / pareto / diff / events / aggregate / cost
mnemosyne-pipeline      observe → evaluate → sweep → compare → inspect in one shot
mnemosyne-memory        write / search / stats (CLI over MemoryStore)
mnemosyne-models        list / current / ping / info / pulled (CLI over the 19 backends)
mnemosyne-triage        scan / daily / weekly / show
mnemosyne-proposer      generate change proposals from triage clusters
mnemosyne-apply         execute accepted proposals and measure impact
mnemosyne-dreams        offline memory consolidation pass
mnemosyne-scengen       auto-generate regression scenarios from events.jsonl
mnemosyne-goals         manage the persistent goal stack
mnemosyne-mcp           serve skills as MCP tools or attach external MCP servers
mnemosyne-serve         long-running daemon
environment-snapshot    first-turn environment preamble
obsidian-search         search / read / list-recent against your vault
notion-search           same shape, backed by the Notion API
harness-telemetry       library smoke test
```

## Not AGI

Mnemosyne is engineering primitives for building usable local-first agents that are observable, tunable, and identity-stable. It is not a claim about emergent general intelligence. The path to AGI runs through the model inside the brain, the training objective, and the test-time compute budget — Mnemosyne makes it cheaper to experiment with whatever research finds, but it is not the path itself. See [`docs/ROADMAP.md`](./docs/ROADMAP.md) for the honest split.

## Security TL;DR

- `.env` lives outside both upstream repos (`~/projects/mnemosyne/.env`), mode `600`, created via `umask 077` (no TOCTOU).
- Channel tokens never appear in `argv`. Audited: 1125 `/proc/<pid>/cmdline` snapshots, zero leaks.
- No third-party shell installers beyond official Ollama.
- No telemetry, no callbacks, no auto-updates.

Full model in [`SETUP.md`](./SETUP.md#security-model).

## Companion repos

Cloned by `install-mnemosyne.sh`:

- [`atxgreene/eternal-context`](https://github.com/atxgreene/eternal-context) — base agent (ICMS, SDI, tool registry, channel adapters).
- [`atxgreene/fantastic-disco`](https://github.com/atxgreene/fantastic-disco) — `mnemosyne-consciousness` extensions (TurboQuant, metacognition, autobiography).

Override via `ETERNAL_REPO=` / `FANTASTIC_REPO=` / `FANTASTIC_BRANCH=` env vars to track a fork.

## Requirements

- WSL2 Ubuntu 24.04 or any Debian-ish Linux with `python3 >= 3.11`, `python3-venv`, `git`, `curl`
- ~10 GB free disk for the model + venv
- Optional: `whiptail` for the TUI wizard; `--text` mode works without it
- Optional: GPU passthrough for faster inference (CPU works; `CPU_TORCH=1` skips the ~2GB CUDA wheels)
