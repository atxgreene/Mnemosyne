# Hermes provider validation

This file records the reproducible validation contract for Mnemosyne v0.9.8.
It supersedes the historical v0.16.0 machine-specific validation log.

## Supported contract

- Hermes Agent: exactly v0.21.4 for the release compatibility gate.
- Mnemosyne: v0.9.8.
- Storage: local SQLite/FTS5.
- Network/API keys: none for provider tests.
- Test state: temporary `HERMES_HOME`; no real profile or memory database.

## Commands

```sh
python3 tests/test_hermes_provider.py
python3 tests/test_hermes_compat.py \
  --hermes-root /path/to/hermes-agent-v0.21.4
```

The release gate also installs the built wheel and resolves the
`hermes_agent.memory_providers` entry point named `mnemosyne`.

## Required checks

- key-based config schema and atomic private config persistence;
- JSON-object results for search, write, stats, validation failures, and
  unknown tools;
- model-callable L5/identity rejection in schema and handler;
- direct trusted `MemoryStore` L5 write remains functional;
- exact current `sync_turn` signature, including `messages` and `turn_author`;
- turn context preserved through the background writer;
- automatic writes limited to `agent_context="primary"`;
- session switch updates write source and invalidates old/in-flight prefetch;
- configured checkout and installed-module resolution;
- provider registration through Hermes' real collector/manager;
- writer flush, SQLite durability, and clean shutdown.

## Local release result

The v0.9.8 hardening branch was exercised against the installed Hermes Agent
v0.21.4 source tree with an isolated temporary home. The compatibility script
must print:

```text
PASS: Mnemosyne provider is compatible with Hermes Agent 0.21.4 using isolated HERMES_HOME
```

No claim in this document implies validation against a user's live profile or
interactive chat process.
