#!/usr/bin/env python3
"""Offline contract tests for the Mnemosyne Hermes provider."""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from integrations.hermes import (  # noqa: E402
    MnemosyneMemoryProvider,
    _WriteWorker,
    _resolve_memory_module,
)
from mnemosyne_memory import L5_IDENTITY, MemoryStore  # noqa: E402


class ProviderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "hermes-home"
        self.home.mkdir()
        self.env = mock.patch.dict(
            os.environ,
            {"HERMES_HOME": str(self.home), "MNEMOSYNE_PATH": str(_REPO)},
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def make_provider(self, *, context: str = "primary", session: str = "s1"):
        provider = MnemosyneMemoryProvider()
        provider.initialize(
            session,
            hermes_home=str(self.home),
            platform="cli",
            agent_context=context,
        )
        self.addCleanup(provider.shutdown)
        return provider

    @staticmethod
    def parsed(provider, tool: str, args: dict, **kwargs):
        result = provider.handle_tool_call(tool, args, **kwargs)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        return parsed

    def test_schema_uses_v0214_key_contract(self):
        provider = MnemosyneMemoryProvider()
        schema = provider.get_config_schema()
        self.assertTrue(schema)
        self.assertTrue(all("key" in field for field in schema))
        self.assertTrue(all("name" not in field for field in schema))

    def test_tool_schema_and_handler_reserve_l0_l1_and_l5(self):
        provider = self.make_provider()
        write_schema = next(
            schema
            for schema in provider.get_tool_schemas()
            if schema["name"] == "memory_write"
        )
        properties = write_schema["parameters"]["properties"]
        self.assertEqual(properties["tier"]["minimum"], 2)
        self.assertEqual(properties["tier"]["maximum"], 4)
        self.assertNotIn("identity", properties["kind"]["enum"])

        for tier in (0, 1, 5):
            by_tier = self.parsed(
                provider,
                "memory_write",
                {"content": "reserved-tier mutation", "kind": "fact", "tier": tier},
            )
            self.assertIn(f"L{tier}", by_tier["error"])
        by_kind = self.parsed(
            provider,
            "memory_write",
            {"content": "identity mutation", "kind": "identity", "tier": 4},
        )
        self.assertIn("identity", by_kind["error"])
        self.assertEqual(provider._store.stats()["total"], 0)

    def test_trusted_memory_store_can_still_write_l5(self):
        db = Path(self.tmp.name) / "trusted.db"
        store = MemoryStore(path=db)
        try:
            memory_id = store.write(
                "Human-approved core value",
                source="human-review",
                kind="core_value",
                tier=L5_IDENTITY,
            )
            row = store.get(memory_id)
            self.assertEqual(row["tier"], L5_IDENTITY)
            self.assertEqual(row["kind"], "core_value")
        finally:
            store.close()

    def test_all_tool_results_are_json_objects(self):
        provider = self.make_provider()
        stored = self.parsed(
            provider,
            "memory_write",
            {"content": "The operator prefers Python", "kind": "preference", "tier": 3},
            session_id="tool-session",
        )
        self.assertTrue(stored["stored"])
        found = self.parsed(
            provider, "memory_search", {"query": "operator Python", "limit": 5}
        )
        self.assertEqual(found["count"], 1)
        self.assertIn("Python", found["results"][0]["content"])
        stats = self.parsed(provider, "memory_stats", {})
        self.assertEqual(stats["total"], 1)
        self.assertIn("error", self.parsed(provider, "unknown", {}))
        self.assertIn("error", self.parsed(provider, "memory_search", {}))

    def test_sync_turn_signature_accepts_v0214_context(self):
        provider = self.make_provider()
        provider.sync_turn(
            "hello",
            "world",
            session_id="turn-session",
            messages=[{"role": "user", "content": "hello"}],
            turn_author={"id": "u1", "name": "User", "is_bot": False},
        )
        provider._worker.flush()
        with provider._store._lock:
            rows = provider._store._conn.execute(
                "SELECT source, kind, content FROM memories ORDER BY id"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["source"] == "turn-session" for row in rows))
        self.assertTrue(all(row["kind"] == "turn" for row in rows))

    def test_non_primary_context_disables_only_automatic_writes(self):
        provider = self.make_provider(context="subagent")
        provider.sync_turn("automatic", "must not persist", session_id="child")
        provider.on_session_end([{"role": "user", "content": "also skipped"}])
        provider._worker.flush()
        self.assertEqual(provider._store.stats()["total"], 0)

        explicit = self.parsed(
            provider,
            "memory_write",
            {"content": "explicit fact", "kind": "fact", "tier": 2},
        )
        self.assertTrue(explicit["stored"])
        self.assertEqual(provider._store.stats()["total"], 1)

    def test_session_switch_clears_cache_and_rebinds_future_writes(self):
        provider = self.make_provider(session="old")
        with provider._cache_lock:
            provider._prefetch_cache["old"] = "stale old context"
        provider.on_session_switch("new", reset=True)
        self.assertEqual(provider.prefetch("anything", session_id="old"), "")
        self.assertEqual(provider.prefetch("anything", session_id="new"), "")

        provider.sync_turn("new user", "new assistant")
        provider._worker.flush()
        with provider._store._lock:
            sources = [
                row[0]
                for row in provider._store._conn.execute(
                    "SELECT DISTINCT source FROM memories"
                ).fetchall()
            ]
        self.assertEqual(sources, ["new"])

    def test_worker_preserves_submitter_context_and_logs_failures(self):
        marker = contextvars.ContextVar("marker", default="unset")
        seen = []
        worker = _WriteWorker()
        try:
            marker.set("turn-context")
            worker.submit(lambda: seen.append(marker.get()))
            worker.flush()
            self.assertEqual(seen, ["turn-context"])

            with self.assertLogs("integrations.hermes", level="WARNING") as logs:
                worker.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
                worker.flush()
            self.assertIn("background operation failed", "\n".join(logs.output))
        finally:
            worker.stop()

    def test_worker_stop_drains_and_rejects_late_submissions(self):
        worker = _WriteWorker()
        accepted = []
        for index in range(20):
            worker.submit(accepted.append, index)
        worker.stop()
        self.assertEqual(accepted, list(range(20)))
        self.assertFalse(worker.is_alive)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            worker.submit(lambda: None)

    def test_worker_submit_stop_race_executes_every_accepted_job(self):
        worker = _WriteWorker()
        start = threading.Barrier(5)
        accepted = []
        executed = []
        records_lock = threading.Lock()

        def record(value):
            with records_lock:
                executed.append(value)

        def submitter(group):
            start.wait()
            for index in range(100):
                value = (group, index)
                try:
                    worker.submit(record, value)
                except RuntimeError:
                    return
                with records_lock:
                    accepted.append(value)

        submitters = [threading.Thread(target=submitter, args=(group,)) for group in range(4)]
        for thread in submitters:
            thread.start()
        start.wait()
        worker.stop()
        for thread in submitters:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.assertFalse(worker.is_alive)
        self.assertCountEqual(executed, accepted)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            worker.submit(record, ("late", 0))

    def test_shutdown_waits_for_worker_before_closing_store(self):
        started = threading.Event()
        release = threading.Event()
        closed = threading.Event()

        class Store:
            path = Path(self.tmp.name) / "race.db"

            def close(inner_self):
                closed.set()

        provider = MnemosyneMemoryProvider()
        provider._store = Store()
        provider._db_path = Store.path
        provider._worker = _WriteWorker()

        def blocked_job():
            started.set()
            self.assertTrue(release.wait(timeout=5))

        provider._worker.submit(blocked_job)
        self.assertTrue(started.wait(timeout=2))
        closer = threading.Thread(target=provider.shutdown)
        closer.start()
        closer.join(timeout=0.1)
        self.assertTrue(closer.is_alive())
        self.assertFalse(closed.is_set())
        release.set()
        closer.join(timeout=2)
        self.assertFalse(closer.is_alive())
        self.assertTrue(closed.is_set())

    def test_config_save_merges_atomically_with_private_permissions(self):
        provider = MnemosyneMemoryProvider()
        provider.save_config({"db_path": "first.db", "prefetch_limit": 3}, str(self.home))
        provider.save_config({"prefetch_limit": 7}, str(self.home))
        path = self.home / "mnemosyne.json"
        config = json.loads(path.read_text())
        self.assertEqual(config, {"db_path": "first.db", "prefetch_limit": 7})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_configured_and_installed_module_resolution_are_dynamic(self):
        checkout = Path(self.tmp.name) / "checkout"
        checkout.mkdir()
        (checkout / "mnemosyne_memory.py").write_text("class MemoryStore: pass\n")
        source, path = _resolve_memory_module(self.home, checkout)
        self.assertEqual(source, "file")
        self.assertEqual(path, (checkout / "mnemosyne_memory.py").resolve())

        # No configured/env checkout: the installed/source-tree module remains resolvable.
        with mock.patch.dict(os.environ, {"MNEMOSYNE_PATH": ""}, clear=False):
            resolved = _resolve_memory_module(self.home, None)
        self.assertIsNotNone(resolved)

    def test_database_is_valid_sqlite_after_shutdown(self):
        provider = self.make_provider()
        self.parsed(
            provider,
            "memory_write",
            {"content": "durable", "kind": "fact", "tier": 2},
        )
        db_path = Path(provider._store.path)
        provider.shutdown()
        with sqlite3.connect(db_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1
            )

    def test_relative_db_paths_are_profile_isolated_and_private(self):
        homes = [Path(self.tmp.name) / "profile-a", Path(self.tmp.name) / "profile-b"]
        db_paths = []
        for index, home in enumerate(homes):
            home.mkdir()
            (home / "mnemosyne.json").write_text(
                json.dumps({"db_path": "state/memory.db"}), encoding="utf-8"
            )
            provider = MnemosyneMemoryProvider()
            provider.initialize(
                f"profile-{index}", hermes_home=str(home), agent_context="primary"
            )
            self.parsed(
                provider,
                "memory_write",
                {"content": f"profile marker {index}", "kind": "fact", "tier": 2},
            )
            db_path = Path(provider._store.path)
            db_paths.append(db_path)
            self.assertEqual(db_path, (home / "state" / "memory.db").resolve())
            self.assertEqual(db_path.parent.stat().st_mode & 0o777, 0o700)
            for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
                if path.exists():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600, path)
            provider.shutdown()

        self.assertNotEqual(db_paths[0], db_paths[1])
        for index, db_path in enumerate(db_paths):
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute("SELECT content FROM memories").fetchall()
            self.assertEqual(rows, [(f"profile marker {index}",)])

    def test_relative_db_path_rejects_parent_traversal(self):
        (self.home / "mnemosyne.json").write_text(
            json.dumps({"db_path": "../outside.db"}), encoding="utf-8"
        )
        provider = MnemosyneMemoryProvider()
        with self.assertRaisesRegex(ValueError, "inside the active HERMES_HOME"):
            provider.initialize("traversal", hermes_home=str(self.home))


if __name__ == "__main__":
    unittest.main(verbosity=2)
