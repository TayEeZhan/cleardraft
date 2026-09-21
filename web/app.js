/* ClearDraft UI.
   Prefers the live API; falls back to the exported snapshot so the demo
   cannot break in front of a judge because a free-tier server was cold. */

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
  try {
    const r = await fetch("/api/emails", { signal: AbortSignal.timeout(2500) });
    if (r.ok) {
      const live = await r.json();
      if (live && live.emails) { setSource(true, "live api"); return live; }
    }
  } catch { /* fall through to the snapshot */ }
  const r = await fetch("public/data.json");
  if (!r.ok) throw new Error("could not load results");
  setSource(false, "snapshot");
  return r.json();
}

function setSource(live, label) {
  const chip = $("#source-chip");
  chip.dataset.live = String(live);
  $("#source-text").textContent = label;
  chip.title = live
    ? "Reading from the live API"
    : "Reading the exported snapshot of a full 520-email run";
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
    a.href = `#/case/${r.email_id}`;
    a.setAttribute("role", "listitem");
    a.append(el("div", "case-ref", r.reference));
    a.append(el("div", "case-subject", r.subject));

    const tags = el("div", "case-fields");
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

function renderVerdict(d, host, { reply = true } = {}) {
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

  if (d.comparisons.length) host.append(seamTable(d));
  if (reply) host.append(replyCard(d, d.reply_draft, "Reply, ready to send", null));
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
  head.append(el("h1", null, d.subject));
  const meta = el("div", "case-meta");
  meta.append(el("span", null, d.from));
  meta.append(el("span", null, d.category.replace(/_/g, " ").toLowerCase()));
  meta.append(el("span", null, `decided by ${d.decided_by}`));
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
  host.append(printButton());
  host.append(originalEmailDetails(d));

  if (d.recheck) {
    renderVerdict(d, host, { reply: false });
    host.append(recheckCard(d.recheck));
    host.append(replyCard(d, d.recheck.reply_draft, "Follow-up reply, ready to send", d.reply_draft));
  } else {
    renderVerdict(d, host);
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
       defending; a row that differs or could not be read does. */
    const provable = st !== "match" && ((c.si && c.si.raw) || (c.bl && c.bl.raw));
    if (provable) {
      const panel = el("div", "proof");
      panel.hidden = true;
      for (const side of ["si", "bl"]) {
        const fv = c[side];
        if (!fv || !fv.raw) continue;
        const line = el("div", "proof-line");
        line.append(el("b", null, side.toUpperCase()));
        line.append(document.createTextNode(`  ${fv.raw}`));
        panel.append(line);
        panel.append(el("div", "proof-src", `${fv.source}${fv.line_no ? `, line ${fv.line_no}` : ""}`));
      }
      const btn = el("button", "proof-btn", "where this came from");
      btn.setAttribute("aria-expanded", "false");
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

/* One primary action: open a real, pre-filled draft in the clerk's own mail
   app. It is a mailto: link - ClearDraft still cannot send anything; the
   clerk's own client opens with the text in it, and the clerk presses Send. */
function replyCard(d, text, title, earlier) {
  const card = el("div", "reply-card");
  const head = el("div", "reply-head");
  head.append(el("h3", null, title));
  head.append(el("div", "reply-note", "Built from the checked values. Not written by a model."));
  card.append(head);

  const body = el("div", "reply-body", text || "");
  card.append(body);

  if (earlier) {
    const past = el("details", "reply-past");
    past.append(el("summary", null, "First discrepancy note, already sent"));
    past.append(el("div", "reply-body", earlier));
    card.append(past);
  }

  const foot = el("div", "reply-foot");
  const subject = /^re[:_]/i.test(d.subject) ? d.subject : `RE: ${d.subject}`;
  const open = el("a", "btn btn-primary", "Open draft in mail app");
  open.href = `mailto:${encodeURIComponent(d.from)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(text || "")}`;
  open.addEventListener("click", () => toast("Draft opened in your mail app. Check it, then press Send."));
  foot.append(open);

  const copy = el("button", "btn btn-quiet", "Copy text");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text || "");
      toast("Reply copied.");
    } catch {
      const r = document.createRange();
      r.selectNodeContents(body);
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      toast("Selected - press Ctrl+C to copy.");
    }
  });
  foot.append(copy);
  foot.append(el("span", "btn-hint", "ClearDraft never sends mail. You do."));
  card.append(foot);
  return card;
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

function renderAccuracy() {
  const s = DATA.stats;
  const host = $("#accuracy-body");
  host.replaceChildren();

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
  $("#mine-empty").hidden = !showEmpty;
  $("#board-normal").hidden = showEmpty;
  $("#mine-actions").hidden = SRC !== "mine";

  if (!showEmpty) {
    updateTabCounts();
    renderBoard();
  }
}

function switchSource(s) {
  if (SRC === s) return;
  setSourceKey(s);
  clearUploadPanels();
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
  if (idx >= 0) MINE.board[idx] = board; else MINE.board.unshift(board);
  MINE.detail[board.email_id] = detail;
}

async function uploadOne(file) {
  const fd = new FormData();
  fd.append("eml", file);
  let r;
  try {
    r = await fetch("/api/process-email", { method: "POST", body: fd });
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

  for (let i = 0; i < emlFiles.length; i++) {
    const file = emlFiles[i];
    progress.textContent = `Reading ${i + 1} of ${emlFiles.length} — ${file.name}`;
    try {
      const data = await uploadOne(file);
      mergeMineResult(data);
      saveMine();
      successCount++;
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
    toast(`${successCount} email${successCount === 1 ? "" : "s"} read${mismatchCount ? ` · ${mismatchCount} with a discrepancy` : ""}`);
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

  $("#add-emails-btn").addEventListener("click", () => input.click());
  $("#try-samples-btn").addEventListener("click", trySampleEmails);
  $("#show-sample-inbox-btn").addEventListener("click", () => switchSource("sample"));

  $("#clear-mine-btn").addEventListener("click", () => {
    if (UPLOADING) return;
    if (!confirm("Clear all of your uploaded mail? This cannot be undone.")) return;
    MINE = { board: [], detail: {} };
    saveMine();
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

function route() {
  const h = location.hash;
  const views = { home: $("#view-home"), board: $("#view-board"), review: $("#view-review"), accuracy: $("#view-accuracy"), check: $("#view-check") };
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
  window.scrollTo(0, 0);
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.dataset.show = "true";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.dataset.show = "false"; }, 3200);
}

(async function boot() {
  MINE = loadMine();
  SRC = getSourceKey();

  try {
    DATA = await load();
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

  const mineCard = $("#home-card-mine");
  if (mineCard) mineCard.addEventListener("click", () => setSourceKey("mine"));

  addEventListener("hashchange", route);
  route();
})();
