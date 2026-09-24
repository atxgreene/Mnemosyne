#!/usr/bin/env python3
"""Create public aggregate-only benchmark artifacts with provenance.

Raw benchmark reports may contain dataset questions, expected answers, model
responses, local paths, and one record per question. Those reports belong in
ignored scratch storage. This module emits only aggregate metrics and an audit
trail linking the public artifact to the raw file by SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

_SANITIZER_VERSION = "1"
_FORBIDDEN_KEYS = {
    "question",
    "expected",
    "actual",
    "actual_preview",
    "answer",
    "response",
    "response_preview",
    "content",
    "text",
    "results",
}
_LOCAL_PATH_METADATA_KEYS = {"argv", "db_path", "dataset_path", "output_path"}


def _scrub(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for child_key, child_value in value.items():
            normalized = str(child_key).lower()
            if normalized in _FORBIDDEN_KEYS or normalized in _LOCAL_PATH_METADATA_KEYS:
                continue
            cleaned[child_key] = _scrub(child_value, key=normalized)
        return cleaned
    if isinstance(value, list):
        return [_scrub(item, key=key) for item in value]
    return value


def _walk_keys(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield str(key).lower(), path
            yield from _walk_keys(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{prefix}[{index}]")


def assert_aggregate_only(report: dict[str, Any]) -> None:
    """Reject dataset-text fields anywhere in a candidate public artifact."""
    leaks = [path for key, path in _walk_keys(report) if key in _FORBIDDEN_KEYS]
    if leaks:
        raise ValueError("dataset/per-question fields remain: " + ", ".join(leaks[:10]))
    provenance = report.get("_provenance")
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise ValueError("_provenance must be an object")
        sha = str(provenance.get("source_sha256") or "")
        if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("_provenance.source_sha256 must be lowercase SHA-256")
        if provenance.get("per_question_records_omitted") is not True:
            raise ValueError("provenance must attest that per-question records were omitted")


def sanitize_report(
    report: dict[str, Any], *, source_bytes: bytes, source_name: str
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise TypeError("benchmark report must be a JSON object")
    sanitized = _scrub(report)
    metadata = sanitized.get("_metadata")
    sanitized["_provenance"] = {
        "sanitizer": f"bench/sanitize_results.py v{_SANITIZER_VERSION}",
        "source_name": Path(source_name).name,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_bytes": len(source_bytes),
        "dataset_sha256": (
            metadata.get("dataset_sha256")
            if isinstance(metadata, dict)
            else None
        ),
        "per_question_records_omitted": True,
        "dataset_text_included": False,
    }
    assert_aggregate_only(sanitized)
    return sanitized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sanitize a raw benchmark report into an aggregate-only artifact"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)

    source_bytes = args.source.read_bytes()
    raw = json.loads(source_bytes)
    sanitized = sanitize_report(
        raw, source_bytes=source_bytes, source_name=args.source.name
    )
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(
        json.dumps(sanitized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"sanitized aggregate artifact: {args.destination} "
        f"(source sha256={sanitized['_provenance']['source_sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
