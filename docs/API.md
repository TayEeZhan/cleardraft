# ClearDraft API contract

**Spec owner: Ee Zhan · Consumer: the web UI**

Everything is JSON over HTTP (`POST /api/check` is `multipart/form-data` in,
JSON out). No authentication — this is a demo running on public sample data.

---

## Where it runs

**Vercel Python serverless functions, same project as the UI.**

```
api/
  index.py          <- exposes `app`, a FastAPI instance
vercel.json         <- serves web/ as the site; rewrites /api/(.*) to /api/index
web/                <- static HTML + CSS + one ES module (web/app.js). No
                        build step, no framework. Deployed by pointing Vercel's
                        outputDirectory at web/ (see vercel.json).
```

Vercel auto-detects `api/index.py` as a Python function; `vercel.json`'s
rewrite sends every `/api/*` request to it, and the function receives the
original path, so the routes below are registered with the full `/api/...`
prefix inside `api/index.py` itself.

---

## The one thing to get right

**Do not re-implement any pipeline logic in the API.** The API is a transport
layer. It imports `core` and calls it. Every rule about classification,
comparison and escalation lives in `core/` and is covered by the test suite
(`python -m pytest tests/ -q`).

```python
from core.classify import classify
from core.compare import compare
from core.decide import decide
from core.extract import extract
from core.reply import draft_reply
```

If you find yourself writing an `if status == ...` branch that decides
something, it belongs in `core/decide.py`, not in the API.

---

## Built vs planned

Two endpoints are implemented and deployed:

| Endpoint | Status |
|---|---|
| `GET /api/health` | Built |
| `POST /api/check` | Built — this is the live checker behind the UI's `#/check` page |
| `POST /api/process-email` | Built — one `.eml` file (field `eml`, 4 MB max) runs through the whole pipeline; returns `{board, detail, model, seconds}` in the same shapes as `web/public/data.json`. Behind the inbox's "Your mail" upload |

Everything else originally sketched for this contract is **planned, not
built**: `GET /api/emails`, `GET /api/emails/{email_id}`, `GET /api/stats`,
`GET /api/attachments/{path}`, `POST /api/recheck/{email_id}`. In their
place, the UI's Inbox and Accuracy screens (`#/board`, `#/accuracy`) read
**`web/public/data.json`** — a full-run snapshot over the whole 520-email
inbox, produced by `python scripts/export_ui_data.py`. The board, per-email
detail, and accuracy numbers all come from that one exported file, not from a
live per-request API. `web/app.js` tries `/api/emails` first purely as a
forward-compatible fallback path and falls back to the snapshot — today that
fetch always misses, by design, because the endpoint does not exist yet.

The rest of this document describes the two built endpoints. The bullet list
at the end names the planned ones and what they were meant to do, so the gap
is explicit rather than silently dropped.

---

## Endpoints

### `GET /api/health`

Liveness plus enough to prove the pipeline can actually run.

```json
{
  "status": "ok",
  "model_available": false,
  "parsers": ["txt", "pdf", "xlsx", "docx"],
  "missing_parsers": {}
}
```

`model_available` is `adapters.model.available()` — honest about whether an
API key is configured. `parsers` / `missing_parsers` come from
`core.parsers.supported()` / `core.parsers.MISSING_PARSERS`: an optional
third-party library that failed to import shows up here instead of failing
silently the first time someone uploads that file type.

---

### `POST /api/check`

The live checker. Upload one Shipping Instruction and one draft Bill of
Lading, get back the same 7-row verdict a clerk would produce by hand. This
is what the UI's `#/check` page calls.

**Request** — `multipart/form-data`:

| Field | Required | Notes |
|---|---|---|
| `si` | yes | file, one of `.txt` `.pdf` `.xlsx` `.docx`, ≤ 2 MB |
| `bl` | yes | file, same constraints |
| `subject` | no | if given (with or without `body`), the email-reading step runs and is reported back |
| `body` | no | as above |

The upload is always treated as an explicit request to compare — the web
form does not run the intent classifier to decide whether to compare, it
only optionally runs it to *report* what an email with this subject/body
would have been read as.

**400 responses**, shape `{"error": "...", "detail": "..."}`:

| `error` | When |
|---|---|
| `missing_file` | `si` or `bl` was not attached |
| `unsupported_extension` | file extension is not one of the four supported formats |
| `file_too_large` | file exceeds 2 MB (checked by reading one byte past the cap, so an oversized upload is never held fully in memory) |

**200 response** (fields trimmed for brevity — see `api/index.py` for the
exact shape):

```json
{
  "reference": "5ALT-01226",
  "email_reading": null,
  "status": "MISMATCH",
  "review_reason": null,
  "rationale": "2 of 7 fields differ: consignee, notify_party",
  "category": "BL_COMPARISON",
  "decided_by": "rule",
  "defect_fields": ["consignee", "notify_party"],
  "documents": {
    "si": { "name": "email_004_SI.txt", "kind": "SI", "readable": true, "error": null },
    "bl": { "name": "email_004_BL.txt", "kind": "BL", "readable": true, "error": null }
  },
  "comparisons": [
    {
      "field": "consignee",
      "label": "Consignee",
      "matched": false,
      "undecidable": false,
      "si": { "value": "EAST BRIGHT FZ-LLC", "raw": "...", "line_no": 6, "label": "Consignee", "decided_by": "rule", "source": "email_004_SI.txt" },
      "bl": { "value": "UAB NOVAKOPA", "raw": "...", "line_no": 7, "label": "Consignee (Non-Negotiable)", "decided_by": "rule", "source": "email_004_BL.txt" }
    }
  ],
  "reply_draft": "Hi team, ...",
  "model": { "available": false, "calls": 0, "gate_rejections": 0, "input_tokens": 0, "output_tokens": 0 },
  "seconds": 0.04,
  "subject": "",
  "from": ""
}
```

Notes:
- **Always exactly 7 entries in `comparisons`**, in `core.types.COMPARE_FIELDS`
  order, produced by `core.compare.compare()` unchanged — same rule as the
  batch pipeline.
- `model` is a **per-request delta** on `adapters.model.STATS`: the counters
  before minus the counters after this one call, so a busy deployment's
  running totals never leak into one clerk's single check.
- The model, when consulted at all, runs **only on the SI/BL documents
  themselves**, for fields the format parsers could not locate — same
  verbatim-verification gate as the batch pipeline (`core/extract.py`). It
  never sees the uploaded `subject`/`body` for extraction, only for the
  optional `email_reading` classification.
- `email_reading` is `null` unless `subject` or `body` was actually supplied.

---

## Planned, not built

These were the original spec for this contract. None of them exist as live
endpoints today; the UI's Inbox and Accuracy screens get the same
information from `web/public/data.json` instead (see "Built vs planned"
above).

- **`GET /api/emails`** — the board: every email in the 520-email inbox, one
  row each, with `counts` for the four board tabs.
- **`GET /api/emails/{email_id}`** — the review screen for one email: full
  detail, 7 comparison rows, reply draft.
- **`GET /api/stats`** — the accuracy screen: category/defect/escalation
  breakdowns, `rule_pct`, live model counters.
- **`GET /api/attachments/{path}`** — an inbox attachment's extracted plain
  text, for showing the source document beside the table.
- **`POST /api/recheck/{email_id}`** — re-run one email live rather than
  serving a cached answer.

If any of these get built, they should read from the same `core` functions
`api/index.py` already imports, exactly as `scripts/export_ui_data.py` does
for the batch snapshot — no pipeline logic re-implemented in the route.

---

## How to know it works

```bash
uvicorn api.index:app --reload --port 8000
```

```bash
curl localhost:8000/api/health
```

```bash
curl -F si=@data/attachments/email_004_SI.txt \
     -F bl=@data/attachments/email_004_BL.txt \
     localhost:8000/api/check
```

`email_004`'s SI/BL pair is the useful one to eyeball: a real mismatch with
two defect fields (`consignee`, `notify_party`). Confirm all 7 comparison
rows come back, two with `matched: false`, and a populated `reply_draft`.

Then have the reviewer look at it:

```
Review api/ with the ecc:fastapi-reviewer agent.
```
