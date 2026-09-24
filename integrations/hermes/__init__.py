#!/usr/bin/env python3
"""Mnemosyne memory provider for Hermes Agent 0.21.4.

The adapter keeps the Mnemosyne core independently usable while enforcing a
narrower model-callable boundary: tools may write L0-L4, but never L5 identity.
Trusted application code can still write L5 directly through ``MemoryStore``.
"""

from __future__ import annotations

import abc
import contextvars
import hashlib
import importlib
import importlib.util
import json
import logging
import os
import queue
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

try:
    from agent.memory_provider import (  # type: ignore
        MemoryProvider,
        ctx_bound,
        spawn_context_thread,
    )
except ImportError:
    class MemoryProvider(abc.ABC):  # type: ignore[no-redef]
        @property
        @abc.abstractmethod
        def name(self) -> str: ...

        @abc.abstractmethod
        def is_available(self) -> bool: ...

        @abc.abstractmethod
        def initialize(self, session_id: str, **kwargs: Any) -> None: ...

        @abc.abstractmethod
        def get_tool_schemas(self) -> List[Dict[str, Any]]: ...

        def system_prompt_block(self) -> str:
            return ""

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            return ""

        def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
            return None

        def sync_turn(
            self,
            user_content: str,
            assistant_content: str,
            *,
            session_id: str = "",
            messages: Optional[List[Dict[str, Any]]] = None,
            turn_author: Optional[Dict[str, Any]] = None,
        ) -> None:
            return None

        def handle_tool_call(
            self, tool_name: str, args: Dict[str, Any], **kwargs: Any
        ) -> str:
            raise NotImplementedError

        def get_config_schema(self) -> List[Dict[str, Any]]:
            return []

        def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
            return None

        def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
            return None

        def on_session_switch(
            self,
            new_session_id: str,
            *,
            parent_session_id: str = "",
            reset: bool = False,
            rewound: bool = False,
            **kwargs: Any,
        ) -> None:
            return None

        def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
            return ""

        def shutdown(self) -> None:
            return None

    def ctx_bound(fn):  # type: ignore[no-redef]
        context = contextvars.copy_context()
        return lambda *args, **kwargs: context.run(fn, *args, **kwargs)

    def spawn_context_thread(  # type: ignore[no-redef]
        target, *, name: str, daemon: bool = True, args=(), kwargs=None
    ):
        return threading.Thread(
            target=ctx_bound(target),
            args=args,
            kwargs=kwargs or {},
            name=name,
            daemon=daemon,
        )


_MODEL_WRITABLE_KINDS = ("fact", "preference", "goal", "pattern")
_MODEL_MAX_TIER = 4
_SENTINEL = object()


def _json_result(**values: Any) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def _read_json_dict(path: Path) -> Dict[str, Any]:
    try:
        from utils import read_json_or_empty  # type: ignore

        value = read_json_or_empty(path)
        return value if isinstance(value, dict) else {}
    except ImportError:
        pass
    except Exception:
        logger.warning("Mnemosyne config read helper failed for %s", path, exc_info=True)

    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except Exception:
        logger.warning("Mnemosyne config is unreadable: %s", path, exc_info=True)
        return {}


def _atomic_json_write(path: Path, value: Dict[str, Any]) -> None:
    """Use Hermes' atomic writer when available, with a stdlib fallback."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from utils import atomic_json_write  # type: ignore

        atomic_json_write(path, value, mode=0o600, sort_keys=True)
        return
    except ImportError:
        pass

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_config(hermes_home: Union[str, Path]) -> Dict[str, Any]:
    return _read_json_dict(Path(hermes_home) / "mnemosyne.json")


def _candidate_roots(
    hermes_home: Optional[Union[str, Path]] = None,
    configured_path: Optional[Union[str, Path]] = None,
) -> List[Path]:
    """Resolve roots on every call so setup changes do not require re-import."""
    candidates: List[Path] = []
    raw_values: List[Optional[Union[str, Path]]] = [
        configured_path,
        os.environ.get("MNEMOSYNE_PATH"),
    ]
    if hermes_home:
        raw_values.append(_load_config(hermes_home).get("mnemosyne_path"))

    # Source checkout (integrations/hermes/__init__.py -> repository root).
    raw_values.append(Path(__file__).resolve().parents[2])
    raw_values.extend(
        [
            Path.home() / "Mnemosyne",
            Path.home() / "mnemosyne-kali-test" / "Mnemosyne",
        ]
    )
    seen = set()
    for raw in raw_values:
        if not raw:
            continue
        path = Path(raw).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            candidates.append(resolved)
    return candidates


def _resolve_memory_module(
    hermes_home: Optional[Union[str, Path]] = None,
    configured_path: Optional[Union[str, Path]] = None,
) -> Optional[Tuple[str, Optional[Path]]]:
    """Return (source kind, module path), preferring configured checkouts."""
    for root in _candidate_roots(hermes_home, configured_path):
        module_file = root / "mnemosyne_memory.py"
        if module_file.is_file():
            return "file", module_file

    # A normal wheel install exposes mnemosyne_memory without any checkout path.
    try:
        spec = importlib.util.find_spec("mnemosyne_memory")
    except (ImportError, ValueError, AttributeError):
        spec = None
    if spec is not None:
        origin = Path(spec.origin).resolve() if spec.origin else None
        return "installed", origin
    return None


def _load_memory_module(
    hermes_home: Union[str, Path],
    configured_path: Optional[Union[str, Path]] = None,
):
    resolved = _resolve_memory_module(hermes_home, configured_path)
    if resolved is None:
        raise ImportError(
            "mnemosyne_memory is unavailable; install this package or configure "
            "mnemosyne_path/MNEMOSYNE_PATH"
        )
    source, module_file = resolved
    if source == "installed":
        return importlib.import_module("mnemosyne_memory")

    assert module_file is not None
    digest = hashlib.sha256(str(module_file).encode("utf-8")).hexdigest()[:16]
    module_name = f"_mnemosyne_provider_core_{digest}"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, module_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Mnemosyne core from {module_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


class _WriteWorker:
    """Single writer whose jobs retain the submitting Hermes contextvars."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = spawn_context_thread(
            self._run, name="mnemosyne-write-worker", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                fn, args, kwargs = item
                fn(*args, **kwargs)
            except Exception:
                logger.warning("Mnemosyne background operation failed", exc_info=True)
            finally:
                self._queue.task_done()

    def submit(self, fn, *args, **kwargs) -> None:
        # Capture the context of THIS turn, not only initialize()'s context.
        self._queue.put((ctx_bound(fn), args, kwargs))

    def flush(self) -> None:
        self._queue.join()

    def stop(self) -> None:
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            logger.warning("Mnemosyne writer did not stop within 5 seconds")


class MnemosyneMemoryProvider(MemoryProvider):
    """Hermes MemoryProvider backed by Mnemosyne's local MemoryStore."""

    def __init__(self) -> None:
        self._store: Any = None
        self._worker: Optional[_WriteWorker] = None
        self._session_id = ""
        self._hermes_home = ""
        self._prefetch_limit = 8
        self._automatic_writes = True
        self._prefetch_cache: Dict[str, str] = {}
        self._prefetch_generation = 0
        self._cache_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        hermes_home = os.environ.get("HERMES_HOME")
        cfg = _load_config(hermes_home) if hermes_home else {}
        return _resolve_memory_module(
            hermes_home, cfg.get("mnemosyne_path")
        ) is not None

    def unavailable_reason(self) -> str:
        return (
            "Install mnemosyne-harness from the GitHub release/source tag, or set "
            "mnemosyne_path in $HERMES_HOME/mnemosyne.json (MNEMOSYNE_PATH also works)."
        )

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        if self._worker is not None or self._store is not None:
            self.shutdown()
        self._hermes_home = str(
            kwargs.get("hermes_home") or os.environ.get("HERMES_HOME") or "."
        )
        cfg = _load_config(self._hermes_home)
        module = _load_memory_module(self._hermes_home, cfg.get("mnemosyne_path"))

        raw_db_path = cfg.get("db_path")
        db_path = (
            Path(raw_db_path).expanduser()
            if raw_db_path
            else Path(self._hermes_home) / "mnemosyne" / "memory.db"
        )
        if not db_path.is_absolute():
            db_path = Path(self._hermes_home) / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._store = module.MemoryStore(path=db_path)
        self._session_id = str(session_id or "hermes")
        try:
            self._prefetch_limit = max(1, min(int(cfg.get("prefetch_limit", 8)), 20))
        except (TypeError, ValueError):
            logger.warning("Invalid Mnemosyne prefetch_limit; using 8")
            self._prefetch_limit = 8
        self._automatic_writes = kwargs.get("agent_context", "primary") == "primary"
        with self._cache_lock:
            self._prefetch_cache.clear()
            self._prefetch_generation += 1
        self._worker = _WriteWorker()

    def shutdown(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()
        store, self._store = self._store, None
        if store is not None:
            try:
                store.close()
            except Exception:
                logger.warning("Mnemosyne store close failed", exc_info=True)

    def system_prompt_block(self) -> str:
        return (
            "## Mnemosyne Memory\n"
            "Persistent local memory is available through memory_search, "
            "memory_write, and memory_stats. Search before answering questions "
            "about the user's preferences, history, projects, or personal context. "
            "memory_write can store facts, preferences, goals, and patterns only "
            "in L0-L4. L5 identity is human/trusted-code managed and is never "
            "model-writable."
        )

    def _sid(self, session_id: str = "") -> str:
        return str(session_id or self._session_id or "hermes")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        with self._cache_lock:
            return self._prefetch_cache.get(self._sid(session_id), "")

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not query.strip() or self._worker is None or self._store is None:
            return
        sid = self._sid(session_id)
        with self._cache_lock:
            generation = self._prefetch_generation

        def _do_prefetch() -> None:
            hits = self._store.search(query, limit=self._prefetch_limit)
            formatted = _format_hits(hits)
            with self._cache_lock:
                if generation == self._prefetch_generation and sid == self._session_id:
                    self._prefetch_cache[sid] = formatted

        self._worker.submit(_do_prefetch)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        turn_author: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Queue primary-context turn persistence using the v0.21.4 signature."""
        del messages, turn_author
        if not self._automatic_writes or self._worker is None or self._store is None:
            return
        sid = self._sid(session_id)

        def _write() -> None:
            if user_content:
                self._store.write(
                    f"[user] {user_content}", source=sid, kind="turn", tier=2
                )
            if assistant_content:
                self._store.write(
                    f"[assistant] {assistant_content}",
                    source=sid,
                    kind="turn",
                    tier=2,
                )

        self._worker.submit(_write)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "memory_search",
                "description": "Search Mnemosyne for relevant memories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "default": 8,
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "memory_write",
                "description": (
                    "Store a fact, preference, goal, or pattern in L0-L4. "
                    "L5 identity cannot be written by the model."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "kind": {
                            "type": "string",
                            "enum": list(_MODEL_WRITABLE_KINDS),
                            "default": "fact",
                        },
                        "tier": {
                            "type": "integer",
                            "default": 2,
                            "minimum": 0,
                            "maximum": _MODEL_MAX_TIER,
                        },
                    },
                    "required": ["content"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "memory_stats",
                "description": "Return memory counts by tier.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ]

    def handle_tool_call(
        self, tool_name: str, args: Dict[str, Any], **kwargs: Any
    ) -> str:
        if self._store is None:
            return _json_result(error="Mnemosyne is not initialized")
        args = args if isinstance(args, dict) else {}
        try:
            if tool_name == "memory_search":
                query = str(args.get("query") or "").strip()
                if not query:
                    return _json_result(error="query is required")
                limit = max(1, min(int(args.get("limit", 8)), 20))
                hits = self._store.search(query, limit=limit)
                return _json_result(results=_hits_as_json(hits), count=len(hits))

            if tool_name == "memory_write":
                content = str(args.get("content") or "").strip()
                if not content:
                    return _json_result(error="content is required")
                kind = str(args.get("kind") or "fact").strip().lower()
                try:
                    tier = int(args.get("tier", 2))
                except (TypeError, ValueError):
                    return _json_result(error="tier must be an integer from 0 through 4")
                if kind not in _MODEL_WRITABLE_KINDS:
                    return _json_result(
                        error=(
                            "kind must be one of fact, preference, goal, pattern; "
                            "identity writes require trusted human-approved code"
                        )
                    )
                if tier < 0 or tier > _MODEL_MAX_TIER:
                    return _json_result(
                        error="tier must be from 0 through 4; L5 identity is not model-writable"
                    )
                sid = self._sid(str(kwargs.get("session_id") or ""))
                memory_id = self._store.write(
                    content, source=sid, kind=kind, tier=tier
                )
                return _json_result(
                    stored=True, id=memory_id, tier=tier, kind=kind
                )

            if tool_name == "memory_stats":
                return _json_result(**_stats_data(self._store))

            return _json_result(error=f"Unknown tool: {tool_name}")
        except Exception as exc:
            logger.warning("Mnemosyne tool %s failed", tool_name, exc_info=True)
            return _json_result(error=f"{tool_name} failed: {exc}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._automatic_writes or self._worker is None:
            return
        extractions = _try_extract(messages)
        if extractions:
            sid = self._session_id
            self._worker.submit(self._write_extractions, extractions, sid)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        if not self._automatic_writes or self._worker is None:
            return ""
        extractions = _try_extract(messages)
        if not extractions:
            return ""
        sid = self._session_id
        self._worker.submit(self._write_extractions, extractions, sid)
        parts = []
        for category in ("facts", "preferences", "goals", "patterns"):
            items = extractions.get(category, [])
            if items:
                parts.append(f"{category}: " + "; ".join(map(str, items)))
        return "Preserved to memory — " + " | ".join(parts) if parts else ""

    def _write_extractions(
        self, extractions: Dict[str, Any], session_id: str
    ) -> None:
        tiers = {
            "facts": 3,
            "preferences": 3,
            "goals": 4,
            "patterns": 4,
            "relationships": 3,
        }
        for category, tier in tiers.items():
            for item in extractions.get(category, []):
                self._store.write(
                    str(item),
                    source=session_id,
                    kind=category.rstrip("s"),
                    tier=tier,
                )

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        del parent_session_id, reset, rewound, kwargs
        new_sid = str(new_session_id or "").strip()
        if new_sid:
            self._session_id = new_sid
        with self._cache_lock:
            self._prefetch_generation += 1
            self._prefetch_cache.clear()

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "mnemosyne_path",
                "description": (
                    "Optional checkout containing mnemosyne_memory.py; omit when "
                    "mnemosyne-harness is installed in Hermes' Python environment"
                ),
                "required": False,
            },
            {
                "key": "db_path",
                "description": (
                    "SQLite path (default: $HERMES_HOME/mnemosyne/memory.db)"
                ),
                "required": False,
            },
            {
                "key": "prefetch_limit",
                "description": "Memories cached for the next turn",
                "type": "integer",
                "default": 8,
                "minimum": 1,
                "maximum": 20,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        path = Path(hermes_home) / "mnemosyne.json"
        current = _read_json_dict(path)
        current.update(dict(values or {}))
        _atomic_json_write(path, current)


def _hits_as_json(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for hit in hits:
        result.append(
            {
                key: hit.get(key)
                for key in ("id", "content", "tier", "kind", "source", "strength")
                if hit.get(key) is not None
            }
        )
    return result


def _format_hits(hits: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{index}. [tier {hit.get('tier', '?')}] "
        f"{hit.get('content') or hit.get('text') or str(hit)}"
        for index, hit in enumerate(hits, 1)
    )


def _stats_data(store: Any) -> Dict[str, Any]:
    stats = getattr(store, "stats", None)
    if callable(stats):
        value = stats()
        if isinstance(value, dict):
            return value
    conn = getattr(store, "_conn", None)
    if conn is None:
        raise RuntimeError("MemoryStore exposes neither stats() nor a SQLite connection")
    rows = conn.execute(
        "SELECT tier, COUNT(*) FROM memories GROUP BY tier ORDER BY tier"
    ).fetchall()
    by_tier = {str(row[0]): int(row[1]) for row in rows}
    return {"total": sum(by_tier.values()), "by_tier": by_tier}


def _try_extract(
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Use the optional extraction layer when installed next to a lab checkout."""
    try:
        lab_root = Path(__file__).resolve().parents[3]
        extraction_path = lab_root / "experiments" / "extraction_layer"
        if not extraction_path.is_dir():
            return None
        if str(extraction_path) not in sys.path:
            sys.path.insert(0, str(extraction_path))
        from extractor import extract_salient  # type: ignore

        text = "\n".join(
            message.get("content", "")
            for message in messages
            if isinstance(message.get("content"), str)
        )
        return extract_salient(text) if text.strip() else None
    except Exception:
        logger.debug("Mnemosyne optional extraction failed", exc_info=True)
        return None


def register(ctx) -> None:  # noqa: ANN001
    ctx.register_memory_provider(MnemosyneMemoryProvider())
