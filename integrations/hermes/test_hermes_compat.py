#!/usr/bin/env python3
"""Compatibility test against an actual Hermes Agent v0.21.4 checkout.

The test is intentionally a separate process from the standalone provider tests
so the provider subclasses Hermes' real ``MemoryProvider`` rather than its
stdlib test shim. State is isolated under a temporary HERMES_HOME.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

_EXPECTED_HERMES_VERSION = "0.21.4"
_REPO = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hermes-root",
        default=os.environ.get(
            "HERMES_AGENT_ROOT", str(Path.home() / ".hermes" / "hermes-agent")
        ),
    )
    args = parser.parse_args(argv)
    hermes_root = Path(args.hermes_root).expanduser().resolve()
    if not (hermes_root / "agent" / "memory_provider.py").is_file():
        print(f"SKIP: Hermes checkout not found at {hermes_root}")
        return 0

    # Hermes first: integrations.hermes must bind to the real ABC.
    sys.path.insert(0, str(hermes_root))
    sys.path.insert(1, str(_REPO))

    import hermes_cli
    from agent.memory_manager import MemoryManager
    from agent.memory_provider import MemoryProvider
    from integrations.hermes import MnemosyneMemoryProvider, register

    assert hermes_cli.__version__ == _EXPECTED_HERMES_VERSION, (
        f"expected Hermes {_EXPECTED_HERMES_VERSION}, got {hermes_cli.__version__}"
    )
    signature = inspect.signature(MnemosyneMemoryProvider.sync_turn)
    assert "messages" in signature.parameters
    assert "turn_author" in signature.parameters

    provider = MnemosyneMemoryProvider()
    assert isinstance(provider, MemoryProvider)

    class Collector:
        def __init__(self):
            self.provider = None

        def register_memory_provider(self, value):
            self.provider = value

    collector = Collector()
    register(collector)
    assert isinstance(collector.provider, MnemosyneMemoryProvider)

    with tempfile.TemporaryDirectory(prefix="mnemo-hermes-compat-") as tmp:
        home = Path(tmp) / "hermes-home"
        home.mkdir()
        old_home = os.environ.get("HERMES_HOME")
        old_path = os.environ.get("MNEMOSYNE_PATH")
        os.environ["HERMES_HOME"] = str(home)
        os.environ["MNEMOSYNE_PATH"] = str(_REPO)
        manager = MemoryManager()
        manager.add_provider(provider)
        try:
            manager.initialize_all(
                "compat-session-1",
                hermes_home=str(home),
                platform="cli",
                agent_context="primary",
            )
            schemas = manager.get_all_tool_schemas()
            assert {schema["name"] for schema in schemas} == {
                "memory_search",
                "memory_write",
                "memory_stats",
            }
            assert all("parameters" in schema for schema in schemas)

            stored = json.loads(
                manager.handle_tool_call(
                    "memory_write",
                    {"content": "Hermes compatibility marker", "kind": "fact", "tier": 2},
                    session_id="compat-session-1",
                )
            )
            assert stored["stored"] is True
            blocked = json.loads(
                manager.handle_tool_call(
                    "memory_write",
                    {"content": "forbidden identity", "kind": "identity", "tier": 5},
                )
            )
            assert "error" in blocked and "identity" in blocked["error"]

            completed = [
                {"role": "user", "content": "compat user"},
                {"role": "assistant", "content": "compat assistant"},
            ]
            manager.sync_all(
                "compat user",
                "compat assistant",
                session_id="compat-session-1",
                messages=completed,
                turn_author={"id": "u1", "name": "User", "is_bot": False},
            )
            assert manager.flush_pending(timeout=5.0)
            provider._worker.flush()

            provider._prefetch_cache["compat-session-1"] = "old cache"
            manager.on_session_switch("compat-session-2", reset=True)
            assert provider.prefetch("query", session_id="compat-session-1") == ""

            manager.sync_all(
                "new user",
                "new assistant",
                session_id="compat-session-2",
                messages=[],
            )
            assert manager.flush_pending(timeout=5.0)
            provider._worker.flush()

            stats = json.loads(manager.handle_tool_call("memory_stats", {}))
            assert stats["total"] == 5, stats
            with provider._store._lock:
                sources = {
                    row[0]
                    for row in provider._store._conn.execute(
                        "SELECT DISTINCT source FROM memories"
                    ).fetchall()
                }
            assert sources == {"compat-session-1", "compat-session-2"}, sources
        finally:
            manager.shutdown_all()
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home
            if old_path is None:
                os.environ.pop("MNEMOSYNE_PATH", None)
            else:
                os.environ["MNEMOSYNE_PATH"] = old_path

    print(
        "PASS: Mnemosyne provider is compatible with Hermes Agent "
        f"{_EXPECTED_HERMES_VERSION} using isolated HERMES_HOME"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
