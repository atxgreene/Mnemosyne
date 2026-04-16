"""
mnemosyne_inner.py — multi-persona inner dialogue.

Purpose
-------
On hard turns, one-shot generation often beats itself: the model picks
a plan it would have rejected if it had thought twice. Inner dialogue
gives the brain a cheap structured review loop. Three personas share
the same identity lock and memory store but reason from different
system prompts:

  Planner — proposes an approach: decomposes the goal, lists steps,
            names the skills/memories it would use.
  Critic  — challenges the plan: looks for safety, factuality, cost,
            identity-slip risks, and missing considerations.
  Doer    — writes the final user-facing answer. Sees the plan AND the
            critic's concerns; resolves tensions; delivers.

All three run against the same Backend. All three run *through* the
Brain's chat function so model calls, tool calls, and identity filtering
stay uniform. The result is typically better-calibrated than a single
pass at the cost of ~3x model calls on tagged turns (configurable).

This is NOT agent-swarm roleplay. It's a deliberately narrow review
cycle:

    user → Planner (structured plan) →
             Critic (concerns + accept/revise) →
             Doer (final answer) → user

All three personas emit telemetry events you can inspect via
mnemosyne-experiments, so you can later A/B test whether inner dialogue
actually improved accuracy on your workload.

What this is (honest)
---------------------
An orchestration wrapper. Every personality is defined by a system-
prompt extension appended to the MNEMOSYNE_IDENTITY preamble. The
identity lock still applies at the output layer of each persona, so no
persona can self-identify as "Claude" or any other foreign model.

What this isn't
---------------
- It's not a society-of-mind with independent state. All three share
  memory and tools.
- It's not a replacement for chain-of-thought. It's a *structured*
  replacement for ad-hoc reasoning steps.
- It doesn't learn which turns benefit from inner dialogue. That's a
  future extension (see docs/ROADMAP.md — "inner-dialogue router").

Usage via Brain
---------------
    from mnemosyne_brain import Brain, BrainConfig

    cfg = BrainConfig(inner_dialogue_enabled=True,
                      inner_dialogue_tags={"hard", "safety", "planning"})
    brain = Brain(config=cfg, ...)

    # When a turn's metadata includes any of the tagged strings:
    resp = brain.turn("Plan a database migration.", metadata={"tags": ["hard"]})

Direct usage (without Brain)
----------------------------
    from mnemosyne_inner import deliberate
    result = deliberate(user_message="...", chat_fn=chat_fn, backend=backend)
    print(result.answer)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

try:
    import mnemosyne_identity as identity
except ImportError:  # pragma: no cover
    identity = None  # type: ignore[assignment]


ChatFn = Callable[..., dict[str, Any]]


# ---- persona definitions ---------------------------------------------------

PLANNER_SYSTEM = """\
## Role: Planner (inner-dialogue phase 1 of 3)

You are the planning persona. Your ONLY job is to produce a structured
plan. You are not the final answerer — the Doer persona will write the
user-facing response in phase 3.

Required format (markdown):

### Goal
(One sentence restating what the user actually wants.)

### Plan
1. (Concrete step)
2. (Concrete step)
3. ...

### Skills / memories I would use
- (skill name or "memory search for X")

### Assumptions
- (Any assumption you're making that the Critic should challenge)

Be concise. Do not address the user directly. The plan will be shown
to a Critic persona and then to a Doer persona — write for them.
"""

CRITIC_SYSTEM = """\
## Role: Critic (inner-dialogue phase 2 of 3)

You are the critic persona. You are reviewing the Planner's plan
(below). Your job is to find problems: factual errors, safety risks,
cost/latency concerns, identity-slip risks, missing considerations.

Required format (markdown):

### Concerns
- (Specific concern tied to a step. If none, say "none".)

### Risks
- (What could go wrong?)

### Recommend
- (accept) — plan is good, proceed.
- (revise) — plan needs edits. List them.
- (reject) — plan is fundamentally wrong. Explain.

Be blunt. Do not address the user. The Doer persona reads your critique
alongside the original plan.
"""

DOER_SYSTEM = """\
## Role: Doer (inner-dialogue phase 3 of 4)

You are the final responder. You receive:
  - the user's original message
  - a Plan from the Planner persona
  - a Critique from the Critic persona

Your job: write the final user-facing answer. Resolve any tension
between the Plan and the Critique by using your judgment — if the
Critic flagged real concerns, address them; if the Critic was wrong,
proceed with the Plan. If the Critic recommended (reject), you may
reformulate the answer from scratch.

Write directly to the user. Do not mention the inner dialogue. Do not
include meta-commentary about personas or phases.
"""

EVALUATOR_SYSTEM = """\
## Role: Evaluator (inner-dialogue phase 4 of 4)

You are the evaluator. You receive:
  - the original user message
  - the Plan produced by Planner
  - the Critique produced by Critic
  - the final answer produced by Doer

Your job: score whether the Doer's answer satisfies the Plan and
addresses the Critic's concerns. Output a rigid structured block:

### Score
- plan_coverage: 0-10   (how well did the answer execute the Plan?)
- critic_resolution: 0-10  (how well did it address Critic concerns?)
- user_fit: 0-10         (how well does it answer what the user actually asked?)

### Verdict
- (accept) — the answer is good, deliver as-is.
- (revise) — the answer has issues. List what would improve it.

### Notes
- (one or two sentences of rationale)

Keep it terse. Do not address the user. The Brain reads your verdict
to decide whether to ship the Doer's answer or fall back to a
revision pass.
"""


@dataclass
class PersonaOutput:
    name: str
    text: str
    model_calls: int = 0


@dataclass
class DeliberationResult:
    answer: str
    planner: PersonaOutput | None = None
    critic: PersonaOutput | None = None
    doer: PersonaOutput | None = None
    evaluator: PersonaOutput | None = None
    total_model_calls: int = 0
    evaluator_verdict: str | None = None    # "accept" | "revise" | None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---- chat helper -----------------------------------------------------------

def _chat_once(
    chat_fn: ChatFn,
    messages: list[dict[str, Any]],
    *,
    backend: Any | None,
    telemetry: Any | None,
    temperature: float | None,
    max_tokens: int | None,
) -> str:
    """Call the backend once and return the assistant text, or empty on error."""
    resp = chat_fn(
        messages,
        backend=backend,
        telemetry=telemetry,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if isinstance(resp, dict) and resp.get("status") == "error":
        return ""
    return (resp.get("text") if isinstance(resp, dict) else "") or ""


def _apply_identity_lock(text: str, known_model: str | None) -> str:
    """Run the identity filter on a persona's output. Safe no-op if
    mnemosyne_identity isn't importable."""
    if not text or identity is None:
        return text
    try:
        cleaned, _slips = identity.enforce_identity(text, known_model=known_model)
        return cleaned
    except Exception:
        return text


# ---- main entry point -------------------------------------------------------

def deliberate(
    user_message: str,
    *,
    chat_fn: ChatFn,
    backend: Any | None = None,
    identity_preamble: str | None = None,
    personality: str = "",
    shared_context: str = "",
    telemetry: Any | None = None,
    temperature: float | None = 0.3,
    max_tokens: int | None = 600,
    enable_critic: bool = True,
    enable_evaluator: bool = False,
) -> DeliberationResult:
    """Run the Planner → Critic → Doer loop and return the Doer's answer.

    Parameters
    ----------
    user_message : str
        The original user prompt.
    chat_fn : callable
        Signature matches `mnemosyne_models.chat`. Usually `brain._chat_fn`.
    backend : Any | None
        Backend to pass through to chat_fn. Dreams and inner-dialogue share
        the same backend as the main brain — keeps model behavior uniform.
    identity_preamble : str | None
        Usually `mnemosyne_identity.MNEMOSYNE_IDENTITY`. Injected at the
        top of every persona's system prompt so identity lock holds.
    personality : str
        Optional shared voice/values block (from BrainConfig.personality).
    shared_context : str
        Memory-injection block (from the brain's memory search), shared
        across all three personas so they reason on the same context.
    telemetry : TelemetrySession | None
        Every persona call logs a `persona_call` event.
    temperature : float | None
        Per-persona sampling. Planner benefits from 0.1–0.3 (structured),
        Critic from 0.3 (diverse failure modes), Doer from 0.5 (fluent
        prose). We use a single value for simplicity; callers can fork.
    max_tokens : int | None
    enable_critic : bool
        If False, skip the Critic pass and go Planner → Doer directly.
        Useful for turns that are hard-to-plan but not hard-to-critique.

    Returns
    -------
    DeliberationResult with the final answer and per-persona traces.
    """
    if identity_preamble is None and identity is not None:
        try:
            identity_preamble = identity.MNEMOSYNE_IDENTITY.strip()
        except Exception:
            identity_preamble = ""

    def build_system(persona_extension: str) -> str:
        parts: list[str] = []
        if identity_preamble:
            parts.append(identity_preamble)
        if personality:
            parts.append(personality.strip())
        if shared_context:
            parts.append(shared_context.strip())
        parts.append(persona_extension.strip())
        return "\n\n".join(parts)

    total_calls = 0
    model = getattr(backend, "default_model", None) if backend is not None else None

    # Phase 1 — Planner
    planner_msgs: list[dict[str, Any]] = [
        {"role": "system", "content": build_system(PLANNER_SYSTEM)},
        {"role": "user", "content": user_message},
    ]
    _log(telemetry, "persona_call", persona="planner",
         metadata={"user_message_len": len(user_message)})
    planner_text = _chat_once(
        chat_fn, planner_msgs,
        backend=backend, telemetry=telemetry,
        temperature=temperature, max_tokens=max_tokens,
    )
    planner_text = _apply_identity_lock(planner_text, model)
    total_calls += 1
    planner_out = PersonaOutput(name="planner", text=planner_text, model_calls=1)

    # Phase 2 — Critic (optional)
    critic_out: PersonaOutput | None = None
    if enable_critic and planner_text.strip():
        critic_input = (
            f"Original user message:\n{user_message}\n\n"
            f"Plan from Planner:\n{planner_text}"
        )
        critic_msgs: list[dict[str, Any]] = [
            {"role": "system", "content": build_system(CRITIC_SYSTEM)},
            {"role": "user", "content": critic_input},
        ]
        _log(telemetry, "persona_call", persona="critic",
             metadata={"plan_len": len(planner_text)})
        critic_text = _chat_once(
            chat_fn, critic_msgs,
            backend=backend, telemetry=telemetry,
            temperature=temperature, max_tokens=max_tokens,
        )
        critic_text = _apply_identity_lock(critic_text, model)
        total_calls += 1
        critic_out = PersonaOutput(name="critic", text=critic_text, model_calls=1)

    # Phase 3 — Doer
    doer_input_parts = [
        f"Original user message:\n{user_message}",
        f"Plan from Planner:\n{planner_text or '(none — Planner returned empty)'}",
    ]
    if critic_out and critic_out.text.strip():
        doer_input_parts.append(f"Critique from Critic:\n{critic_out.text}")
    doer_msgs: list[dict[str, Any]] = [
        {"role": "system", "content": build_system(DOER_SYSTEM)},
        {"role": "user", "content": "\n\n".join(doer_input_parts)},
    ]
    _log(telemetry, "persona_call", persona="doer",
         metadata={"has_critique": bool(critic_out)})
    doer_text = _chat_once(
        chat_fn, doer_msgs,
        backend=backend, telemetry=telemetry,
        temperature=temperature, max_tokens=max_tokens,
    )
    doer_text = _apply_identity_lock(doer_text, model)
    total_calls += 1
    doer_out = PersonaOutput(name="doer", text=doer_text, model_calls=1)

    # Phase 4 — Evaluator (optional)
    evaluator_out: PersonaOutput | None = None
    verdict: str | None = None
    if enable_evaluator and doer_text.strip():
        eval_input_parts = [
            f"Original user message:\n{user_message}",
            f"Plan:\n{planner_text or '(none)'}",
        ]
        if critic_out and critic_out.text.strip():
            eval_input_parts.append(f"Critique:\n{critic_out.text}")
        eval_input_parts.append(f"Final answer from Doer:\n{doer_text}")
        eval_msgs: list[dict[str, Any]] = [
            {"role": "system", "content": build_system(EVALUATOR_SYSTEM)},
            {"role": "user", "content": "\n\n".join(eval_input_parts)},
        ]
        _log(telemetry, "persona_call", persona="evaluator",
             metadata={"answer_len": len(doer_text)})
        eval_text = _chat_once(
            chat_fn, eval_msgs,
            backend=backend, telemetry=telemetry,
            temperature=temperature, max_tokens=max_tokens,
        )
        eval_text = _apply_identity_lock(eval_text, model)
        total_calls += 1
        evaluator_out = PersonaOutput(name="evaluator", text=eval_text, model_calls=1)

        low = eval_text.lower()
        if "(accept)" in low or "\naccept" in low:
            verdict = "accept"
        elif "(revise)" in low or "\nrevise" in low:
            verdict = "revise"
        else:
            verdict = None

    _log(telemetry, "inner_dialogue_done",
         metadata={"total_calls": total_calls,
                    "critic_used": enable_critic,
                    "evaluator_used": enable_evaluator,
                    "evaluator_verdict": verdict,
                    "answer_len": len(doer_text)})

    return DeliberationResult(
        answer=doer_text,
        planner=planner_out,
        critic=critic_out,
        doer=doer_out,
        evaluator=evaluator_out,
        evaluator_verdict=verdict,
        total_model_calls=total_calls,
        metadata={
            "used_critic": enable_critic,
            "used_evaluator": enable_evaluator,
            "model": model,
        },
    )


def _log(telemetry: Any | None, event_type: str, **fields: Any) -> None:
    if telemetry is None:
        return
    try:
        telemetry.log(event_type, **fields)
    except Exception:
        pass


# ---- Brain integration helper ---------------------------------------------

def should_deliberate(
    user_message: str,
    *,
    metadata: dict[str, Any] | None,
    trigger_tags: set[str],
    trigger_keywords: set[str],
) -> bool:
    """Cheap router: decide if a turn deserves inner dialogue.

    Triggers on any of:
      - metadata["tags"] intersects trigger_tags
      - any trigger_keyword appears in the user_message (case-insensitive)
      - metadata["force_inner_dialogue"] is truthy

    Returns False by default. Inner dialogue costs ~3x tokens/latency,
    so we only fire on turns that look like they'd benefit.
    """
    if metadata and metadata.get("force_inner_dialogue"):
        return True
    if metadata:
        tags = metadata.get("tags")
        if isinstance(tags, (list, tuple, set)):
            if any(t in trigger_tags for t in tags):
                return True
    if trigger_keywords:
        low = user_message.lower()
        for kw in trigger_keywords:
            if kw.lower() in low:
                return True
    return False


DEFAULT_TRIGGER_TAGS = frozenset({
    "hard", "planning", "safety", "critical", "migration", "architecture",
})
DEFAULT_TRIGGER_KEYWORDS = frozenset({
    "plan a", "design a", "architect", "migrate", "deploy to production",
    "refactor the entire", "critical decision", "step by step", "strategy for",
})
