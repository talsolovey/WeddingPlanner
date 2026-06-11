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
