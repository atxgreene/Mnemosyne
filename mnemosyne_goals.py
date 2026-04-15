"""
mnemosyne_goals.py — persistent goal stack the agent maintains across sessions.

Purpose
-------
Most agents are reactive: they answer whatever the last user message is
and forget everything else. A goal stack makes the agent proactive: it
tracks open objectives across sessions and surfaces them at the
beginning of each new session so forward motion compounds.

Storage
-------
Goals live in `$PROJECTS_DIR/goals.jsonl`. Append-only; mutations (add,
resolve, reprioritize) rewrite the file atomically. Each line is a
single JSON object. Grep-navigable.

Usage
-----
    from mnemosyne_goals import GoalStack

    gs = GoalStack()
    g = gs.add(text="Finish the database migration plan", priority=2,
                tags=["work", "migration"])
    for g in gs.list_open():
        print(g.text)
    gs.resolve(g.id)

Brain integration
-----------------
The brain reads `goals.list_open()` on session start and injects the top
N goals into the first turn's system prompt so the model knows what's
open. When the brain believes a turn resolved a goal, it calls
`goals.resolve(...)`.

CLI
---
    mnemosyne-goals list
    mnemosyne-goals add 'Ship v0.2.0 docs'
    mnemosyne-goals resolve 7
    mnemosyne-goals top 5

Stdlib only. Safe to import from any Mnemosyne module.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


try:
    from mnemosyne_config import utcnow_iso as _utcnow
except ImportError:  # pragma: no cover
    def _utcnow() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _default_goals_path(projects_dir: Path | None = None) -> Path:
    if projects_dir is None:
        try:
            from mnemosyne_config import default_projects_dir
            projects_dir = default_projects_dir()
        except Exception:
            raw = os.environ.get("MNEMOSYNE_PROJECTS_DIR", "").strip()
            projects_dir = Path(raw).expanduser() if raw else (
                Path.home() / "projects" / "mnemosyne"
            )
    return projects_dir / "goals.jsonl"


@dataclass
class Goal:
    id: int
    text: str
    priority: int = 3                   # 1 = highest, 5 = lowest
    status: str = "open"                # open | resolved | abandoned
    created_utc: str = ""
    updated_utc: str = ""
    resolved_utc: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""


class GoalStack:
    """Append-only JSONL-backed goal list with atomic rewrites."""

    def __init__(self, *, path: Path | None = None,
                 projects_dir: Path | None = None) -> None:
        self.path = path or _default_goals_path(projects_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        self._lock = threading.Lock()

    # ---- io ----------------------------------------------------------------

    def _read_all(self) -> list[Goal]:
        out: list[Goal] = []
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    out.append(Goal(**{k: v for k, v in obj.items()
                                         if k in Goal.__dataclass_fields__}))
        except FileNotFoundError:
            pass
        return out

    def _write_all(self, goals: list[Goal]) -> None:
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for g in goals:
                f.write(json.dumps(asdict(g), default=str) + "\n")
        os.replace(tmp, self.path)

    # ---- mutations ---------------------------------------------------------

    def add(self, text: str, *, priority: int = 3,
            tags: list[str] | None = None, notes: str = "") -> Goal:
        with self._lock:
            goals = self._read_all()
            next_id = max((g.id for g in goals), default=0) + 1
            g = Goal(
                id=next_id, text=text, priority=max(1, min(5, int(priority))),
                created_utc=_utcnow(), updated_utc=_utcnow(),
                tags=tags or [], notes=notes,
            )
            goals.append(g)
            self._write_all(goals)
        return g

    def resolve(self, goal_id: int, *, notes: str = "") -> Goal | None:
        return self._update_status(goal_id, "resolved", notes=notes)

    def abandon(self, goal_id: int, *, notes: str = "") -> Goal | None:
        return self._update_status(goal_id, "abandoned", notes=notes)

    def reprioritize(self, goal_id: int, priority: int) -> Goal | None:
        with self._lock:
            goals = self._read_all()
            for g in goals:
                if g.id == goal_id:
                    g.priority = max(1, min(5, int(priority)))
                    g.updated_utc = _utcnow()
                    self._write_all(goals)
                    return g
        return None

    def _update_status(self, goal_id: int, status: str, *, notes: str) -> Goal | None:
        with self._lock:
            goals = self._read_all()
            for g in goals:
                if g.id == goal_id:
                    g.status = status
                    g.updated_utc = _utcnow()
                    if status != "open":
                        g.resolved_utc = _utcnow()
                    if notes:
                        g.notes = (g.notes + "\n" + notes).strip() if g.notes else notes
                    self._write_all(goals)
                    return g
        return None

    # ---- reads -------------------------------------------------------------

    def list_all(self) -> list[Goal]:
        return self._read_all()

    def list_open(self) -> list[Goal]:
        return sorted(
            (g for g in self._read_all() if g.status == "open"),
            key=lambda g: (g.priority, g.id),
        )

    def top(self, n: int = 5) -> list[Goal]:
        return self.list_open()[:n]

    def get(self, goal_id: int) -> Goal | None:
        for g in self._read_all():
            if g.id == goal_id:
                return g
        return None


# ---- brain injection -------------------------------------------------------

def goals_system_block(goals: list[Goal], limit: int = 5) -> str:
    """Return a system-prompt block listing the top N open goals.

    Used by the brain to inject the goal stack on first turn so the model
    knows what's in flight across sessions.
    """
    top = goals[:limit]
    if not top:
        return ""
    lines = ["## Open goals (across sessions)"]
    for g in top:
        tag_str = f" [{', '.join(g.tags)}]" if g.tags else ""
        lines.append(f"- (P{g.priority}) #{g.id}: {g.text}{tag_str}")
    lines.append(
        "\nIf the user's current request advances or completes one of these, "
        "say so explicitly in your response."
    )
    return "\n".join(lines)


# ---- CLI -------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mnemosyne-goals",
        description="Manage the Mnemosyne goal stack (persistent across sessions).",
    )
    p.add_argument("--projects-dir")
    sub = p.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("list", help="list goals")
    lp.add_argument("--all", action="store_true",
                    help="include resolved + abandoned")
    lp.add_argument("--json", action="store_true")

    ap = sub.add_parser("add", help="add a goal")
    ap.add_argument("text")
    ap.add_argument("--priority", type=int, default=3, choices=[1, 2, 3, 4, 5])
    ap.add_argument("--tags", default="",
                    help="comma-separated tag list")
    ap.add_argument("--notes", default="")

    rp = sub.add_parser("resolve", help="mark a goal resolved")
    rp.add_argument("goal_id", type=int)
    rp.add_argument("--notes", default="")

    abp = sub.add_parser("abandon", help="mark a goal abandoned")
    abp.add_argument("goal_id", type=int)
    abp.add_argument("--notes", default="")

    pp = sub.add_parser("reprioritize", help="change goal priority")
    pp.add_argument("goal_id", type=int)
    pp.add_argument("priority", type=int, choices=[1, 2, 3, 4, 5])

    tp = sub.add_parser("top", help="show top N open goals")
    tp.add_argument("n", type=int, nargs="?", default=5)

    args = p.parse_args(argv)
    pd = Path(args.projects_dir).expanduser() if args.projects_dir else None
    gs = GoalStack(projects_dir=pd)

    if args.cmd == "list":
        goals = gs.list_all() if args.all else gs.list_open()
        if args.json:
            json.dump([asdict(g) for g in goals], sys.stdout, indent=2, default=str)
            print()
            return 0
        if not goals:
            print("(no goals)")
            return 0
        for g in goals:
            marker = {"open": " ", "resolved": "✓", "abandoned": "x"}.get(g.status, "?")
            tags = f" [{', '.join(g.tags)}]" if g.tags else ""
            print(f"  [{marker}] #{g.id:<4} P{g.priority}  {g.text}{tags}")
    elif args.cmd == "add":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        g = gs.add(text=args.text, priority=args.priority,
                    tags=tags, notes=args.notes)
        print(f"added: #{g.id}  P{g.priority}  {g.text}")
    elif args.cmd == "resolve":
        g = gs.resolve(args.goal_id, notes=args.notes)
        if g is None:
            print(f"no goal #{args.goal_id}", file=sys.stderr)
            return 1
        print(f"resolved: #{g.id}  {g.text}")
    elif args.cmd == "abandon":
        g = gs.abandon(args.goal_id, notes=args.notes)
        if g is None:
            print(f"no goal #{args.goal_id}", file=sys.stderr)
            return 1
        print(f"abandoned: #{g.id}  {g.text}")
    elif args.cmd == "reprioritize":
        g = gs.reprioritize(args.goal_id, args.priority)
        if g is None:
            print(f"no goal #{args.goal_id}", file=sys.stderr)
            return 1
        print(f"reprioritized: #{g.id} → P{g.priority}")
    elif args.cmd == "top":
        for g in gs.top(args.n):
            tags = f" [{', '.join(g.tags)}]" if g.tags else ""
            print(f"  P{g.priority}  #{g.id}  {g.text}{tags}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
