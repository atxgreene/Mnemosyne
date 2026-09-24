#!/usr/bin/env python3
"""Regression tests for public benchmark judging and result sanitization."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for path in (str(_HERE), str(_REPO)):
    if path not in sys.path:
        sys.path.insert(0, path)

from locomo import _answer_coverage_judge  # noqa: E402
from sanitize_results import (  # noqa: E402
    assert_aggregate_only,
    sanitize_report,
)


class AnswerCoverageJudgeTests(unittest.TestCase):
    def test_exact_normalized_phrase_passes(self):
        self.assertTrue(
            _answer_coverage_judge(
                "When was the launch?",
                "May 7, 2023",
                "The notes say the launch was May 7 2023.",
            )
        )

    def test_one_four_character_overlap_does_not_pass(self):
        self.assertFalse(
            _answer_coverage_judge(
                "Where does Person A live?",
                "Person A lives in Northport",
                "Person B visited a park near Southport.",
            )
        )

    def test_shared_entity_without_answer_does_not_pass(self):
        self.assertFalse(
            _answer_coverage_judge(
                "What did Person C buy in Metro City?",
                "Person C bought a blue bicycle",
                "Person C discussed Metro City and ordered coffee.",
            )
        )

    def test_empty_expected_or_actual_fails(self):
        self.assertFalse(_answer_coverage_judge("q", "", "anything"))
        self.assertFalse(_answer_coverage_judge("q", "answer", ""))

    def test_single_token_answer_uses_word_boundaries(self):
        self.assertTrue(_answer_coverage_judge("Where?", "Rome", "She moved to Rome."))
        self.assertFalse(_answer_coverage_judge("Where?", "Rome", "Chromebook"))


class SanitizerTests(unittest.TestCase):
    def test_sanitizer_drops_per_question_dataset_text_and_adds_provenance(self):
        raw = {
            "answer_coverage": {"rate": 0.5, "passed": 1, "total": 2},
            "evidence_recall": {"mean": 0.25, "n": 2},
            "results": [
                {
                    "question": "private dataset question",
                    "expected": "private expected answer",
                    "actual_preview": "private generated answer",
                }
            ],
            "_metadata": {
                "runner": "bench/locomo.py v0.9.8",
                "dataset_sha256": "a" * 64,
                "argv": ["--dataset", "/private/path/locomo10.json"],
            },
        }
        raw_bytes = json.dumps(raw, sort_keys=True).encode()
        sanitized = sanitize_report(raw, source_bytes=raw_bytes, source_name="raw.json")
        encoded = json.dumps(sanitized)
        self.assertNotIn("private dataset question", encoded)
        self.assertNotIn("private expected answer", encoded)
        self.assertNotIn("/private/path", encoded)
        self.assertNotIn("results", sanitized)
        provenance = sanitized["_provenance"]
        self.assertEqual(provenance["source_name"], "raw.json")
        self.assertEqual(len(provenance["source_sha256"]), 64)
        self.assertTrue(provenance["per_question_records_omitted"])
        assert_aggregate_only(sanitized)

    def test_validator_requires_provenance(self):
        with self.assertRaises(ValueError):
            assert_aggregate_only({"answer_coverage": {"rate": 1.0}})

    def test_sanitizer_rejects_alternate_private_text_key(self):
        raw = {
            "answer_coverage": {"rate": 1.0, "passed": 1, "total": 1},
            "prompt_body": "private dataset question under an alternate key",
            "_metadata": {"dataset_sha256": "c" * 64},
        }
        with self.assertRaisesRegex(ValueError, "outside aggregate schema"):
            sanitize_report(raw, source_bytes=b"{}", source_name="raw.json")

    def test_validator_rejects_unexpected_nested_array_or_object(self):
        raw = {
            "answer_coverage": {"rate": 1.0, "passed": 1, "total": 1},
            "_metadata": {"dataset_sha256": "d" * 64},
        }
        sanitized = sanitize_report(raw, source_bytes=b"{}", source_name="raw.json")
        for mutation in (
            {"dialogue_records": [{"utterance": "private text"}]},
            {"private_payload": {"body": "private text"}},
        ):
            candidate = json.loads(json.dumps(sanitized))
            candidate["_metadata"].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                assert_aggregate_only(candidate)

    def test_sanitizer_cli_shape_round_trip(self):
        raw = {
            "answer_coverage": {"rate": 1.0, "passed": 1, "total": 1},
            "results": [{"question": "q", "expected": "a"}],
            "_metadata": {"dataset_sha256": "b" * 64},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(raw))
            value = sanitize_report(
                json.loads(path.read_text()),
                source_bytes=path.read_bytes(),
                source_name=path.name,
            )
            assert_aggregate_only(value)
            self.assertEqual(value["answer_coverage"]["rate"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
