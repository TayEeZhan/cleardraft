# Brief — Zi Qi · Classification and Evaluation

**You own:** `core/classify.py`, `eval/`, `tests/` (except `test_contract.py`)
**Nobody else touches those files. Do not edit anything outside them.**
If you need a change to `core/types.py`, message Ee Zhan. It is frozen.

**Tool:** Codex. This is a closed problem with a numeric target and no
cross-file dependencies, which is the shape Codex handles best.

---

## Your job in one line

Decide what each of the 520 emails is asking for, using rules wherever
possible and a model only on what the rules decline.

```python
classify(email) -> Classification(
    category="BL_COMPARISON",
    intent="compare",
    decided_by="rule",
    confidence=0.95,
    evidence="compare the SI and draft BL",
)
```

**Target: macro-F1 at or above 0.95.** This criterion is 30% of the
organiser's score, and it also gates the other 70%, because an email
misrouted away from `BL_COMPARISON` can never have its defect caught.

---

## The two traps

### Trap 1 — keyword matching does not work

These subjects are all `GENERAL`, and every one of them contains "SI" or "BL":

```
_Reminder_Paper - Submit SI & AED_21-01-2026
Pending BL Release 14_01_2026
APRIL PAPER - List of Outstanding BL (BDP SG) as of 2026-01-09
_RPA_ India HSS SD Billing Process Completed - MMSS 2507 V.257087E
```

And `SI_REQUEST` subjects routinely contain a full Bill of Lading number:

```
SI - MEDUUD104332 - DIRECT(MSC) - 5RSG-00133 - CALLAO_PERU - ORIGINAL - MEA
```

**Match the intent verb, not the nouns.** Who is asking whom to do what.

| Category | The ask |
|---|---|
| `BL_COMPARISON` | check / confirm / compare documents, or send a draft BL for checking |
| `SI_REQUEST` | provide / issue / send the shipping instruction |
| `INVOICE_QUERY` | billing, local charges, credit note, cancel an invoice, missing GR, D&D charges |
| `GENERAL` | status updates, berthing reports, reminders, RPA notices, HR, outstanding lists |
| `SPAM` | prizes, phishing, crypto, "one weird trick" |

`SPAM` is the easy 40. Do it first, get a clean win, move on.

### Trap 2 — intent is not category, and the difference is worth 91 emails

Both of these are category `BL_COMPARISON`:

```
"Please assist to send the draft BL for PSGSE9638346 for checking asap."
    -> intent = "send_doc"     ... they want a document sent. Nothing to compare.

"Please compare the SI and draft BL for 070500263211 and confirm
 (attachments appear to have been dropped)."
    -> intent = "compare"      ... they want a comparison. Attachments missing.
```

94 of the 220 comparison emails have **zero attachments**. 91 of those are
ground-truth `OK`. Only the handful that actually asked for a comparison should
escalate.

If you return `intent="compare"` for all of them, `core/decide.py` escalates
all 94 and our escalation precision falls from about 1.0 to about 0.05. Ee Zhan
cannot fix that downstream — the information only exists in the email text, and
you are the one reading it.

---

## Architecture: rules first, decline rather than guess

```python
CONFIDENCE_FLOOR = 0.60

def classify(email, *, use_model=True):
    hit = classify_by_rule(email)
    if hit is not None and hit.confidence >= CONFIDENCE_FLOOR:
        return hit                      # decided_by="rule"
    if use_model and model.available():
        return classify_by_model(email) # decided_by="model"
    return hit or Classification(category="GENERAL", intent="unknown",
                                 decided_by="rule", confidence=0.0,
                                 evidence="no rule matched")
```

`classify_by_rule` returns **None** when unsure. That is the whole design.

A rule that guesses is silently wrong and you will never find it. A rule that
declines is measurable: it shows up as a model call, and `rule_pct` drops.
Precision over recall, always.

### Why we care about `decided_by`

The organiser's scorer reads it and reports `rule_pct`. It does not change the
score. It does give us a free, organiser-computed number for the slide that
answers "how do you know the AI is not making things up": *most decisions were
not made by the AI at all.* Set it honestly.

### Model calls go through one door

```python
from adapters.model import complete_json, available, ModelUnavailable
```

Do not call the Anthropic SDK directly. Everything routes through
`adapters/model.py` so the call counters stay complete. Model: Haiku 4.5. Ask
for a small JSON object, never free text.

And catch `ModelUnavailable`. The pipeline must still run with the API key
removed — that is how we demonstrate the rule tier honestly on stage.

---

## Your second job: the evaluation harness

This is half your value on the team and it is what criterion 4 (Engineering
Quality, 15 points) rewards.

Build `eval/`:

1. **A dev slice you label yourself.** Take roughly 80 emails, stratified
   across the five categories, and label them by reading them yourself. Do not
   use the held-out labels to build this — see PLAN.md section 8.
2. **A confusion matrix printer.** Five by five, actual against predicted.
   This is what tells you *which* pair you are confusing, and the answer is
   almost always `SI_REQUEST` against `BL_COMPARISON`.
3. **Per-category precision, recall and F1**, plus macro-F1.
4. **A regression test that fails the build** when macro-F1 drops below the
   last committed number. Hackathon code drifts backwards at 3am; this stops it.
5. **`rule_pct`** — what fraction you decided without the model.

The confusion matrix goes straight into the accuracy screen of the UI and onto
a slide, so make its output easy to serialise to JSON.

---

## Your third job: unit tests for the normaliser

Ee Zhan writes `core/normalise.py`. You write the tests against the docstrings,
before or while he implements. That is a genuine second pair of eyes on the
module that decides half the score.

Cases worth pinning down:

```python
normalise_port("CALLAO, PERU (PECLL)") == normalise_port("CALLAO, PERU")
normalise_container_count("6 x 40'HC") == 6
normalise_container_count("1 x 20GP")  == 1
normalise_weight("21,577 KG") == 21577
normalise_weight("21577")     == 21577      # the xlsx path
normalise_entity("MOORIM SP CO., LTD") == normalise_entity("MOORIM SP CO LTD")

# and the ones that must NOT collapse:
normalise_entity("MOORIM SP CO., LTD") != normalise_entity("UAB NOVAKOPA")
normalise_container_count("3 x 40'HC") != normalise_container_count("4 x 40'HC")
```

That last pair matters. Every planted defect is substantive, so a normaliser
that is too aggressive destroys the 50% metric silently. Your tests are the
thing that catches it.

---

## Definition of done

```bash
python -m pytest tests/ -q
python scripts/run_pipeline.py --data data --out out/submission.json
```

- [ ] `classify_by_rule` handles all five categories and returns None when unsure
- [ ] All ten `SPAM` patterns caught
- [ ] The three GENERAL traps above classify as `GENERAL`, verified by a test
- [ ] `intent` distinguishes `send_doc` from `compare` — test both example emails
- [ ] Model fallback works, and the pipeline still runs with `ANTHROPIC_API_KEY` unset
- [ ] `decided_by` set honestly on every record
- [ ] `evidence` populated on every record, never empty
- [ ] Confusion matrix prints and serialises to JSON
- [ ] macro-F1 at or above 0.95 on your dev slice
- [ ] Regression test in place
- [ ] `classify` stub count in the pipeline summary drops to zero

---

## When you are done

Have Codex review your own work against this brief, then push your branch and
tell Ee Zhan. He runs the Opus review gate over the merged diff.
