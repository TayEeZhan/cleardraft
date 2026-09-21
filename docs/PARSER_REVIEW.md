# Parser review — where the four document readers break on a real document

**Reviewer:** Sheng Kuan · **Scope:** `core/parsers/{txt,xlsx,docx,pdf}.py`, `core/extract.py`, `core/aliases.py`

The four adapters were written against the organiser's generator, which emits
one predictable shape per format. This review asks a different question: what
happens on a **real** SI or draft BL, the kind the final round runs on.

Six findings. Four are fixed and merged (PR #3). **Two are open** and are
deliberately left for the final round rather than rushed before the deadline.

The ordering principle throughout: **a missing value costs one escalation to a
human, a wrong value is trusted by `compare()` and reaches the customer.** Every
fix below prefers the first.

---

## Fixed (PR #3)

### 1. One missing library killed all 250 attachments — HIGH
`core/parsers/__init__.py` imported all four adapters in a single statement, so
an absent `pdfplumber` raised at import time and took down extraction for
**every** attachment, including the 192 `.txt` files that need no library at
all. This was not hypothetical: it happened on a fresh clone during this review,
because `cryptography` upgraded and left `cffi` missing.

**Fixed:** each adapter imports independently. A missing library is recorded in
`core.parsers.MISSING_PARSERS`, prints a warning, and those files escalate as
`NEEDS_REVIEW / unreadable`. Covered by `tests/test_parser_registry.py`.

### 2. PDF side-by-side boxes produced a wrong value — HIGH
Real Bills of Lading print Shipper and Consignee in boxes side by side, and the
text extractor emits both on one physical line:

```
Shipper ABC PAPER LTD Consignee XYZ TRADING LLC
```

The old code took everything after the first label as its value, so the shipper
became `"ABC PAPER LTD Consignee XYZ TRADING LLC"` and the consignee was never
found. Wrong, not missing — the failure mode this product exists to avoid.

**Fixed:** the value is cut at the next *different* field's label and the second
box is captured as its own field. Only aliases of 6+ characters may cut, so
`POL`, `POD` and `G.W.` cannot slice a real value. Covered by
`tests/test_pdf_columns.py`.

### 3. `.txt` required a colon — MEDIUM
Documents that align labels into columns (`Shipper      ABC PAPER LTD`) yielded
nothing at all.

**Fixed:** a shared matcher in `core/parsers/labels.py`, used by both `txt.py`
and `pdf.py`. That module imports nothing third-party **on purpose**: sharing it
via `pdf.py` would pull `pdfplumber` into the text path and undo finding 1.

### 4. `.txt` label with the party block beneath returned empty — MEDIUM
A document that writes `Shipper:` and puts the name on the indented line below
produced an empty value, escalating a perfectly good document.

**Fixed:** when a row carries no text at all, the **first** continuation line is
taken — never the whole address block, and the same rule on SI and BL, because
an asymmetric rule manufactures mismatches that do not exist. A blank *token*
(`???`, `TBA`) is explicitly excluded: that means the sender left it blank on
purpose and must keep escalating as `missing_value`. Covered by
`tests/test_txt_labels.py`.

---

## Open — recommended for the final round

### 5. `.xlsx` reads only column B of the first sheet — MEDIUM
`core/parsers/xlsx.py:58` takes `wb.worksheets[0]`, and lines 66-67 read only
`row[0]` as the label and `row[1]` as the value.

Real workbooks break all three assumptions:

| Real shape | Current result |
|---|---|
| Value in column C or further right (a spacer column between) | field missing |
| **Merged cells** — `openpyxl` returns the value only for the top-left cell, `None` for the rest | field missing |
| The BL on a second sheet, an instructions tab first | whole document reads as `OTHER` |
| `Shipper: ABC LTD` written inside one cell | label unresolved |

**Suggested fix:** take the first non-empty cell to the right of the label
rather than exactly `row[1]`; iterate all worksheets until one yields fields;
split a `label: value` pair inside a single cell. Roughly one hour with tests.

### 6. `.docx` reads only tables, and its evidence points nowhere — MEDIUM
`core/parsers/docx.py:71` iterates `document.tables` only.

- Labels written as **paragraphs** rather than table rows are invisible.
- **Text boxes** are invisible to `python-docx` entirely (they live in
  `w:txbxContent`), and carrier templates use them often.
- Only `cells[0]` and `cells[1]` are read, so a three-column layout, or a
  `label: value` pair inside one cell, is missed.
- `line_no` is hard-coded to `0` (line 98), so the "show me where you read
  that" panel cannot point at anything for Word documents. The contract allows
  0 for spreadsheet cells; using a running index would restore the evidence.

**Suggested fix:** after the tables, fall back to paragraph parsing; read text
boxes via `document.element.xpath('.//w:txbxContent//w:t')`; number the rows.

---

## Environment findings (these affect the deployment, not the code)

### A. `requirements.txt` does not pin what `pdfplumber` depends on
Only `pdfplumber==0.11.4` is pinned. It pulls in `pdfminer.six`, which pulls in
`cryptography`, which needs `cffi`. A fresh install during this review resolved
to `cryptography 50.0.1` **without** `cffi`, and `import pdfplumber` failed.

**Recommend:** pin `pdfminer.six`, `cryptography` and `cffi` explicitly.

### B. `email_499` changes verdict with the PDF library build
`email_499_BL.pdf` is genuinely corrupt (`Unexpected EOF`). Depending on the
`pdfminer` build, one machine repairs it and another refuses:

| Where | Result |
|---|---|
| The machine that generated `web/public/data.json` | `MISMATCH` (gross_weight_kg) |
| This machine, on plain `main` with no local changes | `NEEDS_REVIEW / unreadable` |

Both are defensible outcomes for a corrupt file, and **neither is caused by our
code** — verified by running unmodified `main`. But it means the board counts
shift by one (mismatch 46/45, needs review 22/23), and **the deployed site may
not match the committed snapshot.** Pinning (A) makes this reproducible.

### C. `pydantic==2.10.4` has no Python 3.14 wheel
Installing the pinned requirements on 3.14 fails while building
`pydantic-core`, so `fastapi` never installs either. The visible symptom is
worse than a missing dependency: **`pytest tests/` aborts during collection**,
because `tests/test_api.py` imports `fastapi`, so *no* test runs at all. On
3.13 the whole suite passes (204 tests here with that one module excluded).

**Recommend:** pin the deploy and CI runtime to Python 3.12 or 3.13, and
consider `pytest.importorskip("fastapi")` in `tests/test_api.py` so one absent
optional dependency cannot hide every other test — the same principle as
finding 1.

---

## Two alias entries worth a second opinion

These predate this review and currently pass, so they were left alone rather
than changed silently:

- **`"buyer" → consignee`.** In triangular trade the buyer is frequently not
  the consignee, so this can compare two different companies.
- **`"containers" → container_count`.** A row labelled "Containers" often lists
  container *numbers*, not a count.

Ee Zhan's call, since the comparator trusts whatever this table says.

---

## What protects these decisions

`tests/test_aliases.py` pins **24 lookalike labels** to "never map", each with
its reason: `Place of Receipt` (inland pickup, not the load port),
`Final Destination`, `Nett Weight`, `Tare Weight`, `VGM`, `Total Packages`
(cartons are not containers), `Also Notify` (a second, different party), and
others. A future "helpful" addition fails the suite instead of reaching a clerk.
