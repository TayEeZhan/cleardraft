# Zi Qi - Finalist Evaluation Plan

**Owner:** Ong Zi Qi

**Working area:** `eval/`, `data/external/`, evaluation documentation

**Goal:** provide independent evidence that ClearDraft generalises beyond the
organiser corpus, quantify its cost and speed advantage, and own the accuracy
story during the finalist presentation and Q&A.

This work should not require changes to the production pipeline in `core/`.
When the external dataset exposes a parser or comparison limitation, record the
failure first and coordinate any production-code change with that module's
owner. Do not silently tune the dataset to the implementation.

## 1. External generalisation dataset

Create `data/external/` with 8-12 anonymised, licensed, or independently
recreated SI/BL pairs that differ meaningfully from the organiser-generated
documents.

Recommended structure:

```text
data/external/
  README.md
  manifest.json
  gold.json
  pair_01/
    SI.pdf
    BL.pdf
  pair_02/
    SI.xlsx
    BL.docx
```

The set should include as many of these variations as practical:

- Alternative labels such as `POL`, `Loading Port`, and `Port of Shipment`.
- PDF documents with side-by-side or boxed fields.
- XLSX files with spacer columns, merged cells, and multiple worksheets.
- DOCX files using paragraphs, tables, and text boxes.
- Party names split across continuation lines.
- Ports with and without LOCODEs.
- Different punctuation and weight formats.
- Blank or missing required values.
- Clean pairs, mismatched pairs, and cases that should require human review.
- At least two labelled defects for each of the seven comparison fields where
  the available sample size permits it.

`README.md` must state:

- Where the documents came from or how they were recreated.
- Why they may be used and that no confidential information remains.
- Why each pair is challenging.
- The date and commit at which the dataset was frozen.
- That the dataset was not used to create the original rules.

### Freeze-before-tuning rule

1. Commit the documents and gold labels before changing extraction rules.
2. Run and save the untouched baseline.
3. Hash or tag the frozen version so later results can be reproduced.
4. If a later change improves the result, report both the original baseline
   and the new result.

Without this sequence, the set is development data rather than convincing
held-out evidence.

## 2. Evaluation outputs

Build an evaluation entry point in `eval/` that can run the frozen external
set in two modes:

1. Rules only: `CLEARDRAFT_USE_MODEL=0`.
2. Rules plus the model fallback.

Produce a machine-readable JSON result plus a presentation-friendly CSV or
Markdown summary.

Required metrics:

| Metric | Question answered |
|---|---|
| Per-field precision | When ClearDraft reports a defect, how often is it correct? |
| Per-field recall | How many real defects did ClearDraft catch? |
| Per-field F1 | What is the balanced precision/recall result? |
| False-clear rate | How often was a defective or undecidable case incorrectly cleared? |
| Exact-case accuracy | Were all seven field outcomes correct for the whole pair? |
| Human-review rate | How often did ClearDraft safely decline to decide? |
| Model call rate | What share of work reached the model? |
| Token usage | How many input and output tokens were consumed? |
| Runtime | What were total and per-email processing times? |
| Cost per email | What did the model-assisted run cost at the verified current price? |

The primary confusion matrix should compare final case outcomes:

```text
                        Predicted
Actual             Cleared   Mismatch   Human review
Cleared
Mismatch
Human review
```

If we want a category confusion matrix as well, create a separate external
triage-email set. SI/BL pairs alone cannot test all five inbox categories.

### Safety-first headline

The finalist slide should lead with a statement of this form:

> On documents not used to create our original rules, ClearDraft produced a
> false-clear rate of X%, caught Y of Z labelled discrepancies, and escalated
> uncertain cases instead of guessing.

Do not replace `X`, `Y`, or `Z` until the frozen evaluation has produced them.

## 3. Cost and speed comparison

Create a one-page comparison between ClearDraft and a naive "LLM for every
email" baseline.

For a fair comparison, use the same:

- Frozen external dataset.
- Model and provider.
- Document content.
- Pricing date and currency.
- Timing environment.
- Required output schema.

The naive baseline should send every eligible email/document pair to the
model, even when deterministic rules could decide it. Record at least:

| Measure | ClearDraft | Naive LLM |
|---|---:|---:|
| Emails or pairs processed | | |
| Model calls | | |
| Input tokens | | |
| Output tokens | | |
| Total model cost | | |
| Cost per email | | |
| Median and p95 latency | | |
| False-clear rate | | |
| Human-review rate | | |
| Source evidence retained | Yes | Measure |
| Deterministic final comparison | Yes | No |

Cost calculation:

```text
input cost  = input tokens  / 1,000,000 * current input price
output cost = output tokens / 1,000,000 * current output price
cost/email  = total model cost / processed emails
```

Verify the current provider pricing on the day the result is generated and
record the source and date. Do not place an unverified hard-coded price in the
evaluation code.

The intended conclusion is not merely "we are cheaper":

> ClearDraft avoids paying for AI when deterministic logic can already provide
> a safer, faster, reproducible answer.

## 4. Validation slide ownership

Zi Qi owns the accuracy and validation section of the finalist presentation.
Keep the three datasets visibly separate:

1. **Organiser corpus - validation:** 520 emails and the official labelled
   problem data.
2. **Team challenge set - held-out:** the existing `data/challenge/` tests of
   unfamiliar email phrasing and field labels.
3. **External-format set - finalist generalisation:** the frozen dataset from
   this plan.

Do not merge the three into one overall accuracy percentage. For each result,
show the dataset, sample size, mode, and metric definition.

Recommended validation slide:

- Organiser corpus: planted discrepancies caught and false-clear count.
- Existing challenge set: rules-only versus model-assisted results.
- External set: per-field result, false-clear rate, and confusion matrix.
- Efficiency: latency, model-call rate, and cost against the naive baseline.

## 5. Q&A ownership

Prepare short, evidence-backed answers for:

- Is 100% accuracy realistic?
- Was the model tested on genuinely unseen data?
- Why is AI needed if rules handled the organiser corpus?
- What prevents the model from inventing a field value?
- What is the false-clear rate?
- Why is human review not considered a failure?
- How was model cost calculated?
- Is the naive LLM comparison fair?
- Was the external dataset used to tune the system?
- Which file format is currently the weakest?
- What happens when the model or document parser is unavailable?

The central answer to memorise:

> The 100% organiser result is validation on the supplied labelled corpus, not
> a universal claim. Our generalisation evidence comes from separately frozen
> held-out and external datasets. We report false clears, escalations, and
> per-field performance instead of hiding everything behind one accuracy
> number.

## 6. Delivery order and acceptance criteria

1. Add and document the external dataset.
2. Commit and tag or hash the frozen baseline.
3. Record the untouched rules-only and hybrid results.
4. Add the reproducible evaluation script and tests.
5. Produce per-field metrics and confusion matrices.
6. Run the naive LLM cost/speed baseline.
7. Create the one-page comparison and validation-slide content.
8. Rehearse the difficult Q&A with the team.

The work is complete when another team member can clone the repository, run a
documented command, reproduce the reported metrics, and trace every finalist
slide number back to a saved evaluation result.
