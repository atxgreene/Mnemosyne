# Release procedure

How to cut a Mnemosyne release. Steps assume you're the maintainer
with write access to PyPI and to the GitHub repo.

## Prereqs (one-time)

```sh
pip install --upgrade build twine
# Set up your PyPI token (https://pypi.org/manage/account/token/)
mkdir -p ~/.config/pypi
cat > ~/.pypirc <<EOF
[pypi]
username = __token__
password = pypi-AgEI…   # your token here
EOF
chmod 600 ~/.pypirc
```

## Cut a release

```sh
# 1. Bump the version in pyproject.toml and CHANGELOG.md
# 2. Verify everything is clean
python3 tests/test_all.py             # expect green
python3 -m pyflakes *.py examples/*.py tests/*.py
shellcheck -x *.sh
bash test-harness.sh                  # 29 integration assertions

# 3. Tag the commit
git tag v$(grep -E '^version = ' pyproject.toml | cut -d'"' -f2)
git push origin --tags

# 4. Build artifacts (wheel + sdist) in a clean venv
rm -rf dist build *.egg-info
python3 -m venv /tmp/release-venv
/tmp/release-venv/bin/pip install --quiet --upgrade pip build twine
/tmp/release-venv/bin/python3 -m build

# 5. Sanity-check the artifacts
/tmp/release-venv/bin/python3 -m twine check dist/*

# 6. Test-install from the wheel in a separate clean venv
rm -rf /tmp/install-test
python3 -m venv /tmp/install-test
/tmp/install-test/bin/pip install dist/mnemosyne_harness-*.whl
/tmp/install-test/bin/mnemosyne-resolver check    # should pass clean
/tmp/install-test/bin/python3 -c "
from mnemosyne_brain import Brain
from mnemosyne_resolver import check_resolvable
from mnemosyne_avatar import compute_state
print('imports OK')"

# 7. Upload to TestPyPI first (catches metadata bugs without burning the prod name)
/tmp/release-venv/bin/python3 -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ --no-deps mnemosyne-harness

# 8. Upload to PyPI proper
/tmp/release-venv/bin/python3 -m twine upload dist/*

# 9. Create a GitHub release
gh release create v0.3.5 \
    --title "v0.3.5 — routing-layer audit" \
    --notes-file <(awk '/^## \[0.3.5\]/,/^## \[0.3.4\]/' CHANGELOG.md | head -n -1) \
    dist/mnemosyne_harness-0.3.5*

# 10. Verify pip install mnemosyne-harness picks up the new version
pip install --upgrade mnemosyne-harness
mnemosyne-resolver check
```

## What ships in the artifact

| Path | Why |
|---|---|
| `mnemosyne_*.py` (~22 modules) | core library |
| `harness_*.py`, `scenario_runner.py` | observability substrate |
| `obsidian_search.py`, `notion_search.py` | bundled skills |
| `environment_snapshot.py` | first-turn context tool |
| `mnemosyne_ui/` package + `static/*` assets | dashboard |
| `scenarios.example.jsonl` | example evals |
| `LICENSE`, `README.md` | metadata |
| 21 console scripts | via `[project.scripts]` |

`scenarios/jailbreak.jsonl`, `examples/`, `tests/`, `docs/` are NOT shipped
in the wheel — they're development artifacts. Users get them by cloning
the repo.

## Rollback

If a release is broken, yank it from PyPI:

```sh
twine yank mnemosyne-harness 0.3.5 --reason "broken UI assets"
```

Yanked versions aren't installed by `pip install mnemosyne-harness` but
remain installable by exact pin (`==0.3.5`) so users with that version
pinned aren't forced to upgrade.
