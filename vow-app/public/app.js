/* Vow shared UI helpers: live agent progress + toasts */

/** Poll a background agent job, showing a quiet loading state in `container`.
    Resolves with the job result, rejects on job error. */
async function pollJob(jobId, container, message = "Vow is working on it… usually 20–60 seconds.") {
  container.innerHTML = `<div class="status"><div class="spinner"></div>${message}</div>`;
  for (;;) {
    const res = await fetch("/api/jobs/" + jobId);
    if (!res.ok) throw new Error("Lost track of the analysis job.");
    const job = await res.json();
    if (job.done) {
      if (job.error) throw new Error(job.error);
      return job.result;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/** Show a toast. Pass {undo: fn} to add an Undo action (6s window). */
function toast(message, opts = {}) {
  let root = document.getElementById("toast-root");
  if (!root) {
    root = document.createElement("div");
    root.id = "toast-root";
    document.body.appendChild(root);
  }
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<span></span>`;
  el.firstChild.textContent = message;
  if (opts.undo) {
    const btn = document.createElement("button");
    btn.textContent = "Undo";
    btn.addEventListener("click", () => { opts.undo(); el.remove(); });
    el.appendChild(btn);
  }
  root.appendChild(el);
  setTimeout(() => el.remove(), opts.undo ? 6000 : 3000);
}

/** Split severity items into urgent (red/high, always visible) and the rest
    (folded behind a "N more" disclosure). `card` renders one item. */
function foldBySeverity(items, card, emptyText, listClass = "flag-list") {
  const urgent = ["red", "high"];
  const sev = (i) => (i.severity || i.priority || "").toLowerCase();
  const hot = (items || []).filter(i => urgent.includes(sev(i)));
  const rest = (items || []).filter(i => !urgent.includes(sev(i)));
  const hotHtml = hot.map(card).join("") ||
    `<p class='lede' style='margin:0;'>${emptyText || "Nothing urgent here."}</p>`;
  const foldHtml = rest.length ? `
    <details class="more">
      <summary>${rest.length} more item${rest.length === 1 ? "" : "s"} (less urgent)</summary>
      <div class="${listClass}">${rest.map(card).join("")}</div>
    </details>` : "";
  return { hotHtml, foldHtml, hot: hot.length, rest: rest.length };
}

/** Chip row summarizing severity counts, e.g. "2 red · 3 yellow · 1 green". */
function verdictChips(items) {
  const counts = {};
  (items || []).forEach(i => {
    const s = (i.severity || i.priority || "").toLowerCase();
    counts[s] = (counts[s] || 0) + 1;
  });
  const order = ["red", "high", "yellow", "medium", "green", "low"];
  const cls = { red: "red", high: "red", yellow: "yellow", medium: "yellow", green: "green", low: "green" };
  const chips = order.filter(s => counts[s]).map(s =>
    `<span class="chip ${cls[s]}">${counts[s]} ${s}</span>`).join(" ");
  return chips ? `<div class="verdict">${chips}</div>`
               : `<div class="verdict"><span class="ok-chip">✓ all clear</span></div>`;
}

/** When the agent answers in prose instead of its JSON schema (format drift),
    show the text readably rather than a broken report full of "—". */
function proseFallback(title, text) {
  const escP = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  let t = escP(text || "No answer produced — try again.");
  t = t.replace(/###\s*/g, "\n\n");                 // markdown headings -> paragraph breaks
  t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");     // **bold**
  t = t.replace(/(^|\n)\s*[-•]\s+/g, "$1• ");       // list dashes -> bullets
  return `
    <div class="card report" style="margin-top:1.5rem;">
      <div class="report-head">
        <p class="eyebrow" style="margin-bottom:.3rem">${escP(title)}</p>
        <div style="white-space:pre-wrap; line-height:1.6;">${t}</div>
      </div>
      <div class="report-section">
        <p style="margin:0;font-size:.85rem;color:var(--ink-soft);">
          Vow answered in free text instead of its usual format this time, so the
          structured view isn't available. Running the analysis again usually fixes it.
        </p>
      </div>
    </div>`;
}
