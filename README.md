# ClearDraft

[![test](https://github.com/TayEeZhan/cleardraft/actions/workflows/test.yml/badge.svg)](https://github.com/TayEeZhan/cleardraft/actions/workflows/test.yml)

**Shipping document verification for a shared operations inbox.**
Averis x Monash Hackathon 2026.

**Live demo: [cleardraft-one.vercel.app](https://cleardraft-one.vercel.app)**

**Written responses** (problem fit, AI and cloud, user feedback, coding
challenges, success metrics, scalability): [jump to the section](#written-responses).

### Try it in 30 seconds

1. Open the live demo and press **See a real check**: a draft BL with two
   wrong fields, the source line behind every value, and the reply it drafted.
2. Go to **Check a pair** and press **No files? Use a sample pair** →
   **Sample with discrepancies**. That pair is read live on the server.
3. Open **Inbox** → **Try 6 sample emails**. Your inbox starts empty; six
   real `.eml` files go through the live pipeline one by one. Add your own
   mail the same way: **Paste an email** (text plus the SI/BL files) or drop
   `.eml` files (Gmail: ⋮ → Download message). No Gmail connection needed.
   Signed out, results stay in your browser; **Sign in** to keep them in an
   account on any device. **Sample company inbox** shows the full 520-email run.
4. Open **Accuracy** to see the results on the full inbox and on mail the
   system has never seen.
5. Open any case and use the human review buttons — **Looks right — next
   case**, **Something's wrong** (with a note), or skip — to record whether
   you agree with ClearDraft's answer. Then go to **Export** for
   **Discrepancy report (CSV)**, **Full results (CSV)**, or **Submission file
   (JSON)**.

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

```
email ──> 1 classify ──> 2 extract ──> 3 compare ──> 4 decide ──> 5 reply
          rules first     4 format       normalise    escalation   template
          model fallback  readers +      + exact      ladder       fill only,
          (evidence-      alias table,   equality,                 never sends
           gated)         model for      no AI
                          missing fields
                          (verbatim gate)
```

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
label that puts Chinese characters before `KGS` in a Word attachment. Alignment is by meaning,
via the table in [`core/aliases.py`](core/aliases.py), not by header text.

---

## Who does what

| ClearDraft | The person |
|---|---|
| Sorts every email into what it's asking for | Confirms or flags each result with **Looks right — next case** / **Something's wrong** (note) |
| Reads both documents and locates all seven fields | Decides every case ClearDraft escalates instead of guessing |
| Compares the two documents and shows the source line behind every value | Edits the drafted reply |
| Drafts the reply, with a "Your part" line saying what to check or do | Sends the reply — **Open in Gmail** only prefills a compose window; ClearDraft never sends mail |

Nothing is sent automatically. The person confirms, decides, edits, and sends.

## Mark as same

A signed-in clerk can teach ClearDraft that one specific SI/BL wording is the
same, on any of the five text fields (`shipper`, `consignee`, `notify_party`,
`port_of_loading`, `port_of_discharge` — never the two numeric fields, where
a difference is always real). Click **Mark as same** on a mismatch row —
either an inbox discrepancy row or a "Check a pair" result — confirm inline,
and optionally add a short reason. From then on:

- that exact pair, for that one field, clears itself on every future check
  (order doesn't matter — "A same as B" also covers "B same as A");
- it never generalises into a rule — only that one verified wording pair is
  affected, never a pattern or a fuzzy match;
- it's per account — one clerk's pair never changes what a colleague sees;
- it's bounded and reversible: up to 500 pairs per account, undoable in one
  click, from the row it cleared or from the pairs page;
- a result already saved before the pair existed re-counts live, without
  re-running the check — the inbox board, a saved case, and "Check a pair"
  all update as soon as a pair is added, edited or undone;
- the **organiser submission export never changes** — it always reflects the
  originally checked result, because it exists to score the pipeline itself.

Find every marked pair — search, filter by field, edit or undo — on the
**Marked as same** page, linked from the top bar (with a count badge) and the
account menu. Full design and trade-offs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
ADR-011.

## Export

- **Discrepancy report (CSV)** — one row per mismatch: field, SI value, BL
  value, plain-English reason.
- **Full results (CSV)** — one row per field per case: value, source line and
  plain-English reason, plus the human review verdict.
- **Submission file (JSON)** — the organiser's own submission format.

All three are generated client-side, in the browser, from data already on
the page.

## Evidence

| What was tested | Result | Where to see it |
|---|---|---|
| Automated test suite | 345 tests (344 pass, 1 skipped), no network, no API key, no model calls | `tests/`, `python -m pytest tests/ -q` |
| Organiser corpus (520 emails, 250 attachments, 119 SI/BL pairs) | End-to-end score 1.0000, 46 of 46 planted discrepancies caught, full run in about 1.7s | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §9, **Accuracy** page |
| Held-out set, written without reference to our rules (`data/challenge/`) | Category accuracy 67% → 100%, intent accuracy 50% → 100%, fields read under unfamiliar labels 2 of 41 → 41 of 41 (18 of 41 rules-only after later alias work); model decided 15 emails, 0 wrong, across 21 calls | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §9, `data/challenge/README.md` |
| Every flow, checked live on the deployed site | Manual pass across home, inbox, check, accuracy, accounts | https://cleardraft-one.vercel.app |
| Phone layouts, 320–414 px | No sideways scroll, 44 px tap targets | live site at that width |
| Attack test — HTML/script in an email subject and body | Rendered as plain text, not executed | live site, "Your mail" upload |
| Session cookie | `HttpOnly` and `Secure` | [docs/API.md](docs/API.md) "Sessions" |
| Passwords | Never stored in plain text (scrypt-hashed) | [docs/API.md](docs/API.md) "Password hashing" |
| Manual comparison time, per a Workshop 2 domain expert | Up to 10 minutes per SI/BL pair by hand. **Estimate**, not measured on this repo: the sample inbox has 119 pairs to compare, so up to about 20 staff-hours of comparison versus about 1.7 seconds of machine time — the person still reviews every flagged case and sends every reply | this table |

## Known limits

- **Partial matches beyond asterisk markers.** Real documents also vary by
  spacing and other continuation conventions; only `*`/`**` continuation
  markers in party names are normalised away today.
- **"To the order of" is treated as the consignee value**, with an on-screen
  note — it does not change the match/mismatch verdict. A negotiable Bill of
  Lading made out "to the order of" is legally different from one naming a
  consignee directly; the note flags this for the person to judge.
- **The Accuracy page's held-out rules-only field figure (2 of 41) predates
  later alias work.** Rules alone now read 18 of 41 under unfamiliar labels;
  see the Evidence table above.
- **No direct Gmail connection, by design.** Gmail read access needs
  Google's restricted-scope security review. Add mail instead by pasting the
  email text (plus SI/BL files) or dropping `.eml` files.

---

## Written responses

Answers to the six questions in the submission requirements.

### 1. Problem-solution alignment

**The problem.** A shipping desk works from one shared inbox. For every
document-check request, a person opens the Shipping Instruction (what the
customer asked for) and the draft Bill of Lading (what the carrier typed) and
compares seven fields by hand. The organiser's domain expert told us at
Workshop 2 that one comparison takes **up to 10 minutes**. A miss means a wrong
Bill of Lading is issued, followed by amendment fees and delayed cargo. The same
inbox also holds SI requests, invoice queries and spam that must be sorted first.

**How each part of ClearDraft answers it.**

| Pain | What ClearDraft does |
|---|---|
| Mixed inbox | Sorts every email by category **and** intent ("check these" is different from "please send the draft") |
| Four file formats, different labels | Four format readers plus a label table, so "Load Port" and "Port of Loading" are the same field |
| Slow, error-prone comparison | Compares all 7 fields exactly, with no AI in the comparison, in milliseconds |
| "Can I trust it?" | Shows the source line behind every value it read |
| A wrong "all clear" is the expensive error | Escalates to a person when it cannot prove a result, instead of guessing |
| Writing the reply | Drafts it from the checked values; the person edits it and sends it from Gmail |
| The corrected draft comes back | Re-checks it: what is fixed, what is still wrong, what the amendment broke |
| Reporting | Exports a discrepancy report (CSV) and a submission file (JSON) |

What it deliberately does **not** do: send mail on its own, or let a model
decide that two values differ.

### 2. AI and cloud infrastructure integration

**AI, used only where rules cannot reach.** The model is `claude-haiku-4-5`,
called through one adapter (`adapters/model.py`) in exactly two places:
reading an email's intent when the rules decline, and finding a field the
format readers could not locate. **Every value the model returns must appear
word for word in the source document, or it is discarded and the case goes to a
person** (the verification gate). The comparison itself never uses AI
(ADR-001 in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). Every result records
`decided_by` and a confidence value, and the case page shows both.

*Why a small model:* it sees very little work. On the held-out set it made 21
calls in total (7,772 tokens), decided 15 emails and got none wrong. On the
organiser's inbox it was needed 0 times. A larger model would cost more for
no measured gain. `CLEARDRAFT_USE_MODEL=0` switches it off instantly.

**Cloud.**

| Piece | Service |
|---|---|
| Website | Vercel, static files from `web/` |
| API | One Vercel Python function (FastAPI, `api/index.py`): `/api/health`, `/api/check`, `/api/process-email`, `/api/auth/*`, `/api/mail*`, `/api/equivalences*` |
| Accounts and saved mail | Upstash Redis through Vercel Storage; scrypt password hashes; HttpOnly, Secure session cookie |
| Model | Anthropic API; key held in a Vercel environment variable |
| Deploy | Every push to `main` deploys automatically |

It fails safe: with no account store, sign-in is hidden and everything else works.
If the store goes down, uploads carry on as signed-out. With the model off, the
rules run alone and anything unresolved is escalated.

**Getting mail in.** A user pastes an email (with its SI and BL files) or drops
`.eml` files. Both go through the same REST endpoint. We chose not to connect
Gmail directly: reading Gmail needs Google's restricted-scope security review,
which takes weeks. The inbox is a port (ADR-007), so a mailbox connector plugs in
later without changing the pipeline.

### 3. User feedback / testing

**Domain-expert feedback, and what we changed because of it (Workshop 2):**

| The expert said | What we built |
|---|---|
| Teams need to export results: each mismatch, the SI value, the BL value and why | Discrepancy report (CSV), Full results (CSV), Submission file (JSON), per-case CSV |
| Show which steps a person validates and which they act on | "Your part" line on every case; "Looks right" / "Something's wrong" review buttons |
| Real documents show partial matches, such as `*` continuation markers | Party names now ignore `*` / `**` markers |
| "To the order of" is legally different from a named consignee | An on-screen note on those cases (it never changes the verdict) |
| Managers watch accuracy and missing fields | Every export row states match, mismatch or missing for all 7 fields |

**A feedback loop inside the product.** People mark each case "Looks right" or
"Something's wrong" with a note. The Accuracy page reports how often reviewers
agreed with ClearDraft, and flagged notes show which labels or rules need work.

**Testing.**
- 345 automated tests (344 pass, 1 skipped). They run with no network, no key and no model calls.
- The organiser's 520-email inbox: end-to-end score 1.0000, with 46 of 46 planted discrepancies caught.
- A held-out set written without reference to our rules, to measure generalisation (see Success metrics).
- Scripted end-to-end passes on the live site covering:
  - every flow;
  - phone widths from 320 to 414 px (no sideways scroll, 44 px tap targets);
  - an attack email with HTML and script in it (shown as plain text);
  - cookie and password handling.

### 4. Coding challenges

1. **One field, four formats, many labels.** The same company comes as
   `KTP CO., LTD | address` in Excel, `KTP CO., LTD` followed by the address on
   a new line in Word, and the address on a continuation line in text files.
   We compare the name only (cut at the first separator), and a label table maps
   variants, including bilingual labels, to one field.
2. **Trusting a model's reading.** A language model can "read" a value that is
   not in the document. Fix: the verification gate. A model value is used only
   if it appears verbatim in the source; otherwise the case is escalated.
3. **Exact comparison without false alarms.** `131,058 KG` and `131058` must
   match, and `NANTONG (CNNTG)` must match `NANTONG`, but a different company or
   one extra container must not. We use per-field normalisers: decimal-safe
   weights, stripping only a trailing port code (LOCODE), and entity punctuation
   rules. Each has its own tests.
4. **Untrusted email in a web page.** Uploaded mail can carry hostile content.
   Fixes:
   - everything is rendered as text, never as HTML;
   - CSV cells that start with `=`, `+`, `-` or `@` are neutralised;
   - attachment names are sanitised before they touch the disk.
5. **Serverless limits.**
   - Vercel caps a request body at 4.5 MB, so the site sends one email per request.
   - Every Python file in `api/` becomes its own function, so private modules
     start with `_`.
   - A reply box measured while hidden grew 5,671 px tall. It now measures only
     when visible and re-measures when its width changes.

### 5. Success metrics

| Metric | Why it matters | Result now |
|---|---|---|
| Discrepancies caught end to end (organiser inbox) | A missed discrepancy means a wrong BL is issued | 46 of 46; score 1.0000 (validation on the one labelled corpus) |
| Generalisation to unseen mail (held-out set) | Real inboxes do not match the sample | Email category 67% (rules alone) → 100% (with the model); intent 50% → 100%; fields under unfamiliar labels 2 of 41 → 41 of 41 |
| Wrong answers from the model | Trust | 0 of 15 model decisions wrong; 0 answers rejected by the gate |
| Share decided without AI | Cost, speed, predictability | 100% on the organiser inbox |
| Speed | Replaces up to 10 minutes of manual comparison per pair | 520 emails in about 1.7 s; one live check in about 0.02 s, or about 3.5 s when the model is consulted |
| Reviewer agreement | The operational metric once people use it | Measured live on the Accuracy page ("Looks right" vs "Something's wrong") |

**Estimate:** the sample inbox has 119 document pairs. At up to 10 minutes each,
that is up to about 20 staff-hours of comparison. People still review flagged
cases and send every reply.

### 6. Scalability plans

**Today's load.** The documentation team is 5 to 10 people (Workshop 2). One
process handles the whole 520-email inbox in under 2 seconds. Each email is
processed on its own (ADR-006), so nothing is shared between requests.

**How it grows, cheapest first** (detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) section 7):
1. **More rules, fewer model calls.** Each new label alias removes model calls
   permanently, at no infrastructure cost.
2. **Layout Memory.** Fingerprint a document's label layout and reuse its field
   map. Carriers reuse a few templates, so repeats skip the model entirely.
3. **More workers.** Emails are independent, so a queue and stateless workers
   scale out without touching `core/`. Remaining model calls can run in
   parallel, with back-off on rate limits.
4. **Mail arriving by itself.** Add a forwarding address or mailbox webhook
   (for example Microsoft Graph or an inbound-email service) that feeds the same
   `/api/process-email` path, through the inbox port.
5. **Storage for teams.** Today a user's saved mail is one Redis record, capped
   at 200 emails. At scale this moves to:
   - Postgres with one row per email;
   - an object store for attachments;
   - per-company isolation;
   - an audit trail of who confirmed, flagged or sent each case.

**Known ceilings** (not built yet, stated plainly): the model rate limit under
bursts, large scanned PDFs held in memory, and cross-email features that need a
shipment store. The fix for each is in the architecture document.

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

Expected output:

```
wrote 520 records to out/submission.json in 2.5s
no stubs - every stage is implemented
```

## See the website locally

```bash
python -m http.server 5190 --directory web            # the UI, reading web/public/data.json
python -m uvicorn api.index:app --port 8011           # the API behind "Check a pair"
python scripts/export_ui_data.py                      # refresh the UI snapshot after a code change
```

The inbox and accuracy screens show a saved run of the full 520-email
pipeline (`web/public/data.json`). The **Check a pair** screen is live: it
sends the two files to `POST /api/check` and runs the same pipeline on them.

## Test it

```bash
python -m pytest tests/ -q
```

345 tests (344 pass, 1 skipped), with no network, no API key and no model
calls: the suite switches the model off so it is free and repeatable.
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
| `core/recheck.py` | Amended-draft re-check: fixed, still wrong, newly broken | Ee Zhan |
| `core/pipeline.py` | The orchestrator, taking each stage as an injected callable | Ee Zhan |
| `core/equivalence.py` | "Mark as same" — pure rule for learned pairs (ADR-011) | Sheng Kuan |
| `adapters/` | Everything that touches the outside world (model, inbox) | Ee Zhan |
| `api/index.py` | FastAPI on Vercel: `/api/health`, `/api/check` | Ee Zhan |
| `api/_equivalences.py` | "Mark as same" routes and live re-evaluation | Sheng Kuan |
| `web/` | The website: plain HTML, CSS and JavaScript, no build step | Ee Zhan |
| `scripts/` | Batch run, UI snapshot export, held-out challenge run | — |
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
| [docs/PARSER_REVIEW.md](docs/PARSER_REVIEW.md) | Where the four document readers break on a real document: four findings fixed, two open, and the dependency pins the deployment needs |
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
| **Tay Ee Zhan** ([@TayEeZhan](https://github.com/TayEeZhan)) | Architecture, comparison engine, API, interface, deployment |
| **Goh Sheng Kuan** ([@shengkuan06](https://github.com/shengkuan06)) | Document extraction, format adapters, label alignment |
| **Ong Zi Qi** ([@chessoreo](https://github.com/chessoreo)) | Email classification, evaluation harness |
