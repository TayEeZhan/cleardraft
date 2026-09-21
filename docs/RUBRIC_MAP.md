# Rubric evidence map

The final rubric says: **"do not reward the same evidence twice."**

So every criterion gets its own artefact. If two criteria point at the same
demo moment, one of them scores low. This table is the checklist for the
slide deck and the five-minute video.

| # | Criterion | Pts | The evidence, and nothing else uses it |
|---|---|---:|---|
| 1 | End-to-End Functionality | 25 | The live deployment. A judge opens the URL, picks a real email from the 520, and watches classify to extract to compare to drafted reply, with no manual step. The `submission.json` export and the scorer output prove it ran over the whole inbox, not one cherry-picked case. *New:* the same pipeline runs live on mail nobody staged — paste an email or drop a `.eml` file into "Your mail" and watch it classified, compared and drafted in one pass, not just replayed from the 520-email snapshot. Show this in the video, not in slides — it is a live action, not a static number. |
| 2 | Architecture and Scalability | 15 | `docs/ARCHITECTURE.md`: seven ADRs with named trade-offs, the dependency rule that `core/` never imports `adapters/`, and section 7 including the honest table of where it breaks. Backed by `core/pipeline.py` taking injected stages. *New:* `docs/ARCHITECTURE.md` §12 "Workshop 2 changes" applies the same discipline — decision, why, trade-off — to five post-plan additions (export design, human review store, confidence surfaced, asterisk normalisation, the To-the-order-of note). Reference the section number in slides; do not re-narrate it in the video, which has no time for a second architecture walkthrough. |
| 3 | Technology Integration | 15 | The two-tier router: rules, then Haiku 4.5, then the verbatim verification gate, all through the single door in `adapters/model.py`, with `rule_pct` and token counters measured live. The point is that the model is load-bearing but bounded, which is the opposite of a cosmetic wrapper. *New:* the held-out `data/challenge/` set is the proof the router earns its place on data nobody tuned against: category accuracy 67% → 100%, intent accuracy 50% → 100%, fields read under unfamiliar labels 2 of 41 → 41 of 41, model decided 15 emails and got none wrong across 21 calls. Put these numbers on a slide (README "Evidence" table has the same figures); do not repeat the 520-corpus 1.0000 score here — that belongs to criterion 1/5, not this one. |
| 4 | Engineering Quality and Robustness | 15 | The failure-mode table in ARCHITECTURE section 6, every row traceable to code. The contract test that caught the bilingual-label bug before feature code existed. The two invariants: no parser raises, no swallowed exception. The `ecc:silent-failure-hunter` and `ecc:security-reviewer` passes. *New:* the export button is client-side and formula-injection safe (a CSV cell that starts with `=`, `+`, `-` or `@` is neutralised before it reaches the file), the attack test that an email with HTML/script in its subject and body renders as plain text, the `HttpOnly`/`Secure` session cookie, and scrypt password hashing with never-plain-text storage. State these as the QA checklist in the video's robustness beat; keep the failure-mode table itself only in the doc and slides, since it is too dense to read on screen. |
| 5 | Solution Effectiveness and User Value | 10 | Time on task, now sourced rather than asserted: a Workshop 2 organiser domain expert stated a manual SI/BL comparison takes up to 10 minutes per pair, for a documentation team of 5–10 people. The sample inbox has 119 pairs, so that is up to about 20 staff-hours of comparison against about 1.7 seconds of machine time — label this an estimate on the slide, since it is a workshop figure, not a repo measurement. The expert also asked by name for exportable results (a CSV with each mismatch, the SI value, the BL value, and why); the "Discrepancy report" and "Full results" exports are that ask, built. Plus the count: 46 planted defects across 520 emails, and what a missed one costs in a reissued Bill of Lading. Say the staff-hours estimate once, in the video's opening problem statement; repeat only the "up to 10 min/pair" line on the slide, not the full arithmetic. |
| 6 | User Experience and Differentiation | 10 | Hick's Law, stated out loud and shown: four tabs not five, one primary action per screen, a fixed seven-row table with mismatches pinned, proof behind one click. Differentiation is Idea 2: it writes the reply. The clerk presses Send. Nobody else will demo the second half of the job. *New:* the human review buttons (**Looks right — next case** / **Something's wrong**, with a note / skip) and the per-case "Your part" line are a direct answer to the workshop ask that demos show which steps a human validates and which they act on. The "To the order of" note is the same idea applied to one legal nuance the expert raised: it surfaces the distinction, informationally, without the tool pretending to make the legal call. Demo the review buttons live in the video; mention the To-the-order-of note in the walkthrough narration, not on a slide — it is a small detail that reads better spoken than printed. |
| 7 | Impact and Future Potential | 10 | The roadmap with a reason each item is next, not a wish list. Layout Memory with the cache-hit number. The inbox port, so a real Microsoft Graph mailbox is one adapter. Idea 5 and letter-of-credit compliance as the commercial case. Success measures: defect catch rate, escalation precision, rule percentage. *New:* the workshop identified a concrete near-term buyer — a documentation team of 5–10 people doing this by hand today — which is a more credible adoption path than a hypothetical market. Keep this to one line on the roadmap slide; it is not a demo moment, so it has no place in the video. |

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
