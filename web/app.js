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

/* The effective status/defect count for a board row: the cached evaluation
   when one exists, otherwise the checked result. Used by tabOf()/counts/tags
   so the board reflects marked-as-same pairs live. */
function effectiveFor(source, row) {
  const cache = EVAL_CACHE[source];
  const ev = cache && cache.get(row.email_id);
  const base = ev
    ? { status: ev.status || row.status, review_reason: ev.review_reason || row.review_reason,
        defect_fields: ev.defect_fields || row.defect_fields || [], changed: Boolean(ev.changed) }
    : { status: row.status, review_reason: row.review_reason,
        defect_fields: row.defect_fields || [], changed: false };
  if (!isProblemReview(getReview(source, row.email_id))) return base;
  return { ...base, status: "NEEDS_REVIEW", review_reason: "human_feedback", changed: true, human_feedback: true };
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
  missing_attachment: "documents not attached",
  wrong_doc_type: "not an SI/BL pair",
  unreadable: "document could not be read",
  missing_value: "a field was blank",
  unclassified: "could not tell what it wants",
  human_feedback: "flagged by a reviewer",
};

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

  if (!rows.length) { list.append(el("div", "empty", q ? "No matches in this tab." : "Nothing here.")); return; }

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
      const chipTxt = rowReview.verdict === "confirmed" ? "Confirmed" : rowReview.verdict === "flagged" ? "Flagged" : problemFound ? "Problem found" : "Done";
      tags.append(el("span", `chip ${chipCls}`, chipTxt));
    }
    const eff = effectiveFor(SRC, r);
    if (eff.status === "MISMATCH") {
      for (const f of eff.defect_fields) tags.append(el("span", "chip chip-mismatch", f.replace(/_/g, " ")));
    } else if (eff.status === "NEEDS_REVIEW") {
      tags.append(el("span", "chip chip-review", REASON[eff.review_reason] || "needs a human"));
    } else if (r.category === "BL_COMPARISON") {
      tags.append(el("span", "chip chip-match", "7 of 7 match"));
    } else {
      tags.append(el("span", "chip chip-quiet", r.category.replace(/_/g, " ").toLowerCase()));
    }
    if (eff.changed) {
      const originalCount = (r.defect_fields || []).length;
      const clearedCount = originalCount - eff.defect_fields.length;
      if (eff.status !== "MISMATCH" && r.status === "MISMATCH") {
        tags.append(el("span", "chip chip-quiet chip-marked-same", "Cleared by your marked pairs"));
      } else if (clearedCount > 0) {
        tags.append(el("span", "chip chip-quiet chip-marked-same", `${clearedCount} of ${originalCount} marked same`));
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

function renderVerdict(d, host, { reply = true, afterVerdict = null, evaluation = null, rerender = null } = {}) {
  const effStatus = evaluation ? evaluation.status : d.status;
  const effDefectFields = evaluation ? evaluation.defect_fields : d.defect_fields;
  const kind = evaluation ? (effStatus === "MISMATCH" ? "mismatch" : effStatus === "NEEDS_REVIEW" ? "review" : "match") : verdictKind(d);
  const icon = { mismatch: "≠", review: "?", match: "✓" }[kind];
  const n = effDefectFields.length;
  const title = {
    mismatch: `${n} discrepanc${n === 1 ? "y" : "ies"} found`,
    review: "A person needs to look at this",
    match: d.comparisons.length ? "No mismatch detected" : "No document check was needed",
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
    vt.append(el("div", "verdict-live-sub",
      `Checked with ${d.defect_fields.length} discrepanc${d.defect_fields.length === 1 ? "y" : "ies"} · ${clearedCount} since marked as same by you`));
  }
  v.append(vt);
  host.append(v);

  if (afterVerdict) host.append(afterVerdict);

  if (d.comparisons.length) {
    host.append(seamTable(d, { evaluation, rerender }));
    const note = orderOfNote(d);
    if (note) host.append(note);
  }
  if (reply) {
    const useDraft = evaluation && evaluation.changed && evaluation.reply_draft ? evaluation.reply_draft : d.reply_draft;
    host.append(replyCard(d, useDraft, "Reply, ready to send", null, Boolean(evaluation && evaluation.changed && evaluation.reply_draft)));
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
    `The ${orderSide} names this party "To the order of" — that makes the Bill of Lading negotiable — while the ${plainSide} names a plain consignee. ClearDraft treats them as the same company, so it is not flagged. Confirm the BL type is what the shipper wants.`
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
  return {
    ...baseExportRow(row, detail, source),
    field: c.label,
    field_result: fieldResultOf(c),
    si_value: (c.si && c.si.value) || "",
    bl_value: (c.bl && c.bl.value) || "",
    si_source: sourceStr(c.si),
    bl_source: sourceStr(c.bl),
    explanation: fieldExplanation(c),
    marked_as_same: pairId ? "yes" : "",
    marked_reason: pair ? (pair.note || "") : "",
  };
}

function noComparisonExportRow(row, detail, source) {
  return {
    ...baseExportRow(row, detail, source),
    field: "", field_result: "", si_value: "", bl_value: "", si_source: "", bl_source: "",
    explanation: noComparisonExplanation(row, source),
  };
}

/* Effective: a field marked as same by the signed-in clerk is dropped from
   the discrepancy report entirely — that is the point of marking it. The
   organiser submission (buildSubmissionJson) never uses this. */
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
        if (c.undecidable || !c.matched) {
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
    return `Your part: Check the ${n} highlighted field${n === 1 ? "" : "s"} against ${n === 1 ? "its" : "their"} source lines, then send the reply asking for an amendment.`;
  }
  if (kind === "review") {
    const reason = REASON[d.review_reason] || "it was not sure";
    return `Your part: ClearDraft did not decide this one (${reason}). Open the documents and decide yourself.`;
  }
  if (evaluation && evaluation.changed && d.status === "MISMATCH" && effStatus !== "MISMATCH") {
    return "Your part: Nothing left to amend — the differences are marked as same by you. Check the reply and send it.";
  }
  if (d.comparisons && d.comparisons.length) {
    return `Your part: All ${d.comparisons.length} fields match. Skim the table, then send the confirmation.`;
  }
  const cat = d.category.replace(/_/g, " ").toLowerCase();
  return `Your part: Not a document check (${cat}). Handle it as usual.`;
}

function yourPartPanel(d, evaluation) {
  const panel = el("div", "your-part-panel");
  panel.append(el("div", "your-part-did", didLine(d)));
  panel.append(el("div", "your-part-yours", yourPartLine(d, evaluation)));
  return panel;
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
  const machineEvaluation = ACCOUNT.user ? EVAL_CACHE[source].get(id) : null;
  const effective = found ? effectiveFor(source, found.row) : null;
  const evaluation = effective && (machineEvaluation || effective.changed)
    ? { ...(machineEvaluation || {}), status: effective.status, review_reason: effective.review_reason,
        defect_fields: effective.defect_fields, changed: effective.changed }
    : machineEvaluation;
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
  meta.append(el("span", null, d.category.replace(/_/g, " ").toLowerCase()));
  let decidedText = d.decided_by === "model" ? "Decided by AI (model)" : "Decided by rule";
  if (typeof d.confidence === "number") decidedText += ` · confidence ${Math.round(d.confidence * 100)}%`;
  meta.append(el("span", null, decidedText));
  head.append(meta);

  if (d.uploaded) {
    const tag = el("div", "uploaded-tag");
    tag.append(el("span", "chip chip-quiet", `Uploaded · ${d.filename || "email"}`));
    if (d.received) tag.append(el("span", "uploaded-received", d.received));
    head.append(tag);
  }
  if (d.skipped_attachments && d.skipped_attachments.length) {
    head.append(el("div", "skipped-note", `Not read: ${d.skipped_attachments.join(", ")}`));
  }

  host.append(head);
  const caseActions = el("div", "case-actions");
  caseActions.append(printButton());
  caseActions.append(caseCsvButton(d));
  host.append(caseActions);
  host.append(originalEmailDetails(d));

  const yourPart = yourPartPanel(d, evaluation);
  if (d.recheck) {
    renderVerdict(d, host, { reply: false, afterVerdict: yourPart, evaluation, rerender });
    host.append(recheckCard(d.recheck, evaluation));
    const recheckDraft = evaluation && evaluation.changed && evaluation.recheck_reply_draft ? evaluation.recheck_reply_draft : d.recheck.reply_draft;
    host.append(replyCard(d, recheckDraft, "Follow-up reply, ready to send", d.reply_draft, Boolean(evaluation && evaluation.changed && evaluation.recheck_reply_draft)));
  } else {
    renderVerdict(d, host, { afterVerdict: yourPart, evaluation, rerender });
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
     and labelled, even though nothing about its status moved. */
  if (!evaluation && ACCOUNT.user && PAIRS.length) {
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
    msg.append(document.createTextNode("Sign in to teach ClearDraft that these two are the same "));
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
    "This teaches ClearDraft only this exact wording, only on this field, only on your account — you can undo it any time.");
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
        err.textContent = "Sign in to teach ClearDraft";
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
        ? "Already marked as same — this row will move up now."
        : "Saved — this row will move up now.";
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
      toast("Removed — this row will move back to Discrepancies.");
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

/* The seven-row table. Order: discrepancies first (the clerk's job is to
   find what is wrong), then a "Marked as same" group for rows a signed-in
   clerk has covered with a learned pair (evaluation.rows), then couldn't-
   read rows, then matches. */
function seamTable(d, ctx = {}) {
  const wrap = el("div", "seam-wrap");
  const head = el("div", "seam-head");
  head.append(el("div", null, "Field"), el("div", "h-si", "Shipping Instruction"), el("div", "h-seam"), el("div", "h-bl", "Draft Bill of Lading"));
  wrap.append(head);

  const evaluation = ctx.evaluation || null;
  const coveredRows = buildCoveredRows(d, evaluation);

  const groups = { mismatch: [], covered: [], unknown: [], match: [] };
  for (const c of d.comparisons) {
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
    const st = coveredPairId ? "covered" : stateOf(c);
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
      const unitChip = el("span", "chip chip-quiet", "units differ — same weight");
      unitChip.title = `${c.unit_note}${kgText}`;
      actions.append(unitChip);
    }

    for (const side of ["si", "bl"]) {
      const cell = el("div", `f-val f-${side}`);
      const fv = c[side];
      if (!fv || !fv.value) {
        cell.classList.add("empty-val");
        cell.textContent = fv ? "left blank" : "not present";
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

    /* "Mark as same": only a mismatch, only a learnable text field, only
       for a signed-in clerk (signed-out visitors still see the button, but
       the panel offers Sign in / Create account instead of a form).
       Numeric-field rows never see the button at all. */
    if (st === "mismatch" && LEARNABLE_FIELDS.has(c.field) && (ACCOUNT.user || ACCOUNT.available)) {
      const markBtn = el("button", "link-btn mark-same-btn", "Mark as same");
      markBtn.type = "button";
      markBtn.setAttribute("aria-expanded", "false");
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
      const btn = el("button", "proof-btn", panel.hidden ? "where this came from" : "hide");
      btn.setAttribute("aria-expanded", String(!panel.hidden));
      btn.addEventListener("click", () => {
        panel.hidden = !panel.hidden;
        btn.setAttribute("aria-expanded", String(!panel.hidden));
        btn.textContent = panel.hidden ? "where this came from" : "hide";
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
  metaRow.append(el("span", "reply-note", redrafted
    ? "Built from the checked values. Not written by a model."
    : "Built from the checked values. Not written by a model."));
  if (redrafted) {
    if (hadEdit) {
      metaRow.append(el("span", "chip chip-quiet reply-redrafted-chip", "Your edit kept — Reset to draft to see the update for your marked pairs"));
    } else {
      metaRow.append(el("span", "chip chip-quiet reply-redrafted-chip", "Updated for your marked pairs"));
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
    past.append(el("summary", null, "First discrepancy note, already sent"));
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
      toast("Reply copied — Gmail's link limit was hit, paste it into the email.");
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

  const otherBtn = el("a", "btn btn-quiet", "Other mail app");
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
        toast(`Discrepancies done — now: ${TAB_LABELS.needs_review} (N)`, undoAction);
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
      if (primary) primary.textContent = "Reply sent — next case";
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
      err.textContent = "Tell us what's wrong before saving.";
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
      status.append(el("span", null, "You flagged this: "));
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

    const checkedBtn = el("button", "btn btn-primary", "I checked — documents agree");
    checkedBtn.type = "button";
    checkedBtn.addEventListener("click", () => {
      setReview(source, id, "done", "checked by hand: documents agree");
      const undo = confirmToast(d, "Checked", source);
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
      ? "Reply sent — next case"
      : kind === "mismatch" ? "Discrepancy confirmed — next case" : "All clear confirmed — next case";
    const confirmBtn = el("button", "btn btn-primary", primaryLabel);
    confirmBtn.type = "button";
    confirmBtn.addEventListener("click", () => {
      setReview(source, id, "confirmed");
      const undo = confirmToast(d, "Confirmed", source);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true, undoAction: undo });
    });
    actions.append(confirmBtn);

    const wrongBtn = el("button", "btn btn-quiet", "Something's wrong");
    wrongBtn.type = "button";
    wrongBtn.setAttribute("aria-expanded", "false");
    actions.append(wrongBtn);

    const skipBtn = el("button", "link-btn", "Skip — next case");
    skipBtn.type = "button";
    skipBtn.addEventListener("click", () => {
      if (!context) return;
      advanceCase(id, context, { requireUndone: false });
    });
    actions.append(skipBtn);

    bar.append(actions);

    const noteForm = buildReviewNoteForm(id, "Save and next case", (note) => {
      setReview(source, id, "flagged", note);
      const undo = confirmToast(d, "Flagged", source);
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
  const hintText = !review && kind === "review" ? "N skip" : "D confirm · N skip";
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
      toast("ClearDraft didn't decide this one — choose an outcome below.");
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
  newly_broken: ["Broken by this amendment", "mismatch"],
  still_wrong:  ["Still wrong", "mismatch"],
  unreadable:   ["Could not be read", "review"],
  fixed:        ["Fixed", "match"],
  ok:           ["Unchanged, correct", "quiet"],
};

function recheckCard(rc, evaluation) {
  const card = el("div", "recheck");
  const head = el("div", "recheck-head");
  head.append(el("h3", null, "Amended draft received, re-checked"));
  if (rc.demo) head.append(el("span", "chip chip-quiet", "demo document"));
  card.append(head);

  const covered = (evaluation && evaluation.recheck) || {};

  const order = ["newly_broken", "still_wrong", "unreadable", "fixed", "ok"];
  for (const kind of order) {
    const rows = rc.rows.filter((r) => (r.outcome === kind) && !(kind !== "ok" && covered[r.field]));
    // A still_wrong/newly_broken row whose SI↔v2 pair is covered moves to
    // "Unchanged, correct" — labelled "marked as same" — never "Fixed".
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
  panel.append(el("div", "sub", `You reviewed ${total} case${total === 1 ? "" : "s"}: ${confirmed} looked right, ${flagged} flagged as wrong`));
  panel.append(el("div", "sub", `Today: ${reviewedTodayCount().handled} handled`));
  if (agreementDenom > 0) {
    panel.append(el("div", "reviews-agreement", `Agreement: ${Math.round((confirmed / agreementDenom) * 100)}%`));
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
    panel.append(el("h4", null, "Problems found on escalated cases"));
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
    [s.totals.emails, "messages processed", `${s.runtime_seconds}s for the full inbox`],
    [s.totals.compared, "document pairs compared", `${s.totals.attachments} attachments opened`],
    [s.totals.defects_found, "drafts with a discrepancy", "caught before the BL was issued"],
    [`${Math.round(s.decisions.rule_pct * 100)}%`, "decided by rule, not AI", `${s.model.calls} model calls this run`],
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
  p3.append(el("h3", null, "When it asked for a human"));
  p3.append(el("div", "sub", "Cases it refused to decide on its own."));
  p3.append(bars(s.escalations));
  host.append(p3);

  if (s.challenge) host.append(challengePanel(s.challenge));

  const note = el("div", "note");
  note.innerHTML = `<b>How to read these numbers.</b> They are measured on every run against the
    sample inbox, not typed in. The model is a fallback: it is asked only for a field the
    format readers could not find, and anything it returns must appear word for word in the
    document or it is thrown away. On this run it was consulted
    <b>${s.model.calls} time${s.model.calls === 1 ? "" : "s"}</b>.`;
  host.append(note);
}

/* The sample inbox is fully covered by rules, so it cannot show what the model
   adds. This panel reports a held-out set written without reference to our
   rules, run twice: rules alone, then rules with the model as fallback. */
function challengePanel(c) {
  const p = el("div", "panel");
  p.append(el("h3", null, "On mail it has never seen"));
  p.append(el("div", "sub", `${c.emails} held-out emails and ${c.doc_pairs.length} document pairs, written without reference to our rules. Each run twice.`));

  const t = el("div", "cmp");
  const head = el("div", "cmp-row cmp-head");
  for (const h of ["", "Rules only", "Rules + model"]) head.append(el("div", null, h));
  t.append(head);
  const rows = [
    ["Email sorted correctly", pct(c.category_accuracy.rules_only), pct(c.category_accuracy.with_model)],
    ["Request understood (intent)", pct(c.intent_accuracy.rules_only), pct(c.intent_accuracy.with_model)],
  ];
  const fr = c.doc_pairs.reduce((a, d) => a + d.fields_rules, 0);
  const fm = c.doc_pairs.reduce((a, d) => a + d.fields_model, 0);
  const fp = c.doc_pairs.reduce((a, d) => a + d.fields_present, 0);
  rows.push(["Fields read, unfamiliar labels", `${fr} of ${fp}`, `${fm} of ${fp}`]);
  for (const r of rows) {
    const row = el("div", "cmp-row");
    row.append(el("div", "cmp-l", r[0]), el("div", "cmp-v", r[1]), el("div", "cmp-v cmp-good", r[2]));
    t.append(row);
  }
  p.append(t);

  const facts = el("div", "cmp-facts");
  const placementRejections = c.placement_rejections || 0;
  facts.textContent =
    `The model decided ${c.decided_by_model} emails and got ${c.model_wrong} wrong. ` +
    `It was asked only what the rules could not answer: ${c.calls} calls, ${c.tokens.toLocaleString()} tokens in total. ` +
    `Answers not found word for word in the document are discarded; ${c.gate_rejections} were discarded this run. ` +
    `Values found on the page but assigned to the wrong field are rejected separately; ${placementRejections} were rejected this run.`;
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
  panel.append(el("strong", null, "The live checker needs the API."));
  const p = el("p", null, "It runs on the deployed site; locally start it with:");
  panel.append(p);
  panel.append(el("code", null, "python -m uvicorn api.index:app"));
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
  const rejections = data.model ? data.model.gate_rejections : 0;
  const placementRejections = data.model ? (data.model.placement_rejections || 0) : 0;
  host.append(el("div", "check-meta", `read in ${secs}s · model calls ${calls} · ${rejections} absent-source rejection${rejections === 1 ? "" : "s"} · ${placementRejections} wrong-field rejection${placementRejections === 1 ? "" : "s"}`));

  if (data.email_reading) {
    const er = data.email_reading;
    host.append(el("div", "check-meta", `The email reads as: ${String(er.category || "").toLowerCase()} / ${er.intent} (decided by ${er.decided_by})`));
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
      showCheckError(`The check failed on the server (HTTP ${r.status}). Try again, or use a sample pair.`);
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
      parts.push(`${pct(s.challenge.category_accuracy.with_model)} of never-seen mail sorted correctly`);
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
      ? `Your own mail, ${mineCount} message${mineCount === 1 ? "" : "s"} read by the live pipeline.`
      : "Add your own emails and watch the live pipeline check them.";
  } else if (SRC === "uploaded") {
    lede.textContent = `${UPLOADED.board.length} email${UPLOADED.board.length === 1 ? "" : "s"} from "${UPLOADED.name}", processed live on the server — nothing from this dataset is stored there.`;
  } else {
    lede.textContent = `One shared mailbox, ${sampleCount} messages. Every one has been read, sorted, and — where documents were attached — checked field by field.`;
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
    const startBtn = el("button", "btn btn-primary triage-start-btn", "Start");
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
    const progLabel = el("span", "triage-progress-label", `${handled} of ${totalY} handled`);
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
    if (pairs > 0) doneText += ` That saved up to ${pairs * 10} min of manual checking (estimate: up to 10 min per SI/BL pair by hand).`;
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
  panel.append(el("strong", null, "Uploading needs the live API."));
  panel.append(el("p", null, "This static preview has no backend. Use the live site:"));
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
      detail = "The zip is too large to upload (4 MB max). Try a smaller dataset.";
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
    ? `Dataset processed: ${UPLOADED.name}`
    : "Processed — not saved in this browser (re-upload after refresh).");

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
      if (!confirm(`Remove the uploaded dataset "${UPLOADED.name}"? This only removes it from this browser — nothing was ever stored on the server.`)) return;
      UPLOADED = emptyUploaded();
      saveUploaded();
      purgeUploadedReviews();
      if (SRC === "uploaded") setSourceKey("mine");
      renderDatasetResult();
      renderBoardView();
      toast("Uploaded dataset removed.");
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
}

function initAddMailTabs() {
  $("#addmail-tab-drop").addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("drop"); });
  $("#addmail-tab-paste").addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("paste"); });
  const datasetTab = $("#addmail-tab-dataset");
  if (datasetTab) datasetTab.addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("dataset"); });
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
    if (err) { err.hidden = false; err.textContent = "Email text is required."; }
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
        ? `${n} of ${MAX_PAIRS} pairs marked as the same, only visible to you.`
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
  if (norm) row.append(el("div", "learned-matches-as", `matches as "${pair.a}" ↔ "${pair.b}"`));

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
    const siLabel = el("label", "field-label", "Shipping Instruction wording");
    const siInput = document.createElement("input");
    siInput.type = "text"; siInput.className = "field-input"; siInput.value = pair.si_raw || pair.a;
    editForm.append(siLabel, siInput);

    const blLabel = el("label", "field-label", "Draft Bill of Lading wording");
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
    $("#case-list").append(el("div", "empty", "Could not load results. Run: python scripts/export_ui_data.py"));
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
