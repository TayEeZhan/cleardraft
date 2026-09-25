/* ClearDraft UI.
   Board data comes straight from the exported snapshot (public/data.json).
   Everything else — uploads, live checks, accounts — talks to the API
   directly, each at the point it is needed. */

const $ = (s, r = document) => r.querySelector(s);
const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let DATA = null;
let TAB = "mismatch";
let QUERY = "";

/* "Your mail" — the user's own uploaded results, persisted in this browser
   only. Same row/detail shapes as the sample DATA, so every render helper
   below can treat either source identically. */
let MINE = { board: [], detail: {} };
let SRC = "mine"; // "mine" | "sample" | "uploaded" — which source the inbox view shows
let UPLOADING = false;

/* "Uploaded: <name>" — the company's own dataset (.zip), processed live by
   POST /api/process-dataset. Same board/detail shapes as MINE/DATA, plus
   `name` (the zip's filename, shown in the source switch), `datasetKey`
   (a fresh random id minted on every successful upload — see reviewKey()
   below, uploadDataset()) and `submission` (the server's own
   organiser-format export, used verbatim by the Submission JSON export for
   this source — see initExportMenu()). A brand-new visitor sees no
   uploaded source at all until they upload one: loadUploaded() returns
   this same empty shape when nothing is saved. */
function emptyUploaded() {
  return {
    name: null, datasetKey: null, board: [], detail: {}, submission: {},
    stats: null, seconds: null, model_calls: null,
  };
}

/* A short, unpredictable id for "this particular upload" — not the zip's
   name (two uploads can share a filename with different content, and the
   SAME file re-uploaded twice must still count as two separate datasets)
   and not a content hash (expensive to compute client-side for a large
   zip). Used only to namespace review keys (reviewKey()) so a second
   upload whose email ids happen to collide with the first always starts
   unreviewed. */
function newDatasetKey() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function loadUploaded() {
  try {
    const raw = localStorage.getItem("cleardraft.uploaded.v1");
    if (!raw) return emptyUploaded();
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.board) && parsed.detail
      && typeof parsed.detail === "object" && parsed.name) return parsed;
  } catch { /* corrupt or inaccessible storage — start empty */ }
  return emptyUploaded();
}

/* Returns true on success, false when the browser refused to store it (over
   quota, private mode, storage disabled, ...) — the caller then tells the
   visitor it will not survive a refresh, rather than pretending it saved. */
function saveUploaded() {
  try {
    if (!UPLOADED.name) { localStorage.removeItem("cleardraft.uploaded.v1"); return true; }
    localStorage.setItem("cleardraft.uploaded.v1", JSON.stringify(UPLOADED));
    return true;
  } catch {
    return false;
  }
}

let UPLOADED = emptyUploaded();

/* Accounts are optional: the site works exactly as it always has when they
   are unavailable or the visitor is signed out. `available` is null until
   GET /api/auth/me answers; once it is false, the account control stays
   hidden for the rest of the session. */
let ACCOUNT = { available: null, user: null };

/* Learned equivalences ("marked as same"). PAIRS mirrors GET
   /api/equivalences for the signed-in account; EVAL_CACHE holds the result
   of POST /api/equivalences/evaluate per source, keyed by email_id, so a
   case view and the board never issue duplicate requests for the same
   case. Both are cleared on sign-out and whenever a pair is created,
   edited or removed — the one rule is re-applied fresh after every change. */
let PAIRS = [];
const EVAL_CACHE = { mine: new Map(), sample: new Map(), uploaded: new Map() };
const MAX_PAIRS = 500;

function invalidateEvalCache() {
  EVAL_CACHE.mine.clear();
  EVAL_CACHE.sample.clear();
  BOARD_BATCH_TRIED = { mine: false, sample: false, uploaded: false };
}

async function loadPairs() {
  if (!ACCOUNT.user) { PAIRS = []; invalidateEvalCache(); return; }
  try {
    const r = await fetch("/api/equivalences", { credentials: "same-origin" });
    if (r.ok) {
      const j = await r.json();
      PAIRS = (j && j.pairs) || [];
    } else {
      PAIRS = [];
    }
  } catch {
    PAIRS = [];
  }
  invalidateEvalCache();
  renderLearnedShortcut();
}

/* Keeps the topbar "Marked as same" shortcut's count badge in sync with
   PAIRS — hidden with zero pairs, otherwise showing the live count. */
function renderLearnedShortcut() {
  const link = document.getElementById("learned-shortcut");
  const count = document.getElementById("learned-shortcut-count");
  if (!link || !count) return;
  const n = PAIRS.length;
  count.textContent = String(n);
  count.hidden = n === 0;
  link.setAttribute("aria-label", n > 0 ? `Marked as same, ${n} pair${n === 1 ? "" : "s"}` : "Marked as same");
}

function pairById(id) { return PAIRS.find((p) => p.id === id) || null; }
let ACCOUNT_MODE = "signup"; // "signup" | "signin" — #/account form mode
let ADDMAIL_TAB = "drop";    // "drop" | "paste" — which add-mail tab shows
let ADDMAIL_TAB_TOUCHED = false; // has the visitor picked a tab themselves this session?
let ADDMAIL_OPEN = false;    // populated "Your mail": is the add-mail panel open

/* Reply text a clerk has typed over the drafted body. Session-only (a plain
   Map, not localStorage) — keyed by email_id + which reply ("reply" or
   "recheck"), so the normal reply and the recheck follow-up never clobber
   each other, and navigating away and back to the same case still shows
   what was typed. */
const EDITS = new Map();

/* Case ids where the clerk has opened a reply (Gmail, Copy or Other mail
   app) this session. Session-only, like EDITS — it only needs to soften the
   primary button's wording while the tab is open, not to persist. */
const REPLY_OPENED = new Set();

/* Human review of a case: whether a clerk confirmed ClearDraft's answer,
   flagged it as wrong (with a note), or — from the old "Done" feature —
   just marked it done. Signed-out reviews use the legacy browser key;
   signed-in reviews have an account-scoped cache and are also mirrored to
   the account API. This prevents two accounts on one browser seeing each
   other's feedback while preserving offline/local-first behaviour. */
function reviewStorageKey() {
  return ACCOUNT.user ? `cleardraft.reviews.v2:${ACCOUNT.user.email}` : "cleardraft.reviews.v1";
}

function loadReviews() {
  try {
    const raw = localStorage.getItem(reviewStorageKey());
    if (!raw) return {};
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) return obj;
  } catch { /* corrupt or inaccessible storage — start empty */ }
  return {};
}
let REVIEWS = loadReviews();

// Shown at most once per page load — a clerk clicking through several
// reviews in a row with a full quota must not get the same toast stacked
// on every single click.
let REVIEWS_SAVE_WARNED = false;

function saveReviews() {
  try {
    localStorage.setItem(reviewStorageKey(), JSON.stringify(REVIEWS));
  } catch {
    if (!REVIEWS_SAVE_WARNED) {
      REVIEWS_SAVE_WARNED = true;
      toast("Couldn't save reviews in this browser (storage full)");
    }
  }
}

/* One-time migration from the old "Done" list: every id there becomes a
   review with verdict "done", then the old key is removed so this only
   ever runs once. */
function migrateDoneList() {
  let raw;
  try { raw = localStorage.getItem("cleardraft.done.v1"); } catch { return; }
  if (!raw) return;
  try {
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) {
      let changed = false;
      for (const id of arr) {
        if (!REVIEWS[id]) { REVIEWS[id] = { verdict: "done", note: "", at: new Date().toISOString() }; changed = true; }
      }
      if (changed) saveReviews();
    }
  } catch { /* corrupt old list — nothing to migrate */ }
  try { localStorage.removeItem("cleardraft.done.v1"); } catch { /* best effort */ }
}
migrateDoneList();

/* Set by navigateToCase() right before it changes the hash; consumed once
   by whichever renderReview() call turns out to be the render that sticks
   (see navigateToCase() below for why that can't just be a direct focus() call). */
let FOCUS_CASE_HEADING = false;

/* Review verdicts are keyed by email_id, and email_ids can collide both
   between the uploaded dataset and the sample inbox (both may have
   "email_004" — an uploaded dataset runs the same batch pipeline over the
   organiser's own id scheme) AND between two different uploads in the same
   session (re-uploading the same zip, or uploading a different one that
   happens to reuse ids). So an "uploaded" key carries the CURRENT upload's
   own datasetKey too: "uploaded:<datasetKey>:<email_id>" — a fresh
   datasetKey is minted on every successful upload (uploadDataset()), so a
   second upload's cases always start unreviewed even when their ids match
   the first upload's exactly. `mine`'s ids are always server-generated
   ("up_" + a content hash) and never collide with anything, so only
   "uploaded" needs a namespace at all; "mine" and "sample" keep their
   original, unprefixed keys so reviews saved before this feature existed
   keep working unchanged. */
const UPLOADED_REVIEW_PREFIX = "uploaded:";

function reviewKey(source, id) {
  if (source !== "uploaded") return id;
  return `${UPLOADED_REVIEW_PREFIX}${UPLOADED.datasetKey || "-"}:${id}`;
}

/* The inverse of reviewKey() for an "uploaded:..." storage key — used only
   by the Accuracy page's cross-source flagged/problem lists, which have to
   read every REVIEWS key back out without already knowing its source. */
function parseReviewKey(key) {
  if (!key.startsWith(UPLOADED_REVIEW_PREFIX)) return { source: null, id: key };
  const rest = key.slice(UPLOADED_REVIEW_PREFIX.length);
  const sep = rest.indexOf(":");
  if (sep === -1) return { source: "uploaded", datasetKey: null, id: rest };
  return { source: "uploaded", datasetKey: rest.slice(0, sep), id: rest.slice(sep + 1) };
}

/* Drops every review recorded against ANY uploaded dataset — called both
   when a dataset is explicitly removed and right before a fresh upload
   replaces it, so orphaned reviews from a previous upload (unreachable
   anyway, since they were keyed to a datasetKey nothing points at any
   more) don't just pile up quietly in localStorage all session. */
function purgeUploadedReviews() {
  let changed = false;
  for (const key of Object.keys(REVIEWS)) {
    if (key.startsWith(UPLOADED_REVIEW_PREFIX)) { delete REVIEWS[key]; changed = true; }
  }
  if (changed) saveReviews();
}

function getReview(source, id) { return REVIEWS[reviewKey(source, id)] || null; }
function isReviewed(source, id) { return Boolean(REVIEWS[reviewKey(source, id)]); }
function isProblemReview(review) {
  if (!review) return false;
  return review.verdict === "flagged"
    || (review.verdict === "done" && /^problem:\s*\S/i.test(review.note || ""));
}

async function persistReview(key, review) {
  if (!ACCOUNT.user) return;
  try {
    await fetch("/api/feedback", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ key, verdict: review.verdict, note: review.note || "" }),
    });
  } catch { /* local cache remains the fallback */ }
}

async function deletePersistedReview(key) {
  if (!ACCOUNT.user) return;
  try {
    await fetch(`/api/feedback/${encodeURIComponent(key)}`, {
      method: "DELETE",
      credentials: "same-origin",
    });
  } catch { /* local deletion still takes effect */ }
}

function setReview(source, id, verdict, note) {
  const key = reviewKey(source, id);
  const review = { verdict, note: note || "", at: new Date().toISOString() };
  REVIEWS[key] = review;
  saveReviews();
  void persistReview(key, review);
}
function clearReview(source, id) {
  const key = reviewKey(source, id);
  delete REVIEWS[key];
  saveReviews();
  void deletePersistedReview(key);
}

/* "Fix a value": a clerk's typed correction for one field of one case, from
   the "Fix value" panel in seamTable(). Same storage discipline as REVIEWS
   just above — localStorage first (works signed out, per the plan),
   mirrored to the signed-in account's saved review when one already exists
   (PUT /api/feedback's schema, extended additively with `corrections` — see
   api/_feedback.py). Keyed the same way as REVIEWS: reviewKey(source, id)
   -> { field: { mode, si, bl, status, note, at } }.

   Two distinct, explicitly-chosen modes (never inferred from the text
   typed):
     "misread" — ClearDraft read the document wrong. The clerk corrects
       what the SI and/or BL actually SAY; `status` is POST /api/recheck-
       field's answer ("match" | "mismatch" | "undecidable") for exactly
       that wording, and a "match" clears the field like any other proven
       match.
     "amend"   — the BL itself is wrong. The clerk types what it SHOULD
       say; `bl` holds that target wording, `status` is always "mismatch"
       — this can never clear the field, because the document on file is
       still wrong until the carrier actually amends it. The reply's
       amendment line asks for exactly this wording instead of restating
       what was read. */
function correctionsStorageKey() {
  return ACCOUNT.user ? `cleardraft.corrections.v1:${ACCOUNT.user.email}` : "cleardraft.corrections.v1";
}

function loadCorrections() {
  try {
    const raw = localStorage.getItem(correctionsStorageKey());
    if (!raw) return {};
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) return obj;
  } catch { /* corrupt or inaccessible storage — start empty */ }
  return {};
}
let CORRECTIONS = loadCorrections();
let CORRECTIONS_SAVE_WARNED = false;

function saveCorrections() {
  try {
    localStorage.setItem(correctionsStorageKey(), JSON.stringify(CORRECTIONS));
  } catch {
    if (!CORRECTIONS_SAVE_WARNED) {
      CORRECTIONS_SAVE_WARNED = true;
      toast("Couldn't save this fix in this browser (storage full)");
    }
  }
}

function getCorrections(source, id) { return CORRECTIONS[reviewKey(source, id)] || null; }
function getCorrection(source, id, field) {
  const all = getCorrections(source, id);
  return (all && all[field]) || null;
}

/* Mirrors this case's corrections into the account's saved review — only
   when a review already exists for it. PUT /api/feedback requires a
   verdict, and "Fix value" must never silently create or alter a review
   verdict the clerk never chose just because they fixed a field; a case
   with no review yet stays localStorage-only for this browser, same
   "best effort, local cache is the fallback" discipline as persistReview()
   above. */
async function persistCorrections(source, id) {
  if (!ACCOUNT.user) return;
  const key = reviewKey(source, id);
  const review = REVIEWS[key];
  if (!review) return;
  const corr = CORRECTIONS[key] || {};
  try {
    await fetch("/api/feedback", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ key, verdict: review.verdict, note: review.note || "", corrections: corr }),
    });
  } catch { /* local cache remains the fallback */ }
}

function setCorrection(source, id, field, mode, si, bl, status, note) {
  const key = reviewKey(source, id);
  const existing = { ...(CORRECTIONS[key] || {}) };
  existing[field] = { mode, si, bl, status, note: note || "", at: new Date().toISOString() };
  CORRECTIONS[key] = existing;
  saveCorrections();
  void persistCorrections(source, id);
}

function clearCorrection(source, id, field) {
  const key = reviewKey(source, id);
  const existing = CORRECTIONS[key];
  if (!existing || !existing[field]) return;
  const next = { ...existing };
  delete next[field];
  if (Object.keys(next).length) CORRECTIONS[key] = next;
  else delete CORRECTIONS[key];
  saveCorrections();
  void persistCorrections(source, id);
}

/* Folds each remote review's `corrections` (api/_feedback.py's schema — the
   status/mode are stored server-side too, so no /api/recheck-field re-call
   is needed here) into CORRECTIONS, same "remote wins when newer, offline
   edits still local" spirit as loadFeedback()'s own REVIEWS merge just
   below. Called from loadFeedback() so a second device's corrections show
   up here, and from afterSignedIn() before that fetch even lands so a
   previous account's corrections never linger on screen (CORRECTIONS is
   reloaded fresh under the new account-scoped key first). */
function mergeCorrectionsFromReviews(remoteReviews) {
  let changed = false;
  for (const [key, review] of Object.entries(remoteReviews || {})) {
    if (!review || typeof review !== "object") continue;
    const remoteCorrections = review.corrections;
    if (!remoteCorrections || typeof remoteCorrections !== "object") continue;
    const merged = { ...(CORRECTIONS[key] || {}) };
    for (const [field, fix] of Object.entries(remoteCorrections)) {
      if (!fix || typeof fix !== "object") continue;
      merged[field] = {
        mode: fix.mode === "amend" ? "amend" : "misread",
        si: typeof fix.si === "string" ? fix.si : "",
        bl: typeof fix.bl === "string" ? fix.bl : "",
        status: fix.status === "match" || fix.status === "undecidable" ? fix.status : "mismatch",
        note: "",
        at: review.at || new Date().toISOString(),
      };
      changed = true;
    }
    if (Object.keys(merged).length) CORRECTIONS[key] = merged;
  }
  if (changed) saveCorrections();
}

async function loadFeedback() {
  if (!ACCOUNT.user) return;
  try {
    const r = await fetch("/api/feedback", { credentials: "same-origin" });
    if (!r.ok) return;
    const j = await r.json();
    const remote = (j && j.reviews && typeof j.reviews === "object") ? j.reviews : {};
    const local = REVIEWS;
    const merged = { ...local };
    for (const [key, review] of Object.entries(remote)) {
      const localTime = Date.parse((local[key] && local[key].at) || "") || 0;
      const remoteTime = Date.parse((review && review.at) || "") || 0;
      if (!local[key] || remoteTime >= localTime) merged[key] = review;
    }
    REVIEWS = merged;
    saveReviews();
    mergeCorrectionsFromReviews(remote);
    // A review recorded offline is uploaded after connectivity returns.
    for (const [key, review] of Object.entries(local)) {
      if (!remote[key] || (Date.parse(review.at || "") || 0) > (Date.parse(remote[key].at || "") || 0)) {
        void persistReview(key, review);
      }
    }
  } catch { /* account-scoped browser cache remains usable offline */ }
}

const TAB_LABELS = {
  mismatch: "Discrepancy found",
  needs_review: "Needs a human",
  cleared: "Cleared",
  other: "Other mail",
};

function pct(x) { return `${Math.round(x * 100)}%`; }

function loadMine() {
  try {
    const raw = localStorage.getItem("cleardraft.mine.v1");
    if (!raw) return { board: [], detail: {} };
    const parsed = JSON.parse(raw);
    if (parsed && Array.isArray(parsed.board) && parsed.detail && typeof parsed.detail === "object") return parsed;
  } catch { /* corrupt or inaccessible storage — start empty */ }
  return { board: [], detail: {} };
}

function saveMine() {
  try {
    localStorage.setItem("cleardraft.mine.v1", JSON.stringify(MINE));
  } catch {
    toast("Browser storage is full — clear your mail to add more");
  }
}

function getSourceKey() {
  try {
    const s = localStorage.getItem("cleardraft.source");
    if (s === "mine" || s === "sample") return s;
    // "uploaded" is only a valid restored choice while there is still an
    // uploaded dataset to show — a brand-new visitor (or one who removed
    // it) must never land on a source that no longer exists.
    if (s === "uploaded" && UPLOADED.name) return s;
  } catch { /* fall through to the default */ }
  return "mine";
}

function setSourceKey(s) {
  SRC = s;
  try { localStorage.setItem("cleardraft.source", s); } catch { /* per-viewer convenience only */ }
}

/* One board/detail lookup per source, everywhere — "mine" (MINE), "sample"
   (DATA) and "uploaded" (UPLOADED) all share this shape. */
function boardFor(source) {
  if (source === "mine") return MINE.board;
  if (source === "uploaded") return UPLOADED.board;
  return DATA ? DATA.board : [];
}
function detailFor(source, id) {
  if (source === "mine") return MINE.detail[id];
  if (source === "uploaded") return UPLOADED.detail[id];
  return DATA && DATA.detail[id];
}

function activeBoard() { return boardFor(SRC); }

/* Every source, active one first — an email_id can collide between the
   uploaded dataset and the sample inbox (both may run through the same
   organiser id scheme), so whichever source is actually on screen must
   always win when resolving a bare id. */
function sourceSearchOrder() {
  const order = [SRC, "mine", "sample", "uploaded"];
  return order.filter((s, i) => order.indexOf(s) === i);
}

function findRowById(id) {
  for (const source of sourceSearchOrder()) {
    const row = boardFor(source).find((r) => r.email_id === id);
    if (row) return { row, source };
  }
  return null;
}

function lookupDetail(id) {
  for (const source of sourceSearchOrder()) {
    const d = detailFor(source, id);
    if (d) return d;
  }
  return undefined;
}

/* Shapes a saved-case detail object into what POST /api/equivalences/
   evaluate expects: the fields it reads (email_id, from, subject, body,
   category, status, review_reason, defect_fields, decided_by, comparisons,
   recheck) and nothing more sensitive than that. */
function buildCaseForEval(d) {
  const comparisons = (d.comparisons || []).map((c) => ({
    field: c.field,
    si: c.si ? { value: c.si.value, raw: c.si.raw, line_no: c.si.line_no, label: c.si.label, decided_by: c.si.decided_by } : null,
    bl: c.bl ? { value: c.bl.value, raw: c.bl.raw, line_no: c.bl.line_no, label: c.bl.label, decided_by: c.bl.decided_by } : null,
    matched: Boolean(c.matched),
    undecidable: Boolean(c.undecidable),
  }));
  const out = {
    email_id: d.email_id,
    from: d.from || "",
    subject: d.subject || "",
    body: d.body || "",
    category: d.category,
    status: d.status,
    review_reason: d.review_reason,
    defect_fields: d.defect_fields || [],
    decided_by: d.decided_by,
    comparisons,
  };
  if (d.recheck && Array.isArray(d.recheck.rows)) {
    out.recheck = { rows: d.recheck.rows.map((r) => ({ field: r.field, si: r.si, v1: r.v1, v2: r.v2, outcome: r.outcome })) };
  }
  return out;
}

/* Shared fetch for a single-case, draft evaluation: posts to
   /api/equivalences/evaluate and returns the one evaluated case, or null on
   any failure (non-OK response, network error, malformed body). No caching
   here — callers that want caching (evaluateCaseLive) wrap this themselves. */
async function postEvaluate(caseObj) {
  try {
    const r = await fetch("/api/equivalences/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ cases: [caseObj], draft: true }),
    });
    if (!r.ok) return null;
    const j = await r.json();
    return (j.cases && j.cases[0]) || null;
  } catch {
    return null;
  }
}

/* Evaluates one saved case live, with a redrafted reply when it changed.
   Returns null when there is nothing to evaluate (signed out, no pairs,
   the endpoint failed) — every caller treats that exactly like "no
   evaluation available yet" and falls back to the checked result. */
async function evaluateCaseLive(id, source, d) {
  if (!ACCOUNT.user || !PAIRS.length) return null;
  const cache = EVAL_CACHE[source] || EVAL_CACHE.mine;
  if (cache.has(id)) return cache.get(id);
  const ev = await postEvaluate(buildCaseForEval(d));
  if (ev) cache.set(id, ev);
  return ev;
}

/* Live-evaluates a "Check a pair" result against the account's current
   pairs. Unlike evaluateCaseLive:
   - never cached — a check result is one-off (there is no board/detail
     entry keyed by email_id to cache against), and it must reflect the
     latest pairs every time it's asked for, including right after a pair
     was just added or removed.
   - never short-circuits on an empty PAIRS list. evaluate_case (server
     side) recomputes each row from its raw SI/BL values against whatever
     pairs the account currently has, rather than trusting the case's own
     stale `matched` flag — so calling it with zero pairs is exactly what
     correctly reverts a row to "discrepancy" after the last covering pair
     is undone. Only a missing signed-in user still short-circuits, since
     the endpoint requires one. */
async function evaluateCheckLive(d) {
  if (!ACCOUNT.user) return null;
  const withId = d.email_id ? d : { ...d, email_id: "check" };
  return postEvaluate(buildCaseForEval(withId));
}

/* Batch-evaluates the board's candidate cases (currently MISMATCH, or any
   row whose stored comparisons already carry a learned:true field) for the
   given source, so the board's tabs/counts/tags can reflect marked pairs
   without a request per row. Any failure leaves the cache untouched — the
   board then silently falls back to checked results, per the plan. */
let BOARD_BATCH_TRIED = { mine: false, sample: false, uploaded: false };

/* The server caps the WHOLE evaluate request at MAX_EVALUATE_BODY_CHARS
   (api/_equivalences.py: 20,000 characters for json.dumps(cases), not per
   case) - an inbox board can easily hold 40+ MISMATCH cases, and each one
   carries its full email body, so one request for the whole board reliably
   blows that cap and comes back 400 body_too_large. evaluateBoardBatch used
   to send exactly one such request and treat a non-OK response as "nothing
   to apply", which is why marking a pair never moved anything on the board:
   the batch call failed every time, silently. Chunking keeps each request
   comfortably under the cap; a generous safety margin below 20,000 absorbs
   the outer {cases, draft} JSON wrapper and per-request variance. */
const EVAL_BATCH_CHAR_BUDGET = 16000;

function chunkCasesByBudget(cases) {
  const chunks = [];
  let chunk = [];
  let chars = 2; // "[]"
  for (const c of cases) {
    const size = JSON.stringify(c).length + 1;
    if (chunk.length && chars + size > EVAL_BATCH_CHAR_BUDGET) {
      chunks.push(chunk);
      chunk = [];
      chars = 2;
    }
    chunk.push(c);
    chars += size;
  }
  if (chunk.length) chunks.push(chunk);
  return chunks;
}

async function postEvaluateChunk(cases, draft) {
  try {
    const r = await fetch("/api/equivalences/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ cases, draft }),
    });
    if (!r.ok) return [];
    const j = await r.json();
    return j.cases || [];
  } catch {
    return [];
  }
}

async function evaluateBoardBatch(source) {
  if (!ACCOUNT.user || !PAIRS.length) return false;
  if (BOARD_BATCH_TRIED[source]) return false;
  const board = boardFor(source);
  const cache = EVAL_CACHE[source];
  const candidateDetails = [];
  for (const row of board) {
    if (cache.has(row.email_id)) continue;
    const detail = detailFor(source, row.email_id);
    if (!detail) continue;
    const hasLearnedRow = (detail.comparisons || []).some((c) => c.learned);
    if (row.status === "MISMATCH" || hasLearnedRow) candidateDetails.push(detail);
  }
  BOARD_BATCH_TRIED[source] = true;
  if (!candidateDetails.length) return false;

  const cases = candidateDetails.slice(0, 600).map(buildCaseForEval);
  const chunks = chunkCasesByBudget(cases);
  const results = await Promise.all(chunks.map((chunk) => postEvaluateChunk(chunk, false)));

  let changed = false;
  for (const evs of results) {
    for (const ev of evs) if (ev && ev.email_id) { cache.set(ev.email_id, ev); changed = true; }
  }
  return changed; /* any chunk that failed just leaves its cases uncached - board falls back to checked results for those */
}

/* Field order for effective-status recomputation — mirrors
   core.types.COMPARE_FIELDS exactly, so a rebuilt defect_fields list orders
   the same way the pipeline's own does. */
const COMPARE_FIELD_ORDER = [
  "shipper", "consignee", "notify_party", "port_of_loading",
  "port_of_discharge", "container_count", "gross_weight_kg",
];

/* "Fix a value" is a LAYER applied on top of effectiveFor()'s own base
   result — never written into EVAL_CACHE. EVAL_CACHE is Mark-as-same's own
   cache of a server evaluation; writing corrections into it would collide
   with (and starve) that evaluation the moment both apply to the same case
   (evaluateCaseLive()/evaluateBoardBatch() skip a case EVAL_CACHE already
   has an entry for). Layering keeps the two independent and composable:
   raw pipeline result -> Mark-as-same evaluation (EVAL_CACHE, if any) ->
   corrections (this function, live, every call) -> human-feedback override
   (effectiveFor()'s own last step, unchanged).

   Re-applies core/decide.py's own precedence exactly: a proven mismatch
   outranks an unreadable field, which outranks a clean case. A corrected
   field can only ever move BETWEEN "defect" and "undecided" via "misread"
   mode (mode "amend" always counts as a defect, on purpose — see
   fixValueConfirmPanel) — it can never make the case MISMATCH -> OK while
   another field is still unreadable, and never NEEDS_REVIEW/missing_value
   -> OK unless every field is actually decided and matching. */
function applyCorrections(source, row, base) {
  const corr = getCorrections(source, row.email_id);
  if (!corr || !Object.keys(corr).length) return base;
  // Same eligibility as core.equivalence.evaluate_case's is_comparison_case,
  // PLUS a missing_value escalation (an unreadable field is exactly what
  // "Fix value" exists to resolve) — never any other NEEDS_REVIEW reason
  // (missing_attachment/unreadable/wrong_doc_type/unclassified), none of
  // which has field-level comparisons to correct in the first place.
  const eligible = base.status === "MISMATCH" || base.status === "OK"
    || (base.status === "NEEDS_REVIEW" && base.review_reason === "missing_value");
  if (!eligible) return base;
  const detail = detailFor(source, row.email_id);
  const comparisons = detail && detail.comparisons;
  if (!comparisons || !comparisons.length) return base;

  const defect = [];
  const undecided = [];
  for (const c of comparisons) {
    const fix = corr[c.field];
    if (fix) {
      if (fix.mode === "amend") { defect.push(c.field); continue; }
      // mode "misread": trust POST /api/recheck-field's own answer for the
      // clerk's corrected wording, exactly like the pipeline trusts
      // compare() for the original wording.
      if (fix.status === "mismatch") defect.push(c.field);
      else if (fix.status === "undecidable") undecided.push(c.field);
      continue; // "match" -> neither a defect nor undecided
    }
    // No active correction for this field: a Mark-as-same pair covering it
    // (base.rows, from EVAL_CACHE) excludes it entirely, exactly like an
    // ordinary covered row; otherwise trust the pipeline's own raw reading.
    if (base.rows && base.rows[c.field]) continue;
    if (c.undecidable) undecided.push(c.field);
    else if (!c.matched) defect.push(c.field);
  }
  const orderedDefect = COMPARE_FIELD_ORDER.filter((f) => defect.includes(f));
  const orderedUndecided = COMPARE_FIELD_ORDER.filter((f) => undecided.includes(f));
  let status, review_reason;
  if (orderedDefect.length) { status = "MISMATCH"; review_reason = null; }
  else if (orderedUndecided.length) { status = "NEEDS_REVIEW"; review_reason = "missing_value"; }
  else { status = "OK"; review_reason = null; }
  return { ...base, status, review_reason, defect_fields: orderedDefect, changed: true };
}

/* The effective status/defect count for a board row: the Mark-as-same
   evaluation cached in EVAL_CACHE when one exists, otherwise the checked
   result — with any saved "Fix value" corrections layered on top (see
   applyCorrections above), then a human "problem" review as the final
   override. Used by tabOf()/counts/tags/exports so the board reflects both
   live. */
function effectiveFor(source, row) {
  const cache = EVAL_CACHE[source];
  const ev = cache && cache.get(row.email_id);
  let base = ev
    ? { status: ev.status || row.status, review_reason: ev.review_reason || row.review_reason,
        defect_fields: ev.defect_fields || row.defect_fields || [], changed: Boolean(ev.changed),
        rows: ev.rows || {} }
    : { status: row.status, review_reason: row.review_reason,
        defect_fields: row.defect_fields || [], changed: false, rows: {} };
  base = applyCorrections(source, row, base);
  if (!isProblemReview(getReview(source, row.email_id))) return base;
  return { ...base, status: "NEEDS_REVIEW", review_reason: "human_feedback", changed: true, human_feedback: true };
}

/* "Hi Najiha," -> "Najiha" — reused from the server's own drafted reply
   rather than recomputing core/reply.py's own recipient-name rule client
   side; falls back to "team", same as the server does when nothing matches.
   Matches up to the comma rather than a fixed character class, so a name
   the server's own fallback pulled from an email local-part — "Hi
   arlene_yamomo," — survives whole instead of being cut at the underscore. */
function greetingName(d) {
  const m = /^Hi ([^,\n]+),/.exec(d.reply_draft || "");
  return m ? m[1] : "team";
}

/* "Fix a value"'s reply redraft — a client-side generalisation of
   core/reply.py's MISMATCH_TEMPLATE/CLEAR_TEMPLATE/REVIEW_TEMPLATE, the
   exact wording the server would produce for the same effective status and
   defect list. No server round trip: corrections must redraft the reply
   even signed out, where the account-gated POST /api/equivalences/evaluate
   (which redrafts for marked pairs) is not available. A "misread"
   correction's amendment line uses the clerk's corrected wording (what the
   document actually says); an "amend" correction's line instead asks the
   carrier for the typed target wording — never restates what was read,
   since that is precisely what is still wrong. */
function draftCorrectedReply(d, defectFields, status, corrections) {
  const name = greetingName(d);
  const ref = d.reference || d.email_id || "this case";
  if (status === "NEEDS_REVIEW") {
    return `Hi ${name},\n\nWe could not complete the check on ${ref}: a required field was missing or blank in the documents.\nA colleague is reviewing this manually and will revert shortly.\n`;
  }
  if (status !== "MISMATCH") {
    return `Hi ${name},\n\nNo mismatch detected. Draft BL for ${ref} is OK to proceed.\n`;
  }
  const byField = {};
  for (const c of d.comparisons || []) byField[c.field] = c;
  const lines = defectFields.map((f) => {
    const c = byField[f];
    const label = (c && c.label) || fieldLabel(f);
    const fix = corrections[f];
    if (fix && fix.mode === "amend") {
      return `- ${label} — BL must be amended to: ${fix.bl}`;
    }
    const siVal = (fix && fix.si) || (c && c.si && c.si.value) || "(missing)";
    const blVal = (fix && fix.bl) || (c && c.bl && c.bl.value) || "(missing)";
    return `- ${label} — SI: ${siVal} / BL: ${blVal}`;
  });
  const n = defectFields.length;
  return `Hi ${name},\n\nWe found ${n} discrepanc${n === 1 ? "y" : "ies"} in the draft BL for ${ref}:\n\n${lines.join("\n")}\n\nPlease amend and resend the draft.\n`;
}

async function load() {
  const r = await fetch("public/data.json");
  if (!r.ok) throw new Error("could not load results");
  return r.json();
}

function tabOf(row, source = SRC) {
  const eff = effectiveFor(source, row);
  if (eff.status === "MISMATCH") return "mismatch";
  if (eff.status === "NEEDS_REVIEW") return "needs_review";
  if (row.category === "BL_COMPARISON") return "cleared";
  return "other";
}

const REASON = {
  missing_attachment: "SI or BL not attached",
  wrong_doc_type: "not an SI/BL pair",
  unreadable: "document could not be read",
  missing_value: "a field was blank",
  unclassified: "unclear what the email asks",
  human_feedback: "flagged by a reviewer",
};

/* core/ speaks machine category codes; a clerk should never see one. Used
   wherever a case's category is shown as its own word or two, never inside
   a longer sentence that already reads naturally with the raw code. NOT
   used in the CSV export's explanation column (buildDiscrepancyRows/
   buildFullResultsRows) — that stays a literal, greppable value. */
const CATEGORY_WORDS = {
  BL_COMPARISON: "SI/BL check",
  SI_REQUEST: "SI request",
  INVOICE_QUERY: "invoice question",
  GENERAL: "general email",
  SPAM: "spam",
};
function categoryWord(cat) {
  return CATEGORY_WORDS[cat] || String(cat || "").replace(/_/g, " ").toLowerCase();
}

/* Case-insensitive substring search across reference, subject, sender and
   the defect fields — including the human-readable form ("notify party")
   so a clerk never has to type the machine name ("notify_party"). */
function matchesQuery(r, q) {
  if (!q) return true;
  const hay = [r.reference, r.subject, r.from].filter(Boolean).join(" • ").toLowerCase();
  if (hay.includes(q)) return true;
  const fields = r.defect_fields || [];
  return fields.some((f) => f.toLowerCase().includes(q) || f.replace(/_/g, " ").toLowerCase().includes(q));
}

function renderBoard() {
  const list = $("#case-list");
  list.replaceChildren();
  const q = QUERY.trim().toLowerCase();
  const rows = activeBoard().filter((r) => tabOf(r) === TAB && matchesQuery(r, q));

  if (!rows.length) { list.append(el("div", "empty", q ? "No matches in this tab." : "Nothing in this tab.")); return; }

  for (const r of rows.slice(0, 200)) {
    const a = el("a", "case");
    const rowReview = getReview(SRC, r.email_id);
    // A "problem found" on a needs-a-human case is not a clean check —
    // ClearDraft never rendered a verdict there for the clerk to rubber
    // stamp, so it must not look like a routine "Done" row.
    const problemFound = Boolean(rowReview && rowReview.verdict === "done" && rowReview.note && rowReview.note.startsWith("problem:"));
    if (isReviewed(SRC, r.email_id) && !problemFound) a.classList.add("case-done");
    a.href = `#/case/${r.email_id}`;
    a.setAttribute("role", "listitem");
    a.append(el("div", "case-ref", r.reference));
    a.append(el("div", "case-subject", r.subject));

    const tags = el("div", "case-fields");
    if (rowReview) {
      const chipCls = rowReview.verdict === "confirmed" ? "chip-confirmed" : rowReview.verdict === "flagged" || problemFound ? "chip-flagged" : "chip-done";
      const chipTxt = rowReview.verdict === "confirmed" ? "Confirmed" : rowReview.verdict === "flagged" ? "Marked wrong" : problemFound ? "Problem found" : "Done";
      tags.append(el("span", `chip ${chipCls}`, chipTxt));
    }
    const eff = effectiveFor(SRC, r);
    if (eff.status === "MISMATCH") {
      for (const f of eff.defect_fields) tags.append(el("span", "chip chip-mismatch", f.replace(/_/g, " ")));
    } else if (eff.status === "NEEDS_REVIEW") {
      tags.append(el("span", "chip chip-review", REASON[eff.review_reason] || "needs a human"));
    } else if (r.category === "BL_COMPARISON") {
      tags.append(el("span", "chip chip-match", "All match"));
    } else {
      tags.append(el("span", "chip chip-quiet", categoryWord(r.category)));
    }
    if (eff.changed) {
      const originalCount = (r.defect_fields || []).length;
      const clearedCount = originalCount - eff.defect_fields.length;
      if (eff.status !== "MISMATCH" && r.status === "MISMATCH") {
        tags.append(el("span", "chip chip-quiet chip-marked-same", "Cleared by you"));
      } else if (clearedCount > 0) {
        tags.append(el("span", "chip chip-quiet chip-marked-same", `${clearedCount} of ${originalCount} cleared by you`));
      }
    }
    a.append(tags);
    list.append(a);
  }

  if (rows.length > 200) list.append(el("div", "empty", `Showing the first 200 of ${rows.length}.`));

  if (ACCOUNT.user && PAIRS.length && !BOARD_BATCH_TRIED[SRC]) {
    evaluateBoardBatch(SRC).then((changed) => {
      if (changed && location.hash.startsWith("#/board")) { updateTabCounts(); renderBoard(); renderTriageBanner(); }
    });
  }
}

function setTab(t) {
  TAB = t;
  for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === t));
  renderBoard();
}

/* Tab counts track the current search too, so a clerk can see at a glance
   which tabs hold a match without opening each one. Always computed from
   the active source's rows — Your mail and the sample inbox each get their
   own counts, never DATA.counts (that number is fixed to the sample run). */
function updateTabCounts() {
  const q = QUERY.trim().toLowerCase();
  const rows = activeBoard();
  const totals = { mismatch: 0, needs_review: 0, cleared: 0, other: 0 };
  for (const r of rows) totals[tabOf(r)]++;
  for (const k of Object.keys(totals)) {
    const n = $(`#n-${k}`);
    if (!n) continue;
    n.textContent = q ? rows.filter((r) => tabOf(r) === k && matchesQuery(r, q)).length : totals[k];

    /* "k left" under the count — search never affects it, so it always
       reads against the whole tab. Shown only once the tab has at least
       one reviewed row (an untouched tab gains nothing from repeating its
       own count back as "N left"); a fully finished tab still shows
       "0 left", which is exactly the satisfying part. Placed after the
       label (.tab-l), not between it and the count, so a screen reader
       says "46, Discrepancy found, 12 left" rather than splitting the
       number from its label. */
    const tabRows = rows.filter((r) => tabOf(r) === k);
    const reviewedInTab = tabRows.filter((r) => isReviewed(SRC, r.email_id)).length;
    let leftSpan = n.parentElement && n.parentElement.querySelector(".tab-left");
    if (reviewedInTab > 0) {
      if (!leftSpan) {
        leftSpan = el("span", "tab-left");
        n.parentElement.append(leftSpan);
      }
      leftSpan.textContent = `${tabRows.length - reviewedInTab} left`;
    } else if (leftSpan) {
      leftSpan.remove();
    }
  }
}

function initSearch() {
  const input = $("#inbox-search");
  if (!input) return;
  input.addEventListener("input", () => {
    QUERY = input.value;
    updateTabCounts();
    renderBoard();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && input.value) {
      e.stopPropagation();
      input.value = "";
      QUERY = "";
      updateTabCounts();
      renderBoard();
    }
  });

  addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    const a = document.activeElement;
    const tag = a && a.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (a && a.isContentEditable)) return;
    e.preventDefault();
    if (location.hash !== "#/board") location.hash = "#/board";
    setTimeout(() => { const s = $("#inbox-search"); if (s) s.focus(); }, 0);
  });
}

/* core/ speaks machine field names; a clerk should never see one. */
const FIELD_WORDS = {
  shipper: "shipper", consignee: "consignee", notify_party: "notify party",
  port_of_loading: "port of loading", port_of_discharge: "port of discharge",
  container_count: "container count", gross_weight_kg: "gross weight",
};
function humanise(text) {
  let out = text;
  for (const [k, v] of Object.entries(FIELD_WORDS)) out = out.split(k).join(v);
  return out;
}

/* Learned equivalences: mirrors core/equivalence.py's EQUIVALENCE_FIELDS.
   Only a mismatch on one of these five text fields can be "marked as
   same" — container_count and gross_weight_kg never get the button, a
   number difference is always a real defect. */
const LEARNABLE_FIELDS = new Set([
  "shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge",
]);

/* The exact capitalised labels seamTable() rows use (from data.json's
   comparison.label), for the five learnable fields only — used wherever a
   pair's field name is shown as its own chip/label (never in prose, where
   FIELD_WORDS's lowercase form reads naturally). */
const LEARNABLE_FIELD_LABELS = {
  shipper: "Shipper", consignee: "Consignee", notify_party: "Notify Party",
  port_of_loading: "Port of Loading", port_of_discharge: "Port of Discharge",
};
function fieldLabel(field) { return LEARNABLE_FIELD_LABELS[field] || FIELD_WORDS[field] || field; }

const VARIANCE_LABELS = {
  punctuation_only: "Punctuation differs",
  suffix_abbreviation: "Possible company-suffix abbreviation",
  token_reorder: "Same words, different order",
  country_suffix: "Possible country suffix",
  ampersand_and: "\"&\" vs \"AND\"",
  port_alias: "Same port under another name - check and mark as same if correct",
  country_variant: "Possible country name/abbreviation variant",
};
function varianceLabel(reason) { return VARIANCE_LABELS[reason] || "Possible formatting variation"; }

/* The case reference (e.g. "5ALT-01226") for a pair's source case id, for
   display — falling back to the raw id only when the case can no longer be
   found (mail cleared, a different account, ...). Always still links to
   #/case/<id>, which works either way. */
function referenceForSource(id) {
  if (!id) return id;
  const d = lookupDetail(id);
  return (d && d.reference) || id;
}

function stateOf(c) {
  if (c.undecidable) return "unknown";
  if (c.matched && c.unit_note) return "converted";
  return c.matched ? "match" : "mismatch";
}

/* Verdict + seam table + reply card, built from any object shaped like a
   DATA.detail[id] entry (status, review_reason, rationale, defect_fields,
   comparisons, reply_draft, subject, from, ...). Shared by the case review
   screen and the live "Check a pair" result, so the two never drift apart. */
function verdictKind(d) {
  return d.status === "MISMATCH" ? "mismatch" : d.status === "NEEDS_REVIEW" ? "review" : "match";
}

/* verdictKind(), but reading the re-counted status when a live evaluation is
   cached for this case (see effectiveFor()/EVAL_CACHE) - the same source
   renderVerdict()/yourPartLine() already read via their `evaluation` param.
   reviewBar() and the keyboard-nav "d" handler only have `d`, not a
   pre-fetched evaluation, so this looks it up the same way renderReview()
   does (findRowById -> EVAL_CACHE[source].get(id)). Falls back to
   verdictKind(d) whenever nothing is cached (signed out, no pairs, or the
   case hasn't been evaluated yet). */
function effectiveKind(d) {
  const found = findRowById(d.email_id);
  const source = found ? found.source : SRC;
  if (!found) return verdictKind(d);
  const effective = effectiveFor(source, found.row);
  return effective.status === "MISMATCH" ? "mismatch" : effective.status === "NEEDS_REVIEW" ? "review" : "match";
}

function renderVerdict(d, host, { reply = true, afterVerdict = null, evaluation = null, rerender = null, source = null } = {}) {
  const effSource = source || (findRowById(d.email_id) || {}).source || SRC;
  const effStatus = evaluation ? evaluation.status : d.status;
  const effDefectFields = evaluation ? evaluation.defect_fields : d.defect_fields;
  const kind = evaluation ? (effStatus === "MISMATCH" ? "mismatch" : effStatus === "NEEDS_REVIEW" ? "review" : "match") : verdictKind(d);
  const icon = { mismatch: "≠", review: "?", match: "✓" }[kind];
  const n = effDefectFields.length;
  const title = {
    mismatch: `${n} discrepanc${n === 1 ? "y" : "ies"} found`,
    review: "Needs a human: check this yourself",
    match: d.comparisons.length ? "No discrepancies found" : "No documents to check",
  }[kind];

  const v = el("div", `verdict verdict-${kind}`);
  v.append(el("div", "verdict-icon", icon));
  const vt = el("div", "verdict-text");
  vt.append(el("strong", null, title));
  /* The checked rationale ("2 of 7 fields differ: consignee, notify party")
     goes stale the moment a covered field is marked as same - recompute it
     from the effective defect fields instead of showing yesterday's count,
     and drop it entirely once nothing differs any more. Only applies to a
     BL_COMPARISON verdict; NEEDS_REVIEW/no-comparison rationale is untouched
     since marking a pair can't fix an unreadable file. */
  let rationaleText = humanise(d.rationale || "");
  if (evaluation && evaluation.changed && d.comparisons.length && (d.status === "MISMATCH" || d.status === "OK")) {
    rationaleText = effDefectFields.length
      ? humanise(`${effDefectFields.length} of ${d.comparisons.length} fields differ: ${effDefectFields.join(", ")}`)
      : "";
  }
  vt.append(document.createTextNode(rationaleText));
  if (evaluation && evaluation.changed) {
    const clearedCount = d.defect_fields.length - effDefectFields.length;
    const subText = clearedCount > 0
      ? `you cleared ${clearedCount}`
      : "updated by you";
    vt.append(el("div", "verdict-live-sub",
      `Found ${d.defect_fields.length} discrepanc${d.defect_fields.length === 1 ? "y" : "ies"} · ${subText}`));
  }
  v.append(vt);
  host.append(v);

  if (afterVerdict) host.append(afterVerdict);

  if (d.comparisons.length) {
    host.append(seamTable(d, { evaluation, rerender, source: effSource }));
    const note = orderOfNote(d);
    if (note) host.append(note);
  }
  if (reply) {
    const useDraft = evaluation && evaluation.changed && evaluation.reply_draft ? evaluation.reply_draft : d.reply_draft;
    host.append(replyCard(d, useDraft, "Reply draft", null, Boolean(evaluation && evaluation.changed && evaluation.reply_draft)));
  }
}

/* "To the order of" vs a plain named consignee: legally, a BL naming a
   party "to the order of" is negotiable, a BL naming the same party
   directly is not. The comparison fields still match (same company), so
   the verdict never changes — this is informational only. */
function orderOfNote(d) {
  for (const c of d.comparisons) {
    const siLabel = c.si && c.si.label;
    const blLabel = c.bl && c.bl.label;
    const siOrder = Boolean(siLabel && /order/i.test(siLabel));
    const blOrder = Boolean(blLabel && /order/i.test(blLabel));
    if (siOrder && !blOrder) return orderOfNoteEl("SI", "BL");
    if (blOrder && !siOrder) return orderOfNoteEl("BL", "SI");
  }
  return null;
}

function orderOfNoteEl(orderSide, plainSide) {
  const note = el("div", "note order-note");
  note.append(document.createTextNode(
    `The ${orderSide} says "To the order of"; the ${plainSide} does not. Not counted as a discrepancy, but it changes the BL type. Check with the shipper.`
  ));
  return note;
}

/* Prints only the view on screen; the @media print rules strip the rest.
   The print-only header is filled on beforeprint, so Ctrl+P gets the same
   header as the button and a previous case's reference never lingers. */
function printButton() {
  const btn = el("button", "link-btn print-btn", "Print / save as PDF");
  btn.type = "button";
  btn.addEventListener("click", () => window.print());
  return btn;
}

/* ── Export ─────────────────────────────────────────────────────
   Client-side only — everything here reads from the already-loaded
   board/detail data (mine, sample or uploaded), builds rows, and triggers a
   Blob download. No request ever leaves the browser. boardFor/detailFor are
   defined earlier, shared with the board view and case routing. */

const CSV_HEADERS = [
  "email_id", "reference", "subject", "from", "category", "status", "review_reason",
  "decided_by", "confidence", "field", "field_result", "si_value", "bl_value",
  "si_source", "bl_source", "explanation", "human_review", "human_note", "reviewed_at",
  "marked_as_same", "marked_reason",
  // "Fix a value": always last, so an existing spreadsheet/import built
  // against the columns above still lines up unchanged.
  "corrected_by_clerk",
];

/* Ensures every candidate row for `source` has a cached evaluation before an
   export reads it, so the Discrepancy report (effective) and the
   marked_as_same/marked_reason columns on the other two exports are never
   built from a half-populated cache. A failed request just leaves those
   two columns blank — exports never fail because of it. */
async function ensureExportEvaluations(source) {
  if (!ACCOUNT.user || !PAIRS.length) return;
  await evaluateBoardBatch(source);
}

/* Which comparison fields are covered by a marked pair for this row, and
   the pair id behind each — from the cached evaluation when one exists. */
function coveredFieldsFor(source, emailId) {
  const ev = EVAL_CACHE[source] && EVAL_CACHE[source].get(emailId);
  return (ev && ev.rows) || {};
}

/* RFC 4180: every cell quoted, internal quotes doubled, CRLF line ends.
   Also neutralises spreadsheet formulas — uploaded mail is untrusted, so a
   subject or value starting with =, +, -, @, tab or CR gets a leading
   single quote before it is ever quoted for the cell. */
function csvCell(value) {
  let s = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  s = s.replace(/"/g, '""');
  return `"${s}"`;
}

function toCsv(rows) {
  const lines = [CSV_HEADERS.map(csvCell).join(",")];
  for (const row of rows) lines.push(CSV_HEADERS.map((h) => csvCell(row[h])).join(","));
  return lines.join("\r\n") + "\r\n";
}

function sourceStr(fv) {
  if (!fv || !fv.source) return "";
  return fv.line_no ? `${fv.source} line ${fv.line_no}` : fv.source;
}

function fieldResultOf(c) {
  if (c.undecidable) return "missing";
  return c.matched ? "match" : "mismatch";
}

/* fieldResultOf(), but a "Fix value" correction (when one exists for this
   row's field) overrides the pipeline's own reading — the same override
   effectiveFor()/EVAL_CACHE apply to the case-level status, applied here at
   field grain for the exports. POST /api/recheck-field's vocabulary
   ("match"/"mismatch"/"undecidable") differs only in spelling "missing" for
   "undecidable", to match fieldResultOf()'s own existing column values. */
function correctedFieldResult(fix) {
  if (!fix) return null;
  return fix.status === "undecidable" ? "missing" : fix.status;
}

function fieldExplanation(c) {
  if (c.undecidable) {
    if (!c.bl || !c.bl.value) return "Not found on the draft BL";
    if (!c.si || !c.si.value) return "Not found on the SI";
    return "Could not be determined";
  }
  if (c.matched) return "All fields match";
  const siVal = (c.si && c.si.value) || "left blank";
  const blVal = (c.bl && c.bl.value) || "left blank";
  const hint = c.variance_reason ? ` Possible formatting variation: ${varianceLabel(c.variance_reason).toLowerCase()}.` : "";
  return `SI says ${siVal}; draft BL says ${blVal}.${hint}`;
}

function fieldExplanationWithCorrection(c, fix) {
  if (!fix) return fieldExplanation(c);
  if (fix.mode === "amend") return `You said the BL must be amended to '${fix.bl}'. Still a discrepancy until it is.`;
  if (fix.status === "match") return `Read wrong, corrected by you — now matches. SI: ${fix.si}; BL: ${fix.bl}.`;
  if (fix.status === "undecidable") return "Read wrong, corrected by you — still could not be determined.";
  return `Read wrong, corrected by you — still differs. SI: ${fix.si}; BL: ${fix.bl}.`;
}

/* "bl consignee: 'X' -> 'Y'" (mode "misread") or "bl consignee must be
   amended to: 'Y'" (mode "amend") — only the side(s) the clerk actually
   changed from what was originally read for a misread correction,
   lowercase field name to match the rest of this column's plain wording
   (fieldExplanation, noComparisonExplanation). */
function correctedByClerkText(source, id, field, c) {
  const fix = source && id ? getCorrection(source, id, field) : null;
  if (!fix) return "";
  const label = FIELD_WORDS[field] || field;
  if (fix.mode === "amend") return `bl ${label} must be amended to: '${fix.bl}'`;
  const origSi = (c && c.si && c.si.value) || "";
  const origBl = (c && c.bl && c.bl.value) || "";
  const parts = [];
  if (fix.si !== origSi) parts.push(`si ${label}: '${origSi}' -> '${fix.si}'`);
  if (fix.bl !== origBl) parts.push(`bl ${label}: '${origBl}' -> '${fix.bl}'`);
  return parts.join("; ");
}

function noComparisonExplanation(row, source) {
  const review = getReview(source, row.email_id);
  if (isProblemReview(review)) return `Flagged by reviewer: ${String(review.note || "").replace(/^problem:\s*/i, "")}`;
  if (row.status === "NEEDS_REVIEW") return REASON[row.review_reason] || "needs a human";
  return `Not a document check (${String(row.category || "").replace(/_/g, " ").toLowerCase()})`;
}

function reviewColumnsFor(source, id) {
  const r = getReview(source, id);
  if (!r) return { human_review: "", human_note: "", reviewed_at: "" };
  return { human_review: r.verdict, human_note: r.note || "", reviewed_at: r.at || "" };
}

function baseExportRow(row, detail, source) {
  const rv = reviewColumnsFor(source, row.email_id);
  const effective = effectiveFor(source, row);
  return {
    email_id: row.email_id,
    reference: row.reference,
    subject: row.subject,
    from: row.from,
    category: row.category,
    status: effective.status,
    review_reason: effective.review_reason || "",
    decided_by: row.decided_by || (detail && detail.decided_by) || "",
    confidence: (detail && typeof detail.confidence === "number") ? Math.round(detail.confidence * 100) : "",
    human_review: rv.human_review,
    human_note: rv.human_note,
    reviewed_at: rv.reviewed_at,
  };
}

function fieldExportRow(row, detail, c, source) {
  const covered = source ? coveredFieldsFor(source, row.email_id) : {};
  const pairId = covered[c.field];
  const pair = pairId ? pairById(pairId) : null;
  const fix = source ? getCorrection(source, row.email_id, c.field) : null;
  return {
    ...baseExportRow(row, detail, source),
    field: c.label,
    field_result: correctedFieldResult(fix) || fieldResultOf(c),
    si_value: (c.si && c.si.value) || "",
    bl_value: (c.bl && c.bl.value) || "",
    si_source: sourceStr(c.si),
    bl_source: sourceStr(c.bl),
    explanation: fieldExplanationWithCorrection(c, fix),
    marked_as_same: pairId ? "yes" : "",
    marked_reason: pair ? (pair.note || "") : "",
    corrected_by_clerk: correctedByClerkText(source, row.email_id, c.field, c),
  };
}

function noComparisonExportRow(row, detail, source) {
  return {
    ...baseExportRow(row, detail, source),
    field: "", field_result: "", si_value: "", bl_value: "", si_source: "", bl_source: "",
    corrected_by_clerk: "",
    explanation: noComparisonExplanation(row, source),
  };
}

/* Effective: a field marked as same by the signed-in clerk, or fixed by a
   "Fix value" correction that now reads "match", is dropped from the
   discrepancy report entirely — that is the point of either one. A
   correction that instead reads "mismatch"/"undecidable" is still a
   discrepancy (and still shown), even if the field the pipeline originally
   read happened to match. The organiser submission (buildSubmissionJson)
   never uses this. */
function buildDiscrepancyRows(source) {
  const rows = [];
  for (const row of boardFor(source)) {
    const detail = detailFor(source, row.email_id);
    const comparisons = (detail && detail.comparisons) || [];
    const covered = coveredFieldsFor(source, row.email_id);
    const problemFeedback = isProblemReview(getReview(source, row.email_id));
    let emitted = false;
    if (comparisons.length) {
      for (const c of comparisons) {
        if (covered[c.field]) continue;
        const fix = getCorrection(source, row.email_id, c.field);
        const isDefect = fix ? fix.status !== "match" : (c.undecidable || !c.matched);
        if (isDefect) {
          rows.push(fieldExportRow(row, detail, c, source));
          emitted = true;
        }
      }
      if (problemFeedback && !emitted) rows.push(noComparisonExportRow(row, detail, source));
    } else if (row.status === "NEEDS_REVIEW" || problemFeedback) {
      rows.push(noComparisonExportRow(row, detail, source));
    }
  }
  return rows;
}

function buildFullResultsRows(source) {
  const rows = [];
  for (const row of boardFor(source)) {
    const detail = detailFor(source, row.email_id);
    const comparisons = (detail && detail.comparisons) || [];
    if (comparisons.length) {
      for (const c of comparisons) rows.push(fieldExportRow(row, detail, c, source));
    } else {
      rows.push(noComparisonExportRow(row, detail, source));
    }
  }
  return rows;
}

/* {category, status, review_reason, defect_fields, has_defect} in exactly
   that key order, matching the organiser's sample_submission format. For the
   uploaded source this is never called — its submission export is the
   server's own `submission` block from POST /api/process-dataset, returned
   verbatim, since that already IS the organiser-format record for a run
   nothing here re-ran or re-scored. */
function buildSubmissionJson(source) {
  if (source === "uploaded") return UPLOADED.submission || {};
  const out = {};
  for (const row of boardFor(source)) {
    out[row.email_id] = {
      category: row.category,
      status: row.status,
      review_reason: row.review_reason,
      defect_fields: row.defect_fields,
      has_defect: row.has_defect,
    };
  }
  return out;
}

function todayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function downloadBlob(content, mime, filename) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadCsv(rows, kindLabel) {
  const csv = `﻿${toCsv(rows)}`;
  downloadBlob(csv, "text/csv;charset=utf-8", `cleardraft-${SRC}-${kindLabel}-${todayStr()}.csv`);
}

function downloadJson(obj, kindLabel) {
  const json = JSON.stringify(obj, null, 2);
  downloadBlob(json, "application/json;charset=utf-8", `cleardraft-${SRC}-${kindLabel}-${todayStr()}.json`);
}

function closeExportMenu() {
  const menu = $("#export-menu");
  const btn = $("#export-btn");
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function initExportMenu() {
  const btn = $("#export-btn");
  const menu = $("#export-menu");
  if (!btn || !menu) return;

  btn.addEventListener("click", () => {
    const willShow = menu.hidden;
    menu.hidden = !willShow;
    btn.setAttribute("aria-expanded", String(willShow));
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) closeExportMenu();
  });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { closeExportMenu(); btn.focus(); }
  });

  const discBtn = $("#export-discrepancy-csv");
  if (discBtn) discBtn.addEventListener("click", async () => {
    closeExportMenu();
    await ensureExportEvaluations(SRC);
    downloadCsv(buildDiscrepancyRows(SRC), "discrepancy-report");
    toast("Discrepancy report downloaded.");
  });
  const fullBtn = $("#export-full-csv");
  if (fullBtn) fullBtn.addEventListener("click", async () => {
    closeExportMenu();
    await ensureExportEvaluations(SRC);
    downloadCsv(buildFullResultsRows(SRC), "full-results");
    toast("Full results downloaded.");
  });
  const subBtn = $("#export-submission-json");
  if (subBtn) subBtn.addEventListener("click", () => {
    closeExportMenu();
    downloadJson(buildSubmissionJson(SRC), "submission");
    toast("Submission file downloaded.");
  });
}

/* "ClearDraft did / Your part" — the compact panel that shows, at a
   glance, what the pipeline already did and what is left for a person.
   Shared wording lives here so the case page and any future caller stay
   in sync. */
function joinPlain(parts) { return parts.join(", "); }

function didLine(d) {
  const parts = ["sorted the email"];
  const hasDocs = attachmentNames(d).length > 0 || (d.documents && (d.documents.si || d.documents.bl));
  if (d.comparisons && d.comparisons.length) {
    if (hasDocs) parts.push("read both documents");
    parts.push(`compared ${d.comparisons.length} field${d.comparisons.length === 1 ? "" : "s"}`);
  }
  if (d.reply_draft) parts.push("drafted the reply");
  return `ClearDraft did: ${joinPlain(parts)}.`;
}

/* Effective, not checked: once a marked pair clears a field, "Your part"
   must stop asking the clerk to chase a difference that no longer counts as
   one. evaluation is optional — every existing (non-live, non-signed-in)
   caller keeps working off the checked d exactly as before. */
function yourPartLine(d, evaluation) {
  const effDefectFields = evaluation ? evaluation.defect_fields : d.defect_fields;
  const effStatus = evaluation ? evaluation.status : d.status;
  const kind = evaluation ? (effStatus === "MISMATCH" ? "mismatch" : effStatus === "NEEDS_REVIEW" ? "review" : "match") : verdictKind(d);

  if (kind === "mismatch") {
    const n = effDefectFields.length;
    return `To do: check the ${n} highlighted field${n === 1 ? "" : "s"}, then send the reply.`;
  }
  if (kind === "review") {
    const reason = REASON[d.review_reason] || "it was not sure";
    return `To do: ClearDraft did not decide (${reason}). Check it yourself.`;
  }
  if (evaluation && evaluation.changed && d.status === "MISMATCH" && effStatus !== "MISMATCH") {
    return "To do: nothing to fix. Check the reply, then send it.";
  }
  if (d.comparisons && d.comparisons.length) {
    return `To do: all ${d.comparisons.length} fields match. Send the confirmation.`;
  }
  const cat = categoryWord(d.category);
  return `To do: no SI/BL check needed (${cat}). Handle as usual.`;
}

function yourPartPanel(d, evaluation) {
  const panel = el("div", "your-part-panel");
  panel.append(el("div", "your-part-did", didLine(d)));
  panel.append(el("div", "your-part-yours", yourPartLine(d, evaluation)));
  return panel;
}

/* ── "Why this verdict?" — case page only ──────────────────────────────
   A closed-by-default panel under the verdict, built ONLY from data
   already in the case detail (d) plus what this browser already tracks
   client-side (effectiveFor()'s own inputs: EVAL_CACHE, CORRECTIONS,
   REVIEWS) — no new backend call, no new field on the record. Plain words
   throughout, never "model", "pipeline" or "gate" (ADR-004/014's own terms,
   fine for the architecture doc, not for a clerk reading their own case). */
function readerLabel(path) {
  const ext = String(path || "").split(".").pop().toLowerCase();
  if (ext === "pdf") return "PDF";
  if (ext === "xlsx" || ext === "xls") return "Excel";
  if (ext === "docx" || ext === "doc") return "Word";
  return "text";
}

function whyStep1(d) {
  const cat = categoryWord(d.category);
  const how = d.decided_by === "model" ? "by AI" : "by a fixed rule";
  let text = `Email sorted as ${cat} — ${how}`;
  if (d.evidence) text += `, evidence "${d.evidence}"`;
  return `${text}.`;
}

/* Whether this case is a BL_COMPARISON email whose intent ISN'T "compare"
   (core/decide.py rung 2's other branch — "please send the draft BL", no
   attachments needed, 91 of the 520 emails are exactly this). Checked
   before the plain category test in both whyStep2 and whyStep5 below, so
   this specific, common case never gets the generic "wasn't an SI/BL
   comparison" wording, which would wrongly suggest the email wasn't even
   about an SI/BL pair. */
function isSendDocCase(d) {
  return d.category === "BL_COMPARISON" && Boolean(d.intent) && d.intent !== "compare";
}

//: Expected kind per document slot (core/decide.py rung 4: si.kind != "SI"
//: or bl.kind != "BL" -> wrong_doc_type), for the "looks like: X" note below.
const DOC_SLOT_EXPECT = {
  si: { kind: "SI", label: "a Shipping Instruction" },
  bl: { kind: "BL", label: "a Bill of Lading" },
};

function whyStep2(d) {
  const docs = d.documents;
  const si = docs && docs.si;
  const bl = docs && docs.bl;
  if (!si && !bl) {
    if (d.review_reason === "missing_attachment") {
      return "Documents found: none — fewer than two files were attached.";
    }
    if (isSendDocCase(d)) {
      return "Documents found: none — not asked for; this email asks to send a document, not compare one.";
    }
    return "Documents found: none — this wasn't an SI/BL comparison.";
  }
  const parts = [];
  for (const [side, doc, label] of [["si", si, "SI"], ["bl", bl, "BL"]]) {
    if (!doc) { parts.push(`${label} not attached`); continue; }
    const name = String(doc.path || "").split("/").pop() || label;
    if (doc.readable === false) {
      parts.push(`${label} ${name} could not be read${doc.error ? ` (${doc.error})` : ""}`);
      continue;
    }
    let entry = `${label} ${name}, reader: ${readerLabel(doc.path)}`;
    const expect = DOC_SLOT_EXPECT[side];
    if (expect && doc.kind && doc.kind !== expect.kind) {
      entry += ` — ${label} slot: not ${expect.label} (looks like: ${String(doc.kind).toLowerCase()})`;
    }
    parts.push(entry);
  }
  return `Documents found: ${parts.join("; ")}.`;
}

function whyStep3(d) {
  const comparisons = d.comparisons || [];
  if (!comparisons.length) return "Fields read: not applicable — no fields to read for this email.";
  let byAi = 0;
  for (const c of comparisons) {
    const aiRead = (c.si && c.si.decided_by === "model") || (c.bl && c.bl.decided_by === "model");
    if (aiRead) byAi++;
  }
  const byRule = comparisons.length - byAi;
  let text = `Fields read: ${byRule} by their label`;
  if (byAi > 0) text += `, ${byAi} read by AI and placement-checked`;
  return `${text}.`;
}

//: These three review reasons are document-level failures — the "seven-field
//: comparison" behind them (if data.json even has one; wrong_doc_type keeps
//: comparing the wrong file against the SI) is not a real read of a BL, so
//: showing its counts would say e.g. "1 match, 6 couldn't read" about a
//: commercial invoice. Skip it and name the actual reason instead.
const WHY_SKIP_REASON_TEXT = {
  wrong_doc_type: "one of the attachments isn't an SI or a BL",
  unreadable: "a document could not be read",
  missing_attachment: "fewer than two documents were attached",
};

function whyStep4(d, evaluation) {
  if (d.status === "NEEDS_REVIEW" && WHY_SKIP_REASON_TEXT[d.review_reason]) {
    return `Compared: skipped — ${WHY_SKIP_REASON_TEXT[d.review_reason]}.`;
  }
  const comparisons = d.comparisons || [];
  if (!comparisons.length) return "Compared: not applicable — nothing to compare.";
  let match = 0, differ = 0, unknown = 0;
  for (const c of comparisons) {
    if (c.undecidable) unknown++;
    else if (c.matched) match++;
    else differ++;
  }
  let text = `Compared ${comparisons.length} fields: ${match} match, ${differ} differ, ${unknown} couldn't read (after units/normalising).`;
  // Raw checked counts above stay put (they are the audit trail); a
  // Mark-as-same pair or a Fix-value correction only adds a second,
  // clearly-labelled live count — it never rewrites the first one. Step 5
  // says the same thing in words ("...after your changes"), so the two
  // never disagree about whether anything actually changed.
  if (evaluation && evaluation.changed) {
    const stillDiffer = (evaluation.defect_fields || []).length;
    text += ` After your changes: ${stillDiffer} still differ.`;
  }
  return text;
}

/* core/decide.py's own ladder (see its module docstring), in plain words,
   picking the rung the case's FINAL status/review_reason actually landed
   on — the live effective one when a signed-in clerk's Mark-as-same
   evaluation changed it, otherwise the checked result. */
const WHY_REASON_TEXT = {
  unclassified: "Nobody could tell what this email wants → Needs a human.",
  missing_attachment: "A comparison was asked for but fewer than two documents were attached → Needs a human.",
  unreadable: "A document could not be read → Needs a human.",
  wrong_doc_type: "One of the attachments isn't an SI or a BL → Needs a human.",
  missing_value: "A field couldn't be read → Needs a human.",
  human_feedback: "A reviewer flagged a problem → Needs a human.",
};

function whyStep5(d, evaluation) {
  const status = evaluation ? evaluation.status : d.status;
  const reason = evaluation ? evaluation.review_reason : d.review_reason;
  // Step 4 already spells out the raw vs. live counts; this just names,
  // in words, that the rule below was applied to the changed picture, so
  // the two steps can never quietly disagree about whether anything moved.
  const suffix = evaluation && evaluation.changed ? " after your changes" : "";
  if (status === "MISMATCH") return `Decision rule used: any field differs → Discrepancy${suffix}.`;
  if (status === "NEEDS_REVIEW") {
    const base = (WHY_REASON_TEXT[reason] || "ClearDraft could not decide → Needs a human.").replace(/\.$/, "");
    return `Decision rule used: ${base}${suffix}.`;
  }
  // core/decide.py rung 2's OTHER branch: a BL_COMPARISON email whose
  // intent isn't "compare" is OK with nothing to compare — checked BEFORE
  // the plain category test below, so this common case (91 of 520 emails)
  // never gets the generic "not an SI/BL check" wording.
  if (isSendDocCase(d)) {
    return `Decision rule used: sorted as an SI/BL email, but it asks to send a document, not to compare — nothing to compare yet${suffix}.`;
  }
  if (d.category !== "BL_COMPARISON" || !(d.comparisons || []).length) {
    return `Decision rule used: not an SI/BL check → no documents to check${suffix}.`;
  }
  return `Decision rule used: every field matched → All clear${suffix}.`;
}

function whyStep6(d, source) {
  const parts = [];
  const covered = source ? coveredFieldsFor(source, d.email_id) : {};
  const coveredFields = Object.keys(covered);
  if (coveredFields.length) {
    parts.push(`marked as same: ${coveredFields.map((f) => FIELD_WORDS[f] || f).join(", ")}`);
  }
  const corrections = source ? getCorrections(source, d.email_id) : null;
  if (corrections && Object.keys(corrections).length) {
    parts.push(`fixed values: ${Object.keys(corrections).map((f) => FIELD_WORDS[f] || f).join(", ")}`);
  }
  const review = source ? getReview(source, d.email_id) : null;
  if (review) {
    if (isProblemReview(review)) {
      const note = String(review.note || "").replace(/^problem:\s*/i, "");
      parts.push(`feedback: something's wrong${note ? ` — "${note}"` : ""}`);
    } else if (review.verdict === "confirmed") {
      parts.push("feedback: looks right, confirmed");
    }
  }
  if (!parts.length) return "Changes by you: none yet.";
  return `Changes by you: ${parts.join("; ")}.`;
}

function whyPanel(d, evaluation, source) {
  const details = document.createElement("details");
  details.className = "why-panel";
  const summary = document.createElement("summary");
  summary.textContent = "Why this verdict?";
  details.append(summary);

  const ol = document.createElement("ol");
  ol.className = "why-steps";
  const steps = [
    whyStep1(d),
    whyStep2(d),
    whyStep3(d),
    whyStep4(d, evaluation),
    whyStep5(d, evaluation),
    whyStep6(d, source),
  ];
  for (const step of steps) ol.append(el("li", null, step));
  details.append(ol);
  return details;
}

/* "Download CSV" on the case page — the exact Full-results rows for just
   this email, built with the same row-builders the inbox export uses so
   the two never disagree. */
function caseCsvButton(d) {
  const btn = el("button", "link-btn case-csv-btn", "Download CSV");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    const found = findRowById(d.email_id);
    const boardRow = found ? found.row : {
      email_id: d.email_id, reference: d.reference, subject: d.subject, from: d.from,
      category: d.category, status: d.status, review_reason: d.review_reason,
      decided_by: d.decided_by, defect_fields: d.defect_fields, has_defect: d.has_defect,
    };
    const source = found ? found.source : SRC;
    if (ACCOUNT.user && PAIRS.length && !EVAL_CACHE[source].has(d.email_id)) {
      await evaluateCaseLive(d.email_id, source, d);
    }
    const comparisons = d.comparisons || [];
    const rows = comparisons.length
      ? comparisons.map((c) => fieldExportRow(boardRow, d, c, source))
      : [noComparisonExportRow(boardRow, d, source)];
    downloadCsv(rows, `case-${d.email_id}`);
    toast("CSV downloaded.");
  });
  return btn;
}

addEventListener("beforeprint", () => {
  const h = location.hash;
  const d = h.startsWith("#/case/") && lookupDetail(h.slice("#/case/".length));
  $("#print-ref").textContent = d ? d.reference : h.startsWith("#/check") ? "Uploaded pair" : "";
  $("#print-time").textContent = `Printed ${new Date().toLocaleString()}`;
});

/* The attachment names for the "Original email" panel. An uploaded case
   carries them directly (contract: attachments: [names]); a sample case
   only has the two documents the pipeline actually opened. */
function attachmentNames(d) {
  if (Array.isArray(d.attachments)) return d.attachments;
  const names = [];
  if (d.documents) {
    for (const side of ["si", "bl"]) {
      const doc = d.documents[side];
      if (doc && doc.path) names.push(String(doc.path).split(/[\\/]/).pop());
    }
  }
  return names;
}

function metaLine(label, value) {
  const s = el("span", "orig-meta-line");
  s.append(el("b", null, label));
  s.append(document.createTextNode(value || "—"));
  return s;
}

/* Every case gets this, collapsed by default: the message as it arrived,
   untouched. A worker should never have to open their own mail client just
   to see what was asked — everything they need is already on this page.
   Built with el()/textContent throughout: subject, from and body can come
   straight from an uploaded .eml, so none of it is trusted as markup. */
function originalEmailDetails(d) {
  const details = el("details", "orig-email");
  if (d.uploaded) details.open = true;
  details.append(el("summary", null, "Original email"));

  const body = el("div", "orig-body");
  const meta = el("div", "orig-meta");
  meta.append(metaLine("From ", d.from));
  meta.append(metaLine("Subject ", d.subject));
  body.append(meta);

  body.append(el("div", "orig-text", d.body || "(no message body)"));

  const names = attachmentNames(d);
  if (names.length) {
    const att = el("div", "orig-attachments");
    att.append(el("b", null, "Attachments: "));
    att.append(document.createTextNode(names.join(", ")));
    body.append(att);
  }

  details.append(body);
  return details;
}

function renderReview(id) {
  const d = lookupDetail(id);
  const host = $("#review-body");
  host.replaceChildren();
  if (!d) { host.append(el("div", "empty", "Not found.")); return; }

  const found = findRowById(id);
  const source = found ? found.source : SRC;
  const machineEvaluation = EVAL_CACHE[source].get(id) || null;
  // effectiveFor() layers any saved "Fix value" corrections on top of
  // machineEvaluation itself (applyCorrections) — corrections are never
  // written into EVAL_CACHE (that would collide with Mark-as-same's own
  // evaluation there), so this works whether or not the clerk is signed in.
  const effective = found ? effectiveFor(source, found.row) : null;
  let evaluation = effective && (machineEvaluation || effective.changed)
    ? { ...(machineEvaluation || {}), status: effective.status, review_reason: effective.review_reason,
        defect_fields: effective.defect_fields, changed: effective.changed }
    : machineEvaluation;
  const corrections = getCorrections(source, id);
  if (corrections && Object.keys(corrections).length && evaluation) {
    evaluation = { ...evaluation, reply_draft: draftCorrectedReply(d, evaluation.defect_fields, evaluation.status, corrections) };
  }
  const rerender = () => { if (location.hash === `#/case/${id}`) renderReview(id); };

  /* Built with el()/textContent, not innerHTML: an uploaded case's subject
     and sender text come straight from the .eml the user dropped in, so
     none of it is trusted as markup, escaped or not. */
  const head = el("div", "case-head");
  head.append(el("div", "ref", d.reference));
  const heading = el("h1", null, d.subject);
  heading.id = "case-heading";
  heading.tabIndex = -1;
  head.append(heading);
  const meta = el("div", "case-meta");
  meta.append(el("span", null, d.from));
  meta.append(el("span", null, categoryWord(d.category)));
  let decidedText = d.decided_by === "model" ? "AI helped decide" : "Decided by fixed rules";
  if (typeof d.confidence === "number") decidedText += ` · ${Math.round(d.confidence * 100)}% sure`;
  meta.append(el("span", null, decidedText));
  head.append(meta);

  if (d.uploaded) {
    const tag = el("div", "uploaded-tag");
    tag.append(el("span", "chip chip-quiet", `Uploaded · ${d.filename || "email"}`));
    if (d.received) tag.append(el("span", "uploaded-received", d.received));
    head.append(tag);
  }
  if (d.skipped_attachments && d.skipped_attachments.length) {
    head.append(el("div", "skipped-note", `Could not open: ${d.skipped_attachments.join(", ")}`));
  }

  host.append(head);
  const caseActions = el("div", "case-actions");
  caseActions.append(printButton());
  caseActions.append(caseCsvButton(d));
  host.append(caseActions);
  host.append(originalEmailDetails(d));

  // yourPartPanel + whyPanel share the same slot renderVerdict already
  // exposes ("afterVerdict" — right after the verdict box, before the
  // seam table), so "Why this verdict?" always lands directly under the
  // verdict on the case page without renderVerdict itself needing to know
  // this panel exists (it is also used, without it, by "Check a pair").
  const afterVerdict = el("div", "case-after-verdict");
  afterVerdict.append(yourPartPanel(d, evaluation));
  afterVerdict.append(whyPanel(d, evaluation, source));
  if (d.recheck) {
    renderVerdict(d, host, { reply: false, afterVerdict, evaluation, rerender, source });
    host.append(recheckCard(d.recheck, evaluation));
    const recheckDraft = evaluation && evaluation.changed && evaluation.recheck_reply_draft ? evaluation.recheck_reply_draft : d.recheck.reply_draft;
    host.append(replyCard(d, recheckDraft, "Follow-up reply draft", d.reply_draft, Boolean(evaluation && evaluation.changed && evaluation.recheck_reply_draft)));
  } else {
    renderVerdict(d, host, { afterVerdict, evaluation, rerender, source });
  }

  host.append(reviewBar(d));

  if (FOCUS_CASE_HEADING) {
    FOCUS_CASE_HEADING = false;
    heading.focus();
  }

  /* Live re-count: only when signed in with at least one saved pair, and
     only once per case (the cache above already returns a hit on repeat
     renders). Re-render whenever the evaluation covers anything at all, not
     only when it *changed* the case's status/defect count — a case the
     pipeline already cleared by applying the pair live (uploaded after the
     pair was learned, or re-checked) still needs its covered rows grouped
     and labelled, even though nothing about its status moved.

     Gated on `!machineEvaluation` (Mark-as-same's OWN cache), never on
     `!evaluation` — a "Fix value" correction alone already makes
     `evaluation` truthy (applyCorrections sets changed:true), and a
     correction on one field must never suppress fetching the Mark-as-same
     evaluation a DIFFERENT field on the same case is still waiting on. */
  if (!machineEvaluation && ACCOUNT.user && PAIRS.length) {
    evaluateCaseLive(id, source, d).then((ev) => {
      if (ev && (ev.changed || (ev.rows && Object.keys(ev.rows).length))) rerender();
    });
  }
}

/* "Mark as same" — teach ClearDraft that this exact SI/BL pair is the same
   <field>, for this account only. Never a window.confirm()/alert()/
   prompt(): the confirm step is inline, right under the row — same
   interaction shape as the review screen's "Something's wrong" flag form. */
const MARK_SAME_REASON_MAX = 280;

function markSameConfirmPanel(c, d, markBtn, onSaved) {
  const panel = el("div", "mark-same-confirm");
  panel.hidden = true;

  /* Signed out but accounts are available: no form, just an explanation and
     a Sign in / Create account link — never a silent hide, per the plan. */
  if (!ACCOUNT.user) {
    const msg = el("div", "mark-same-prompt");
    msg.append(document.createTextNode("Sign in to save these as the same "));
    msg.append(document.createTextNode(`${FIELD_WORDS[c.field] || c.field}. `));
    panel.append(msg);
    const link = el("a", "link-btn", "Sign in or create an account");
    link.href = "#/account";
    panel.append(link);
    return panel;
  }

  const prompt = el("div", "mark-same-prompt");
  prompt.append(document.createTextNode("Treat "));
  prompt.append(el("b", null, (c.si && c.si.value) || ""));
  prompt.append(document.createTextNode(" and "));
  prompt.append(el("b", null, (c.bl && c.bl.value) || ""));
  prompt.append(document.createTextNode(` as the same ${FIELD_WORDS[c.field] || c.field}?`));
  panel.append(prompt);

  const scope = el("div", "mark-same-scope",
    "Only this exact wording, this field, your account. You can undo it.");
  panel.append(scope);

  const reasonId = `mark-same-reason-${(d && d.email_id) || "adhoc"}-${c.field}`.replace(/[^a-zA-Z0-9-]+/g, "-");
  const reasonLabel = el("label", "field-label mark-same-reason-label", "Why are these the same? (optional)");
  reasonLabel.setAttribute("for", reasonId);
  panel.append(reasonLabel);
  const reasonBox = document.createElement("textarea");
  reasonBox.id = reasonId;
  reasonBox.className = "field-input mark-same-reason";
  reasonBox.rows = 2;
  reasonBox.maxLength = MARK_SAME_REASON_MAX;
  if (c.variance_reason) reasonBox.value = varianceLabel(c.variance_reason);
  panel.append(reasonBox);
  const counter = el("div", "mark-same-reason-counter", `${MARK_SAME_REASON_MAX - reasonBox.value.length} characters left`);
  panel.append(counter);
  reasonBox.addEventListener("input", () => {
    counter.textContent = `${MARK_SAME_REASON_MAX - reasonBox.value.length} characters left`;
  });

  const err = el("div", "account-error mark-same-error");
  err.hidden = true;
  err.setAttribute("aria-live", "polite");
  panel.append(err);

  const note = el("div", "note mark-same-saved-note");
  note.hidden = true;
  panel.append(note);

  const actions = el("div", "mark-same-actions");
  const confirmBtn = el("button", "btn btn-primary", "Confirm");
  confirmBtn.type = "button";
  const cancelBtn = el("button", "link-btn", "Cancel");
  cancelBtn.type = "button";
  actions.append(confirmBtn, cancelBtn);
  panel.append(actions);

  cancelBtn.addEventListener("click", () => { panel.hidden = true; if (markBtn) markBtn.setAttribute("aria-expanded", "false"); });

  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      panel.hidden = true;
      if (markBtn) { markBtn.setAttribute("aria-expanded", "false"); markBtn.focus(); }
    }
  });

  confirmBtn.addEventListener("click", async () => {
    confirmBtn.disabled = true;
    err.hidden = true;
    try {
      const r = await fetch("/api/equivalences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          field: c.field,
          si_value: (c.si && c.si.value) || "",
          bl_value: (c.bl && c.bl.value) || "",
          source: (d && d.email_id) || "",
          note: reasonBox.value,
        }),
      });
      let j = {};
      try { j = await r.json(); } catch { /* no body */ }

      if (r.status === 401) {
        err.hidden = false;
        err.textContent = "Please sign in first.";
        return;
      }
      if (!r.ok) {
        err.hidden = false;
        err.textContent = (j && j.detail) || "Could not save this pair.";
        return;
      }

      await loadPairs();

      // .mark-same-actions/.btn/.link-btn all set their own `display`, which
      // overrides the `[hidden]` UA rule (an author style always beats it,
      // regardless of specificity) — so Confirm/Cancel stayed visible and
      // clickable after a successful save. Removing the node sidesteps that
      // entirely instead of fighting the cascade.
      actions.remove();
      reasonBox.remove();
      reasonLabel.remove();
      counter.remove();
      prompt.hidden = true;
      scope.hidden = true;
      note.hidden = false;
      note.textContent = j.already_marked
        ? 'Already saved. See "Marked as same" above.'
        : 'Saved. See "Marked as same" above.';
      if (markBtn) markBtn.disabled = true;
      if (onSaved) onSaved(j.pair);
    } catch {
      err.hidden = false;
      err.textContent = "Could not reach the server. Check your connection and try again.";
    } finally {
      confirmBtn.disabled = false;
    }
  });

  return panel;
}

/* Deletes a saved pair by id and refreshes local state — every caller
   already knows the id (from PAIRS or an evaluation's rows map), so this
   never needs to search for one. */
async function deletePair(id) {
  try {
    const r = await fetch(`/api/equivalences/${encodeURIComponent(id)}`, { method: "DELETE", credentials: "same-origin" });
    if (r.ok) await loadPairs();
    return r.ok;
  } catch {
    return false;
  }
}

/* Which comparison fields are covered by a marked pair, and the pair id
   behind each — merging two sources so the "Marked as same" group and its
   full label (reason/date/source/Undo/Edit) render consistently everywhere
   a covered field can show up:
   1. evaluation.rows, from POST /api/equivalences/evaluate against a saved
      case — present whether or not the evaluation actually *changed* the
      case's status (a case already checked OK because the pipeline applied
      the pair live still has that field's row covered).
   2. comparison.learned + a local PAIRS lookup, for a row that arrived
      already matched via a pair — the live "Check a pair" checker and
      freshly processed mail both apply pairs at check time (server-side
      known_equal) and never call /evaluate at all, so this is the only
      signal available for them. Retires the old learnedChip's inline
      "Matched via a pair you approved" rendering in favour of the one
      shared label. */
function buildCoveredRows(d, evaluation) {
  const covered = { ...((evaluation && evaluation.rows) || {}) };
  for (const c of d.comparisons || []) {
    if (covered[c.field] || !c.learned) continue;
    const match = PAIRS.find((p) => p.field === c.field && (
      (p.a === c.si_norm && p.b === c.bl_norm) || (p.a === c.bl_norm && p.b === c.si_norm)
    ));
    if (match) covered[c.field] = match.id;
  }
  return covered;
}

function formatPairDate(iso) {
  if (!iso) return "";
  const dt = new Date(iso);
  if (isNaN(dt)) return "";
  return dt.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/* The label + Undo/Edit under a row in the "Marked as same" group: who
   marked it, when, from which case, and the reason (or a nudge to add
   one). Undo removes the pair outright; Edit deep-links to #/learned/<id>. */
function markedSameLabel(pairId, onUndo) {
  const wrap = el("div", "marked-same-label");
  const pair = pairById(pairId);
  if (!pair) {
    wrap.append(document.createTextNode("Marked as same by you."));
    return wrap;
  }
  const line = el("div", "marked-same-line");
  const date = formatPairDate(pair.added_at);
  line.append(document.createTextNode(`Marked as same by you${date ? ` · ${date}` : ""}${pair.source ? ` · from ` : ""}`));
  if (pair.source) {
    const srcLink = el("a", null, referenceForSource(pair.source));
    srcLink.href = `#/case/${pair.source}`;
    line.append(srcLink);
  }
  wrap.append(line);

  const reasonLine = el("div", "marked-same-reason");
  if (pair.note) {
    reasonLine.append(document.createTextNode(`"${pair.note}"`));
  } else {
    reasonLine.append(document.createTextNode("No reason given — "));
    const addLink = el("a", null, "add one");
    addLink.href = `#/learned/${pair.id}`;
    reasonLine.append(addLink);
  }
  wrap.append(reasonLine);

  const actions = el("div", "marked-same-actions");
  const undoBtn = el("button", "link-btn", "Undo");
  undoBtn.type = "button";
  undoBtn.addEventListener("click", async () => {
    undoBtn.disabled = true;
    const removed = await deletePair(pair.id);
    if (removed) {
      toast("Removed. This counts as a discrepancy again.");
      if (onUndo) onUndo();
    } else {
      undoBtn.disabled = false;
      toast("Could not remove this pair. Try again.");
    }
  });
  actions.append(undoBtn);
  const editLink = el("a", "link-btn", "Edit");
  editLink.href = `#/learned/${pair.id}`;
  actions.append(editLink);
  wrap.append(actions);

  return wrap;
}

/* "Fix a value" — see fixValueConfirmPanel below for the row button that
   opens this. Two distinct audit lines for the two distinct modes: a
   "misread" correction only reports the side(s) the clerk actually typed
   something different for; an "amend" correction always reports the target
   wording, since the field stays a discrepancy either way. */
function correctionAuditLine(c, fix) {
  if (fix.mode === "amend") {
    return `BL must be amended to '${fix.bl}'`;
  }
  const origSi = (c.si && c.si.value) || "";
  const origBl = (c.bl && c.bl.value) || "";
  const parts = [];
  if (fix.si !== origSi) parts.push(`SI '${origSi}' → '${fix.si}'`);
  if (fix.bl !== origBl) parts.push(`BL '${origBl}' → '${fix.bl}'`);
  if (!parts.length) return "Read wrong — checked again by you, no change to the values.";
  return `Read wrong — corrected by you: ${parts.join("; ")}`;
}

const FIX_VALUE_MAX = 500;

/* "Fix value"'s inline panel — same interaction shape as
   markSameConfirmPanel above (inline, never window.confirm/alert/prompt),
   but never gated on sign-in: a clerk's typed correction always works,
   signed in or not (see CORRECTIONS above).

   Two modes, chosen explicitly (never inferred from what gets typed) —
   the core rule this whole feature answers to is that a correction must
   never make ClearDraft tell a carrier "OK to proceed" while the BL is
   actually still wrong:
     "ClearDraft read it wrong" — the clerk corrects what the SI and/or BL
       actually say, then "Check again" calls the stateless
       POST /api/recheck-field (api/_recheck.py -> core.compare.
       compare_values) — the exact same deterministic rule the pipeline
       used the first time, no AI. May become a match.
     "The BL is wrong — it should say…" — one input for the target
       wording. Always saved as a discrepancy (see the "amend" branch
       below) — no recheck call, because no wording the clerk types here
       can make the document on file correct; only the carrier's actual
       amendment can. */
function fixValueConfirmPanel(c, d, source, fixBtn, onSaved) {
  const panel = el("div", "fix-value-panel");
  panel.hidden = true;

  let mode = "misread";
  const idBase = `fix-${(d && d.email_id) || "adhoc"}-${c.field}`.replace(/[^a-zA-Z0-9-]+/g, "-");

  const modeRow = el("div", "fix-value-mode-row");
  modeRow.setAttribute("role", "group");
  modeRow.setAttribute("aria-label", "What kind of fix is this?");
  const misreadBtn = el("button", "fix-value-mode-btn", "ClearDraft read it wrong");
  misreadBtn.type = "button";
  misreadBtn.setAttribute("aria-pressed", "true");
  const amendBtn = el("button", "fix-value-mode-btn", "The BL is wrong — it should say…");
  amendBtn.type = "button";
  amendBtn.setAttribute("aria-pressed", "false");
  modeRow.append(misreadBtn, amendBtn);
  panel.append(modeRow);

  const fieldsHost = el("div", "fix-value-fields");
  panel.append(fieldsHost);

  let siInput = null;
  let blInput = null;

  function renderFields() {
    fieldsHost.replaceChildren();
    if (mode === "misread") {
      fieldsHost.append(el("div", "fix-value-hint", "Change a value if ClearDraft read the document wrong."));

      const siLabel = el("label", "field-label", "Shipping Instruction (SI) says");
      siLabel.setAttribute("for", `${idBase}-si`);
      fieldsHost.append(siLabel);
      siInput = document.createElement("textarea");
      siInput.id = `${idBase}-si`;
      siInput.className = "field-input fix-value-input";
      siInput.rows = 2;
      siInput.maxLength = FIX_VALUE_MAX;
      siInput.value = (c.si && c.si.value) || "";
      fieldsHost.append(siInput);

      const blLabel = el("label", "field-label", "Draft Bill of Lading (BL) says");
      blLabel.setAttribute("for", `${idBase}-bl`);
      fieldsHost.append(blLabel);
      blInput = document.createElement("textarea");
      blInput.id = `${idBase}-bl`;
      blInput.className = "field-input fix-value-input";
      blInput.rows = 2;
      blInput.maxLength = FIX_VALUE_MAX;
      blInput.value = (c.bl && c.bl.value) || "";
      fieldsHost.append(blInput);
    } else {
      fieldsHost.append(el("div", "fix-value-hint",
        "This asks the carrier to change the BL to exactly this wording. The field stays a discrepancy until they do."));

      const label = el("label", "field-label", "The BL should say");
      label.setAttribute("for", `${idBase}-amend`);
      fieldsHost.append(label);
      blInput = document.createElement("textarea");
      blInput.id = `${idBase}-amend`;
      blInput.className = "field-input fix-value-input";
      blInput.rows = 2;
      blInput.maxLength = FIX_VALUE_MAX;
      blInput.value = (c.bl && c.bl.value) || "";
      fieldsHost.append(blInput);
      siInput = null;
    }
  }
  renderFields();

  function selectMode(next) {
    if (mode === next) return;
    mode = next;
    misreadBtn.setAttribute("aria-pressed", String(mode === "misread"));
    amendBtn.setAttribute("aria-pressed", String(mode === "amend"));
    err.hidden = true;
    renderFields();
    const first = fieldsHost.querySelector("textarea");
    if (first) first.focus();
  }
  misreadBtn.addEventListener("click", () => selectMode("misread"));
  amendBtn.addEventListener("click", () => selectMode("amend"));

  const err = el("div", "account-error fix-value-error");
  err.hidden = true;
  err.setAttribute("aria-live", "polite");
  panel.append(err);

  const actions = el("div", "fix-value-actions");
  const checkBtn = el("button", "btn btn-primary", "Check again");
  checkBtn.type = "button";
  const cancelBtn = el("button", "link-btn", "Cancel");
  cancelBtn.type = "button";
  actions.append(checkBtn, cancelBtn);
  panel.append(actions);

  function close() {
    panel.hidden = true;
    if (fixBtn) fixBtn.setAttribute("aria-expanded", "false");
  }
  cancelBtn.addEventListener("click", close);
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(); if (fixBtn) fixBtn.focus(); }
  });

  checkBtn.addEventListener("click", async () => {
    err.hidden = true;

    if (mode === "amend") {
      const target = (blInput.value || "").trim();
      if (!target) {
        err.hidden = false;
        err.textContent = "Type what the BL should say.";
        return;
      }
      // No recheck call and no compare_values status to trust here: what
      // the BL SHOULD say is the clerk's own judgement, not something
      // core.compare can verify, and the field must stay a discrepancy
      // regardless — the document on file is still wrong until the
      // carrier actually amends it.
      setCorrection(source, d.email_id, c.field, "amend", (c.si && c.si.value) || "", blInput.value, "mismatch", "");
      close();
      if (onSaved) onSaved();
      return;
    }

    checkBtn.disabled = true;
    try {
      const r = await fetch("/api/recheck-field", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ field: c.field, si_value: siInput.value, bl_value: blInput.value }),
      });
      let j = {};
      try { j = await r.json(); } catch { /* no body */ }
      if (!r.ok) {
        err.hidden = false;
        err.textContent = (j && j.detail) || "Could not check this value.";
        return;
      }
      setCorrection(source, d.email_id, c.field, "misread", siInput.value, blInput.value, j.status, j.note);
      close();
      if (onSaved) onSaved();
    } catch {
      err.hidden = false;
      err.textContent = "Could not reach the server. Check your connection and try again.";
    } finally {
      checkBtn.disabled = false;
    }
  });

  return panel;
}

/* The seven-row table. Order: discrepancies first (the clerk's job is to
   find what is wrong), then a "Marked as same" group for rows a signed-in
   clerk has covered with a learned pair (evaluation.rows), then a "Fixed by
   you" group for rows a "Fix value" correction now reads as a match, then
   couldn't-read rows, then ordinary matches. */
function seamTable(d, ctx = {}) {
  const wrap = el("div", "seam-wrap");
  const head = el("div", "seam-head");
  head.append(el("div", null, "Field"), el("div", "h-si", "Shipping Instruction (SI)"), el("div", "h-seam"), el("div", "h-bl", "Draft Bill of Lading (BL)"));
  wrap.append(head);

  const evaluation = ctx.evaluation || null;
  const source = ctx.source || SRC;
  const coveredRows = buildCoveredRows(d, evaluation);
  const corrections = (d && d.email_id) ? (getCorrections(source, d.email_id) || {}) : {};

  const groups = { mismatch: [], covered: [], fixed: [], unknown: [], match: [] };
  for (const c of d.comparisons) {
    const fix = corrections[c.field];
    if (fix) {
      // A correction always wins the grouping for its own field — it is
      // the clerk's most recent word on it, more specific than either the
      // pipeline's own read or an account-wide learned pair.
      if (fix.status === "match") groups.fixed.push(c);
      else if (fix.status === "mismatch") groups.mismatch.push(c);
      else groups.unknown.push(c);
      continue;
    }
    const st = stateOf(c);
    // Covered regardless of state: a genuine mismatch the evaluation
    // resolved, or a row that arrived already matched via a pair applied at
    // check time — both land in the "Marked as same" group, never among
    // ordinary matches.
    // "converted" (same weight, different unit) groups and orders exactly
    // like an ordinary match — it only gets its own row styling via
    // data-state in renderRow, not its own section or ordering.
    const groupKey = st === "converted" ? "match" : st;
    if (coveredRows[c.field]) groups.covered.push(c);
    else groups[groupKey].push(c);
  }

  function renderRow(c, coveredPairId) {
    const fix = corrections[c.field];
    const st = coveredPairId ? "covered"
      : fix ? (fix.status === "match" ? "fixed" : fix.status === "mismatch" ? "mismatch" : "unknown")
      : stateOf(c);
    const row = el("div", "seam-row");
    row.dataset.state = st;

    const label = el("div", "f-label");
    label.append(el("span", null, c.label));
    if (st === "mismatch" && c.variance_reason) {
      label.append(el("span", "variance-hint", varianceLabel(c.variance_reason)));
    }
    row.append(label);
    const actions = el("div", "f-label-actions");

    if (c.unit_note) {
      const kgText = c.unit_kg == null ? "" : ` · ${Number(c.unit_kg).toLocaleString("en-US")} kg`;
      const unitChip = el("span", "chip chip-quiet", "units converted");
      unitChip.title = `${c.unit_note}${kgText}`;
      unitChip.setAttribute("aria-label", `Units converted: ${c.unit_note}${kgText}`);
      actions.append(unitChip);
    }

    for (const side of ["si", "bl"]) {
      const cell = el("div", `f-val f-${side}`);
      const fv = c[side];
      if (!fv || !fv.value) {
        cell.classList.add("empty-val");
        cell.textContent = fv ? "left blank" : "not found";
      } else {
        cell.textContent = fv.value;
      }
      if (side === "si") { row.append(cell); row.append(el("div", "seam")); }
      else row.append(cell);
    }

    wrap.append(row);

    if (coveredPairId) {
      const labelPanel = el("div", "marked-same-panel");
      labelPanel.append(markedSameLabel(coveredPairId, () => { if (ctx.rerender) ctx.rerender(); }));
      wrap.append(labelPanel);
    }

    /* "Fix a value"'s audit line + Undo — the corrected wording itself is
       never shown here, only what changed: the SI/BL cells above stay the
       original values as read (per the plan, "the ORIGINAL source line
       stays visible"). */
    if (fix) {
      const auditPanel = el("div", "fix-value-audit");
      auditPanel.append(el("div", null, correctionAuditLine(c, fix)));
      const undoBtn = el("button", "link-btn", "Undo change");
      undoBtn.type = "button";
      undoBtn.addEventListener("click", () => {
        clearCorrection(source, d.email_id, c.field);
        toast("Change undone. This field is checked as originally read.");
        if (ctx.rerender) ctx.rerender();
      });
      auditPanel.append(undoBtn);
      wrap.append(auditPanel);
    }

    /* "Mark as same": only a mismatch, only a learnable text field, only
       for a signed-in clerk (signed-out visitors still see the button, but
       the panel offers Sign in / Create account instead of a form).
       Numeric-field rows never see the button at all. */
    if (st === "mismatch" && LEARNABLE_FIELDS.has(c.field) && (ACCOUNT.user || ACCOUNT.available)) {
      const markBtn = el("button", "link-btn mark-same-btn", "Mark as same");
      markBtn.type = "button";
      markBtn.setAttribute("aria-expanded", "false");
      const markTipId = `mark-same-tip-${((d && d.email_id) || "adhoc")}-${c.field}`.replace(/[^a-zA-Z0-9-]+/g, "-");
      const markTipText = "These two wordings mean the same thing. Stop flagging them for your account.";
      markBtn.title = markTipText;
      markBtn.setAttribute("aria-describedby", markTipId);
      const markTipDesc = el("span", "visually-hidden", markTipText);
      markTipDesc.id = markTipId;
      actions.append(markTipDesc);
      const panel = markSameConfirmPanel(c, d, markBtn, () => { if (ctx.rerender) ctx.rerender(); });
      markBtn.addEventListener("click", () => {
        panel.hidden = !panel.hidden;
        markBtn.setAttribute("aria-expanded", String(!panel.hidden));
        if (!panel.hidden) {
          const reasonBox = panel.querySelector(".mark-same-reason");
          if (reasonBox) reasonBox.focus();
        }
      });
      actions.append(markBtn);
      wrap.append(panel);
    }

    /* "Fix value": any mismatched or unreadable field (not just the five
       learnable text fields "Mark as same" allows — a wrong container count
       or weight is just as fixable), and needs no account. Only on a saved
       case (d.email_id set) — "Check a pair"'s one-off result has no stable
       id to key a correction against. */
    if ((st === "mismatch" || st === "unknown") && d && d.email_id) {
      const fixBtn = el("button", "link-btn fix-value-btn", "Fix value");
      fixBtn.type = "button";
      fixBtn.setAttribute("aria-expanded", "false");
      const fixPanel = fixValueConfirmPanel(c, d, source, fixBtn, () => {
        if (ctx.rerender) ctx.rerender();
      });
      fixBtn.addEventListener("click", () => {
        fixPanel.hidden = !fixPanel.hidden;
        fixBtn.setAttribute("aria-expanded", String(!fixPanel.hidden));
        if (!fixPanel.hidden) {
          const first = fixPanel.querySelector("textarea");
          if (first) first.focus();
        }
      });
      actions.append(fixBtn);
      wrap.append(fixPanel);
    }

    /* Proof is offered only where it is needed. Seven identical "show the
       source" links down a table is a wall of choices the clerk has to read
       past to find the one row that matters. A row that matched needs no
       defending; a row that differs or could not be read does. A row that
       actually mismatches starts open — that is the one line a tired clerk
       must not have to click to see. */
    const provable = st !== "match" && ((c.si && c.si.raw) || (c.bl && c.bl.raw));
    if (provable) {
      const panel = el("div", "proof");
      panel.hidden = st !== "mismatch";
      for (const side of ["si", "bl"]) {
        const fv = c[side];
        if (!fv || !fv.raw) continue;
        const line = el("div", "proof-line");
        line.append(el("b", null, side.toUpperCase()));
        line.append(document.createTextNode(`  ${fv.raw}`));
        panel.append(line);
        panel.append(el("div", "proof-src", `${fv.source}${fv.line_no ? `, line ${fv.line_no}` : ""}`));
      }
      const btn = el("button", "proof-btn", panel.hidden ? "Show source line" : "Hide source line");
      btn.setAttribute("aria-expanded", String(!panel.hidden));
      btn.addEventListener("click", () => {
        panel.hidden = !panel.hidden;
        btn.setAttribute("aria-expanded", String(!panel.hidden));
        btn.textContent = panel.hidden ? "Show source line" : "Hide source line";
      });
      actions.append(btn);
      wrap.append(panel);
    }

    if (actions.childElementCount) label.append(actions);
  }

  for (const c of groups.mismatch) renderRow(c, null);
  if (groups.covered.length) {
    wrap.append(el("div", "seam-group-label seam-group-marked", `Marked as same (${groups.covered.length})`));
    for (const c of groups.covered) renderRow(c, coveredRows[c.field]);
  }
  if (groups.fixed.length) {
    wrap.append(el("div", "seam-group-label seam-group-fixed", `Fixed by you (${groups.fixed.length})`));
    for (const c of groups.fixed) renderRow(c, null);
  }
  for (const c of groups.unknown) renderRow(c, null);
  for (const c of groups.match) renderRow(c, null);

  return wrap;
}

/* Editable, auto-growing reply. Edits live in EDITS (session-only, keyed by
   email_id + which reply) so navigating away and back keeps them. Primary
   action opens a pre-filled compose window straight in Gmail; ClearDraft
   still never sends anything — the clerk checks it and presses Send. */
function replyCard(d, text, title, earlier, redrafted = false) {
  const which = earlier ? "recheck" : "reply";
  const key = `${d.email_id || "adhoc"}::${which}`;
  const draftText = text || "";
  const startText = EDITS.has(key) ? EDITS.get(key) : draftText;
  const hadEdit = EDITS.has(key);

  const card = el("div", "reply-card");

  const head = el("div", "reply-head");
  head.append(el("h3", null, title));
  const metaRow = el("div", "reply-meta-row");
  metaRow.append(el("span", "reply-note", "Written from the checked fields. No AI used."));
  if (redrafted) {
    if (hadEdit) {
      metaRow.append(el("span", "chip chip-quiet reply-redrafted-chip", 'Your edits kept. Click "Reset to draft" for the updated reply.'));
    } else {
      metaRow.append(el("span", "chip chip-quiet reply-redrafted-chip", "Updated for your changes"));
    }
  }
  const editedChip = el("span", "chip chip-quiet reply-edited-chip", "Edited");
  const resetBtn = el("button", "link-btn reply-reset-btn", "Reset to draft");
  resetBtn.type = "button";
  metaRow.append(editedChip, resetBtn);
  head.append(metaRow);
  card.append(head);

  const fieldId = `reply-text-${key.replace(/[^a-zA-Z0-9]+/g, "-")}`;
  const label = el("label", "visually-hidden", "Reply text");
  label.setAttribute("for", fieldId);
  card.append(label);

  const ta = document.createElement("textarea");
  ta.id = fieldId;
  ta.className = "reply-textarea";
  ta.setAttribute("spellcheck", "true");
  ta.setAttribute("autocapitalize", "sentences");
  ta.setAttribute("autocorrect", "on");
  ta.value = startText;
  card.append(ta);

  const subject = /^re[:_]/i.test(d.subject) ? d.subject : `RE: ${d.subject}`;
  function mailtoHref(t) {
    return `mailto:${encodeURIComponent(d.from || "")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(t)}`;
  }

  function syncEditedUI() {
    const edited = EDITS.has(key) && EDITS.get(key) !== draftText;
    editedChip.hidden = !edited;
    resetBtn.hidden = !edited;
  }
  syncEditedUI();

  function autoGrow() {
    // A hidden view has zero width, where the text wraps one character per
    // line and scrollHeight is thousands of pixels. Measure only when laid out.
    if (!ta.isConnected || ta.clientWidth === 0) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight + 2}px`;
  }
  // Re-measure whenever the width changes: the view becoming visible, a phone
  // rotating, a window resizing. Height changes do not re-trigger it.
  let lastWidth = 0;
  new ResizeObserver(() => {
    if (ta.clientWidth !== lastWidth) { lastWidth = ta.clientWidth; autoGrow(); }
  }).observe(ta);
  queueMicrotask(autoGrow);
  setTimeout(autoGrow, 0);

  ta.addEventListener("input", () => {
    EDITS.set(key, ta.value);
    syncEditedUI();
    autoGrow();
    otherBtn.href = mailtoHref(ta.value);
  });

  resetBtn.addEventListener("click", () => {
    EDITS.delete(key);
    ta.value = draftText;
    syncEditedUI();
    autoGrow();
    otherBtn.href = mailtoHref(draftText);
    ta.focus();
  });

  if (earlier) {
    const past = el("details", "reply-past");
    past.append(el("summary", null, "Your first reply (already sent)"));
    past.append(el("div", "reply-body", earlier));
    card.append(past);
  }

  const foot = el("div", "reply-foot");

  const gmailBtn = el("button", "btn btn-primary", "Open in Gmail");
  gmailBtn.type = "button";
  gmailBtn.addEventListener("click", async () => {
    REPLY_OPENED.add(d.email_id);
    refreshDoneBarForReply(d);
    const currentText = ta.value;
    const toPart = d.from ? `&to=${encodeURIComponent(d.from)}` : "";
    const url = `https://mail.google.com/mail/?view=cm&fs=1${toPart}&su=${encodeURIComponent(subject)}&body=${encodeURIComponent(currentText)}`;
    if (url.length > 7500) {
      try {
        await navigator.clipboard.writeText(currentText);
      } catch {
        ta.focus();
        ta.select();
      }
      toast("Too long for Gmail. Reply copied. Paste it in.");
      return;
    }
    window.open(url, "_blank", "noopener");
    toast("Gmail opened with your reply. Check it, then press Send.");
  });
  foot.append(gmailBtn);

  const copyBtn = el("button", "btn btn-quiet", "Copy text");
  copyBtn.type = "button";
  copyBtn.addEventListener("click", async () => {
    REPLY_OPENED.add(d.email_id);
    refreshDoneBarForReply(d);
    try {
      await navigator.clipboard.writeText(ta.value);
      toast("Reply copied.");
    } catch {
      ta.focus();
      ta.select();
      toast("Selected - press Ctrl+C to copy.");
    }
  });
  foot.append(copyBtn);

  const otherBtn = el("a", "btn btn-quiet", "Open in my mail app");
  otherBtn.href = mailtoHref(startText);
  otherBtn.addEventListener("click", () => {
    REPLY_OPENED.add(d.email_id);
    refreshDoneBarForReply(d);
    toast("Draft opened in your mail app. Check it, then press Send.");
  });
  foot.append(otherBtn);

  foot.append(el("span", "btn-hint", "ClearDraft never sends mail. You do."));
  card.append(foot);
  return card;
}

/* ── Done — next case ───────────────────────────────────────────
   Which board (Your mail / Sample / Uploaded) a case belongs to, and its
   filtered list order — the exact order renderBoard() shows for that
   source, tab and search query — so "Done — next" / "Skip" walk the list a
   clerk was actually looking at, not some other order. boardFor/findRowById
   are defined earlier, shared with the board view and export. */
function computeFilteredList(source, tab, query) {
  const q = (query || "").trim().toLowerCase();
  return boardFor(source).filter((r) => tabOf(r) === tab && matchesQuery(r, q));
}

/* If the case is part of the list currently on screen (same source, tab and
   search), that is the list to walk. Otherwise (opened another way, e.g.
   from the home page) fall back to the case's own source + tab, no query. */
function getCaseListContext(id) {
  const found = findRowById(id);
  if (!found) return null;
  const { row, source } = found;

  if (SRC === source) {
    /* Stay on the tab the clerk was actually reviewing even when a live
       re-count (Mark as same) has just moved this case out of it (e.g.
       Discrepancy -> Cleared) - do not fall back to the case's new tab.
       The case may no longer be IN this list; advanceCase() handles that
       by walking forward from "before the start" of it. */
    return { source: SRC, tab: TAB, query: QUERY, list: computeFilteredList(SRC, TAB, QUERY) };
  }
  const ownTab = tabOf(row);
  return { source, tab: ownTab, query: "", list: computeFilteredList(source, ownTab, "") };
}

/* The next row, in list order, that has not been reviewed — wrapping
   around once. Returns -1 when every other row (and the current one) is
   already reviewed. */
function nextUnreviewedIndex(list, fromIdx, source) {
  const n = list.length;
  for (let step = 1; step <= n; step++) {
    const idx = (fromIdx + step) % n;
    if (!isReviewed(source, list[idx].email_id)) return idx;
  }
  return -1;
}

/* Setting location.hash directly always queues an async native "hashchange"
   event, on top of our own addEventListener("hashchange", route). Calling
   route() here too, for an immediate render, would make renderReview() run
   a second time right after — rebuilding the case heading and silently
   dropping the focus() call below. history.pushState() moves the address
   bar without firing any event, so this renders exactly once, synchronously,
   in the same tick as the click/keypress that triggered it — and back/
   forward through these entries still fires hashchange as usual, so the
   browser's own history buttons keep working. */
function navigateToCase(id) {
  FOCUS_CASE_HEADING = true;
  history.pushState(null, "", `#/case/${id}`);
  route();
}

function goToBoardTab(source, tab, query, message, action) {
  setSourceKey(source);
  TAB = tab;
  for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  QUERY = query || "";
  const searchInput = $("#inbox-search");
  if (searchInput) searchInput.value = QUERY;
  history.pushState(null, "", "#/board");
  route();
  if (message) toast(message, action);
}

/* undoAction (optional) is the {label, onClick} confirmToast() already put on
   screen for this save. When this call ends up showing its own toast (the
   mismatch → needs-a-human roll-over, or "all reviewed"), that toast replaces
   the confirm one — so the same action is carried over onto it, or Undo
   would quietly vanish the moment the last case in a tab gets confirmed. */
function advanceCase(id, context, { requireUndone, undoAction }) {
  const { list, source, tab, query } = context;
  if (!list.length) { goToBoardTab(source, tab, query, null); return; }
  /* idx is -1 when a live re-count just moved this case out of the active
     tab's list (e.g. Discrepancy -> Cleared via Mark as same). Treat that
     as sitting just before the list rather than bailing out to the board:
     (idx + 1) % list.length and nextUnreviewedIndex(list, idx) both land on
     index 0 first when idx is -1, so "next" walks to the first unreviewed
     row of the tab the clerk was actually on. */
  const idx = list.findIndex((r) => r.email_id === id);

  if (!requireUndone) {
    navigateToCase(list[(idx + 1) % list.length].email_id);
    return;
  }
  const nextIdx = nextUnreviewedIndex(list, idx, source);
  if (nextIdx !== -1) { navigateToCase(list[nextIdx].email_id); return; }

  /* Every discrepancy in this source is reviewed. Rather than dump the
     clerk back on an empty tab, walk them straight into the tab ClearDraft
     could not decide for itself — that queue is the one still owed
     attention, and it is easy to forget once the mismatch pile is clear.
     Only when NONE remain anywhere for this source, though — a case hidden
     by the current search is still an unreviewed case, so an active filter
     must not trigger the roll-over early. */
  if (tab === "mismatch") {
    const anyUnreviewedMismatch = computeFilteredList(source, "mismatch", "").some((r) => !isReviewed(source, r.email_id));
    if (!anyUnreviewedMismatch) {
      const needsReview = computeFilteredList(source, "needs_review", "").find((r) => !isReviewed(source, r.email_id));
      if (needsReview) {
        TAB = "needs_review";
        for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === "needs_review"));
        navigateToCase(needsReview.email_id);
        toast(`All discrepancies done. Now: ${TAB_LABELS.needs_review}.`, undoAction);
        return;
      }
    }
  }
  goToBoardTab(source, tab, query, `All reviewed in ${TAB_LABELS[tab]}.`, undoAction);
}

/* A small, undoable confirmation: every save (button or the D key) tells the
   clerk what just got recorded and offers one click to take it back, so a
   fast click-through never becomes an un-fixable mistake once the page has
   already moved to the next case. Returns the action object so a caller that
   goes on to call advanceCase() can hand the same Undo to whatever toast
   advanceCase shows next (see advanceCase's undoAction param). */
function confirmToast(d, verb, source) {
  const id = d.email_id;
  const ref = d.reference || id;
  const action = { label: "Undo", onClick: () => { clearReview(source, id); navigateToCase(id); } };
  toast(`${verb} ${ref}`, action);
  return action;
}

/* Reopening a reply (Gmail / Copy / Other mail app) after the clerk has
   already started typing a "Something's wrong" / "I found a problem" note
   must not blow that note away — rebuilding the whole bar rebuilds a fresh,
   empty textarea. So while that note form is open, only the bit REPLY_OPENED
   actually changes (the mismatch primary button's wording, plus a small
   marker) gets touched in place; the full rebuild only happens when there is
   no in-progress note to lose. */
function refreshDoneBarForReply(d) {
  const bar = document.querySelector(".done-bar");
  if (!bar) return;
  const noteForm = bar.querySelector(".review-flag-form");
  if (noteForm && !noteForm.hidden) {
    if (effectiveKind(d) === "mismatch") {
      if (!bar.querySelector(".reply-opened-chip")) {
        bar.insertBefore(el("span", "chip chip-quiet reply-opened-chip", "Reply opened"), bar.firstChild);
      }
      const primary = bar.querySelector(".done-bar-actions .btn-primary");
      if (primary) primary.textContent = "I sent it — next case";
    }
    return;
  }
  bar.replaceWith(reviewBar(d));
}

/* The inline "what's wrong" note field, shared by the discrepancy/match
   "Something's wrong" flow and the needs-a-human "I found a problem" flow.
   onSave receives the trimmed, non-empty note. */
function buildReviewNoteForm(id, saveLabel, onSave) {
  const form = el("div", "review-flag-form");
  form.hidden = true;
  const fieldId = `review-note-${id.replace(/[^a-zA-Z0-9]+/g, "-")}`;
  const label = el("label", "field-label", "What's wrong?");
  label.setAttribute("for", fieldId);
  form.append(label);
  const ta = document.createElement("textarea");
  ta.id = fieldId;
  ta.className = "field-input review-flag-textarea";
  ta.rows = 3;
  form.append(ta);
  const err = el("div", "account-error review-flag-error", "");
  err.setAttribute("aria-live", "polite");
  err.hidden = true;
  form.append(err);
  const saveBtn = el("button", "btn btn-primary", saveLabel);
  saveBtn.type = "button";
  saveBtn.addEventListener("click", () => {
    const note = ta.value.trim();
    if (!note) {
      err.hidden = false;
      err.textContent = "Write a short note first.";
      ta.focus();
      return;
    }
    onSave(note);
  });
  form.append(saveBtn);
  return { form, textarea: ta };
}

/* Bottom bar on each case. The wording and the choices on offer depend on
   what ClearDraft itself decided (verdictKind):
   - mismatch: primary "Discrepancy confirmed — next case" — never worded so
     it could be misread as "the BL is fine".
   - match: primary "All clear confirmed — next case".
   - review (needs a human): no "looks right" button at all — ClearDraft
     refused to decide, so the clerk chooses "I checked — documents agree"
     or "I found a problem" instead of rubber-stamping.
   Reviewed: a status line plus "Undo review" / "Next case". Same
   list-walking (getCaseListContext / advanceCase) as before. */
function reviewBar(d) {
  const id = d.email_id;
  const context = getCaseListContext(id);
  // Which source this case belongs to, for namespacing its review key (an
  // uploaded dataset can share ids with the sample inbox) — the on-screen
  // list context already resolved this; fall back to a fresh lookup for a
  // case opened some other way (e.g. directly from #/case/<id>).
  const source = (context && context.source) || (findRowById(id) || {}).source || SRC;
  const review = getReview(source, id);
  const kind = effectiveKind(d);

  const bar = el("div", "done-bar");

  if (review) {
    const status = el("div", "review-status");
    if (review.verdict === "confirmed") {
      status.append(el("span", null, "You confirmed this."));
    } else if (review.verdict === "flagged") {
      status.append(el("span", null, "You said ClearDraft was wrong: "));
      status.append(el("span", "review-status-note", review.note));
    } else {
      status.append(el("span", null, review.note ? "Done: " : "Done."));
      if (review.note) status.append(el("span", "review-status-note", review.note));
    }
    bar.append(status);

    const actions = el("div", "done-bar-actions");
    const undoBtn = el("button", "link-btn", "Undo review");
    undoBtn.type = "button";
    undoBtn.addEventListener("click", () => {
      clearReview(source, id);
      renderReview(id);
    });
    actions.append(undoBtn);

    const nextBtn = el("button", "btn btn-primary", "Next case");
    nextBtn.type = "button";
    nextBtn.addEventListener("click", () => {
      if (!context) return;
      advanceCase(id, context, { requireUndone: false });
    });
    actions.append(nextBtn);
    bar.append(actions);
  } else if (kind === "review") {
    const actions = el("div", "done-bar-actions");

    // A case escalated because the SI or BL was never attached has nothing
    // to "agree" — asking the sender for the missing documents is the real
    // next step, so the button and the saved note both say that instead.
    const missingDocs = d.review_reason === "missing_attachment";
    const checkedBtn = el("button", "btn btn-primary", missingDocs ? "Asked sender for the documents" : "I checked — documents agree");
    checkedBtn.type = "button";
    checkedBtn.addEventListener("click", () => {
      setReview(source, id, "done", missingDocs ? "asked sender for the documents" : "checked by hand: documents agree");
      const undo = confirmToast(d, missingDocs ? "Noted" : "Checked", source);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true, undoAction: undo });
    });
    actions.append(checkedBtn);

    const problemBtn = el("button", "btn btn-quiet", "I found a problem");
    problemBtn.type = "button";
    problemBtn.setAttribute("aria-expanded", "false");
    actions.append(problemBtn);

    bar.append(actions);

    const noteForm = buildReviewNoteForm(id, "Save and next case", (note) => {
      setReview(source, id, "done", `problem: ${note}`);
      const undo = confirmToast(d, "Problem noted", source);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true, undoAction: undo });
    });
    bar.append(noteForm.form);

    problemBtn.addEventListener("click", () => {
      noteForm.form.hidden = !noteForm.form.hidden;
      problemBtn.setAttribute("aria-expanded", String(!noteForm.form.hidden));
      if (!noteForm.form.hidden) noteForm.textarea.focus();
    });
  } else {
    const replySent = kind === "mismatch" && REPLY_OPENED.has(id);
    if (replySent) bar.append(el("span", "chip chip-quiet reply-opened-chip", "Reply opened"));

    const actions = el("div", "done-bar-actions");

    const primaryLabel = replySent
      ? "I sent it — next case"
      : kind === "mismatch" ? "Confirm discrepancy — next" : "Confirm all clear — next";
    const confirmBtn = el("button", "btn btn-primary", primaryLabel);
    confirmBtn.type = "button";
    confirmBtn.addEventListener("click", () => {
      setReview(source, id, "confirmed");
      const undo = confirmToast(d, "Confirmed", source);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true, undoAction: undo });
    });
    actions.append(confirmBtn);

    const wrongBtn = el("button", "btn btn-quiet", "ClearDraft is wrong");
    wrongBtn.type = "button";
    wrongBtn.setAttribute("aria-expanded", "false");
    const wrongTipText = "Use this if ClearDraft's check is wrong, not the BL.";
    wrongBtn.title = wrongTipText;
    wrongBtn.setAttribute("aria-describedby", "review-wrong-tip");
    const wrongTipDesc = el("span", "visually-hidden", wrongTipText);
    wrongTipDesc.id = "review-wrong-tip";
    actions.append(wrongBtn, wrongTipDesc);

    const skipBtn = el("button", "link-btn", "Skip");
    skipBtn.type = "button";
    skipBtn.addEventListener("click", () => {
      if (!context) return;
      advanceCase(id, context, { requireUndone: false });
    });
    actions.append(skipBtn);

    bar.append(actions);

    const noteForm = buildReviewNoteForm(id, "Save and next case", (note) => {
      setReview(source, id, "flagged", note);
      const undo = confirmToast(d, "Marked wrong", source);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true, undoAction: undo });
    });
    bar.append(noteForm.form);

    wrongBtn.addEventListener("click", () => {
      noteForm.form.hidden = !noteForm.form.hidden;
      wrongBtn.setAttribute("aria-expanded", String(!noteForm.form.hidden));
      if (!noteForm.form.hidden) noteForm.textarea.focus();
    });
  }

  if (context) {
    const idx = context.list.findIndex((r) => r.email_id === id);
    /* idx is -1 when "Mark as same" re-counted this case out of the tab the
       clerk is walking. Say so instead of printing "Case 0 of N". */
    bar.append(el("div", "done-bar-pos", idx === -1
      ? `Moved out of ${TAB_LABELS[context.tab]} (${context.list.length} case${context.list.length === 1 ? "" : "s"} there now)`
      : `Case ${idx + 1} of ${context.list.length} in ${TAB_LABELS[context.tab]}`));
  }

  /* On an unreviewed needs-a-human case, D does not confirm anything — it
     just points at the two buttons above — so the hint says only what D
     actually still does there. */
  const hintText = !review && kind === "review" ? "Keys: N = skip" : "Keys: D = confirm, N = skip";
  bar.append(el("div", "done-bar-hint", hintText));
  return bar;
}

/* "n" = Skip to next. "d" confirms — except on an unreviewed needs-a-human
   case, where ClearDraft deliberately did not decide: there, "d" records
   nothing and just tells the clerk to pick an outcome below, so a reflexive
   keypress can never silently clear an escalation. Once that case has been
   reviewed (by either button), "d" behaves like everywhere else and simply
   advances. Ignored while typing anywhere, or with a modifier held, so it
   never fights the reply textarea. */
function initCaseKeyboardNav() {
  addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (!location.hash.startsWith("#/case/")) return;
    const k = e.key.toLowerCase();
    if (k !== "n" && k !== "d") return;
    const a = document.activeElement;
    const tag = a && a.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (a && a.isContentEditable)) return;

    const id = location.hash.slice("#/case/".length);
    const d = lookupDetail(id);
    if (!d) return;
    const context = getCaseListContext(id);
    if (!context) return;
    const source = context.source;
    e.preventDefault();
    if (k === "n") {
      advanceCase(id, context, { requireUndone: false });
      return;
    }
    if (!isReviewed(source, id) && effectiveKind(d) === "review") {
      toast("ClearDraft didn't decide this. Pick a button below.");
      return;
    }
    let undo;
    if (!isReviewed(source, id)) {
      setReview(source, id, "confirmed");
      undo = confirmToast(d, "Confirmed", source);
    }
    advanceCase(id, context, { requireUndone: true, undoAction: undo });
  });
}

/* The second half of the job: the carrier sent a corrected draft. Every field
   is sorted by what the amendment did to it. "Broken by this amendment" goes
   first - it is the one a tired clerk misses, because they only re-read the
   fields they complained about. */
const OUTCOME = {
  newly_broken: ["Newly wrong in this draft", "mismatch"],
  still_wrong:  ["Still wrong", "mismatch"],
  unreadable:   ["Could not be read", "review"],
  fixed:        ["Fixed", "match"],
  ok:           ["Correct, unchanged", "quiet"],
};

function recheckCard(rc, evaluation) {
  const card = el("div", "recheck");
  const head = el("div", "recheck-head");
  head.append(el("h3", null, "New draft BL, checked again"));
  if (rc.demo) head.append(el("span", "chip chip-quiet", "sample document"));
  card.append(head);

  const covered = (evaluation && evaluation.recheck) || {};

  const order = ["newly_broken", "still_wrong", "unreadable", "fixed", "ok"];
  for (const kind of order) {
    const rows = rc.rows.filter((r) => (r.outcome === kind) && !(kind !== "ok" && covered[r.field]));
    // A still_wrong/newly_broken row whose SI↔v2 pair is covered moves to
    // "Correct, unchanged" — labelled "marked as same" — never "Fixed".
    if (kind === "ok") {
      for (const r of rc.rows) {
        if (r.outcome !== "ok" && covered[r.field] && !rows.includes(r)) rows.push(r);
      }
    }
    if (!rows.length) continue;
    const [label, tone] = OUTCOME[kind];
    const grp = el("div", `recheck-group tone-${tone}`);
    grp.append(el("div", "recheck-label", `${label} (${rows.length})`));
    for (const r of rows) {
      const line = el("div", "recheck-row");
      line.append(el("span", "recheck-field", r.label));
      const isMarked = kind === "ok" && covered[r.field];
      if (kind === "ok") {
        line.append(el("span", "recheck-val", r.v2 || ""));
        if (isMarked) line.append(el("span", "chip chip-quiet recheck-marked-chip", "marked as same"));
      } else {
        line.append(el("span", "recheck-val", `${r.v1 ?? "-"}  →  ${r.v2 ?? "-"}`));
        line.append(el("span", "recheck-si", `SI says ${r.si ?? "-"}`));
      }
      grp.append(line);
    }
    card.append(grp);
  }
  return card;
}

/* Single-hue bars. Colour does no identity work here — the row labels carry
   identity — so no categorical palette is needed and none can be misread. */
function bars(data) {
  const top = Math.max(...Object.values(data), 1);
  const frag = document.createDocumentFragment();
  for (const [k, v] of Object.entries(data)) {
    const row = el("div", "bar-row");
    row.append(el("div", "bar-label", k.replace(/_/g, " ")));
    const track = el("div", "bar-track");
    const w = v > 0 ? Math.max((v / top) * 100, 1.5) : 0;
    track.innerHTML = `<svg width="100%" height="16" role="img" aria-label="${esc(k.replace(/_/g, " "))}: ${v}">
      <rect x="0" y="3" width="100%" height="10" rx="2" fill="#EEF1F5"></rect>
      <rect x="0" y="3" width="${w}%" height="10" rx="4" fill="#14395E"><title>${esc(k.replace(/_/g, " "))}: ${v}</title></rect>
    </svg>`;
    row.append(track);
    row.append(el("div", "bar-val", String(v)));
    frag.append(row);
  }
  return frag;
}

/* A link to a reviewed case for the Accuracy page's flagged/problem lists.
   `f.source` is set when the review came from the uploaded dataset (its ids
   can collide with the sample inbox's) — the click handler switches the
   active source first, so #/case/<id> always resolves (and routes) to the
   SAME row this review was actually about, never a same-numbered row in
   whichever source happens to be active right now. Plain href kept as a
   fallback (e.g. opening in a new tab) even though the click handler is
   what actually drives normal navigation. */
function reviewedCaseLink(f) {
  const row = f.source
    ? boardFor(f.source).find((r) => r.email_id === f.id)
    : (findRowById(f.id) || {}).row;
  const a = el("a", null, row ? row.reference : f.id);
  a.href = `#/case/${f.id}`;
  a.addEventListener("click", (e) => {
    e.preventDefault();
    if (f.source) setSourceKey(f.source);
    navigateToCase(f.id);
  });
  return a;
}

/* "Your checks of ClearDraft's answers" — only shown once at least one
   case has been reviewed. Reviews are a single flat store shared across
   every source, so this counts every review regardless of which board it
   came from. */
function renderReviewsPanel(host) {
  const ids = Object.keys(REVIEWS);
  if (!ids.length) return;

  let confirmed = 0, flagged = 0;
  const flaggedList = [];
  const problemsFound = [];
  for (const key of ids) {
    const r = REVIEWS[key];
    // Storage keys are namespaced per source, and an uploaded dataset's
    // per datasetKey (see reviewKey()) — parse that back off here, but
    // remember which source it came from so the link below can still
    // resolve (and route to) the right case.
    const { source, id } = parseReviewKey(key);
    if (r.verdict === "confirmed") confirmed++;
    else if (r.verdict === "flagged") { flagged++; flaggedList.push({ id, source, note: r.note }); }
    else if (r.verdict === "done" && r.note && r.note.startsWith("problem:")) {
      problemsFound.push({ id, source, note: r.note.slice("problem:".length).trim() });
    }
  }
  const total = ids.length;
  const agreementDenom = confirmed + flagged;

  const panel = el("div", "panel reviews-panel");
  panel.append(el("h3", null, ACCOUNT.user
    ? "Your saved checks of ClearDraft's answers"
    : "Your checks of ClearDraft's answers (this browser)"));
  panel.append(el("div", "sub", `You reviewed ${total} case${total === 1 ? "" : "s"}: ${confirmed} right, ${flagged} wrong`));
  panel.append(el("div", "sub", `Today: ${reviewedTodayCount().handled} handled`));
  if (agreementDenom > 0) {
    panel.append(el("div", "reviews-agreement", `ClearDraft was right: ${Math.round((confirmed / agreementDenom) * 100)}%`));
  }
  if (flaggedList.length) {
    const list = el("ul", "reviews-flagged-list");
    for (const f of flaggedList) {
      const li = el("li");
      const a = reviewedCaseLink(f);
      li.append(a);
      li.append(document.createTextNode(` — ${f.note}`));
      list.append(li);
    }
    panel.append(list);
  }

  /* Escalations where the clerk actually found something wrong. These sit
     outside the Agreement % on purpose — ClearDraft never rendered a
     verdict on a needs-a-human case for the clerk to agree or disagree
     with, so they are a record of what the queue turned up, not a miss. */
  if (problemsFound.length) {
    panel.append(el("h4", null, 'Problems you found on "Needs a human" cases'));
    const problemList = el("ul", "reviews-flagged-list");
    for (const f of problemsFound) {
      const li = el("li");
      const a = reviewedCaseLink(f);
      li.append(a);
      li.append(document.createTextNode(` — ${f.note}`));
      problemList.append(li);
    }
    panel.append(problemList);
  }
  host.append(panel);
}

function renderAccuracy() {
  const s = DATA.stats;
  const host = $("#accuracy-body");
  host.replaceChildren();

  renderReviewsPanel(host);

  const grid = el("div", "stat-grid");
  const stats = [
    [s.totals.emails, "emails read", `${s.runtime_seconds}s for the full inbox`],
    [s.totals.compared, "SI/BL pairs checked", `${s.totals.attachments} attachments opened`],
    [s.totals.defects_found, "drafts with a discrepancy", "caught before the BL was issued"],
    [`${Math.round(s.decisions.rule_pct * 100)}%`, "decided without AI", `AI used ${s.model.calls} times`],
  ];
  for (const [n, l, sub] of stats) {
    const c = el("div", "stat");
    c.append(el("div", "stat-n", String(n)));
    c.append(el("div", "stat-l", l));
    c.append(el("div", "stat-sub", sub));
    grid.append(c);
  }
  host.append(grid);

  const two = el("div", "panel-2");
  const p1 = el("div", "panel");
  p1.append(el("h3", null, "What the inbox contained"));
  p1.append(el("div", "sub", "Every message sorted by what it was asking for."));
  p1.append(bars(s.categories));
  two.append(p1);

  const p2 = el("div", "panel");
  p2.append(el("h3", null, "Which fields were wrong"));
  p2.append(el("div", "sub", "Across every discrepancy found."));
  p2.append(bars(s.defect_fields));
  two.append(p2);
  host.append(two);

  const p3 = el("div", "panel");
  p3.append(el("h3", null, "Why it asked a human"));
  p3.append(el("div", "sub", "Cases it would not decide alone."));
  p3.append(bars(s.escalations));
  host.append(p3);

  if (s.challenge) host.append(challengePanel(s.challenge));

  const note = el("div", "note");
  note.innerHTML = `<b>About AI.</b> AI is used only when rules can't find a field. Its answer
    must appear word for word in the document. Used
    <b>${s.model.calls} time${s.model.calls === 1 ? "" : "s"}</b>.`;
  host.append(note);
}

/* The sample inbox is fully covered by rules, so it cannot show what the model
   adds. This panel reports a held-out set written without reference to our
   rules, run twice: rules alone, then rules with the model as fallback. */
function challengePanel(c) {
  const p = el("div", "panel");
  p.append(el("h3", null, "On mail it has never seen"));
  p.append(el("div", "sub", `${c.emails} emails it had never seen, checked with and without AI.`));

  const t = el("div", "cmp");
  const head = el("div", "cmp-row cmp-head");
  for (const h of ["", "Without AI", "With AI"]) head.append(el("div", null, h));
  t.append(head);
  const rows = [
    ["Email sorted correctly", pct(c.category_accuracy.rules_only), pct(c.category_accuracy.with_model)],
    ["Request understood", pct(c.intent_accuracy.rules_only), pct(c.intent_accuracy.with_model)],
  ];
  const fr = c.doc_pairs.reduce((a, d) => a + d.fields_rules, 0);
  const fm = c.doc_pairs.reduce((a, d) => a + d.fields_model, 0);
  const fp = c.doc_pairs.reduce((a, d) => a + d.fields_present, 0);
  rows.push(["Fields found (unusual labels)", `${fr} of ${fp}`, `${fm} of ${fp}`]);
  for (const r of rows) {
    const row = el("div", "cmp-row");
    row.append(el("div", "cmp-l", r[0]), el("div", "cmp-v", r[1]), el("div", "cmp-v cmp-good", r[2]));
    t.append(row);
  }
  p.append(t);

  const facts = el("div", "cmp-facts");
  facts.textContent =
    `AI decided ${c.decided_by_model} emails and got ${c.model_wrong} wrong. ` +
    `${c.gate_rejections} AI answers were thrown out.`;
  p.append(facts);
  return p;
}

/* ── Check a pair ──────────────────────────────────────────────
   Lets a judge upload their own SI/BL and run the real pipeline live,
   via POST /api/check. The result is rendered with the exact same
   renderVerdict() used by the case review screen. */
const CHECK = { si: null, bl: null };

const SAMPLE_SETS = {
  discrepancies: { si: "samples/unseen_SI.txt", bl: "samples/unseen_BL.txt" },
  inbox: { si: "samples/inbox_SI.txt", bl: "samples/inbox_BL.txt" },
};

function checkFilesReady() { return Boolean(CHECK.si && CHECK.bl); }

function updateCheckSubmit() {
  const btn = $("#check-submit");
  if (btn) btn.disabled = !checkFilesReady();
}

function acceptCheckFile(key, zoneId, nameId, file) {
  if (!file) return;
  CHECK[key] = file;
  $(`#${nameId}`).textContent = file.name;
  $(`#${zoneId}`).dataset.filled = "true";
  updateCheckSubmit();
}

function wireDropZone(zoneId, inputId, nameId, key) {
  const zone = $(`#${zoneId}`);
  const input = $(`#${inputId}`);
  input.addEventListener("change", () => acceptCheckFile(key, zoneId, nameId, input.files[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.dataset.drag = "true"; });
  zone.addEventListener("dragleave", () => { zone.dataset.drag = "false"; });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.dataset.drag = "false";
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) acceptCheckFile(key, zoneId, nameId, file);
  });
}

function checkResultHost() { return $("#check-result"); }

function showCheckApiMissing() {
  const host = checkResultHost();
  host.replaceChildren();
  const panel = el("div", "check-error");
  panel.append(el("strong", null, "The checker doesn't work here."));
  panel.append(el("p", null, "Please use the live site."));
  // The start command is only useful to someone running this on their own
  // machine — a visitor on the deployed site has no use for it.
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    const p = el("p", null, "Locally, start it with:");
    panel.append(p);
    panel.append(el("code", null, "python -m uvicorn api.index:app"));
  }
  host.append(panel);
}

function showCheckError(message) {
  const host = checkResultHost();
  host.replaceChildren();
  const panel = el("div", "check-error");
  panel.append(el("strong", null, "Could not check these documents."));
  panel.append(el("p", null, message || "Something went wrong."));
  host.append(panel);
}

function renderCheckResult(data, evaluation = null) {
  const host = checkResultHost();
  host.replaceChildren();

  const secs = typeof data.seconds === "number" ? data.seconds.toFixed(2) : "?";
  const calls = data.model ? data.model.calls : 0;
  host.append(el("div", "check-meta", `Checked in ${secs}s · AI used ${calls} time${calls === 1 ? "" : "s"}`));

  if (data.email_reading) {
    const er = data.email_reading;
    host.append(el("div", "check-meta", `Email type: ${categoryWord(er.category)}`));
  }

  host.append(printButton());

  renderVerdict(data, host, { evaluation, rerender: () => refreshCheckResult(data) });
}

/* Re-renders a "Check a pair" result after Mark as same / Undo changes what
   the account's pairs cover — this is the check page's equivalent of
   renderReview's rerender, so seamTable's onSaved/Undo callbacks actually do
   something instead of leaving the row exactly as it was checked. */
async function refreshCheckResult(data) {
  const scrollY = window.scrollY;
  const evaluation = await evaluateCheckLive(data);
  if (!evaluation && PAIRS.length) {
    // The mark/undo itself already succeeded — only the live re-evaluation
    // failed (network hiccup) — so say so rather than silently rendering as
    // if nothing had happened.
    toast("Saved. Run the check again to see it applied.");
  }
  renderCheckResult(data, evaluation);
  window.scrollTo(0, scrollY);
}

async function runCheck() {
  if (!checkFilesReady()) return;
  const btn = $("#check-submit");
  btn.disabled = true;
  btn.textContent = "Checking…";
  checkResultHost().replaceChildren();

  const fd = new FormData();
  fd.append("si", CHECK.si);
  fd.append("bl", CHECK.bl);
  const subject = $("#check-subject").value.trim();
  const body = $("#check-body").value.trim();
  if (subject) fd.append("subject", subject);
  if (body) fd.append("body", body);

  try {
    const r = await fetch("/api/check", { method: "POST", body: fd });
    if (r.status === 404 || r.status === 405) { showCheckApiMissing(); return; }
    if (r.status === 400) {
      const j = await r.json().catch(() => ({}));
      showCheckError(j.error);
      return;
    }
    if (!r.ok) {
      // The API answered but failed. Saying "not deployed" here would send a
      // judge looking for the wrong problem.
      showCheckError(`The check failed (error ${r.status}). Try again or try a sample.`);
      return;
    }
    const data = await r.json();
    renderCheckResult(data);
  } catch {
    showCheckApiMissing();
  } finally {
    btn.textContent = "Check these documents";
    updateCheckSubmit();
  }
}

async function loadSample(key) {
  const set = SAMPLE_SETS[key];
  if (!set) return;
  try {
    const [siBlob, blBlob] = await Promise.all([
      fetch(set.si).then((r) => { if (!r.ok) throw new Error("sample missing"); return r.blob(); }),
      fetch(set.bl).then((r) => { if (!r.ok) throw new Error("sample missing"); return r.blob(); }),
    ]);
    const siFile = new File([siBlob], set.si.split("/").pop(), { type: siBlob.type || "text/plain" });
    const blFile = new File([blBlob], set.bl.split("/").pop(), { type: blBlob.type || "text/plain" });
    acceptCheckFile("si", "drop-si", "name-si", siFile);
    acceptCheckFile("bl", "drop-bl", "name-bl", blFile);
    $("#sample-choice").hidden = true;
    $("#use-sample").setAttribute("aria-expanded", "false");
    await runCheck();
  } catch {
    showCheckError("Could not load the sample files.");
  }
}

/* ── "Try to fool it" — Check a pair, mode 2 ───────────────────────────
   A second way into the same #/check page, no files: seven rows, one SI
   value and one BL value each, each one rechecked live against
   POST /api/recheck-field on every edit — the exact same deterministic
   core.compare.compare_values() call "Fix a value" already uses
   (fixValueConfirmPanel above), never a second copy of the rule and never
   the model. The point of this panel is that a judge can see, directly, that
   nothing here is AI: every chip and reason line is one HTTP round trip to a
   pure function. */
const FOOL_FIELDS = [
  { field: "shipper", label: "Shipper" },
  { field: "consignee", label: "Consignee" },
  { field: "notify_party", label: "Notify party" },
  { field: "port_of_loading", label: "Port of loading" },
  { field: "port_of_discharge", label: "Port of discharge" },
  { field: "container_count", label: "Container count" },
  { field: "gross_weight_kg", label: "Gross weight" },
];

/* A clean, fully-matching sample pair, taken from data.json's email_001 (a
   cleared BL_COMPARISON case where all 7 fields read identically off both
   documents — see web/public/data.json, detail.email_001.comparisons).
   Hardcoded rather than read live from DATA so this panel works even before
   the board's fetch has resolved, and so a judge always sees the same
   starting point run to run. */
const FOOL_DEFAULTS = {
  shipper: "APRIL FAR EAST (M) SDN BHD",
  consignee: "MOORIM SP CO., LTD",
  notify_party: "UAB NOVAKOPA",
  port_of_loading: "PORT KLANG (WESTPORT), MALAYSIA (MYPKG)",
  port_of_discharge: "CALLAO, PERU (PECLL)",
  container_count: "1 x 40'HC",
  gross_weight_kg: "21,577 KG",
};

/* Each trick fills exactly one row's SI/BL inputs and re-runs the check for
   it immediately (no debounce wait) — chosen so every one demonstrates a
   specific, named rule in core/normalise.py, core/units.py or
   core/variance.py, not a fuzzy guess. */
const FOOL_TRICKS = [
  { label: "22 MT vs 22 KG", field: "gross_weight_kg", si: "22 MT", bl: "22 KG" },
  { label: "22,000 KG vs 22 MT", field: "gross_weight_kg", si: "22,000 KG", bl: "22 MT" },
  { label: "Mixed sizes: 2 x 40HC + 1 x 20GP", field: "container_count", si: "2 x 40HC + 1 x 20GP", bl: "2 x 40HC + 1 x 20GP" },
  { label: "CO., LTD vs COMPANY LIMITED", field: "shipper", si: "Ocean Paper Co Ltd", bl: "OCEAN PAPER COMPANY LIMITED" },
  { label: "& vs AND", field: "shipper", si: "Smith & Sons Trading", bl: "SMITH AND SONS TRADING" },
  { label: "Port Klang vs Port Kelang", field: "port_of_loading", si: "PORT KLANG", bl: "PORT KELANG" },
  { label: "Dubai vs Jebel Ali", field: "port_of_discharge", si: "DUBAI", bl: "JEBEL ALI" },
  { label: "TBA (blank)", field: "notify_party", si: "TBA", bl: "UAB NOVAKOPA" },
  { label: "Swapped name order", field: "consignee", si: "SMITH JOHN TRADING", bl: "JOHN SMITH TRADING" },
];

/* field -> { si, bl, result }. `result` mirrors POST /api/recheck-field's
   own body ({status, si_normalised, bl_normalised, note, hint?}), or null
   before the first check for that row has come back. */
let FOOL_STATE = {};
let FOOL_INITED = false;
const FOOL_INPUTS = {}; // field -> {siInput, blInput, row, chip, reason}
const FOOL_DEBOUNCE_MS = 350;
// ONE debounce timer PER FIELD — a single shared timer would cancel row A's
// pending check the moment row B is edited within the same window, leaving
// row A stuck on "Checking…" forever.
const FOOL_DEBOUNCE_TIMERS = {}; // field -> timer id
// A monotonic per-field sequence number. Every edit or trick click bumps it
// before the request even starts; a response is applied only if the field's
// counter still matches the value captured when THIS request began — so an
// out-of-order network reply (two trick clicks fired in quick succession,
// or an edit that raced a still-in-flight earlier check) can never overwrite
// a row with a stale result.
const FOOL_SEQ = {}; // field -> counter

function nextFoolSeq(field) {
  FOOL_SEQ[field] = (FOOL_SEQ[field] || 0) + 1;
  return FOOL_SEQ[field];
}

function foolResetState() {
  FOOL_STATE = {};
  for (const { field } of FOOL_FIELDS) {
    FOOL_STATE[field] = { si: FOOL_DEFAULTS[field], bl: FOOL_DEFAULTS[field], result: null };
  }
}

/* Overall verdict line, using the SAME precedence core/decide.py actually
   uses (see its module docstring, rungs 5/6): a PROVEN discrepancy outranks
   an unreadable field, because a defect already known to be real is more
   use to a clerk than "we could not check one field". So: any row that
   differs -> "N discrepancies found"; else any row ClearDraft could not
   decide -> "Needs a human"; else "All match". */
function foolVerdictText() {
  const results = FOOL_FIELDS.map(({ field }) => FOOL_STATE[field].result).filter(Boolean);
  if (results.length < FOOL_FIELDS.length) return { kind: "pending", text: "Checking…" };
  const undecidable = results.filter((r) => r.status === "undecidable").length;
  const mismatch = results.filter((r) => r.status === "mismatch").length;
  if (mismatch > 0) {
    return { kind: "mismatch", text: `${mismatch} discrepanc${mismatch === 1 ? "y" : "ies"} found` };
  }
  if (undecidable > 0) {
    return { kind: "review", text: "Needs a human — ClearDraft could not decide every field." };
  }
  return { kind: "match", text: "All match" };
}

function renderFoolVerdict() {
  const host = $("#fool-verdict");
  if (!host) return;
  const { kind, text } = foolVerdictText();
  host.dataset.kind = kind;
  host.textContent = text;
}

/* One row's chip + reason, from a POST /api/recheck-field body. Mirrors
   fieldExplanation()'s wording where it overlaps ("SI says X; draft BL says
   Y"), but this panel's own copy: it reads a live {status, hint} pair
   in-memory, not a saved FieldComparison off a case. */
function foolRowReason(field, si, bl, result) {
  if (!result) return "";
  if (result.status === "match") {
    return result.note ? `Same value once normalised. ${result.note} — unit converted, not a defect.` : "Same value once normalised.";
  }
  if (result.status === "undecidable") {
    if (field === "container_count") return "Equipment couldn't be confirmed on both sides, or the quantity is written ambiguously — a person checks it.";
    return "Could not be read with certainty (blank, or an ambiguous format) — sent to a person rather than guessed at.";
  }
  const siNorm = result.si_normalised == null ? "(blank)" : String(result.si_normalised);
  const blNorm = result.bl_normalised == null ? "(blank)" : String(result.bl_normalised);
  let text = `Normalised: SI "${siNorm}" vs BL "${blNorm}".`;
  if (field === "gross_weight_kg" && result.note) text += ` ${result.note}.`;
  if (result.hint && result.hint.label) text += ` ${result.hint.label}.`;
  return text;
}

function foolChipFor(status) {
  if (status === "match") return { cls: "chip-match", text: "Match" };
  if (status === "undecidable") return { cls: "chip-review", text: "Can't decide (sent to a person)" };
  return { cls: "chip-mismatch", text: "Discrepancy" };
}

function renderFoolRow(field) {
  const refs = FOOL_INPUTS[field];
  if (!refs) return;
  const state = FOOL_STATE[field];
  const result = state.result;
  refs.chip.replaceChildren();
  if (result) {
    const { cls, text } = foolChipFor(result.status);
    refs.chip.className = `chip ${cls}`;
    refs.chip.textContent = text;
    refs.row.dataset.state = result.status === "match" ? "match" : result.status === "undecidable" ? "unknown" : "mismatch";
  } else {
    refs.chip.className = "chip chip-quiet";
    refs.chip.textContent = "Checking…";
    delete refs.row.dataset.state;
  }
  refs.reason.textContent = foolRowReason(field, state.si, state.bl, result);
}

/* The stateless POST /api/recheck-field call itself — same endpoint, same
   core.compare.compare_values() rule, "Fix a value" already trusts
   (fixValueConfirmPanel above). Never raises: a network failure just leaves
   the row showing "Can't decide", which is the honest answer when
   ClearDraft itself could not be reached either. */
async function foolCheckField(field) {
  const state = FOOL_STATE[field];
  // Captured BEFORE the request starts: this call "owns" seq, and only
  // applies its own result if nothing newer (another edit, another trick)
  // has bumped the counter again by the time it comes back.
  const mySeq = nextFoolSeq(field);
  let result;
  try {
    const r = await fetch("/api/recheck-field", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ field, si_value: state.si, bl_value: state.bl }),
    });
    result = r.ok
      ? await r.json()
      : { status: "undecidable", si_normalised: null, bl_normalised: null, note: null, hint: null };
  } catch {
    result = { status: "undecidable", si_normalised: null, bl_normalised: null, note: null, hint: null };
  }
  if (FOOL_SEQ[field] !== mySeq) return; // superseded — a newer check owns this row now
  state.result = result;
  renderFoolRow(field);
  renderFoolVerdict();
}

function foolCheckAll() {
  renderFoolVerdict();
  for (const { field } of FOOL_FIELDS) foolCheckField(field);
}

function buildFoolRow(fieldDef) {
  const { field, label } = fieldDef;
  const row = el("div", "fool-row");
  row.append(el("div", "fool-row-label", label));

  const inputsWrap = el("div", "fool-row-inputs");

  function makeInput(side, tag) {
    const wrap = el("div", "fool-input-wrap");
    const id = `fool-${field}-${side}`;
    const tagEl = el("label", "fool-input-tag", tag);
    tagEl.setAttribute("for", id);
    wrap.append(tagEl);
    const input = document.createElement("input");
    input.type = "text";
    input.id = id;
    input.className = "field-input fool-input";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.maxLength = 500; // matches api/_recheck.py's MAX_VALUE_LENGTH
    input.value = FOOL_STATE[field][side];
    wrap.append(input);
    return input;
  }

  const siInput = makeInput("si", "SI");
  const blInput = makeInput("bl", "BL");
  inputsWrap.append(siInput.parentElement, blInput.parentElement);
  row.append(inputsWrap);

  const resultRow = el("div", "fool-row-result");
  const chip = el("span", "chip chip-quiet", "Checking…");
  resultRow.append(chip);
  row.append(resultRow);
  const reason = el("div", "fool-reason");
  row.append(reason);

  FOOL_INPUTS[field] = { siInput, blInput, row, chip, reason };

  function onEdit() {
    FOOL_STATE[field].si = siInput.value;
    FOOL_STATE[field].bl = blInput.value;
    FOOL_STATE[field].result = null;
    renderFoolRow(field);
    renderFoolVerdict();
    // This field's OWN timer only — editing a different row must never
    // cancel this row's pending check (see FOOL_DEBOUNCE_TIMERS above).
    clearTimeout(FOOL_DEBOUNCE_TIMERS[field]);
    FOOL_DEBOUNCE_TIMERS[field] = setTimeout(() => foolCheckField(field), FOOL_DEBOUNCE_MS);
  }
  siInput.addEventListener("input", onEdit);
  blInput.addEventListener("input", onEdit);

  return row;
}

function applyFoolTrick(trick) {
  FOOL_STATE[trick.field].si = trick.si;
  FOOL_STATE[trick.field].bl = trick.bl;
  const refs = FOOL_INPUTS[trick.field];
  if (refs) { refs.siInput.value = trick.si; refs.blInput.value = trick.bl; }
  // A trick is immediate, not debounced — only this field's own pending
  // timer is cancelled; foolCheckField's own seq guard (not this clear)
  // is what protects against two rapid trick clicks on the same row
  // resolving out of order.
  clearTimeout(FOOL_DEBOUNCE_TIMERS[trick.field]);
  foolCheckField(trick.field);
}

function resetFoolPanel() {
  foolResetState();
  for (const { field } of FOOL_FIELDS) {
    const refs = FOOL_INPUTS[field];
    if (refs) { refs.siInput.value = FOOL_STATE[field].si; refs.blInput.value = FOOL_STATE[field].bl; }
    clearTimeout(FOOL_DEBOUNCE_TIMERS[field]);
  }
  foolCheckAll();
}

function buildFoolTricks() {
  const host = $("#fool-tricks");
  if (!host) return;
  host.replaceChildren();
  for (const trick of FOOL_TRICKS) {
    const btn = el("button", "fool-trick-btn", trick.label);
    btn.type = "button";
    btn.addEventListener("click", () => applyFoolTrick(trick));
    host.append(btn);
  }
  const resetBtn = el("button", "fool-trick-btn fool-reset-btn", "Reset");
  resetBtn.type = "button";
  resetBtn.addEventListener("click", resetFoolPanel);
  host.append(resetBtn);
}

/* Built once, lazily, the first time a clerk actually opens "Type values" —
   never on page load, so a visitor who never touches this panel never
   triggers its 7 initial POST /api/recheck-field calls. */
function ensureFoolPanelBuilt() {
  if (FOOL_INITED) return;
  FOOL_INITED = true;
  foolResetState();
  const rowsHost = $("#fool-rows");
  rowsHost.replaceChildren();
  for (const fieldDef of FOOL_FIELDS) rowsHost.append(buildFoolRow(fieldDef));
  buildFoolTricks();
  foolCheckAll();
}

function setCheckMode(mode) {
  const uploadBtn = $("#check-mode-upload-btn");
  const typeBtn = $("#check-mode-type-btn");
  const uploadPanel = $("#check-mode-upload-panel");
  const typePanel = $("#check-mode-type-panel");
  if (!uploadBtn || !typeBtn || !uploadPanel || !typePanel) return;
  const toType = mode === "type";
  uploadBtn.setAttribute("aria-pressed", String(!toType));
  typeBtn.setAttribute("aria-pressed", String(toType));
  uploadPanel.hidden = toType;
  typePanel.hidden = !toType;
  if (toType) ensureFoolPanelBuilt();
}

function initFoolModeSwitch() {
  const uploadBtn = $("#check-mode-upload-btn");
  const typeBtn = $("#check-mode-type-btn");
  if (!uploadBtn || !typeBtn) return;
  uploadBtn.addEventListener("click", () => setCheckMode("upload"));
  typeBtn.addEventListener("click", () => setCheckMode("type"));
}

function initCheckPage() {
  wireDropZone("drop-si", "file-si", "name-si", "si");
  wireDropZone("drop-bl", "file-bl", "name-bl", "bl");

  $("#check-form").addEventListener("submit", (e) => { e.preventDefault(); runCheck(); });

  const sampleBtn = $("#use-sample");
  const choice = $("#sample-choice");
  sampleBtn.addEventListener("click", () => {
    const willShow = choice.hidden;
    choice.hidden = !willShow;
    sampleBtn.setAttribute("aria-expanded", String(willShow));
  });
  $("#sample-discrepancies").addEventListener("click", () => loadSample("discrepancies"));
  $("#sample-inbox").addEventListener("click", () => loadSample("inbox"));

  initFoolModeSwitch();
}

/* The main menu / home screen. One primary action, two secondary cards, one
   quiet proof line — everything else lives one click away, never crowding
   this screen. Built only from data already loaded; every number is
   optional-guarded so a missing field just drops its clause, never breaks
   the render. */
function renderHome() {
  if (!DATA) return;

  const proof = $("#home-proof");
  if (proof && DATA.stats) {
    const s = DATA.stats;
    const parts = [];
    if (s.totals && typeof s.totals.emails === "number") parts.push(`${s.totals.emails} messages read`);
    if (s.totals && typeof s.totals.defects_found === "number") parts.push(`${s.totals.defects_found} discrepancies caught`);
    if (s.challenge && s.challenge.category_accuracy && typeof s.challenge.category_accuracy.with_model === "number") {
      parts.push(`${pct(s.challenge.category_accuracy.with_model)} of new mail sorted correctly`);
    }
    if (parts.length) proof.textContent = parts.join(" · ");
  }
}

/* ── Inbox view: Your mail vs the sample company inbox vs an uploaded
   dataset ──────────────────────────────────────────────────────
   One board view, up to three data sources. The segmented switch picks the
   source; everything else (tabs, search, counts, the case list) already
   runs on activeBoard(), so the rest of the render just decides which
   chrome to show — the empty "add your first email" state, or the normal
   tabs/search/list. A brand-new visitor never sees the "Uploaded" switch at
   all — it only appears once UPLOADED.name is set (i.e. something has been
   uploaded this session or restored from localStorage). */
function renderBoardView() {
  if (!DATA) return;
  const mineCount = MINE.board.length;
  const sampleCount = DATA.board.length;
  const hasUploaded = Boolean(UPLOADED.name);

  $("#mine-count").textContent = mineCount;
  $("#sample-count").textContent = sampleCount;
  $("#switch-mine").setAttribute("aria-pressed", String(SRC === "mine"));
  $("#switch-sample").setAttribute("aria-pressed", String(SRC === "sample"));

  const uploadedSwitch = $("#switch-uploaded");
  if (uploadedSwitch) {
    uploadedSwitch.hidden = !hasUploaded;
    if (hasUploaded) {
      uploadedSwitch.textContent = `Uploaded: ${UPLOADED.name} (${UPLOADED.board.length})`;
      uploadedSwitch.setAttribute("aria-pressed", String(SRC === "uploaded"));
    }
  }

  const lede = $("#board-lede");
  if (SRC === "mine") {
    lede.textContent = mineCount
      ? `Your mail: ${mineCount} email${mineCount === 1 ? "" : "s"} checked.`
      : "Add emails to check them.";
  } else if (SRC === "uploaded") {
    lede.textContent = `${UPLOADED.board.length} email${UPLOADED.board.length === 1 ? "" : "s"} from "${UPLOADED.name}". Not stored on our server.`;
  } else {
    lede.textContent = `Sample inbox: ${sampleCount} emails, all sorted and checked.`;
  }

  const showEmpty = SRC === "mine" && mineCount === 0;
  $("#board-normal").hidden = showEmpty;
  $("#mine-actions").hidden = SRC !== "mine";
  $("#mine-sample-actions").hidden = !(SRC === "mine" && showEmpty);
  const uploadedActions = $("#uploaded-actions");
  if (uploadedActions) uploadedActions.hidden = SRC !== "uploaded";

  const showAddMail = SRC === "mine" && (showEmpty || ADDMAIL_OPEN);
  if (showAddMail && !ADDMAIL_TAB_TOUCHED) {
    // Most visitors opening this panel for the first time don't have a
    // saved .eml file yet — lead with the tab they can actually use.
    setAddMailTab(showEmpty ? "paste" : "drop");
  }
  $("#addmail-panel").hidden = !showAddMail;
  const addBtn = $("#add-emails-btn");
  if (addBtn) addBtn.setAttribute("aria-expanded", String(ADDMAIL_OPEN));

  const privacy = $("#mine-privacy-line");
  privacy.hidden = SRC !== "mine";
  if (SRC === "mine") {
    privacy.replaceChildren();
    if (ACCOUNT.user) {
      privacy.append(document.createTextNode("Saved in your account. Only you can see it."));
    } else {
      privacy.append(document.createTextNode("Saved in this browser only. "));
      const link = el("a", "link-btn", "Create an account to keep it on any device.");
      link.href = "#/account";
      privacy.append(link);
    }
  }

  if (!showEmpty) {
    updateTabCounts();
    renderBoard();
    renderTriageBanner();
  } else {
    const banner = $("#triage-banner");
    if (banner) { banner.hidden = true; banner.replaceChildren(); }
  }
}

/* Every row still waiting on a person: a discrepancy or a needs-a-human
   case that has not been reviewed yet. This is the whole triage queue —
   "cleared" and "other" never need a clerk's judgement, so they never
   belong here. Order follows the active board, same as renderBoard(). */
function needsYouRows() {
  return activeBoard().filter((r) => {
    const t = tabOf(r);
    return (t === "mismatch" || t === "needs_review") && !isReviewed(SRC, r.email_id);
  });
}

/* How many rows of the active board were reviewed today, by local date —
   and, of those, how many were an actual SI/BL pair comparison
   (category "BL_COMPARISON"), since only a pair comparison is a document
   check a person would otherwise have done by hand. Backs both the
   triage banner's done state and the accuracy page's reviews panel. */
function reviewedTodayCount() {
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  let handled = 0, pairs = 0;
  for (const r of activeBoard()) {
    const rv = REVIEWS[reviewKey(SRC, r.email_id)];
    if (!rv || !rv.at) continue;
    const d = new Date(rv.at);
    if (Number.isNaN(d.getTime())) continue;
    if (`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` !== today) continue;
    handled++;
    if (r.category === "BL_COMPARISON") pairs++;
  }
  return { handled, pairs };
}

/* The first thing a clerk should see: where to start, and how close to
   finished the queue already is. Y (the denominator) is every mismatch or
   needs-a-human case in the active board, reviewed or not, so the
   progress bar reflects the whole queue rather than resetting each time a
   case leaves it. */
function renderTriageBanner() {
  const banner = $("#triage-banner");
  if (!banner) return;
  banner.className = "triage";
  banner.replaceChildren();

  const relevant = activeBoard().filter((r) => { const t = tabOf(r); return t === "mismatch" || t === "needs_review"; });
  const totalY = relevant.length;
  if (totalY === 0) { banner.hidden = true; return; }
  banner.hidden = false;

  const rows = needsYouRows();
  const count = rows.length;

  if (count > 0) {
    const mismatchLeft = rows.filter((r) => tabOf(r) === "mismatch").length;
    const reviewLeft = count - mismatchLeft;
    const handled = totalY - count;

    const headline = el("div", "triage-headline");
    headline.setAttribute("aria-live", "polite");
    headline.append(el("strong", null, `${count} need${count === 1 ? "s" : ""} you now`));
    headline.append(document.createTextNode(
      ` · ${mismatchLeft} discrepanc${mismatchLeft === 1 ? "y" : "ies"} · ${reviewLeft} need${reviewLeft === 1 ? "s" : ""} a human`
    ));
    banner.append(headline);

    const row = el("div", "triage-row");
    const startBtn = el("button", "btn btn-primary triage-start-btn", "Start checking");
    startBtn.type = "button";
    startBtn.addEventListener("click", startQueue);
    row.append(startBtn);

    const progWrap = el("div", "triage-progress-wrap");
    const bar = document.createElement("progress");
    bar.className = "triage-progress";
    bar.max = totalY;
    bar.value = handled;
    bar.setAttribute("aria-labelledby", "triage-progress-label");
    progWrap.append(bar);
    const progLabel = el("span", "triage-progress-label", `${handled} of ${totalY} done`);
    progLabel.id = "triage-progress-label";
    progWrap.append(progLabel);
    row.append(progWrap);
    banner.append(row);
  } else {
    // Every mismatch and needs-a-human case in this board is reviewed.
    // Colour is never the only signal — the state is in the words too.
    banner.classList.add("triage-clear");
    const headline = el("div", "triage-headline", "Nothing needs you right now.");
    headline.setAttribute("aria-live", "polite");
    banner.append(headline);

    const { handled: t, pairs } = reviewedTodayCount();
    let doneText = `You handled ${t} today.`;
    if (pairs > 0) doneText += ` Up to ${pairs * 10} min saved (about 10 min per SI/BL pair).`;
    banner.append(el("div", "triage-done-note", doneText));
  }
}

/* Jump straight into the queue: discrepancies first (they are the ones
   with a concrete answer waiting), needs-a-human only once every
   discrepancy is cleared. Reuses the exact list-walking the case page
   already relies on — this just aims it at the first case instead of
   the next one. */
function startQueue() {
  const rows = needsYouRows();
  if (!rows.length) return;
  const tab = rows.some((r) => tabOf(r) === "mismatch") ? "mismatch" : "needs_review";
  setTab(tab);
  const first = rows.find((r) => tabOf(r) === tab);
  if (first) navigateToCase(first.email_id);
}

function switchSource(s) {
  if (SRC === s) return;
  setSourceKey(s);
  clearUploadPanels();
  ADDMAIL_OPEN = false;
  ADDMAIL_TAB_TOUCHED = false;
  setAddMailTab("drop");
  renderBoardView();
}

/* ── Upload flow ────────────────────────────────────────────────
   One .eml per request (Vercel's body limit), so files upload one at a
   time in sequence rather than all at once. Each success is merged and
   saved immediately, so a batch that fails partway through still keeps
   whatever it already read. */
function clearUploadPanels() {
  $("#upload-panel").hidden = true;
  $("#upload-panel").replaceChildren();
  $("#upload-errors").hidden = true;
  $("#upload-errors").replaceChildren();
  const datasetProgress = $("#dataset-upload-progress");
  if (datasetProgress) { datasetProgress.hidden = true; datasetProgress.textContent = ""; }
  const datasetResult = $("#dataset-upload-result");
  if (datasetResult) { datasetResult.hidden = true; datasetResult.replaceChildren(); }
}

function showUploadApiMissing() {
  const host = $("#upload-panel");
  host.replaceChildren();
  host.hidden = false;
  const panel = el("div", "check-error");
  panel.append(el("strong", null, "Uploading doesn't work here."));
  panel.append(el("p", null, "Please use the live site:"));
  const a = el("a", "check-error-link", "https://cleardraft-one.vercel.app");
  a.href = "https://cleardraft-one.vercel.app";
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  panel.append(a);
  host.append(panel);
}

function showUploadErrors(errors) {
  const host = $("#upload-errors");
  host.replaceChildren();
  if (!errors.length) { host.hidden = true; return; }
  host.hidden = false;
  host.append(el("div", "upload-errors-title", `${errors.length} file${errors.length === 1 ? "" : "s"} could not be read`));
  for (const e of errors) {
    const row = el("div", "upload-error-row");
    row.append(el("b", null, e.name));
    row.append(document.createTextNode(` — ${e.detail}`));
    host.append(row);
  }
}

function setUploadingUI(uploading) {
  UPLOADING = uploading;
  for (const id of [
    "add-emails-btn", "try-samples-btn", "clear-mine-btn", "mail-file-input",
    "dataset-file-input", "remove-uploaded-btn",
  ]) {
    const node = $(`#${id}`);
    if (node) node.disabled = uploading;
  }
}

/* Merge one processed email into Your mail, deduping by email_id — a
   re-upload of the same message (stable "up_" + hash id) replaces the
   earlier result in place rather than appearing twice. */
function mergeMineResult(data) {
  const { board, detail } = data;
  const idx = MINE.board.findIndex((r) => r.email_id === board.email_id);
  const alreadyThere = idx >= 0;
  if (alreadyThere) MINE.board[idx] = board; else MINE.board.unshift(board);
  MINE.detail[board.email_id] = detail;
  return alreadyThere;
}

/* Signed-in mail lives on the server (already saved by the time the
   response comes back); only a signed-out visitor's mail belongs in
   localStorage. Every place that used to call saveMine() unconditionally
   now goes through this so account mail is never written to the browser. */
function persistMineIfLocal() {
  if (!ACCOUNT.user) saveMine();
}

/* Shared by both ways of adding an email — a dropped .eml and a pasted
   email — so the two never drift apart. Throws { missingApi: true } when
   there is no live backend to talk to, or { detail } for a rejected file. */
async function submitProcessEmail(fd) {
  let r;
  try {
    r = await fetch("/api/process-email", { method: "POST", body: fd, credentials: "same-origin" });
  } catch {
    throw { missingApi: true };
  }
  // A static server with no backend answers POST with 404/405, or — as
  // Python's http.server does — 501 Not Implemented. A real API failure is
  // always JSON per the contract; anything else (an HTML error page, an
  // empty body) means there is no live API to talk to, not a rejected file.
  if (r.status === 404 || r.status === 405 || r.status === 501) throw { missingApi: true };
  if (r.status === 413) throw { detail: "file is too large (4 MB max)" };
  if (!r.ok) {
    const ct = r.headers.get("content-type") || "";
    if (!ct.includes("json")) throw { missingApi: true };
    const j = await r.json().catch(() => ({}));
    throw { detail: j.detail || j.error || `HTTP ${r.status}` };
  }
  return r.json();
}

function chooseDocumentCandidates(data) {
  const host = $("#upload-panel");
  const candidates = data.document_candidates || {};
  const groups = [
    { key: "si", title: "Shipping Instruction", items: candidates.si || [] },
    { key: "bl", title: "Draft Bill of Lading", items: candidates.bl || [] },
  ];

  host.replaceChildren();
  host.hidden = false;
  const card = el("section", "document-chooser");
  card.append(el("div", "document-chooser-badge", "Selection required"));
  card.append(el("h3", null, "Choose the documents to compare"));
  card.append(el("p", "document-chooser-help",
    "This email contains more than one possible document. ClearDraft will not guess from attachment order."));

  const picked = { si: "", bl: "" };
  const confirm = el("button", "btn btn-primary", "Compare selected pair");
  confirm.type = "button";
  confirm.disabled = true;
  const refresh = () => { confirm.disabled = !(picked.si && picked.bl); };

  for (const group of groups) {
    const fieldset = document.createElement("fieldset");
    fieldset.className = "document-choice-group";
    const legend = document.createElement("legend");
    legend.textContent = group.title;
    fieldset.append(legend);
    for (const item of group.items) {
      const label = el("label", "document-choice");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = `document-choice-${group.key}`;
      radio.value = item.id;
      const copy = el("span", "document-choice-copy");
      copy.append(el("strong", null, item.name));
      copy.append(el("small", null,
        item.readable ? `${item.kind} · readable` : `${item.kind} · ${item.error || "unreadable"}`));
      label.append(radio, copy);
      fieldset.append(label);
      radio.addEventListener("change", () => {
        picked[group.key] = radio.value;
        refresh();
      });
      if (group.items.length === 1) {
        radio.checked = true;
        picked[group.key] = item.id;
      }
    }
    card.append(fieldset);
  }
  refresh();

  return new Promise((resolve) => {
    const actions = el("div", "document-chooser-actions");
    const cancel = el("button", "btn btn-quiet", "Cancel");
    cancel.type = "button";
    const finish = (value) => {
      host.replaceChildren();
      host.hidden = true;
      resolve(value);
    };
    cancel.addEventListener("click", () => finish(null));
    confirm.addEventListener("click", () => finish({
      selected_si: picked.si,
      selected_bl: picked.bl,
    }));
    actions.append(cancel, confirm);
    card.append(actions);
    host.append(card);
  });
}

async function uploadOne(file, selection = {}) {
  const fd = new FormData();
  fd.append("eml", file);
  if (selection.selected_si) fd.append("selected_si", selection.selected_si);
  if (selection.selected_bl) fd.append("selected_bl", selection.selected_bl);
  return submitProcessEmail(fd);
}

async function uploadFiles(fileList) {
  if (UPLOADING) return;
  const files = Array.from(fileList || []);
  const emlFiles = files.filter((f) => /\.eml$/i.test(f.name) || f.type === "message/rfc822");
  const skipped = files.filter((f) => !emlFiles.includes(f));

  clearUploadPanels();
  if (!emlFiles.length) {
    toast(files.length ? "Only .eml files are supported." : "No files selected.");
    return;
  }

  setUploadingUI(true);
  const progress = $("#upload-progress");
  progress.hidden = false;

  const errors = skipped.map((f) => ({ name: f.name, detail: "not a .eml file" }));
  let successCount = 0;
  let mismatchCount = 0;
  let duplicateCount = 0;

  for (let i = 0; i < emlFiles.length; i++) {
    const file = emlFiles[i];
    progress.textContent = `Reading ${i + 1} of ${emlFiles.length} — ${file.name}`;
    try {
      let data = await uploadOne(file);
      if (data.selection_required) {
        progress.textContent = `Choose documents for ${file.name}`;
        const selection = await chooseDocumentCandidates(data);
        if (!selection) {
          errors.push({ name: file.name, detail: "document selection cancelled" });
          continue;
        }
        progress.textContent = `Checking selected documents — ${file.name}`;
        data = await uploadOne(file, selection);
      }
      const wasDuplicate = mergeMineResult(data);
      persistMineIfLocal();
      successCount++;
      if (wasDuplicate) duplicateCount++;
      if (data.board && data.board.status === "MISMATCH") mismatchCount++;
      renderBoardView();
    } catch (err) {
      if (err && err.missingApi) {
        progress.hidden = true;
        setUploadingUI(false);
        showUploadApiMissing();
        return;
      }
      errors.push({ name: file.name, detail: (err && err.detail) || "could not be processed" });
    }
  }

  progress.hidden = true;
  setUploadingUI(false);
  showUploadErrors(errors);
  renderBoardView();

  if (successCount) {
    let msg = `${successCount} email${successCount === 1 ? "" : "s"} read`;
    if (mismatchCount) msg += ` · ${mismatchCount} with a discrepancy`;
    if (duplicateCount) msg += ` · ${duplicateCount} already in your mail`;
    toast(msg);
  }
}

/* "Try 6 sample emails" runs the exact same upload path against real .eml
   files shipped with the site — this is live processing through the real
   API, not canned data. If the manifest or the files themselves cannot be
   fetched (as on a static preview with no deployed backend), that reads
   the same as the API being unavailable. */
async function trySampleEmails() {
  if (UPLOADING) return;
  clearUploadPanels();

  let manifest;
  try {
    const r = await fetch("samples/eml/manifest.json");
    if (!r.ok) throw new Error("missing");
    manifest = await r.json();
  } catch {
    showUploadApiMissing();
    return;
  }

  const entries = (manifest && manifest.files) || [];
  if (!entries.length) { toast("No sample emails available."); return; }

  const files = [];
  for (const entry of entries) {
    try {
      const r = await fetch(`samples/eml/${entry.name}`);
      if (!r.ok) throw new Error("missing");
      const blob = await r.blob();
      files.push(new File([blob], entry.name, { type: "message/rfc822" }));
    } catch { /* skip a missing sample file, try the rest */ }
  }

  if (!files.length) { showUploadApiMissing(); return; }
  await uploadFiles(files);
}

/* ── Upload a dataset (.zip) ────────────────────────────────────
   POST /api/process-dataset (api/_dataset.py) processes the whole zip live,
   in one request, and returns board/detail/stats/submission shaped exactly
   like MINE/DATA — every existing board feature (tabs, search, case pages,
   review, export) works on the result unchanged because it is read through
   the same boardFor()/detailFor() this whole file already uses. There is no
   incremental progress for a single POST, so the progress line is just an
   elapsed-seconds counter, replaced by a one-line summary the moment the
   response lands. */
let DATASET_PROGRESS_TIMER = null;

function stopDatasetProgressTimer() {
  if (DATASET_PROGRESS_TIMER) { clearInterval(DATASET_PROGRESS_TIMER); DATASET_PROGRESS_TIMER = null; }
}

function startDatasetProgressTimer(label) {
  const host = $("#dataset-upload-progress");
  if (!host) return;
  host.hidden = false;
  const started = Date.now();
  const tick = () => { host.textContent = `${label} — ${((Date.now() - started) / 1000).toFixed(1)}s…`; };
  tick();
  stopDatasetProgressTimer();
  DATASET_PROGRESS_TIMER = setInterval(tick, 200);
}

function datasetSummaryLine(u) {
  const mismatches = u.board.filter((r) => r.status === "MISMATCH").length;
  const needsReview = u.board.filter((r) => r.status === "NEEDS_REVIEW").length;
  const parts = [
    `${u.board.length} email${u.board.length === 1 ? "" : "s"}`,
    `${mismatches} discrepanc${mismatches === 1 ? "y" : "ies"}`,
    `${needsReview} need${needsReview === 1 ? "s" : ""} a human`,
  ];
  if (typeof u.seconds === "number") parts.push(`${u.seconds.toFixed(1)} s`);
  const calls = typeof u.model_calls === "number" ? u.model_calls : 0;
  parts.push(`${calls} AI call${calls === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function renderDatasetResult() {
  const host = $("#dataset-upload-result");
  if (!host) return;
  if (!UPLOADED.name) { host.hidden = true; host.replaceChildren(); return; }
  host.hidden = false;
  host.replaceChildren(el("div", "dataset-upload-summary", datasetSummaryLine(UPLOADED)));
}

async function uploadDataset(file) {
  if (UPLOADING || !file) return;
  if (!/\.zip$/i.test(file.name)) { toast("Only a .zip file is supported."); return; }

  setUploadingUI(true);
  clearUploadPanels();
  const resultHost = $("#dataset-upload-result");
  if (resultHost) { resultHost.hidden = true; resultHost.replaceChildren(); }
  startDatasetProgressTimer(`Processing ${file.name}`);

  const fd = new FormData();
  fd.append("file", file);

  let resp;
  try {
    resp = await fetch("/api/process-dataset", { method: "POST", body: fd, credentials: "same-origin" });
  } catch {
    stopDatasetProgressTimer();
    const progress = $("#dataset-upload-progress");
    if (progress) progress.hidden = true;
    showUploadApiMissing();
    setUploadingUI(false);
    return;
  }

  stopDatasetProgressTimer();
  const progress = $("#dataset-upload-progress");
  if (progress) progress.hidden = true;

  // Same "no live backend" detection as submitProcessEmail() — a static
  // server with no API answers these three the way it answers any
  // unhandled POST, never with our own JSON error shape.
  if (resp.status === 404 || resp.status === 405 || resp.status === 501) {
    showUploadApiMissing();
    setUploadingUI(false);
    return;
  }
  if (!resp.ok) {
    const ct = resp.headers.get("content-type") || "";
    let detail;
    if (ct.includes("json")) {
      // Our own clean {error, detail} shape (api/_dataset.py's _error()),
      // whatever the status - 400 for a rejected zip, 413 for a result
      // too large to return, etc.
      const j = await resp.json().catch(() => ({}));
      detail = j.detail || j.error || null;
    }
    if (!detail && resp.status === 413) {
      // Vercel's own platform limit (4.5 MB) can reject the request before
      // it ever reaches api/_dataset.py, with a plain, non-JSON body - the
      // 4 MB app-level cap should catch this first, but this is the
      // friendly fallback when it doesn't.
      detail = "The zip is too large to upload (4 MB max). Try a smaller file.";
    }
    if (!detail) {
      // Any other failure with a body we can't read as our own error shape
      // (a 500, a proxy error page, ...) - never claim the API is simply
      // missing when it clearly answered, just less specifically than usual.
      detail = `Upload failed (HTTP ${resp.status}). Try again, or use a smaller zip.`;
    }
    showUploadErrors([{ name: file.name, detail }]);
    setUploadingUI(false);
    return;
  }

  const data = await resp.json();
  // A fresh, unpredictable datasetKey per upload (see newDatasetKey()) —
  // together with purging every review namespaced to the PREVIOUS
  // datasetKey, this guarantees a second upload's cases start unreviewed
  // even when their email ids exactly match the first upload's.
  purgeUploadedReviews();
  UPLOADED = {
    name: (data.source && data.source.name) || file.name.replace(/\.zip$/i, ""),
    datasetKey: newDatasetKey(),
    board: data.board || [],
    detail: data.details || {},
    submission: data.submission || {},
    stats: data.stats || null,
    seconds: data.source ? data.source.seconds : null,
    model_calls: data.source ? data.source.model_calls : null,
  };
  const persisted = saveUploaded();

  setSourceKey("uploaded");
  ADDMAIL_OPEN = false;
  renderBoardView();
  renderDatasetResult();

  toast(persisted
    ? `Inbox checked: ${UPLOADED.name}`
    : "Checked, but not saved. Upload again after a refresh.");

  setUploadingUI(false);
}

function initDatasetUpload() {
  const input = $("#dataset-file-input");
  if (input) {
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (file) uploadDataset(file);
      input.value = "";
    });
  }
  const zone = $("#dataset-drop-zone");
  if (zone) {
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.dataset.drag = "true"; });
    zone.addEventListener("dragleave", () => { zone.dataset.drag = "false"; });
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.dataset.drag = "false";
      if (UPLOADING) return;
      const files = e.dataTransfer && e.dataTransfer.files;
      const file = files && files[0];
      if (file) uploadDataset(file);
    });
  }
  const removeBtn = $("#remove-uploaded-btn");
  if (removeBtn) {
    removeBtn.addEventListener("click", () => {
      if (UPLOADING) return;
      if (!confirm(`Remove "${UPLOADED.name}" from this browser?`)) return;
      UPLOADED = emptyUploaded();
      saveUploaded();
      purgeUploadedReviews();
      if (SRC === "uploaded") setSourceKey("mine");
      renderDatasetResult();
      renderBoardView();
      toast("Uploaded inbox removed.");
    });
  }
}

function initSourceSwitch() {
  $("#switch-mine").addEventListener("click", () => switchSource("mine"));
  $("#switch-sample").addEventListener("click", () => switchSource("sample"));
  const uploadedSwitch = $("#switch-uploaded");
  if (uploadedSwitch) uploadedSwitch.addEventListener("click", () => switchSource("uploaded"));
}

function initMineControls() {
  const input = $("#mail-file-input");
  input.addEventListener("change", () => {
    uploadFiles(input.files);
    input.value = "";
  });

  $("#add-emails-btn").addEventListener("click", () => {
    ADDMAIL_OPEN = !ADDMAIL_OPEN;
    renderBoardView();
  });
  $("#try-samples-btn").addEventListener("click", trySampleEmails);
  $("#show-sample-inbox-btn").addEventListener("click", () => switchSource("sample"));

  $("#clear-mine-btn").addEventListener("click", async () => {
    if (UPLOADING) return;
    if (!confirm("Clear all of your uploaded mail? This cannot be undone.")) return;
    if (ACCOUNT.user) {
      try {
        const r = await fetch("/api/mail", { method: "DELETE", credentials: "same-origin" });
        if (!r.ok && r.status !== 401) { toast("Could not clear your mail. Try again."); return; }
      } catch {
        toast("Could not reach the server. Try again.");
        return;
      }
    }
    MINE = { board: [], detail: {} };
    persistMineIfLocal();
    clearUploadPanels();
    renderBoardView();
    toast("Your mail cleared.");
  });

  const zone = $("#mail-drop-zone");
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.dataset.drag = "true"; });
  zone.addEventListener("dragleave", () => { zone.dataset.drag = "false"; });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.dataset.drag = "false";
    if (UPLOADING) return;
    const dropped = e.dataTransfer && e.dataTransfer.files;
    if (dropped && dropped.length) uploadFiles(dropped);
  });

  // Drag-and-drop anywhere on the inbox view, not just the empty-state zone.
  const view = $("#view-board");
  ["dragenter", "dragover"].forEach((evt) => view.addEventListener(evt, (e) => {
    if (SRC !== "mine" || UPLOADING) return;
    e.preventDefault();
    view.classList.add("drag-over");
  }));
  ["dragleave", "drop"].forEach((evt) => view.addEventListener(evt, () => view.classList.remove("drag-over")));
  view.addEventListener("drop", (e) => {
    if (SRC !== "mine" || UPLOADING) return;
    e.preventDefault();
    const dropped = e.dataTransfer && e.dataTransfer.files;
    if (dropped && dropped.length) uploadFiles(dropped);
  });
}

/* ── Add mail: "Drop .eml files" / "Paste an email" / "Upload a dataset
   (.zip)" tabs ────────────────────────────────────────────────── */
function setAddMailTab(tab) {
  ADDMAIL_TAB = tab;
  $("#addmail-tab-drop").setAttribute("aria-selected", String(tab === "drop"));
  $("#addmail-tab-paste").setAttribute("aria-selected", String(tab === "paste"));
  const datasetTab = $("#addmail-tab-dataset");
  if (datasetTab) datasetTab.setAttribute("aria-selected", String(tab === "dataset"));
  $("#addmail-drop").hidden = tab !== "drop";
  $("#addmail-paste").hidden = tab !== "paste";
  const datasetSection = $("#addmail-dataset");
  if (datasetSection) datasetSection.hidden = tab !== "dataset";
  // "Scan a photo" — the panel itself is owned and populated by
  // web/scan.js; this file only knows how to show/hide it and mark it
  // selected, same as the dataset tab above.
  const scanTab = $("#addmail-tab-scan");
  if (scanTab) scanTab.setAttribute("aria-selected", String(tab === "scan"));
  const scanSection = $("#addmail-scan");
  if (scanSection) scanSection.hidden = tab !== "scan";
}

function initAddMailTabs() {
  $("#addmail-tab-drop").addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("drop"); });
  $("#addmail-tab-paste").addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("paste"); });
  const datasetTab = $("#addmail-tab-dataset");
  if (datasetTab) datasetTab.addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("dataset"); });
  const scanTab = $("#addmail-tab-scan");
  if (scanTab) scanTab.addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("scan"); });
}

function resetPasteForm() {
  const form = $("#paste-form");
  if (form) form.reset();
  const err = $("#paste-body-error");
  if (err) { err.hidden = true; err.textContent = ""; }
}

function clearPasteBodyError() {
  const err = $("#paste-body-error");
  if (err && !err.hidden) { err.hidden = true; err.textContent = ""; }
}

/* Runs the pasted email through the exact same pipeline as a dropped .eml —
   submitProcessEmail(), mergeMineResult(), the same progress/error panels —
   so the two intake paths can never show different results for the same
   email. */
async function submitPaste(e) {
  e.preventDefault();
  if (UPLOADING) return;

  const subject = $("#paste-subject").value.trim();
  const body = $("#paste-body").value.trim();
  const sender = $("#paste-from").value.trim();
  const filesInput = $("#paste-files");
  const files = Array.from((filesInput && filesInput.files) || []);

  if (!body) {
    const err = $("#paste-body-error");
    if (err) { err.hidden = false; err.textContent = "Paste the email text first."; }
    const ta = $("#paste-body");
    if (ta) ta.focus();
    return;
  }
  clearPasteBodyError();
  if (files.length > 4) { toast("Attach up to 4 files."); return; }

  clearUploadPanels();
  setUploadingUI(true);
  const btn = $("#paste-submit");
  if (btn) { btn.disabled = true; btn.textContent = "Checking…"; }
  const progress = $("#upload-progress");
  progress.hidden = false;
  progress.textContent = "Reading the email…";

  const pasteFormData = (selection = {}) => {
    const fd = new FormData();
    if (subject) fd.append("subject", subject);
    fd.append("body", body);
    if (sender) fd.append("sender", sender);
    for (const f of files) fd.append("files", f);
    if (selection.selected_si) fd.append("selected_si", selection.selected_si);
    if (selection.selected_bl) fd.append("selected_bl", selection.selected_bl);
    return fd;
  };

  try {
    let data = await submitProcessEmail(pasteFormData());
    if (data.selection_required) {
      progress.textContent = "Choose the documents to compare…";
      const selection = await chooseDocumentCandidates(data);
      if (!selection) {
        toast("Document selection cancelled.");
        return;
      }
      progress.textContent = "Checking the selected documents…";
      data = await submitProcessEmail(pasteFormData(selection));
    }
    const wasDuplicate = mergeMineResult(data);
    persistMineIfLocal();
    resetPasteForm();
    renderBoardView();
    let msg = "Email read";
    if (data.board && data.board.status === "MISMATCH") msg += " · discrepancy found";
    if (wasDuplicate) msg += " · already in your mail";
    toast(msg);
  } catch (err) {
    if (err && err.missingApi) {
      showUploadApiMissing();
    } else {
      showUploadErrors([{ name: subject || "Pasted email", detail: (err && err.detail) || "could not be processed" }]);
    }
  } finally {
    progress.hidden = true;
    setUploadingUI(false);
    if (btn) { btn.disabled = false; btn.textContent = "Check this email"; }
  }
}

function initPasteForm() {
  const form = $("#paste-form");
  if (form) form.addEventListener("submit", submitPaste);
  const bodyInput = $("#paste-body");
  if (bodyInput) bodyInput.addEventListener("input", clearPasteBodyError);
}

/* Hook for web/scan.js ("Scan a document (photo)"): once the clerk has
   checked and edited the AI-read text and presses "Text looks right —
   check it", scan.js hands the subject/body/files here so the result goes
   through the exact same /api/process-email call and Your-mail merge as
   "Paste an email" (submitProcessEmail, mergeMineResult,
   persistMineIfLocal, renderBoardView) — the two intake paths can never
   show different results for the same email. Kept as one small,
   additive, window-exposed function rather than duplicating any of this
   logic in scan.js, which is a plain script and cannot import this
   module's private functions directly.
   Throws the same { missingApi: true } / { detail } shapes
   submitProcessEmail throws; scan.js is responsible for displaying those. */
window.clearDraftSubmitScan = async function clearDraftSubmitScan({ subject, body, files }) {
  // Same mutual-exclusion every other intake path respects (uploadFiles,
  // submitPaste, dataset upload): one submission in flight at a time,
  // across ALL of them, not just within scan.js's own confirm button —
  // two intake paths racing to merge into MINE.board at once is exactly
  // the kind of thing UPLOADING exists to prevent.
  if (UPLOADING) throw { detail: "Another upload is already in progress. Wait for it to finish." };
  setUploadingUI(true);
  try {
    const fd = new FormData();
    if (subject) fd.append("subject", subject);
    fd.append("body", body || "");
    for (const f of files || []) fd.append("files", f);
    const data = await submitProcessEmail(fd);
    const wasDuplicate = mergeMineResult(data);
    persistMineIfLocal();
    // Any error/progress panel left over from an earlier "Drop .eml
    // files" or "Paste an email" attempt on this same board view must not
    // linger once a scan submission succeeds — same panels, same clear
    // function every other successful intake path already relies on.
    clearUploadPanels();
    renderBoardView();
    return { data, wasDuplicate };
  } finally {
    setUploadingUI(false);
  }
};

/* ── Accounts ───────────────────────────────────────────────────
   Entirely optional: GET /api/auth/me tells us on boot whether accounts
   exist at all. Any failure (503, 404, a network error) is treated the
   same way — accounts are unavailable, the account control stays hidden,
   and the rest of the site behaves exactly as it does without them. */
async function fetchMe() {
  try {
    const r = await fetch("/api/auth/me", { credentials: "same-origin", signal: AbortSignal.timeout(2500) });
    if (r.status === 401) { ACCOUNT.available = true; ACCOUNT.user = null; return; }
    if (!r.ok) { ACCOUNT.available = false; ACCOUNT.user = null; return; }
    const j = await r.json();
    ACCOUNT.available = true;
    ACCOUNT.user = j.user || null;
  } catch {
    ACCOUNT.available = false;
    ACCOUNT.user = null;
  }
}

function renderAccountChip() {
  const chip = $("#account-chip");
  if (!chip) return;
  if (!ACCOUNT.available) { chip.hidden = true; return; }
  chip.hidden = false;
  const signinBtn = $("#account-signin-btn");
  const wrap = $("#account-user-wrap");
  if (ACCOUNT.user) {
    signinBtn.hidden = true;
    wrap.hidden = false;
    $("#account-email-text").textContent = ACCOUNT.user.email;
    $("#account-email-btn").title = ACCOUNT.user.email;
  } else {
    // The #/account page already has its own "Sign in" / "Create account"
    // form front and centre — a second, identical "Sign in" button in the
    // topbar just confuses testers, so it hides while that page is open.
    signinBtn.hidden = location.hash.startsWith("#/account");
    wrap.hidden = true;
    closeAccountMenu();
  }
  renderLearnedShortcut();
}

function closeAccountMenu() {
  const menu = $("#account-menu");
  const btn = $("#account-email-btn");
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
}

/* A visitor's local mail (added before they had an account, or on a
   browser they were signed out on) is folded into the account the first
   time they sign in or sign up, then the local copy is cleared — it now
   lives on the server, the single source of truth from here on. */
async function importLocalMailIfAny() {
  const local = loadMine();
  if (!local.board.length) return;
  try {
    const r = await fetch("/api/mail/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ board: local.board, detail: local.detail }),
    });
    if (!r.ok) return;
    const j = await r.json().catch(() => ({}));
    try { localStorage.removeItem("cleardraft.mine.v1"); } catch { /* best effort */ }
    const n = typeof j.count === "number" ? j.count : local.board.length;
    toast(`Moved ${n} email${n === 1 ? "" : "s"} into your account`);
  } catch { /* offline — the local copy stays put as a fallback */ }
}

async function loadAccountMail() {
  try {
    const r = await fetch("/api/mail", { credentials: "same-origin", signal: AbortSignal.timeout(4000) });
    if (r.status === 401) { ACCOUNT.user = null; return; }
    if (!r.ok) return;
    const j = await r.json();
    if (j && Array.isArray(j.board) && j.detail) MINE = { board: j.board, detail: j.detail };
  } catch { /* network hiccup — keep whatever Your mail already shows */ }
}

async function afterSignedIn() {
  REVIEWS = loadReviews();
  CORRECTIONS = loadCorrections();
  await importLocalMailIfAny();
  await loadAccountMail();
  await Promise.all([loadPairs(), loadFeedback()]);
  renderAccountChip();
  if (location.hash.startsWith("#/board")) renderBoardView();
}

async function signOut() {
  try { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); } catch { /* proceed regardless */ }
  ACCOUNT.user = null;
  MINE = loadMine();
  REVIEWS = loadReviews();
  CORRECTIONS = loadCorrections();
  PAIRS = [];
  invalidateEvalCache();
  renderLearnedShortcut();
  renderAccountChip();
  location.hash = "#/";
  route();
  toast("Signed out.");
}

function initAccountControl() {
  const signinBtn = $("#account-signin-btn");
  if (signinBtn) signinBtn.addEventListener("click", () => { location.hash = "#/account"; });

  const emailBtn = $("#account-email-btn");
  if (emailBtn) {
    emailBtn.addEventListener("click", () => {
      const menu = $("#account-menu");
      const willShow = menu.hidden;
      menu.hidden = !willShow;
      emailBtn.setAttribute("aria-expanded", String(willShow));
    });
  }

  document.addEventListener("click", (e) => {
    const wrap = $("#account-user-wrap");
    if (wrap && !wrap.hidden && !wrap.contains(e.target)) closeAccountMenu();
  });

  const signoutBtn = $("#account-signout-btn");
  if (signoutBtn) signoutBtn.addEventListener("click", () => { closeAccountMenu(); signOut(); });
}

/* ── #/account: create-account / sign-in form ──────────────────── */
const AUTH_ERROR_TEXT = {
  invalid_email: "That email address doesn't look right.",
  weak_password: "Choose a longer password (at least 8 characters).",
  email_taken: "An account with that email already exists.",
  bad_credentials: "Wrong email or password.",
  too_many_attempts: "Too many attempts. Wait a bit and try again.",
  accounts_unavailable: "Accounts aren't available right now.",
};

function authErrorMessage(j) {
  if (j && j.detail) return j.detail;
  if (j && j.error && AUTH_ERROR_TEXT[j.error]) return AUTH_ERROR_TEXT[j.error];
  return "Something went wrong. Try again.";
}

function setAccountMode(mode) {
  ACCOUNT_MODE = mode;
  renderAccountView();
}

function renderAccountView() {
  const title = $("#account-title");
  const submit = $("#account-submit");
  const switchBtn = $("#account-switch-btn");
  const pwInput = $("#account-password-input");
  const errHost = $("#account-error");
  if (!title) return;
  errHost.hidden = true;
  errHost.textContent = "";
  if (ACCOUNT_MODE === "signup") {
    title.textContent = "Create your account";
    submit.textContent = "Create account";
    switchBtn.textContent = "Already have an account? Sign in";
    pwInput.autocomplete = "new-password";
  } else {
    title.textContent = "Sign in";
    submit.textContent = "Sign in";
    switchBtn.textContent = "New here? Create an account";
    pwInput.autocomplete = "current-password";
  }
}

async function submitAccountForm(e) {
  e.preventDefault();
  const email = $("#account-email-input").value.trim();
  const password = $("#account-password-input").value;
  const errHost = $("#account-error");
  errHost.hidden = true;
  errHost.textContent = "";

  const btn = $("#account-submit");
  btn.disabled = true;
  const prevText = btn.textContent;
  btn.textContent = ACCOUNT_MODE === "signup" ? "Creating…" : "Signing in…";

  const path = ACCOUNT_MODE === "signup" ? "/api/auth/signup" : "/api/auth/login";
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ email, password }),
    });
    let j = {};
    try { j = await r.json(); } catch { /* no body */ }

    if (r.status === 503) {
      ACCOUNT.available = false;
      renderAccountChip();
      errHost.hidden = false;
      errHost.textContent = authErrorMessage(j);
      return;
    }
    if (!r.ok) {
      errHost.hidden = false;
      errHost.textContent = authErrorMessage(j);
      return;
    }

    ACCOUNT.available = true;
    ACCOUNT.user = j.user;
    renderAccountChip();
    await afterSignedIn();
    setSourceKey("mine");
    location.hash = "#/board";
    route();
    // Focus lands on a heading, not left behind on the submit button the
    // route just hid.
    const heading = $("#board-heading");
    if (heading) heading.focus();
    toast(ACCOUNT_MODE === "signup" ? "Account created." : "Signed in.");
  } catch {
    errHost.hidden = false;
    errHost.textContent = "Could not reach the server. Check your connection and try again.";
  } finally {
    btn.disabled = false;
    btn.textContent = prevText;
  }
}

function initAccountForm() {
  const form = $("#account-form");
  if (!form) return;
  form.addEventListener("submit", submitAccountForm);
  $("#account-switch-btn").addEventListener("click", () => setAccountMode(ACCOUNT_MODE === "signup" ? "signin" : "signup"));
  const toggle = $("#account-password-toggle");
  toggle.addEventListener("click", () => {
    const input = $("#account-password-input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    toggle.textContent = show ? "Hide" : "Show";
    toggle.setAttribute("aria-pressed", String(show));
  });
}

/* ── #/account, signed in: a summary + link to #/learned ─────────
   A signed-in visitor to #/account sees a one-line summary instead of the
   create-account/sign-in form (that form is for signed-out visitors); the
   full management page lives at #/learned. */
function renderAccountSignedInView() {
  $("#account-form").hidden = true;
  $("#account-signed-in").hidden = false;
  $("#account-signed-in-email").textContent = ACCOUNT.user.email;
  const summary = $("#learned-summary-line");
  if (summary) {
    loadPairs().then(() => {
      const n = PAIRS.length;
      summary.textContent = n
        ? `${n} of ${MAX_PAIRS} saved. Only you can see them.`
        : "No pairs marked as the same yet.";
    });
  }
}

/* ── #/learned — the management page ──────────────────────────── */
let LEARNED_QUERY = "";
let LEARNED_FIELD_FILTER = null; // null = all fields
let LEARNED_EDIT_ID = null;      // set by #/learned/<id>, opens that row for editing
const LEARNED_REMOVED = new Map(); // id -> removed pair, for "Removed · Restore"

function learnedMatchesQuery(pair, q) {
  if (!q) return true;
  const hay = [pair.si_raw, pair.bl_raw, pair.a, pair.b, pair.note, pair.field].filter(Boolean).join(" ").toLowerCase();
  return hay.includes(q);
}

function learnedFieldChips(pairs) {
  const wrap = $("#learned-field-chips");
  wrap.replaceChildren();
  const counts = {};
  for (const p of pairs) counts[p.field] = (counts[p.field] || 0) + 1;

  const allBtn = el("button", "chip chip-quiet learned-chip-btn", `All (${pairs.length})`);
  allBtn.type = "button";
  allBtn.setAttribute("aria-pressed", String(LEARNED_FIELD_FILTER === null));
  allBtn.addEventListener("click", () => { LEARNED_FIELD_FILTER = null; renderLearnedList(); });
  wrap.append(allBtn);

  for (const field of Object.keys(counts).sort()) {
    const btn = el("button", "chip chip-quiet learned-chip-btn", `${fieldLabel(field)} (${counts[field]})`);
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(LEARNED_FIELD_FILTER === field));
    btn.addEventListener("click", () => { LEARNED_FIELD_FILTER = field; renderLearnedList(); });
    wrap.append(btn);
  }
}

/* One row: field · SI wording ↔ BL wording (· "matches as …" when
   normalisation changed it) · reason · marked date + source link · edited.
   Edit toggles an inline form (wordings + reason, server errors inline);
   Remove swaps the row for "Removed · Restore". */
function learnedPairRow(pair, openEdit) {
  const row = el("div", "learned-row");
  row.dataset.pairId = pair.id;

  if (LEARNED_REMOVED.has(pair.id)) {
    const removedLine = el("div", "learned-removed-line");
    removedLine.append(document.createTextNode("Removed · "));
    const restoreBtn = el("button", "link-btn", "Restore");
    restoreBtn.type = "button";
    restoreBtn.addEventListener("click", async () => {
      restoreBtn.disabled = true;
      const removedPair = LEARNED_REMOVED.get(pair.id);
      try {
        const r = await fetch("/api/equivalences", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            field: removedPair.field, si_value: removedPair.si_raw, bl_value: removedPair.bl_raw,
            source: removedPair.source || "", note: removedPair.note || "",
          }),
        });
        if (r.ok) {
          LEARNED_REMOVED.delete(pair.id);
          await loadPairs();
          renderLearnedPage();
          toast("Restored.");
        } else {
          restoreBtn.disabled = false;
          toast("Could not restore this pair.");
        }
      } catch {
        restoreBtn.disabled = false;
        toast("Could not reach the server.");
      }
    });
    removedLine.append(restoreBtn);
    row.append(removedLine);
    return row;
  }

  const head = el("div", "learned-row-head");
  head.append(el("span", "chip chip-quiet learned-field-chip", fieldLabel(pair.field)));
  const wordings = el("span", "learned-wordings");
  wordings.append(el("span", null, pair.si_raw || pair.a));
  wordings.append(el("span", "learned-pair-arrow", "↔"));
  wordings.append(el("span", null, pair.bl_raw || pair.b));
  head.append(wordings);
  row.append(head);

  const norm = pair.a !== (pair.si_raw || pair.a) || pair.b !== (pair.bl_raw || pair.b);
  if (norm) row.append(el("div", "learned-matches-as", `compared as "${pair.a}" ↔ "${pair.b}"`));

  const reasonLine = el("div", "learned-reason");
  reasonLine.textContent = pair.note ? `"${pair.note}"` : "No reason given";
  row.append(reasonLine);

  const meta = el("div", "learned-meta");
  const date = formatPairDate(pair.added_at);
  meta.append(document.createTextNode(`Marked${date ? ` ${date}` : ""}${pair.source ? " from case " : ""}`));
  if (pair.source) {
    const link = el("a", null, referenceForSource(pair.source));
    link.href = `#/case/${pair.source}`;
    meta.append(link);
  }
  if (pair.updated_at) meta.append(document.createTextNode(" · edited"));
  row.append(meta);

  const actions = el("div", "learned-actions");
  const editBtn = el("button", "link-btn", "Edit");
  editBtn.type = "button";
  const removeBtn = el("button", "link-btn", "Remove");
  removeBtn.type = "button";
  actions.append(editBtn, removeBtn);
  row.append(actions);

  const editForm = el("div", "learned-edit-form");
  editForm.hidden = !openEdit;

  function buildEditForm() {
    editForm.replaceChildren();
    const siLabel = el("label", "field-label", "SI wording");
    const siInput = document.createElement("input");
    siInput.type = "text"; siInput.className = "field-input"; siInput.value = pair.si_raw || pair.a;
    editForm.append(siLabel, siInput);

    const blLabel = el("label", "field-label", "BL wording");
    const blInput = document.createElement("input");
    blInput.type = "text"; blInput.className = "field-input"; blInput.value = pair.bl_raw || pair.b;
    editForm.append(blLabel, blInput);

    const noteLabel = el("label", "field-label", "Reason (optional)");
    const noteInput = document.createElement("textarea");
    noteInput.className = "field-input"; noteInput.rows = 2; noteInput.maxLength = MARK_SAME_REASON_MAX;
    noteInput.value = pair.note || "";
    editForm.append(noteLabel, noteInput);

    const err = el("div", "account-error learned-edit-error");
    err.hidden = true; err.setAttribute("aria-live", "polite");
    editForm.append(err);

    const editActions = el("div", "learned-edit-actions");
    const saveBtn = el("button", "btn btn-primary", "Save");
    saveBtn.type = "button";
    const cancelBtn = el("button", "link-btn", "Cancel");
    cancelBtn.type = "button";
    editActions.append(saveBtn, cancelBtn);
    editForm.append(editActions);

    cancelBtn.addEventListener("click", () => { editForm.hidden = true; });
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      err.hidden = true;
      try {
        const r = await fetch(`/api/equivalences/${encodeURIComponent(pair.id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ si_value: siInput.value, bl_value: blInput.value, note: noteInput.value }),
        });
        let j = {};
        try { j = await r.json(); } catch { /* no body */ }
        if (!r.ok) {
          err.hidden = false;
          err.textContent = (j && j.detail) || "Could not save this change.";
          return;
        }
        await loadPairs();
        renderLearnedPage();
        toast("Saved.");
      } catch {
        err.hidden = false;
        err.textContent = "Could not reach the server. Check your connection and try again.";
      } finally {
        saveBtn.disabled = false;
      }
    });
  }
  buildEditForm();
  row.append(editForm);

  editBtn.addEventListener("click", () => { editForm.hidden = !editForm.hidden; });

  removeBtn.addEventListener("click", async () => {
    removeBtn.disabled = true;
    const removed = await deletePair(pair.id);
    if (removed) {
      LEARNED_REMOVED.set(pair.id, pair);
      renderLearnedPage();
    } else {
      removeBtn.disabled = false;
      toast("Could not remove this pair. Try again.");
    }
  });

  return row;
}

function renderLearnedList() {
  const host = $("#learned-list");
  const empty = $("#learned-empty");
  if (!host) return;
  learnedFieldChips(PAIRS);
  const q = LEARNED_QUERY.trim().toLowerCase();
  let pairs = [...PAIRS].sort((a, b) => (b.added_at || "").localeCompare(a.added_at || ""));
  if (LEARNED_FIELD_FILTER) pairs = pairs.filter((p) => p.field === LEARNED_FIELD_FILTER);
  pairs = pairs.filter((p) => learnedMatchesQuery(p, q));

  host.replaceChildren();
  const removedIds = [...LEARNED_REMOVED.keys()].filter((id) => !PAIRS.some((p) => p.id === id));
  if (!pairs.length && !removedIds.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  for (const pair of pairs) host.append(learnedPairRow(pair, pair.id === LEARNED_EDIT_ID));
  for (const id of removedIds) {
    const removedPair = LEARNED_REMOVED.get(id);
    host.append(learnedPairRow(removedPair, false));
  }
}

function renderLearnedPage() {
  const usage = $("#learned-usage-line");
  if (usage) usage.textContent = `${PAIRS.length} of ${MAX_PAIRS} used`;
  renderLearnedList();
}

async function renderLearnedView(editId) {
  LEARNED_EDIT_ID = editId || null;
  const signedOut = $("#learned-signed-out");
  const unavailable = $("#learned-unavailable");
  const signedIn = $("#learned-signed-in");
  signedOut.hidden = true; unavailable.hidden = true; signedIn.hidden = true;

  if (ACCOUNT.available === false) { unavailable.hidden = false; return; }
  if (!ACCOUNT.user) { signedOut.hidden = false; return; }

  signedIn.hidden = false;
  const list = $("#learned-list");
  list.replaceChildren(el("div", "empty", "Loading…"));
  await loadPairs();
  renderLearnedPage();
  const heading = $("#learned-heading");
  if (heading) heading.focus();
}

function initLearnedPage() {
  const search = $("#learned-search");
  if (search) {
    search.addEventListener("input", () => { LEARNED_QUERY = search.value; renderLearnedList(); });
  }
}

function route() {
  const h = location.hash;
  const views = { home: $("#view-home"), board: $("#view-board"), review: $("#view-review"), accuracy: $("#view-accuracy"), check: $("#view-check"), account: $("#view-account"), learned: $("#view-learned") };
  for (const v of Object.values(views)) v.hidden = true;

  let active = "home";
  if (h.startsWith("#/learned")) {
    active = "learned";
    views.learned.hidden = false;
    const editId = h.startsWith("#/learned/") ? h.slice("#/learned/".length) : null;
    renderLearnedView(editId);
  } else if (h.startsWith("#/case/")) {
    active = "review";
    views.review.hidden = false;
    renderReview(h.slice("#/case/".length));
  } else if (h.startsWith("#/accuracy")) {
    active = "accuracy";
    views.accuracy.hidden = false;
    renderAccuracy();
  } else if (h.startsWith("#/check")) {
    active = "check";
    views.check.hidden = false;
  } else if (h.startsWith("#/account")) {
    active = "account";
    views.account.hidden = false;
    if (ACCOUNT.user) {
      renderAccountSignedInView();
    } else {
      $("#account-form").hidden = false;
      $("#account-signed-in").hidden = true;
      setAccountMode("signup");
      const emailInput = $("#account-email-input");
      if (emailInput) emailInput.focus();
    }
  } else if (h.startsWith("#/board")) {
    active = "board";
    views.board.hidden = false;
    renderBoardView();
  } else {
    // "", "#", "#/", and any hash we don't recognise all land on the home
    // screen rather than a dead page.
    active = "home";
    views.home.hidden = false;
    renderHome();
  }

  for (const a of document.querySelectorAll(".nav-link")) {
    const route = a.dataset.route;
    const on = route === active || (route === "board" && active === "review");
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const learnedShortcut = document.getElementById("learned-shortcut");
  if (learnedShortcut) {
    if (location.hash.startsWith("#/learned")) learnedShortcut.setAttribute("aria-current", "page");
    else learnedShortcut.removeAttribute("aria-current");
  }
  renderAccountChip();
  window.scrollTo(0, 0);
}

/* action = { label, onClick }. Plain toasts keep the old timing and stay
   inert (pointer-events: none), so a clerk can never accidentally click
   through one. A toast carrying an action becomes clickable and lingers
   longer, since it is now something to read and decide on, not just notice. */
let toastTimer;
function toast(msg, action) {
  const t = $("#toast");
  t.replaceChildren(document.createTextNode(msg));
  if (action) {
    const btn = el("button", "toast-action", action.label);
    btn.type = "button";
    btn.addEventListener("click", () => {
      clearTimeout(toastTimer);
      t.dataset.show = "false";
      action.onClick();
    });
    t.append(btn);
  }
  t.classList.toggle("toast-has-action", Boolean(action));
  t.dataset.show = "true";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.dataset.show = "false"; }, action ? 6000 : 3200);
}

(async function boot() {
  MINE = loadMine();
  UPLOADED = loadUploaded();
  SRC = getSourceKey();

  try {
    [DATA] = await Promise.all([load(), fetchMe()]);
  } catch {
    $("#case-list").append(el("div", "empty", "Could not load the inbox. Please refresh."));
    return;
  }

  for (const b of document.querySelectorAll(".tab")) {
    b.addEventListener("click", () => setTab(b.dataset.tab));
  }
  initCheckPage();
  initSearch();
  initSourceSwitch();
  initMineControls();
  initAddMailTabs();
  initDatasetUpload();
  initPasteForm();
  initAccountControl();
  initAccountForm();
  initCaseKeyboardNav();
  initExportMenu();
  initLearnedPage();

  const mineCard = $("#home-card-mine");
  if (mineCard) mineCard.addEventListener("click", () => setSourceKey("mine"));

  renderAccountChip();
  if (ACCOUNT.available && ACCOUNT.user) await afterSignedIn();

  addEventListener("hashchange", route);
  route();
})();
