# ClearDraft

**Shipping document verification for a shared operations inbox.**
Averis x Monash Hackathon 2026.

A shipping desk receives hundreds of emails a day in one mailbox: requests to
check documents, requests for new shipping instructions, invoice queries,
operational updates, and spam. For a document-check request, a clerk opens two
attachments — the Shipping Instruction (SI, what the customer asked for) and
the draft Bill of Lading (BL, what the carrier typed) — and compares seven
fields by hand. It is slow, repetitive, and a missed discrepancy means a Bill
of Lading is issued wrong, which costs money and delays cargo.

ClearDraft reads the inbox, works out what each email is asking for, compares
the two documents when a comparison is what was asked, shows the exact source
line behind every value it read, and escalates to a person when it cannot
decide instead of guessing.

---

## The design, in one sentence

**A deterministic pipeline that calls a language model only where determinism
cannot reach, and verifies every model output against the source document
before accepting it.**

The comparison itself uses no AI. Values are normalised and compared exactly.
The model is used for two things only — resolving an email's intent when rules
decline, and reading a field the format parsers could not locate — and
anything it returns must appear verbatim in the source document or the value
is discarded and the email is escalated.

Every record reports `decided_by`, so the proportion of decisions made without
the model is measurable rather than asserted.

Full reasoning, including seven architecture decision records with their
trade-offs: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

---

## The seven compared fields

`shipper` · `consignee` · `notify_party` · `port_of_loading` ·
`port_of_discharge` · `container_count` · `gross_weight_kg`

The two documents routinely label the same field differently — `Port of
Loading` in one and `Load Port` in the other, or a bilingual
`Gross Weight (<chinese> KGS)` in a Word attachment. Alignment is by meaning,
via the table in [`core/aliases.py`](core/aliases.py), not by header text.

---

## Setup

Requires Python 3.10 or newer.

```bash
git clone https://github.com/TayEeZhan/cleardraft.git
cd cleardraft

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

The model fallback needs an Anthropic API key:

```bash
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY
```

**The key is optional.** With no key set, the pipeline runs on its rule tier
alone and escalates anything the rules cannot resolve. That is deliberate: it
is how we demonstrate that the deterministic path stands on its own.

---

## Run it

Process the whole inbox and write a submission file:

```bash
python scripts/run_pipeline.py --data data --out out/submission.json
```

Or against the organiser's docker server instead of a local folder
(`--data` and `--server` are mutually exclusive):

```bash
python scripts/run_pipeline.py --server http://localhost:8080 --out out/submission.json
```

Output confirms every stage ran (during the build it listed any stage still
stubbed, so progress was visible):

```
wrote 520 records to out/submission.json in 2.5s
no stubs - every stage is implemented
```

## Test it

```bash
python -m pytest tests/ -q
```

`tests/test_contract.py` proves the plumbing rather than the accuracy: that all
520 emails parse, that the submission record shape matches the organiser's
sample exactly, that no label aliases to two different fields, and that
bilingual labels resolve to their English field.

## Evaluate it

```bash
python -m eval.score out/submission.json
```

Prints classification accuracy and macro-F1, defect precision and recall,
end-to-end catch rate, escalation precision and recall, and the share of
decisions made by rule.

**Ground-truth labels are deliberately excluded from this repository.**
`eval/score.py` reads them from a local gitignored path under `.secrets/`, so
the score is measurable without labels ever entering version control. On a
clean clone the command prints an explanatory message instead of failing. See
section 8 of [PLAN.md](PLAN.md).

On the organiser's 520 emails the end-to-end score is **1.0000 (46 of 46
planted defects caught)**. That is a validation number on the one corpus we
hold labels for, not evidence of generalisation — see the held-out result
below for that.

---

## Beyond the batch run

- **Live checker** — `POST /api/check` and the UI's `#/check` page let you
  upload one SI and one BL directly and see the same 7-row verdict, without
  running the whole inbox. See [docs/API.md](docs/API.md).
- **Re-check** — `core/recheck.py` compares an amended draft BL against the
  same SI and reports what got fixed, what is still wrong, and what the
  amendment broke. Demonstrated with a hand-authored amended draft in
  `data/demo/`, since the organiser's inbox holds only first drafts.
- **Held-out challenge set** — `data/challenge/` is a small set written
  without reference to our rules, to measure how the model fallback
  generalises rather than how well it fits the sample inbox. Run with
  `python scripts/run_challenge.py`. On it: category accuracy 67% -> 100%
  and intent accuracy 50% -> 100% with the model as fallback; fields read
  under unfamiliar labels went from 2 of 41 to 41 of 41 (18 of 41 with rules
  alone after later alias work); the model decided 15 emails and got none
  wrong, across 21 calls.

---

## Repository layout

| Path | Contents | Owner |
|---|---|---|
| `core/types.py` | The frozen contract every stage shares | Ee Zhan |
| `core/classify.py` | Stage 1 — what is this email asking for | Zi Qi |
| `core/aliases.py`, `core/parsers/` | Label table and one adapter per file format | Sheng Kuan |
| `core/extract.py` | Stage 2 — read a document into located field values | Sheng Kuan |
| `core/normalise.py`, `core/compare.py` | Stage 3 — reduce and compare. No AI | Ee Zhan |
| `core/decide.py` | Stage 4 — the escalation precedence ladder | Ee Zhan |
| `core/reply.py` | Template-filled draft reply. Never free model text | Ee Zhan |
| `core/pipeline.py` | The orchestrator, taking each stage as an injected callable | Ee Zhan |
| `adapters/` | Everything that touches the outside world | Ee Zhan |
| `eval/` | The scoring harness | Zi Qi |
| `data/` | The organiser's inbox and attachments | — |

`core/` never imports `adapters/`. The dependency arrow points one way, into
the core, which is why the test suite runs with no network, no API key and no
dataset.

---

## Documentation

| Document | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, seven ADRs with trade-offs, failure modes, scaling limits |
| [docs/API.md](docs/API.md) | The API contract: what is built (`/api/health`, `/api/check`) and what is still planned |
| [docs/PROJECT_DESCRIPTION.md](docs/PROJECT_DESCRIPTION.md) | The hackathon submission summary: name, problem, approach, results |
| [PLAN.md](PLAN.md) | What we learned from the dataset, the build plan, team split |
| [docs/RUBRIC_MAP.md](docs/RUBRIC_MAP.md) | One distinct piece of evidence per judging criterion |
| [tasks/todo.md](tasks/todo.md) | Live task board |

---

## Data

The inbox in `data/` was supplied by the organisers: 520 email records and 250
attachments across `.txt`, `.pdf`, `.xlsx` and `.docx`. Redistribution in a
public repository was confirmed permitted by the organisers during the opening
ceremony question-and-answer session.

---

## Team

| | Role |
|---|---|
| **Ee Zhan** ([@TayEeZhan](https://github.com/TayEeZhan)) | Architecture, comparison engine, API, interface, deployment |
| **Sheng Kuan** ([@shengkuan06](https://github.com/shengkuan06)) | Document extraction, format adapters, label alignment |
| **Zi Qi** ([@chessoreo](https://github.com/chessoreo)) | Email classification, evaluation harness |
