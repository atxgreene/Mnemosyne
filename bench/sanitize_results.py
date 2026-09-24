#!/usr/bin/env python3
"""Create public aggregate-only benchmark artifacts with strict provenance.

Raw reports may contain dataset questions, expected answers, model responses,
local paths, host details, and per-question records. Public reports are
projected onto the closed aggregate schema below. Unknown fields, dynamic names,
and unconstrained strings fail closed; known host/free-text metadata is omitted.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

_SANITIZER_VERSION = "3"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^(?:[0-9a-f]{7,40}|unknown)$")
_DATE_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?$")
_PYTHON_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_RUNNER = re.compile(r"^bench/(?:locomo|longmemeval|efficiency_frontier)\.py v\d+\.\d+\.\d+$")
_SOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.json$")
_TOP_K_NAME = re.compile(r"^k(?:[1-9]|[1-9][0-9]|100)$")

# Only these dynamic object keys can cross the public boundary. A generic slug
# check is insufficient: dataset text can itself be made slug-shaped.
_RUN_NAMES = {"fts", "full", "random", "recency", "mnemosyne", "mem0"}
_CATEGORY_NAMES = {
    "adversarial",
    "cross_session",
    "fact",
    "goal",
    "identity",
    "multi_hop",
    "open_domain",
    "preference",
    "rule",
    "single_hop",
    "temporal",
    "uncategorized",
}
_JUDGES = {"answer_coverage", "openai", "retired_any_content_token_or_phrase"}
_METRICS = {"deterministic_lexical_coverage", "openai_llm_judge"}
_ADVERSARIAL_NOTES = {
    "abstention-judged (llm-grounded)",
    "not scored: retrieval-only mode has no model to abstain; excluded from headline score",
}
_TOKEN_METHODS = {"chars/4", "chars/4 estimate", "tiktoken", "tiktoken/cl100k_base"}
_ARTIFACT_SCOPES = {
    "aggregate-only; no questions, expected answers, model responses, or per-question records",
    "aggregate-only; no dataset questions, answers, responses, or per-question records",
}
_BENCHMARKS = {
    "LOCOMO (snap-research/locomo, locomo10.json)",
    "LOCOMO retrieval track (snap-research/locomo, locomo10.json)",
    "LOCOMO efficiency frontier (retrieval substrate)",
}
_FULL_CONTEXT_SOURCES = {"Mem0 arXiv:2504.19413 (~26k tok/conv)"}
_RETRIEVAL_MODES = {"fts", "full", "random", "recency", "mem0"}

# Raw runners can emit these metadata keys. Local paths and unconstrained prose
# are accepted only so they can be deliberately discarded, never copied.
_RAW_METADATA_KEYS = {
    "argv",
    "artifact_scope",
    "benchmark",
    "cost_usd",
    "dataset",
    "dataset_counts",
    "dataset_path",
    "dataset_sha256",
    "date_utc",
    "db_path",
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
    "output_path",
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

# These metadata fields have value validators strong enough to be copied. The
# omitted raw fields above are free text, local details, or dynamic identifiers.
_PUBLIC_METADATA_FIELDS = {
    "artifact_scope",
    "benchmark",
    "cost_usd",
    "dataset_counts",
    "dataset_sha256",
    "date_utc",
    "execution_source_commit",
    "first_committed_artifact_commit",
    "first_committed_runner_commit",
    "full_context_tokens_ref",
    "full_context_tokens_ref_source",
    "git_commit",
    "input_price_per_1m_usd",
    "judge",
    "llm_grounded",
    "python",
    "reproduction_commit",
    "retrieval_cost_usd",
    "retrieval_mode",
    "runner",
    "top_k",
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
    "summary",
    "tokens",
    "tokens_to_reach",
    "top_k_sweep_fts",
}

Validator = Callable[[Any, str], None]


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


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


def _fields(
    value: Any,
    path: str,
    validators: dict[str, Validator],
    *,
    required: set[str] | None = None,
) -> dict[str, Any]:
    obj = _fixed_object(value, path, set(validators), required=required)
    for key, child in obj.items():
        validators[key](child, f"{path}.{key}")
    return obj


def _bool(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")


def _integer(value: Any, path: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{path} must be a non-negative integer")


def _number(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{path} must be a finite non-negative number")


def _nullable(validator: Validator) -> Validator:
    def check(value: Any, path: str) -> None:
        if value is not None:
            validator(value, path)

    return check


def _ratio(value: Any, path: str) -> None:
    _number(value, path)
    if value > 1:
        raise ValueError(f"{path} must be between 0 and 1")


def _percentage(value: Any, path: str) -> None:
    _number(value, path)
    if value > 100:
        raise ValueError(f"{path} must be between 0 and 100")


def _enum(options: set[str]) -> Validator:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, str) or value not in options:
            raise ValueError(f"{path} must be one of: {', '.join(sorted(options))}")

    return check


def _regex(pattern: re.Pattern[str], label: str) -> Validator:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ValueError(f"{path} must be {label}")

    return check


_sha256 = _regex(_SHA256, "a lowercase SHA-256 digest")
_commit = _regex(_COMMIT, "a lowercase git commit or 'unknown'")
_date_utc = _regex(_DATE_UTC, "an ISO-like UTC date")
_python_version = _regex(_PYTHON_VERSION, "a Python X.Y.Z version")
_runner = _regex(_RUNNER, "a controlled benchmark runner identifier")
_source_name = _regex(_SOURCE_NAME, "a safe JSON basename")


def _validate_dataset_counts(value: Any, path: str) -> None:
    validators = {
        "adversarial": _integer,
        "conversations": _integer,
        "non_adversarial": _integer,
        "questions_total": _integer,
        "turns": _integer,
    }
    _fields(value, path, validators)


_METADATA_VALIDATORS: dict[str, Validator] = {
    "artifact_scope": _enum(_ARTIFACT_SCOPES),
    "benchmark": _enum(_BENCHMARKS),
    "cost_usd": _nullable(_number),
    "dataset_counts": _validate_dataset_counts,
    "dataset_sha256": _sha256,
    "date_utc": _date_utc,
    "execution_source_commit": _commit,
    "first_committed_artifact_commit": _commit,
    "first_committed_runner_commit": _commit,
    "full_context_tokens_ref": _integer,
    "full_context_tokens_ref_source": _enum(_FULL_CONTEXT_SOURCES),
    "git_commit": _commit,
    "input_price_per_1m_usd": _number,
    "judge": _enum(_JUDGES),
    "llm_grounded": _bool,
    "python": _python_version,
    "reproduction_commit": _commit,
    "retrieval_cost_usd": _number,
    "retrieval_mode": _enum(_RETRIEVAL_MODES),
    "runner": _runner,
    "top_k": _integer,
}


def _validate_metadata(value: Any) -> None:
    _fields(value, "_metadata", _METADATA_VALIDATORS)


def _project_metadata(value: Any) -> dict[str, Any]:
    obj = _fixed_object(value, "_metadata", _RAW_METADATA_KEYS)
    projected = {
        key: copy.deepcopy(obj[key])
        for key in _PUBLIC_METADATA_FIELDS
        if key in obj and obj[key] is not None
    }
    _validate_metadata(projected)
    return projected


def _validate_provenance(value: Any) -> None:
    validators = {
        "dataset_sha256": _nullable(_sha256),
        "dataset_text_included": _bool,
        "per_question_records_omitted": _bool,
        "sanitizer": _enum({f"bench/sanitize_results.py v{_SANITIZER_VERSION}"}),
        "source_bytes": _integer,
        "source_name": _source_name,
        "source_sha256": _sha256,
    }
    required = set(validators) - {"dataset_sha256"}
    obj = _fields(value, "_provenance", validators, required=required)
    if obj["per_question_records_omitted"] is not True:
        raise ValueError("provenance must attest that per-question records were omitted")
    if obj["dataset_text_included"] is not False:
        raise ValueError("provenance must attest that dataset text is excluded")


def _validate_category_map(value: Any, path: str) -> None:
    obj = _object(value, path)
    unexpected = sorted(set(obj) - _CATEGORY_NAMES)
    if unexpected:
        raise ValueError(f"{path} has unsupported category names: {', '.join(unexpected)}")
    validators = {
        "coverage_rate": _ratio,
        "legacy_coverage_rate": _ratio,
        "matched": _integer,
        "passed": _integer,
        "score": _ratio,
        "total": _integer,
    }
    for name, metrics in obj.items():
        _fields(metrics, f"{path}.{name}", validators)


def _validate_evidence(value: Any, path: str) -> None:
    def categories(child: Any, child_path: str) -> None:
        obj = _object(child, child_path)
        unexpected = sorted(set(obj) - _CATEGORY_NAMES)
        if unexpected:
            raise ValueError(
                f"{child_path} has unsupported category names: {', '.join(unexpected)}"
            )
        for category, score in obj.items():
            _ratio(score, f"{child_path}.{category}")

    _fields(
        value,
        path,
        {
            "all_found_rate": _nullable(_ratio),
            "by_category": categories,
            "mean": _nullable(_ratio),
            "n": _integer,
        },
    )


def _validate_latency(value: Any, path: str) -> None:
    def latency_block(child: Any, child_path: str) -> None:
        _fields(
            child,
            child_path,
            {
                "max_ms": _number,
                "mean_ms": _number,
                "n": _integer,
                "p50_ms": _number,
                "p95_ms": _number,
            },
        )

    _fields(
        value,
        path,
        {"llm": latency_block, "search": latency_block, "wall_clock_s": _number},
    )


def _validate_tokens(value: Any, path: str) -> None:
    def methods(child: Any, child_path: str) -> None:
        _fields(
            child,
            child_path,
            {"context": _enum(_TOKEN_METHODS), "ingested": _enum(_TOKEN_METHODS)},
        )

    _fields(
        value,
        path,
        {
            "context_per_probe_mean_est": _integer,
            "context_per_probe_p95_est": _integer,
            "context_total_est": _integer,
            "ingested_total_est": _integer,
            "method": methods,
        },
    )


def _validate_run(value: Any, path: str) -> None:
    def answer_coverage(child: Any, child_path: str) -> None:
        _fields(
            child,
            child_path,
            {
                "is_answer_accuracy": _bool,
                "metric": _enum(_METRICS),
                "passed": _integer,
                "rate": _ratio,
                "total": _integer,
            },
        )

    def legacy_coverage(child: Any, child_path: str) -> None:
        _fields(
            child,
            child_path,
            {
                "comparable_to_hardened_v0.9.8": _bool,
                "is_answer_accuracy": _bool,
                "judge": _enum(_JUDGES),
                "matched": _integer,
                "rate": _ratio,
                "total": _integer,
            },
        )

    def adversarial(child: Any, child_path: str) -> None:
        _fields(
            child,
            child_path,
            {
                "note": _enum(_ADVERSARIAL_NOTES),
                "passed": _integer,
                "score": _nullable(_ratio),
                "scored": _integer,
                "total": _integer,
            },
        )

    def ingest(child: Any, child_path: str) -> None:
        _fields(
            child,
            child_path,
            {
                "samples": _integer,
                "seconds": _number,
                "turns_total": _integer,
                "writes_per_sec": _nullable(_number),
            },
        )

    _fields(
        value,
        path,
        {
            "adversarial": adversarial,
            "answer_coverage": answer_coverage,
            "answer_coverage_with_adversarial": _ratio,
            "by_category": _validate_category_map,
            "cost_usd": _nullable(_number),
            "evidence_recall": _validate_evidence,
            "ingest": ingest,
            "judge": _enum(_JUDGES),
            "latency": _validate_latency,
            "legacy_answer_coverage": legacy_coverage,
            "samples_run": _integer,
            "tokens": _validate_tokens,
        },
    )


def _validate_frontier_point(value: Any, path: str) -> None:
    _fields(
        value,
        path,
        {
            "answer_coverage": _ratio,
            "context_tokens_per_probe": _integer,
            "context_tokens_per_probe_est": _integer,
            "cost_per_1k_questions_usd": _number,
            "coverage_per_1k_tokens": _number,
            "evidence_recall": _ratio,
            "evidence_recall_mean": _ratio,
            "k": _integer,
            "legacy_answer_coverage": _ratio,
            "legacy_answer_coverage_rate": _ratio,
            "legacy_coverage_per_1k_tokens": _number,
            "pct_of_ceiling_coverage": _percentage,
            "pct_of_ceiling_legacy_coverage": _percentage,
            "pct_of_full_context_tokens": _percentage,
            "questions": _integer,
            "search_p50_ms": _number,
            "search_p95_ms": _number,
            "token_savings_vs_full_pct": _percentage,
        },
    )


def _validate_array(value: Any, path: str, item_validator: Validator) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    for index, item in enumerate(value):
        item_validator(item, f"{path}[{index}]")


def _validate_summary(value: Any, path: str) -> None:
    obj = _fixed_object(
        value,
        path,
        {"cost_ratio_full_over_deepest", "deepest_point", "most_efficient_point"},
    )
    for key, child in obj.items():
        if key == "cost_ratio_full_over_deepest":
            _nullable(_number)(child, f"{path}.{key}")
        else:
            _validate_frontier_point(child, f"{path}.{key}")


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

    for key, child in obj.items():
        if key in {"_metadata", "_provenance"}:
            continue
        path = f"report.{key}"
        if key == "runs":
            runs = _object(child, path)
            unexpected = sorted(set(runs) - _RUN_NAMES)
            if unexpected:
                raise ValueError(f"{path} has unsupported run names: {', '.join(unexpected)}")
            for name, run in runs.items():
                _validate_run(run, f"{path}.{name}")
        elif key == "top_k_sweep_fts":
            points = _object(child, path)
            for name, point in points.items():
                if not isinstance(name, str) or _TOP_K_NAME.fullmatch(name) is None:
                    raise ValueError(f"{path} has unsupported top-k key: {name!r}")
                _validate_frontier_point(point, f"{path}.{name}")
        elif key == "frontier":
            _validate_array(child, path, _validate_frontier_point)
        elif key == "tokens_to_reach":
            def target(item: Any, item_path: str) -> None:
                _fields(
                    item,
                    item_path,
                    {
                        "context_tokens_per_probe": _integer,
                        "k": _integer,
                        "legacy_coverage_target": _ratio,
                        "target": _ratio,
                    },
                )
            _validate_array(child, path, target)
        elif key == "full_context_baseline":
            _validate_frontier_point(child, path)
        elif key in {"historical_summary", "summary"}:
            _validate_summary(child, path)
        else:
            _validate_run({key: child}, "report")


def sanitize_report(
    report: dict[str, Any], *, source_bytes: bytes, source_name: str
) -> dict[str, Any]:
    """Project a raw report onto the public schema and attach provenance.

    ``source_name`` is an explicit trusted provenance argument, but it is still
    constrained to a safe JSON basename. Host, model, hardware, path, and prose
    metadata from the raw report are intentionally omitted.
    """
    if not isinstance(report, dict):
        raise TypeError("benchmark report must be a JSON object")

    unexpected = sorted(set(report) - (_TOP_LEVEL_KEYS | {"results"}))
    if unexpected:
        raise ValueError("raw report has fields outside aggregate schema: " + ", ".join(unexpected))
    if "results" in report and not isinstance(report["results"], list):
        raise ValueError("raw report results must be an array")

    metadata = _project_metadata(report.get("_metadata", {}))
    sanitized = {
        key: copy.deepcopy(value)
        for key, value in report.items()
        if key not in {"_metadata", "_provenance", "results"}
    }
    sanitized["_metadata"] = metadata
    sanitized["_provenance"] = {
        "sanitizer": f"bench/sanitize_results.py v{_SANITIZER_VERSION}",
        "source_name": Path(source_name).name,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_bytes": len(source_bytes),
        "dataset_sha256": metadata.get("dataset_sha256"),
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
