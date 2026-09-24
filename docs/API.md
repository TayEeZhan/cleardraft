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

These endpoints are implemented and deployed:

| Endpoint | Status |
|---|---|
| `GET /api/health` | Built — now also reports `accounts` (bool: is storage configured) |
| `POST /api/check` | Built — this is the live checker behind the UI's `#/check` page |
| `POST /api/process-email` | Built — accepts either an uploaded `.eml` file (field `eml`, 4 MB max) or a **pasted email** (`subject`/`body`/`sender` form fields + up to 4 `files`), runs it through the whole pipeline, and returns `{board, detail, model, seconds, saved}` in the same shapes as `web/public/data.json`. Behind the inbox's "Your mail" upload |
| `POST /api/auth/signup` | Built — see "Accounts" below |
| `POST /api/auth/login` | Built |
| `POST /api/auth/logout` | Built |
| `GET /api/auth/me` | Built |
| `GET /api/mail` | Built — the signed-in user's saved mailbox |
| `POST /api/mail/import` | Built — merge locally-saved results into the account |
| `DELETE /api/mail` | Built — clear the saved mailbox |
| `DELETE /api/mail/{email_id}` | Built — remove one saved email |
| `GET /api/equivalences` | Built — this account's learned "marked as same" pairs |
| `POST /api/equivalences` | Built — mark one exact SI/BL wording as the same, with an optional note |
| `PATCH /api/equivalences/{id}` | Built — edit a pair's wording or note |
| `DELETE /api/equivalences/{id}` | Built — undo one pair |
| `POST /api/equivalences/evaluate` | Built — re-apply this account's pairs to already-checked cases, live |

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
  "accounts": true,
  "parsers": ["txt", "pdf", "xlsx", "docx"],
  "missing_parsers": {},
  "commit": "a1b2c3d",
  "model": "claude-haiku-4-5",
  "model_switch": "on"
}
```

`model_available` is `adapters.model.available()` — honest about whether an
API key is configured. `parsers` / `missing_parsers` come from
`core.parsers.supported()` / `core.parsers.MISSING_PARSERS`: an optional
third-party library that failed to import shows up here instead of failing
silently the first time someone uploads that file type. `commit` is the
first 7 characters of `VERCEL_GIT_COMMIT_SHA` (Vercel sets this at build
time), or `"local"` outside Vercel, so two live deploys can be told apart at
a glance. `model` is the exact model ID from `adapters.model.MODEL`.
`model_switch` is `"off"` when `CLEARDRAFT_USE_MODEL=0` and `"on"`
otherwise (`adapters.model.switch_enabled()`) — distinct from
`model_available`, since the switch can be on with no key configured, in
which case `model_available` is still `false`.

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

### `POST /api/process-email`

Two request shapes, same pipeline underneath:

1. **Upload a `.eml` file** — `multipart/form-data` field `eml`, ≤ 4 MB.
2. **Paste an email** — no `eml` field; instead `subject` (optional),
   `body` (required, ≤ 50,000 characters), `sender` (optional — defaults to
   `unknown@pasted.local`), and 0–4 `files` (`.txt`/`.pdf`/`.xlsx`/`.docx`,
   4 MB total). The server builds an `email.message.EmailMessage` out of
   these fields, serialises it to bytes, and runs it through **the exact
   same** parse → classify → extract → compare → decide → draft_reply chain
   as an uploaded `.eml` (`api.index._process_eml_bytes` — one pipeline, no
   duplicated logic). `email_id` is `"up_" + sha1(bytes)[:12]` either way;
   in paste mode `detail.filename` is always `"Pasted email"`.

**400 responses**, shape `{"error": "...", "detail": "..."}`:

| `error` | When |
|---|---|
| `missing_email` | Neither an `eml` file nor a non-blank `body` was given |
| `unsupported_extension` | `.eml` extension isn't `.eml`, or a pasted `files` entry isn't `.txt`/`.pdf`/`.xlsx`/`.docx` |
| `file_too_large` | `.eml` exceeds 4 MB, or pasted `files` exceed 4 MB combined |
| `body_too_long` | pasted `body` exceeds 50,000 characters |
| `too_many_files` | more than 4 `files` attached in paste mode |
| `not_an_email` | the bytes (uploaded or built from the paste) don't parse as an email |

**200 response** adds two accounts-aware fields on top of `{board, detail,
model, seconds}`:

- `saved` (bool) — `true` when the caller was signed in (a valid
  `cd_session` cookie) and the result was written into their mailbox;
  `false` when signed out.
- `save_error` — present only when `saved` is `false` **and** the caller
  was signed in but the save itself raised. This should be rare: the
  mailbox cap (see "Accounts" below) always makes room by dropping the
  oldest entry, so it never fails for being "full" in the literal sense —
  its value is `save_failed`: whatever storage-level failure
  prevented the save, so the check result is still returned instead of a
  500.

---

## Learned equivalences ("marked as same")

A signed-in clerk can mark one exact SI/BL wording, on one of the five text
fields (`shipper`, `consignee`, `notify_party`, `port_of_loading`,
`port_of_discharge`), as "the same". That pair is remembered per account
(`core/equivalence.py`, storage key `equiv:<email>`) and downgrades that
exact mismatch on future checks (`core/compare.py`'s `known_equal`), and —
via the `evaluate` endpoint below — on already-checked/saved cases too, live.
`container_count` and `gross_weight_kg` can never be taught: a numeric
difference is always a real defect.

### `GET /api/equivalences`

Lists the signed-in account's pairs. Each record now also carries `note`
(string, `""` if never set) and `updated_at` (ISO UTC string, or `null` if
never edited) — older records saved before this field existed report `""`
/ `null`.

### `POST /api/equivalences`

```json
{"field": "consignee", "si_value": "EAST BRIGHT FZ-LLC", "bl_value": "UAB NOVAKOPA", "note": "renamed after merger", "source": "web-check"}
```

`note` is optional, ≤ `MAX_NOTE_LENGTH` (280) characters —
`400 {"error": "note_too_long", ...}` if longer.

The raw `si_value`/`bl_value` may be up to `MAX_INPUT_LENGTH` (2000)
characters — `400 {"error": "value_too_long", ...}` past that. The
**normalised** wording (what is actually compared and stored) must still fit
`MAX_VALUE_LENGTH` (200) characters — a company name with its postal address
glued onto the same cell can run well past 200 raw characters while
normalising to a short name, and that pair must still be learnable; the
200-character cap now applies after normalisation, not before.

Learning an **already-known pair** (same field, same two wordings, either
order) no longer errors: it returns `200 {"pair": <existing record>,
"already_marked": true}`. If the existing pair's `note` was empty and this
request sent a non-empty one, that note is saved onto the existing pair
(and `updated_at` is set) rather than discarded.

### `PATCH /api/equivalences/{pair_id}`

```json
{"si_value": "EAST BRIGHT FZ-LLC (RENAMED)", "bl_value": "UAB NOVAKOPA", "note": "renamed after merger"}
```

Any of `si_value`, `bl_value`, `note` may be sent; omitted ones are left
unchanged. `field` may be sent but only to confirm it — a `field` that
differs from the pair's stored field is rejected:

| `error` | Status | When |
|---|---|---|
| `field_immutable` | 400 | `field` sent and different from the stored field |
| `note_too_long` | 400 | `note` over 280 characters |
| `value_too_long` | 400 | raw wording over 2000 chars, or normalised wording over 200 |
| `invalid_pair` | 400 | new wording is blank, or both sides normalise the same |
| `duplicate_pair` | 409 | the new wording clashes with **another** saved pair (same field, same normalised pair) |
| `not_found` | 404 | no such id on this account (including another account's id) |
| `not_signed_in` | 401 | signed out |
| `accounts_unavailable` | 503 | storage not configured |

On success, wording changes are re-normalised with the pair's (unchanged)
field and re-validated with the same `can_learn` rule as `POST`. `updated_at`
is set to the current time on every successful edit, including a note-only
edit.

### `POST /api/equivalences/evaluate`

Re-applies "the one rule" (see `core/equivalence.evaluate_case`) to a batch
of already-checked/saved cases, live, using the signed-in account's current
pairs — this is what lets the inbox and a saved mailbox case re-count
without re-running the pipeline, and without ever touching the organiser's
scored `submission.json`.

```json
{"cases": [ { "email_id": "...", "status": "MISMATCH", "category": "BL_COMPARISON", "review_reason": null, "defect_fields": ["consignee"], "comparisons": [...], "recheck": {...} } ], "draft": false}
```

Each case is the same shape as one entry of `web/public/data.json`'s
`detail` map (or `POST /api/check`'s response). `draft: true` additionally
requires the case to carry `from`/`subject`/`body` and requires **exactly
one** case in the request.

**Caps**, all `400`:

| `error` | When |
|---|---|
| `too_many_cases` | more than 600 cases |
| `too_many_rows` | a case has more than 7 comparison rows, or more than 7 recheck rows |
| `value_too_long` | any comparison row's si/bl `value` exceeds 2000 characters |
| `body_too_long` | a case's `body` exceeds 20,000 characters |
| `body_too_large` | the whole `cases` array serialises past 20,000 characters |
| `draft_requires_one_case` | `draft: true` with zero or more than one case |
| `invalid_case` | a case is not a JSON object |

Auth: `401 not_signed_in` signed out, `503 accounts_unavailable` when
storage isn't configured — same as every other equivalences route.

**200 response**:

```json
{
  "cases": [
    {
      "email_id": "email_004",
      "status": "MISMATCH",
      "defect_fields": ["notify_party"],
      "has_defect": true,
      "changed": true,
      "rows": {"consignee": "a1b2c3d4"},
      "recheck": {},
      "reply_draft": "Hi Mitchelle, ... (only when draft:true and changed)",
      "recheck_reply_draft": "... (only when draft:true, changed, and the case has recheck rows)"
    }
  ]
}
```

`rows` maps each comparison field **covered by a saved pair** to that pair's
id (fields with no covering pair are simply absent). `recheck` maps each
recheck field whose outcome changed (`still_wrong`/`newly_broken` → `ok`) to
`{"outcome": "ok", "pair_id": "..."}`. `changed` is true when the effective
status, effective defect fields, or any recheck outcome differs from what
was checked. Results come back **in the same order as the request**.

`reply_draft` / `recheck_reply_draft` are built by reconstructing
`Email`/`FieldValue`/`FieldComparison`/`Decision`/`RecheckRow` from the
submitted JSON and calling the real, unmodified `core.reply.draft_reply` /
`draft_recheck_reply` — never a re-implementation of the template logic.
Any reconstruction problem simply omits that draft for that case; it never
turns into a 500.

`NEEDS_REVIEW` cases and non-`BL_COMPARISON` cases are always returned with
their checked status/defects unchanged (marking a pair can't fix an
unreadable file or a blank field) — only their `rows`/`recheck` maps may
still note a covered field for display purposes upstream.

---

## Accounts

Sign-up/login plus a small per-user saved mailbox, so a visitor can create
an account, paste or upload emails through the checker, and see them again
on return — **without ever connecting a real mailbox**. No Gmail/OAuth
integration exists or is planned here; "put your Gmail stuff into the
website" means paste the email's text (or upload a `.eml`/attachments you
already saved), never hand over real Gmail credentials.

**Storage** — `adapters/store.py`, chosen once per process and cached:

1. **Upstash Redis REST API**, when `KV_REST_API_URL` + `KV_REST_API_TOKEN`
   are set (what the Vercel Upstash integration injects), falling back to
   `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`. Implemented over
   `urllib` only — no new pip dependency.
2. **In-memory dict** (`adapters.store.MemoryStore`), when
   `CLEARDRAFT_STORE=memory`. Tests and local dev only; not shared across
   processes and lost on restart.
3. **Unconfigured** — every accounts route answers `503
   {"error": "accounts_unavailable", ...}` rather than silently pretending
   to work.

`GET /api/health`'s `accounts` field reports whether a store resolved.

**Password hashing** — `hashlib.scrypt(password, salt=<16 random bytes>,
n=2**14, r=8, p=1, dklen=32)`, salt and hash stored as hex, compared with
`hmac.compare_digest`. A login attempt for an unknown email still runs one
scrypt call (against a fixed dummy salt) so response timing doesn't reveal
which addresses have accounts. Passwords are never logged and never appear
in an API response.

**Sessions** — a random `secrets.token_urlsafe(32)` token lives in cookie
`cd_session` (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` 30 days,
`Secure` when the request is HTTPS — checked via `request.url.scheme` or
`X-Forwarded-Proto`). The server stores only `sha256(token)` as the key
(`session:<sha256hex> -> email`, 30-day expiry), so a store dump never
yields a usable token.

**Storage keys**:

| Key | Value |
|---|---|
| `user:<email>` | `{"email", "created" (ISO date), "salt", "hash"}` |
| `session:<sha256(token)>` | the signed-in email, as a plain string |
| `mail:<email>` | `{"board": [...], "detail": {...}}` — same shapes as `web/public/data.json` |
| `loginfail:<email>` | failed-login counter, 15-minute expiry |

**Limits**:

- Email: lowercased/stripped, must contain exactly one `@`, a non-empty
  local part and domain, a `.` in the domain, ≤ 254 characters total.
- Password: 8–128 characters.
- Login lockout: 10 failed attempts per email inside 15 minutes ->
  `429 {"error": "too_many_attempts", ...}` on the next attempt (the 11th),
  via `store.incr` with a 15-minute expiry.
- Mailbox: capped at 200 emails. `POST /api/mail/import` and a signed-in
  `POST /api/process-email` both merge into the existing mailbox, dedupe by
  `email_id` (the newer copy wins), and drop the oldest entries once over
  the cap.
- `POST /api/mail/import` rejects a request body over 2 MB outright
  (`413 {"error": "too_large", ...}`) and silently drops any `board` row
  that isn't a dict with a string `email_id` starting `"up_"` (and any
  `detail` entry not keyed by a surviving id) rather than failing the whole
  import over one bad row.

**Env vars**: `KV_REST_API_URL`, `KV_REST_API_TOKEN` (or
`UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`) for production;
`CLEARDRAFT_STORE=memory` for tests/local dev without Redis.

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
