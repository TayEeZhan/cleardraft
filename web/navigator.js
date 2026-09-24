/* "Find anything" — a search box that takes a clerk straight to the page,
   tab or control they typed about, in their own words.

   RULES FIRST, AI ONLY AS A FALLBACK, AND THE AI CAN ONLY CHOOSE FROM A
   FIXED LIST. Matching against web/navigator-destinations.json (a small,
   fixed index of every page/tab/control worth jumping to) is pure,
   deterministic string scoring — no network, no model. Only when that local
   match is weak or empty does this ask POST /api/navigate, and the server
   validates whatever the model answers against that exact same fixed list —
   see api/_navigate.py. The model can never send the clerk somewhere this
   file doesn't already know about.

   A standalone module, deliberately: app.js is being edited by other agents
   in parallel, so this file owns its own DOM (a dialog it builds itself),
   its own state, and only ever reaches into the existing page to read real
   ids/classes and click real controls — never duplicating what app.js's
   handlers already do. */

const $id = (id) => document.getElementById(id);
const $q = (sel, root = document) => root.querySelector(sel);

/* ── tiny DOM builder (no innerHTML with any dynamic text, ever) ────── */
function make(tag, opts = {}) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text != null) node.textContent = opts.text;
  if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
  return node;
}

function reducedMotion() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/* ── wait for something app.js's own (possibly async, hashchange-driven)
   render produces, without ever calling into app.js's private functions.
   Polls rather than assuming a fixed delay — app.js sometimes renders
   synchronously (a direct click handler) and sometimes only after the
   native "hashchange" event fires on the next task, and this file has no
   way to tell which is about to happen. */
function waitFor(getEl, { timeout = 1500, interval = 30 } = {}) {
  return new Promise((resolve) => {
    const start = Date.now();
    (function poll() {
      let node = null;
      try { node = getEl(); } catch { node = null; }
      if (node) { resolve(node); return; }
      if (Date.now() - start >= timeout) { resolve(null); return; }
      setTimeout(poll, interval);
    })();
  });
}

function setHash(hash) {
  if (location.hash !== hash) location.hash = hash;
}

/* Resolves once the named view section is the one showing — i.e. once
   app.js's own hashchange -> route() has actually run, not just once the
   hash string changed. Every board/check/account/etc. control below is
   static markup that exists in the DOM the whole time (see web/index.html);
   what route() controls is which <section class="view"> is visible. */
function onView(hash, viewId) {
  return () => {
    setHash(hash);
    return waitFor(() => {
      const view = $id(viewId);
      return view && !view.hidden ? view : null;
    });
  };
}

/* ── small, safe helpers used by more than one destination's action ──── */
function ensureMineSource() {
  const btn = $id("switch-mine");
  if (btn && btn.getAttribute("aria-pressed") !== "true") btn.click();
}

function ensureAddMailOpen() {
  ensureMineSource();
  const panel = $id("addmail-panel");
  const openBtn = $id("add-emails-btn");
  // Only toggle it open — the panel can already be showing (an empty "Your
  // mail" opens it by itself, or a clerk opened it earlier this session),
  // and add-emails-btn only exists once "Your mail" has emails in it.
  if (panel && panel.hidden && openBtn) openBtn.click();
}

function openExportMenu() {
  const menu = $id("export-menu");
  const btn = $id("export-btn");
  if (menu && btn && menu.hidden) btn.click();
}

/* The tabs, search box, triage banner and export menu all live inside
   #board-normal, which app.js hides whenever "Your mail" is the active
   source and empty (the default state for anyone new) — see app.js's
   renderBoardView(). Clicking a real control there would silently do
   nothing visible. Rather than land a clerk on an empty board, switch to
   the sample inbox (520 messages, always populated) so the destination
   they asked for actually has something to show. */
function boardHasContent() {
  const normal = $id("board-normal");
  return Boolean(normal && !normal.hidden);
}

function ensureBoardContent() {
  if (boardHasContent()) return;
  const sampleBtn = $id("switch-sample");
  if (sampleBtn) sampleBtn.click();
}

async function onBoardWithContent() {
  await onView("#/board", "view-board")();
  ensureBoardContent();
}

/* ── destination actions ──────────────────────────────────────────────
   Every id here must exist in web/navigator-destinations.json, and vice
   versa (see tests/test_navigate_api.py's id-parity check). Each `go()`:
     1. gets to the right page (waiting for app.js's own render, if any),
     2. clicks only SAFE, reversible real controls (a tab, a source switch,
        an open-the-menu button) — never a control with an external side
        effect (a download, a live model/API call, a destructive delete),
     3. returns the element to scroll to and ring-highlight, or null.
   `skipArrive: true` means the destination already moves focus itself
   (see the "start" entry) — arriving must never fight that. */
function tabGo(tabKey) {
  return {
    async go() {
      await onBoardWithContent();
      const btn = $q(`.tab[data-tab="${tabKey}"]`);
      if (btn) btn.click();
      return btn;
    },
  };
}

function intakeGo(tabId) {
  return {
    async go() {
      await onView("#/board", "view-board")();
      ensureAddMailOpen();
      const btn = $id(tabId);
      if (btn) btn.click();
      return btn;
    },
  };
}

function exportItemGo(itemId) {
  return {
    async go() {
      await onBoardWithContent();
      openExportMenu();
      // Never click the item itself — that starts a real download.
      return $id(itemId);
    },
  };
}

function sampleChoiceGo(btnId) {
  return {
    async go() {
      await onView("#/check", "view-check")();
      const toggle = $id("use-sample");
      if (toggle && toggle.getAttribute("aria-expanded") !== "true") toggle.click();
      // Never click the sample button itself — it fetches files and runs a
      // real check against the live API.
      return $id(btnId);
    },
  };
}

const ACTIONS = {
  home: {
    async go() {
      await onView("#/", "view-home")();
      return $q("#view-home h1");
    },
  },
  inbox: {
    async go() {
      await onView("#/board", "view-board")();
      return $id("board-heading");
    },
  },
  check: {
    async go() {
      await onView("#/check", "view-check")();
      return $q("#view-check h1");
    },
  },
  accuracy: {
    async go() {
      await onView("#/accuracy", "view-accuracy")();
      return $q("#view-accuracy h1");
    },
  },
  account: {
    async go() {
      await onView("#/account", "view-account")();
      return $id("account-email-input") || $id("account-title");
    },
  },
  learned: {
    async go() {
      await onView("#/learned", "view-learned")();
      return $id("learned-heading");
    },
  },

  "tab-mismatch": tabGo("mismatch"),
  "tab-needs-review": tabGo("needs_review"),
  "tab-cleared": tabGo("cleared"),
  "tab-other": tabGo("other"),

  "intake-drop": intakeGo("addmail-tab-drop"),
  "intake-paste": intakeGo("addmail-tab-paste"),
  "intake-dataset": intakeGo("addmail-tab-dataset"),
  scan: {
    async go() {
      await onView("#/board", "view-board")();
      ensureAddMailOpen();
      const tab = $id("addmail-tab-scan");
      if (tab) { tab.click(); return tab; }
      // Graceful skip: this build has no "Scan a photo" tab yet — land on
      // the add-mail panel itself rather than failing.
      return $q(".addmail-tabs") || $id("addmail-panel") || $id("add-emails-btn");
    },
  },

  start: {
    // startQueue() (the real Start button's handler) navigates straight
    // into a case and app.js focuses that case's own heading itself — our
    // own arrival highlight must never race that, so it is skipped here.
    skipArrive: true,
    async go() {
      await onBoardWithContent();
      const btn = $q("#triage-banner .triage-start-btn");
      if (btn) { btn.click(); return null; }
      return $id("triage-banner") || $id("board-heading");
    },
  },

  "search-inbox": {
    async go() {
      await onBoardWithContent();
      return $id("inbox-search");
    },
  },

  export: {
    async go() {
      await onBoardWithContent();
      openExportMenu();
      return $id("export-btn");
    },
  },
  "export-discrepancy": exportItemGo("export-discrepancy-csv"),
  "export-full": exportItemGo("export-full-csv"),
  "export-submission": exportItemGo("export-submission-json"),

  "source-sample": {
    async go() {
      await onView("#/board", "view-board")();
      const btn = $id("switch-sample");
      if (btn) btn.click();
      return btn;
    },
  },
  "source-mine": {
    async go() {
      await onView("#/board", "view-board")();
      const btn = $id("switch-mine");
      if (btn) btn.click();
      return btn;
    },
  },
  "try-sample-emails": {
    async go() {
      await onView("#/board", "view-board")();
      ensureMineSource();
      // Side-effecting (adds 6 real, live-checked emails) — reveal it,
      // never click it for the clerk.
      return $id("try-samples-btn") || $id("switch-mine");
    },
  },

  "check-use-sample": {
    async go() {
      await onView("#/check", "view-check")();
      const btn = $id("use-sample");
      if (btn && btn.getAttribute("aria-expanded") !== "true") btn.click();
      return btn;
    },
  },
  "check-sample-discrepancies": sampleChoiceGo("sample-discrepancies"),
  "check-sample-inbox": sampleChoiceGo("sample-inbox"),

  "add-emails": {
    async go() {
      await onView("#/board", "view-board")();
      ensureMineSource();
      const btn = $id("add-emails-btn");
      if (btn && btn.getAttribute("aria-expanded") !== "true") btn.click();
      return btn || $id("addmail-panel");
    },
  },
  "clear-your-mail": {
    async go() {
      await onView("#/board", "view-board")();
      ensureMineSource();
      // Destructive (a confirm() dialog, then deletes data) — reveal only.
      return $id("clear-mine-btn");
    },
  },
  "remove-uploaded-dataset": {
    async go() {
      await onView("#/board", "view-board")();
      const switchBtn = $id("switch-uploaded");
      if (!switchBtn || switchBtn.hidden) return $id("board-heading");
      switchBtn.click();
      return $id("remove-uploaded-btn");
    },
  },
  "sign-out": {
    async go() {
      const wrap = $id("account-user-wrap");
      if (!wrap || wrap.hidden) {
        // Not signed in — nothing to sign out of.
        await onView("#/account", "view-account")();
        return $id("account-title");
      }
      const emailBtn = $id("account-email-btn");
      if (emailBtn && emailBtn.getAttribute("aria-expanded") !== "true") emailBtn.click();
      return $id("account-signout-btn");
    },
  },
};

/* ── matching: deterministic, local, no network ───────────────────────
   Score = an exact/whole-phrase bonus, plus per-field token overlap
   (title weighted highest, then keywords, then hint), where each token
   pair can match exactly, by prefix, or — for typo tolerance — within a
   small edit-distance window. */
function levenshtein(a, b) {
  if (a === b) return 0;
  const al = a.length, bl = b.length;
  if (al === 0) return bl;
  if (bl === 0) return al;
  let prev = new Array(bl + 1);
  let curr = new Array(bl + 1);
  for (let j = 0; j <= bl; j++) prev[j] = j;
  for (let i = 1; i <= al; i++) {
    curr[0] = i;
    for (let j = 1; j <= bl; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
    }
    const tmp = prev; prev = curr; curr = tmp;
  }
  return prev[bl];
}

function tokenize(s) {
  return String(s || "").toLowerCase().trim().split(/[^a-z0-9]+/).filter(Boolean);
}

function tokenPairScore(qTok, tTok) {
  if (qTok === tTok) return 1;
  if (qTok.length >= 2 && tTok.length >= 2) {
    if (tTok.startsWith(qTok)) return 0.7;
    if (qTok.startsWith(tTok)) return 0.55;
  }
  const maxLen = Math.max(qTok.length, tTok.length);
  if (maxLen >= 4) {
    const tolerance = maxLen > 6 ? 2 : 1;
    if (levenshtein(qTok, tTok) <= tolerance) return 0.5;
  }
  return 0;
}

function fieldScore(queryTokens, text, weight) {
  const targetTokens = tokenize(text);
  if (!targetTokens.length || !queryTokens.length) return 0;
  let total = 0;
  for (const qTok of queryTokens) {
    let best = 0;
    for (const tTok of targetTokens) {
      const s = tokenPairScore(qTok, tTok);
      if (s > best) best = s;
    }
    total += best;
  }
  return (total / queryTokens.length) * weight;
}

function scoreDestination(query, dest) {
  const q = query.toLowerCase().trim();
  if (!q) return 0;

  let score = 0;
  if (q === dest.title.toLowerCase()) score += 100;

  // A whole keyword/synonym phrase matching (or containing) the whole
  // query is the strongest possible signal — it is how "upload company
  // inbox" picks the dataset-upload tab over the merely-token-similar
  // "Sample company inbox" switch.
  if (q.length >= 4) {
    const phrases = [dest.title, dest.hint, ...(dest.keywords || [])].map((s) => s.toLowerCase());
    for (const phrase of phrases) {
      if (phrase === q) { score += 60; break; }
      if (phrase.includes(q)) { score += 40; break; }
    }
  }

  const queryTokens = tokenize(q);
  if (!queryTokens.length) return score;

  score += fieldScore(queryTokens, dest.title, 18);
  score += fieldScore(queryTokens, dest.hint, 7);

  let bestKeyword = 0;
  for (const kw of dest.keywords || []) {
    const s = fieldScore(queryTokens, kw, 14);
    if (s > bestKeyword) bestKeyword = s;
  }
  score += bestKeyword;

  return score;
}

const LOCAL_MATCH_THRESHOLD = 24;
const MAX_LOCAL_RESULTS = 8;
// A lone, low-confidence fuzzy token match (one stray word coincidentally
// close to one keyword) scores only a few points — worth ignoring outright
// rather than cluttering the list with destinations that don't actually fit.
const MIN_RESULT_SCORE = 6;

function localSearch(query, destinations) {
  return destinations
    .map((dest) => ({ dest, score: scoreDestination(query, dest) }))
    .filter((r) => r.score >= MIN_RESULT_SCORE)
    .sort((a, b) => b.score - a.score)
    .slice(0, MAX_LOCAL_RESULTS);
}

/* ── the module ───────────────────────────────────────────────────────
   Everything below is deferred until the destination index has loaded, so
   a slow/failed fetch never leaves a half-wired dialog behind. */
let DESTINATIONS = [];
let DEST_BY_ID = new Map();

async function loadDestinations() {
  const res = await fetch(new URL("./navigator-destinations.json", import.meta.url));
  if (!res.ok) throw new Error(`navigator-destinations.json: HTTP ${res.status}`);
  const data = await res.json();
  const list = [];
  for (const entry of data) {
    if (!entry || typeof entry.id !== "string") continue;
    const action = ACTIONS[entry.id];
    if (!action) continue; // every JSON entry must have a matching action — see the ACTIONS table above
    list.push({ ...entry, action });
  }
  return list;
}

/* ── dialog UI ─────────────────────────────────────────────────────── */
let dialogEl, backdropEl, inputEl, listEl, statusEl, titleId;
let openerEl = null; // the "Find…" button, for returning focus on a plain close
let activeIndex = -1;
let currentResults = []; // [{kind:"dest", dest, score} | {kind:"ai", query}]
let searchSeq = 0;
let debounceTimer = null;
let aiRequestSeq = 0;
let highlightTimer = null;

function buildDialog() {
  backdropEl = make("div", { class: "nav-backdrop", attrs: { id: "nav-backdrop", hidden: "" } });
  dialogEl = make("div", {
    class: "nav-dialog",
    attrs: {
      id: "nav-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "nav-dialog-title",
    },
  });

  titleId = "nav-dialog-title";
  const title = make("h2", { class: "visually-hidden", text: "Find anything", attrs: { id: titleId } });

  const fieldWrap = make("div", { class: "nav-field" });
  const icon = make("span", { class: "nav-field-icon", attrs: { "aria-hidden": "true" } });
  icon.innerHTML = SEARCH_ICON_SVG; // static, trusted markup — never user text
  inputEl = make("input", {
    class: "nav-input",
    attrs: {
      type: "text", id: "nav-input", role: "combobox", "aria-expanded": "false",
      "aria-controls": "nav-listbox", "aria-autocomplete": "list", "aria-haspopup": "listbox",
      autocomplete: "off", spellcheck: "false", maxlength: "200",
      placeholder: "Try “download report”, “wrong ones”, “upload company inbox”…",
    },
  });
  fieldWrap.append(icon, inputEl);

  listEl = make("ul", { class: "nav-list", attrs: { id: "nav-listbox", role: "listbox", "aria-label": "Destinations" } });
  statusEl = make("div", { class: "nav-status", attrs: { "aria-live": "polite" } });

  const hint = make("div", { class: "nav-hint" });
  const hintParts = [
    ["↑↓", " to move, "],
    ["Enter", " to go, "],
    ["Esc", " to close"],
  ];
  for (const [kbd, rest] of hintParts) {
    hint.append(make("kbd", { class: "nav-kbd", text: kbd }), document.createTextNode(rest));
  }

  dialogEl.append(title, fieldWrap, listEl, statusEl, hint);
  backdropEl.append(dialogEl);
  document.body.append(backdropEl);

  backdropEl.addEventListener("mousedown", (e) => {
    if (e.target === backdropEl) closeDialog({ returnFocus: true });
  });
  dialogEl.addEventListener("keydown", onDialogKeydown);
  inputEl.addEventListener("input", onInput);
}

const SEARCH_ICON_SVG =
  '<svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8.6" cy="8.6" r="5.6"/><line x1="17" y1="17" x2="12.8" y2="12.8"/></svg>';

function isOpen() {
  return backdropEl && !backdropEl.hidden;
}

function openDialog(trigger) {
  if (!dialogEl) return;
  openerEl = trigger || document.activeElement;
  backdropEl.hidden = false;
  inputEl.value = "";
  statusEl.textContent = "";
  renderResults([]);
  document.body.classList.add("nav-open");
  // Focus after the element is visible, not before — some browsers refuse
  // to focus a node whose ancestor was [hidden] a frame ago.
  requestAnimationFrame(() => inputEl.focus());
}

function closeDialog({ returnFocus }) {
  if (!isOpen()) return;
  backdropEl.hidden = true;
  document.body.classList.remove("nav-open");
  clearTimeout(debounceTimer);
  aiRequestSeq++; // orphan any in-flight "ask the AI" request
  if (returnFocus && openerEl && typeof openerEl.focus === "function") openerEl.focus();
}

function onDialogKeydown(e) {
  if (e.key === "Escape") {
    e.preventDefault();
    closeDialog({ returnFocus: true });
    return;
  }
  if (e.key === "Tab") {
    // A one-field dialog: trap Tab on the input rather than building a
    // multi-stop focus cycle for a single interactive control.
    e.preventDefault();
    inputEl.focus();
    return;
  }
  if (e.key === "ArrowDown") {
    e.preventDefault();
    moveActive(1);
    return;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    moveActive(-1);
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    if (activeIndex >= 0 && currentResults[activeIndex]) choose(currentResults[activeIndex]);
  }
}

function moveActive(delta) {
  if (!currentResults.length) return;
  activeIndex = (activeIndex + delta + currentResults.length) % currentResults.length;
  updateActiveDescendant();
}

function updateActiveDescendant() {
  const rows = listEl.querySelectorAll("[role=\"option\"]");
  rows.forEach((row, i) => {
    const on = i === activeIndex;
    row.setAttribute("aria-selected", String(on));
    row.classList.toggle("nav-row-active", on);
    if (on) {
      inputEl.setAttribute("aria-activedescendant", row.id);
      row.scrollIntoView({ block: "nearest" });
    }
  });
  if (activeIndex < 0) inputEl.removeAttribute("aria-activedescendant");
}

function resultRow(index, result) {
  const row = make("li", {
    class: "nav-row",
    attrs: { role: "option", id: `nav-opt-${index}`, "aria-selected": "false" },
  });
  if (result.kind === "ai") {
    row.classList.add("nav-row-ai");
    row.append(
      make("span", { class: "nav-row-ai-icon", text: "AI", attrs: { "aria-hidden": "true" } }),
      make("span", { class: "nav-row-title", text: `Ask ClearDraft AI: “${result.query}”` }),
    );
  } else {
    row.append(
      make("span", { class: "nav-row-title", text: result.dest.title }),
      make("span", { class: "nav-row-hint", text: result.dest.hint }),
    );
  }
  row.addEventListener("mouseenter", () => { activeIndex = index; updateActiveDescendant(); });
  row.addEventListener("mousedown", (e) => { e.preventDefault(); choose(result); });
  return row;
}

function renderResults(results) {
  currentResults = results;
  activeIndex = results.length ? 0 : -1;
  listEl.replaceChildren();
  results.forEach((r, i) => listEl.append(resultRow(i, r)));
  updateActiveDescendant();
  inputEl.setAttribute("aria-expanded", String(results.length > 0));
}

function onInput() {
  clearTimeout(debounceTimer);
  const query = inputEl.value;
  debounceTimer = setTimeout(() => runSearch(query), 80);
}

function runSearch(rawQuery) {
  const mySeq = ++searchSeq;
  const query = rawQuery.trim();
  statusEl.textContent = "";

  if (!query) { renderResults([]); return; }

  const localHits = localSearch(query, DESTINATIONS);
  const topScore = localHits.length ? localHits[0].score : 0;
  const results = localHits.map((r) => ({ kind: "dest", dest: r.dest, score: r.score }));

  // Offer the AI row whenever the best local match is weak, or there was
  // no local match at all — never when a clean local match already exists.
  if (!localHits.length || topScore < LOCAL_MATCH_THRESHOLD) {
    results.push({ kind: "ai", query });
  }

  if (mySeq !== searchSeq) return; // a newer keystroke already superseded this
  renderResults(results);
}

/* ── choosing a result: close, move there smoothly, arrive ───────────── */
async function choose(result) {
  if (result.kind === "ai") {
    await askAi(result.query);
    return;
  }
  closeDialog({ returnFocus: false }); // focus is about to land on the destination itself, not the opener
  await goTo(result.dest);
}

/* Runs dest.action.go() exactly once, wrapped in a View Transition when one
   is available and the browser actually starts it promptly. Browsers can
   defer or never invoke a startViewTransition() callback at all when the
   document isn't currently visible for compositing (backgrounded tab,
   about to be un-hidden, etc.) — real navigation must never be hostage to
   that, so a short grace period falls back to running the action directly.
   `ran` guards against ever calling the action twice, whichever path wins. */
function runNavigation(dest) {
  return new Promise((resolve) => {
    let ran = false;
    // Async on purpose: when the View Transition callback IS the one that
    // runs this, its returned promise is what startViewTransition uses to
    // time the crossfade — so the normal case still gets the full-navigation
    // crossfade this was written for, not just a same-tick no-op animation.
    const runOnce = async () => {
      if (ran) return;
      ran = true;
      resolve(await dest.action.go());
    };

    let vt = null;
    try {
      vt = document.startViewTransition(runOnce);
    } catch {
      vt = null;
    }
    if (vt) vt.updateCallbackDone.catch(() => {}); // swallow — runOnce()'s own result is what matters

    setTimeout(runOnce, 150);
  });
}

async function goTo(dest) {
  const reduced = reducedMotion();
  const target = !reduced && document.startViewTransition
    ? await runNavigation(dest)
    : await dest.action.go();

  if (dest.action.skipArrive) return;
  arrive(target, reduced);
}

function arrive(target, reduced) {
  if (!target) return;
  if (highlightTimer) clearTimeout(highlightTimer);
  const prior = document.querySelector(".nav-arrived");
  if (prior && prior !== target) prior.classList.remove("nav-arrived");

  target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });

  const settle = () => {
    target.classList.add("nav-arrived");
    focusProgrammatically(target);
    highlightTimer = setTimeout(() => target.classList.remove("nav-arrived"), 1200);
  };
  // Give the smooth scroll a moment to actually start before drawing the
  // eye to the ring, same as any "scroll, then point at it" sequence;
  // instant (reduced-motion) scrolling needs no such delay.
  setTimeout(settle, reduced ? 0 : 220);
}

function focusProgrammatically(el) {
  const hadTabIndex = el.hasAttribute("tabindex");
  if (!hadTabIndex) el.setAttribute("tabindex", "-1");
  el.focus({ preventScroll: true });
  if (!hadTabIndex) {
    const cleanup = () => { el.removeAttribute("tabindex"); el.removeEventListener("blur", cleanup); };
    el.addEventListener("blur", cleanup, { once: true });
  }
}

/* ── AI fallback: POST /api/navigate, id validated server-side against the
   exact same fixed list this file loaded its destinations from ─────── */
async function askAi(query) {
  const mySeq = ++aiRequestSeq;
  statusEl.textContent = "Asking ClearDraft AI…";
  renderResults([{ kind: "ai", query }]);

  let res;
  try {
    res = await fetch("/api/navigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
  } catch {
    if (mySeq !== aiRequestSeq) return;
    statusEl.textContent = "Could not reach the server. Try other words.";
    return;
  }
  if (mySeq !== aiRequestSeq) return; // the dialog moved on (closed, or a newer query) — drop this answer

  if (res.status === 503) {
    statusEl.textContent = "AI help is off right now — try other words.";
    return;
  }
  if (!res.ok) {
    statusEl.textContent = "Could not ask ClearDraft AI. Try other words.";
    return;
  }

  const body = await res.json().catch(() => ({}));
  const dest = typeof body.id === "string" ? DEST_BY_ID.get(body.id) : null;
  if (!dest) {
    statusEl.textContent = "ClearDraft AI wasn't sure either — try other words.";
    return;
  }

  statusEl.textContent = "";
  closeDialog({ returnFocus: false });
  await goTo(dest);
}

/* ── header entry point + keyboard shortcut ───────────────────────────
   Ctrl+K / ⌘K on purpose — "/" is already the inbox search shortcut
   (see web/app.js's initSearch()), so this feature must never claim it. */
function wireOpener() {
  const btn = $id("find-btn");
  if (btn) btn.addEventListener("click", () => openDialog(btn));

  document.addEventListener("keydown", (e) => {
    const key = e.key.toLowerCase();
    if (key !== "k") return;
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
    e.preventDefault();
    if (isOpen()) { inputEl.focus(); return; }
    openDialog(btn);
  });
}

(async function boot() {
  try {
    DESTINATIONS = await loadDestinations();
  } catch {
    // No destination index, no navigator — the rest of the app is
    // unaffected either way, so fail quietly rather than breaking the page.
    return;
  }
  DEST_BY_ID = new Map(DESTINATIONS.map((d) => [d.id, d]));
  buildDialog();
  wireOpener();
})();
