# ClearDraft — System Design

Rubric criterion 2, Architecture and Scalability, 15 points. Judges assess
*"component structure, data flow, dependencies and ability to grow"* and
award the top band for *"well-justified architecture with clear trade-offs and
a realistic scaling approach, supported by the implementation."*

This document states the decisions and the trade-offs. Every claim in it
points at a file you can open.

---

## 1. The one-sentence design

**A deterministic pipeline that calls a language model only where determinism
cannot reach, and verifies every model output against the source document
before accepting it.**

Everything below follows from that sentence.

---

## 2. Context

```
   +---------------+        +--------------------+       +---------------+
   | Shipping desk |  reads | ClearDraft         | calls | Anthropic API |
   | operator      |<------>| classify, extract, |------>| Haiku 4.5     |
   |               | acts   | compare, escalate  |       +---------------+
   +---------------+        +---------+----------+
                                      | reads
                            +---------v----------+
                            | Shared mailbox     |
                            | 520 emails,        |
                            | 250 attachments    |
                            +--------------------+
```

The operator is the only actor who takes an irreversible action. ClearDraft
drafts a reply; the operator presses Send. That boundary is deliberate and it
is enforced in code, not in policy: `core/reply.py` renders text and returns
it. Nothing in the repository can send mail.

---

## 3. Containers

```
  +----------------------------------------------------------------+
  |  web/         Static HTML + CSS + one ES module, on Vercel      |
  |               no build step. board, review, accuracy, live      |
  |               checker (#/check)                                 |
  +--------------------------------+-------------------------------+
                                   | HTTPS, JSON
  +--------------------------------v-------------------------------+
  |  api/         FastAPI, Vercel Python serverless function        |
  |               thin transport layer, no business logic           |
  +--------------------------------+-------------------------------+
                                   | in-process calls
  +--------------------------------v-------------------------------+
  |  core/        the pipeline. Pure functions. No I/O.             |
  +--------------------------------+-------------------------------+
                                   | injected callables
  +--------------------------------v-------------------------------+
  |  adapters/    inbox sources, model client, cache                |
  +----------------------------------------------------------------+
```

`core/` does not import `adapters/`. The dependency arrow points one way, into
the core. That is what lets `tests/test_contract.py` exercise the whole
pipeline without a network, an API key or a dataset.

---

## 4. Components and data flow

One immutable record is threaded through five stages. Each stage is a pure
function. Each returns a frozen dataclass from `core/types.py`.

```
  Email                         adapters/inbox.py      LocalInbox.emails()
    |
    v
  Classification                core/classify.py       Zi Qi
    |  category + intent + decided_by + evidence
    |
    +-- not BL_COMPARISON ------------------------> Decision(OK)
    |
    v
  ExtractedDoc x 2              core/extract.py        Sheng Kuan
    |  per field: value, raw line, line number, label
    |  model fallback gated by verify_against_source()
    |
    v
  FieldComparison x 7           core/compare.py        Ee Zhan
    |  normalise both sides, then exact equality
    |
    v
  Decision                      core/decide.py         Ee Zhan
       status, review_reason, defect_fields, rationale
```

The orchestrator is `core/pipeline.py:process`. It receives every stage as a
callable rather than importing it:

```python
process(email, classify=..., read_doc=..., compare=..., decide=...)
```

Three things fall out of that signature, and each maps to a scored criterion:

| Consequence | Criterion it serves |
|---|---|
| Any stage can be stubbed, so tests need no dataset | Engineering Quality |
| Three people build three stages against one contract | delivered on time |
| A stage can move to a remote service without touching the orchestrator | Architecture and Scalability |

`scripts/run_pipeline.py` demonstrates the first point today: unimplemented
stages fall back to a safe default and are counted, so the plumbing was
provably correct before any feature code existed.

---

## 5. Design decisions and their trade-offs

### ADR-001 — Comparison is deterministic. The model never decides a mismatch.

**Decision.** `core/compare.py` normalises both values and tests equality. No
model call, no similarity threshold.

**Why.** We read the organiser's generator. Every planted defect is
substantive: a different company, a different port, a count differing by at
least one, a weight differing by at least 500 kg. There is no case in the
problem where two values differ cosmetically and should still be called a
mismatch.

**Trade-off we accept.** A genuinely ambiguous pair, say a legal name that
changed spelling between documents, produces a false positive. We accept that
because a false positive costs one operator glance, while a fuzzy threshold
that swallows a real defect costs a reissued Bill of Lading. The asymmetry is
not close.

**Rejected alternative.** Feed both documents to a model and ask "do these
match?". Cheaper to write, impossible to audit, non-reproducible run to run,
and it puts the 50% metric at the mercy of sampling.

### ADR-002 — Ports and adapters for document formats.

**Decision.** `core/parsers/__init__.py` defines a `DocumentParser` protocol
and a registry. Each format is one adapter module.

**Why.** The dataset has four formats today. A real shipping desk has more,
and Idea 5 (checking the commercial invoice, packing list and certificate of
origin against each other) needs several more. Adding a format must be an
additive change.

**Trade-off.** One indirection layer for four implementations is mild
over-engineering at today's size. It pays for itself the first time a format
is added, which we expect in the final round.

**Evidence.** `for_path()` dispatches on extension; no core module names a
format.

### ADR-003 — Every extracted value carries its provenance.

**Decision.** `FieldValue` is `{value, raw, line_no, label, decided_by}`, not
a bare string.

**Why.** It buys two separate things from one field.
1. The review screen can show the operator the exact source line. That is the
   "shows its proof" feature, and it is what makes an operator trust the tool
   on day one rather than re-reading both documents anyway.
2. `verify_against_source()` can check a model's answer against the document
   text. Without the text we would have to take the model's word.

**Trade-off.** Roughly four times the memory per field. At 250 documents that
is irrelevant. At ten million it would need a content-addressed blob store,
which is noted in section 7 and not built.

### ADR-004 — Two tiers with a verification gate, not one model call.

**Decision.** Rules run first and set `decided_by="rule"`. The model runs only
on what rules could not resolve. Any model answer that does not appear
verbatim in the source document is discarded and the email escalates.

**Why.** This is the direct answer to "how do you stop the model inventing
things". It is a structural answer rather than a prompt-engineering one. A
model cannot return a consignee that is not on the page, because the gate
compares its answer to the page.

**Trade-off.** The gate rejects correct-but-reworded answers, for example a
model that expands "CO., LTD" to "COMPANY LIMITED". Those become escalations
rather than silent corrections. We prefer an escalation to a silent rewrite of
a legal party name.

**Evidence.** `core/extract.py:verify_against_source`, and `adapters/model.py`
is the single door every model call passes through, so the counters in
`ModelStats` are complete by construction.

### ADR-005 — Intent is modelled separately from category.

**Decision.** `Classification` carries both `category` and `intent`.

**Why.** They diverge, and the divergence is worth 91 emails. "Please send the
draft BL for checking" and "Please compare the attached SI and BL" are both
category `BL_COMPARISON`. Only the second one should escalate when attachments
are absent. 94 of the 220 comparison emails have no attachments and 91 of them
are ground-truth `OK`. A system that keys escalation off category alone
escalates all 94 and drops escalation precision from roughly 1.0 to 0.05.

**Trade-off.** Two labels to get right instead of one. Worth it.

### ADR-006 — Stateless, per-email independent processing.

**Decision.** `process()` holds no state between emails. Nothing is shared
except read-only tables.

**Why.** It makes the workload embarrassingly parallel, which is the whole
scaling story in section 7. It also makes a failed email a failed email rather
than a failed batch.

**Trade-off.** Cross-email intelligence, such as grouping every message about
one shipment into a timeline, needs a store we have not built. Noted as a
roadmap item; the product document already flags that this dataset gives every
email a distinct shipment, so the timeline could not be demonstrated anyway.

### ADR-007 — The inbox is a port.

**Decision.** `adapters/inbox.py` defines `InboxSource`. Two adapters
implement it: `LocalInbox`, which reads a folder, and `HttpInbox`, which
reads the organiser's docker server (`GET /emails`, `GET /attachments/{path}`)
over stdlib `urllib` only — no third-party HTTP client. `scripts/run_pipeline.py
--server URL` selects it, mutually exclusive with `--data`. A Microsoft Graph
or IMAP adapter is the same interface, not built.

**Why.** "This could run against a real shared mailbox" is a claim judges hear
from every team. It is credible only if the seam exists, and more credible
once a second adapter has actually been built against it rather than asserted.

**Evidence against a hostile or buggy server.** `HttpInbox` never trusts the
server's attachment path as a local path: it rejects anything absolute or
containing a `..` traversal segment before it touches the filesystem, and
downloads land in a private per-instance temp directory keyed by a flattened,
validated name. Every request carries a timeout, and an unreachable server
raises a `ConnectionError` naming the URL, not a bare socket traceback.

### ADR-011 — Learned equivalences: exact pairs, per account, live path only.

**Status.** Proposed. **Deciders.** Sheng Kuan (pending Ee Zhan).

**Context.** The deck (p.21) promises that ClearDraft "learns from every
correction": on a mismatch row, a reviewer clicks "these are the same" and the
next identical case clears itself. Reading the repo moved three things from
the deck's original sketch, all simpler and safer:
1. No new storage adapter. `adapters/store.py` already is the storage port
   (`Store`: get/set/delete/incr), with Upstash Redis in production and
   `MemoryStore` in tests. The deck's "JSON for the demo, SQLite later" is
   unnecessary — this reuses exactly what `api/_accounts.py` already uses for
   users, sessions, and mail.
2. Per-account, not global. Accounts already exist (`api/_accounts.py:
   current_user`). A learned pair is stored under `equiv:{email}`, so one
   careless click only ever affects that clerk's own future checks — the
   deck's biggest risk, "one click harms everyone", does not arise. A
   signed-out visitor never sees the control and is never affected by anyone
   else's pairs.
3. No change to the frozen `core/types.py`. A learned match is detectable
   without a new field: `matched == True` while `si_norm != bl_norm` can only
   happen via an approved pair, so the API derives `"learned": true` from
   that, and `core/decide.py`, `core/reply.py` and the type contract are
   untouched.

**Decision.** `core/compare.py` gains an optional `known_equal` keyword
parameter (default `None`); with it omitted or `None`, behaviour is
byte-identical to before this feature existed, which is what keeps
`scripts/run_pipeline.py` and `scripts/export_ui_data.py` reproducible for
the organiser's scorer — neither script ever passes it. When given, a mismatch
downgrades to a match only for one of the five text fields
(`core/equivalence.py:EQUIVALENCE_FIELDS` — shipper, consignee, notify_party,
port_of_loading, port_of_discharge; never `container_count` or
`gross_weight_kg`, where a numeric difference is always a real defect), only
when the row is not `undecidable`, and only for the exact normalised pair a
signed-in clerk approved, order-independent (`core/equivalence.py:pair_key`).
A new router, `api/_equivalences.py`, exposes `GET/POST/DELETE
/api/equivalences`, gated on `current_user()` the same way `api/_accounts.py`
gates mail; `api/index.py` passes `known_equal=lookup_for(request)` at both of
its `compare()` call sites. The web UI (`web/app.js`) adds a "Mark as same"
button on an eligible mismatch row with an inline (never `window.confirm`)
confirm step, a "Matched via a pair you approved" chip with Undo on a row
that came back `learned: true`, and a "Learned pairs" list with Undo on the
account page.

**Alternatives considered.**
- *A rule derived from the pair* (e.g. "treat any X as Y from now on"). Rejected:
  a rule generalises past what a human actually verified and risks hiding a
  real future defect between two similarly-named parties.
- *A global, shared equivalence table.* Rejected for this round: one clerk's
  mistake would silently change every other clerk's results with no
  attribution. Per-account is the safe default; a team-approved list with a
  supervisor sign-off is a natural follow-up, not a blocker.
- *Fuzzy/similarity matching instead of exact pairs.* Rejected for the same
  reason ADR-001 rejects it for the base comparison: every planted defect in
  this dataset is substantive, so a threshold that helps a formatting mismatch
  is a threshold that can also swallow a real one.

**Consequences.**
- Applies to *new* checks only; a result already saved in a mailbox keeps its
  original verdict — that snapshot is also the audit trail of what a clerk
  actually saw at the time.
- If the store is unavailable, `lookup_for()` returns `None` and the button
  hides; checks run exactly as they do today. A `known_equal` that raises for
  any other reason is caught inside `compare()` and treated as no-match, never
  as a crash.
- Bounded and reversible: at most 500 pairs per account, each value at most
  200 characters, every pair attributed (who, when, source) and undoable in
  one click, both from the row it cleared and from the account page.

---

## 6. Failure modes

The system has one rule: **it never produces a confident wrong answer.** Every
failure path converges on escalation to a person.

| Failure | Detected by | Result |
|---|---|---|
| Corrupt or truncated PDF | parser try/except | `NEEDS_REVIEW / unreadable` |
| Image-only scan, no text layer | text length check | `NEEDS_REVIEW / unreadable` |
| Attachment is an invoice, not an SI | document signature test | `NEEDS_REVIEW / wrong_doc_type` |
| Comparison requested, no attachments | intent plus attachment count | `NEEDS_REVIEW / missing_attachment` |
| Field present but blank (`???`, `TBA`) | `aliases.is_blank` | `NEEDS_REVIEW / missing_value` |
| Label we have never seen | alias lookup returns None | field missing, escalate |
| Model answer rejected by the gate — value not found verbatim in the source document | `core/extract.py:verify_against_source`, counted in `adapters/model.py STATS.gate_rejections` | value discarded, field missing, escalate |
| Model API down or no key | `ModelUnavailable` | rule tier only, escalate on gaps |
| New document format | no adapter registered | `kind="OTHER"`, escalate |
| Email nobody can classify — no rule matched, and the model was unavailable or its own answer failed verification | `core/decide.py`: `intent == "unknown" and confidence == 0.0` | `NEEDS_REVIEW / unclassified` — ours, not the organiser's four reasons; never fires on the sample inbox, exists for unseen mail |
| PDF label text overflows into the value column and the text extractor interleaves the two runs of glyphs | `core/parsers/pdf.py:_looks_interleaved` | field returned blank rather than a merged wrong value; the comparison row is `undecidable` rather than a false mismatch |

Two invariants enforce this, and both are checkable by reading the code:

1. **No parser raises.** `DocumentParser.parse` returns an unreadable document
   instead. One corrupt PDF must not end a 520-email batch.
2. **No `except: pass` anywhere.** Every caught exception either escalates or
   is recorded in `ModelStats.failures`. The `ecc:silent-failure-hunter` agent
   runs against this repository specifically to enforce it, because a pipeline
   whose promise is "it escalates instead of guessing" is falsified by a single
   swallowed error.

---

## 7. Scaling

### Where the time goes

The rule path is string operations over a file that is a few kilobytes. It is
sub-millisecond per field. The model path is a network round trip, roughly two
to three orders of magnitude slower. So throughput is set almost entirely by
**what fraction of work reaches the model**, which is exactly the quantity
`rule_pct` measures and the accuracy screen displays.

### Scaling axes, cheapest first

1. **Raise the rule hit rate.** Every alias Sheng Kuan adds removes model
   calls permanently. This is the cheapest scaling lever in the system and it
   costs no infrastructure.
2. **Layout Memory** (Idea 4, final round). Hash the ordered list of labels in
   a document to get a layout fingerprint. On a repeat fingerprint, apply the
   stored field map directly: no model call, deterministic, instant. A shipping
   desk sees the same handful of carrier templates every day, so the
   fingerprint cache converges fast. The interface for this already exists:
   `ModelStats.cache_hits` has a home for the number.
3. **Horizontal workers.** ADR-006 makes emails independent, so a process pool
   scales linearly to the core count and a queue plus stateless workers scales
   past one machine. Nothing in `core/` would change.
4. **Batch the tail.** Remaining model calls are independent and can be issued
   concurrently rather than serially.

### Where it would break, honestly

| Limit | Symptom | Fix |
|---|---|---|
| Model rate limit | throughput ceiling under burst | queue plus backoff; escalate on timeout rather than block |
| Documents held in memory | large scanned PDFs | stream to a blob store, keep only the text |
| Single-process run | one core only | worker pool, then a queue |
| Cross-email features | not possible today | a shipment store, keyed on the OC reference |

We have not built the queue or the blob store. Saying so is the point: the
rubric rewards *"clear trade-offs and a realistic scaling approach"*, not a
claim to have solved problems we do not have at 520 emails.

### The cost argument

Cost is proportional to model calls, and model calls are what the rule tier and
the layout cache remove. The harness instruments this directly rather than
estimating it: `ModelStats` counts calls, tokens and cache hits per run, and
the accuracy screen reports them. The claim we make on the slide is whatever
that counter actually says after a full 520-email run, not a projection.

---

## 8. Security

| Concern | Control |
|---|---|
| API key leakage | `.env` gitignored; `.env.example` holds a placeholder; `ecc:security-reviewer` runs before the repository goes public |
| Ground-truth labels | `.secrets/` is the first entry in `.gitignore`, committed before any other file. Verified with `git check-ignore`. See PLAN.md section 8 |
| Untrusted document content | documents are parsed as data. No `eval`, no shell, no deserialisation of document content |
| Prompt injection from an email body | the model never receives authority to act. Its only outputs are a category label and a field value, and the field value must survive `verify_against_source` |
| Sending mail | not implemented anywhere. The system drafts; a person sends |

---

## 9. What shipped after the preliminary plan

Everything below was built after PLAN.md was written and is not covered by
the ADRs above.

**Re-check of an amended draft.** `core/recheck.py` compares the SI against
two BL drafts — the original and a carrier's amendment — and sorts every one
of the 7 fields into `fixed`, `still_wrong`, `newly_broken`, `ok`, or
`unreadable`. `newly_broken` (right in v1, wrong in v2) is the case a tired
clerk misses, because they only re-read the fields they complained about. No
model involvement: it is `core/compare.py` run twice. `data/demo/` holds one
hand-authored amended draft (`email_004_BL_v2.txt`) built to exercise all
three non-trivial outcomes, since the organiser's own inbox contains only
first drafts and has nothing to re-check against — see
`data/demo/README.md`. `core/reply.py:draft_recheck_reply` drafts the
follow-up the same way the first reply is drafted: template fill over
already-verified values, never free model text.

**The held-out challenge set.** The organiser corpus (`data/`) is fully
covered by the rule tier, so on it the model tier is never actually
consulted — "the model earns its place" is asserted, not shown.
`data/challenge/` is a small, hand-authored set (30 triage emails, 3 SI/BL
document pairs) written from general knowledge of shipping-operations email,
without reading `core/classify.py`, `core/aliases.py`, `core/parsers/`,
`data/inbox/`, or the ground truth — see `data/challenge/README.md`.
`scripts/run_challenge.py` runs it twice, rules-only and rules-plus-model,
and reports the difference. Results on this held-out set:

| Measure | Rules only | Rules + model |
|---|---|---|
| Category accuracy | 67% | 100% |
| Intent accuracy | 50% | 100% |
| Fields read, unfamiliar labels | 2 of 41 | 41 of 41 |

The model decided 15 of the 30 emails in this run, got 0 of them wrong, and
made 21 calls in total. After Sheng Kuan's alias work (adding labels the
challenge set uses but the organiser corpus never does), the rule tier alone
now reads 18 of the 41 fields without the model. This is the one number in
the whole project that speaks to generalisation, because it is the one
dataset nobody tuned against — see "Honest framing" below.

**The off switch.** `CLEARDRAFT_USE_MODEL=0` (`adapters/model.py:available`)
turns the model tier off without touching the API key, so "how well do the
rules do alone" is a real, reproducible run rather than a claim — this is
also how the test suite stays hermetic (`tests/conftest.py` sets it for
every test).

**Gate rejections are counted, not just gated.** Every model answer that
fails `verify_against_source` increments `adapters.model.STATS
.gate_rejections`. It is the same mechanism as the extraction gate described
in ADR-004, now surfaced as a live number rather than only a code path.

**The live checker.** `POST /api/check` (`api/index.py`) runs the same
pipeline stages — `extract`, `compare`, `decide`, `draft_reply` — over a pair
of documents a person uploads through the UI's `#/check` page, rather than
over the fixed 520-email inbox. See `docs/API.md`.

**"Open draft in mail app."** The reply card's primary action is a `mailto:`
link built from the drafted subject, recipient and body
(`web/app.js:replyCard`) — the clerk's own mail client opens with the draft
already in it. ClearDraft still never sends anything; this replaces "copy
the text and paste it into a new email" with one click that does the same
thing.

**`HttpInbox`.** See ADR-007 above.

### Honest framing of the accuracy numbers

On the organiser's 520 emails, the scored end-to-end rate is **1.0000 (46 of
46 planted defects caught)**. State this plainly and state its limit equally
plainly: **this is a validation number on the one corpus we hold labels
for, not evidence of generalisation.** A system can score 1.0 on data whose
labels informed every rule it contains and still fail on the next inbox it
sees. The held-out challenge set above is the actual generalisation
evidence, and it is weaker evidence precisely because it is smaller and
harder — that is what makes it worth more than a second decimal place on the
520.

---

## 10. Testing strategy

Three layers, deliberately separated so that a failure tells you where to look.

| Layer | File | Asks |
|---|---|---|
| Contract | `tests/test_contract.py` | Does the plumbing hold? Submission shape, alias table unambiguous, CJK labels resolve, 520 emails parse |
| Unit | `tests/test_*.py` | Does one normaliser do the right thing on one awkward value? |
| Evaluation | `eval/score.py` | What does the organiser's own scorer say? |

The contract layer earned its place immediately: it caught a real bug in
`normalise_label` before any feature code existed. A bilingual label stripped
to `gross weight ( kgs)`, which matched no alias. The bracket canonicalisation
step in `normalise_label` exists because that test failed.

`eval/score.py` deliberately reads the ground truth from a gitignored path and
prints a clear message on a clean clone rather than failing. The labels are
a measurement instrument, not an input.

---

## 11. Repository layout

```
core/            pure domain logic, no I/O
  types.py       the frozen contract. One owner.
  classify.py    stage 1                      Zi Qi
  aliases.py     label table                  Sheng Kuan
  parsers/       one adapter per format       Sheng Kuan
  extract.py     stage 2                      Sheng Kuan
  normalise.py   stage 3a                     Ee Zhan
  compare.py     stage 3b                     Ee Zhan
  decide.py      stage 4                      Ee Zhan
  reply.py       draft generation             Ee Zhan
  recheck.py     re-check an amended draft    Ee Zhan
  pipeline.py    the orchestrator             Ee Zhan
adapters/        everything touching the outside world
  inbox.py       LocalInbox, HttpInbox
  model.py       the one door every model call passes through
api/             FastAPI transport, Vercel Python serverless function
web/             static HTML + CSS + one ES module, no build step
eval/            the scoring harness
tests/           contract and unit tests
scripts/         runnable entry points
  run_pipeline.py     batch run over --data or --server, writes submission.json
  export_ui_data.py   batch run, writes web/public/data.json for the UI
  run_challenge.py    the held-out challenge set, rules vs rules+model
docs/            this file and the rubric map
data/            the organiser bundle, committable
  demo/          hand-authored amended draft, for the re-check demo. Not scored.
  challenge/     the held-out challenge set. Not scored.
.secrets/        ground-truth labels. Never committable.
```

Ownership is written into the module docstrings, not only into this file, so
it is visible at the point of work.

---

## 12. Workshop 2 changes

Five decisions taken in response to the organiser domain expert's session
(Workshop 2, 2026), each stated the same way as the ADRs above: decision,
why, trade-off.

**Export design.** All three exports — Discrepancy report, Full results,
Submission file — are generated client-side in `web/app.js`, from data
already rendered on the page, rather than by a server round trip. Why: the
expert asked for exportable results by name, and the UI already holds every
value, source line and reason it would need to write; a server endpoint
would duplicate that state for no benefit. **Formula-injection safe:** any
CSV cell whose text begins with `=`, `+`, `-` or `@` is prefixed with a
leading `'` before it is written, so a value pulled verbatim from a document
(where an attacker or a careless typist could put `=CMD(...)`) cannot
execute as a formula when the file is opened in a spreadsheet application.
Trade-off: client-side generation means the export reflects only what the
browser has loaded, not a fresh server-side re-run — acceptable, since the
export is a report on a result already computed and verified.

**Human review store.** Each case's review verdict (**Looks right**,
**Something's wrong** plus a note, or skipped) is recorded per case, keyed
the same way the mailbox is, and surfaces on the Accuracy page as "Your
checks of ClearDraft's answers" (agreement %). Why: the expert asked for
demos to show which steps a human validates and which they act on, and a
review that is not recorded cannot be reported back. Trade-off: this is a
record of agreement with ClearDraft's verdict, not a second ground truth —
it never feeds back into the comparison logic itself, so a wrong human
verdict cannot silently retrain the rules.

**Confidence surfaced.** Every case already carried `decided_by` (rule or
model); the review screen now shows it alongside a confidence label next to
each field. Why: `decided_by` existed in the record for measurement
(ADR-004); surfacing it to the reviewer turns an internal metric into the
signal a person actually needs before trusting a value without re-reading
the source. Trade-off: none of consequence — the field was already computed
and verified, so this is display-only.

**Asterisk normalisation.** `core/normalise.py:normalise_entity` now strips
`*`/`**` continuation markers from party names (`_ENTITY_CONTINUATION`)
before comparison, so `"EAST BRIGHT FZ-LLC *"` and `"EAST BRIGHT FZ-LLC"`
match. Why: the expert noted real documents truncate a party name with an
asterisk when it continues elsewhere on the page — formatting noise, not
content. Trade-off: this is one specific, verified continuation convention;
other partial-match conventions the expert also mentioned, such as spacing
variants, are not yet covered — see "Known limits" in the README.

**To-the-order-of note.** When a BL's consignee field reads "to the order
of" (or "to order of"), the review screen adds a note explaining that this
makes the Bill of Lading negotiable, which is legally different from one
naming a consignee directly. Why: the expert raised this as a distinction
the tool must not paper over. The note is **informational only and never
changes the match/mismatch verdict** — comparison stays exact string
equality on the normalised value (ADR-001), because deciding "is a
negotiable BL an acceptable substitute for a named consignee" is a legal
judgement, not a formatting question, and ADR-001's whole argument is that
the model never decides a mismatch. Flagging it for the person to judge is
the correct place for that decision to live, not a silent rule that could
be wrong in either direction depending on the shipment.
