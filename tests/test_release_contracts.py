#!/usr/bin/env python3
"""Offline release/documentation contract gates for the public core."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_BARE_INSTALL = re.compile(
    r"(?:pip|pip3|pipx)\s+install\s+"
    r"[\"']?mnemosyne-harness(?:\[[^\]\r\n]+\])?[\"']?"
    r"(?!\s*@)(?=\s|$|[;&|`)])",
    re.IGNORECASE,
)
_ACTION_PIN = re.compile(r"^\s*-\s+uses:\s+([^\s@]+)@([^\s#]+)", re.MULTILINE)


def bare_pypi_claims(text: str) -> list[str]:
    """Return package-name-only install claims, including quoted extras."""
    logical = re.sub(r"\\\s*\n\s*", " ", text)
    return [match.group(0) for match in _BARE_INSTALL.finditer(logical)]


class PublicClaimsTests(unittest.TestCase):
    def test_bare_pypi_detector_catches_quotes_and_extras(self):
        mutations = (
            "pip install mnemosyne-harness",
            "pip3 install 'mnemosyne-harness'",
            'python3 -m pip install "mnemosyne-harness[train]"',
            "pipx install mnemosyne-harness[dev]",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertTrue(bare_pypi_claims(mutation))

    def test_current_public_docs_make_no_bare_pypi_claim(self):
        current_docs = [_REPO / "README.md", *sorted((_REPO / "docs").glob("*.md"))]
        leaks = {
            str(path.relative_to(_REPO)): bare_pypi_claims(path.read_text(encoding="utf-8"))
            for path in current_docs
        }
        self.assertEqual({path: hits for path, hits in leaks.items() if hits}, {})

    def test_landing_page_matches_supported_provider_contract(self):
        page = (_REPO / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("v0.16.0", page)
        self.assertNotIn("2.2–2.5×", page)
        self.assertNotIn("tier 2–5", page)
        self.assertNotIn("Session-end hooks run salient extraction", page)
        self.assertIn("v0.21.4", page)
        self.assertIn("tiers L2–L4", page)

    def test_public_tests_use_neutral_synthetic_identities(self):
        personal_names = (
            "Ali" + "ce",
            "Aus" + "tin",
            "B" + "ob",
            "Pri" + "ya",
            "Cedar" + " Park",
            "Dal" + "las",
            "Ber" + "lin",
        )
        personal_fixture_terms = re.compile(
            r"\b(?:" + "|".join(map(re.escape, personal_names)) + r")\b",
            re.IGNORECASE,
        )
        hits = {}
        for path in sorted((_REPO / "tests").glob("test*.py")) + sorted(
            (_REPO / "bench").glob("test*.py")
        ):
            matches = sorted(set(personal_fixture_terms.findall(path.read_text(encoding="utf-8"))))
            if matches:
                hits[str(path.relative_to(_REPO))] = matches
        self.assertEqual(hits, {})


class ReleaseMechanicsTests(unittest.TestCase):
    def test_legacy_portable_edit_selftest(self):
        result = subprocess.run(
            ["/bin/bash", str(_REPO / "install-mnemosyne.sh"), "--self-test-portable-edit"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("portable edit self-test passed", result.stdout)

    def test_release_uses_one_wheel_venv_name_and_pinned_tools(self):
        release = (_REPO / "RELEASE.md").read_text(encoding="utf-8")
        self.assertNotIn(".whl-venv", release)
        for requirement in ("build==1.3.0", "twine==6.2.0", "pyflakes==3.4.0"):
            self.assertIn(requirement, release)

    def test_workflow_actions_are_full_sha_pinned_and_read_only(self):
        workflow = (_REPO / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", workflow)
        pins = _ACTION_PIN.findall(workflow)
        self.assertTrue(pins)
        expected = {
            "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
            "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
        }
        for action, ref in pins:
            with self.subTest(action=action):
                self.assertRegex(ref, r"^[0-9a-f]{40}$")
                self.assertEqual(ref, expected[action])

    def test_build_backend_is_exactly_pinned(self):
        pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires = ["setuptools==80.9.0"]', pyproject)
        for package in (
            '"mnemosyne_ui"',
            '"mnemosyne_ui.static"',
            '"integrations"',
            '"integrations.hermes"',
        ):
            self.assertIn(package, pyproject)
        self.assertNotIn("[tool.setuptools.packages.find]", pyproject)

    def test_benchmark_provenance_commits_contain_reproduction_code(self):
        cases = (
            (
                "2026-06-11-locomo-retrieval-track.json",
                "bench/locomo.py",
                "b73d1c8e4e3a3d77d0d2985027298fc838b5c5c5",
                "e6a1db7f76bc8ff8e5e90e28089bc27163cbf460",
                "c68949401983dc0f888f3a05019a9acd4eab5447",
            ),
            (
                "2026-07-01-locomo-efficiency-frontier.json",
                "bench/efficiency_frontier.py",
                "c68949401983dc0f888f3a05019a9acd4eab5447",
                "9d23404280b49762522241c1d0705f80b55f8325",
                "9d23404280b49762522241c1d0705f80b55f8325",
            ),
        )
        for artifact_name, runner, source_parent, runner_commit, artifact_commit in cases:
            with self.subTest(artifact=artifact_name):
                path = _REPO / "docs" / "benchmark-results" / artifact_name
                metadata = json.loads(path.read_text(encoding="utf-8"))["_metadata"]
                self.assertEqual(metadata["execution_source_commit"], source_parent)
                self.assertEqual(metadata["first_committed_runner_commit"], runner_commit)
                self.assertEqual(metadata["first_committed_artifact_commit"], artifact_commit)
                self.assertEqual(metadata["reproduction_commit"], artifact_commit)
                parent = subprocess.run(
                    ["git", "rev-parse", f"{artifact_commit}^"],
                    cwd=_REPO,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                self.assertEqual(parent, source_parent)
                for tracked_path, first_commit in (
                    (runner, runner_commit),
                    (f"docs/benchmark-results/{artifact_name}", artifact_commit),
                ):
                    additions = subprocess.run(
                        [
                            "git",
                            "log",
                            "--diff-filter=A",
                            "--format=%H",
                            "--",
                            tracked_path,
                        ],
                        cwd=_REPO,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.splitlines()
                    self.assertTrue(additions, tracked_path)
                    self.assertEqual(additions[-1], first_commit)
                for commit_path in (
                    f"{artifact_commit}:{runner}",
                    f"{artifact_commit}:docs/benchmark-results/{artifact_name}",
                ):
                    subprocess.run(
                        ["git", "cat-file", "-e", commit_path],
                        cwd=_REPO,
                        check=True,
                        capture_output=True,
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
