/* Echolocate PWA — vanilla JS. No build step.
 *
 * Connects to FastAPI over WebSocket for live updates and uses the REST
 * endpoints for everything else (registration, chat, report).
 *
 * The base URL is the page origin in production. For local dev when serving
 * the static files separately from the API, override with ?api=http://...
 */

const params = new URLSearchParams(location.search);
const API_BASE = params.get("api") || "";
const WS_URL = (() => {
  if (API_BASE) return API_BASE.replace(/^http/, "ws") + "/ws";
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
})();

const $ = (sel) => document.querySelector(sel);
const toast = (msg, ms = 2200) => {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), ms);
};

// ---------- Tab switcher ----------

document.querySelectorAll("nav.tabs button").forEach(btn => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    document.querySelectorAll("nav.tabs button").forEach(b => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.dataset.view === target));
  });
});

// ---------- Sparkline (variance over time) ----------

const sparkPoints = []; // {t, var, level}
const SPARK_MAX = 120;

function drawSpark() {
  const c = $("#spark");
  if (!c) return;
  // Resize to actual layout size for crisp drawing
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (sparkPoints.length < 2) return;
  const maxVar = Math.max(...sparkPoints.map(p => p.var), 1);

  // Vertical scale band shading by level
  const colorFor = (lv) => ({
    empty: "#22c55e", low: "#eab308", moderate: "#f97316",
    high: "#ef4444", calibrating: "#a78bfa"
  }[lv] || "#94a3b8");

  // Plot line
  const pad = 4;
  const stepX = (w - pad * 2) / Math.max(sparkPoints.length - 1, 1);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#0ea5e9";
  ctx.beginPath();
  sparkPoints.forEach((p, i) => {
    const x = pad + i * stepX;
    const y = h - pad - (p.var / maxVar) * (h - pad * 2);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots colored by level
  sparkPoints.forEach((p, i) => {
    const x = pad + i * stepX;
    const y = h - pad - (p.var / maxVar) * (h - pad * 2);
    ctx.fillStyle = colorFor(p.level);
    ctx.beginPath();
    ctx.arc(x, y, 2.2, 0, Math.PI * 2);
    ctx.fill();
  });
}

window.addEventListener("resize", drawSpark);

// ---------- Render occupancy ----------

function renderOccupancy(occ) {
  const level = occ.level || "calibrating";
  const dotClass = `dot level-${level}`;

  // Individual view
  $("#ind-dot").className = dotClass;
  $("#ind-level").textContent = level;
  $("#ind-sub").textContent = occ.calibration_phase
    ? `Calibrating (${Math.round((occ.calibration_progress || 0) * 100)}%)…`
    : `Variance ratio ${occ.variance_ratio?.toFixed?.(2) ?? "—"} ×`;

  // Operator view
  $("#op-dot").className = dotClass;
  $("#op-level").textContent = level;
  $("#op-sub").textContent = occ.calibration_phase
    ? `Baseline calibration in progress`
    : `${occ.count_estimate ?? 0} estimated • confidence ${(occ.confidence ?? 0).toFixed(2)}`;
  $("#st-var").textContent = occ.variance?.toFixed?.(2) ?? "—";
  $("#st-rat").textContent = occ.variance_ratio?.toFixed?.(2) ?? "—";
  $("#st-cnt").textContent = occ.count_estimate ?? 0;
}

function renderHealth(streamHealth, totalObs) {
  const h = $("#health");
  if (!h) return;
  const rows = [
    ["Source", streamHealth?.source || "—"],
    ["Lines seen", streamHealth?.lines_seen ?? "—"],
    ["Lines parsed", streamHealth?.lines_parsed ?? "—"],
    ["Parse rate", streamHealth?.parse_rate ? (streamHealth.parse_rate * 100).toFixed(1) + "%" : "—"],
    ["Total observations", totalObs ?? 0],
  ];
  h.innerHTML = rows.map(([k, v]) =>
    `<div class="health-row"><span>${k}</span><span class="v">${v}</span></div>`
  ).join("");
}

function renderObservations(latest, total) {
  const el = $("#observations");
  $("#st-obs").textContent = total;
  if (!latest) return; // keep existing list
  // We only get the latest one over WS; prepend it
  if (el.querySelector("em")) el.innerHTML = "";
  const div = document.createElement("div");
  div.className = "obs";
  const ts = latest.timestamp ? new Date(latest.timestamp).toLocaleTimeString() : "—";
  const issue = latest.spatial_issue || (latest._no_camera ? "Threshold breach (no camera connected)" : "—");
  const choke = (latest.chokepoints || []).join(", ") || "none";
  const total_p = latest.total_people_visible ?? "?";
  const density = latest.overall_density || "?";
  div.innerHTML = `<div class="ts">${ts}</div>
    <div><strong>${issue}</strong></div>
    <div style="color:var(--muted);font-size:.85em">
      ${total_p} visible · density ${density} · chokepoints: ${choke}
    </div>`;
  el.prepend(div);
  // Cap to 10 visible
  while (el.children.length > 10) el.removeChild(el.lastChild);
}

// ---------- WebSocket ----------

let ws = null;
let wsRetryMs = 1000;

function setConn(up, label) {
  const el = $("#conn");
  if (!el) return;
  el.classList.toggle("conn-up", up);
  el.classList.toggle("conn-down", !up);
  el.textContent = label || (up ? "live" : "offline");
}

function connectWS() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => { wsRetryMs = 1000; setConn(true, "live"); toast("Connected to sensor"); };
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "occupancy_update") {
        renderOccupancy(msg.occupancy);
        renderHealth(msg.stream_health, msg.total_observations);
        renderObservations(msg.latest_spatial, msg.total_observations);

        const v = msg.occupancy?.variance ?? 0;
        const lv = msg.occupancy?.level ?? "calibrating";
        sparkPoints.push({ t: Date.now(), var: v, level: lv });
        while (sparkPoints.length > SPARK_MAX) sparkPoints.shift();
        drawSpark();
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

// ---------- Status fallback poll (in case WS is blocked) ----------

async function pollStatus() {
  try {
    const r = await fetch(`${API_BASE}/api/status`);
    if (r.ok) {
      const d = await r.json();
      renderOccupancy(d.occupancy);
      renderHealth(d.stream_health, d.total_observations);
    }
  } catch (_) {}
}
setInterval(pollStatus, 5000);
pollStatus();

// ---------- Push subscription / anonymous registration ----------

function urlBase64ToUint8Array(b64) {
  const padding = "=".repeat((4 - b64.length % 4) % 4);
  const base64 = (b64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function enroll() {
  $("#btn-enroll").disabled = true;
  $("#enroll-status").textContent = "Setting up…";
  let pushSub = null;

  try {
    if ("serviceWorker" in navigator) {
      await navigator.serviceWorker.register("sw.js");
    }
    if ("Notification" in window && "PushManager" in window) {
      const perm = await Notification.requestPermission();
      if (perm === "granted") {
        const reg = await navigator.serviceWorker.ready;
        const keyResp = await fetch(`${API_BASE}/api/vapid-public-key`);
        const { publicKey } = await keyResp.json();
        if (publicKey) {
          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(publicKey),
          });
          pushSub = sub.toJSON();
        }
      }
    }
  } catch (err) {
    console.warn("Push setup failed (continuing as anonymous-only):", err);
  }

  const r = await fetch(`${API_BASE}/api/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ push_subscription: pushSub || {} }),
  });
  const { token_id } = await r.json();
  localStorage.setItem("echolocate_token", token_id);

  // Auto check-in to "main" zone
  await fetch(`${API_BASE}/api/checkin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_id, zone: "main" }),
  });

  $("#btn-enroll").style.display = "none";
  $("#btn-report").style.display = "";
  $("#enroll-status").innerHTML = pushSub
    ? `<span class="ok">✓ Enrolled with push notifications</span>`
    : `<span style="color:var(--yellow)">✓ Enrolled (no push — add to Home Screen for iOS push)</span>`;
}

async function reportPositive() {
  if (!confirm("This will send anonymous exposure alerts to anyone who shared this space with you in the last 14 days. Continue?")) return;
  const token = localStorage.getItem("echolocate_token");
  if (!token) return;
  const r = await fetch(`${API_BASE}/api/report-positive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_id: token }),
  });
  const data = await r.json();
  toast(`Sent ${data.notifications_sent} anonymous alerts`);
}

$("#btn-enroll").addEventListener("click", enroll);
$("#btn-report").addEventListener("click", reportPositive);

if (localStorage.getItem("echolocate_token")) {
  $("#btn-enroll").style.display = "none";
  $("#btn-report").style.display = "";
  $("#enroll-status").innerHTML = `<span class="ok">✓ Already enrolled</span>`;
}

// ---------- Chat ----------

const chatLog = $("#chat-log");
function appendChat(role, text) {
  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function sendChat() {
  const inp = $("#chat-input");
  const msg = inp.value.trim();
  if (!msg) return;
  appendChat("user", msg);
  inp.value = "";
  $("#btn-send").disabled = true;
  try {
    const r = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    const data = await r.json();
    appendChat("bot", data.response || "(empty response)");
  } catch (e) {
    appendChat("bot", `Error: ${e.message}`);
  } finally {
    $("#btn-send").disabled = false;
  }
}
$("#btn-send").addEventListener("click", sendChat);
$("#chat-input").addEventListener("keypress", (e) => { if (e.key === "Enter") sendChat(); });

// Seed welcome message
appendChat("bot", "Hi! I can answer questions about how crowded this space is and any patterns I've noticed. I can't see anyone — only spatial metadata.");

// ---------- Report ----------

$("#btn-recalibrate").addEventListener("click", async () => {
  if (!confirm("Reset the empty-room baseline? Make sure the space is empty first.")) return;
  const r = await fetch(`${API_BASE}/api/recalibrate`, { method: "POST" });
  if (r.ok) toast("Recalibrating — keep the space empty for 10s");
});

$("#btn-report-gen").addEventListener("click", async () => {
  $("#btn-report-gen").disabled = true;
  $("#btn-report-gen").textContent = "Generating…";
  try {
    const r = await fetch(`${API_BASE}/api/generate-report`, { method: "POST" });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      $("#report").textContent = err.error || `HTTP ${r.status}`;
    } else {
      const data = await r.json();
      $("#report").textContent = data.report;
    }
    $("#report").style.display = "";
  } finally {
    $("#btn-report-gen").disabled = false;
    $("#btn-report-gen").textContent = "Generate report";
  }
});

// ---------- Diagnostics ----------

function renderDiagnostics(data) {
  const target = $("#diag-checks");
  if (!data || !data.checks) {
    target.innerHTML = `<em style="color:var(--red)">Failed to load diagnostics</em>`;
    return;
  }
  target.innerHTML = data.checks.map(c => `
    <div class="check-row">
      <div class="led ${c.ok ? "ok" : "bad"}"></div>
      <div class="body">
        <div class="label">${c.label}</div>
        <div class="detail">${c.detail || ""}</div>
        ${c.blocker ? `<div class="blocker">→ ${c.blocker}</div>` : ""}
      </div>
    </div>
  `).join("");

  const cfg = data.config || {};
  $("#diag-config").innerHTML = Object.entries(cfg).map(([k, v]) => `
    <div class="health-row"><span>${k}</span><span class="v">${v}</span></div>
  `).join("");
}

async function loadDiagnostics() {
  try {
    const r = await fetch(`${API_BASE}/api/diagnostics`);
    const data = await r.json();
    renderDiagnostics(data);
  } catch (e) {
    $("#diag-checks").innerHTML = `<em style="color:var(--red)">Backend unreachable: ${e.message}</em>`;
  }
}

$("#btn-diag-refresh").addEventListener("click", () => {
  $("#btn-diag-refresh").disabled = true;
  loadDiagnostics().finally(() => { $("#btn-diag-refresh").disabled = false; });
});

$("#btn-diag-firmware").addEventListener("click", async () => {
  $("#btn-diag-firmware").disabled = true;
  $("#btn-diag-firmware").textContent = "Pinging…";
  try {
    const r = await fetch(`${API_BASE}/api/firmware-status`);
    const data = await r.json();
    if (data.reachable) {
      const dev = data.device || {};
      toast(`✓ Reached ${dev.firmware || "device"} (RSSI ${dev.rssi}, ${dev.packets_received} pkts)`, 3500);
    } else {
      toast(`✗ Device unreachable: ${data.error || "unknown"}`, 4000);
    }
  } catch (e) {
    toast(`Error: ${e.message}`, 3500);
  } finally {
    $("#btn-diag-firmware").disabled = false;
    $("#btn-diag-firmware").textContent = "Test device link";
  }
});

// Auto-load diagnostics when the user opens that tab the first time
document.querySelectorAll('nav.tabs button[data-tab="diagnostics"]').forEach(btn => {
  btn.addEventListener("click", loadDiagnostics);
});
// Also load once on page boot so the tab isn't empty if the user goes straight there
loadDiagnostics();

// ---------- Plain-language toggle ----------

const PLAIN_KEY = "echolocate_plain";
function applyPlain(plain) {
  document.body.classList.toggle("plain", plain);
  const t = $("#plain-toggle");
  if (t) t.checked = plain;
  localStorage.setItem(PLAIN_KEY, plain ? "1" : "0");
}
applyPlain(localStorage.getItem(PLAIN_KEY) !== "0");  // default on
$("#plain-toggle")?.addEventListener("change", e => applyPlain(e.target.checked));

// ---------- AI Decision Log (operator) ----------

const STATUS_LABEL = {
  pending: "Awaiting decision",
  considered: "Considered",
  accepted: "Accepted",
  rejected: "Rejected",
};

function renderDecisions(decisions) {
  const target = $("#decisions");
  if (!target) return;
  if (!decisions || decisions.length === 0) {
    target.innerHTML = `<em style="color:var(--muted)">No suggestions yet — they appear here when crowding triggers an AI analysis.</em>`;
    return;
  }
  target.innerHTML = decisions.map(d => {
    const status = d.operator_status || "pending";
    const ts = new Date(d.created_at).toLocaleString();
    const notesDisplay = d.operator_notes
      ? `<div class="notes-display">📝 ${escapeHtml(d.operator_notes)}</div>` : "";
    return `
      <div class="decision" data-id="${d.id}">
        <div class="row1">
          <span class="badge badge-${status}">${STATUS_LABEL[status] || status}</span>
          <span>${escapeHtml(d.decision_type)}</span>
          <span>·</span>
          <span>${ts}</span>
          <span>·</span>
          <span>${escapeHtml(d.model || "?")}</span>
        </div>
        <div class="summary">${escapeHtml(d.summary || "(no summary)")}</div>
        ${notesDisplay}
        <div class="actions">
          <button data-status="considered">Considered</button>
          <button data-status="accepted">Accept</button>
          <button data-status="rejected">Reject</button>
        </div>
        <input class="notes-input" placeholder="Add a note (optional, saved on click)" />
      </div>
    `;
  }).join("");

  // Attach click handlers
  target.querySelectorAll(".decision").forEach(card => {
    const id = card.dataset.id;
    const noteInput = card.querySelector(".notes-input");
    card.querySelectorAll(".actions button").forEach(btn => {
      btn.addEventListener("click", async () => {
        const status = btn.dataset.status;
        const r = await fetch(`${API_BASE}/api/decisions/${id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, notes: noteInput.value || null }),
        });
        if (r.ok) {
          toast(`Marked as ${STATUS_LABEL[status]}`);
          loadDecisions();
        } else {
          const err = await r.json().catch(() => ({}));
          toast(`Failed: ${err.error || r.status}`, 3000);
        }
      });
    });
  });
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

async function loadDecisions() {
  try {
    const r = await fetch(`${API_BASE}/api/decisions`);
    const data = await r.json();
    renderDecisions(data.decisions);
  } catch (e) {
    console.warn("decisions load failed", e);
  }
}

// ---------- Community feedback ----------

async function loadFeedbackOperator() {
  try {
    const r = await fetch(`${API_BASE}/api/community-feedback`);
    const { feedback } = await r.json();
    const target = $("#feedback-list");
    if (!target) return;
    if (!feedback || feedback.length === 0) {
      target.innerHTML = `<em style="color:var(--muted)">No feedback yet.</em>`;
      return;
    }
    target.innerHTML = feedback.map(f => `
      <div class="feedback-item ${escapeHtml(f.sentiment)}">
        ${escapeHtml(f.message)}
        <div class="meta">${escapeHtml(f.sentiment)} · ${new Date(f.created_at).toLocaleString()} · zone ${escapeHtml(f.zone || "main")}</div>
      </div>
    `).join("");
  } catch (e) { console.warn("feedback load failed", e); }
}

$("#feedback-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sentiment = $("#fb-sentiment").value;
  const message = $("#fb-message").value.trim();
  if (!message) return;
  const r = await fetch(`${API_BASE}/api/community-feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sentiment, message, zone: "main" }),
  });
  if (r.ok) {
    $("#fb-message").value = "";
    toast("Submitted anonymously");
    loadFeedbackOperator();
    loadTransparency();
  } else {
    toast("Failed to submit", 2500);
  }
});

// ---------- Public Transparency view ----------

async function loadTransparency() {
  try {
    const r = await fetch(`${API_BASE}/api/transparency`);
    const data = await r.json();

    const now = data.right_now || {};
    const plainStatus = now.plain_status || "—";
    $("#tp-now").textContent = `${plainStatus.charAt(0).toUpperCase() + plainStatus.slice(1)} right now`;
    $("#tp-now-sub").textContent = now.estimate != null
      ? `Roughly ${now.estimate} ${now.estimate === 1 ? "person" : "people"} (${now.raw_level})`
      : `Status: ${now.raw_level || "unknown"}`;

    const inv = data.privacy_invariants || {};
    $("#tp-yes").innerHTML = (inv.what_is_collected || []).map(x => `<li>${escapeHtml(x)}</li>`).join("");
    $("#tp-no").innerHTML  = (inv.what_is_NEVER_collected || []).map(x => `<li>${escapeHtml(x)}</li>`).join("");
    $("#tp-proof").textContent = inv.schema_proof || "";

    const decTarget = $("#tp-decisions");
    const decisions = (data.ai_activity || {}).recent || [];
    if (decisions.length === 0) {
      decTarget.innerHTML = `<em style="color:var(--muted)">No AI judgments yet.</em>`;
    } else {
      decTarget.innerHTML = decisions.map(d => {
        const status = d.operator_status || "pending";
        const notesDisplay = d.operator_notes
          ? `<div class="notes-display">Operator note: ${escapeHtml(d.operator_notes)}</div>` : "";
        return `
          <div class="decision">
            <div class="row1">
              <span class="badge badge-${status}">${STATUS_LABEL[status] || status}</span>
              <span>${escapeHtml(d.decision_type)}</span>
              <span>·</span>
              <span>${new Date(d.created_at).toLocaleString()}</span>
            </div>
            <div class="summary">${escapeHtml(d.summary || "(no summary)")}</div>
            ${notesDisplay}
          </div>
        `;
      }).join("");
    }

    const fbTarget = $("#tp-feedback");
    const feedback = data.community_feedback_recent || [];
    if (feedback.length === 0) {
      fbTarget.innerHTML = `<em style="color:var(--muted)">No feedback yet.</em>`;
    } else {
      fbTarget.innerHTML = feedback.map(f => `
        <div class="feedback-item ${escapeHtml(f.sentiment)}">
          ${escapeHtml(f.message)}
          <div class="meta">${escapeHtml(f.sentiment)} · ${new Date(f.created_at).toLocaleString()}</div>
        </div>
      `).join("");
    }

    const v = $("#tp-verify");
    if (v && data.device?.verify_yourself) v.href = data.device.verify_yourself;

  } catch (e) {
    $("#tp-now").textContent = "(Unable to reach backend)";
    $("#tp-now-sub").textContent = e.message;
  }
}

// Auto-load operator panels + transparency when those tabs open
document.querySelectorAll('nav.tabs button[data-tab="operator"]').forEach(btn => {
  btn.addEventListener("click", () => { loadDecisions(); loadFeedbackOperator(); });
});
document.querySelectorAll('nav.tabs button[data-tab="transparency"]').forEach(btn => {
  btn.addEventListener("click", loadTransparency);
});

// Refresh decisions/feedback periodically while operator is open
setInterval(() => {
  if (document.querySelector('.view[data-view="operator"].active')) {
    loadDecisions(); loadFeedbackOperator();
  }
  if (document.querySelector('.view[data-view="transparency"].active')) {
    loadTransparency();
  }
}, 15000);

// Boot-time fetch so they're never blank
loadDecisions();
loadFeedbackOperator();
loadTransparency();
