# Rubric evidence map

The final rubric says: **"do not reward the same evidence twice."**

So every criterion gets its own artefact. If two criteria point at the same
demo moment, one of them scores low. This table is the checklist for the
slide deck and the five-minute video.

| # | Criterion | Pts | The evidence, and nothing else uses it |
|---|---|---:|---|
| 1 | End-to-End Functionality | 25 | The live deployment. A judge opens the URL, picks a real email from the 520, and watches classify to extract to compare to drafted reply, with no manual step. The `submission.json` export and the scorer output prove it ran over the whole inbox, not one cherry-picked case. |
| 2 | Architecture and Scalability | 15 | `docs/ARCHITECTURE.md`: seven ADRs with named trade-offs, the dependency rule that `core/` never imports `adapters/`, and section 7 including the honest table of where it breaks. Backed by `core/pipeline.py` taking injected stages. |
| 3 | Technology Integration | 15 | The two-tier router: rules, then Haiku 4.5, then the verbatim verification gate, all through the single door in `adapters/model.py`, with `rule_pct` and token counters measured live. The point is that the model is load-bearing but bounded, which is the opposite of a cosmetic wrapper. |
| 4 | Engineering Quality and Robustness | 15 | The failure-mode table in ARCHITECTURE section 6, every row traceable to code. The contract test that caught the bilingual-label bug before feature code existed. The two invariants: no parser raises, no swallowed exception. The `ecc:silent-failure-hunter` and `ecc:security-reviewer` passes. |
| 5 | Solution Effectiveness and User Value | 10 | Time on task. The manual job is opening two documents and comparing seven fields by hand. Measured against the clock, versus the tool. Plus the count: 46 planted defects across 520 emails, and what a missed one costs in a reissued Bill of Lading. |
| 6 | User Experience and Differentiation | 10 | Hick's Law, stated out loud and shown: four tabs not five, one primary action per screen, a fixed seven-row table with mismatches pinned, proof behind one click. Differentiation is Idea 2: it writes the reply. The clerk presses Send. Nobody else will demo the second half of the job. |
| 7 | Impact and Future Potential | 10 | The roadmap with a reason each item is next, not a wish list. Layout Memory with the cache-hit number. The inbox port, so a real Microsoft Graph mailbox is one adapter. Idea 5 and letter-of-credit compliance as the commercial case. Success measures: defect catch rate, escalation precision, rule percentage. |

## Traps in this rubric

**Criterion 3 says "primarily cosmetic" is the Weak band.** A thin wrapper
around a model scores badly. Our defence is that the model is genuinely
load-bearing for extraction across four formats, and genuinely bounded by a
verification gate. Both halves of that sentence need to be demonstrated, not
asserted.

**Criterion 1 says "depend heavily on mock-ups" is the Weak band.** The demo
must run against the deployed URL on real data. No slide-based walkthroughs of
screens that do not exist.

**Criterion 6 weighs usability and differentiation equally.** A beautiful but
undifferentiated checker caps at about 5. Idea 2 is the differentiation, and
it costs little on top of Idea 1. Build it.

**Criterion 2 rewards trade-offs, not confidence.** The "where it would break"
table scores better than claiming it scales forever. Judges have heard the
second one before.
