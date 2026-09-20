/* ClearDraft UI.
   Prefers the live API; falls back to the exported snapshot so the demo
   cannot break in front of a judge because a free-tier server was cold. */

const $ = (s, r = document) => r.querySelector(s);
const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let DATA = null;
let TAB = "mismatch";

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
};

function renderBoard() {
  const list = $("#case-list");
  list.replaceChildren();
  const rows = DATA.board.filter((r) => tabOf(r) === TAB);

  if (!rows.length) { list.append(el("div", "empty", "Nothing here.")); return; }

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

function renderReview(id) {
  const d = DATA.detail[id];
  const host = $("#review-body");
  host.replaceChildren();
  if (!d) { host.append(el("div", "empty", "Not found.")); return; }

  const head = el("div", "case-head");
  head.innerHTML = `
    <div class="ref">${esc(d.reference)}</div>
    <h2>${esc(d.subject)}</h2>
    <div class="case-meta">
      <span>${esc(d.from)}</span>
      <span>${esc(d.category.replace(/_/g, " ").toLowerCase())}</span>
      <span>decided by ${esc(d.decided_by)}</span>
    </div>`;
  host.append(head);

  const kind = d.status === "MISMATCH" ? "mismatch" : d.status === "NEEDS_REVIEW" ? "review" : "match";
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
  host.append(replyCard(d));
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
        line.innerHTML = `<b>${side.toUpperCase()}</b>  ${esc(fv.raw)}`;
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

function replyCard(d) {
  const card = el("div", "reply-card");
  const head = el("div", "reply-head");
  head.append(el("h3", null, "Reply, ready to send"));
  head.append(el("div", "reply-note", "Built from the checked values. Not written by a model."));
  card.append(head);

  const body = el("div", "reply-body", d.reply_draft || "");
  card.append(body);

  const foot = el("div", "reply-foot");
  const btn = el("button", "btn btn-primary", "Copy reply");
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(d.reply_draft || "");
      toast("Reply copied. Paste it into your mail client and send.");
    } catch {
      const r = document.createRange();
      r.selectNodeContents(body);
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      toast("Selected — press Ctrl+C to copy.");
    }
  });
  foot.append(btn);
  foot.append(el("span", "btn-hint", "ClearDraft never sends mail. You do."));
  card.append(foot);
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

  const note = el("div", "note");
  note.innerHTML = `<b>How to read these numbers.</b> They are measured on every run against the
    sample inbox, not typed in. The model is a fallback: it is asked only for a field the
    format readers could not find, and anything it returns must appear word for word in the
    document or it is thrown away. On this run it was consulted
    <b>${s.model.calls} time${s.model.calls === 1 ? "" : "s"}</b>.`;
  host.append(note);
}

function route() {
  const h = location.hash || "#/board";
  const views = { board: $("#view-board"), review: $("#view-review"), accuracy: $("#view-accuracy") };
  for (const v of Object.values(views)) v.hidden = true;

  let active = "board";
  if (h.startsWith("#/case/")) {
    active = "review";
    views.review.hidden = false;
    renderReview(h.slice("#/case/".length));
  } else if (h.startsWith("#/accuracy")) {
    active = "accuracy";
    views.accuracy.hidden = false;
    renderAccuracy();
  } else {
    views.board.hidden = false;
    renderBoard();
  }

  for (const a of document.querySelectorAll(".nav-link")) {
    const on = (a.dataset.route === "accuracy") === (active === "accuracy");
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
  try {
    DATA = await load();
  } catch {
    $("#case-list").append(el("div", "empty", "Could not load results. Run: python scripts/export_ui_data.py"));
    return;
  }

  $("#total-count").textContent = DATA.board.length;
  for (const [k, v] of Object.entries(DATA.counts)) {
    const n = $(`#n-${k}`);
    if (n) n.textContent = v;
  }
  for (const b of document.querySelectorAll(".tab")) {
    b.addEventListener("click", () => setTab(b.dataset.tab));
  }
  addEventListener("hashchange", route);
  route();
})();
