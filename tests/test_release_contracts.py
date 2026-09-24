#!/usr/bin/env python3
"""Offline release/documentation contract gates for the public core."""

from __future__ import annotations

import html
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
_PUBLIC_DOC_EXTENSIONS = frozenset({".htm", ".html", ".markdown", ".md", ".mdx", ".rst", ".txt"})
_PUBLIC_DOC_BASENAMES = frozenset({"install", "installation", "readme", "release", "setup"})
_PUBLIC_INSTALL_NAME_MARKERS = ("install", "release", "setup")
_SHELL_BLOCK = re.compile(r"```(?:bash|console|sh|shell)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_HTML_CODE_BLOCK = re.compile(r'<div class="code">(.*?)</div>', re.DOTALL | re.IGNORECASE)


def bare_pypi_claims(text: str) -> list[str]:
    """Return package-name-only install claims, including quoted extras."""
    logical = re.sub(r"\\\s*\n\s*", " ", text)
    return [match.group(0) for match in _BARE_INSTALL.finditer(logical)]


def public_install_surfaces() -> list[Path]:
    """Return tracked public documentation/install surfaces without directory omissions."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    paths = []
    for raw_path in tracked:
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        stem = relative.stem.lower()
        if (
            relative.suffix.lower() in _PUBLIC_DOC_EXTENSIONS
            or stem in _PUBLIC_DOC_BASENAMES
            or (
                relative.suffix.lower() == ".sh"
                and any(marker in stem for marker in _PUBLIC_INSTALL_NAME_MARKERS)
            )
        ):
            paths.append(_REPO / relative)
    return sorted(paths)


def packaged_console_scripts() -> set[str]:
    """Return the wheel's declared console commands without importing build tooling."""
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    scripts = pyproject.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    return {
        match.group(1)
        for match in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=", scripts, re.MULTILINE)
    }


def documented_post_install_commands(path: Path) -> list[tuple[str, bool]]:
    """Return Mnemosyne commands in install blocks and whether each is checkout-only."""
    text = path.read_text(encoding="utf-8")
    blocks = list(_SHELL_BLOCK.findall(text)) + list(_HTML_CODE_BLOCK.findall(text))
    claims = []
    for raw_block in blocks:
        plain = html.unescape(re.sub(r"<[^>]+>", "", raw_block))
        if "pip install" not in plain or not re.search(
            r"mnemosyne(?:-harness)?", plain, re.IGNORECASE
        ):
            continue
        checkout_only = "checkout-only" in plain.lower()
        for match in re.finditer(
            r"(?m)^\s*(?:\$\s*)?(?:bash\s+)?(?:\./)?(mnemosyne-[A-Za-z0-9._-]+)",
            plain,
        ):
            claims.append((match.group(1), checkout_only))
    return claims


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
        leaks = {
            str(path.relative_to(_REPO)): bare_pypi_claims(path.read_text(encoding="utf-8"))
            for path in public_install_surfaces()
        }
        self.assertEqual({path: hits for path, hits in leaks.items() if hits}, {})

    def test_claim_scan_covers_every_install_surface_class(self):
        relative = {path.relative_to(_REPO) for path in public_install_surfaces()}
        self.assertIn(Path("README.md"), relative)
        self.assertIn(Path("RELEASE.md"), relative)
        self.assertIn(Path("SETUP.md"), relative)
        self.assertIn(Path("docs/index.html"), relative)
        self.assertIn(Path("docs/articles/v0.8-launch-substack.md"), relative)
        self.assertIn(Path("bench/README.md"), relative)
        self.assertIn(Path("integrations/hermes/README.md"), relative)
        self.assertIn(Path("integrations/hermes/VALIDATION.md"), relative)
        self.assertIn(Path("install-mnemosyne.sh"), relative)
        self.assertIn(Path("deploy/install-service.sh"), relative)

    def test_packaged_integration_readme_mutation_catches_quoted_extra(self):
        readme = (_REPO / "integrations" / "hermes" / "README.md").read_text(
            encoding="utf-8"
        )
        mutation = readme + '\npython3 -m pip install "mnemosyne-harness[train]"\n'
        self.assertEqual(
            bare_pypi_claims(mutation),
            ['pip install "mnemosyne-harness[train]"'],
        )

    def test_public_commands_use_current_hermes_test_paths(self):
        public_command_surfaces = {
            *public_install_surfaces(),
            *((_REPO / "integrations" / "hermes").glob("*.md")),
        }
        joined = "\n".join(
            path.read_text(encoding="utf-8") for path in public_command_surfaces
        )
        self.assertNotIn("integrations/hermes/test_provider.py", joined)
        self.assertNotIn("integrations/hermes/test_hermes_compat.py", joined)
        self.assertIn("python3 tests/test_hermes_provider.py", joined)
        self.assertIn("python3 tests/test_hermes_compat.py", joined)

    def test_landing_page_matches_supported_provider_contract(self):
        page = (_REPO / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("v0.16.0", page)
        self.assertNotIn("2.2–2.5×", page)
        self.assertNotIn("tier 2–5", page)
        self.assertNotIn("Session-end hooks run salient extraction", page)
        self.assertNotIn("prefetch (non-blocking)", page)
        self.assertNotIn("fresh-session recall", page)
        self.assertNotIn("8/8", page)
        self.assertNotIn("hermes plugins enable mnemosyne", page)
        self.assertNotIn("hermes plugins list", page)
        self.assertNotIn("enabled  user", page)
        self.assertIn("v0.21.4", page)
        self.assertIn("hermes config set memory.provider mnemosyne", page)
        self.assertIn("hermes memory status", page)
        self.assertIn("tiers L2–L4", page)

    def test_post_install_commands_are_packaged_or_explicitly_checkout_only(self):
        packaged = packaged_console_scripts()
        claims = {
            path.relative_to(_REPO): documented_post_install_commands(path)
            for path in public_install_surfaces()
        }
        self.assertTrue(any(commands for commands in claims.values()))
        failures = []
        for path, commands in claims.items():
            for command, checkout_only in commands:
                if command in packaged:
                    continue
                if checkout_only and (_REPO / command).is_file():
                    continue
                failures.append(f"{path}: {command}")
        self.assertEqual(failures, [])

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
        checkout_steps = re.findall(
            r"(?ms)^\s*- uses: actions/checkout@[^\n]+\n(?P<body>(?:\s{8,}[^\n]*\n)*)",
            workflow,
        )
        self.assertTrue(checkout_steps)
        for body in checkout_steps:
            with self.subTest(step=body):
                self.assertRegex(body, r"(?m)^\s+persist-credentials:\s*false\s*$")

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
