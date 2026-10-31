/**
 * ReelGist — Frontend Application
 * Talks to Flask backend at localhost:5050
 */

const API = "http://localhost:5050";

// ── State ────────────────────────────────────────────────────────────────────
let currentResult = null;
let libraryData   = [];

// ── DOM refs ─────────────────────────────────────────────────────────────────
const urlInput       = document.getElementById("url-input");
const processBtn     = document.getElementById("process-btn");
const urlError       = document.getElementById("url-error");
const statusCard     = document.getElementById("status-card");
const progressBar    = document.getElementById("progress-bar");
const resultCard     = document.getElementById("result-card");
const searchInput    = document.getElementById("search-input");
const libraryGrid    = document.getElementById("library-grid");
const reelCount      = document.getElementById("reel-count");
const modalOverlay   = document.getElementById("modal-overlay");
const modalClose     = document.getElementById("modal-close");

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  registerSW();
  handleShareTarget();
  bindTabs();
  bindProcessForm();
  bindModal();
  bindSearch();
});

// ── Service Worker ───────────────────────────────────────────────────────────
function registerSW() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

// ── Share Target (Android share sheet) ───────────────────────────────────────
function handleShareTarget() {
  const params = new URLSearchParams(window.location.search);
  const sharedText = params.get("text") || "";
  const sharedUrl  = params.get("url")  || "";

  let instagramUrl = sharedUrl;

  // Instagram often embeds the link in the "text" field
  if (!instagramUrl || !instagramUrl.includes("instagram.com")) {
    instagramUrl = extractInstagramUrl(sharedText) || instagramUrl;
  }

  if (instagramUrl && instagramUrl.includes("instagram.com")) {
    urlInput.value = instagramUrl;
    // Clear the query string from address bar without reload
    history.replaceState({}, "", "/");
    // Auto-trigger after a brief pause so the UI renders first
    setTimeout(() => processBtn.click(), 400);
  }
}

function extractInstagramUrl(text) {
  const match = text.match(/https?:\/\/(?:www\.)?instagram\.com\/[^\s"')]+/);
  return match ? match[0] : null;
}

// ── Tab navigation ───────────────────────────────────────────────────────────
function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${tab}`).classList.add("active");
      if (tab === "library") loadLibrary();
    });
  });
}

// ── Process form ─────────────────────────────────────────────────────────────
function bindProcessForm() {
  processBtn.addEventListener("click", handleProcess);
  urlInput.addEventListener("keydown", e => {
    if (e.key === "Enter") handleProcess();
  });
}

let processing = false;

async function handleProcess() {
  if (processing) return;  // Enter key bypasses the disabled button
  const url = urlInput.value.trim();
  urlError.classList.add("hidden");

  if (!url) {
    showUrlError("Please enter an Instagram Reel URL.");
    return;
  }
  if (!url.includes("instagram.com")) {
    showUrlError("URL must be from instagram.com");
    return;
  }

  processing = true;
  resultCard.classList.add("hidden");
  showStatus(true);
  progressBar.classList.add("indeterminate");
  processBtn.disabled = true;

  try {
    const res = await fetch(`${API}/api/process`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });

    let data = null;
    try { data = await res.json(); } catch { /* non-JSON error body */ }

    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);

    currentResult = data;
    showStatus(false);
    renderResult(data);

  } catch (err) {
    showStatus(false);
    const msg = err instanceof TypeError
      ? "Cannot reach the backend. Is it running on port 5050?"
      : err.message;
    showUrlError(`Error: ${msg}`);
  } finally {
    progressBar.classList.remove("indeterminate");
    processing = false;
    processBtn.disabled = false;
  }
}

function showUrlError(msg) {
  urlError.textContent = msg;
  urlError.classList.remove("hidden");
}

function showStatus(show) {
  statusCard.classList.toggle("hidden", !show);
  if (show) {
    ["step-download","step-transcribe","step-summarize","step-done"].forEach(s =>
      setStep(s, "idle")
    );
    setProgress(0);
  }
}

function setStep(id, state) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove("active", "done", "error");
  if (state !== "idle") el.classList.add(state);
  const stateEl = el.querySelector(".step-state");
  if (stateEl) {
    stateEl.textContent = state === "done" ? "✓" : state === "active" ? "…" : "";
  }
}

function setProgress(pct) {
  progressBar.style.width = pct + "%";
}

// ── Result rendering ──────────────────────────────────────────────────────────
function renderResult(data) {
  document.getElementById("reel-thumb").src       = data.thumbnail || "";
  document.getElementById("reel-thumb").style.display = data.thumbnail ? "" : "none";
  document.getElementById("reel-title").textContent    = data.title || "Instagram Reel";
  document.getElementById("reel-uploader").textContent = data.uploader ? `@${data.uploader}` : "";
  document.getElementById("reel-duration").textContent = metaLine(data);

  // Gist card (falls back to plain summary text for older reels)
  renderCard(document.getElementById("summary-content"), data, "transcript-content");
  document.getElementById("truncated-note").classList.toggle("hidden", !data.truncated);

  // Transcript
  renderTranscriptBlock("transcript-content", data);

  // Copy button
  const copyBtn = document.getElementById("copy-summary-btn");
  copyBtn.onclick = () => copyText(data.summary, copyBtn);

  // Open link
  document.getElementById("open-reel-link").href = data.url;

  resultCard.classList.remove("hidden");
  resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function metaLine(data) {
  const parts = [];
  if (data.duration) parts.push(`⏱ ${formatDuration(data.duration)}`);
  if (data.language && !["en", "english"].includes(data.language)) {
    const lang = data.language.charAt(0).toUpperCase() + data.language.slice(1);
    parts.push(`🌐 ${lang}${data.transcript_en ? " · English translation available" : ""}`);
  }
  return parts.join("   ");
}

// ── Gist card ─────────────────────────────────────────────────────────────────
const SNIPPET_LABELS = { prompt: "Prompt", command: "Command", code: "Code", setting: "Setting", url: "URL", other: "Snippet" };

function tsChip(t) {
  return typeof t === "number"
    ? `<button class="ts-chip" data-t="${t}" title="Jump to this moment in the transcript">${formatTime(t)}</button>`
    : "";
}

function unverifiedBadge(item) {
  return item.verified === false
    ? `<span class="unverified" title="This quote was not found word for word in the transcript. Check it before relying on it.">unverified</span>`
    : "";
}

function evidenceLine(item) {
  return item.evidence ? `<div class="gist-evidence">“${escHtml(item.evidence)}”</div>` : "";
}

function itemList(items, ordered) {
  const rows = items.map(it => `
    <li>
      <span class="gist-text">${escHtml(it.text)}</span> ${tsChip(it.t)} ${unverifiedBadge(it)}
      ${evidenceLine(it)}
    </li>`).join("");
  return ordered ? `<ol class="gist-list">${rows}</ol>` : `<ul class="gist-list">${rows}</ul>`;
}

function section(title, body, extraClass = "") {
  return `<section class="gist-sec ${extraClass}"><h4>${title}</h4>${body}</section>`;
}

function renderCard(container, data, transcriptId) {
  const card = data.card;
  if (!card) {
    container.classList.remove("gist-card");
    container.textContent = data.summary || "(no summary)";
    return;
  }
  container.classList.add("gist-card");

  let html = `<p class="gist-tldr">${escHtml(card.tldr)}</p>`;
  if (card.steps?.length) html += section("✅ Steps", itemList(card.steps, true));
  if (card.tools?.length) {
    html += section("🧰 Tools & resources", `<ul class="gist-list">${card.tools.map(t => `
      <li>
        <strong>${escHtml(t.name)}</strong> — ${escHtml(t.what)} ${tsChip(t.t)} ${unverifiedBadge(t)}
        ${evidenceLine(t)}
      </li>`).join("")}</ul>`);
  }
  if (card.snippets?.length) {
    html += section("⌨️ Prompts & commands (as spoken)", card.snippets.map((sn, i) => `
      <div class="gist-snippet">
        <div class="gist-snippet-head">
          <span class="snippet-kind">${SNIPPET_LABELS[sn.kind] || "Snippet"}</span>
          ${tsChip(sn.t)} ${unverifiedBadge(sn)}
          <button class="copy-btn snippet-copy" data-i="${i}">📋 Copy</button>
        </div>
        <code>${escHtml(sn.text)}</code>
      </div>`).join(""));
  }
  if (card.insights?.length) html += section("💡 Insights", itemList(card.insights, false));
  if (card.warnings?.length) html += section("⚠️ Warnings", itemList(card.warnings, false));
  html += section("❓ What's missing from this reel",
    card.missing?.length
      ? `<ul class="gist-list">${card.missing.map(m => `<li>${escHtml(m)}</li>`).join("")}</ul>`
      : `<p class="gist-none">Nothing obviously missing: everything referenced was said in the reel.</p>`,
    "gist-missing");

  container.innerHTML = html;

  container.querySelectorAll(".ts-chip").forEach(btn =>
    btn.addEventListener("click", () => jumpToTime(transcriptId, parseFloat(btn.dataset.t))));
  container.querySelectorAll(".snippet-copy").forEach(btn =>
    btn.addEventListener("click", () => copyText(card.snippets[+btn.dataset.i].text, btn)));
}

// ── Transcript (original / English) ───────────────────────────────────────────
function renderTranscriptBlock(containerId, data) {
  const el = document.getElementById(containerId);
  el.innerHTML = "";
  const hasEnglish = (data.segments_en && data.segments_en.length) || data.transcript_en;

  const rows = document.createElement("div");
  rows.className = "transcript-rows";

  if (hasEnglish) {
    const bar = document.createElement("div");
    bar.className = "transcript-lang-toggle";
    bar.innerHTML = `
      <button class="lang-btn active" data-view="original">Original (${escHtml(data.language || "original")})</button>
      <button class="lang-btn" data-view="english">English</button>`;
    bar.querySelectorAll(".lang-btn").forEach(btn => btn.addEventListener("click", () => {
      bar.querySelectorAll(".lang-btn").forEach(b => b.classList.toggle("active", b === btn));
      if (btn.dataset.view === "english") fillTranscript(rows, data.segments_en, data.transcript_en);
      else fillTranscript(rows, data.segments, data.transcript);
    }));
    el.appendChild(bar);
  }

  el.appendChild(rows);
  fillTranscript(rows, data.segments, data.transcript);
}

function fillTranscript(el, segments, plainText) {
  el.innerHTML = "";

  if (segments && segments.length > 0) {
    segments.forEach(seg => {
      const row = document.createElement("div");
      row.className = "transcript-segment";
      row.dataset.start = seg.start;
      row.innerHTML = `
        <span class="seg-time">${formatTime(seg.start)}</span>
        <span class="seg-text">${escHtml(seg.text)}</span>
      `;
      el.appendChild(row);
    });
  } else if (plainText) {
    const p = document.createElement("p");
    p.className = "transcript-plain";
    p.textContent = plainText;
    el.appendChild(p);
  } else {
    el.textContent = "(No transcript available)";
  }
}

function jumpToTime(transcriptId, t) {
  const container = document.getElementById(transcriptId);
  if (!container) return;
  const details = container.closest("details");
  if (details) details.open = true;

  // Evidence timestamps refer to the original transcript
  const origBtn = container.querySelector('.lang-btn[data-view="original"]');
  if (origBtn && !origBtn.classList.contains("active")) origBtn.click();

  const rows = [...container.querySelectorAll(".transcript-segment")];
  if (!rows.length) return;
  let target = rows[0];
  for (const r of rows) {
    if (parseFloat(r.dataset.start) <= t + 0.05) target = r; else break;
  }
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.remove("flash");
  void target.offsetWidth;  // restart the highlight animation
  target.classList.add("flash");
}

// ── Library ───────────────────────────────────────────────────────────────────
async function loadLibrary() {
  try {
    const res = await fetch(`${API}/api/library`);
    libraryData = await res.json();
    renderLibrary(libraryData);
  } catch {
    libraryGrid.innerHTML = `<p class="empty-state">⚠️ Cannot reach backend. Is it running on port 5050?</p>`;
  }
}

function renderLibrary(reels) {
  reelCount.textContent = reels.length ? `${reels.length} reel${reels.length !== 1 ? "s" : ""}` : "";
  libraryGrid.innerHTML = "";

  if (!reels.length) {
    libraryGrid.innerHTML = `<p class="empty-state">No reels saved yet. Analyse a reel to get started!</p>`;
    return;
  }

  reels.forEach(reel => {
    const card = document.createElement("div");
    card.className = "library-card";
    card.innerHTML = `
      <div class="lib-thumb-wrap">
        ${reel.thumbnail
          ? `<img class="lib-thumb" src="${escHtml(reel.thumbnail)}" alt="" loading="lazy" />`
          : `<div class="lib-thumb" style="background:var(--bg3)"></div>`}
        ${reel.duration ? `<span class="lib-duration-badge">${formatDuration(reel.duration)}</span>` : ""}
      </div>
      <div class="lib-body">
        <div class="lib-title">${escHtml(reel.title || "Instagram Reel")}</div>
        ${reel.uploader ? `<div class="lib-uploader">@${escHtml(reel.uploader)}</div>` : ""}
        <div class="lib-preview">${escHtml(stripEmoji(reel.summary || "").slice(0, 200))}</div>
        <div class="lib-date">${formatDate(reel.created_at)}</div>
      </div>
    `;
    card.addEventListener("click", () => openModal(reel));
    libraryGrid.appendChild(card);
  });
}

// ── Search ────────────────────────────────────────────────────────────────────
function bindSearch() {
  searchInput.addEventListener("input", () => {
    const q = searchInput.value.toLowerCase();
    if (!q) { renderLibrary(libraryData); return; }
    const filtered = libraryData.filter(r =>
      (r.title || "").toLowerCase().includes(q) ||
      (r.uploader || "").toLowerCase().includes(q) ||
      (r.summary || "").toLowerCase().includes(q) ||
      (r.transcript || "").toLowerCase().includes(q) ||
      (r.transcript_en || "").toLowerCase().includes(q)
    );
    renderLibrary(filtered);
  });
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function bindModal() {
  modalClose.addEventListener("click", closeModal);
  modalOverlay.addEventListener("click", e => {
    if (e.target === modalOverlay) closeModal();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });
}

function openModal(reel) {
  // Meta
  document.getElementById("modal-meta").innerHTML = `
    ${reel.thumbnail ? `<img class="reel-thumb" src="${escHtml(reel.thumbnail)}" alt="" />` : ""}
    <div class="reel-info">
      <div class="reel-title">${escHtml(reel.title || "Instagram Reel")}</div>
      ${reel.uploader ? `<div class="reel-uploader">@${escHtml(reel.uploader)}</div>` : ""}
      ${metaLine(reel) ? `<div class="reel-duration">${escHtml(metaLine(reel))}</div>` : ""}
    </div>
  `;

  // Gist card
  renderCard(document.getElementById("modal-summary"), reel, "modal-transcript");

  // Transcript
  renderTranscriptBlock("modal-transcript", reel);

  // Buttons
  const copyBtn = document.getElementById("modal-copy-btn");
  copyBtn.onclick = () => copyText(reel.summary, copyBtn);

  document.getElementById("modal-open-link").href = reel.url;

  const delBtn = document.getElementById("modal-delete-btn");
  delBtn.onclick = () => deleteReel(reel.id);

  modalOverlay.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeModal() {
  modalOverlay.classList.add("hidden");
  document.body.style.overflow = "";
}

async function deleteReel(id) {
  if (!confirm("Delete this reel from your library?")) return;
  await fetch(`${API}/api/library/${id}`, { method: "DELETE" });
  closeModal();
  loadLibrary();
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function formatDuration(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatTime(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatDate(ts) {
  if (!ts) return "";
  return new Date(ts).toLocaleDateString(undefined, {
    day: "numeric", month: "short", year: "numeric"
  });
}

function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function stripEmoji(str) {
  // Remove emoji for the preview blurb
  return str.replace(/[\u{1F300}-\u{1FFFF}✅💡⚠️•*]/gu, "").trim();
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text || "");
    if (btn) {
      btn.textContent = "✓ Copied!";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = "📋 Copy";
        btn.classList.remove("copied");
      }, 2000);
    }
  } catch {
    // fallback
    const ta = document.createElement("textarea");
    ta.value = text || "";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
}

