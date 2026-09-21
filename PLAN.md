# ClearDraft — Preliminary Round Plan

**Team:** Ee Zhan (Claude Code) · Sheng Kuan (Claude Code) · Zi Qi (Codex)
**Deadline:** Tue 22 Sep 2026
**Stack:** Python FastAPI (core) + static HTML/CSS/JS on Vercel (UI)

**Note, added after the preliminary round:** what actually shipped differs
from a few specifics in this plan, most visibly the UI framework (below) and
the API host (Vercel Python serverless, not Render — see §7). This document
is kept as the historical build plan; `docs/ARCHITECTURE.md` and
`docs/API.md` describe the system as built.

---

## 1. What we learned from the dataset

We read the organiser bundle and the generator source. These facts drive every
decision below.

### 1.1 The exact score formula

From `server/scoring.py`:

```
final = 0.30 x stage1_macro_f1      (classification)
      + 0.20 x stage3_defect_f1     (did we flag the right emails)
      + 0.50 x end_to_end_rate      (routed AND flagged AND exact field set)
```

`NEEDS_REVIEW` handling is reported as a separate reliability axis. It is
**not** in the final score. It is still worth doing well, because the problem
statement asks for it and the judges see it.

### 1.2 The dataset

| Category | Count |
|---|---|
| BL_COMPARISON | 220 |
| SI_REQUEST | 125 |
| INVOICE_QUERY | 75 |
| GENERAL | 60 |
| SPAM | 40 |
| **Total** | **520** |

| Status | Count |
|---|---|
| OK | 454 |
| MISMATCH | 46 |
| NEEDS_REVIEW | 20 |

`NEEDS_REVIEW` splits into exactly 5 of each reason: `wrong_doc_type`,
`missing_attachment`, `unreadable`, `missing_value`.

### 1.3 Half the score rides on 46 emails

`end_to_end` counts only BL_COMPARISON emails that carry a planted defect.
There are **46** of them. Each one is worth about 1.1% of the end-to-end rate,
which is about 0.54% of the final score.

All 46 have exactly 2 attachments. 26 of them have two defect fields, 20 have
one. The scorer demands an **exact set match** on `defect_fields`. One extra
field or one missing field scores zero for that email.

Defect field frequency across the 46:

```
container_count     19
port_of_discharge   13
gross_weight_kg     12
notify_party         8
consignee            7
shipper              7
port_of_loading      6
```

### 1.4 Defects are always substantive, never cosmetic

From `shipment.py:_mutate`:

- `shipper` / `consignee` / `notify_party` — swapped for a **different company**
- `port_of_loading` / `port_of_discharge` — swapped for a **different port**
- `container_count` — changed by **at least 1**, never clamped back
- `gross_weight_kg` — changed by **500, 1000 or 2000 kg**

**Consequence:** the comparison stage is deterministic. Normalise both sides,
then compare. No AI. No fuzzy threshold. A fuzzy matcher would only create
false negatives here.

### 1.5 The real difficulty is extraction, not comparison

The same field is labelled differently in each document. From `pools.LABELS`:

| Field | Labels seen |
|---|---|
| shipper | Shipper · Shipper/Exporter · Shipper (Principal or Seller) · SHIPPER |
| consignee | Consignee · Consignee (Non-Negotiable) · CONSIGNEE · **To the Order of** |
| notify_party | Notify Party · Notify · Notify Party/Intermediate Consignee · NOTIFY PARTY |
| port_of_loading | Port of Loading · Port of Loading (POL) · **Load Port** · POL · PORT OF LOADING |
| port_of_discharge | Port of Discharge · Port of Discharge (POD) · **Discharge Port** · POD · PORT OF DISCHARGE |
| container_count | No. of Containers · Total Containers · No. of Containers or Packages · Container Count |
| gross_weight_kg | Gross Weight (KG) · Gross Wt (kgs) · **Gross Weight 毛重 (KGS)** · GROSS WEIGHT |

Attachment formats, 250 files in total:

```
txt    192
pdf     28
xlsx    22
docx     8
```

Format quirks we confirmed in `render.py`:

- **txt** writes ports with the UN/LOCODE: `CALLAO, PERU (PECLL)`
- **pdf** writes ports **without** the code: `CALLAO, PERU`
- **docx** uses bilingual labels: `Port of Loading (装货港)`, `Gross Weight (毛重 KGS)`
- **xlsx** stores gross weight as a **raw number**, not a formatted string
- container count is always rendered as `N x SIZE`, for example `6 x 40'HC`

The normaliser must strip the port code, strip CJK characters from labels,
strip thousands separators and unit suffixes, and parse `N x SIZE` down to the
integer `N`. Container **size** is not one of the 7 compared fields, so it must
be discarded, not compared.

### 1.6 Two traps that will sink a naive build

**Trap A — keyword classification fails.**
GENERAL subjects include `_Reminder_Paper - Submit SI & AED`, `Pending BL
Release`, and `List of Outstanding BL`. SI_REQUEST subjects contain BL numbers.
Matching on the strings "SI" or "BL" misclassifies dozens of emails. You must
read the **intent verb**, not the nouns.

**Trap B — most comparison emails have nothing to compare.**
94 of the 220 BL_COMPARISON emails have **zero attachments**. 91 of those are
ground-truth `status: OK`, not `NEEDS_REVIEW`. The difference is the verb:

- `"Please assist to send the draft BL for X for checking"` → OK. It is a
  request to send a document. There is nothing to compare yet.
- `"Please compare the SI and draft BL ... (attachments appear to have been
  dropped)"` → NEEDS_REVIEW / `missing_attachment`.

A system that escalates every attachment-less comparison email destroys its
escalation precision: 94 predicted against 5 true.

### 1.7 The scorer watches how much we decide by rule

`score_stage1` reads an optional `decided_by` field on each record and reports
`rule_pct`. It does not affect the score. We emit it anyway. It gives us a free,
organiser-sanctioned number for the slide that answers the question about the
AI being unreliable: *"82% of decisions were made by a deterministic rule. The
model was only consulted on the remainder, and every model output was verified
against the source text before we accepted it."*

---

## 2. Architecture

The guiding principle: **deterministic by default, AI where it earns its
place, and a verification gate on every AI output.**

```
        +--------------------------------------------------+
        |  Inbox adapter                                   |
        |  local folder  |  organiser Docker API           |
        +-----------------------+--------------------------+
                                | Email
        +-----------------------v--------------------------+
  S1    |  CLASSIFY                                        |
        |  1. intent rules on subject + body  (high prec.) |
        |  2. Haiku 4.5 only on low-confidence residue     |
        |  -> category, decided_by: rule | model           |
        +-----------------------+--------------------------+
                                | BL_COMPARISON only
        +-----------------------v--------------------------+
  S2    |  EXTRACT                                         |
        |  format parser (txt/xlsx/docx/pdf)               |
        |  -> alias table label match                      |
        |  -> Haiku 4.5 fallback ONLY for missing fields   |
        |  -> VERIFY: model value must appear verbatim in  |
        |     the source text, else NEEDS_REVIEW           |
        |  every value carries {value, raw, line_no}       |
        +-----------------------+--------------------------+
                                | SI doc + BL doc
        +-----------------------v--------------------------+
  S3    |  NORMALISE + COMPARE   (no AI, ever)             |
        |  7 fields, exact match after normalisation       |
        +-----------------------+--------------------------+
                                |
        +-----------------------v--------------------------+
  S4    |  DECIDE                                          |
        |  OK | MISMATCH + defect_fields | NEEDS_REVIEW    |
        |  + review_reason                                 |
        +-----------------------+--------------------------+
                                |
        +-----------------------v--------------------------+
        |  Reply drafter (template fill, never free text)  |
        |  Static HTML/CSS/JS UI on Vercel                 |
        |  submission.json exporter                        |
        +--------------------------------------------------+
```

### 2.1 Why this answers "how do you stop the AI being unreliable"

Four defences, in order:

1. **The AI never decides a mismatch.** Comparison is arithmetic and string
   equality on normalised values.
2. **Every model-extracted value is verified against the source.** If the value
   the model returns does not appear in the document text, we reject it and
   escalate. A model cannot invent a consignee that is not on the page.
3. **The model is a fallback, not the path.** Rules run first. We report
   `rule_pct` to prove it.
4. **When it cannot decide, it escalates.** `NEEDS_REVIEW` with a reason and
   the proof lines, not a guess.

### 2.2 Escalation gates (`NEEDS_REVIEW`)

| Reason | Gate |
|---|---|
| `missing_attachment` | intent is *compare* AND attachment count < 2 |
| `wrong_doc_type` | a document fails the SI/BL header signature test |
| `unreadable` | file is empty, PDF has no text layer, or bytes do not parse |
| `missing_value` | a required field resolves to a blank token (`???`, `___`, `TBA`, `TBC`, `N/A`, empty) |

Note the `missing_attachment` gate uses the intent verb, per trap B.

---

## 3. Team split

The contract files land first. Everyone then works against stubs, in parallel,
with no collisions.

### Ee Zhan — integration owner
**Files:** `core/normalise.py`, `core/compare.py`, `core/decide.py`,
`api/`, `web/`, `scripts/run_pipeline.py`, deployment

- Write the interface contracts and repo scaffold **first** (see §5, Phase 0)
- Normaliser and comparator — this is the 50% path, it stays with one owner
- Escalation gates
- FastAPI service
- Static HTML/CSS/JS UI, no build step (Hick's Law, §4)
- Vercel deployment: static site plus Python serverless API, one project
- `submission.json` exporter
- Agent review gate (§6)
- Slide deck and 5-minute video

### Sheng Kuan — extraction (Claude Code)
**Files:** `core/parsers/*.py`, `core/aliases.py`, `core/extract.py`

- `parsers/txt.py`, `parsers/xlsx.py`, `parsers/docx.py`, `parsers/pdf.py`
- Alias table covering every label in §1.5, plus CJK stripping
- Provenance: every value returns `{value, raw, line_no, source}`
- Document signature test for `wrong_doc_type`
- Unreadable detection: empty file, no text layer, corrupt bytes
- Haiku fallback for missing fields, with the verbatim-verification gate

**Why Sheng Kuan:** he has seen how these document classes are managed in
practice. The alias table is a domain-knowledge artefact, not a coding task.
He should sanity-check it against what real SI and BL documents look like and
add aliases the generator does not use. That is what makes the system survive
the unseen final-round data.

**Definition of done:** `extract(path)` returns all 7 fields for at least 99%
of the 250 attachments, with a line number on every value.

### Zi Qi — classification and evaluation (Codex)
**Files:** `core/classify.py`, `eval/`, `tests/`

- Intent-rule classifier over subject and body — target 0.95 macro-F1 or better
- Must survive trap A: `Submit SI`, `Pending BL Release`, `Outstanding BL` are
  all GENERAL
- Haiku fallback for low-confidence cases
- Emit `decided_by: "rule" | "model"`
- Evaluation harness: a self-labelled dev slice, a confusion matrix, and a
  regression test that fails the build when macro-F1 drops
- Unit tests for the normaliser and comparator, against Ee Zhan's contract

**Why Zi Qi:** classification plus an eval harness is a closed problem with a
crisp numeric target and no cross-file dependencies. Codex is strongest on
self-contained Python with a clear test to satisfy.

### Shared rule
Nobody edits a file outside their own list. Cross-file needs go through the
contract in `core/types.py`, which only Ee Zhan changes.

---

## 4. UI design — Hick's Law applied

Hick's Law: the time to decide grows with the number and complexity of the
choices. The clerk's job is to decide *"is this draft BL correct?"* We remove
every choice that is not that one.

### 4.1 Rules we hold ourselves to

1. **One primary action per screen.** Nothing competes with it.
2. **Four tabs, never five.** Needs check · Discrepancy · Needs review ·
   Cleared. Category filters, sort controls and column pickers are cut.
3. **Fixed 7-row comparison table.** No sorting, no configuration. Mismatched
   rows pin to the top and are the only coloured rows.
4. **Progressive disclosure for proof.** The source line is behind one click,
   not on screen by default. Trust is available on demand, not forced.
5. **No settings page in the demo.**
6. **The reply is pre-written.** The clerk reads and presses one button. They
   do not compose.

### 4.2 Screens

**Screen 1 — Board.** Four tabs. Each card shows: OC reference, destination
port, customer, and a single state chip. One action: *Review*.

**Screen 2 — Review.** The 7-row table, SI value against BL value, mismatches
at the top in red. Below it, the drafted reply in a read-only box. One primary
button: *Copy reply*. One secondary: *Mark cleared*. Proof lines expand on
click.

**Screen 3 — Accuracy.** The organiser said teams may show accuracy inside the
UI. We do. Classification confusion matrix, defect recall, escalation recall,
and `rule_pct`. This is a judged differentiator and it costs one page.

---

## 5. Timeline

Today is Sun 20 Sep. Deadline Tue 22 Sep.

### Phase 0 — Sun evening, 3 hours, Ee Zhan alone, blocking
Nobody else can start until this is pushed.

- Repo scaffold, `pyproject.toml`, `.gitignore`
- `core/types.py` — the frozen contract: `Email`, `ExtractedDoc`,
  `FieldValue{value, raw, line_no, source}`, `Decision`
- Stub every module with the right signature and a `NotImplementedError`
- `scripts/run_pipeline.py` — walks the inbox, calls the stubs, writes
  `submission.json` in the exact `sample_submission.json` shape
- Confirm the stub pipeline produces a valid, scoreable submission
- Push to GitHub, invite both teammates, write `tasks/todo.md`

### Phase 1 — Mon 00:00 to 12:00, parallel
Zi Qi on classify. Sheng Kuan on extract. Ee Zhan on normalise, compare,
decide. Each pushes to their own branch. No merges into main yet.

### Phase 2 — Mon 12:00 to 16:00, integration
Merge all three. First full scoring run. Fix what breaks. Target: end-to-end
rate above 0.90.

### Phase 3 — Mon 16:00 to 22:00, product
Ee Zhan builds the UI and deploys. Sheng Kuan hardens extraction against the
pdf and docx edge cases. Zi Qi builds the accuracy page data and the reply
template.

### Phase 4 — Mon 22:00 to Tue 02:00, review
The agent review pass in §6. Fix findings by severity.

### Phase 5 — Tue morning, submission
Slide deck, 5-minute video, project description, prototype link, public repo.

---

## 6. Review and test plan — which agents do what

We use Opus 5 for planning and for the review gate. Implementation runs on
cheaper models. The in-product model is Haiku 4.5.

| Stage | Agent or skill | What it checks |
|---|---|---|
| After extraction lands | `ecc:python-reviewer` agent | Parser correctness, encoding, resource leaks |
| After every stage | **`ecc:silent-failure-hunter`** agent | Swallowed exceptions and bad fallbacks. Critical: a pipeline whose whole promise is *"it escalates instead of guessing"* must not have a bare `except: pass` anywhere |
| After the API lands | `ecc:fastapi-reviewer` agent | Async correctness, schemas, error paths |
| After the UI lands | `ecc:react-reviewer` agent | Hook correctness, render performance |
| Before the repo goes public | `ecc:security-reviewer` agent and `/security-review` | API keys and secrets. The repo is public and holds an Anthropic key path |
| On the deployed site | `ecc:e2e-runner` agent with Playwright MCP | The demo works on the live URL, not just locally |
| Before submission | `/code-review high` on Opus 5 | Full diff review |

### Skills we use

| Skill | Where |
|---|---|
| `ecc:regex-vs-llm-structured-text` | Stage 2 design. Exactly our rules-versus-model decision |
| `ecc:contract-first` | Phase 0 interface contracts |
| `ecc:eval-harness` | Zi Qi's evaluation harness |
| `ecc:cost-aware-llm-pipeline` | The `rule_pct` and cost story on the slide |
| `frontend-design` | UI direction, so it does not look templated |
| `dataviz` | The accuracy page charts |
| `ecc:frontend-a11y` | Contrast and keyboard access |
| `ecc:deployment-patterns` | Vercel: static site plus Python serverless API, one project |
| `anthropic-skills:pptx` | Slide deck |
| `hyperframes` or `product-launch-video` | The 5-minute demo video |

---

## 7. Cloud and AI requirements

Both are mandatory. The slides say a weak cloud story scores significantly
lower. Ours:

| Layer | Service | Tier |
|---|---|---|
| UI + API | Static HTML/CSS/JS and FastAPI, one **Vercel** project (Python serverless function for the API) | Free |
| Model | **Anthropic API**, Haiku 4.5 | Pay per call, tiny |
| Layout cache (final round) | **Upstash Redis** | Free |

We ship the dataset inside the deployment so judges can run the live prototype
without our machine. The organiser confirmed this is acceptable and that the
dataset may go in a public repo.

The AI component is real and defensible: intent classification fallback and
field extraction fallback, both on Haiku 4.5, both behind a verification gate.

---

## 8. Ground truth and evaluation discipline

The organiser's ground-truth labels are deliberately **not** in this
repository.

1. **`.secrets/` is the first entry in `.gitignore`**, committed before any
   other file. `git check-ignore` confirms the exclusion.
2. **`eval/score.py` reads the labels from that local path**, so the score is
   measurable without labels ever entering version control. On a clean clone it
   prints an explanatory message instead of failing.
3. **We measure against them. We do not tune to them.** Design decisions come
   from the documented field semantics and the format differences between the
   four attachment types, not from fitting to labels.
4. The final round runs on data nobody has seen. A system fitted to one label
   set would gain nothing there, so the discipline is practical as well as
   principled.

## 9. What we defer

| Item | When |
|---|---|
| Idea 4, Layout Memory | Final round. Gives the scale and cost answer |
| The re-check loop on a corrected BL | Final round. Needs demo data we author |
| Idea 5, full document-set compliance | Roadmap slide only. No data exists for it |
| Multilingual email bodies | Roadmap. The organiser said it is optional |
