"""
mnemosyne_resolver.py — audit the routing layer of an agent system.

Inspired by Garry Tan's "Resolvers: The Routing Table for Intelligence"
(2026) — but kept narrow. The article identifies three real engineering
gaps in agent systems at scale:

  1. Skills exist but aren't reachable from the routing layer
  2. Skill descriptions are too vague to win against siblings
  3. AGENTS.md / TOOLS.md reference skills that don't exist (or vice versa)

This module is the audit that catches all three. It is read-only,
stdlib-only, and produces a structured report that humans (or
mnemosyne-proposer) can act on.

What this is NOT
----------------
A separate routing-table file. We're skill-registry-first; a parallel
RESOLVER.md would create two-source-of-truth problems. The registry's
own `description` field is the resolver — this audit checks that it's
strong enough to do its job.

CLI
---
    mnemosyne-resolver check                          # full audit
    mnemosyne-resolver check --json                   # machine output
    mnemosyne-resolver check --include-builtins=no    # only user skills

Output: a list of `Issue` objects with severity (info | warn | error)
and a one-line fix hint.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")


def _default_projects_dir() -> Path:
    try:
        from mnemosyne_config import default_projects_dir
        return default_projects_dir()
    except ImportError:
        import os
        raw = os.environ.get("MNEMOSYNE_PROJECTS_DIR", "").strip()
        return (Path(raw).expanduser().resolve()
                if raw else (Path.home() / "projects" / "mnemosyne").resolve())


# ---- distinguishability scoring --------------------------------------------

def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _hashed_vector(tokens: set[str], dim: int = 128) -> list[float]:
    """Tiny hashed bag-of-words vector for cheap cosine similarity.
    Mirrors mnemosyne_embeddings.HashedBowEmbedder but local so the
    audit has no dependency on the embeddings module."""
    vec = [0.0] * dim
    for tok in tokens:
        h = hashlib.md5(tok.encode("utf-8")).digest()
        bin_idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[bin_idx] += sign
    n = math.sqrt(sum(v * v for v in vec))
    if n == 0:
        return vec
    return [v / n for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---- audit issue model -----------------------------------------------------

SEVERITIES = ("info", "warn", "error")


@dataclass
class Issue:
    skill: str
    severity: str         # info | warn | error
    code: str             # short stable identifier
    message: str          # human-readable
    fix_hint: str = ""    # one-line suggested action

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResolverReport:
    skills_audited: int
    issues: list[Issue] = field(default_factory=list)
    counts_by_severity: dict[str, int] = field(default_factory=dict)
    distinguishability_pairs: list[dict[str, Any]] = field(default_factory=list)
    agents_md_gaps: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills_audited": self.skills_audited,
            "issues": [i.to_dict() for i in self.issues],
            "counts_by_severity": self.counts_by_severity,
            "distinguishability_pairs": self.distinguishability_pairs,
            "agents_md_gaps": self.agents_md_gaps,
        }


# ---- audit checks ----------------------------------------------------------

# A description shorter than this can't realistically distinguish a
# skill from siblings. Tunable via check_resolvable(min_description_chars=).
MIN_DESC_CHARS = 24

# Two skills with cosine similarity above this are likely to confuse
# the model — flag the pair so the user can sharpen one of them.
SIMILARITY_THRESHOLD = 0.85


def _check_description_quality(skills: list[Any],
                                 min_chars: int) -> list[Issue]:
    out: list[Issue] = []
    for sk in skills:
        desc = (sk.description or "").strip()
        if not desc:
            out.append(Issue(
                skill=sk.name, severity="error", code="DESC_EMPTY",
                message="skill has no description; the model cannot pick it",
                fix_hint=f"add a description to skill {sk.name!r} explaining "
                          f"when it should be invoked",
            ))
        elif len(desc) < min_chars:
            out.append(Issue(
                skill=sk.name, severity="warn", code="DESC_TOO_SHORT",
                message=f"description is {len(desc)} chars (< {min_chars} "
                         "minimum) — likely too vague to win routing",
                fix_hint="extend the description with concrete trigger "
                          "phrasings the user might say",
            ))
    return out


def _check_distinguishability(skills: list[Any]) -> tuple[list[Issue],
                                                              list[dict[str, Any]]]:
    """Compute pairwise cosine similarity over hashed-BOW description
    vectors. Skills above SIMILARITY_THRESHOLD are flagged because the
    model cannot reliably distinguish them at inference."""
    issues: list[Issue] = []
    pairs: list[dict[str, Any]] = []
    if len(skills) < 2:
        return issues, pairs

    vecs = [
        _hashed_vector(_tokens(sk.description or ""))
        for sk in skills
    ]
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            sim = _cosine(vecs[i], vecs[j])
            if sim >= SIMILARITY_THRESHOLD:
                a = skills[i]; b = skills[j]
                pairs.append({
                    "a": a.name, "b": b.name,
                    "similarity": round(sim, 4),
                })
                issues.append(Issue(
                    skill=a.name, severity="warn", code="DESC_AMBIGUOUS",
                    message=(f"description overlaps strongly with "
                             f"{b.name!r} (cosine={sim:.2f}); model may "
                             "pick the wrong one"),
                    fix_hint=(f"sharpen one description so the unique "
                              f"behavior of {a.name!r} vs. {b.name!r} is "
                              "stated explicitly"),
                ))
    return issues, pairs


def _check_runnable(skills: list[Any]) -> list[Issue]:
    """A skill exposed to the model must be invokable. Knowledge-only
    skills are fine (they're not in the OpenAI tools list); subprocess
    skills must have a command; python skills must have a callable."""
    out: list[Issue] = []
    for sk in skills:
        inv = sk.invocation
        if inv == "python" and sk.callable is None:
            out.append(Issue(
                skill=sk.name, severity="error", code="NO_CALLABLE",
                message="declared as python skill but has no callable",
                fix_hint="register the skill via SkillRegistry."
                          "register_python(...)",
            ))
        elif inv == "subprocess" and not sk.command:
            out.append(Issue(
                skill=sk.name, severity="error", code="NO_COMMAND",
                message="declared as subprocess skill but has no command",
                fix_hint="add a `command:` line to the skill's frontmatter",
            ))
    return out


def _check_unique_names(skills: list[Any]) -> list[Issue]:
    out: list[Issue] = []
    seen: dict[str, str] = {}
    for sk in skills:
        if sk.name in seen:
            out.append(Issue(
                skill=sk.name, severity="error", code="NAME_COLLISION",
                message=f"name collides with another skill from "
                         f"{seen[sk.name]!r}",
                fix_hint="rename one of the skills",
            ))
        else:
            seen[sk.name] = (str(sk.source_path) if sk.source_path
                              else f"<{sk.invocation}>")
    return out


# ---- AGENTS.md / TOOLS.md cross-check --------------------------------------

_AGENT_DOC_NAMES = ("AGENTS.md", "TOOLS.md")


def _read_user_docs(projects_dir: Path) -> str:
    """Concatenate user-editable agent docs into one string."""
    chunks: list[str] = []
    for fname in _AGENT_DOC_NAMES:
        p = projects_dir / fname
        if p.is_file():
            try:
                chunks.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n\n".join(chunks)


def _check_agents_md(skills: list[Any], doc_text: str) -> list[str]:
    """Find skill names mentioned in AGENTS.md/TOOLS.md that don't
    correspond to any registered skill. Reverse direction (registered
    but not mentioned) is fine — descriptions handle it."""
    if not doc_text:
        return []
    registered = {sk.name for sk in skills}
    # Look for word-boundary mentions of common skill-name patterns
    # (snake_case, kebab-case, dotted). Conservative: only flag names
    # that look "skill-y" — at least one underscore/hyphen, or
    # explicitly inside backticks.
    candidates: set[str] = set()
    for m in re.finditer(r"`([a-z][a-z0-9_\-]{2,})`", doc_text):
        candidates.add(m.group(1))
    return sorted([c for c in candidates
                    if c not in registered
                    and ("_" in c or "-" in c)])


# ---- main entry point ------------------------------------------------------

def check_resolvable(
    *,
    registry: Any | None = None,
    projects_dir: Path | None = None,
    include_builtins: bool = True,
    min_description_chars: int = MIN_DESC_CHARS,
) -> ResolverReport:
    """Audit the routing layer. Read-only.

    `registry` defaults to `mnemosyne_skills.default_registry()` so the
    audit catches everything the brain would actually see.
    """
    if registry is None:
        from mnemosyne_skills import default_registry
        registry = default_registry(load_builtins=include_builtins,
                                      discover_commands=False,
                                      load_learned=False,
                                      projects_dir=projects_dir)
    skills = list(registry.all())

    issues: list[Issue] = []
    issues.extend(_check_description_quality(skills, min_description_chars))
    issues.extend(_check_runnable(skills))
    issues.extend(_check_unique_names(skills))

    # Distinguishability is restricted to skills the model actually
    # picks between — knowledge-only skills aren't on the tools list.
    pickable = [sk for sk in skills
                if sk.invocation in ("python", "subprocess")]
    dup_issues, pairs = _check_distinguishability(pickable)
    issues.extend(dup_issues)

    # AGENTS.md cross-check
    pd = projects_dir or _default_projects_dir()
    doc_text = _read_user_docs(pd)
    gaps = _check_agents_md(skills, doc_text)
    for name in gaps:
        issues.append(Issue(
            skill=name, severity="warn", code="AGENTS_MD_GHOST",
            message=(f"{name!r} is mentioned in AGENTS.md/TOOLS.md but "
                     "no skill of that name is registered"),
            fix_hint=("either implement the skill or remove the "
                      "reference from the doc"),
        ))

    # Summary counts
    counts: dict[str, int] = {s: 0 for s in SEVERITIES}
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1

    return ResolverReport(
        skills_audited=len(skills),
        issues=issues,
        counts_by_severity=counts,
        distinguishability_pairs=pairs,
        agents_md_gaps=gaps,
    )


# ---- formatting ------------------------------------------------------------

_SEV_COLOR = {
    "error": "\033[1;31m",  # bold red
    "warn":  "\033[1;33m",  # bold yellow
    "info":  "\033[1;36m",  # bold cyan
}
_RESET = "\033[0m"


def format_text(report: ResolverReport, *, color: bool = True) -> str:
    out: list[str] = []
    out.append(f"resolver: {report.skills_audited} skills audited, "
                f"{len(report.issues)} issues")
    for sev in SEVERITIES:
        n = report.counts_by_severity.get(sev, 0)
        if n:
            out.append(f"  {sev}: {n}")
    out.append("")

    for issue in report.issues:
        col = _SEV_COLOR.get(issue.severity, "") if color else ""
        rst = _RESET if color else ""
        out.append(f"  [{col}{issue.severity}{rst}] {issue.skill:<28} "
                    f"{issue.code:<18} {issue.message}")
        if issue.fix_hint:
            out.append(f"       fix: {issue.fix_hint}")
    if report.distinguishability_pairs:
        out.append("")
        out.append(f"distinguishability pairs (cosine ≥ {SIMILARITY_THRESHOLD}):")
        for p in report.distinguishability_pairs:
            out.append(f"  {p['a']:<28} ↔ {p['b']:<28} {p['similarity']}")
    return "\n".join(out)


# ---- CLI -------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mnemosyne-resolver",
        description="Audit the agent's routing layer. Reports skills the "
                    "model can't pick (vague descriptions), skills it "
                    "would confuse with siblings, ghost references in "
                    "AGENTS.md, and skills that aren't actually runnable.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    cp = sub.add_parser("check", help="run the full resolver audit")
    cp.add_argument("--projects-dir")
    cp.add_argument("--include-builtins", default="yes",
                     choices=["yes", "no"])
    cp.add_argument("--min-description-chars", type=int,
                     default=MIN_DESC_CHARS)
    cp.add_argument("--json", action="store_true")
    cp.add_argument("--strict", action="store_true",
                     help="exit non-zero on warnings as well as errors")

    args = p.parse_args(argv)
    pd = Path(args.projects_dir).expanduser() if args.projects_dir else None

    if args.cmd == "check":
        report = check_resolvable(
            projects_dir=pd,
            include_builtins=(args.include_builtins == "yes"),
            min_description_chars=args.min_description_chars,
        )
        if args.json:
            json.dump(report.to_dict(), sys.stdout, indent=2, default=str)
            print()
        else:
            print(format_text(report, color=sys.stdout.isatty()))

        if report.has_errors:
            return 2
        if args.strict and report.counts_by_severity.get("warn", 0):
            return 1
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_main())
