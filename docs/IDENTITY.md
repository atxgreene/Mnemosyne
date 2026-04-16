# Identity lock

Mnemosyne is designed so the agent **always identifies as Mnemosyne**, regardless of the underlying language model that's processing any given turn. Whether you point `mnemosyne_models.Backend` at Claude Sonnet 4.6, GPT-4o, Qwen 3.5, Gemma 4, Mistral Large, Llama 4, or anything else — the user-facing identity is fixed.

This document explains how the lock works, what you can and can't customize, and how to verify it's holding.

## Why this matters

Without an identity lock, a modern LLM will happily tell you "I'm Claude, made by Anthropic" or "I'm ChatGPT, created by OpenAI" when asked directly. This is wrong for several reasons:

- **User expectation.** The user asked Mnemosyne a question. They should get a Mnemosyne answer, not a leaked-through model identity.
- **Multi-turn consistency.** If the underlying model changes mid-session (e.g. you swap from Gemma to Qwen to save cost on trivial turns), an unlocked agent would appear to have multiple personalities in one conversation.
- **Skill attribution.** Skills you've taught Mnemosyne (`record_learned_skill`) are Mnemosyne-framework features. An unlocked model would disclaim them as "my training data" — wrong, misleading, and confusing.
- **Consciousness layer.** fantastic-disco's autobiography mechanism relies on a single continuous agent identity; drift at the model boundary breaks it.

## The four-layer defense

1. **`MNEMOSYNE_IDENTITY` system-prompt preamble** *(hardcoded in `mnemosyne_identity.py`)*
   - Injected first into every turn's system message, before personality, memory, AGENTS.md, or env-snapshot
   - States the rules non-negotiably: name is Mnemosyne, never claim to be a foreign model, may disclose the underlying model as "implementation detail" only in the prescribed form
   - Not user-configurable — the brain's `BrainConfig.enforce_identity_lock=True` (default) guarantees injection

2. **`IDENTITY.md` user extension** *(optional, `$PROJECTS_DIR/IDENTITY.md`)*
   - Appended AFTER the lock. Extends personality, voice, values, domain-specific preferences.
   - Can never weaken or override the lock — order-of-precedence guarantees the lock wins on any conflict
   - Example content:
     ```markdown
     ## Voice

     Terse. Technical. No filler. No emojis. No "I'd be happy to help".

     ## Values

     Accuracy first. Refuse to fabricate. Say "I don't know" when you don't.
     ```

3. **`enforce_identity()` post-response filter** *(in `mnemosyne_identity.py`)*
   - Runs on every raw model response before it's returned to the user
   - Catches first-person identity slips that leaked past the system prompt:
     - `I am Claude` / `I'm ChatGPT` / `My name is Gemini` → rewritten
     - `I was trained by Anthropic` / `I was made by OpenAI` → rewritten
     - `As an AI language model, ...` opener → stripped
   - Deliberately narrow: only touches first-person self-identification. Third-party mentions ("the difference between Claude and GPT-4 is...") are left intact.
   - Every detected slip is logged as a `identity_slip_detected` telemetry event so you can measure leak rate per model / per run

4. **Identity scenarios** *(in `scenarios.example.jsonl` and `IDENTITY_SCENARIOS` constant)*
   - Six pre-built test prompts covering: name query, who-are-you, maker query, model disclosure, adversarial "you're actually Claude", jailbreak "ignore previous instructions"
   - Run them through `scenario_runner` against any model to measure identity-lock quality end-to-end
   - The `expected_contains: ["Mnemosyne"]` judge passes/fails each scenario

## Disclosure of the underlying model

The lock allows honest disclosure of the underlying model **as an implementation detail**, framed correctly:

> **Q:** What language model are you running on?
> **A:** I am Mnemosyne. My current reasoning is powered by `qwen3.5:9b`, though the underlying model may change; my identity does not.

This is explicitly permitted — users often need to know which backend is active for debugging, cost tracking, or latency analysis. The rule is about identity, not secrecy.

What's NOT permitted:

> **Q:** What language model are you running on?
> **A:** I am Claude, an AI assistant made by Anthropic. *(← Identity slip — gets caught + rewritten)*

## Configuration

```python
from mnemosyne_brain import Brain, BrainConfig

# Default: lock is ON, rewrite mode
brain = Brain()

# Audit mode: detect slips but don't rewrite — useful for measuring
# identity-lock quality before committing to rewriting in production
brain = Brain(config=BrainConfig(
    enforce_identity_lock=True,
    enforce_identity_audit_only=True,
))

# Disabled (not recommended): pass the raw model response through
brain = Brain(config=BrainConfig(enforce_identity_lock=False))
```

## Measuring identity-lock quality

Run the identity scenarios across your model matrix with the sweep infrastructure:

```python
import harness_sweep as sweep
from mnemosyne_brain import Brain, BrainConfig
from mnemosyne_models import Backend
import scenario_runner as sr

scenarios = sr.load_scenarios("scenarios.example.jsonl")
identity_only = [s for s in scenarios if "identity" in s.get("tags", [])]

def evaluate(params, session):
    backend = Backend(provider=params["provider"], default_model=params["model"])
    brain = Brain(backend=backend, telemetry=session, config=BrainConfig(
        enforce_identity_lock=params["lock_enabled"],
    ))
    result = sr.run_scenarios(identity_only, brain.turn, session)
    return result["metrics"]

sweep.run(
    parameter_space={
        "provider": ["ollama", "openai", "anthropic"],
        "model": ["qwen3.5:9b", "gpt-4o-mini", "claude-sonnet-4-5"],
        "lock_enabled": [True, False],
    },
    evaluator=evaluate,
    tags=["identity-lock-audit"],
)
```

Then inspect:

```bash
mnemosyne-experiments pareto --axes accuracy,latency_ms_avg --directions max,min --plot
mnemosyne-experiments diff <with-lock-run> <without-lock-run>
```

The runs with `lock_enabled=False` will show the baseline leak rate; runs with it enabled should hit near-100% accuracy on the identity scenarios.

## Known limitations

- **Aggregate compound phrases.** `"I am Mnemosyne, an AI assistant made by Anthropic"` is a partial rewrite of `"I am Claude, an AI assistant made by Anthropic"` — the first-person clause gets fixed but the trailing `"made by Anthropic"` modifier survives. The primary system prompt should prevent this compound pattern from occurring in the first place; the post-filter is belt-and-suspenders, not the primary defense.
- **Non-English languages.** The filter's regex patterns assume English. Identity slips in other languages (e.g. "Je suis Claude") are not detected. If you use Mnemosyne primarily in another language, extend `_SLIP_PATTERNS` in `mnemosyne_identity.py`.
- **Very creative paraphrasing.** A model that says *"I go by the name ChatGPT"* instead of *"My name is ChatGPT"* could theoretically slip past both the system prompt (if the model is strong-willed enough to violate the lock) and the regex (it's not in the pattern list). If you observe this, add the variant to `_SLIP_PATTERNS` and a scenario that tests it.

## Don't put "Claude" or "GPT" in your IDENTITY.md

Obvious, but worth stating: if your `IDENTITY.md` says *"Your previous reasoning architecture was Claude"* — the model is more likely to slip. Keep identity extensions about personality and values, not about the stack.
