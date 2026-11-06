# Temporal Knowledge System

A dependency-free temporal retrieval system for the interface.ai Applied AI Engineer build assignment. It answers member questions from dated credit-union website snapshots, cites the supporting page, preserves historical context for `as_of` questions, and abstains when the supplied snapshots do not support an answer.

The central rule is simple: **newer is not automatically truer**. The system keeps complete snapshots, selects the correct point in time first, and then prefers authoritative current product pages over blogs, news, PDFs, and expired offers.

## Public-evaluation result

| Metric | Result |
| --- | ---: |
| Public-eval score | **0.9643** |
| Questions | 40 |
| Errors | **0** |
| Stale-answer traps | **0** |
| Repeat consistency | 1.00 |
| Paraphrase consistency | 1.00 |
| Ingest time | 1.4 s |
| P95 answer latency | 0.01 s |

These results are from a local run of the supplied public evaluator against the five supplied snapshots. The score is a public-eval result, not a claim about the hidden evaluation set.

## Design

```text
dated website snapshots
        │
        ▼
ingest: preserve pages, snapshot IDs, dates, token statistics, and table rows
        │
        ▼
select one complete state: latest, or the latest snapshot on/before `as_of`
        │
        ├── focused product responders ──► qualified answer + source
        └── source-aware lexical retrieval ──► concise passage + source / abstention
```

### Snapshot-first temporal model

`ingest()` stores each crawl as a distinct, date-ordered state. A question with no `as_of` value uses the newest snapshot. For an `as_of` value, the system selects the latest complete snapshot whose date is on or before that date. It does not blend pages from different crawls, preventing a new page from being answered with an old rate table.

### Qualified financial facts

Financial tables are retained as rows rather than flattened into isolated values. This keeps a rate associated with the relevant term, balance range, loan amount, model year, and rate/APY column. The focused responders use those row-level qualifiers for common certificate, savings, checking, auto-loan, personal-loan, and mortgage questions.

### Authority and evidence

Answers carry the source URL, snapshot ID, and—when available—a short quote. Primary product, business, contact, and about pages receive higher retrieval authority; blogs, newsroom pages, PDFs, and expired-offer pages receive lower authority. For unsupported rate questions, the system returns a non-confident answer instead of inventing a value.

## Interface contract

The submission exposes `build()` in `submission/solution.py`. It returns an object with the assignment contract:

```python
system = build()
system.ingest(snapshot)                         # snapshots arrive oldest → newest
answer = system.answer(question, as_of=None)    # Answer(text, sources, confident, ...)
conflicts = system.conflicts()                   # list[Conflict]
```

The implementation uses only Python's standard library and requires Python 3.9 or later. No API keys, model calls, or third-party packages are required.

## Conflict handling

The submitted implementation treats a changed rate as a conflict only when the same labeled rate-table row appears with different values across dated, primary product/rate pages. It also considers labeled rate prose. When there is a high-confidence historical conflict, the later primary-page value is selected and both sources are retained in the `Conflict` object.

The system intentionally does **not** treat all differences as conflicts:

- Different balance, term, model-year, or loan-amount tiers are separate facts.
- Rate versus APY is not a contradiction.
- Blogs, news, promotional pages, and expired offers are historical evidence, not equal-authority current rate pages.

### Conflict-audit note

The project review inventory records **45 candidate contradictions: 37 across historical snapshots and 8 within a single snapshot**. This is useful context for reviewing the dataset. The version of `submission/solution.py` in this repository deliberately reports a narrower, high-confidence cross-snapshot rate-change set: it deduplicates output, caps the returned list at 30, and **does not currently emit the eight same-snapshot findings**. The counts above should therefore not be read as the runtime output of `conflicts()`; same-snapshot comparison is a clearly bounded next extension.

## Local setup and evaluation

Clone this repository alongside the participant kit, or copy the `submission/` directory into the kit root. The supplied snapshots must be available locally but must not be committed.

```bash
python3 --version                 # Python 3.9+
python3 -m venv .venv
source .venv/bin/activate

# No installation is needed: requirements.txt declares no third-party packages.
python3 harness/run_eval.py \
  --submission submission \
  --eval eval/public_eval.jsonl \
  --out results/public
```

On a fresh participant kit, the evaluator automatically expands the supplied snapshot ZIPs into `snapshots/`. Re-running the command is deterministic for a fixed snapshot set and evaluator.

To run the harness's small self-test (when included in the kit):

```bash
python3 harness/run_eval.py \
  --submission submission \
  --snapshots harness/selftest/snapshots \
  --eval harness/selftest/eval.jsonl \
  --conflicts-gold harness/selftest/conflicts_gold.json \
  --out results/selftest
```