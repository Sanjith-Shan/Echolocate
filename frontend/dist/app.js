/* Echolocate PWA — vanilla JS, three modes (Home / Consumer / Business).
 *
 * The contact-tracing flow is the centerpiece:
 *   Consumer "I'm here" → /api/consumer/check-in (logs visit)
 *   Consumer "Report sick" → /api/consumer/report-sick (notifies overlaps)
 *   Business "Notify visitors" → /api/business/notify-visitors (broadcast)
 *   Consumer "Notifications inbox" → /api/consumer/notifications (read)
 *
 * Auth/perms intentionally omitted per scope.
 */

const params  = new URLSearchParams(location.search);
const API_BASE = params.get("api") || "";
const WS_URL   = (() => {
  if (API_BASE) return API_BASE.replace(/^http/, "ws") + "/ws";
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
})();

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const TOKEN_KEY = "echolocate_token";

const toast = (msg, ms = 2200) => {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), ms);
};

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

// ---------- Tab switcher ----------

function switchTab(tab) {
  $$("nav.tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".view").forEach(v => v.classList.toggle("active", v.dataset.view === tab));
  if (tab === "consumer") { loadNotifications(); loadVisits(); }
  if (tab === "business") {
    loadDecisions(); loadFeedbackOperator(); loadVisitStats(); loadDiagnostics();
  }
  if (tab === "home") loadTransparency();
}

$$("nav.tabs button").forEach(btn =>
  btn.addEventListener("click", () => switchTab(btn.dataset.tab)));

$("#go-consumer")?.addEventListener("click", () => switchTab("consumer"));
$("#go-business")?.addEventListener("click", () => switchTab("business"));

// Collapse toggles
$$(".collapse-toggle").forEach(t =>
  t.addEventListener("click", () => {
    const card = document.getElementById(t.dataset.toggle);
    if (!card) return;
    card.classList.toggle("collapsed");
    t.textContent = card.classList.contains("collapsed") ? "Expand" : "Collapse";
  }));

// ---------- Token bookkeeping ----------

async function ensureToken() {
  let tok = localStorage.getItem(TOKEN_KEY);
  if (tok) return tok;
  // Register anonymous server-side
  const r = await fetch(`${API_BASE}/api/register`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ push_subscription: null }),
  });
  const data = await r.json();
  tok = data.token_id;
  localStorage.setItem(TOKEN_KEY, tok);
  return tok;
}

async function showTokenInfo() {
  const tok = localStorage.getItem(TOKEN_KEY);
  $("#con-token-info").textContent = tok
    ? `Your anonymous token (stored only on this device): ${tok.slice(0, 8)}…${tok.slice(-4)}`
    : "No token yet — tap 'I'm here' to create one anonymously.";
}

// ---------- Live sensor (WebSocket + poll fallback) ----------

const sparkPoints = [];
const SPARK_MAX = 120;

const colorFor = (lv) => ({
  empty: "#22c55e", low: "#eab308", moderate: "#f97316",
  high: "#ef4444", calibrating: "#a78bfa",
}[lv] || "#94a3b8");

const plainFor = (lv) => ({
  calibrating: "warming up", empty: "calm", low: "calm",
  moderate: "busy", high: "crowded",
}[lv] || lv || "—");

function setConn(up, label) {
  const el = $("#conn"); if (!el) return;
  el.classList.toggle("conn-up", up); el.classList.toggle("conn-down", !up);
  el.textContent = label || (up ? "live" : "offline");
}

function renderOccupancy(occ) {
  const lv = occ?.level || "calibrating";
  const dotClass = `dot level-${lv}`;
  const plain = plainFor(lv);
  const sub = occ?.calibration_phase
    ? `Calibrating (${Math.round((occ.calibration_progress || 0) * 100)}%)…`
    : (occ?.count_estimate != null ? `~${occ.count_estimate} people` : `Status: ${lv}`);

  for (const prefix of ["home", "con", "biz"]) {
    const dot = $(`#${prefix}-dot`); const label = $(`#${prefix}-level`); const subEl = $(`#${prefix}-sub`);
    if (!dot) continue;
    dot.className = dotClass;
    label.textContent = plain;
    subEl.textContent = sub;
  }
}

function drawSpark() {
  const c = $("#spark"); if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (sparkPoints.length < 2) return;
  const maxV = Math.max(...sparkPoints.map(p => p.var), 1);
  const pad = 4, stepX = (w - pad * 2) / Math.max(sparkPoints.length - 1, 1);
  ctx.lineWidth = 2; ctx.strokeStyle = "#0ea5e9"; ctx.beginPath();
  sparkPoints.forEach((p, i) => {
    const x = pad + i * stepX;
    const y = h - pad - (p.var / maxV) * (h - pad * 2);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  sparkPoints.forEach((p, i) => {
    const x = pad + i * stepX;
    const y = h - pad - (p.var / maxV) * (h - pad * 2);
    ctx.fillStyle = colorFor(p.level);
    ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
  });
}
window.addEventListener("resize", drawSpark);

let ws = null, wsRetryMs = 1000;
function connectWS() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => { wsRetryMs = 1000; setConn(true, "live"); };
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "occupancy_update") {
        renderOccupancy(m.occupancy);
        const v = m.occupancy?.variance ?? 0;
        const lv = m.occupancy?.level ?? "calibrating";
        sparkPoints.push({ t: Date.now(), var: v, level: lv });
        while (sparkPoints.length > SPARK_MAX) sparkPoints.shift();
        drawSpark();
        renderObservation(m.latest_spatial);
      }
    } catch (err) { console.error(err); }
  };
  ws.onclose = () => {
    setConn(false, "offline");
    wsRetryMs = Math.min(wsRetryMs * 2, 15000);
    setTimeout(connectWS, wsRetryMs);
  };
  ws.onerror = () => { setConn(false, "error"); try { ws.close(); } catch (_) {} };
}
connectWS();

async function pollStatus() {
  try {
    const r = await fetch(`${API_BASE}/api/status`);
    if (r.ok) {
      const d = await r.json();
      renderOccupancy(d.occupancy);
    }
  } catch (_) {}
}
setInterval(pollStatus, 5000);
pollStatus();

function renderObservation(latest) {
  if (!latest) return;
  const el = $("#observations"); if (!el) return;
  if (el.querySelector("em")) el.innerHTML = "";
  const div = document.createElement("div");
  div.className = "decision";
  const ts = latest.timestamp ? new Date(latest.timestamp).toLocaleTimeString() : "—";
  const issue = latest.spatial_issue || (latest._no_camera ? "Threshold breach (camera off)" : "—");
  const choke = (latest.chokepoints || []).join(", ") || "none";
  const total_p = latest.total_people_visible ?? "?";
  const density = latest.overall_density || "?";
  div.innerHTML = `<div class="row1">${ts}</div>
    <div class="summary">${escapeHtml(issue)}</div>
    <div style="color:var(--muted);font-size:.85em">
      ~${total_p} people · ${escapeHtml(density)} density · chokepoints: ${escapeHtml(choke)}
    </div>`;
  el.prepend(div);
  while (el.children.length > 8) el.removeChild(el.lastChild);
}

// ---------- Home: transparency / verify-yourself link ----------

async function loadTransparency() {
  try {
    const r = await fetch(`${API_BASE}/api/transparency`);
    const d = await r.json();
    const v = $("#verify-link");
    if (v && d.device?.verify_yourself) v.href = d.device.verify_yourself;
  } catch (_) {}
}

// ---------- Consumer: check-in ----------

$("#btn-checkin")?.addEventListener("click", async () => {
  const zone = $("#con-zone").value.trim() || "main";
  const crowded = $("#con-crowded").checked;
  let tok = await ensureToken();
  const r = await fetch(`${API_BASE}/api/consumer/check-in`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_id: tok, zone, crowded }),
  });
  if (!r.ok) { toast("Check-in failed", 2500); return; }
  const data = await r.json();
  // If the server auto-registered (e.g. server restart wiped in-memory tokens),
  // adopt the new token returned and forget the old one.
  if (data.token_id !== tok) {
    localStorage.setItem(TOKEN_KEY, data.token_id);
    tok = data.token_id;
  }
  $("#con-crowded").checked = false;
  toast(`Checked in to ${zone}`);
  showTokenInfo();
  loadVisits();
  loadNotifications();  // server may push new alerts after this point
});

// ---------- Consumer: notifications inbox ----------

async function loadNotifications() {
  const tok = localStorage.getItem(TOKEN_KEY);
  if (!tok) {
    $("#con-notifs").innerHTML = `<em style="color:var(--muted)">Check in once to receive alerts.</em>`;
    updateConsumerBadge(0);
    return;
  }
  try {
    const r = await fetch(`${API_BASE}/api/consumer/notifications?token_id=${encodeURIComponent(tok)}`);
    const data = await r.json();
    const ns = data.notifications || [];
    if (ns.length === 0) {
      $("#con-notifs").innerHTML = `<em style="color:var(--muted)">No alerts yet.</em>`;
    } else {
      $("#con-notifs").innerHTML = ns.map(n => `
        <div class="notif ${n.read ? "" : "unread"} ${n.type === "exposure" ? "exposure" : ""}">
          ${n.read ? "" : `<button class="read-btn" data-id="${n.id}">Mark read</button>`}
          <div class="meta">
            ${escapeHtml(n.type)} · ${new Date(n.created_at).toLocaleString()}
            ${n.zone ? "· " + escapeHtml(n.zone) : ""}
          </div>
          <div class="title">${escapeHtml(n.title || "")}</div>
          <div class="body">${escapeHtml(n.body || "")}</div>
          ${n.exposure_date ? `<div class="meta" style="margin-top:.3em">Exposure date: ${new Date(n.exposure_date).toLocaleString()}</div>` : ""}
        </div>
      `).join("");
      $("#con-notifs").querySelectorAll(".read-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.id;
          const r2 = await fetch(`${API_BASE}/api/consumer/notifications/${id}/read?token_id=${encodeURIComponent(tok)}`, { method: "POST" });
          if (r2.ok) loadNotifications();
        });
      });
    }
    updateConsumerBadge(ns.filter(n => !n.read).length);
  } catch (e) {
    $("#con-notifs").innerHTML = `<em style="color:var(--red)">Failed to load.</em>`;
  }
}

function updateConsumerBadge(count) {
  const b = $("#con-badge");
  if (!b) return;
  b.textContent = count > 9 ? "9+" : (count || "");
  b.classList.toggle("show", count > 0);
}

// ---------- Consumer: my visits ----------

async function loadVisits() {
  const tok = localStorage.getItem(TOKEN_KEY);
  if (!tok) {
    $("#con-visits").innerHTML = `<em style="color:var(--muted)">No visits yet.</em>`;
    return;
  }
  try {
    const r = await fetch(`${API_BASE}/api/consumer/my-visits?token_id=${encodeURIComponent(tok)}`);
    const { visits } = await r.json();
    if (!visits || visits.length === 0) {
      $("#con-visits").innerHTML = `<em style="color:var(--muted)">No visits yet.</em>`;
      return;
    }
    $("#con-visits").innerHTML = visits.map(v => `
      <div class="visit">
        <div>
          <div>${escapeHtml(v.zone)}</div>
          <div class="when">${new Date(v.visited_at).toLocaleString()}</div>
        </div>
        ${v.crowded ? `<span class="crowded-flag">felt crowded</span>` : ""}
      </div>
    `).join("");
  } catch (_) {}
}

// ---------- Consumer: report sick ----------

$("#btn-report-sick")?.addEventListener("click", async () => {
  const tok = localStorage.getItem(TOKEN_KEY);
  if (!tok) { toast("Check in at least once first", 2500); return; }
  if (!confirm("Send anonymous exposure alerts to everyone whose visits overlapped yours? Your identity is never shared.")) return;
  const r = await fetch(`${API_BASE}/api/consumer/report-sick`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_id: tok }),
  });
  const data = await r.json();
  toast(`Sent ${data.notifications_inapp || 0} anonymous alerts`, 3500);
});

// ---------- Consumer: anonymous feedback ----------

$("#feedback-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sentiment = $("#fb-sentiment").value;
  const message = $("#fb-message").value.trim();
  if (!message) return;
  const r = await fetch(`${API_BASE}/api/community-feedback`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sentiment, message, zone: "main" }),
  });
  if (r.ok) { $("#fb-message").value = ""; toast("Submitted anonymously"); }
  else toast("Failed to submit", 2500);
});

// ---------- Consumer: push enrollment ----------

$("#btn-enable-push")?.addEventListener("click", async () => {
  $("#btn-enable-push").disabled = true;
  $("#push-status").textContent = "Setting up…";
  try {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      throw new Error("Push not supported in this browser");
    }
    const reg = await navigator.serviceWorker.register("sw.js");
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("Permission denied");
    const keyResp = await fetch(`${API_BASE}/api/vapid-public-key`);
    const { publicKey } = await keyResp.json();
    if (!publicKey) throw new Error("Server has no VAPID key (push disabled server-side)");
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
    // Persist subscription against the existing token
    let tok = localStorage.getItem(TOKEN_KEY) || (await ensureToken());
    await fetch(`${API_BASE}/api/register`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ push_subscription: sub.toJSON() }),
    });
    $("#push-status").innerHTML = `<span style="color:var(--green)">✓ Push enabled</span>`;
  } catch (e) {
    $("#push-status").innerHTML = `<span style="color:var(--yellow)">${escapeHtml(e.message)}</span>`;
  } finally {
    $("#btn-enable-push").disabled = false;
  }
});

function urlBase64ToUint8Array(b64) {
  const padding = "=".repeat((4 - b64.length % 4) % 4);
  const base64 = (b64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

// ---------- Business: visit stats ----------

async function loadVisitStats() {
  try {
    const r = await fetch(`${API_BASE}/api/business/visits`);
    const s = await r.json();
    $("#biz-visits-total").textContent  = s.total_visits ?? 0;
    $("#biz-visits-unique").textContent = s.unique_tokens ?? 0;
    $("#biz-self-crowded").textContent  = s.self_reported_crowded ?? 0;
    $("#biz-self-sick").textContent     = s.self_reported_sick ?? 0;
  } catch (_) {}
}

// ---------- Business: broadcast ----------

function defaultBroadcastWindow() {
  const now = new Date(); const hourAgo = new Date(now.getTime() - 3600 * 1000);
  // datetime-local needs no Z and uses local time
  const fmt = (d) => d.toISOString().slice(0, 16);  // yyyy-mm-ddThh:mm in UTC
  $("#bc-from").value = fmt(hourAgo);
  $("#bc-to").value   = fmt(now);
}

$("#broadcast-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  // Convert datetime-local (which we treat as UTC since defaultBroadcastWindow
  // wrote UTC ISO into the field) to the API's "%Y-%m-%dT%H:%M:%SZ" shape.
  const from = $("#bc-from").value;
  const to   = $("#bc-to").value;
  const utcify = (s) => s.length === 16 ? s + ":00Z" : (s.endsWith("Z") ? s : s + "Z");
  const payload = {
    zone: $("#bc-zone").value.trim() || "main",
    time_from: utcify(from),
    time_to:   utcify(to),
    title: $("#bc-title").value.trim() || "Echolocate alert",
    body:  $("#bc-body").value.trim(),
    notification_type: $("#bc-type").value,
  };
  if (!payload.body) { toast("Please add a message body", 2500); return; }
  const r = await fetch(`${API_BASE}/api/business/notify-visitors`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) { toast("Broadcast failed", 2500); return; }
  const d = await r.json();
  $("#bc-status").textContent = `Sent to ${d.matched_tokens} visitor${d.matched_tokens === 1 ? "" : "s"} (${d.notifications_inapp} in-app, ${d.notifications_push} push)`;
  toast(`Broadcast: ${d.matched_tokens} reached`);
  loadVisitStats();
});

// ---------- Business: AI Decision Log ----------

const STATUS_LABEL = {
  pending: "Awaiting decision", considered: "Considered",
  accepted: "Accepted", rejected: "Rejected",
};

async function loadDecisions() {
  try {
    const r = await fetch(`${API_BASE}/api/decisions`);
    const data = await r.json();
    const target = $("#decisions"); if (!target) return;
    const decisions = data.decisions || [];
    if (decisions.length === 0) {
      target.innerHTML = `<em style="color:var(--muted)">No suggestions yet.</em>`;
      return;
    }
    target.innerHTML = decisions.map(d => {
      const status = d.operator_status || "pending";
      const ts = new Date(d.created_at).toLocaleString();
      const notes = d.operator_notes ? `<div class="notes-display">📝 ${escapeHtml(d.operator_notes)}</div>` : "";
      return `
        <div class="decision" data-id="${d.id}">
          <div class="row1">
            <span class="badge badge-${status}">${STATUS_LABEL[status] || status}</span>
            <span>${escapeHtml(d.decision_type)}</span>
            <span>·</span><span>${ts}</span>
            <span>·</span><span>${escapeHtml(d.model || "?")}</span>
          </div>
          <div class="summary">${escapeHtml(d.summary || "(no summary)")}</div>
          ${notes}
          <div class="actions">
            <button data-status="considered">Considered</button>
            <button data-status="accepted">Accept</button>
            <button data-status="rejected">Reject</button>
          </div>
          <input class="notes-input" placeholder="Add a note (optional, saved on click)" />
        </div>`;
    }).join("");
    target.querySelectorAll(".decision").forEach(card => {
      const id = card.dataset.id;
      const noteInput = card.querySelector(".notes-input");
      card.querySelectorAll(".actions button").forEach(btn => {
        btn.addEventListener("click", async () => {
          const status = btn.dataset.status;
          const r2 = await fetch(`${API_BASE}/api/decisions/${id}`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status, notes: noteInput.value || null }),
          });
          if (r2.ok) { toast(`Marked ${STATUS_LABEL[status]}`); loadDecisions(); }
        });
      });
    });
  } catch (_) {}
}

// ---------- Business: feedback list ----------

async function loadFeedbackOperator() {
  try {
    const r = await fetch(`${API_BASE}/api/community-feedback`);
    const { feedback } = await r.json();
    const target = $("#feedback-list"); if (!target) return;
    if (!feedback || feedback.length === 0) {
      target.innerHTML = `<em style="color:var(--muted)">No feedback yet.</em>`; return;
    }
    target.innerHTML = feedback.map(f => `
      <div class="feedback-item ${escapeHtml(f.sentiment)}">
        ${escapeHtml(f.message)}
        <div class="meta">${escapeHtml(f.sentiment)} · ${new Date(f.created_at).toLocaleString()}</div>
      </div>
    `).join("");
  } catch (_) {}
}

// ---------- Business: diagnostics + recalibrate ----------

async function loadDiagnostics() {
  try {
    const r = await fetch(`${API_BASE}/api/diagnostics`);
    const d = await r.json();
    $("#diag-checks").innerHTML = (d.checks || []).map(c => `
      <div class="check-row">
        <div class="led ${c.ok ? "ok" : "bad"}"></div>
        <div class="body">
          <div class="label">${escapeHtml(c.label)}</div>
          <div class="detail">${escapeHtml(c.detail || "")}</div>
          ${c.blocker ? `<div class="blocker">→ ${escapeHtml(c.blocker)}</div>` : ""}
        </div>
      </div>`).join("");
  } catch (_) {}
}

$("#btn-diag-refresh")?.addEventListener("click", () => {
  $("#btn-diag-refresh").disabled = true;
  loadDiagnostics().finally(() => { $("#btn-diag-refresh").disabled = false; });
});

$("#btn-diag-firmware")?.addEventListener("click", async () => {
  $("#btn-diag-firmware").disabled = true;
  try {
    const r = await fetch(`${API_BASE}/api/firmware-status`);
    const d = await r.json();
    if (d.reachable) {
      const dev = d.device || {};
      toast(`✓ Reached ${dev.firmware} (RSSI ${dev.rssi}, ${dev.packets_received} pkts)`, 3500);
    } else toast(`✗ Device unreachable: ${d.error}`, 4000);
  } finally { $("#btn-diag-firmware").disabled = false; }
});

$("#btn-recalibrate")?.addEventListener("click", async () => {
  if (!confirm("Reset baseline? Make sure the space is empty first.")) return;
  await fetch(`${API_BASE}/api/recalibrate`, { method: "POST" });
  toast("Recalibrating — keep the space empty for 10s");
});

// ---------- Business: report generation + chat ----------

$("#btn-report-gen")?.addEventListener("click", async () => {
  const btn = $("#btn-report-gen");
  btn.disabled = true; btn.textContent = "Generating…";
  try {
    const r = await fetch(`${API_BASE}/api/generate-report`, { method: "POST" });
    const d = await r.json();
    $("#report").textContent = d.report || d.error || "(no output)";
    $("#report").style.display = "";
  } finally { btn.disabled = false; btn.textContent = "Generate report"; }
});

const chatLog = $("#chat-log");
function appendChat(role, text) {
  if (!chatLog) return;
  const div = document.createElement("div");
  div.className = `notif ${role === "user" ? "" : ""}`;
  div.style.background = role === "user" ? "var(--accent)" : "var(--bg)";
  div.style.color = role === "user" ? "white" : "var(--fg)";
  div.style.borderLeftColor = role === "user" ? "var(--accent2)" : "var(--line)";
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}
async function sendChat() {
  const inp = $("#chat-input"); if (!inp) return;
  const msg = inp.value.trim(); if (!msg) return;
  appendChat("user", msg); inp.value = "";
  const r = await fetch(`${API_BASE}/api/chat`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: msg }),
  });
  const d = await r.json();
  appendChat("bot", d.response || "(empty)");
}
$("#btn-send")?.addEventListener("click", sendChat);
$("#chat-input")?.addEventListener("keypress", (e) => { if (e.key === "Enter") sendChat(); });

// ---------- Periodic refreshes ----------

setInterval(() => {
  // Always poll consumer notifications so the badge stays current even on Home/Business
  loadNotifications();
  if ($(`.view[data-view="business"].active`)) {
    loadDecisions(); loadFeedbackOperator(); loadVisitStats();
  }
}, 15000);

// ---------- Boot ----------

defaultBroadcastWindow();
showTokenInfo();
loadTransparency();
loadNotifications();
loadVisitStats();
loadDiagnostics();
