# Brief — Sheng Kuan · Extraction

**You own:** `core/parsers/*.py`, `core/aliases.py`, `core/extract.py`
**Nobody else touches those files. Do not edit anything outside them.**
If you need a change to `core/types.py`, message Ee Zhan. It is frozen.

**Tool:** Claude Code. Use Sonnet for the implementation. It is a well-specified
task and Opus buys nothing here.

---

## Your job in one line

Turn an attachment in any of four formats into seven field values, each
carrying the exact line it was read from.

```python
extract("data/attachments/email_001_SI.txt") -> ExtractedDoc(
    kind="SI",
    fields={
        "shipper": FieldValue(
            value="APRIL FAR EAST (M) SDN BHD",
            raw="Shipper/Exporter: APRIL FAR EAST (M) SDN BHD",
            line_no=4,
            label="Shipper/Exporter",
            decided_by="rule",
        ),
        ...
    },
    text="<the whole document as plain text>",
    readable=True,
)
```

---

## Why you specifically

You have seen how these document classes get managed in practice. That matters
more than the code here.

The alias table in `core/aliases.py` currently covers exactly the labels the
organiser's generator produces. That is enough to score well on the data we
were given, and **not enough for the final round**, which runs on data nobody
has seen.

**The single highest-value thing you can do on this project** is add the
aliases that real shipping documents use and this generator does not. Some
worth considering, and some traps:

| Add | Careful |
|---|---|
| "Shipper/Consignor" | "Place of Receipt" is NOT port of loading |
| "Receiver", "Consignee Name" | "Final Destination" is NOT port of discharge |
| "Also Notify", "Notify Address" | "Nett Weight" is NOT gross weight |
| "Total Packages", "Qty of Containers" | "Net Wt" must never map to `gross_weight_kg` |

A wrong alias is worse than a missing one. A missing label leaves the field
absent and the email escalates to a human, which is safe. A wrong alias feeds
the comparator a value from the wrong row, and the comparator trusts you.

---

## The four formats and what bites in each

| Format | Count | The trap |
|---|---|---|
| `.txt` | 192 | Entity addresses continue on the NEXT line, indented. Decide whether the address is part of the value. Be consistent between SI and BL or you will invent defects. |
| `.xlsx` | 22 | Gross weight is stored as a **number**, so there are no commas and no "KG" suffix. Label in column A, value in column B. |
| `.docx` | 8 | Labels are **bilingual**: English followed by Chinese in brackets, for example the port-of-loading and gross-weight rows. `aliases.normalise_label` already strips CJK and canonicalises brackets. Use it. Do not write your own. |
| `.pdf` | 28 | Three sub-traps. Some are **image-only scans with no text layer**. Some are **truncated corrupt bytes**. And ports are written **without** the UN/LOCODE here, while `.txt` writes them **with** it. |

On that last point: do not try to fix the port code difference. Return what is
on the page. `core/normalise.py` strips the code on both sides. If you strip it
too you will not break anything, but the layering will be wrong.

---

## Two rules that are not negotiable

### 1. No parser may raise.

```python
def parse(self, path: str) -> ExtractedDoc:
    try:
        ...
    except Exception as exc:
        return ExtractedDoc(
            path=path, kind="UNREADABLE", readable=False, error=str(exc)
        )
```

One corrupt PDF must not end a 520-email batch. An unreadable document is a
**result**, not an error. It becomes `NEEDS_REVIEW / unreadable`, which is a
correct answer that earns points.

### 2. The verification gate on model output.

When the rule tier misses a field you may ask Haiku for it. Whatever it
returns must appear **verbatim in `doc.text`**, casefolded and
whitespace-collapsed. If it does not, throw the value away and leave the field
missing.

```python
def verify_against_source(value: str, text: str) -> bool:
    norm = lambda s: " ".join(s.split()).casefold()
    return norm(value) in norm(text)
```

This is not a nice-to-have. It is the structural reason we can tell judges the
model cannot invent a consignee. Casefold and collapse whitespace, because the
model will tidy spacing. Do **not** make it fuzzy — a fuzzy gate is not a gate.

---

## Document kind detection

You also decide `kind`, and it drives the `wrong_doc_type` escalation. Read the
first non-blank line:

| Starts with | kind |
|---|---|
| `SHIPPING INSTRUCTION` | `SI` |
| `BILL OF LADING` | `BL` |
| `COMMERCIAL INVOICE` / `PACKING LIST` / `CERTIFICATE OF ORIGIN` | `OTHER` |
| anything else | `OTHER` |

There are 5 `wrong_doc_type` cases planted in the data, and they helpfully
carry an explicit marker line saying the document is not an SI. Do not rely on
that marker alone; real documents will not have it. Use the title line.

---

## Blank values are not missing values

`aliases.is_blank` already knows the tokens: `???`, `_______`, `TBA`, `TBC`,
`N/A`, empty. When a field is present but blank, store it with `value=""` so
the decider can tell "this document left it blank" (`missing_value`, an
escalation) apart from "this document has no such row" (field absent).

Five emails in the data are exactly this case.

---

## Definition of done

```bash
python -m pytest tests/ -q
python scripts/run_pipeline.py --data data --out out/submission.json
```

- [ ] All four parsers implemented, none of them raise on any of the 250 files
- [ ] `extract()` returns all seven fields for at least 99% of SI/BL attachments
- [ ] Every `FieldValue` has a real `line_no` (0 only for xlsx cells)
- [ ] The image-only PDFs come back `readable=False`
- [ ] The corrupt PDFs come back `readable=False`
- [ ] The commercial invoice / packing list / certificate files come back `kind="OTHER"`
- [ ] Blank-field documents return `value=""`, not a missing key
- [ ] `verify_against_source` implemented and unit tested
- [ ] `extract` stub count in the pipeline summary drops to zero

Useful while you work:

```bash
python -c "import sys;sys.path.insert(0,'.');from core.extract import extract;d=extract('data/attachments/email_001_SI.txt');print(d.kind);[print(f'{k:20} {v.value[:40]:42} line {v.line_no}') for k,v in d.fields.items()]"
```

---

## When you are done

Ask Claude Code to run the reviewers on your files:

```
Review core/parsers/ and core/extract.py with the ecc:python-reviewer agent,
then with the ecc:silent-failure-hunter agent. The second one matters most:
this pipeline's entire promise is that it escalates instead of guessing, so a
swallowed exception is a correctness bug, not a style issue.
```

Then push your branch and tell Ee Zhan.
