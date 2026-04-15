"""
mnemosyne_proposer.py — the Meta-Harness proposer loop, local-first.

Purpose
-------
Closes the loop that the Stanford Meta-Harness paper (Lee et al., 2026)
specifies but hasn't been shipped as a local-first tool. Reads triage
reports and telemetry history, identifies recurring failure patterns,
and writes HARNESS CHANGE PROPOSALS to $PROJECTS_DIR/proposals/.

What this is (honest)
---------------------
This is a DETERMINISTIC first implementation. It does not use an LLM to
generate proposals — it uses rules over the triage clusters to suggest
well-understood harness changes:

  - Recurring identity slips on model X → recommend lowering temperature
    or switching to `enforce_identity_audit_only=False` if off
  - Recurring tool failures for tool Y with error type E → propose
    adding a guard skill or a retry policy
  - Timeout patterns → propose raising the tool's timeout or adding a
    fallback path
  - Memory retrieval misses → propose raising memory_retrieval_limit
    or promoting specific L2 memories to L1
  - Scenario failures clustered by tag → propose writing a targeted
    skill (via record_learned_skill) to teach that capability

What this isn't (honest)
------------------------
This is NOT the full agentic proposer the Stanford paper describes. That
version uses Claude Code (or equivalent) to generate novel Python code
and has a closed-loop eval+accept mechanism. A future version of this
module can swap the rule engine for an LLM call against
mnemosyne_models.chat(); the filesystem interface is designed for that.

Where it fits in the loop
-------------------------
    triage → proposer → (human review OR automated apply) → sweep → measure

The proposer NEVER applies changes automatically. It writes markdown
proposals with a "status: pending" header. A human (or a separate apply
agent) is required to move status to "accepted" and implement the
change. This is deliberate: we ship the loop but keep humans in the
decision path for the first release.

Output
------
    $PROJECTS_DIR/proposals/
      PROP-0001-identity-slip-gemma4-e4b.md
      PROP-0002-tool-timeout-obsidian.md
      ...

Each proposal has:
  - yaml-ish frontmatter (id, created, status, severity, cluster_id)
  - "## Problem" section (cites triage cluster + events)
  - "## Proposal" section (specific harness change)
  - "## How to verify" section (sweep params + scenarios to rerun)
  - "## Risk" section (what could go wrong)

Proposals are deduplicated by cluster_id — if the same cluster surfaces
again tomorrow, the existing proposal is refreshed rather than duplicated.

Stdlib only. No LLM calls by default. Safe on cron.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# These imports are local-only to avoid forcing the proposer to load the
# full agent framework when it just wants to read reports.
try:
    from mnemosyne_triage import TriageReport, run_triage
    from mnemosyne_config import default_projects_dir
except ImportError:  # pragma: no cover — only hits when running from outside the package
    TriageReport = Any  # type: ignore[misc,assignment]

    def run_triage(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("mnemosyne_triage not importable")

    def default_projects_dir() -> Path:
        import os
        raw = os.environ.get("MNEMOSYNE_PROJECTS_DIR", "").strip()
        return Path(raw).expanduser().resolve() if raw else Path.home() / "projects" / "mnemosyne"


# ---- proposal model --------------------------------------------------------

@dataclass
class Proposal:
    id: str
    created_utc: str
    status: str              # pending | accepted | rejected | superseded
    severity: float
    cluster_id: str
    title: str
    category: str            # identity | tool | memory | prompt | skill | config
    problem: str
    proposal: str
    how_to_verify: str
    risk: str
    cites_events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        frontmatter = [
            "---",
            f"id: {self.id}",
            f"created_utc: {self.created_utc}",
            f"status: {self.status}",
            f"severity: {self.severity}",
            f"cluster_id: {self.cluster_id}",
            f"category: {self.category}",
            "---",
        ]
        body = [
            f"# {self.title}",
            "",
            "## Problem",
            "",
            self.problem,
            "",
            "## Proposal",
            "",
            self.proposal,
            "",
            "## How to verify",
            "",
            self.how_to_verify,
            "",
            "## Risk",
            "",
            self.risk,
            "",
        ]
        if self.cites_events:
            body.append("## Sample events")
            body.append("")
            body.append("```json")
            for ev in self.cites_events:
                body.append(json.dumps(ev, default=str, ensure_ascii=False))
            body.append("```")
            body.append("")
        return "\n".join(frontmatter + body)


# ---- rule engine -----------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _next_proposal_id(proposals_dir: Path) -> str:
    existing = sorted(proposals_dir.glob("PROP-*.md"))
    max_num = 0
    pat = re.compile(r"PROP-(\d{4})")
    for p in existing:
        m = pat.search(p.name)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"PROP-{max_num + 1:04d}"


def _find_existing_for_cluster(
    proposals_dir: Path, cluster_id: str
) -> Path | None:
    """Return the path of a prior proposal covering the same cluster, if any."""
    for p in proposals_dir.glob("PROP-*.md"):
        try:
            head = p.read_text(encoding="utf-8", errors="replace").splitlines()[:15]
        except OSError:
            continue
        for line in head:
            if line.startswith(f"cluster_id: {cluster_id}"):
                return p
    return None


def _proposals_for_cluster(cluster: dict[str, Any]) -> list[tuple[str, str, str, str, str, str]]:
    """Return a list of (title, category, problem, proposal, verify, risk) tuples
    derived from a single triage cluster via hand-written rules."""
    et = cluster.get("event_type", "")
    tool = cluster.get("tool")
    err = cluster.get("error_type")
    count = cluster.get("count", 0)
    sev = cluster.get("severity", 0.0)

    out: list[tuple[str, str, str, str, str, str]] = []

    if et == "identity_slip_detected":
        out.append((
            f"Identity slips recurring ({count} events, severity {sev})",
            "identity",
            f"The identity filter has caught {count} first-person slips to "
            f"foreign model names across recent runs. This means the model is "
            f"trying to self-identify as Claude/GPT/etc. despite the "
            f"MNEMOSYNE_IDENTITY system preamble.",
            "Two non-exclusive options:\n\n"
            "1. Lower `temperature` on the current Backend — high temperature "
            "correlates with prompt-obedience drift. Sweep [0.0, 0.2, 0.5] and "
            "compare slip rate.\n"
            "2. Extend `mnemosyne_identity._SLIP_PATTERNS` if new phrasings "
            "are appearing (check `Sample events` below for patterns not yet caught).\n"
            "3. If a specific model is the main offender per `affected_models`, "
            "switch to a different model with better prompt adherence for "
            "routing-heavy turns (Qwen 3.5, Gemma 4 tend to be strong).",
            "Run the 6 identity scenarios in `scenarios.example.jsonl` "
            "against the candidate configuration via `harness_sweep`. "
            "Target: `identity_slip_rate_per_1000 == 0` on the follow-up "
            "triage scan.",
            "Lowering temperature can reduce creative output quality on "
            "non-routing turns. Counter-measure: only lower temperature for "
            "turns tagged as routing/classification, keep default for "
            "generation turns.",
        ))

    if et == "tool_call" and err:
        out.append((
            f"Tool `{tool}` failing with `{err}` (×{count})",
            "tool",
            f"The `{tool}` tool has failed {count} times with `{err}` across "
            f"recent runs. Blast radius {sev}. Check sample events for "
            f"whether the cause is transient (network/timeout) or structural "
            f"(bad args, auth, schema).",
            f"If `{err}` is `TimeoutError` or `HTTPError`: add a retry-with-backoff "
            f"wrapper around the `{tool}` skill. If structural: update the skill's "
            f"parameter schema so the model is less likely to emit bad args, OR "
            f"add a guard skill that validates args before dispatching.\n\n"
            f"If the tool's return value is sometimes malformed: add a `result_schema` "
            f"check to the skill wrapper that retries or returns a typed error "
            f"the model can reason about.",
            "After implementing, re-run scenarios tagged with `tool_use` and the "
            "tool name. Track `tool_failure_rate_per_1000` in the next triage "
            "report — should drop to near zero for this cluster.",
            "Retries add latency and cost (real, for cloud backends). Put a "
            "hard cap (e.g., 2 retries) and exponential backoff with jitter. "
            "Don't retry non-idempotent operations.",
        ))

    if et == "session_error":
        out.append((
            f"Session-level errors ({count} events)",
            "config",
            f"The brain's session-level error path has fired {count} times. "
            f"This usually indicates a systemic problem (missing env var, "
            f"unreachable backend, file-system issue) rather than a "
            f"per-turn anomaly.",
            "Check `environment-snapshot` output on the affected host: is Ollama "
            "reachable? Are the credentials expected by the wizard present? "
            "Is `$PROJECTS_DIR` writable? If a specific model is involved "
            "(per `affected_models`), run `mnemosyne-models info <model>` and "
            "`mnemosyne-models ping`.",
            "After the fix: re-run `validate-mnemosyne.sh` locally to confirm "
            "the baseline health. Then run a small sweep with the same model "
            "and watch the triage report.",
            "Low. Fixing environment issues rarely regresses anything. Just be "
            "careful not to store raw credentials in version-controlled files.",
        ))

    if et == "scenario_end" and count >= 5:
        out.append((
            f"Scenario failures clustered ({count} events, severity {sev})",
            "skill",
            f"Scenarios are ending with `status=error` in a cluster of {count}. "
            f"Inspect the `Sample events` to see which scenario IDs are affected — "
            f"if they share a tag (e.g. `tool_use`, `safety`, `math`), the agent "
            f"has a skill gap in that category.",
            "Write a targeted skill to teach the capability:\n\n"
            "```python\n"
            "from mnemosyne_skills import record_learned_skill\n"
            "record_learned_skill(\n"
            "    name='<descriptive_name>',\n"
            "    description='<one-line description>',\n"
            "    command='<CLI invocation or python call>',\n"
            "    notes='Proposed by PROP-XXXX after N scenario failures clustered in category Y'\n"
            ")\n"
            "```\n\n"
            "Or, if the failure is about routing rather than missing capability: "
            "add instructions to `TOOLS.md` or `AGENTS.md` so the brain knows "
            "to reach for an existing skill.",
            "After adding the skill, rerun the failing scenarios via "
            "`scenario_runner.run_scenarios(...)`. Compare pass rate via "
            "`mnemosyne-experiments diff` against the pre-skill run.",
            "A bad skill spec can worsen routing: the model may try to use the "
            "new skill when the old path was better. Counter-measure: start "
            "with `enforce_identity_audit_only=True` and measure before enforcing.",
        ))

    if et == "model_call" and err:
        out.append((
            f"Model call failures with `{err}` (×{count})",
            "config",
            f"The model backend is returning `{err}` for {count} recent calls. "
            f"This points at transport, auth, or rate-limit issues rather than "
            f"prompt content.",
            "Check `mnemosyne-models current`: is the backend what you expected? "
            "If it's a cloud provider, verify the API key is set and not expired. "
            "If local, run `mnemosyne-models ping ollama` and "
            "`mnemosyne-models pulled` to confirm the model is actually available.\n\n"
            "If the error is a rate-limit (HTTP 429): add a token-bucket rate "
            "limiter in the backend. Not yet a first-class feature — open a "
            "follow-up task.",
            "After the fix, rerun the integration harness (`bash test-harness.sh`) "
            "and check that `model_call` errors drop to zero in the next triage.",
            "Rotating API keys can leak into logs if not careful. Use the wizard "
            "to update `.env` (atomic write, mode 600) rather than editing "
            "manually.",
        ))

    return out


def propose(
    report: Any | None = None,
    *,
    projects_dir: Path | None = None,
    window_days: int = 7,
    min_severity: float = 20.0,
    dry_run: bool = False,
) -> list[Proposal]:
    """Generate (or refresh) harness-change proposals from a triage report.

    Parameters
    ----------
    report : TriageReport | None
        If None, run triage fresh with `window_days`.
    projects_dir : Path | None
    window_days : int
    min_severity : float
        Clusters below this severity are ignored — noise reduction.
    dry_run : bool
        If True, compute proposals but don't write markdown files.

    Returns
    -------
    list[Proposal], newest first.
    """
    pd = projects_dir or default_projects_dir()
    proposals_dir = pd / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    if report is None:
        report = run_triage(projects_dir=pd, window_days=window_days)

    proposals: list[Proposal] = []

    for cluster in report.clusters:
        if cluster.get("severity", 0.0) < min_severity:
            continue

        specs = _proposals_for_cluster(cluster)
        for title, category, problem, suggestion, verify, risk in specs:
            existing = _find_existing_for_cluster(proposals_dir, cluster["cluster_id"])
            if existing and not dry_run:
                # Refresh the existing file (bump last-seen)
                text = existing.read_text(encoding="utf-8")
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if line.startswith("severity:"):
                        lines[i] = f"severity: {cluster['severity']}"
                    elif line.startswith("last_seen_utc:"):
                        lines[i] = f"last_seen_utc: {_utcnow()}"
                        break
                else:
                    # add the last_seen_utc line to frontmatter
                    for i, line in enumerate(lines):
                        if line == "---" and i > 0:
                            lines.insert(i, f"last_seen_utc: {_utcnow()}")
                            break
                existing.write_text("\n".join(lines), encoding="utf-8")
                continue

            pid = _next_proposal_id(proposals_dir)
            p = Proposal(
                id=pid,
                created_utc=_utcnow(),
                status="pending",
                severity=cluster["severity"],
                cluster_id=cluster["cluster_id"],
                title=title,
                category=category,
                problem=problem,
                proposal=suggestion,
                how_to_verify=verify,
                risk=risk,
                cites_events=cluster.get("sample_events") or [],
                metadata={"event_type": cluster.get("event_type"),
                           "tool": cluster.get("tool"),
                           "error_type": cluster.get("error_type")},
            )
            proposals.append(p)

            if not dry_run:
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
                out = proposals_dir / f"{pid}-{slug}.md"
                out.write_text(p.to_markdown(), encoding="utf-8")

    return proposals


# ---- CLI -------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="mnemosyne-proposer",
        description="Generate harness-change proposals from triage reports. "
                    "Local-first first-pass of the Meta-Harness proposer loop.",
    )
    p.add_argument("--projects-dir")
    p.add_argument("--window-days", type=int, default=7)
    p.add_argument("--min-severity", type=float, default=20.0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    pd = Path(args.projects_dir).expanduser() if args.projects_dir else None
    proposals = propose(
        projects_dir=pd,
        window_days=args.window_days,
        min_severity=args.min_severity,
        dry_run=args.dry_run,
    )

    if args.json:
        json.dump(
            [{"id": pr.id, "title": pr.title, "category": pr.category,
              "severity": pr.severity, "cluster_id": pr.cluster_id,
              "status": pr.status} for pr in proposals],
            sys.stdout, indent=2,
        )
        print()
        return 0

    if not proposals:
        print(f"No new proposals. (window={args.window_days}d, "
              f"min_severity={args.min_severity}, dry_run={args.dry_run})")
        return 0

    print(f"Generated {len(proposals)} proposal(s):")
    for pr in proposals:
        print(f"  [{pr.severity:>5.1f}] {pr.id} {pr.category:<8} {pr.title}")
    if not args.dry_run:
        print()
        print(f"Written to: {(pd or default_projects_dir()) / 'proposals'}/")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_main())
