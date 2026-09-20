# ClearDraft API contract

**Spec owner: Ee Zhan · Consumer: the web UI**

This is the agreed shape. Build to it exactly and the UI will work the first
time. If something here is wrong or awkward, say so and we change this document
first, not the code.

Everything is JSON over HTTP. No authentication — this is a demo running on
public sample data.

---

## Where it runs

**Vercel Python serverless functions, same project as the UI.**

Netlify is not an option: Netlify Functions run JavaScript, TypeScript and Go
only, and this entire pipeline is Python (`pdfplumber`, `openpyxl`,
`python-docx`). Vercel has a real Python runtime, so one repo deploys both the
site and the API, from one URL, with no CORS configuration at all.

Layout Vercel expects:

```
api/
  index.py          <- exposes `app`, a FastAPI instance
  requirements.txt  <- the API's own dependencies
vercel.json         <- routes /api/* to the Python function
web/                <- the Next.js UI
```

---

## The one thing to get right

**Do not re-implement any pipeline logic in the API.** The API is a transport
layer. It imports `core` and calls it. Every rule about classification,
comparison and escalation already lives in `core/` and is covered by 40 tests.

```python
from adapters.inbox import LocalInbox
from core import pipeline
from core.classify import classify
from core.compare import compare
from core.decide import decide
from core.extract import extract
from core.reply import draft_reply
```

If you find yourself writing an `if status == ...` branch that decides
something, it belongs in `core/decide.py`, not in the API.

---

## Performance note, read before you start

A full 520-email run takes about **4 seconds** end to end, because it opens 250
attachments. That is fine as a one-off and far too slow per request.

**Compute once at startup, serve from memory.** Build the full result set when
the module loads, hold it in a dict keyed by `email_id`, and have every
endpoint read from that dict. Serverless cold starts make this a few seconds on
the first request and instant thereafter.

---

## Endpoints

### `GET /api/health`

Liveness plus enough to prove the pipeline really ran.

```json
{
  "status": "ok",
  "emails": 520,
  "attachments": 250,
  "model_available": false,
  "pipeline_ready": true
}
```

`model_available` is `adapters.model.available()`. It is honest about whether
an API key is configured; the UI shows it on the accuracy screen.

---

### `GET /api/emails`

The board. Every email, one row each, small enough to send in one go.

Optional query parameters:
- `status` — `OK` | `MISMATCH` | `NEEDS_REVIEW`
- `category` — `BL_COMPARISON` | `SI_REQUEST` | `INVOICE_QUERY` | `GENERAL` | `SPAM`

```json
{
  "total": 520,
  "counts": {
    "needs_check": 129,
    "mismatch": 46,
    "needs_review": 22,
    "cleared": 323
  },
  "emails": [
    {
      "email_id": "email_004",
      "subject": "TO CONFIRM DOCS _ 5ALT-01226 _ ...",
      "from": "aziztz@safqa.co.ke",
      "reference": "5ALT-01226",
      "category": "BL_COMPARISON",
      "status": "MISMATCH",
      "review_reason": null,
      "has_defect": true,
      "defect_fields": ["consignee", "notify_party"],
      "attachment_count": 2,
      "decided_by": "rule"
    }
  ]
}
```

`reference` is the shipment reference, already extracted by
`core.reply._reference(email)`. Use that function — do not write another regex.

The four `counts` keys map exactly onto the four board tabs.

---

### `GET /api/emails/{email_id}`

The review screen. Everything needed to render one case.

```json
{
  "email_id": "email_004",
  "subject": "TO CONFIRM DOCS _ 5ALT-01226 _ ...",
  "from": "aziztz@safqa.co.ke",
  "body": "Hi Mitchelle, ...",
  "reference": "5ALT-01226",
  "category": "BL_COMPARISON",
  "intent": "compare",
  "status": "MISMATCH",
  "review_reason": null,
  "rationale": "2 of 7 fields differ: consignee, notify_party",
  "decided_by": "rule",
  "documents": {
    "si": {
      "path": "attachments/email_004_SI.txt",
      "kind": "SI",
      "readable": true,
      "error": null
    },
    "bl": {
      "path": "attachments/email_004_BL.txt",
      "kind": "BL",
      "readable": true,
      "error": null
    }
  },
  "comparisons": [
    {
      "field": "consignee",
      "label": "Consignee",
      "matched": false,
      "undecidable": false,
      "si": {
        "value": "EAST BRIGHT FZ-LLC",
        "raw": "CONSIGNEE: EAST BRIGHT FZ-LLC",
        "line_no": 6,
        "source": "attachments/email_004_SI.txt"
      },
      "bl": {
        "value": "UAB NOVAKOPA",
        "raw": "Consignee (Non-Negotiable): UAB NOVAKOPA",
        "line_no": 7,
        "source": "attachments/email_004_BL.txt"
      }
    }
  ],
  "reply_draft": "Hi Mitchelle, ..."
}
```

Rules for `comparisons`:
- **Always exactly 7 entries, always in `core.types.COMPARE_FIELDS` order.**
  `core.compare.compare()` already guarantees this. Do not filter or reorder —
  the UI pins mismatches to the top itself.
- `label` is the human name. Use `core.reply.FIELD_LABELS` — it already maps
  `notify_party` to `Notify Party` and `gross_weight_kg` to `Gross Weight (kg)`.
- `si` or `bl` may be `null` when that document had no such field.
- `raw` and `line_no` are the proof. The UI reveals them on click. Never drop
  them; they are the whole "show your working" feature.

For a non-comparison email (`SPAM`, `GENERAL`, …) return the same shape with
`comparisons: []` and both `documents` entries `null`.

---

### `GET /api/stats`

The accuracy screen.

```json
{
  "totals": { "emails": 520, "compared": 129, "defects_found": 46 },
  "categories": {
    "BL_COMPARISON": 220, "SI_REQUEST": 125,
    "INVOICE_QUERY": 75, "GENERAL": 60, "SPAM": 40
  },
  "defect_fields": {
    "container_count": 19, "port_of_discharge": 13, "gross_weight_kg": 12,
    "notify_party": 8, "consignee": 7, "shipper": 7, "port_of_loading": 6
  },
  "escalations": {
    "missing_attachment": 5, "wrong_doc_type": 5,
    "unreadable": 6, "missing_value": 6
  },
  "decisions": { "by_rule": 520, "by_model": 0, "rule_pct": 1.0 },
  "model": {
    "available": false,
    "calls": 0, "input_tokens": 0, "output_tokens": 0, "failures": 0
  },
  "runtime_seconds": 4.1
}
```

`decisions.rule_pct` and the `model` block come from `adapters.model.STATS`.
This is the "most decisions were not made by AI" evidence — it must be measured
live, never hardcoded.

---

### `GET /api/attachments/{path}`

Serve an attachment's extracted **plain text** so the review screen can show
the source document beside the table.

```json
{
  "path": "attachments/email_004_SI.txt",
  "kind": "SI",
  "readable": true,
  "text": "SHIPPING INSTRUCTION ...",
  "error": null
}
```

Reject any path that does not start with `attachments/` or that contains `..`.
It is a public endpoint reading from disk; treat the path as hostile.

---

### `POST /api/recheck/{email_id}` — optional, only if time allows

Re-run one email through the pipeline and return the same payload as
`GET /api/emails/{email_id}`. Useful for the demo video: show it running live
rather than serving a cached answer. Skip it if anything else is unfinished.

---

## Errors

Consistent shape, always:

```json
{ "error": "email_id not found", "detail": "email_999" }
```

Use `404` for a missing email or attachment, `400` for a bad path, `500` only
for a genuine crash. The pipeline itself does not raise — if you are catching
exceptions from `core`, something is wrong and it should be fixed there.

---

## How to know it works

```bash
uvicorn api.index:app --reload --port 8000
```

```bash
curl localhost:8000/api/health
```

```bash
curl localhost:8000/api/emails/email_004
```

`email_004` is the useful one to eyeball: a real mismatch with two defect
fields. Confirm all 7 comparison rows come back, two with `matched: false`,
and a populated `reply_draft`.

Then have the reviewer look at it:

```
Review api/ with the ecc:fastapi-reviewer agent.
```
