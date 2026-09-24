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
let SRC = "mine"; // "mine" | "sample" — which source the inbox view shows
let UPLOADING = false;

/* Accounts are optional: the site works exactly as it always has when they
   are unavailable or the visitor is signed out. `available` is null until
   GET /api/auth/me answers; once it is false, the account control stays
   hidden for the rest of the session. */
let ACCOUNT = { available: null, user: null };
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
   just marked it done. Persisted per-browser only, keyed by email_id;
   every access is try/caught, same pattern as loadMine()/saveMine(). */
function loadReviews() {
  try {
    const raw = localStorage.getItem("cleardraft.reviews.v1");
    if (!raw) return {};
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) return obj;
  } catch { /* corrupt or inaccessible storage — start empty */ }
  return {};
}
let REVIEWS = loadReviews();

function saveReviews() {
  try { localStorage.setItem("cleardraft.reviews.v1", JSON.stringify(REVIEWS)); } catch { /* per-browser convenience only */ }
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

function getReview(id) { return REVIEWS[id] || null; }
function isReviewed(id) { return Boolean(REVIEWS[id]); }
function setReview(id, verdict, note) {
  REVIEWS[id] = { verdict, note: note || "", at: new Date().toISOString() };
  saveReviews();
}
function clearReview(id) {
  delete REVIEWS[id];
  saveReviews();
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
  } catch { /* fall through to the default */ }
  return "mine";
}

function setSourceKey(s) {
  SRC = s;
  try { localStorage.setItem("cleardraft.source", s); } catch { /* per-viewer convenience only */ }
}

function activeBoard() { return SRC === "mine" ? MINE.board : DATA.board; }

function lookupDetail(id) { return MINE.detail[id] || (DATA && DATA.detail[id]); }

async function load() {
  const r = await fetch("public/data.json");
  if (!r.ok) throw new Error("could not load results");
  return r.json();
}

function tabOf(row) {
  if (row.status === "MISMATCH") return "mismatch";
  if (row.status === "NEEDS_REVIEW") return "needs_review";
  if (row.category === "BL_COMPARISON") return "cleared";
  return "other";
}

const REASON = {
  missing_attachment: "documents not attached",
  wrong_doc_type: "not an SI/BL pair",
  unreadable: "document could not be read",
  missing_value: "a field was blank",
  unclassified: "could not tell what it wants",
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
    if (isReviewed(r.email_id)) a.classList.add("case-done");
    a.href = `#/case/${r.email_id}`;
    a.setAttribute("role", "listitem");
    a.append(el("div", "case-ref", r.reference));
    a.append(el("div", "case-subject", r.subject));

    const tags = el("div", "case-fields");
    const rowReview = getReview(r.email_id);
    if (rowReview) {
      const chipCls = rowReview.verdict === "confirmed" ? "chip-confirmed" : rowReview.verdict === "flagged" ? "chip-flagged" : "chip-done";
      const chipTxt = rowReview.verdict === "confirmed" ? "Confirmed" : rowReview.verdict === "flagged" ? "Flagged" : "Done";
      tags.append(el("span", `chip ${chipCls}`, chipTxt));
    }
    if (r.status === "MISMATCH") {
      for (const f of r.defect_fields) tags.append(el("span", "chip chip-mismatch", f.replace(/_/g, " ")));
    } else if (r.status === "NEEDS_REVIEW") {
      tags.append(el("span", "chip chip-review", REASON[r.review_reason] || "needs a human"));
    } else if (r.category === "BL_COMPARISON") {
      tags.append(el("span", "chip chip-match", "7 of 7 match"));
    } else {
      tags.append(el("span", "chip chip-quiet", r.category.replace(/_/g, " ").toLowerCase()));
    }
    a.append(tags);
    list.append(a);
  }

  if (rows.length > 200) list.append(el("div", "empty", `Showing the first 200 of ${rows.length}.`));
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

function stateOf(c) {
  if (c.undecidable) return "unknown";
  return c.matched ? "match" : "mismatch";
}

/* Verdict + seam table + reply card, built from any object shaped like a
   DATA.detail[id] entry (status, review_reason, rationale, defect_fields,
   comparisons, reply_draft, subject, from, ...). Shared by the case review
   screen and the live "Check a pair" result, so the two never drift apart. */
function verdictKind(d) {
  return d.status === "MISMATCH" ? "mismatch" : d.status === "NEEDS_REVIEW" ? "review" : "match";
}

function renderVerdict(d, host, { reply = true, afterVerdict = null } = {}) {
  const kind = verdictKind(d);
  const icon = { mismatch: "≠", review: "?", match: "✓" }[kind];
  const n = d.defect_fields.length;
  const title = {
    mismatch: `${n} discrepanc${n === 1 ? "y" : "ies"} found`,
    review: "A person needs to look at this",
    match: d.comparisons.length ? "No mismatch detected" : "No document check was needed",
  }[kind];

  const v = el("div", `verdict verdict-${kind}`);
  v.append(el("div", "verdict-icon", icon));
  const vt = el("div", "verdict-text");
  vt.append(el("strong", null, title));
  vt.append(document.createTextNode(humanise(d.rationale || "")));
  v.append(vt);
  host.append(v);

  if (afterVerdict) host.append(afterVerdict);

  if (d.comparisons.length) {
    host.append(seamTable(d));
    const note = orderOfNote(d);
    if (note) host.append(note);
  }
  if (reply) host.append(replyCard(d, d.reply_draft, "Reply, ready to send", null));
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
   board/detail data (mine or sample), builds rows, and triggers a
   Blob download. No request ever leaves the browser. */

function boardForSource(source) { return source === "mine" ? MINE.board : (DATA ? DATA.board : []); }
function detailForSource(source, id) { return source === "mine" ? MINE.detail[id] : (DATA && DATA.detail[id]); }

const CSV_HEADERS = [
  "email_id", "reference", "subject", "from", "category", "status", "review_reason",
  "decided_by", "confidence", "field", "field_result", "si_value", "bl_value",
  "si_source", "bl_source", "explanation", "human_review", "human_note", "reviewed_at",
];

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
  return `SI says ${siVal}; draft BL says ${blVal}`;
}

function noComparisonExplanation(row) {
  if (row.status === "NEEDS_REVIEW") return REASON[row.review_reason] || "needs a human";
  return `Not a document check (${String(row.category || "").replace(/_/g, " ").toLowerCase()})`;
}

function reviewColumnsFor(id) {
  const r = getReview(id);
  if (!r) return { human_review: "", human_note: "", reviewed_at: "" };
  return { human_review: r.verdict, human_note: r.note || "", reviewed_at: r.at || "" };
}

function baseExportRow(row, detail) {
  const rv = reviewColumnsFor(row.email_id);
  return {
    email_id: row.email_id,
    reference: row.reference,
    subject: row.subject,
    from: row.from,
    category: row.category,
    status: row.status,
    review_reason: row.review_reason || "",
    decided_by: row.decided_by || (detail && detail.decided_by) || "",
    confidence: (detail && typeof detail.confidence === "number") ? Math.round(detail.confidence * 100) : "",
    human_review: rv.human_review,
    human_note: rv.human_note,
    reviewed_at: rv.reviewed_at,
  };
}

function fieldExportRow(row, detail, c) {
  return {
    ...baseExportRow(row, detail),
    field: c.label,
    field_result: fieldResultOf(c),
    si_value: (c.si && c.si.value) || "",
    bl_value: (c.bl && c.bl.value) || "",
    si_source: sourceStr(c.si),
    bl_source: sourceStr(c.bl),
    explanation: fieldExplanation(c),
  };
}

function noComparisonExportRow(row, detail) {
  return {
    ...baseExportRow(row, detail),
    field: "", field_result: "", si_value: "", bl_value: "", si_source: "", bl_source: "",
    explanation: noComparisonExplanation(row),
  };
}

function buildDiscrepancyRows(source) {
  const rows = [];
  for (const row of boardForSource(source)) {
    const detail = detailForSource(source, row.email_id);
    const comparisons = (detail && detail.comparisons) || [];
    if (comparisons.length) {
      for (const c of comparisons) {
        if (c.undecidable || !c.matched) rows.push(fieldExportRow(row, detail, c));
      }
    } else if (row.status === "NEEDS_REVIEW") {
      rows.push(noComparisonExportRow(row, detail));
    }
  }
  return rows;
}

function buildFullResultsRows(source) {
  const rows = [];
  for (const row of boardForSource(source)) {
    const detail = detailForSource(source, row.email_id);
    const comparisons = (detail && detail.comparisons) || [];
    if (comparisons.length) {
      for (const c of comparisons) rows.push(fieldExportRow(row, detail, c));
    } else {
      rows.push(noComparisonExportRow(row, detail));
    }
  }
  return rows;
}

/* {category, status, review_reason, defect_fields, has_defect} in exactly
   that key order, matching the organiser's sample_submission format. */
function buildSubmissionJson(source) {
  const out = {};
  for (const row of boardForSource(source)) {
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
  if (discBtn) discBtn.addEventListener("click", () => {
    closeExportMenu();
    downloadCsv(buildDiscrepancyRows(SRC), "discrepancy-report");
    toast("Discrepancy report downloaded.");
  });
  const fullBtn = $("#export-full-csv");
  if (fullBtn) fullBtn.addEventListener("click", () => {
    closeExportMenu();
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

function yourPartLine(d) {
  const kind = verdictKind(d);
  if (kind === "mismatch") {
    const n = d.defect_fields.length;
    return `Your part: Check the ${n} highlighted field${n === 1 ? "" : "s"} against ${n === 1 ? "its" : "their"} source lines, then send the reply asking for an amendment.`;
  }
  if (kind === "review") {
    const reason = REASON[d.review_reason] || "it was not sure";
    return `Your part: ClearDraft did not decide this one (${reason}). Open the documents and decide yourself.`;
  }
  if (d.comparisons && d.comparisons.length) {
    return `Your part: All ${d.comparisons.length} fields match. Skim the table, then send the confirmation.`;
  }
  const cat = d.category.replace(/_/g, " ").toLowerCase();
  return `Your part: Not a document check (${cat}). Handle it as usual.`;
}

function yourPartPanel(d) {
  const panel = el("div", "your-part-panel");
  panel.append(el("div", "your-part-did", didLine(d)));
  panel.append(el("div", "your-part-yours", yourPartLine(d)));
  return panel;
}

/* "Download CSV" on the case page — the exact Full-results rows for just
   this email, built with the same row-builders the inbox export uses so
   the two never disagree. */
function caseCsvButton(d) {
  const btn = el("button", "link-btn case-csv-btn", "Download CSV");
  btn.type = "button";
  btn.addEventListener("click", () => {
    const found = findRowById(d.email_id);
    const boardRow = found ? found.row : {
      email_id: d.email_id, reference: d.reference, subject: d.subject, from: d.from,
      category: d.category, status: d.status, review_reason: d.review_reason,
      decided_by: d.decided_by, defect_fields: d.defect_fields, has_defect: d.has_defect,
    };
    const comparisons = d.comparisons || [];
    const rows = comparisons.length
      ? comparisons.map((c) => fieldExportRow(boardRow, d, c))
      : [noComparisonExportRow(boardRow, d)];
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

  const yourPart = yourPartPanel(d);
  if (d.recheck) {
    renderVerdict(d, host, { reply: false, afterVerdict: yourPart });
    host.append(recheckCard(d.recheck));
    host.append(replyCard(d, d.recheck.reply_draft, "Follow-up reply, ready to send", d.reply_draft));
  } else {
    renderVerdict(d, host, { afterVerdict: yourPart });
  }

  host.append(reviewBar(d));

  if (FOCUS_CASE_HEADING) {
    FOCUS_CASE_HEADING = false;
    heading.focus();
  }
}

/* The seven-row table. Mismatches pinned to the top — the clerk's job is to
   find what is wrong, so what is wrong goes first. */
function seamTable(d) {
  const wrap = el("div", "seam-wrap");
  const head = el("div", "seam-head");
  head.innerHTML = `<div>Field</div><div class="h-si">Shipping Instruction</div><div class="h-seam"></div><div class="h-bl">Draft Bill of Lading</div>`;
  wrap.append(head);

  const rank = { mismatch: 0, unknown: 1, match: 2 };
  const rows = [...d.comparisons].sort((a, b) => rank[stateOf(a)] - rank[stateOf(b)]);

  for (const c of rows) {
    const st = stateOf(c);
    const row = el("div", "seam-row");
    row.dataset.state = st;

    const label = el("div", "f-label");
    label.append(el("span", null, c.label));
    row.append(label);

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
      label.append(btn);
      wrap.append(panel);
    }
  }
  return wrap;
}

/* Editable, auto-growing reply. Edits live in EDITS (session-only, keyed by
   email_id + which reply) so navigating away and back keeps them. Primary
   action opens a pre-filled compose window straight in Gmail; ClearDraft
   still never sends anything — the clerk checks it and presses Send. */
function replyCard(d, text, title, earlier) {
  const which = earlier ? "recheck" : "reply";
  const key = `${d.email_id || "adhoc"}::${which}`;
  const draftText = text || "";
  const startText = EDITS.has(key) ? EDITS.get(key) : draftText;

  const card = el("div", "reply-card");

  const head = el("div", "reply-head");
  head.append(el("h3", null, title));
  const metaRow = el("div", "reply-meta-row");
  metaRow.append(el("span", "reply-note", "Built from the checked values. Not written by a model."));
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
    const doneBar = document.querySelector(".done-bar");
    if (doneBar) doneBar.replaceWith(reviewBar(d));
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
    const doneBar = document.querySelector(".done-bar");
    if (doneBar) doneBar.replaceWith(reviewBar(d));
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
    const doneBar = document.querySelector(".done-bar");
    if (doneBar) doneBar.replaceWith(reviewBar(d));
    toast("Draft opened in your mail app. Check it, then press Send.");
  });
  foot.append(otherBtn);

  foot.append(el("span", "btn-hint", "ClearDraft never sends mail. You do."));
  card.append(foot);
  return card;
}

/* ── Done — next case ───────────────────────────────────────────
   Which board (Your mail / Sample) a case belongs to, and its filtered
   list order — the exact order renderBoard() shows for that source, tab
   and search query — so "Done — next" / "Skip" walk the list a clerk was
   actually looking at, not some other order. */
function boardFor(source) { return source === "mine" ? MINE.board : (DATA ? DATA.board : []); }

function computeFilteredList(source, tab, query) {
  const q = (query || "").trim().toLowerCase();
  return boardFor(source).filter((r) => tabOf(r) === tab && matchesQuery(r, q));
}

function findRowById(id) {
  const m = MINE.board.find((r) => r.email_id === id);
  if (m) return { row: m, source: "mine" };
  const s = DATA && DATA.board.find((r) => r.email_id === id);
  if (s) return { row: s, source: "sample" };
  return null;
}

/* If the case is part of the list currently on screen (same source, tab and
   search), that is the list to walk. Otherwise (opened another way, e.g.
   from the home page) fall back to the case's own source + tab, no query. */
function getCaseListContext(id) {
  const found = findRowById(id);
  if (!found) return null;
  const { row, source } = found;
  const ownTab = tabOf(row);

  if (SRC === source) {
    const activeList = computeFilteredList(SRC, TAB, QUERY);
    if (activeList.some((r) => r.email_id === id)) {
      return { source: SRC, tab: TAB, query: QUERY, list: activeList };
    }
  }
  return { source, tab: ownTab, query: "", list: computeFilteredList(source, ownTab, "") };
}

/* The next row, in list order, that has not been reviewed — wrapping
   around once. Returns -1 when every other row (and the current one) is
   already reviewed. */
function nextUnreviewedIndex(list, fromIdx) {
  const n = list.length;
  for (let step = 1; step <= n; step++) {
    const idx = (fromIdx + step) % n;
    if (!isReviewed(list[idx].email_id)) return idx;
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

function goToBoardTab(source, tab, query, message) {
  setSourceKey(source);
  TAB = tab;
  for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  QUERY = query || "";
  const searchInput = $("#inbox-search");
  if (searchInput) searchInput.value = QUERY;
  history.pushState(null, "", "#/board");
  route();
  if (message) toast(message);
}

function advanceCase(id, context, { requireUndone }) {
  const { list, source, tab, query } = context;
  const idx = list.findIndex((r) => r.email_id === id);
  if (idx === -1) { goToBoardTab(source, tab, query, null); return; }

  if (!requireUndone) {
    navigateToCase(list[(idx + 1) % list.length].email_id);
    return;
  }
  const nextIdx = nextUnreviewedIndex(list, idx);
  if (nextIdx !== -1) { navigateToCase(list[nextIdx].email_id); return; }

  /* Every discrepancy in this source is reviewed. Rather than dump the
     clerk back on an empty tab, walk them straight into the tab ClearDraft
     could not decide for itself — that queue is the one still owed
     attention, and it is easy to forget once the mismatch pile is clear. */
  if (tab === "mismatch") {
    const needsReview = computeFilteredList(source, "needs_review", "").find((r) => !isReviewed(r.email_id));
    if (needsReview) {
      TAB = "needs_review";
      for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === "needs_review"));
      navigateToCase(needsReview.email_id);
      toast(`Discrepancies done — now: ${TAB_LABELS.needs_review} (N)`);
      return;
    }
  }
  goToBoardTab(source, tab, query, `All reviewed in ${TAB_LABELS[tab]}.`);
}

/* A small, undoable confirmation: every save (button or the D key) tells the
   clerk what just got recorded and offers one click to take it back, so a
   fast click-through never becomes an un-fixable mistake once the page has
   already moved to the next case. */
function confirmToast(d) {
  const id = d.email_id;
  const ref = d.reference || id;
  toast(`Confirmed ${ref}`, {
    label: "Undo",
    onClick: () => { clearReview(id); navigateToCase(id); },
  });
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
  const review = getReview(id);
  const kind = verdictKind(d);

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
      clearReview(id);
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
      setReview(id, "done", "checked by hand: documents agree");
      confirmToast(d);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true });
    });
    actions.append(checkedBtn);

    const problemBtn = el("button", "btn btn-quiet", "I found a problem");
    problemBtn.type = "button";
    problemBtn.setAttribute("aria-expanded", "false");
    actions.append(problemBtn);

    bar.append(actions);

    const noteForm = buildReviewNoteForm(id, "Save and next case", (note) => {
      setReview(id, "done", `problem: ${note}`);
      confirmToast(d);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true });
    });
    bar.append(noteForm.form);

    problemBtn.addEventListener("click", () => {
      noteForm.form.hidden = !noteForm.form.hidden;
      problemBtn.setAttribute("aria-expanded", String(!noteForm.form.hidden));
      if (!noteForm.form.hidden) noteForm.textarea.focus();
    });
  } else {
    const replySent = kind === "mismatch" && REPLY_OPENED.has(id);
    if (replySent) bar.append(el("span", "chip chip-quiet", "Reply opened"));

    const actions = el("div", "done-bar-actions");

    const primaryLabel = replySent
      ? "Reply sent — next case"
      : kind === "mismatch" ? "Discrepancy confirmed — next case" : "All clear confirmed — next case";
    const confirmBtn = el("button", "btn btn-primary", primaryLabel);
    confirmBtn.type = "button";
    confirmBtn.addEventListener("click", () => {
      setReview(id, "confirmed");
      confirmToast(d);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true });
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
      setReview(id, "flagged", note);
      confirmToast(d);
      if (!context) return;
      advanceCase(id, context, { requireUndone: true });
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
    bar.append(el("div", "done-bar-pos", `Case ${idx + 1} of ${context.list.length} in ${TAB_LABELS[context.tab]}`));
  }

  bar.append(el("div", "done-bar-hint", "D confirm · N skip"));
  return bar;
}

/* "n" = Skip to next. "d" confirms — except on a needs-a-human case, where
   ClearDraft deliberately did not decide: there, "d" records nothing and
   just tells the clerk to pick an outcome below, so a reflexive keypress can
   never silently clear an escalation. Ignored while typing anywhere, or with
   a modifier held, so it never fights the reply textarea. */
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
    e.preventDefault();
    if (k === "n") {
      advanceCase(id, context, { requireUndone: false });
      return;
    }
    if (verdictKind(d) === "review") {
      toast("ClearDraft didn't decide this one — choose an outcome below.");
      return;
    }
    if (!isReviewed(id)) {
      setReview(id, "confirmed");
      confirmToast(d);
    }
    advanceCase(id, context, { requireUndone: true });
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

function recheckCard(rc) {
  const card = el("div", "recheck");
  const head = el("div", "recheck-head");
  head.append(el("h3", null, "Amended draft received, re-checked"));
  if (rc.demo) head.append(el("span", "chip chip-quiet", "demo document"));
  card.append(head);

  const order = ["newly_broken", "still_wrong", "unreadable", "fixed", "ok"];
  for (const kind of order) {
    const rows = rc.rows.filter((r) => r.outcome === kind);
    if (!rows.length) continue;
    const [label, tone] = OUTCOME[kind];
    const grp = el("div", `recheck-group tone-${tone}`);
    grp.append(el("div", "recheck-label", `${label} (${rows.length})`));
    for (const r of rows) {
      const line = el("div", "recheck-row");
      line.append(el("span", "recheck-field", r.label));
      if (kind === "ok") {
        line.append(el("span", "recheck-val", r.v2 || ""));
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

/* "Your checks of ClearDraft's answers" — only shown once at least one
   case has been reviewed. Reviews are a single flat store shared across
   both sources, so this counts every review regardless of which board it
   came from. */
function renderReviewsPanel(host) {
  const ids = Object.keys(REVIEWS);
  if (!ids.length) return;

  let confirmed = 0, flagged = 0;
  const flaggedList = [];
  for (const id of ids) {
    const r = REVIEWS[id];
    if (r.verdict === "confirmed") confirmed++;
    else if (r.verdict === "flagged") { flagged++; flaggedList.push({ id, note: r.note }); }
  }
  const total = ids.length;
  const agreementDenom = confirmed + flagged;

  const panel = el("div", "panel reviews-panel");
  panel.append(el("h3", null, "Your checks of ClearDraft's answers (this browser)"));
  panel.append(el("div", "sub", `You reviewed ${total} case${total === 1 ? "" : "s"}: ${confirmed} looked right, ${flagged} flagged as wrong`));
  if (agreementDenom > 0) {
    panel.append(el("div", "reviews-agreement", `Agreement: ${Math.round((confirmed / agreementDenom) * 100)}%`));
  }
  if (flaggedList.length) {
    const list = el("ul", "reviews-flagged-list");
    for (const f of flaggedList) {
      const found = findRowById(f.id);
      const li = el("li");
      const a = el("a", null, found ? found.row.reference : f.id);
      a.href = `#/case/${f.id}`;
      li.append(a);
      li.append(document.createTextNode(` — ${f.note}`));
      list.append(li);
    }
    panel.append(list);
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
  facts.textContent =
    `The model decided ${c.decided_by_model} emails and got ${c.model_wrong} wrong. ` +
    `It was asked only what the rules could not answer: ${c.calls} calls, ${c.tokens.toLocaleString()} tokens in total. ` +
    `Answers not found word for word in the document are discarded; ${c.gate_rejections} were discarded this run.`;
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

function renderCheckResult(data) {
  const host = checkResultHost();
  host.replaceChildren();

  const secs = typeof data.seconds === "number" ? data.seconds.toFixed(2) : "?";
  const calls = data.model ? data.model.calls : 0;
  const rejections = data.model ? data.model.gate_rejections : 0;
  host.append(el("div", "check-meta", `read in ${secs}s · model calls ${calls} · ${rejections} answer${rejections === 1 ? "" : "s"} rejected by the gate`));

  if (data.email_reading) {
    const er = data.email_reading;
    host.append(el("div", "check-meta", `The email reads as: ${String(er.category || "").toLowerCase()} / ${er.intent} (decided by ${er.decided_by})`));
  }

  host.append(printButton());

  renderVerdict(data, host);
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

/* ── Inbox view: Your mail vs the sample company inbox ────────
   One board view, two data sources. The segmented switch picks the
   source; everything else (tabs, search, counts, the case list) already
   runs on activeBoard(), so the rest of the render just decides which
   chrome to show — the empty "add your first email" state, or the normal
   tabs/search/list. */
function renderBoardView() {
  if (!DATA) return;
  const mineCount = MINE.board.length;
  const sampleCount = DATA.board.length;

  $("#mine-count").textContent = mineCount;
  $("#sample-count").textContent = sampleCount;
  $("#switch-mine").setAttribute("aria-pressed", String(SRC === "mine"));
  $("#switch-sample").setAttribute("aria-pressed", String(SRC === "sample"));

  const lede = $("#board-lede");
  if (SRC === "mine") {
    lede.textContent = mineCount
      ? `Your own mail, ${mineCount} message${mineCount === 1 ? "" : "s"} read by the live pipeline.`
      : "Add your own emails and watch the live pipeline check them.";
  } else {
    lede.textContent = `One shared mailbox, ${sampleCount} messages. Every one has been read, sorted, and — where documents were attached — checked field by field.`;
  }

  const showEmpty = SRC === "mine" && mineCount === 0;
  $("#board-normal").hidden = showEmpty;
  $("#mine-actions").hidden = SRC !== "mine";
  $("#mine-sample-actions").hidden = !(SRC === "mine" && showEmpty);

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
  }

  const doneLine = $("#done-count-line");
  if (doneLine) {
    const board = activeBoard();
    const reviewedCount = board.filter((r) => isReviewed(r.email_id)).length;
    const flaggedCount = board.filter((r) => { const rv = getReview(r.email_id); return rv && rv.verdict === "flagged"; }).length;
    doneLine.hidden = reviewedCount <= 0;
    if (reviewedCount > 0) doneLine.textContent = `${reviewedCount} reviewed · ${flaggedCount} flagged`;
  }
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
  for (const id of ["add-emails-btn", "try-samples-btn", "clear-mine-btn", "mail-file-input"]) {
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

async function uploadOne(file) {
  const fd = new FormData();
  fd.append("eml", file);
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
      const data = await uploadOne(file);
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

function initSourceSwitch() {
  $("#switch-mine").addEventListener("click", () => switchSource("mine"));
  $("#switch-sample").addEventListener("click", () => switchSource("sample"));
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

/* ── Add mail: "Drop .eml files" / "Paste an email" tabs ────────── */
function setAddMailTab(tab) {
  ADDMAIL_TAB = tab;
  $("#addmail-tab-drop").setAttribute("aria-selected", String(tab === "drop"));
  $("#addmail-tab-paste").setAttribute("aria-selected", String(tab === "paste"));
  $("#addmail-drop").hidden = tab !== "drop";
  $("#addmail-paste").hidden = tab !== "paste";
}

function initAddMailTabs() {
  $("#addmail-tab-drop").addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("drop"); });
  $("#addmail-tab-paste").addEventListener("click", () => { ADDMAIL_TAB_TOUCHED = true; setAddMailTab("paste"); });
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

  const fd = new FormData();
  if (subject) fd.append("subject", subject);
  fd.append("body", body);
  if (sender) fd.append("sender", sender);
  for (const f of files) fd.append("files", f);

  try {
    const data = await submitProcessEmail(fd);
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
  await importLocalMailIfAny();
  await loadAccountMail();
  renderAccountChip();
  if (location.hash.startsWith("#/board")) renderBoardView();
}

async function signOut() {
  try { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); } catch { /* proceed regardless */ }
  ACCOUNT.user = null;
  MINE = loadMine();
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

function route() {
  const h = location.hash;
  const views = { home: $("#view-home"), board: $("#view-board"), review: $("#view-review"), accuracy: $("#view-accuracy"), check: $("#view-check"), account: $("#view-account") };
  for (const v of Object.values(views)) v.hidden = true;

  let active = "home";
  if (h.startsWith("#/case/")) {
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
    setAccountMode("signup");
    const emailInput = $("#account-email-input");
    if (emailInput) emailInput.focus();
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
  initPasteForm();
  initAccountControl();
  initAccountForm();
  initCaseKeyboardNav();
  initExportMenu();

  const mineCard = $("#home-card-mine");
  if (mineCard) mineCard.addEventListener("click", () => setSourceKey("mine"));

  renderAccountChip();
  if (ACCOUNT.available && ACCOUNT.user) await afterSignedIn();

  addEventListener("hashchange", route);
  route();
})();
