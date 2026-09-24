# LOCOMO and LongMemEval benchmarks

Mnemosyne ships reproducible retrieval runners. The public numbers in this
repository are **retrieval measurements**, not end-to-end answer accuracy.

## What is measured

| Metric | Requires an answer model? | Requires a judge? | Meaning |
|---|---:|---:|---|
| evidence recall@k | no | no | Fraction of gold dialogue IDs present in the retrieved rows. |
| answer coverage | no in retrieval mode | deterministic lexical rule | Whether enough expected-answer words occur in retrieved text. This is a diagnostic, **not accuracy**. |
| answer accuracy | yes | normally yes | Whether a generated answer is correct. No public v0.9.8 result claims this metric. |
| adversarial abstention | yes | rule or judge | Whether a model declines an unanswerable question. Retrieval-only runs cannot be scored here. |

The judge-free retrieval metric is the primary public result. The hardened
lexical coverage judge accepts a normalized full phrase, an exact one-token
answer, or at least two content tokens covering 60% of a multi-token expected
answer. A single shared four-character token is not enough.

## Published retrieval result (2026-06-11)

The checked-in aggregate artifact records a historical run over the canonical
10-conversation LOCOMO dataset:

| retrieval mode | evidence recall@8 | context tokens/probe | search p50 |
|---|---:|---:|---:|
| **Mnemosyne FTS top-8** | **0.5009** | 319 | 2.76 ms |
| recency-8 | 0.0054 | 299 | ~0 ms |
| random-8 | 0.0198 | 302 | ~0 ms |
| full conversation | 0.9961 | 22,576 | 0.13 ms |

These are judge-free retrieval measurements over rows with gold evidence IDs
(n=1,536). The artifact also preserves the old lexical-match aggregates under
`legacy_answer_coverage`; those values came from the retired any-one-token
judge, are not answer accuracy, and are not comparable to the current judge.

Artifact:
[`benchmark-results/2026-06-11-locomo-retrieval-track.json`](./benchmark-results/2026-06-11-locomo-retrieval-track.json)

The artifact is aggregate-only. It includes source and dataset SHA-256
provenance but no dataset questions, expected answers, generated responses, or
per-question rows.

Git provenance uses distinct fields rather than treating the checkout at run
time as if it already contained the later artifact:

- execution source parent: `b73d1c8e4e3a3d77d0d2985027298fc838b5c5c5`;
- first commit containing this measured artifact: `c68949401983dc0f888f3a05019a9acd4eab5447`;
- first repository commit containing `bench/locomo.py` in the imported history:
  `e6a1db7f76bc8ff8e5e90e28089bc27163cbf460`;
- historical reproduction checkout: `c68949401983dc0f888f3a05019a9acd4eab5447`,
  which contains both the artifact and its measured runner revision.

## Reproduce with the current judge

Mnemosyne does not redistribute LOCOMO. Fetch it from the upstream repository:

```sh
mkdir -p bench/data bench/results
curl -L https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o bench/data/locomo10.json
# expected dataset SHA-256:
# 79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4

python3 bench/locomo.py \
  --substrate mnemosyne \
  --retrieval-mode fts \
  --judge answer_coverage \
  --out bench/results/locomo-fts-raw.json

python3 bench/sanitize_results.py \
  bench/results/locomo-fts-raw.json \
  bench/results/locomo-fts-aggregate.json
```

Keep the raw report in `bench/results/`, which is gitignored. Only a reviewed,
sanitized aggregate belongs under `docs/benchmark-results/`.

Run same-protocol baselines by changing `--retrieval-mode` to `recency`,
`random`, or `full`. Sweep retrieval depth with
`bench/efficiency_frontier.py`. New frontier output labels the metric
`answer_coverage` and `coverage_per_1k_tokens`; it never labels those values as
accuracy.

## Output contract

Current `bench/locomo.py` reports:

- `answer_coverage` (`rate`, `passed`, `total`), with
  `is_answer_accuracy: false`;
- `evidence_recall.mean`, all-found rate, and category aggregates;
- search and optional LLM latency;
- estimated context/ingest tokens;
- ingest throughput and cost metadata;
- per-question records in the **raw local report only**.

`bench/sanitize_results.py` omits the schema-defined raw `results` array and
local path arguments, records the source file SHA-256, and validates every
remaining field against a closed aggregate schema. Unexpected keys, arrays, or
objects fail closed instead of being copied into a public artifact.

## LLM-grounded answer evaluation

To evaluate generated answers, supply a model:

```sh
python3 bench/locomo.py \
  --substrate mnemosyne \
  --llm-grounded \
  --provider lmstudio \
  --model <model-id> \
  --judge answer_coverage \
  --out bench/results/locomo-grounded-raw.json
```

For the paid OpenAI judge path, install `bench/requirements.txt`, set
`OPENAI_API_KEY`, and pass `--judge openai --judge-model gpt-4o-mini`.
Generated-answer results must state the answer model, judge model, dataset hash,
runner commit, and sampling settings. They are not directly comparable to the
retrieval-only artifact.

LOCOMO category 5 is unanswerable by construction. It is excluded from
retrieval-only answer coverage because there is no model to abstain. In
LLM-grounded mode it is reported separately using abstention detection.

## Efficiency frontier

The historical frontier artifact is also aggregate-only:
[`benchmark-results/2026-07-01-locomo-efficiency-frontier.json`](./benchmark-results/2026-07-01-locomo-efficiency-frontier.json).
Its answer fields are explicitly prefixed `legacy_` because they used the
retired permissive judge. Token, latency, cost-model, and judge-free evidence
recall fields remain useful, but the legacy coverage values must not be
presented as accuracy.

The frontier executed from source parent
`c68949401983dc0f888f3a05019a9acd4eab5447`; its runner and artifact first
appear together in `9d23404280b49762522241c1d0705f80b55f8325`.
Reproduce the historical artifact from the latter commit, which actually
contains `bench/efficiency_frontier.py`.

## LongMemEval

`bench/longmemeval.py` reports judge-free session and turn retrieval recall. Its
schema-faithful offline self-test is:

```sh
python3 bench/longmemeval.py --selftest
```

No full LongMemEval result is claimed in this release.

## Regression checks

```sh
python3 bench/test_benchmark.py
python3 bench/longmemeval.py --selftest
python3 -c "import json; from bench.sanitize_results import assert_aggregate_only; assert_aggregate_only(json.load(open('docs/benchmark-results/2026-06-11-locomo-retrieval-track.json')))"
```

The benchmark test includes adversarial examples that previously passed from a
single four-character overlap, whole-word checks for one-token answers, and
sanitizer leakage checks.
