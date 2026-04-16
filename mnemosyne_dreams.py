"""
mnemosyne_dreams.py — offline pattern extraction from cold memory.

Purpose
-------
During waking time, the brain writes memories as they happen: turn
summaries, tool outputs, facts. Over weeks that fills L2 warm and then
spills to L3 cold. Most of it is noise, but buried in it are patterns
the brain couldn't see during a single turn because the relevant
memories were never retrieved together.

Dreams fix that. When the agent is idle, `consolidate()` walks the
cold store, clusters related memories by lexical similarity, and either

  A. Calls the configured model backend to write a short abstract that
     captures the cluster's gist, then promotes that abstract to L2
     warm so it's retrievable by future FTS5 searches, OR
  B. If no model is available / a `model_fn=None` is passed, falls back
     to a deterministic stdlib summarizer that picks the highest-scoring
     sentence as the abstract (honest: less smart, still useful).

The abstract is written as a new memory with:

    kind="dream_abstract"
    source="dream:<dream_id>"
    metadata={"source_ids": [...], "cluster_key": "...", "generated_by": "model"|"stdlib"}

Original memories are NOT deleted. Dreams add, they don't replace. The
eviction logic lives in mnemosyne_memory.evict_l3_older_than and runs
on a separate policy.

What this is (honest)
---------------------
This is a lightweight, non-LLM-required first implementation of the
"dream consolidation" pattern described in the eternal-context +
fantastic-disco writeups. Sleep-consolidation research in neuroscience
and recent AI memory papers (e.g. MemGPT, Generative Agents, CoALA)
all describe something like this. We ship a stdlib clustering pass +
optional model-backed summarization so it works on any install.

What this isn't
---------------
- It is not a learned-representation clusterer. It uses TF-IDF-ish
  token overlap, not embeddings. Embedding-based clustering is a
  future extension; the filesystem interface won't change.
- It does not prune L3 memories — that's mnemosyne_memory's job, and
  the policy is intentionally separate so users can opt out of pruning
  while keeping dreams.
- It does not "understand" memories. It finds statistical regularities
  and (optionally) asks a small model to summarize them.

Stdlib only. Safe to import from any Mnemosyne module. Safe on cron.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import mnemosyne_memory as mm
    from mnemosyne_config import default_projects_dir
except ImportError:  # pragma: no cover — allows running outside the package
    import os

    mm = None  # type: ignore[assignment]

    def default_projects_dir() -> Path:
        raw = os.environ.get("MNEMOSYNE_PROJECTS_DIR", "").strip()
        return Path(raw).expanduser().resolve() if raw else (
            Path.home() / "projects" / "mnemosyne"
        )


# Functional signature for the optional LLM summarizer. Given a list of
# memory contents (strings), return a short abstract (string). The brain
# can pass `lambda memories: chat(...).text` or any similar wrapper.
SummarizerFn = Callable[[list[str]], str]


# ---- utilities --------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}")
_STOPWORDS = frozenset("""
about above after again against all and any are because been before being below
between both could did does doing down during each few for from further had has
have having here hers herself him himself his how its itself just more most
myself nor now off once only other ought our ours ourselves out over own same
she should some such than that the their theirs them themselves then there
these they this those through too under until very was were what when where
which while who whom why will with would you your yours yourself yourselves
will being not can this that about into onto also same more your been have
""".split())


try:
    from mnemosyne_config import utcnow_iso as _utcnow
except ImportError:  # pragma: no cover
    def _utcnow() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _tokenize(text: str) -> list[str]:
    return [
        t.lower() for t in _TOKEN_RE.findall(text)
        if t.lower() not in _STOPWORDS and len(t) > 2
    ]


def _cosine(a: Counter, b: Counter) -> float:
    """Cosine similarity on term-frequency counters."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[k] * b[k] for k in common)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


# ---- clustering -------------------------------------------------------------

@dataclass
class MemoryCluster:
    """A group of related memories sharing a lexical centroid."""
    key: str                              # representative phrase
    members: list[dict[str, Any]] = field(default_factory=list)
    centroid: Counter = field(default_factory=Counter)

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def source_ids(self) -> list[int]:
        return [int(m["id"]) for m in self.members]


def _cluster_memories(
    memories: list[dict[str, Any]],
    *,
    similarity_threshold: float = 0.3,
    min_cluster_size: int = 3,
) -> list[MemoryCluster]:
    """Greedy single-pass clustering on token-overlap cosine similarity.

    Not as sharp as k-means or HDBSCAN, but deterministic, stdlib-only,
    and more than good enough for compressing a few hundred noisy L3
    memories into ~10 coherent clusters per nightly run.
    """
    # Pre-tokenize once
    items: list[tuple[dict[str, Any], Counter]] = []
    for m in memories:
        toks = _tokenize(m.get("content", "") or "")
        if not toks:
            continue
        items.append((m, Counter(toks)))

    clusters: list[MemoryCluster] = []
    for mem, vec in items:
        # Find the best-matching existing cluster
        best_sim = 0.0
        best_idx = -1
        for i, c in enumerate(clusters):
            sim = _cosine(vec, c.centroid)
            if sim > best_sim:
                best_sim = sim
                best_idx = i
        if best_idx >= 0 and best_sim >= similarity_threshold:
            c = clusters[best_idx]
            c.members.append(mem)
            # Update centroid (streaming mean of counters)
            for k, v in vec.items():
                c.centroid[k] += v
        else:
            # Use the top 3 tokens as the cluster key for human readability
            top = [t for t, _ in vec.most_common(3)]
            clusters.append(MemoryCluster(
                key=" ".join(top) or "cluster",
                members=[mem],
                centroid=Counter(vec),
            ))

    return [c for c in clusters if c.size >= min_cluster_size]


# ---- summarization ----------------------------------------------------------

def _stdlib_summarize(contents: list[str], *, max_chars: int = 280) -> str:
    """Deterministic fallback summarizer: pick the sentence with the
    highest term overlap with the cluster's aggregated vocabulary."""
    if not contents:
        return ""
    joined = " ".join(contents)
    global_counter = Counter(_tokenize(joined))
    if not global_counter:
        # Fall back to the first non-empty content
        for c in contents:
            if c.strip():
                return c.strip()[:max_chars]
        return ""

    # Split into candidate sentences across all contents
    best_sent = ""
    best_score = -1.0
    for c in contents:
        for sent in re.split(r"(?<=[.!?])\s+", c):
            sent = sent.strip()
            if not sent or len(sent) < 10:
                continue
            stoks = _tokenize(sent)
            if not stoks:
                continue
            score = sum(global_counter[t] for t in stoks) / (len(stoks) ** 0.5)
            if score > best_score:
                best_score = score
                best_sent = sent

    prefix = f"Pattern across {len(contents)} memories: "
    body = best_sent or contents[0]
    return (prefix + body)[:max_chars]


def _llm_summarize(
    contents: list[str],
    summarizer_fn: SummarizerFn,
    *,
    max_chars: int = 280,
) -> str:
    """Ask the user-supplied summarizer for an abstract; fall back to
    the stdlib summarizer on any failure."""
    try:
        out = summarizer_fn(contents)
        if isinstance(out, str) and out.strip():
            return out.strip()[:max_chars]
    except Exception:
        pass
    return _stdlib_summarize(contents, max_chars=max_chars)


# ---- dream record (for telemetry + optional file trail) ---------------------

@dataclass
class DreamReport:
    dream_id: str
    started_utc: str
    finished_utc: str
    clusters_examined: int
    abstracts_written: int
    memories_scanned: int
    generated_by: str  # "model" | "stdlib"
    abstracts: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "dream_id": self.dream_id,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "clusters_examined": self.clusters_examined,
            "abstracts_written": self.abstracts_written,
            "memories_scanned": self.memories_scanned,
            "generated_by": self.generated_by,
            "abstracts": self.abstracts,
        }


# ---- main entry point -------------------------------------------------------

def consolidate(
    memory: Any | None = None,
    *,
    summarizer_fn: SummarizerFn | None = None,
    tier: int | None = None,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.3,
    max_memories_scanned: int = 500,
    dry_run: bool = False,
    projects_dir: Path | None = None,
    telemetry: Any | None = None,
) -> DreamReport:
    """Run one dream-consolidation pass.

    Parameters
    ----------
    memory : MemoryStore
        If None, a default MemoryStore is opened at $PROJECTS_DIR/memory.db.
    summarizer_fn : callable | None
        Optional. Accepts list[str] content, returns str abstract. When
        None, uses the deterministic stdlib fallback. Callers usually
        pass `lambda cs: chat(...).text` or similar.
    tier : int | None
        Which tier to consolidate. Defaults to L3 cold. L2 warm is also
        valid if you want faster-cycle consolidation.
    min_cluster_size : int
        Clusters with fewer members are ignored — we want signal, not noise.
    similarity_threshold : float
        Cosine-similarity cutoff for cluster membership. Raise to get
        tighter clusters, lower to get broader ones.
    max_memories_scanned : int
        Cap per pass. Keeps dreams bounded in time/memory.
    dry_run : bool
        If True, compute clusters + abstracts but don't write to memory.
    projects_dir : Path | None
    telemetry : TelemetrySession | None
        Every dream step logs as telemetry: dream_start, dream_cluster,
        dream_abstract_written, dream_end.

    Returns
    -------
    DreamReport summarizing what was done. Safe to ignore.
    """
    if mm is None:  # pragma: no cover
        raise RuntimeError("mnemosyne_memory is not importable")

    pd = projects_dir or default_projects_dir()
    store = memory or mm.MemoryStore(telemetry=telemetry)

    dream_id = f"dream-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    started = _utcnow()
    generated_by = "model" if summarizer_fn else "stdlib"

    _log(telemetry, "dream_start", metadata={
        "dream_id": dream_id,
        "tier": tier or mm.L3_COLD,
        "max_memories_scanned": max_memories_scanned,
        "generated_by": generated_by,
    })

    # Pull candidate memories. We go directly to the store's internal
    # cursor here rather than the FTS-based search API because we want
    # all memories in the target tier, ordered by recency, not a query.
    target_tier = tier if tier is not None else mm.L3_COLD
    with store._lock:  # type: ignore[attr-defined]
        rows = store._conn.execute(  # type: ignore[attr-defined]
            """SELECT * FROM memories
               WHERE tier = ?
               ORDER BY last_accessed_utc DESC NULLS LAST, created_utc DESC
               LIMIT ?""",
            (target_tier, max_memories_scanned),
        ).fetchall()
    memories = [dict(r) for r in rows]

    clusters = _cluster_memories(
        memories,
        similarity_threshold=similarity_threshold,
        min_cluster_size=min_cluster_size,
    )

    abstracts: list[dict[str, Any]] = []
    for c in clusters:
        _log(telemetry, "dream_cluster", metadata={
            "dream_id": dream_id,
            "cluster_key": c.key,
            "size": c.size,
            "source_ids": c.source_ids,
        })

        contents = [str(m.get("content") or "") for m in c.members]
        if summarizer_fn:
            abstract = _llm_summarize(contents, summarizer_fn)
        else:
            abstract = _stdlib_summarize(contents)

        record = {
            "cluster_key": c.key,
            "size": c.size,
            "source_ids": c.source_ids,
            "abstract": abstract,
            "generated_by": generated_by,
        }
        abstracts.append(record)

        if not dry_run and abstract:
            mid = store.write(
                content=abstract,
                source=f"dream:{dream_id}",
                kind="dream_abstract",
                tier=mm.L2_WARM,
                metadata={
                    "cluster_key": c.key,
                    "size": c.size,
                    "source_ids": c.source_ids,
                    "dream_id": dream_id,
                    "generated_by": generated_by,
                },
            )
            _log(telemetry, "dream_abstract_written", metadata={
                "dream_id": dream_id,
                "memory_id": mid,
                "cluster_key": c.key,
                "source_ids": c.source_ids,
                "generated_by": generated_by,
            })

    finished = _utcnow()
    report = DreamReport(
        dream_id=dream_id,
        started_utc=started,
        finished_utc=finished,
        clusters_examined=len(clusters),
        abstracts_written=(0 if dry_run else sum(1 for a in abstracts if a["abstract"])),
        memories_scanned=len(memories),
        generated_by=generated_by,
        abstracts=abstracts,
    )

    _log(telemetry, "dream_end", metadata=report.to_json())

    # Write a JSONL trail under $PROJECTS_DIR/dreams/ for human review
    if not dry_run:
        try:
            dreams_dir = pd / "dreams"
            dreams_dir.mkdir(parents=True, exist_ok=True)
            (dreams_dir / f"{dream_id}.json").write_text(
                json.dumps(report.to_json(), indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass  # trail is best-effort

    return report


def _log(telemetry: Any | None, event_type: str, **fields: Any) -> None:
    if telemetry is None:
        return
    try:
        telemetry.log(event_type, **fields)
    except Exception:
        pass


# ---- brain integration helper ----------------------------------------------

def make_brain_summarizer(brain: Any) -> SummarizerFn:
    """Build a summarizer_fn bound to a Brain instance.

    The brain's own model backend is used to write dream abstracts. The
    identity lock is bypassed here because dream abstracts are internal
    memory — they never reach the user and they aren't conversational.
    """
    def summarize(contents: list[str]) -> str:
        joined = "\n\n".join(f"- {c}" for c in contents[:15])
        prompt = (
            "You are summarizing a cluster of related memories into one "
            "short abstract. Write one to three sentences capturing the "
            "shared pattern. No preamble, no meta-commentary. Plain prose.\n\n"
            f"Memories:\n{joined}\n\nAbstract:"
        )
        resp = brain._chat_fn(
            [{"role": "user", "content": prompt}],
            backend=brain.config.backend,
            telemetry=None,  # dreams are their own event stream
            temperature=0.3,
            max_tokens=200,
        )
        if resp.get("status") == "error":
            return ""
        return resp.get("text") or ""
    return summarize


# ---- CLI -------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="mnemosyne-dreams",
        description="Run one offline dream-consolidation pass over the "
                    "Mnemosyne memory store. Clusters related memories in "
                    "the target tier, writes one summary abstract per "
                    "cluster back as a fresh L2 warm memory. Safe on cron.",
    )
    p.add_argument("--projects-dir")
    p.add_argument("--tier", type=int, default=None,
                   help="which tier to consolidate (default L3 cold)")
    p.add_argument("--max-memories", type=int, default=500)
    p.add_argument("--min-cluster-size", type=int, default=3)
    p.add_argument("--similarity", type=float, default=0.3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="emit the full DreamReport JSON on stdout")
    args = p.parse_args(argv)

    pd = Path(args.projects_dir).expanduser() if args.projects_dir else None
    report = consolidate(
        tier=args.tier,
        max_memories_scanned=args.max_memories,
        min_cluster_size=args.min_cluster_size,
        similarity_threshold=args.similarity,
        dry_run=args.dry_run,
        projects_dir=pd,
    )

    if args.json:
        json.dump(report.to_json(), sys.stdout, indent=2, default=str)
        print()
        return 0

    print(f"dream: {report.dream_id}")
    print(f"  scanned:   {report.memories_scanned} memories")
    print(f"  clusters:  {report.clusters_examined}")
    print(f"  abstracts: {report.abstracts_written} "
          f"({report.generated_by})")
    if report.abstracts:
        print()
        for a in report.abstracts[:10]:
            print(f"  [{a['size']}x] {a['cluster_key']}")
            print(f"    {a['abstract']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
