# Release procedure

Mnemosyne is not published on PyPI. Releases are source tags plus GitHub release
assets. Do not use `twine upload` or claim that a package-name-only pip install
works.

## v0.9.8 release gates

From a clean checkout:

```sh
python3 -m venv .release-venv
.release-venv/bin/python -m pip install --upgrade pip build twine pyflakes
.release-venv/bin/python tests/test_all.py
/bin/bash test-harness.sh
.release-venv/bin/python integrations/hermes/test_provider.py
.release-venv/bin/python bench/test_benchmark.py
.release-venv/bin/python bench/longmemeval.py --selftest

rm -rf build dist *.egg-info
.release-venv/bin/python -m build
.release-venv/bin/python -m twine check dist/*
```

Run the Hermes compatibility gate with Python 3.11+ and an exact Hermes Agent
v0.21.4 checkout:

```sh
python3 integrations/hermes/test_hermes_compat.py \
  --hermes-root /path/to/hermes-agent-v0.21.4
```

## Wheel-install verification

```sh
python3 -m venv .wheel-venv
.whl-venv/bin/python -m pip install dist/mnemosyne_harness-0.9.8-py3-none-any.whl
.whl-venv/bin/python -c '
from importlib.metadata import entry_points, version
assert version("mnemosyne-harness") == "0.9.8"
eps = entry_points(group="hermes_agent.memory_providers")
ep = next(ep for ep in eps if ep.name == "mnemosyne")
assert callable(ep.load())
print("wheel + Hermes entry point OK")
'
.whl-venv/bin/mnemosyne-memory --help >/dev/null
```

Inspect wheel contents and confirm these are present:

- `integrations/hermes/__init__.py`
- `integrations/hermes/plugin.yaml`
- `integrations/hermes/README.md`
- `mnemosyne_memory.py`
- UI static assets

Do not commit `dist/`, `build/`, virtual environments, raw benchmark reports,
or datasets.

## Public-safety checks

```sh
git diff --check
git status --short
git grep -nE 'pip(3)? install mnemosyne-harness|pypi\.org/project/mnemosyne-harness'
python3 -c "import json; from bench.sanitize_results import assert_aggregate_only; assert_aggregate_only(json.load(open('docs/benchmark-results/2026-06-11-locomo-retrieval-track.json')))"
```

Review the grep output. Historical changelog discussion may mention planned
PyPI work, but current installation instructions must use a GitHub tag/release
asset. Public benchmark artifacts must be aggregate-only and contain no dataset
questions, expected answers, generated responses, or per-question records.

## Cut the GitHub release (maintainer only)

After all local gates pass and the release commit is on the default branch:

```sh
git tag -s v0.9.8 -m "Mnemosyne v0.9.8"
git push origin v0.9.8
```

Create the GitHub release from tag `v0.9.8` and attach:

- `dist/mnemosyne_harness-0.9.8-py3-none-any.whl`
- `dist/mnemosyne_harness-0.9.8.tar.gz`
- checksums generated from those exact files

This repository task does **not** perform those remote operations.

## User installation

Source tag:

```sh
python3 -m pip install \
  "https://github.com/atxgreene/Mnemosyne/archive/refs/tags/v0.9.8.tar.gz"
```

After the GitHub release asset exists, users may install its wheel URL directly:

```sh
python3 -m pip install \
  "https://github.com/atxgreene/Mnemosyne/releases/download/v0.9.8/mnemosyne_harness-0.9.8-py3-none-any.whl"
```

## Rollback

GitHub releases and tags are immutable release evidence. If v0.9.8 is broken,
mark the release as affected, publish a fixed patch version, and update current
documentation. Do not replace an existing asset or move the tag.
