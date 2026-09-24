#!/usr/bin/env python3
"""Create public aggregate-only benchmark artifacts with strict provenance.

Raw reports may contain dataset questions, expected answers, model responses,
local paths, and per-question records.  Public reports are projected onto the
explicit aggregate schema below; unknown fields or container shapes fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

_SANITIZER_VERSION = "2"
_LOCAL_METADATA_KEYS = {"argv", "db_path", "dataset_path", "output_path"}
_SCALAR = (str, int, float, bool, type(None))

_METADATA_SCALARS = {
    "artifact_scope",
    "benchmark",
    "cost_usd",
    "dataset",
    "dataset_sha256",
    "date_utc",
    "execution_source_commit",
    "first_committed_artifact_commit",
    "first_committed_runner_commit",
    "full_context_tokens_ref",
    "full_context_tokens_ref_source",
    "git_commit",
    "hardware",
    "historical_git_commit",
    "historical_reproduction_note",
    "historical_runner",
    "input_price_per_1m_usd",
    "judge",
    "judge_free_primary_metric",
    "judge_model",
    "llm_grounded",
    "metric_notice",
    "model",
    "platform",
    "provider",
    "python",
    "reproduction_commit",
    "retrieval_cost_usd",
    "retrieval_mode",
    "runner",
    "scoring_protocol",
    "substrate",
    "top_k",
}
_DATASET_COUNT_KEYS = {
    "adversarial",
    "conversations",
    "non_adversarial",
    "questions_total",
    "turns",
}
_TOP_LEVEL_KEYS = {
    "_metadata",
    "_provenance",
    "adversarial",
    "answer_coverage",
    "answer_coverage_with_adversarial",
    "by_category",
    "cost_usd",
    "evidence_recall",
    "frontier",
    "full_context_baseline",
    "historical_summary",
    "ingest",
    "judge",
    "latency",
    "runs",
    "samples_run",
    "tokens",
    "tokens_to_reach",
    "top_k_sweep_fts",
}


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _scalar(value: Any, path: str) -> None:
    if not isinstance(value, _SCALAR):
        raise ValueError(f"{path} must be a scalar")


def _fixed_object(
    value: Any,
    path: str,
    allowed: set[str],
    *,
    required: set[str] | None = None,
) -> dict[str, Any]:
    obj = _object(value, path)
    unexpected = sorted(set(obj) - allowed)
    if unexpected:
        raise ValueError(f"{path} has unexpected fields: {', '.join(unexpected)}")
    missing = sorted((required or set()) - set(obj))
    if missing:
        raise ValueError(f"{path} is missing required fields: {', '.join(missing)}")
    return obj


def _scalar_object(
    value: Any,
    path: str,
    allowed: set[str],
    *,
    required: set[str] | None = None,
) -> None:
    obj = _fixed_object(value, path, allowed, required=required)
    for key, child in obj.items():
        _scalar(child, f"{path}.{key}")


def _validate_metadata(value: Any) -> None:
    allowed = _METADATA_SCALARS | {"dataset_counts"}
    obj = _fixed_object(value, "_metadata", allowed)
    for key, child in obj.items():
        if key == "dataset_counts":
            _scalar_object(child, "_metadata.dataset_counts", _DATASET_COUNT_KEYS)
        else:
            _scalar(child, f"_metadata.{key}")


def _validate_provenance(value: Any) -> None:
    allowed = {
        "dataset_sha256",
        "dataset_text_included",
        "per_question_records_omitted",
        "sanitizer",
        "source_bytes",
        "source_name",
        "source_sha256",
    }
    required = allowed - {"dataset_sha256"}
    obj = _fixed_object(value, "_provenance", allowed, required=required)
    for key, child in obj.items():
        _scalar(child, f"_provenance.{key}")
    sha = obj["source_sha256"]
    if not isinstance(sha, str) or len(sha) != 64 or any(
        char not in "0123456789abcdef" for char in sha
    ):
        raise ValueError("_provenance.source_sha256 must be lowercase SHA-256")
    if obj["per_question_records_omitted"] is not True:
        raise ValueError("provenance must attest that per-question records were omitted")
    if obj["dataset_text_included"] is not False:
        raise ValueError("provenance must attest that dataset text is excluded")
    if not isinstance(obj["source_bytes"], int) or isinstance(obj["source_bytes"], bool):
        raise ValueError("_provenance.source_bytes must be an integer")


def _validate_category_map(value: Any, path: str) -> None:
    obj = _object(value, path)
    allowed = {"coverage_rate", "legacy_coverage_rate", "matched", "passed", "total"}
    for name, metrics in obj.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{path} category names must be non-empty strings")
        _scalar_object(metrics, f"{path}.{name}", allowed)


def _validate_evidence(value: Any, path: str) -> None:
    obj = _fixed_object(value, path, {"all_found_rate", "by_category", "mean", "n"})
    for key, child in obj.items():
        if key == "by_category":
            categories = _object(child, f"{path}.by_category")
            for category, score in categories.items():
                _scalar(score, f"{path}.by_category.{category}")
        else:
            _scalar(child, f"{path}.{key}")


def _validate_latency(value: Any, path: str) -> None:
    obj = _fixed_object(value, path, {"llm", "search", "wall_clock_s"})
    for key, child in obj.items():
        if key in {"llm", "search"}:
            _scalar_object(
                child,
                f"{path}.{key}",
                {"max_ms", "mean_ms", "n", "p50_ms", "p95_ms"},
            )
        else:
            _scalar(child, f"{path}.{key}")


def _validate_tokens(value: Any, path: str) -> None:
    obj = _fixed_object(
        value,
        path,
        {
            "context_per_probe_mean_est",
            "context_per_probe_p95_est",
            "context_total_est",
            "ingested_total_est",
            "method",
        },
    )
    for key, child in obj.items():
        if key == "method":
            _scalar_object(child, f"{path}.method", {"context", "ingested"})
        else:
            _scalar(child, f"{path}.{key}")


def _validate_run(value: Any, path: str) -> None:
    allowed = {
        "adversarial",
        "answer_coverage",
        "answer_coverage_with_adversarial",
        "by_category",
        "cost_usd",
        "evidence_recall",
        "ingest",
        "judge",
        "latency",
        "legacy_answer_coverage",
        "samples_run",
        "tokens",
    }
    obj = _fixed_object(value, path, allowed)
    scalar_fields = {"answer_coverage_with_adversarial", "cost_usd", "judge", "samples_run"}
    for key, child in obj.items():
        child_path = f"{path}.{key}"
        if key in scalar_fields:
            _scalar(child, child_path)
        elif key == "by_category":
            _validate_category_map(child, child_path)
        elif key == "evidence_recall":
            _validate_evidence(child, child_path)
        elif key == "latency":
            _validate_latency(child, child_path)
        elif key == "tokens":
            _validate_tokens(child, child_path)
        elif key == "ingest":
            _scalar_object(
                child, child_path, {"samples", "seconds", "turns_total", "writes_per_sec"}
            )
        elif key == "adversarial":
            _scalar_object(child, child_path, {"note", "passed", "score", "scored", "total"})
        elif key == "answer_coverage":
            _scalar_object(
                child,
                child_path,
                {"is_answer_accuracy", "metric", "passed", "rate", "total"},
            )
        elif key == "legacy_answer_coverage":
            _scalar_object(
                child,
                child_path,
                {"comparable_to_hardened_v0.9.8", "is_answer_accuracy", "judge", "matched", "rate", "total"},
            )


def _validate_frontier_point(value: Any, path: str) -> None:
    _scalar_object(
        value,
        path,
        {
            "context_tokens_per_probe",
            "context_tokens_per_probe_est",
            "cost_per_1k_questions_usd",
            "evidence_recall",
            "evidence_recall_mean",
            "k",
            "legacy_answer_coverage",
            "legacy_answer_coverage_rate",
            "legacy_coverage_per_1k_tokens",
            "pct_of_ceiling_legacy_coverage",
            "pct_of_full_context_tokens",
            "questions",
            "search_p50_ms",
            "search_p95_ms",
            "token_savings_vs_full_pct",
        },
    )


def _validate_array(value: Any, path: str, item_validator: Callable[[Any, str], None]) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    for index, item in enumerate(value):
        item_validator(item, f"{path}[{index}]")


def assert_aggregate_only(report: dict[str, Any]) -> None:
    """Validate a public artifact against a closed aggregate-only schema."""
    obj = _fixed_object(
        report,
        "report",
        _TOP_LEVEL_KEYS,
        required={"_metadata", "_provenance"},
    )
    _validate_metadata(obj["_metadata"])
    _validate_provenance(obj["_provenance"])

    scalar_fields = {"answer_coverage_with_adversarial", "cost_usd", "judge", "samples_run"}
    for key, child in obj.items():
        if key in {"_metadata", "_provenance"}:
            continue
        path = f"report.{key}"
        if key in scalar_fields:
            _scalar(child, path)
        elif key == "runs":
            for name, run in _object(child, path).items():
                _validate_run(run, f"{path}.{name}")
        elif key == "top_k_sweep_fts":
            for name, point in _object(child, path).items():
                _validate_frontier_point(point, f"{path}.{name}")
        elif key == "frontier":
            _validate_array(child, path, _validate_frontier_point)
        elif key == "tokens_to_reach":
            _validate_array(
                child,
                path,
                lambda item, item_path: _scalar_object(
                    item,
                    item_path,
                    {"context_tokens_per_probe", "k", "legacy_coverage_target"},
                ),
            )
        elif key == "full_context_baseline":
            _validate_frontier_point(child, path)
        elif key == "historical_summary":
            summary = _fixed_object(
                child, path, {"cost_ratio_full_over_deepest", "deepest_point", "most_efficient_point"}
            )
            for summary_key, summary_value in summary.items():
                if summary_key == "cost_ratio_full_over_deepest":
                    _scalar(summary_value, f"{path}.{summary_key}")
                else:
                    _validate_frontier_point(summary_value, f"{path}.{summary_key}")
        else:
            _validate_run({key: child}, "report")


def sanitize_report(
    report: dict[str, Any], *, source_bytes: bytes, source_name: str
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise TypeError("benchmark report must be a JSON object")

    unexpected = sorted(set(report) - (_TOP_LEVEL_KEYS | {"results"}))
    if unexpected:
        raise ValueError("raw report has fields outside aggregate schema: " + ", ".join(unexpected))
    if "results" in report and not isinstance(report["results"], list):
        raise ValueError("raw report results must be an array")

    sanitized = {key: value for key, value in report.items() if key != "results"}
    metadata = sanitized.get("_metadata")
    if metadata is not None:
        metadata = _object(metadata, "_metadata")
        sanitized["_metadata"] = {
            key: value for key, value in metadata.items() if key not in _LOCAL_METADATA_KEYS
        }
    else:
        sanitized["_metadata"] = {}

    sanitized["_provenance"] = {
        "sanitizer": f"bench/sanitize_results.py v{_SANITIZER_VERSION}",
        "source_name": Path(source_name).name,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_bytes": len(source_bytes),
        "dataset_sha256": sanitized["_metadata"].get("dataset_sha256"),
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
