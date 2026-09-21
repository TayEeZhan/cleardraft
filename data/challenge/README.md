# Held-out challenge set

This is a small, hand-authored, held-out test set for the shipping-document
email triage system. It was written from general knowledge of how real
shipping-operations email and documents look — without reading
`core/classify.py`, `core/aliases.py`, `core/parsers/`, `data/inbox/`, or
`.secrets/` — so it does not reflect any of the system's internal rules,
label lists, or training examples.

Purpose: measure how well the rule tier and the model fallback generalise to
phrasing and document labels they were never tuned on, rather than how well
they match patterns they were built against.

Contents:
- `emails.jsonl` — 30 synthetic triage emails with gold category/intent
  labels, deliberately including terse/non-native phrasing, quoted threads,
  and category-boundary hard cases.
- `docs/pair1_SI.txt` / `pair1_BL.txt`, `pair2_SI.txt` / `pair2_BL.txt`,
  `pair3_SI.txt` / `pair3_BL.txt` — three synthetic SI/BL document pairs
  using unusual-but-realistic field labels not drawn from the system's own
  alias list.
- `docs/gold.json` — the true field values and genuine defects for each pair.

This data is entirely synthetic and fictional (companies, vessels, booking
and BL numbers are made up) and is never scored by the organiser scorer.
