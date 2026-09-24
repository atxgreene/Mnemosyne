# Hermes Agent integration

Mnemosyne v0.9.8 ships an installable memory-provider plugin for Hermes Agent
v0.21.4.

## Install

There is no PyPI release. Install the GitHub source tag into the Python
environment that runs Hermes:

```sh
python3 -m pip install \
  "https://github.com/atxgreene/Mnemosyne/archive/refs/tags/v0.9.8.tar.gz"
```

The package registers:

```toml
[project.entry-points."hermes_agent.memory_providers"]
mnemosyne = "integrations.hermes:register"
```

Then select it in Hermes `config.yaml`:

```yaml
memory:
  provider: mnemosyne
```

For a checkout-only installation, copy `integrations/hermes/` to
`$HERMES_HOME/plugins/mnemosyne/` and set `MNEMOSYNE_PATH` to the repository
root.

## Configuration

Optional `$HERMES_HOME/mnemosyne.json`:

```json
{
  "db_path": "mnemosyne/memory.db",
  "prefetch_limit": 8
}
```

A relative database path is rooted under `HERMES_HOME`. Package installs do not
need `mnemosyne_path`; use it only to point at a specific source checkout.

## Model-callable boundary

`memory_write` permits these kinds: `fact`, `preference`, `goal`, `pattern`.
It permits tiers L2-L4. Both the advertised JSON schema and runtime handler
reserve L0 instinct, L1 working memory, and L5 identity for trusted lifecycle
code rather than model tool calls.

This does not remove L5 from Mnemosyne. Trusted application code and reviewed
human workflows may write L5 directly through `MemoryStore`. The adapter is the
security boundary for model-callable tools.

## Hermes v0.21.4 behavior

- Config descriptors use the current `key` schema.
- All tool results are JSON-object strings.
- `sync_turn` accepts `session_id`, `messages`, and `turn_author`.
- Background work preserves submitter context and logs failures.
- Automatic turn writes are restricted to primary agent context; this public
  package does not claim a standalone session-extraction layer.
- Session switching changes the default write source and clears both cached and
  in-flight old-session prefetch results.
- Configured checkouts and installed modules are resolved dynamically.
- Config writes use Hermes' atomic helper when available and a mode-0600 atomic
  stdlib fallback otherwise.

## Verification

```sh
python3 tests/test_hermes_provider.py
python3 tests/test_hermes_compat.py \
  --hermes-root /path/to/hermes-agent-v0.21.4
```

The second command imports Hermes' real ABC and `MemoryManager`, uses a
temporary isolated `HERMES_HOME`, and fails unless Hermes reports exactly
0.21.4. See [`../integrations/hermes/VALIDATION.md`](../integrations/hermes/VALIDATION.md).

## Benchmark interpretation

The published LOCOMO result is a retrieval result. Judge-free evidence recall
is reported separately from deterministic lexical answer coverage; neither is
presented as generated-answer accuracy. See
[`BENCHMARKS_LOCOMO.md`](./BENCHMARKS_LOCOMO.md).
