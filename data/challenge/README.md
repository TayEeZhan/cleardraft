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
- `docs/` contains 13 synthetic SI/BL pairs: three unfamiliar-label baseline
  pairs plus adversarial cases for MT/KG and LB/KG conversion, unsupported
  units, compound container quantities, suffix abbreviations, reordered party
  names, country suffixes, punctuation, model field placement, and a real
  side-by-side PDF box layout.
- `docs/gold.json` — the true field values and genuine defects for each pair.
- `multi_draft/` — one email with an SI and two candidate draft BLs. It is a
  fixture for the explicit draft-selection policy: attachment order must not
  silently decide which draft is checked.

This data is entirely synthetic and fictional (companies, vessels, booking
and BL numbers are made up) and is never scored by the organiser scorer.
