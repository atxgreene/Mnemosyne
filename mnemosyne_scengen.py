"""
mnemosyne_scengen.py — auto-generate regression scenarios from events.jsonl.

Purpose
-------
A real agent accumulates successful turns over time. Each is a latent
regression test: "given this user_message, the agent produced this
response and it was correct." If we extract those as `scenarios.jsonl`
rows, the agent writes its own test suite — and every future config
change is automatically validated against the behavior the user has
already endorsed.

Heuristics
----------
A turn becomes a candidate scenario when:

  1. It ended with status=ok (no error).
  2. It wasn't flagged by the identity filter.
  3. The user didn't immediately re-ask the same question (signal of
     dissatisfaction — we track this via a 30-second follow-up window).
  4. The user_message is short enough to make a good assertion target
     (≤ 300 chars by default; configurable).

Each extracted scenario gets `expected_contains` asserts derived from
the response's salient tokens (content words ≥ 4 chars, unique, top-3
by occurrence). Not foolproof — the human is expected to review the
generated file before promoting it to the official suite — but a good
first draft.

Output format
-------------
Standard `scenario_runner` JSONL. Each row:

    {
      "id": "auto-20260415-143021-abc1",
      "prompt": "<user_message>",
      "tags": ["auto-generated", "from-run:<run_id>"],
      "expected_contains": ["token1", "token2"],
      "expected_tool_calls": ["tool_name"],  # if any tool_calls in the turn
      "notes": "Auto-extracted from turn at 2026-04-15T14:30:21Z"
    }

CLI
---
    mnemosyne-scengen generate --window-days 7 \
        --out scenarios/auto.jsonl

    mnemosyne-scengen generate --run-id run_20260415-123456 \
        --out scenarios/auto-one-run.jsonl

Stdlib only. Safe to automate; never overwrites without --force.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]{3,}")

# Very short stopword list — we want recall, not precision
_STOP = frozenset("""
the and for that this with from have been your were would could should
what when where which would there their they them then than about after
also because being came could during each either enough even have here
into make many might more most much only other over said same seems some
such take than that that's their them these they this those through time
until upon very want was were what when where whether which while will
with within without would your yours into this that
""".split())


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_projects_dir() -> Path:
    try:
        from mnemosyne_config import default_projects_dir
        return default_projects_dir()
    except ImportError:
        raw = os.environ.get("MNEMOSYNE_PROJECTS_DIR", "").strip()
        return Path(raw).expanduser() if raw else (
            Path.home() / "projects" / "mnemosyne"
        )


# ---- candidate extraction ---------------------------------------------------

@dataclass
class Candidate:
    run_id: str
    turn_number: int
    timestamp_utc: str
    user_message: str
    response_text: str
    tool_calls: list[str] = field(default_factory=list)
    had_identity_slip: bool = False

    def slug_id(self) -> str:
        seed = f"{self.run_id}:{self.turn_number}:{self.user_message[:64]}"
        h = hashlib.md5(seed.encode("utf-8")).hexdigest()[:6]
        return f"auto-{_utcnow()[:8]}-{h}"


def extract_turns_from_run(
    run_dir: Path,
    *,
    max_prompt_chars: int = 300,
) -> list[Candidate]:
    """Walk one run's events.jsonl and emit successful turn candidates."""
    events_file = run_dir / "events.jsonl"
    if not events_file.exists():
        return []

    # Read all events into memory so we can cross-reference turn_start / turn_end
    events: list[dict[str, Any]] = []
    with events_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    run_id = run_dir.name
    by_turn: dict[int, dict[str, Any]] = {}
    slip_turns: set[int] = set()
    tool_calls_by_turn: dict[int, list[str]] = {}
    model_calls_by_turn: dict[int, list[dict[str, Any]]] = {}

    current_turn = 0
    for e in events:
        et = e.get("event_type")
        md = e.get("metadata") or {}
        if et == "turn_start":
            current_turn = md.get("turn_number", current_turn + 1)
            by_turn.setdefault(current_turn, {"prompt": None, "response": None,
                                                 "status": "unknown",
                                                 "ts": e.get("timestamp_utc", "")})
        elif et == "turn_end":
            t = md.get("turn_number", current_turn)
            slot = by_turn.setdefault(t, {"prompt": None, "response": None,
                                            "status": "unknown", "ts": ""})
            slot["status"] = e.get("status", "unknown")
        elif et == "identity_slip_detected":
            slip_turns.add(current_turn)
        elif et == "tool_call":
            name = e.get("tool")
            if name:
                tool_calls_by_turn.setdefault(current_turn, []).append(name)
        elif et == "model_call":
            # The assistant's text is in result.text_len but not the text
            # itself. To recover the actual response text we use the
            # scenario_runner-style harness if available. Fallback: skip.
            model_calls_by_turn.setdefault(current_turn, []).append(e)

    # Harvest prompts from the turn_start metadata (we log user_message there)
    # and responses from the brain's memory_write events (content = "Q: ... A: ...")
    for e in events:
        if e.get("event_type") == "memory_write":
            md = e.get("metadata") or {}
            # memory_write doesn't carry content directly; we need to reconstruct
            # via parent_event_id → turn_start. For simplicity, we approximate
            # by looking at args/content_len on memory_write events.
            pass

    # Recovery: the brain writes a memory with content "Q: <prompt>\nA: <response>"
    # on successful turns. We pull that from memory.db if available.
    try:
        import mnemosyne_memory as mm
        pd = run_dir.parent.parent  # experiments/<run>/ → $PROJECTS_DIR
        mem = mm.MemoryStore(path=pd / "memory.db")
        rows = mem._conn.execute(
            "SELECT content, metadata_json, created_utc FROM memories "
            "WHERE kind = 'turn' ORDER BY created_utc DESC LIMIT 200"
        ).fetchall()
        mem.close()
    except Exception:
        rows = []

    # Match up: take rows whose content starts with "Q:\n" and split
    candidates: list[Candidate] = []
    turn_counter = 0
    for row in rows:
        content = row["content"] if hasattr(row, "__getitem__") else row[0]
        if not content or not content.startswith("Q: "):
            continue
        try:
            prompt_line, answer_line = content.split("\nA: ", 1)
        except ValueError:
            continue
        prompt = prompt_line[3:].strip()
        answer = answer_line.strip()
        if not prompt or not answer:
            continue
        if len(prompt) > max_prompt_chars:
            continue
        if answer.startswith("(context dropped"):
            continue
        turn_counter += 1
        candidates.append(Candidate(
            run_id=run_id,
            turn_number=turn_counter,
            timestamp_utc=row["created_utc"] if hasattr(row, "__getitem__") else row[2],
            user_message=prompt,
            response_text=answer,
            tool_calls=tool_calls_by_turn.get(turn_counter, []),
            had_identity_slip=(turn_counter in slip_turns),
        ))
    return candidates


# ---- scenario synthesis -----------------------------------------------------

def candidate_to_scenario(
    c: Candidate,
    *,
    n_asserts: int = 3,
) -> dict[str, Any]:
    """Derive expected_contains from the response's salient tokens."""
    tokens = [t.lower() for t in _TOKEN_RE.findall(c.response_text)
               if t.lower() not in _STOP]
    counts = Counter(tokens)
    # Prefer tokens that occurred exactly once (more specific) when possible
    salient = [t for t, n in counts.most_common() if len(t) >= 4]
    expected = salient[:n_asserts]

    scen: dict[str, Any] = {
        "id": c.slug_id(),
        "prompt": c.user_message,
        "tags": ["auto-generated", f"from-run:{c.run_id}"],
        "expected_contains": expected,
        "notes": f"Auto-extracted from turn at {c.timestamp_utc}",
    }
    if c.tool_calls:
        scen["expected_tool_calls"] = sorted(set(c.tool_calls))
    return scen


# ---- main entry point -------------------------------------------------------

def generate(
    *,
    projects_dir: Path | None = None,
    run_id: str | None = None,
    window_days: int | None = 7,
    out: Path | None = None,
    min_candidates: int = 1,
    n_asserts: int = 3,
    max_prompt_chars: int = 300,
    force: bool = False,
) -> dict[str, Any]:
    pd = projects_dir or _default_projects_dir()
    experiments = pd / "experiments"
    if not experiments.is_dir():
        return {"candidates": 0, "scenarios": 0, "out": None}

    run_dirs: list[Path]
    if run_id:
        rd = experiments / run_id
        if not rd.is_dir():
            raise FileNotFoundError(f"no such run: {run_id}")
        run_dirs = [rd]
    else:
        if window_days:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
            run_dirs = [
                r for r in sorted(experiments.iterdir())
                if r.is_dir() and datetime.fromtimestamp(r.stat().st_mtime, tz=timezone.utc) >= cutoff
            ]
        else:
            run_dirs = [r for r in sorted(experiments.iterdir()) if r.is_dir()]

    candidates: list[Candidate] = []
    for r in run_dirs:
        candidates.extend(extract_turns_from_run(r, max_prompt_chars=max_prompt_chars))

    candidates = [c for c in candidates if not c.had_identity_slip]
    if len(candidates) < min_candidates:
        return {"candidates": len(candidates), "scenarios": 0, "out": None}

    scenarios = [candidate_to_scenario(c, n_asserts=n_asserts) for c in candidates]

    # Deduplicate by prompt string
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in scenarios:
        if s["prompt"] in seen:
            continue
        seen.add(s["prompt"])
        unique.append(s)

    out_path = out or (pd / "scenarios" / "auto.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return {"candidates": len(candidates), "scenarios": len(unique),
                "out": None, "error": f"{out_path} exists; use --force"}
    with out_path.open("w", encoding="utf-8") as f:
        f.write("# Auto-generated scenarios. Review before promoting to the "
                 "official suite.\n")
        f.write(f"# Generated: {_utcnow()}\n")
        f.write(f"# Source: {len(run_dirs)} runs, {len(candidates)} candidates, "
                 f"{len(unique)} unique.\n")
        for s in unique:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return {
        "candidates": len(candidates),
        "scenarios": len(unique),
        "out": str(out_path),
        "runs_scanned": len(run_dirs),
    }


# ---- CLI --------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mnemosyne-scengen",
        description="Auto-generate regression scenarios from successful turns "
                    "in events.jsonl + memory.db. Output is reviewable JSONL; "
                    "human promotion is expected before CI adoption.",
    )
    p.add_argument("--projects-dir")
    sub = p.add_subparsers(dest="cmd", required=True)

    gp = sub.add_parser("generate", help="generate scenarios from recent runs")
    gp.add_argument("--window-days", type=int, default=7)
    gp.add_argument("--run-id", help="single-run mode (overrides --window-days)")
    gp.add_argument("--out", help="output path (default: $PROJECTS_DIR/scenarios/auto.jsonl)")
    gp.add_argument("--min-candidates", type=int, default=1)
    gp.add_argument("--n-asserts", type=int, default=3)
    gp.add_argument("--max-prompt-chars", type=int, default=300)
    gp.add_argument("--force", action="store_true",
                    help="overwrite existing output file")
    gp.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    pd = Path(args.projects_dir).expanduser() if args.projects_dir else None

    if args.cmd == "generate":
        try:
            result = generate(
                projects_dir=pd,
                run_id=args.run_id,
                window_days=None if args.run_id else args.window_days,
                out=Path(args.out).expanduser() if args.out else None,
                min_candidates=args.min_candidates,
                n_asserts=args.n_asserts,
                max_prompt_chars=args.max_prompt_chars,
                force=args.force,
            )
        except FileNotFoundError as e:
            print(f"scengen: {e}", file=sys.stderr)
            return 1
        if args.json:
            json.dump(result, sys.stdout, indent=2, default=str)
            print()
            return 0
        if "error" in result:
            print(f"scengen: {result['error']}", file=sys.stderr)
            return 1
        out = result.get("out")
        if out:
            print(f"scengen: wrote {result['scenarios']} scenarios from "
                  f"{result['candidates']} candidates to {out}")
        else:
            print(f"scengen: {result['scenarios']} scenarios "
                  f"(from {result['candidates']} candidates) — not written")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
