/* ClearDraft — "Scan a document (photo)" intake tab.
   Not a module (see index.html: loaded with `defer`, after app.js's own
   `type="module"` script, so it always runs after app.js's top-level setup
   but does not share app.js's private module scope). Talks to app.js only
   through the one hook it exposes on `window` (window.clearDraftSubmitScan
   — see app.js, right after initPasteForm) and through the DOM.

   Design principle this file exists to serve: AI never decides. A photo is
   read by the model into plain text, but nothing from that reading enters
   the pipeline until a clerk has looked at the photo and the text side by
   side and pressed "Text looks right — check it". That confirmation is the
   verification gate for AI-read text, the same way core/extract.py's
   verify_against_source() gates a model field reading against the source
   document — just done by a human instead of a string match, because a
   photo has no ground-truth text to match against.

   Flow:
     1. Clerk picks/drops photos (JPEG/PNG/WEBP).
     2. Each is downscaled client-side (canvas, max 2000px long edge, JPEG
        q0.85) so a phone photo fits comfortably under POST /api/scan's 4 MB
        cap, then POSTed one at a time.
     3. Each photo gets an editable draft: thumbnail, extracted text, and a
        required "This is: SI / BL / the email" choice.
     4. On confirm, the SI draft(s) become scan_SI.txt, the BL draft(s)
        become scan_BL.txt, and the email draft (if any) becomes the email
        body — sent through window.clearDraftSubmitScan(), i.e. the exact
        same /api/process-email + Your-mail merge as "Paste an email".
     5. The resulting case is flagged (by email_id, in localStorage) so its
        case page can show "Text from a photo, checked by you" — see
        initPhotoNoteObserver() below, which never touches app.js's
        rendering code directly. */

(function () {
  "use strict";

  /* ── Tiny local DOM helpers (deliberately not shared with app.js — this
     file is a classic script, app.js is a module with its own private
     scope, so there is nothing to collide with). Same shape as app.js's
     own so the two files read the same way. ── */
  const $ = (s, r) => (r || document).querySelector(s);
  const el = (t, c, txt) => {
    const n = document.createElement(t);
    if (c) n.className = c;
    if (txt != null) n.textContent = txt;
    return n;
  };

  const MAX_LONG_EDGE = 2000;
  const JPEG_QUALITY = 0.85;
  const MAX_FILES_PER_BATCH = 12; // a generous cap so one drop can't wedge the UI
  const PHOTO_CASES_KEY = "cleardraft.scan.photoCases.v1";

  const DOC_TYPES = [
    { value: "SI", label: "Shipping Instruction" },
    { value: "BL", label: "Draft Bill of Lading" },
    { value: "EMAIL", label: "The email text" },
  ];

  /* One entry per photo the clerk has added this session (cleared on a
     successful submit). Shape:
       { id, thumbUrl, status: "reading"|"done"|"error", text, reason,
         docType: null|"SI"|"BL"|"EMAIL" } */
  let ITEMS = [];
  let NEXT_ID = 1;
  let BUSY = false; // a confirm submit is in flight

  /* ── Downscale ─────────────────────────────────────────────────── */

  /** Resolves to a downscaled JPEG Blob, or rejects if the file can't be
   * decoded as an image at all (e.g. a corrupt file the OS let through the
   * picker). Never throws synchronously. */
  function downscaleImage(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const longEdge = Math.max(img.naturalWidth, img.naturalHeight) || 1;
        const scale = Math.min(1, MAX_LONG_EDGE / longEdge);
        const width = Math.max(1, Math.round(img.naturalWidth * scale));
        const height = Math.max(1, Math.round(img.naturalHeight * scale));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        if (!ctx) { URL.revokeObjectURL(url); reject(new Error("no canvas context")); return; }
        ctx.drawImage(img, 0, 0, width, height);
        URL.revokeObjectURL(url);
        canvas.toBlob(
          (blob) => { if (blob) resolve(blob); else reject(new Error("could not encode photo")); },
          "image/jpeg",
          JPEG_QUALITY
        );
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("could not read photo")); };
      img.src = url;
    });
  }

  /* ── POST /api/scan ────────────────────────────────────────────── */

  async function postScan(blob, filename) {
    const fd = new FormData();
    fd.append("file", blob, filename);
    let r;
    try {
      r = await fetch("/api/scan", { method: "POST", body: fd, credentials: "same-origin" });
    } catch {
      return { ok: false, reason: "Could not reach the server. Type the text yourself." };
    }
    if (r.status === 404 || r.status === 405 || r.status === 501) {
      return { ok: false, reason: "This preview has no live backend. Type the text yourself." };
    }
    const ct = r.headers.get("content-type") || "";
    let body = null;
    if (ct.includes("json")) body = await r.json().catch(() => null);
    if (!r.ok) {
      const detail = (body && (body.detail || body.error)) || `HTTP ${r.status}`;
      return { ok: false, reason: detail };
    }
    if (!body || typeof body.text !== "string") {
      return { ok: false, reason: "Unexpected response from the server. Type the text yourself." };
    }
    return { ok: true, text: body.text };
  }

  /* ── Reading heuristics ────────────────────────────────────────── */

  /** True when there is little or nothing usable to check — an empty
   * reading, or one where most lines are the model's own "[unreadable]"
   * marker. Recomputed on every edit so fixing the text clears the
   * warning. */
  function looksUnreadable(text) {
    const trimmed = (text || "").trim();
    if (!trimmed) return true;
    const lines = trimmed.split(/\r?\n/).filter((l) => l.trim());
    if (!lines.length) return true;
    const unreadable = lines.filter((l) => /\[unreadable\]/i.test(l)).length;
    return unreadable / lines.length >= 0.6;
  }

  /* ── localStorage: which cases came from a photo ──────────────────
     A display-only flag, not a source of truth — losing it just means the
     case page's note doesn't show; nothing else depends on it. Kept here
     rather than in app.js's own mail storage so this file's footprint on
     app.js stays a single small hook (see the top of this file). */

  function loadPhotoCases() {
    try {
      const raw = localStorage.getItem(PHOTO_CASES_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch { return []; }
  }

  function markPhotoCase(emailId) {
    if (!emailId) return;
    try {
      const ids = loadPhotoCases();
      if (!ids.includes(emailId)) {
        ids.push(emailId);
        while (ids.length > 200) ids.shift(); // same order of magnitude as the mailbox cap
        localStorage.setItem(PHOTO_CASES_KEY, JSON.stringify(ids));
      }
    } catch { /* storage unavailable or full — the note just won't show */ }
  }

  function isPhotoCase(emailId) {
    return loadPhotoCases().includes(emailId);
  }

  /* ── Case-page note, without touching app.js's rendering code ──────
     app.js's renderReview() owns #review-body and rebuilds it from
     scratch on every case view (including a live re-render once a learned
     pair applies). Rather than edit that function, this observes the
     container and re-adds the note whenever it's missing — self-healing
     across every re-render, and zero coupling to app.js's internals. */

  function currentCaseId() {
    const h = location.hash;
    return h.startsWith("#/case/") ? h.slice("#/case/".length) : null;
  }

  function injectPhotoNoteIfNeeded() {
    const id = currentCaseId();
    const host = document.getElementById("review-body");
    if (!host || !id || !isPhotoCase(id)) return;
    if (host.querySelector(".scan-photo-note")) return;
    const head = host.querySelector(".case-head");
    const note = el("div", "scan-photo-note", "Text from a photo, checked by you");
    if (head && head.parentNode === host) head.insertAdjacentElement("afterend", note);
    else host.insertBefore(note, host.firstChild);
  }

  function initPhotoNoteObserver() {
    const host = document.getElementById("review-body");
    if (!host || typeof MutationObserver === "undefined") return;
    new MutationObserver(injectPhotoNoteIfNeeded).observe(host, { childList: true });
    window.addEventListener("hashchange", injectPhotoNoteIfNeeded);
    injectPhotoNoteIfNeeded(); // in case this script finished loading after the case view did
  }

  /* ── Toast (reuses the site-wide #toast element app.js already shows;
     kept local and simple — no action-button support — so this file never
     calls into app.js's private toast()). ── */

  let toastTimer;
  function scanToast(msg) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.replaceChildren(document.createTextNode(msg));
    t.classList.remove("toast-has-action");
    t.dataset.show = "true";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.dataset.show = "false"; }, 3200);
  }

  /* ── Rendering ─────────────────────────────────────────────────── */

  function textareaId(id) { return `scan-text-${id}`; }
  function radioName(id) { return `scan-doctype-${id}`; }

  function itemStatusLine(item) {
    if (item.status === "reading") return "Reading…";
    if (item.status === "error") return `Could not read this photo — ${item.reason || "try again"}.`;
    return "Read. Check it against the photo below.";
  }

  function buildItemCard(item, index) {
    const card = el("div", "scan-item");
    card.dataset.id = String(item.id);

    const thumbWrap = el("div", "scan-thumb-wrap");
    if (item.thumbUrl) {
      const img = el("img", "scan-thumb");
      img.src = item.thumbUrl;
      img.alt = `Photo ${index + 1}`;
      thumbWrap.append(img);
    } else {
      thumbWrap.append(el("div", "scan-thumb scan-thumb-empty", `Photo ${index + 1}`));
    }
    card.append(thumbWrap);

    const body = el("div", "scan-item-body");

    const statusRow = el("div", `scan-item-status scan-item-status-${item.status}`, itemStatusLine(item));
    body.append(statusRow);

    if (item.status !== "reading") {
      const label = el("label", "field-label", "Extracted text — check and fix");
      label.htmlFor = textareaId(item.id);
      body.append(label);

      const ta = el("textarea", "field-input scan-textarea");
      ta.id = textareaId(item.id);
      ta.rows = 7;
      ta.spellcheck = true;
      ta.value = item.text || "";
      ta.setAttribute("aria-describedby", `${textareaId(item.id)}-note`);
      ta.addEventListener("input", () => {
        item.text = ta.value;
        warnBox.hidden = !looksUnreadable(item.text);
      });
      body.append(ta);

      const warnBox = el(
        "div", "scan-warning",
        "We couldn't read much of this photo. Try a sharper, flatter, well-lit photo."
      );
      warnBox.hidden = !looksUnreadable(item.text);
      warnBox.setAttribute("role", "status");
      body.append(warnBox);

      const note = el(
        "p", "scan-item-note",
        "Read by AI from your photo — check every line against the photo before you continue. Fix anything that is wrong."
      );
      note.id = `${textareaId(item.id)}-note`;
      body.append(note);

      const fieldset = el("fieldset", "scan-doctype");
      fieldset.dataset.id = String(item.id);
      fieldset.append(el("legend", null, "This is:"));
      for (const opt of DOC_TYPES) {
        const optLabel = el("label", "scan-doctype-opt");
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = radioName(item.id);
        radio.value = opt.value;
        radio.checked = item.docType === opt.value;
        radio.addEventListener("change", () => {
          item.docType = opt.value;
          fieldset.classList.remove("scan-doctype-invalid");
        });
        optLabel.append(radio, document.createTextNode(" " + opt.label));
        fieldset.append(optLabel);
      }
      body.append(fieldset);
    }

    const removeBtn = el("button", "link-btn scan-remove-btn", `Remove photo ${index + 1}`);
    removeBtn.type = "button";
    removeBtn.addEventListener("click", () => removeItem(item.id));
    body.append(removeBtn);

    card.append(body);
    return card;
  }

  function render() {
    const list = $("#scan-items");
    const actions = $("#scan-actions");
    if (!list || !actions) return;
    list.replaceChildren();
    ITEMS.forEach((item, index) => list.append(buildItemCard(item, index)));
    actions.hidden = ITEMS.length === 0;
    const confirmBtn = $("#scan-confirm-btn");
    if (confirmBtn) {
      const anyReading = ITEMS.some((it) => it.status === "reading");
      confirmBtn.disabled = BUSY || anyReading || ITEMS.length === 0;
      confirmBtn.textContent = BUSY ? "Checking…" : "Text looks right — check it";
    }
  }

  function removeItem(id) {
    const item = ITEMS.find((it) => it.id === id);
    if (item && item.thumbUrl) URL.revokeObjectURL(item.thumbUrl);
    ITEMS = ITEMS.filter((it) => it.id !== id);
    render();
  }

  function resetScan() {
    for (const item of ITEMS) if (item.thumbUrl) URL.revokeObjectURL(item.thumbUrl);
    ITEMS = [];
    clearConfirmError();
    render();
    const input = $("#scan-file-input");
    if (input) input.value = "";
  }

  /* ── Confirm error banner ─────────────────────────────────────── */

  function clearConfirmError() {
    const box = $("#scan-confirm-error");
    if (box) { box.hidden = true; box.textContent = ""; }
  }

  function showConfirmError(msg) {
    const box = $("#scan-confirm-error");
    if (box) { box.hidden = false; box.textContent = msg; }
  }

  /* ── Adding photos ─────────────────────────────────────────────── */

  async function addFiles(fileList) {
    const files = Array.from(fileList || []).slice(0, MAX_FILES_PER_BATCH);
    if (!files.length) return;

    const imageFiles = files.filter((f) => /^image\//.test(f.type) || /\.(jpe?g|png|webp)$/i.test(f.name));
    if (!imageFiles.length) {
      scanToast("Only JPEG, PNG or WEBP photos are supported.");
      return;
    }

    const newItems = imageFiles.map((file) => ({
      id: NEXT_ID++,
      file,
      thumbUrl: null,
      status: "reading",
      text: "",
      reason: null,
      docType: null,
    }));
    ITEMS = ITEMS.concat(newItems);
    render();

    const progress = $("#scan-progress");

    for (let i = 0; i < newItems.length; i++) {
      const item = newItems[i];
      if (progress) progress.textContent = `Reading photo ${i + 1} of ${newItems.length}…`;
      try {
        const blob = await downscaleImage(item.file);
        item.thumbUrl = URL.createObjectURL(blob);
        render();
        const result = await postScan(blob, "photo.jpg");
        if (result.ok) {
          item.status = "done";
          item.text = result.text;
        } else {
          item.status = "error";
          item.reason = result.reason;
          item.text = "";
        }
      } catch {
        item.status = "error";
        item.reason = "Could not read this photo file. Type the text yourself.";
        item.text = "";
      }
      render();
    }

    if (progress) progress.textContent = "";

    // Focus the first draft of THIS batch once every photo in it has been
    // read (or failed to read) — the point where there is something for
    // the clerk to actually check.
    const first = newItems[0];
    if (first) {
      const ta = document.getElementById(textareaId(first.id));
      if (ta) ta.focus();
    }
  }

  /* ── Confirm & submit ──────────────────────────────────────────── */

  async function confirmSubmit() {
    clearConfirmError();
    if (BUSY) return;
    if (!ITEMS.length) { showConfirmError("Add at least one photo first."); return; }
    if (ITEMS.some((it) => it.status === "reading")) {
      showConfirmError("Wait for every photo to finish reading first.");
      return;
    }
    for (const item of ITEMS) {
      if (!item.docType) {
        showConfirmError("Choose what each photo is (Shipping Instruction, draft Bill of Lading, or the email text) before continuing.");
        const fs = document.querySelector(`.scan-doctype[data-id="${item.id}"]`);
        if (fs) {
          fs.classList.add("scan-doctype-invalid");
          const firstRadio = fs.querySelector('input[type="radio"]');
          if (firstRadio) firstRadio.focus();
        }
        return;
      }
    }

    const siText = ITEMS.filter((it) => it.docType === "SI").map((it) => (it.text || "").trim()).filter(Boolean);
    const blText = ITEMS.filter((it) => it.docType === "BL").map((it) => (it.text || "").trim()).filter(Boolean);
    const emailText = ITEMS.filter((it) => it.docType === "EMAIL").map((it) => (it.text || "").trim()).filter(Boolean);

    const files = [];
    if (siText.length) files.push(new File([siText.join("\n\n")], "scan_SI.txt", { type: "text/plain" }));
    if (blText.length) files.push(new File([blText.join("\n\n")], "scan_BL.txt", { type: "text/plain" }));
    const body = emailText.length ? emailText.join("\n\n") : "Please compare the attached SI and draft BL.";

    if (typeof window.clearDraftSubmitScan !== "function") {
      showConfirmError("Uploading needs the live API. This static preview has no backend.");
      return;
    }

    BUSY = true;
    render();
    try {
      const { data } = await window.clearDraftSubmitScan({ body, files });
      if (data && data.board && data.board.email_id) markPhotoCase(data.board.email_id);
      let msg = "Photos read and checked";
      if (data && data.board && data.board.status === "MISMATCH") msg += " · discrepancy found";
      scanToast(msg);
      resetScan();
    } catch (err) {
      if (err && err.missingApi) {
        showConfirmError("Uploading needs the live API. This static preview has no backend.");
      } else {
        showConfirmError((err && err.detail) || "Could not check these photos. Try again.");
      }
    } finally {
      BUSY = false;
      render();
    }
  }

  /* ── Panel construction (built entirely here — index.html only has the
     empty <div id="addmail-scan"> for this to fill) ────────────────── */

  function buildPanel() {
    const host = document.getElementById("addmail-scan");
    if (!host) return;

    host.append(el(
      "p", "scan-intro",
      "Take or choose photos of the Shipping Instruction and the draft Bill of Lading. We read the text, you check it, then we compare."
    ));

    const zone = el("div", "scan-drop-zone");
    zone.id = "scan-drop-zone";
    const label = el("label", "drop-label");
    label.htmlFor = "scan-file-input";
    label.append(el("span", "drop-title", "Take or choose photos"));
    label.append(el("span", "drop-hint", "JPEG, PNG or WEBP — one photo per page, or drag several in at once"));
    zone.append(label);

    const input = document.createElement("input");
    input.type = "file";
    input.id = "scan-file-input";
    input.className = "drop-input";
    input.accept = "image/*";
    input.multiple = true;
    zone.append(input);
    host.append(zone);

    const progress = el("div", "scan-progress");
    progress.id = "scan-progress";
    progress.setAttribute("aria-live", "polite");
    host.append(progress);

    const list = el("div", "scan-items");
    list.id = "scan-items";
    host.append(list);

    const actions = el("div", "scan-actions");
    actions.id = "scan-actions";
    actions.hidden = true;
    const confirmBtn = el("button", "btn btn-primary", "Text looks right — check it");
    confirmBtn.type = "button";
    confirmBtn.id = "scan-confirm-btn";
    actions.append(confirmBtn);
    const errBox = el("div", "account-error");
    errBox.id = "scan-confirm-error";
    errBox.hidden = true;
    errBox.setAttribute("aria-live", "polite");
    actions.append(errBox);
    host.append(actions);

    input.addEventListener("change", () => { addFiles(input.files); });
    confirmBtn.addEventListener("click", confirmSubmit);

    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.dataset.drag = "true"; });
    zone.addEventListener("dragleave", () => { zone.dataset.drag = "false"; });
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.dataset.drag = "false";
      const dropped = e.dataTransfer && e.dataTransfer.files;
      if (dropped && dropped.length) addFiles(dropped);
    });
  }

  function init() {
    if (!document.getElementById("addmail-scan")) return; // not on the board page's markup at all
    buildPanel();
    render();
    initPhotoNoteObserver();
  }

  init();
})();
