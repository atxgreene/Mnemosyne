# Mnemosyne memory provider for Hermes Agent

Mnemosyne v0.9.8 includes a packaged memory provider targeting Hermes Agent
v0.21.4. It stores data locally in SQLite and adds no runtime dependency.

## Security boundary

The model-callable `memory_write` tool accepts `fact`, `preference`, `goal`, and
`pattern` in tiers L2-L4. It rejects L0 instinct, L1 working-memory, L5, and
identity writes in both the JSON schema and the handler. Trusted application code can still write L5 through
`mnemosyne_memory.MemoryStore` for reviewed identity workflows.

Automatic turn writes run only when Hermes initializes the provider with
`agent_context="primary"`. Explicit tool calls remain available in other
contexts, subject to the L2-L4 restriction. No standalone session-extraction
capability is claimed by the public package.

## Install

Mnemosyne is not published on PyPI. Install v0.9.8 from the GitHub source tag
with the same Python environment that runs Hermes:

```sh
python3 -m pip install \
  "https://github.com/atxgreene/Mnemosyne/archive/refs/tags/v0.9.8.tar.gz"
```

The wheel/source install registers the `mnemosyne` entry point in
`hermes_agent.memory_providers`; no plugin-directory copy is required. A source
checkout also works by copying `integrations/hermes/` to
`$HERMES_HOME/plugins/mnemosyne/` and setting `MNEMOSYNE_PATH` to the checkout.

Configure Hermes:

```yaml
memory:
  provider: mnemosyne
```

Optional `$HERMES_HOME/mnemosyne.json`:

```json
{
  "db_path": "mnemosyne/memory.db",
  "prefetch_limit": 8,
  "mnemosyne_path": "/absolute/path/to/a/source/checkout"
}
```

`mnemosyne_path` is unnecessary for a package install. Relative `db_path`
values are resolved under `HERMES_HOME`. Config writes are atomic and mode 0600
when the platform permits it.

## Tools

| Tool | Contract |
|---|---|
| `memory_search(query, limit=8)` | Search all tiers; returns a JSON object. |
| `memory_write(content, kind, tier)` | Explicit L2-L4 write; L0/L1 and identity/L5 rejected. |
| `memory_stats()` | Tier/kind counts in a JSON object. |

All handler paths, including validation errors and unknown tools, return JSON
objects serialized as strings, matching the Hermes v0.21.4 provider contract.

## Lifecycle behavior

- `sync_turn(user, assistant, *, session_id="", messages=None,
  turn_author=None)` matches the v0.21.4 call signature.
- Background jobs preserve the submitting turn's context variables and log
  failures explicitly.
- Session switches update the default source session and invalidate prefetch
  caches, including in-flight results from the old generation.
- Module resolution is dynamic: configured checkout, environment checkout,
  source tree, then installed `mnemosyne_memory` module.
- Shutdown drains the single writer and closes SQLite.

## Verify

Standalone contract tests:

```sh
python3 integrations/hermes/test_provider.py
```

Compatibility test against an actual Hermes v0.21.4 checkout, with a temporary
isolated `HERMES_HOME`:

```sh
python3 integrations/hermes/test_hermes_compat.py \
  --hermes-root /path/to/hermes-agent-v0.21.4
```

The compatibility test verifies Hermes' real `MemoryProvider` ABC and
`MemoryManager`, package registration shape, tools, JSON results, turn writes,
session switching/cache reset, and shutdown. It skips cleanly when the checkout
is absent and fails if the imported Hermes version is not exactly 0.21.4.
