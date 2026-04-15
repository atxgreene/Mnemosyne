# Mnemosyne Roadmap

Honest account of what is shipped, what is research-grade, and what is
aspirational. No AGI claims. Capabilities are listed as **verifiable**
(covered by a test or a reproducible demo), **experimental** (lands in
main but the behavior can regress without a loud signal), or
**aspirational** (on the list, not yet implemented).

The yardstick is "could a stranger reproduce this with `git clone && pip
install -e . && ./demo.sh` on a fresh laptop?" If yes, it's verifiable.

---

## Shipped and verifiable

| Area | Module(s) | Test coverage |
|---|---|---|
| Telemetry (run + event model, secret redaction, FTS-friendly JSONL) | `harness_telemetry` | 15+ tests, integration via `test-harness.sh` |
| Parameter sweeps + Pareto analysis | `harness_sweep`, `mnemosyne_experiments` | 10+ tests, sweep_demo |
| Scenario runner with expectations DSL | `scenario_runner`, `scenarios.example.jsonl` | 8+ tests |
| Environment snapshot (first-turn context) | `environment_snapshot` | integration test |
| SQLite+FTS5 memory with L1/L2/L3 tiers | `mnemosyne_memory` | 12+ tests |
| Model backend (19 providers, stdlib-only) | `mnemosyne_models` | 8+ tests (mocked HTTP) |
| Skill registry (agentskills.io-compatible) | `mnemosyne_skills` | 10+ tests |
| 4-layer identity lock + audit mode | `mnemosyne_identity` | 15+ tests, demo rewrites "I am Claude" → "I am Mnemosyne" |
| Brain routing orchestrator | `mnemosyne_brain` | 12+ tests, mocked chat_fn |
| Local-model context adaptation | `mnemosyne_brain._maybe_adapt_to_context` | 3 tests |
| Triage / clustering + severity scoring | `mnemosyne_triage` | 8+ tests |
| **Meta-Harness proposer (rule-based v1)** | `mnemosyne_proposer` | 4 tests |
| **Dream consolidation (stdlib + optional LLM)** | `mnemosyne_dreams` | 4 tests |
| **Multi-persona inner dialogue** | `mnemosyne_inner` | 4 tests + 3 brain-integration tests |
| 9-command CLI entry point (via `pip install -e .`) | `pyproject.toml` | CI install-smoke |
| 6-phase GitHub Actions CI | `.github/workflows/ci.yml` | runs on every push |

Full test count: **122 unit tests** + shellcheck + pyflakes + install-smoke
+ triage-demo + end-to-end `demo.sh`.

---

## Shipped but experimental

These work in the happy path but haven't been pressure-tested at scale.

- **Dream consolidation with a live LLM summarizer.** The clustering and
  the stdlib fallback are verified. The model-backed summarizer is wired
  via `dreams.make_brain_summarizer(brain)` and honored by the brain's
  dream hook, but we do not yet measure whether the L2 abstracts
  actually improve downstream retrieval. That's the next A/B.
- **Proposer writes markdown proposals deterministically from triage
  clusters.** The rules cover 5 cluster shapes. Nothing *applies* a
  proposal automatically — by design. An applier is on the list (below).
- **Inner dialogue routing.** `should_deliberate` is a keyword+tag
  heuristic. It does not learn which turns benefit. Measuring this
  requires a scenario set that splits fairly between reasoning-heavy
  and reasoning-light turns; we don't have that yet.
- **19 model providers.** OpenAI-compatible + Anthropic native + Ollama
  native are exercised in unit tests via mocked HTTP. Cloud-provider
  end-to-end runs require credentials and are gated in CI.

---

## Research-grade, documented but not yet shipped

These are architectural directions that the codebase is *shaped to
accept* but which we have not implemented.

- **LLM-driven proposer.** The current `mnemosyne_proposer` uses hand-
  written rules. The Stanford Meta-Harness paper uses a coding agent
  (e.g. Claude Code) to generate proposal code directly. The filesystem
  interface (`PROP-NNNN-slug.md` with yaml frontmatter) is designed for
  drop-in replacement.
- **Closed-loop proposal apply + eval.** Proposals are human-reviewed
  today. A future `mnemosyne-apply` could execute an accepted proposal's
  change, re-run a scenario sweep, and mark the proposal
  `status: accepted` or `status: reverted` based on the Pareto delta.
- **Embedding-based memory clustering for dreams.** Current dream
  consolidation uses TF-IDF-ish token overlap. Swapping in sentence
  embeddings would tighten clusters. Interface is stable.
- **Inner-dialogue router.** A learned classifier over `(user_message,
  memory_context)` predicting whether inner dialogue improves accuracy.
  Needs a labeled scenario set first.
- **Hybrid attention backend.** Qwen 3.5's DeltaNet and Mamba-3 style
  models are supported as Ollama targets today, but the brain does not
  exploit the context-length advantage. A long-context scenario set
  would expose this.

---

## Aspirational (on the list, not yet scoped)

- **Behavioral coupling**: two Mnemosyne instances negotiating over a
  shared memory store. Needs a protocol spec before any code.
- **Dream-driven skill synthesis**: the dream loop proposes *new skill
  files* (not just summaries) when it detects a recurring procedural
  pattern. Overlap with the proposer loop — design needed.
- **Continuous identity-audit via statistical control charts**: treat
  identity slip rate as a process variable and raise alarms when it
  drifts, not just when the filter catches a single slip.
- **Federated personal agents**: an opinionated wire protocol so user
  A's Mnemosyne can query user B's Mnemosyne with consent tokens. This
  is a product decision, not a research one.

---

## What this project is NOT

- **Not AGI, not a path to AGI.** These are engineering primitives for
  building usable local-first agents that are observable, tunable, and
  identity-stable. They are not claims about emergent general
  intelligence.
- **Not a benchmarks-chaser.** We do not tune against SWE-bench,
  Terminal-Bench, or GAIA. The scenarios file is a smoke test, not a
  leaderboard submission.
- **Not a replacement for the frontier labs' SDKs.** Mnemosyne wraps
  those labs' APIs (OpenAI, Anthropic, Google, xAI, Mistral, Cohere)
  as one of 19 backends. It does not reimplement them. If you want
  the frontier, use the frontier; if you want local-first observability
  around the frontier, this is that layer.

---

## How to verify anything on this page

Every "shipped and verifiable" row has a corresponding test. To verify
locally:

```sh
git clone https://github.com/atxgreene/sturdy-doodle.git
cd sturdy-doodle
pip install -e .
python3 tests/test_all.py          # 122 unit tests, <2s on laptops
./demo.sh                           # end-to-end narrative demo
./validate-mnemosyne.sh             # environment health check
```

To reproduce the Meta-Harness-style loop locally, without real failures:

```sh
# Seed a tiny run with a synthetic identity slip
MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo \
  python3 -c "
import harness_telemetry as ht
rid = ht.create_run(model='test', tags=['roadmap-demo'])
with ht.TelemetrySession(rid) as sess:
    sess.log('identity_slip_detected', status='error',
             metadata={'slips': ['I am Claude'], 'count': 1})
ht.finalize_run(rid, metrics={})
"
# Triage it
MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo mnemosyne-triage scan --json
# Propose
MNEMOSYNE_PROJECTS_DIR=/tmp/mnemo-demo mnemosyne-proposer --min-severity 0
ls /tmp/mnemo-demo/proposals/
```

If these commands run on your machine and output proposals, the loop is
verified for you. If they don't, open an issue — the docs are the first
thing to fix.
