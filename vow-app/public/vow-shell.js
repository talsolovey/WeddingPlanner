/* Vow shared shell: header/nav, footer, toasts, floating AI chat, helpers.
   Every app page includes this with <body data-page="...">. Pages with no app
   chrome (login, onboarding, RSVP, day-of sheet) set data-chrome="none". */

const VOW = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const money = (n, opts = {}) => {
    const v = Math.round(Number(n) || 0);
    return "$" + v.toLocaleString("en-US", opts);
  };

  const NAV = [
    ["home", "Home", "/"],
    ["checklist", "Checklist", "/checklist"],
    ["budget", "Budget", "/budget"],
    ["guests", "Guests", "/guests"],
    ["invitations", "Invites", "/invitations"],
    ["seating", "Seating", "/seating"],
    ["contracts", "Contracts", "/contracts"],
    ["vendors", "Vendors", "/vendors"],
    ["timeline", "Timeline", "/timeline"],
  ];

  let profile = null;
  try { profile = JSON.parse(localStorage.getItem("vow-profile") || "null"); } catch (e) { /* ignore */ }

  const coupleNames = () => {
    if (profile && profile.partner_a && profile.partner_b) return `${profile.partner_a} & ${profile.partner_b}`;
    if (profile && profile.partner_a) return profile.partner_a;
    return "";
  };

  const greeting = () => {
    const h = new Date().getHours();
    return h < 5 ? "Up late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  };

  async function loadProfile() {
    try {
      const res = await fetch("/api/profile");
      if (!res.ok) return profile;
      profile = await res.json();
      localStorage.setItem("vow-profile", JSON.stringify(profile));
      const el = document.querySelector(".vow-couple");
      if (el) el.textContent = coupleNames();
    } catch (e) { /* offline — keep cached */ }
    return profile;
  }

  function mountHeader(active) {
    const header = document.createElement("header");
    header.className = "vow-header";
    header.innerHTML = `
      <a href="/" class="vow-logo">Vow <span class="spark">✦</span></a>
      <nav class="vow-nav">${NAV.map(([key, label, href]) =>
        `<a href="${href}"${key === active ? ' class="active"' : ""}>${label}</a>`).join("")}
      </nav>
      <span class="vow-couple">${esc(coupleNames())}</span>
      <button class="vow-signout" title="Sign out" style="border:none;background:none;cursor:pointer;color:var(--ink-faint);font:inherit;font-size:0.78rem;padding:0.2rem 0.4rem">Sign out</button>`;
    document.body.prepend(header);
    header.querySelector(".vow-signout").onclick = async () => {
      try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ }
      localStorage.removeItem("vow-profile");
      location.href = "/login";
    };
  }

  function mountFooter() {
    const footer = document.createElement("footer");
    footer.className = "vow-footer";
    footer.innerHTML = `Vow keeps watch so you can enjoy the part that matters.`;
    document.body.appendChild(footer);
  }

  const TABS = [
    ["home", "Home", "/", "✦"],
    ["budget", "Budget", "/budget", "$"],
    ["guests", "Guests", "/guests", "☺"],
    ["seating", "Seating", "/seating", "◍"],
    ["contracts", "Contracts", "/contracts", "✎"],
  ];

  function mountTabbar(active) {
    const bar = document.createElement("nav");
    bar.className = "vow-tabbar";
    bar.innerHTML = TABS.map(([key, label, href, glyph]) =>
      `<a href="${href}"${key === active ? ' class="active"' : ""}><span class="glyph">${glyph}</span>${label}</a>`).join("");
    document.body.appendChild(bar);
  }

  /* ---------- toast (Fraunces italic pill, 2.6s) ---------- */
  let toastEl = null;
  function toast(message) {
    if (toastEl) toastEl.remove();
    toastEl = document.createElement("div");
    toastEl.className = "vow-toast";
    toastEl.textContent = message;
    document.body.appendChild(toastEl);
    setTimeout(() => { if (toastEl) { toastEl.remove(); toastEl = null; } }, 2600);
  }

  const CHEERS = ["One less thing on your mind ✦", "Beautifully handled ✦", "That's the hard part done ✦"];
  /* Celebration: rose petals drift down the screen (plus the toast).
     Canvas, ~2.5s, removed after; reduced-motion users get the toast only. */
  function petals() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (document.getElementById("vow-petals")) return;
    const canvas = document.createElement("canvas");
    canvas.id = "vow-petals";
    canvas.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:9999";
    canvas.width = innerWidth; canvas.height = innerHeight;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    const COLORS = ["#e4c9cf", "#d8a7b1", "#a05c6d", "#f3e2e0", "#c98d9b"];
    const petalsArr = Array.from({ length: 26 }, () => ({
      x: Math.random() * canvas.width,
      y: -20 - Math.random() * canvas.height * 0.4,
      r: 5 + Math.random() * 6,
      vy: 1.4 + Math.random() * 1.8,
      sway: 0.6 + Math.random() * 1.4,
      phase: Math.random() * Math.PI * 2,
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.06,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
    }));
    const t0 = performance.now();
    (function frame(t) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const age = (t - t0) / 1000;
      const fade = age > 2 ? Math.max(0, 1 - (age - 2) / 0.6) : 1;
      for (const p of petalsArr) {
        p.y += p.vy; p.rot += p.vr;
        p.x += Math.sin(t / 600 + p.phase) * p.sway;
        ctx.save();
        ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.globalAlpha = 0.85 * fade;
        ctx.fillStyle = p.color;
        ctx.beginPath();  // petal = two joined arcs
        ctx.moveTo(0, -p.r);
        ctx.quadraticCurveTo(p.r, -p.r * 0.2, 0, p.r);
        ctx.quadraticCurveTo(-p.r, -p.r * 0.2, 0, -p.r);
        ctx.fill();
        ctx.restore();
      }
      if (age < 2.6) requestAnimationFrame(frame);
      else canvas.remove();
    })(t0);
  }

  const cheer = () => {
    petals();
    toast(CHEERS[Math.floor(Math.random() * CHEERS.length)]);
  };

  /* ---------- live agent plan panel (plan -> act -> observe) ----------
     The harness streams `plan::{json}` events while it works. When pollJob is
     called without a custom event handler, this panel renders the plan as a
     live checklist: done ✓, active ● (pulsing), pending ○, plus a "replanned"
     line when the agent revised its plan mid-run. */
  let planEl = null;

  function removePlanPanel(delay = 900) {
    const el = planEl;
    planEl = null;
    if (el) setTimeout(() => el.remove(), delay);
  }

  function renderPlanPanel(events) {
    let plan = null, reason = null, activity = "";
    for (const e of events) {
      const s = String(e);
      const m = s.match(/plan::(\{.*\})/);
      if (m) {
        try {
          plan = JSON.parse(m[1]);
          reason = plan.reason || reason;
        } catch (err) { /* malformed plan event — keep the last good one */ }
      } else {
        activity = s.replace(/^plan::.*/, "") || activity;
      }
    }
    if (!plan || !Array.isArray(plan.steps) || !plan.steps.length) return;
    if (!planEl) {
      planEl = document.createElement("div");
      planEl.className = "vow-plan";
      document.body.appendChild(planEl);
    }
    const GLYPH = { done: "✓", active: "●", pending: "○" };
    planEl.innerHTML = `
      <div class="plan-eyebrow">✦ Vow's plan</div>
      ${plan.steps.map((s) => `
        <div class="plan-step ${esc(s.status)}">
          <span class="plan-glyph">${GLYPH[s.status] || "○"}</span>
          <span>${esc(s.text)}</span>
        </div>`).join("")}
      ${reason ? `<div class="plan-reason">↻ replanned — ${esc(reason)}</div>` : ""}
      ${activity ? `<div class="plan-activity">${esc(activity)}…</div>` : ""}`;
  }

  /* ---------- background jobs ---------- */
  async function pollJob(jobId, onEvent) {
    const handler = onEvent || renderPlanPanel;
    try {
      for (;;) {
        const res = await fetch("/api/jobs/" + jobId);
        if (!res.ok) throw new Error("Lost track of the job.");
        const job = await res.json();
        handler(job.events || [], job);
        if (job.done) {
          if (job.error) throw new Error(job.error);
          return job.result;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    } finally {
      if (!onEvent) removePlanPanel();
    }
  }

  /* ---------- floating AI chat (every app page) ---------- */
  const CHAT_CHIPS = ["Is our budget realistic?", "Who should we chase this week?", "Is the venue contract fair?"];

  let chatAsk = null;  // set by mountChat; used by VOW.askVow

  function mountChat() {
    const root = document.createElement("div");
    document.body.appendChild(root);
    let open = false, thinking = false, draft = "";
    let messages = [];
    try { messages = JSON.parse(sessionStorage.getItem("vow-chat") || "[]"); } catch (e) { /* ignore */ }
    if (!messages.length) {
      const names = coupleNames();
      messages = [{ role: "assistant", text: `Hi ${names || "there"} ✦ I know your budget, guest list, seating and contracts. Ask me anything — or push back on my advice, I don't mind a good debate.` }];
    }

    const save = () => { try { sessionStorage.setItem("vow-chat", JSON.stringify(messages.slice(-30))); } catch (e) { /* ignore */ } };

    function render() {
      if (!open) {
        root.innerHTML = `<button class="vow-chat-fab">✦ Ask Vow</button>`;
        root.querySelector(".vow-chat-fab").onclick = () => { open = true; render(); };
        return;
      }
      root.innerHTML = `
        <div class="vow-chat-panel">
          <div class="vow-chat-head">
            <div>
              <div class="vow-chat-title">Vow <span class="spark">✦</span></div>
              <div class="vow-chat-sub">Your planner — knows your whole wedding</div>
            </div>
            <button class="vow-chat-close" title="Close">✕</button>
          </div>
          <div class="vow-chat-list"></div>
          ${messages.length <= 1 && !thinking ? `<div class="vow-chat-chips">${CHAT_CHIPS.map((c) =>
            `<button class="vow-chat-chip">${esc(c)}</button>`).join("")}</div>` : ""}
          <div class="vow-chat-inputrow">
            <input class="vow-chat-input" placeholder="Ask or debate anything…" value="${esc(draft)}">
            <button class="vow-chat-send" title="Send">↑</button>
          </div>
        </div>`;
      const list = root.querySelector(".vow-chat-list");
      list.innerHTML = messages.map((m) => `
        <div class="vow-chat-row ${m.role === "user" ? "user" : ""}">
          <div class="vow-chat-bubble">${esc(m.text)}</div>
        </div>`).join("") +
        (thinking ? `<div class="vow-chat-row"><div class="vow-chat-thinking">✦ thinking…</div></div>` : "");
      list.scrollTop = list.scrollHeight;
      root.querySelector(".vow-chat-close").onclick = () => { open = false; render(); };
      const input = root.querySelector(".vow-chat-input");
      input.oninput = (e) => { draft = e.target.value; };
      input.onkeydown = (e) => { if (e.key === "Enter") ask(draft); };
      root.querySelector(".vow-chat-send").onclick = () => ask(draft);
      root.querySelectorAll(".vow-chat-chip").forEach((btn) => { btn.onclick = () => ask(btn.textContent); });
      if (document.activeElement !== input) input.focus();
    }

    async function ask(text) {
      text = (text || "").trim();
      if (!text || thinking) return;
      messages.push({ role: "user", text });
      draft = ""; thinking = true; save(); render();
      let reply;
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: messages.map((m) => ({ role: m.role, content: m.text })) }),
        });
        const data = await res.json();
        reply = res.ok ? data.reply : (data.error || "I couldn't reach my brain just now — try me again in a moment.");
      } catch (e) {
        reply = "I couldn't reach my brain just now — try me again in a moment.";
      }
      messages.push({ role: "assistant", text: reply });
      thinking = false; save(); render();
    }

    chatAsk = (text) => { open = true; render(); ask(text); };
    render();
  }

  /* ---------- boot ---------- */
  document.addEventListener("DOMContentLoaded", () => {
    const body = document.body;
    if (body.dataset.chrome === "none") return;
    mountHeader(body.dataset.page || "");
    mountFooter();
    mountTabbar(body.dataset.page || "");
    mountChat();
    loadProfile();
  });

  /* Guarded fetch: JSON in/out, throws Error(server message) on any non-2xx
     (a 401 sends the visitor back to /login — their session expired). */
  async function getJSON(url, options) {
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (res.status === 401) { location.href = "/login"; throw new Error("Signed out."); }
    if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status}).`);
    return body;
  }
  const postJSON = (url, payload) => getJSON(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });

  /* Tween a number from 0 to `value` inside `el` over ~600ms.
     `format` maps the in-flight number to display text (default: rounded). */
  function countUp(el, value, format) {
    if (!el) return;
    const target = Number(value) || 0;
    const fmt = format || ((n) => Math.round(n).toLocaleString());
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.textContent = fmt(target);
      return;
    }
    const t0 = performance.now(), dur = 600;
    (function tick(t) {
      const p = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
      el.textContent = fmt(target * eased);
      if (p < 1) requestAnimationFrame(tick);
    })(t0);
  }

  return { esc, money, toast, cheer, pollJob, loadProfile, greeting, coupleNames,
           getJSON, postJSON, countUp,
           askVow: (text) => { if (chatAsk) chatAsk(text); },
           get profile() { return profile; } };
})();
