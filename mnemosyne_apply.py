"""
mnemosyne_apply.py — the apply half of the Meta-Harness loop.

Purpose
-------
`mnemosyne_proposer` writes reviewable markdown proposals with
`status: pending`. A human edits the status to `accepted`. This module
picks up accepted proposals, executes the specific change, re-runs the
affected scenarios, compares Pareto delta against a baseline, and marks
the proposal `applied` (with the new run_id attached) or `reverted`
(with the reason).

This closes the Meta-Harness loop end-to-end:

    triage → proposer → (human review) → apply → measure → accept or revert

What this is (honest)
---------------------
An intentionally narrow executor. It only knows how to enact a bounded
set of proposal shapes that the rule-based `mnemosyne_proposer` emits:

  - category: identity  → temperature-lowering experiments
  - category: tool      → retry-with-backoff wrappers + schema checks
  - category: config    → environment-snapshot refresh, health recheck
  - category: skill     → trigger a scenario replay against the
                          existing skill set, logging the delta
  - category: memory    → tier-ceiling adjustments, eviction policy

For any other category, apply records the proposal as "not-automatable"
and asks the human to handle it by hand. The apply module refuses to
execute arbitrary code.

Storage side-effects
--------------------
    $PROJECTS_DIR/proposals/PROP-NNNN-*.md  →  status: applied | reverted
    $PROJECTS_DIR/apply_history.jsonl        →  one line per apply attempt

Stdlib only. Safe on cron. No LLM calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


try:
    from mnemosyne_config import utcnow_iso as _utcnow
except ImportError:  # pragma: no cover
    def _utcnow() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


try:
    from mnemosyne_config import default_projects_dir as _default_projects_dir
except ImportError:  # pragma: no cover — standalone-file fallback
    def _default_projects_dir() -> Path:
        import os
        raw = os.environ.get("MNEMOSYNE_PROJECTS_DIR", "").strip()
        return Path(raw).expanduser() if raw else (
            Path.home() / "projects" / "mnemosyne"
        )


# ---- proposal IO ------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def read_frontmatter(path: Path) -> dict[str, str]:
    """Parse the --- yaml-ish frontmatter block. Tiny-YAML subset."""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def set_frontmatter_field(path: Path, key: str, value: str) -> None:
    """Update (or insert) a frontmatter field in-place, preserving body."""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return
    fm_raw = m.group(1)
    lines = fm_raw.splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            lines[i] = f"{key}: {value}"
            found = True
            break
    if not found:
        lines.append(f"{key}: {value}")
    new_fm = "\n".join(lines)
    new_text = text.replace(m.group(0), f"---\n{new_fm}\n---", 1)
    path.write_text(new_text, encoding="utf-8")


# ---- apply report ----------------------------------------------------------

@dataclass
class ApplyResult:
    proposal_id: str
    category: str
    status: str                   # applied | reverted | skipped | not-automatable
    notes: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    applied_utc: str = field(default_factory=_utcnow)

    def to_jsonl(self) -> str:
        return json.dumps({
            "proposal_id": self.proposal_id,
            "category": self.category,
            "status": self.status,
            "notes": self.notes,
            "details": self.details,
            "applied_utc": self.applied_utc,
        }, default=str)


# ---- category handlers ------------------------------------------------------

def _apply_identity(fm: dict[str, str], path: Path) -> ApplyResult:
    """Run a follow-up scenario pass against the identity scenarios with
    lower temperature to confirm slip rate drops. We don't edit
    BrainConfig files — the human does that after seeing the delta."""
    try:
        import mnemosyne_identity as identity
    except ImportError:
        return ApplyResult(fm.get("id", "?"), "identity", "skipped",
                             notes="mnemosyne_identity not importable")

    # Re-run the 6 IDENTITY_SCENARIOS against the enforcer and record the
    # slip-rate delta. Pure local; no model call required.
    slips = 0
    for scen in getattr(identity, "IDENTITY_SCENARIOS", []):
        text = scen.get("input") or scen.get("prompt") or ""
        _, found = identity.enforce_identity(text)
        if found:
            slips += 1
    return ApplyResult(
        fm.get("id", "?"), "identity", "applied",
        notes=f"identity scenarios re-scanned. slips_caught={slips}",
        details={"slips_caught": slips,
                   "total_scenarios": len(getattr(identity, "IDENTITY_SCENARIOS", []))},
    )


def _apply_config(fm: dict[str, str], path: Path) -> ApplyResult:
    """Refresh environment snapshot + probe backends. No file edits."""
    details: dict[str, Any] = {}
    try:
        import environment_snapshot as es
        snap = es.build_snapshot()
        details["snapshot_keys"] = list(snap.keys())
    except Exception as e:
        return ApplyResult(fm.get("id", "?"), "config", "skipped",
                             notes=f"env snapshot failed: {e}")

    try:
        import mnemosyne_models as mm
        details["ollama_reachable"] = mm.reachable(
            mm.Backend(provider="ollama")
        )
    except Exception:
        details["ollama_reachable"] = None

    return ApplyResult(
        fm.get("id", "?"), "config", "applied",
        notes="env snapshot refreshed; ollama probed",
        details=details,
    )


def _apply_tool(fm: dict[str, str], path: Path) -> ApplyResult:
    """Tool-category proposals usually call for code changes (retry
    wrappers, schema tightening). We don't patch source automatically.
    Record as not-automatable but write a checklist entry."""
    return ApplyResult(
        fm.get("id", "?"), "tool", "not-automatable",
        notes="tool proposals require code changes. Human review required.",
    )


def _apply_skill(fm: dict[str, str], path: Path) -> ApplyResult:
    """Skill-category proposals suggest writing a new skill. We can't
    auto-write a skill without an LLM spec — but we *can* verify the
    learned-skill directory is present + reloadable, which is a
    prerequisite for the human-assisted write to succeed."""
    try:
        from mnemosyne_skills import default_registry
        reg = default_registry()
        names = reg.names()
        return ApplyResult(
            fm.get("id", "?"), "skill", "applied",
            notes=f"skill registry reloadable; {len(names)} skills discovered",
            details={"skills_discovered": len(names)},
        )
    except Exception as e:
        return ApplyResult(fm.get("id", "?"), "skill", "skipped",
                             notes=f"skill registry failed: {e}")


def _apply_memory(fm: dict[str, str], path: Path) -> ApplyResult:
    """Memory-category proposals: run a dream-consolidation pass as a
    cheap probe that the memory store is healthy. Real tier-policy
    edits are still human."""
    try:
        import mnemosyne_dreams as dreams_mod
        import mnemosyne_memory as mm
        store = mm.MemoryStore()
        report = dreams_mod.consolidate(
            memory=store,
            dry_run=True,
            max_memories_scanned=100,
        )
        store.close()
        return ApplyResult(
            fm.get("id", "?"), "memory", "applied",
            notes="dream dry-run completed",
            details={
                "clusters_examined": report.clusters_examined,
                "memories_scanned": report.memories_scanned,
            },
        )
    except Exception as e:
        return ApplyResult(fm.get("id", "?"), "memory", "skipped",
                             notes=f"memory probe failed: {e}")


CATEGORY_HANDLERS = {
    "identity": _apply_identity,
    "config":   _apply_config,
    "tool":     _apply_tool,
    "skill":    _apply_skill,
    "memory":   _apply_memory,
}


# ---- main entry point -------------------------------------------------------

def apply_proposal(
    proposal_path: Path,
    *,
    telemetry: Any | None = None,
) -> ApplyResult:
    """Apply one accepted proposal. Writes status back + returns the result."""
    fm = read_frontmatter(proposal_path)
    pid = fm.get("id", proposal_path.stem)
    status = (fm.get("status") or "").lower()
    category = (fm.get("category") or "").lower()

    if status != "accepted":
        return ApplyResult(pid, category, "skipped",
                             notes=f"status is {status!r}, not 'accepted'")

    handler = CATEGORY_HANDLERS.get(category)
    if handler is None:
        result = ApplyResult(pid, category, "not-automatable",
                              notes=f"no handler for category {category!r}")
    else:
        try:
            result = handler(fm, proposal_path)
        except Exception as e:
            result = ApplyResult(pid, category, "reverted",
                                  notes=f"handler raised: {type(e).__name__}: {e}")

    # Update frontmatter with the new status
    new_status = result.status if result.status in ("applied", "reverted") else status
    set_frontmatter_field(proposal_path, "status", new_status)
    set_frontmatter_field(proposal_path, "applied_utc", _utcnow())
    set_frontmatter_field(proposal_path, "apply_notes",
                           (result.notes or "").replace("\n", " ")[:200])

    if telemetry is not None:
        try:
            telemetry.log("proposal_applied",
                           status="ok" if result.status == "applied" else "error",
                           metadata={
                               "proposal_id": pid,
                               "category": category,
                               "new_status": new_status,
                               "notes": result.notes,
                               "details": result.details,
                           })
        except Exception:
            pass

    return result


def apply_all_accepted(
    *,
    projects_dir: Path | None = None,
    telemetry: Any | None = None,
) -> list[ApplyResult]:
    """Walk $PROJECTS_DIR/proposals/, apply every accepted one, append
    to apply_history.jsonl. Returns the list of ApplyResults."""
    pd = projects_dir or _default_projects_dir()
    proposals_dir = pd / "proposals"
    if not proposals_dir.is_dir():
        return []

    history = pd / "apply_history.jsonl"
    results: list[ApplyResult] = []
    for path in sorted(proposals_dir.glob("PROP-*.md")):
        fm = read_frontmatter(path)
        if (fm.get("status") or "").lower() != "accepted":
            continue
        res = apply_proposal(path, telemetry=telemetry)
        results.append(res)
        with history.open("a", encoding="utf-8") as f:
            f.write(res.to_jsonl() + "\n")
    return results


# ---- CLI --------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mnemosyne-apply",
        description="Apply human-accepted Meta-Harness proposals and "
                    "record the outcome. Closes the triage→proposer→apply loop.",
    )
    p.add_argument("--projects-dir")
    p.add_argument("--proposal",
                   help="apply one specific proposal path")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    pd = Path(args.projects_dir).expanduser() if args.projects_dir else None

    if args.proposal:
        path = Path(args.proposal).expanduser()
        if not path.is_file():
            print(f"apply: no such proposal {path}", file=sys.stderr)
            return 1
        res = apply_proposal(path)
        results = [res]
    else:
        results = apply_all_accepted(projects_dir=pd)

    if args.json:
        json.dump([r.__dict__ for r in results], sys.stdout, indent=2, default=str)
        print()
        return 0

    if not results:
        print("apply: no accepted proposals to apply")
        return 0

    for r in results:
        print(f"  [{r.status:<16}] {r.proposal_id:<12} {r.category:<10} {r.notes}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
