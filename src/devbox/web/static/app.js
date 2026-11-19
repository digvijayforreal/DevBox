"use strict";

/* ===================== setup ===================== */
const AGENTS = ["triage", "storage", "process", "dependency"];
const SUGGESTIONS = [
  "Show my system details",
  "Why is my disk full?",
  "What is using port 3000?",
  "Show top processes by CPU",
  "Find git repos with unsaved work on my Desktop",
  "Find old node_modules in my Documents",
];
const KIND_LABEL = { normal: "normal", show: "show & stop", action: "needs approval", handoff: "handoff" };
const KIND_ICON = { normal: "🔧", show: "📋", action: "⚠️", handoff: "🧭" };

// The launch URL carries a one-time token; keep it in this tab only and hide it from the address bar.
const qs = new URLSearchParams(location.search);
if (qs.get("token")) {
  sessionStorage.setItem("devbox_token", qs.get("token"));
  history.replaceState(null, "", location.pathname);
}
const TOKEN = sessionStorage.getItem("devbox_token") || "";

const $ = (sel, root = document) => root.querySelector(sel);
const chat = $("#chat");
const events = $("#events");
const input = $("#input");

/** Tiny DOM builder. Text is always set via textContent (no HTML injection). */
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
const fmt = (n) => Number(n || 0).toLocaleString();

async function api(path) {
  const r = await fetch(path, { headers: { "X-DevBox-Token": TOKEN } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function toast(text, level = "info") {
  const t = h("div", { class: `toast ${level}`, role: level === "error" ? "alert" : "status", text });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4500);
}

/* ===================== mini markdown (escape first, then format) ===================== */
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}
function mdBlock(text) {
  let out = "", list = null, para = [];
  const flush = () => { if (para.length) { out += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
  const close = () => { if (list) { out += `</${list}>`; list = null; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flush(); close(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flush(); close(); const n = Math.min(m[1].length + 2, 6); out += `<h${n}>${inline(m[2])}</h${n}>`; continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flush(); if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flush(); if (list !== "ol") { close(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; continue; }
    close(); para.push(line);
  }
  flush(); close();
  return out;
}
function md(src) {
  return String(src).replace(/\r/g, "").split("```").map((part, i) =>
    i % 2 ? `<pre><code>${esc(part.replace(/^[\w-]*\n/, ""))}</code></pre>` : mdBlock(esc(part))
  ).join("");
}

/* ===================== connection ===================== */
let ws = null;
let busy = false;
let currentAgent = "triage";

function setConn(state) {
  const c = $("#conn");
  c.className = `conn ${state}`;
  c.textContent = { online: "Connected", offline: "Reconnecting…", auth: "Not authorized" }[state] || "Connecting…";
}

function connect() {
  if (!TOKEN) return showAuthError();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
  ws.onopen = () => setConn("online");
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch { return; } handle(m); };
  ws.onclose = (ev) => {
    if (ev.code === 4401) return showAuthError();
    setConn("offline");
    setTimeout(connect, 1500);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; }
  toast("Not connected to DevBox.", "error");
  return false;
}

function showAuthError() {
  setConn("auth");
  chat.replaceChildren(h("div", { class: "msg error" }, h("div", { class: "bubble" },
    "This page isn't authorized. Open DevBox with the link printed in the terminal ",
    "(it contains a one-time token), or restart it with ", h("code", { text: "uv run devbox" }), ".")));
}

function handle(m) {
  switch (m.type) {
    case "hello":
      chat.replaceChildren();
      if (!m.transcript.length) showEmpty();
      m.transcript.forEach(addItem);
      events.replaceChildren();
      m.activity.forEach(addEvent);
      setBusy(m.busy);
      setAgent(m.agent);
      approvals.length = 0; currentApproval = null;
      m.pending.forEach((p) => queueApproval(p.id, p.payload));
      if (!m.pending.length && $("#approval").open) $("#approval").close();
      setModelState(m.model);
      break;
    case "model":
      setModelState(m.value);
      if (stDlg.open && !$("#tab-model").hidden) renderModel();
      break;
    case "item": addItem(m.item); break;
    case "event":
      addEvent(m.event);
      if (m.event.kind === "llm_start" && AGENTS.includes(m.event.agent)) setAgent(m.event.agent);
      break;
    case "busy": setBusy(m.value); break;
    case "agent": setAgent(m.value); break;
    case "approval_request": queueApproval(m.id, m.payload); break;
    case "approval_closed": closeApproval(m.id); break;
    case "reset": chat.replaceChildren(); showEmpty(); setAgent("triage"); break;
    case "toast": toast(m.text, m.level); break;
  }
}

/* ===================== chat ===================== */
function setAgent(a) {
  if (!AGENTS.includes(a)) return;
  const changed = a !== currentAgent;
  currentAgent = a;
  document.querySelectorAll(".chip").forEach((c) => {
    const on = c.dataset.agent === a;
    c.classList.toggle("active", on);
    c.setAttribute("aria-current", on ? "true" : "false");
    if (on && changed) { c.classList.remove("pulse"); void c.offsetWidth; c.classList.add("pulse"); }
  });
}

function setBusy(v) {
  busy = !!v;
  $("#working").hidden = !busy;
  $("#send").disabled = busy;
  $("#btn-new").disabled = busy;
  if (busy) scrollChat();
}

function scrollChat() { chat.scrollTop = chat.scrollHeight; }

function showEmpty() {
  chat.append(h("div", { class: "empty", id: "empty" },
    h("h1", { text: "What should we check on your PC?" }),
    h("p", { text: "Four agents hand work to each other. Reports are shown exactly; anything that changes your PC asks you first." }),
    h("div", { class: "suggestions" }, SUGGESTIONS.map((s) =>
      h("button", { class: "btn", type: "button", onclick: () => submit(s) }, s))),
    h("div", { class: "legend" },
      h("span", {}, "🔧 normal: the AI reads the result"),
      h("span", {}, "📋 show & stop: exact report to you"),
      h("span", {}, "⚠️ needs approval: you approve / edit / reject"),
      h("span", {}, "🧭 handoff: one agent passes you to another"))));
}

function avatar(agent) {
  return h("div", { class: `avatar a-${agent}`, "aria-hidden": "true", text: (agent || "?")[0].toUpperCase() });
}

function addItem(it) {
  $("#empty")?.remove();
  let node;
  switch (it.type) {
    case "user":
      node = h("div", { class: "msg user" }, h("div", { class: "bubble", text: it.text }));
      break;
    case "ai": {
      const b = h("div", { class: "bubble md" });
      b.innerHTML = md(it.text); // md() escapes all input before formatting
      node = h("div", { class: "msg ai" }, avatar(AGENTS.includes(it.agent) ? it.agent : currentAgent), b);
      break;
    }
    case "handoff":
      node = h("div", { class: "handoff" }, h("span", {}, "🧭 ", it.text));
      break;
    case "show":
      node = renderShow(it);
      break;
    case "action":
      node = h("div", { class: "card action" },
        h("div", { class: "card-head" }, h("span", { class: "card-icon", "aria-hidden": "true", text: "⚠️" }),
          h("strong", { text: "Action result" }), h("code", { class: "tool-name", text: it.tool })),
        h("pre", { class: "pre", text: it.text }));
      break;
    case "note":
      node = h("div", { class: "note" }, h("span", {}, `${it.icon || "🔁"} `, it.text));
      break;
    case "error":
      node = h("div", { class: "msg error" }, h("div", { class: "bubble", text: "⚠ " + it.text }));
      break;
    default:
      return;
  }
  chat.append(node);
  scrollChat();
}

/* ===================== TYPE A reports ===================== */
function renderShow(it) {
  let rep = null;
  try { rep = JSON.parse(it.text); } catch { /* plain text */ }
  if (!rep || rep.kind !== "report") {
    return h("div", { class: "card report" },
      h("div", { class: "card-head" }, h("strong", { text: "Tool output" }), h("code", { class: "tool-name", text: it.tool })),
      h("pre", { class: "pre", text: it.text }));
  }
  const body = h("div", { class: "card-body" });
  fillSections(body, rep.sections);
  const head = h("div", { class: "card-head" },
    h("span", { class: "card-icon", "aria-hidden": "true", text: rep.icon || "📋" }),
    h("strong", { text: rep.title }),
    h("span", { class: "badge ok", title: "Shown exactly as the tool returned it. The AI did not rewrite it.", text: "exact tool output" }),
    h("code", { class: "tool-name", text: it.tool }));

  if (rep.live === "system") {
    let timer = null;
    const btn = h("button", { class: "btn small ghost live-btn", type: "button", "aria-pressed": "false", text: "● Live" });
    btn.addEventListener("click", () => {
      if (timer) { clearInterval(timer); timer = null; btn.setAttribute("aria-pressed", "false"); btn.classList.remove("on"); return; }
      const tick = () => api("/api/system").then((r) => fillSections(body, r.sections)).catch(() => {});
      tick();
      timer = setInterval(tick, 2500);
      btn.setAttribute("aria-pressed", "true"); btn.classList.add("on");
    });
    head.append(btn);
  }
  return h("div", { class: "card report" }, head, body);
}

function fillSections(body, sections) {
  body.replaceChildren(...(sections || []).map(renderSection));
}

function meter(percent) {
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  const lvl = p < 60 ? "ok" : p < 85 ? "warn" : "bad";
  const fill = h("span");
  fill.style.width = `${p}%`;
  return h("div", { class: `meter ${lvl}`, role: "meter", "aria-valuemin": "0", "aria-valuemax": "100",
                    "aria-valuenow": String(Math.round(p)), "aria-label": `${Math.round(p)} percent` }, fill);
}

function renderSection(s) {
  const wrap = h("section", { class: "sec" });
  if (s.title) wrap.append(h("h4", { text: s.title }));
  if (s.type === "metrics") {
    wrap.append(h("div", { class: "metrics" }, (s.items || []).map((m) =>
      h("div", { class: "metric" },
        h("div", { class: "metric-top" }, h("span", { class: "metric-label", text: m.label }), h("span", { class: "metric-val", text: m.value })),
        m.percent != null ? meter(m.percent) : null))));
  } else if (s.type === "table") {
    if (!s.rows || !s.rows.length) {
      wrap.append(h("p", { class: "muted", text: "Nothing found." }));
    } else {
      wrap.append(h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, s.columns.map((c) => h("th", { scope: "col", text: c })))),
        h("tbody", {}, s.rows.map((r) => h("tr", {}, r.map((c) => h("td", { text: c }))))))));
    }
  } else if (s.type === "text") {
    wrap.append(h("p", { text: s.text }));
  }
  if (s.footer) wrap.append(h("p", { class: "muted small", text: s.footer }));
  return wrap;
}

/* ===================== activity panel ===================== */
const A = (a) => h("span", { class: `agent-tag a-${a}`, text: a || "?" });
const B = (t) => h("strong", { text: t });
const M = (t) => h("span", { class: "muted", text: t });
const kindBadge = (k) => h("span", { class: `kind k-${k}`, text: KIND_LABEL[k] || k });

function fmtEvent(e) {
  switch (e.kind) {
    case "user": return { icon: "👤", parts: [B("you"), ": ", e.text] };
    case "llm_start": return { icon: "🧠", parts: [A(e.agent), ` calling ${e.provider || "LLM"} `, M(`(${e.model ? e.model + " · " : ""}${e.messages} msgs)`), e.waited ? M(` · waited ${e.waited}s for rate limit`) : null] };
    case "llm_end": return { icon: "💬", parts: [A(e.agent), " ", M(`${e.latency}s · ${fmt(e.input_tokens)}→${fmt(e.output_tokens)} tok → `), B(e.tool_calls && e.tool_calls.length ? e.tool_calls.join(", ") : "final answer")] };
    case "tool_start": return { icon: KIND_ICON[e.tool_kind] || "🔧", parts: [A(e.agent), " → ", B(e.tool), kindBadge(e.tool_kind), e.args && e.args !== "{}" ? h("div", { class: "ev-args", text: e.args }) : null] };
    case "tool_end":
      if (e.tool_kind === "handoff") return { icon: "🧭", cls: "handoff", parts: [B("HANDOFF "), A(e.agent), " → ", A(String(e.tool || "").replace("transfer_to_", ""))] };
      return { icon: "✅", parts: [B(e.tool), " ", M(`${e.ms} ms`), e.preview ? h("div", { class: "ev-args", text: e.preview }) : null] };
    case "tool_error": return { icon: "❌", cls: "error", parts: [B(e.tool), ": ", e.error] };
    case "interrupt": return { icon: "⏸", cls: "approval", parts: [A(e.agent), " paused ", B(e.tool), " for your approval"] };
    case "approval_wait": return { icon: "🙋", cls: "approval", parts: ["waiting for you: ", e.summary] };
    case "approval_approve": return { icon: "👍", cls: "approval", parts: [B("approved "), e.tool] };
    case "approval_edit": return { icon: "✏️", cls: "approval", parts: [B("edited & approved "), e.tool] };
    case "approval_reject": return { icon: "👎", cls: "approval", parts: [B("rejected "), e.tool] };
    case "turn_total": return { icon: "🧾", cls: "total", parts: [B(`Message cost: ${fmt(e.total_tokens)} tokens`),
      M(` · in ${fmt(e.input_tokens)} · out ${fmt(e.output_tokens)} · ${e.llm_calls} LLM call${e.llm_calls === 1 ? "" : "s"}`)] };
    case "throttle": return { icon: "⏳", parts: [`${e.reason} limit near, waiting ${e.seconds}s`] };
    case "rate_limited": return { icon: "🚫", cls: "error", parts: [B("429 rate limit: "), e.error] };
    case "llm_error": return { icon: "❌", cls: "error", parts: [B("LLM error: "), e.error] };
    default: return null;
  }
}

function addEvent(e) {
  const f = fmtEvent(e);
  if (!f) return;
  const stick = events.scrollHeight - events.scrollTop - events.clientHeight < 40;
  events.append(h("li", { class: `ev ${f.cls ? "ev-" + f.cls : ""}` },
    h("time", { text: e.ts }), h("span", { "aria-hidden": "true", text: f.icon }), h("div", { class: "ev-body" }, f.parts)));
  while (events.children.length > 600) events.firstElementChild.remove();
  if (stick) events.scrollTop = events.scrollHeight;
}

/* ===================== TYPE B approvals ===================== */
const approvals = [];
let currentApproval = null;
const apDlg = $("#approval");

function queueApproval(id, payload) {
  if (currentApproval?.id === id || approvals.some((a) => a.id === id)) return;
  approvals.push({ id, payload });
  if (!currentApproval) nextApproval();
}

function nextApproval() {
  currentApproval = approvals.shift() || null;
  if (!currentApproval) { if (apDlg.open) apDlg.close(); return; }
  const p = currentApproval.payload || {};
  $("#ap-title").textContent = p.summary || "Approve this action?";
  $("#ap-tool").textContent = p.tool || "";
  const w = $("#ap-warning");
  w.hidden = !p.warning;
  w.textContent = p.warning ? `⚠ ${p.warning}` : "";
  $("#ap-details").textContent = (p.details || []).join("\n") || "(no details)";
  $("#ap-args").value = JSON.stringify(p.args || {}, null, 2);
  $("#ap-reason").value = "";
  $("#ap-err").textContent = "";
  $(".edit-box", apDlg).open = false;
  if (!apDlg.open) apDlg.showModal();
  $("#ap-approve").focus();
}

function decide(decision) {
  if (!currentApproval) return;
  const out = { decision };
  if (decision === "edit") {
    try {
      const a = JSON.parse($("#ap-args").value);
      if (!a || typeof a !== "object" || Array.isArray(a)) throw new Error("must be a JSON object");
      out.args = a;
    } catch (err) {
      $(".edit-box", apDlg).open = true;
      $("#ap-err").textContent = `Invalid JSON: ${err.message}`;
      return;
    }
  }
  if (decision === "reject") out.reason = $("#ap-reason").value.trim() || "user said no";
  if (send({ type: "approval", id: currentApproval.id, decision: out })) {
    currentApproval = null;
    nextApproval();
  }
}

function closeApproval(id) {
  const i = approvals.findIndex((a) => a.id === id);
  if (i >= 0) approvals.splice(i, 1);
  if (currentApproval?.id === id) { currentApproval = null; nextApproval(); }
}

$("#ap-approve").addEventListener("click", () => decide("approve"));
$("#ap-edit").addEventListener("click", () => decide("edit"));
$("#ap-reject").addEventListener("click", () => decide("reject"));
apDlg.addEventListener("cancel", (ev) => ev.preventDefault()); // Esc must not silently skip a decision

/* ===================== settings ===================== */
const stDlg = $("#settings");
let usageTimer = null;
let configCache = null;

function stat(label, value) { return h("div", { class: "stat" }, h("b", { text: String(value) }), h("span", { text: label })); }

const SOURCE_BADGE = {
  provider: ["ok", (p) => `from ${p}`],
  estimate: ["warn", () => "estimate"],
  local: ["info", () => "counted locally"],
};

function usageRow(m, providerLabel) {
  const left = Math.max(m.limit - m.used, 0);
  const [cls, text] = SOURCE_BADGE[m.source] || SOURCE_BADGE.local;
  return h("div", { class: "usage-row" },
    h("div", { class: "metric-top" },
      h("span", {}, h("span", { class: "metric-label", text: m.label }), " ",
        h("span", { class: `badge ${cls}`, text: text(providerLabel) })),
      h("span", {}, B(`${fmt(left)} left`), M(` · ${fmt(m.used)} / ${fmt(m.limit)} used`))),
    meter(m.limit ? (m.used / m.limit) * 100 : 0),
    h("div", { class: "muted small", text: m.note }));
}

let usageProvider = null; // which provider the Usage tab shows (default: active one)

function renderUsage(s) {
  const switcher = h("div", { class: "row" }, ...Object.entries(modelState?.providers || {}).map(([name, p]) =>
    h("button", { type: "button", class: `btn small ${name === s.provider ? "primary" : "ghost"}`,
                  "aria-pressed": name === s.provider ? "true" : "false",
                  onclick: () => { usageProvider = name; loadUsage(); } },
      p.label, name === modelState.provider ? " (active)" : "")));
  $("#tab-usage").replaceChildren(...[
    switcher,
    h("div", { class: "usage-head" }, B(`${s.provider_label} · ${s.model || "no model selected"}`),
      h("span", { class: "badge info", text: `plan: ${s.plan}` }),
      h("span", { class: `badge ${s.key_set ? "ok" : "bad"}`, text: s.key_set ? "API key set" : "API key missing" }),
      s.live ? M(`limits updated ${s.headers_age} ago`) : M("no response from this provider yet")),
    ...s.meters.map((m) => usageRow(m, s.provider_label)),
    h("div", { class: "stats" },
      stat("Requests this session", fmt(s.requests)),
      stat("Input tokens (session)", fmt(s.input_tokens)),
      stat("Output tokens (session)", fmt(s.output_tokens)),
      stat("Requests today (DevBox)", fmt(s.day_requests)),
      stat("Last latency", `${s.last_latency.toFixed(1)} s`),
      stat("Avg latency", `${s.avg_latency.toFixed(1)} s`),
      stat("429 rate-limit errors", s.errors_429),
      stat("Other errors", s.errors_other),
      stat("Waited for limits", `${s.throttle_waits}×`)),
    s.last_error ? h("p", { class: "err small", text: `Last error: ${s.last_error}` }) : null,
    h("p", { class: "muted small" },
      `"from ${s.provider_label}" values come from the rate-limit headers on every response. `,
      "\"Counted locally\" values aren't in the headers, so DevBox counts its own calls against the limits in .env. ",
      "Limits apply to your whole organization, so other apps using the same account aren't counted locally. Exact limits: ",
      h("a", { href: s.limits_url, target: "_blank", rel: "noopener noreferrer", text: `${s.provider_label} limits page` }),
      "."),
  ].filter(Boolean));
}

async function loadUsage() {
  const q = usageProvider ? `?provider=${encodeURIComponent(usageProvider)}` : "";
  try { renderUsage(await api(`/api/usage${q}`)); }
  catch (e) { $("#tab-usage").replaceChildren(h("p", { class: "err", text: `Could not load usage: ${e.message}` })); }
}

async function getConfig() {
  if (!configCache) configCache = await api("/api/config");
  return configCache;
}

async function renderAgents() {
  const c = await getConfig();
  $("#tab-agents").replaceChildren(
    h("p", { class: "muted small", text: "Each agent only sees its own tools. transfer_to_* tools are handoffs: they move the conversation to another agent." }),
    ...AGENTS.map((a) => h("div", { class: "agent-card" },
      h("h3", {}, A(a), " agent"),
      h("div", { class: "tool-list" }, (c.agents[a] || []).map((t) =>
        h("span", { class: `tool-pill k-${t.kind}`, title: KIND_LABEL[t.kind] }, `${KIND_ICON[t.kind] || ""} ${t.name}`))))),
    h("p", { class: "muted small", text: "🔧 normal · 📋 show & stop (exact output to you) · ⚠️ needs your approval · 🧭 handoff" }));
}

async function renderConfig() {
  const c = await getConfig();
  const row = (k, v) => [h("dt", { text: k }), h("dd", {}, v)];
  $("#tab-config").replaceChildren(
    h("dl", { class: "kv" },
      row("Active model", modelState ? `${modelState.providers[modelState.provider].label} · ${modelState.model || "(first available)"}` : "?"),
      row("GROQ_API_KEY", c.keys.groq ? "set in .env" : h("span", { class: "muted", text: "not set" })),
      row("OPENAI_API_KEY", c.keys.openai ? "set in .env" : h("span", { class: "muted", text: "not set" })),
      row("NVIDIA_API_KEY", c.keys.nvidia ? "set in .env" : h("span", { class: "muted", text: "not set" })),
      row("Groq fallback limits (RPM / RPD / TPM / TPD)",
          `${fmt(c.limits.groq.rpm)} / ${fmt(c.limits.groq.rpd)} / ${fmt(c.limits.groq.tpm)} / ${fmt(c.limits.groq.tpd)}`),
      row("OpenAI fallback limits (RPM / TPM / RPD)",
          `${fmt(c.limits.openai.rpm)} / ${fmt(c.limits.openai.tpm)} / ${c.limits.openai.rpd ? fmt(c.limits.openai.rpd) : "not set"}`),
      row("NVIDIA limit (RPM)", `${fmt(c.limits.nvidia.rpm)} (counted locally)`),
      row("LangSmith tracing", c.langsmith.enabled
        ? (c.langsmith.key_set
            ? h("span", {}, h("span", { class: "badge ok", text: "on" }), ` → ${c.langsmith.target} · ${c.langsmith.endpoint}`)
            : h("span", { class: "err", text: "on, but LANGSMITH_API_KEY is missing" }))
        : h("span", { class: "muted", text: "off (set LANGSMITH_TRACING=true in .env)" })),
      row("Allowed folders", h("div", {}, c.allowed_roots.map((p) => h("div", {}, h("code", { text: p }))))),
      row("Protected (never touched)", h("div", {}, c.protected_paths.map((p) => h("div", {}, h("code", { text: p }))))),
      row("Server", `http://127.0.0.1:${c.port} (local only)`),
      row("Activity log", h("code", { text: c.logs.activity })),
      row("Action log", h("code", { text: c.logs.actions }))),
    h("p", { class: "muted small", text: "To change any of these, edit .env in the project folder and restart DevBox." }));
}

async function renderHistory() {
  const r = await api("/api/actions");
  $("#tab-history").replaceChildren(
    h("p", { class: "muted small", text: "Every action you approved (newest first)." }),
    r.lines.length ? h("pre", { class: "pre details", text: r.lines.join("\n") }) : h("p", { class: "muted", text: "No actions yet." }));
}

/* ----- Model tab ----- */
let modelState = null;
let modelDraft = null; // { provider, model } being edited in the tab

function setModelState(s) {
  if (!s) return;
  modelState = s;
  const p = s.providers[s.provider];
  const btn = $("#btn-model");
  btn.textContent = `${p ? p.label : s.provider} · ${s.model || "auto"}`;
  btn.setAttribute("aria-label", `Current model: ${btn.textContent}. Change model`);
}

async function renderModel() {
  const tab = $("#tab-model");
  if (!modelState) modelState = await api("/api/llm");
  if (!modelDraft) modelDraft = { provider: modelState.provider, model: modelState.model };
  const providers = modelState.providers;
  const draftP = providers[modelDraft.provider];

  const cards = h("div", { class: "providers", role: "radiogroup", "aria-label": "LLM provider" },
    Object.entries(providers).map(([name, p]) => {
      const radio = h("input", { type: "radio", name: "provider", value: name, disabled: !p.key_set,
                                 checked: name === modelDraft.provider });
      radio.addEventListener("change", () => {
        modelDraft = { provider: name, model: name === modelState.provider ? modelState.model : p.default_model };
        renderModel();
      });
      return h("label", { class: `provider-card ${name === modelDraft.provider ? "selected" : ""} ${p.key_set ? "" : "disabled"}` },
        radio,
        h("span", {}, h("b", { text: p.label }),
          p.key_set ? h("span", { class: "badge ok", text: "API key set" })
                    : h("span", { class: "muted small" }, `Add ${p.env_key} to .env and restart. `,
                        h("a", { href: p.keys_url, target: "_blank", rel: "noopener noreferrer", text: "Get a key" })),
          name === modelState.provider ? h("div", { class: "muted small", text: "active" }) : null));
    }));

  const select = h("select", { id: "model-select", "aria-describedby": "model-help" },
    h("option", { value: "", text: "Loading models…" }));
  const custom = h("input", { type: "text", id: "model-custom", placeholder: "or type a model ID", value: "" });
  const refresh = h("button", { type: "button", class: "btn small ghost", text: "↻ Refresh list" });
  const status = h("p", { id: "model-help", class: "muted small" });
  const apply = h("button", { type: "button", class: "btn primary", text: "Use this model" });

  tab.replaceChildren(
    h("div", { class: "current-model" }, M("Active: "),
      B(`${providers[modelState.provider].label} · ${modelState.model || "first available model"}`)),
    h("h3", { class: "label", text: "1. Provider" }), cards,
    h("h3", { class: "label", text: "2. Model" }),
    h("div", { class: "field" },
      h("label", { for: "model-select", class: "sr-only", text: "Model" }),
      h("div", { class: "row" }, select, refresh),
      h("label", { for: "model-custom", class: "sr-only", text: "Custom model ID" }), custom),
    status,
    h("p", { class: "muted small", text: "DevBox needs a model that supports tool calling. Switching keeps your conversation; the next message uses the new model." }),
    h("div", { class: "row" }, apply));

  const load = async (force = false) => {
    if (!draftP.key_set) { select.replaceChildren(h("option", { value: "", text: "No API key" })); return; }
    status.textContent = "Loading the models available to your key…";
    const r = await api(`/api/models?provider=${encodeURIComponent(modelDraft.provider)}${force ? "&refresh=true" : ""}`);
    const models = [...r.models];
    if (modelDraft.model && !models.includes(modelDraft.model)) models.unshift(modelDraft.model);
    select.replaceChildren(...models.map((id) => h("option", { value: id, text: id, selected: id === modelDraft.model })));
    if (!modelDraft.model && models.length) modelDraft.model = models[0];
    status.textContent = r.error ? `Couldn't load the list: ${r.error}` : `${r.models.length} models available to your ${draftP.label} key.`;
    status.className = r.error ? "err small" : "muted small";
  };
  select.addEventListener("change", () => { modelDraft.model = select.value; custom.value = ""; });
  custom.addEventListener("input", () => { if (custom.value.trim()) modelDraft.model = custom.value.trim(); });
  refresh.addEventListener("click", () => load(true).catch((e) => { status.textContent = e.message; }));
  apply.addEventListener("click", () => {
    const model = custom.value.trim() || select.value || modelDraft.model;
    if (!model) { toast("Pick a model first.", "warn"); return; }
    if (busy) { toast("Wait for the current request to finish.", "warn"); return; }
    send({ type: "set_model", provider: modelDraft.provider, model });
    modelDraft = { provider: modelDraft.provider, model };
  });
  load().catch((e) => { status.textContent = e.message; status.className = "err small"; });
}

const TABS = { model: renderModel, usage: loadUsage, agents: renderAgents, config: renderConfig, history: renderHistory };

function selectTab(name) {
  for (const key of Object.keys(TABS)) {
    const on = key === name;
    const btn = $(`#tab-btn-${key}`);
    btn.setAttribute("aria-selected", on ? "true" : "false");
    btn.tabIndex = on ? 0 : -1;
    $(`#tab-${key}`).hidden = !on;
  }
  clearInterval(usageTimer); usageTimer = null;
  TABS[name]().catch((e) => $(`#tab-${name}`).replaceChildren(h("p", { class: "err", text: e.message })));
  if (name === "usage") usageTimer = setInterval(loadUsage, 2000);
}

document.querySelectorAll(".tab").forEach((btn, i, all) => {
  btn.addEventListener("click", () => selectTab(btn.id.replace("tab-btn-", "")));
  btn.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
    const next = all[(i + (ev.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
    next.focus(); next.click();
  });
});
function openSettings(tab) { modelDraft = null; usageProvider = null; stDlg.showModal(); selectTab(tab); }
$("#btn-settings").addEventListener("click", () => openSettings("model"));
$("#btn-model").addEventListener("click", () => openSettings("model"));
$("#st-close").addEventListener("click", () => stDlg.close());
stDlg.addEventListener("close", () => { clearInterval(usageTimer); usageTimer = null; });
stDlg.addEventListener("click", (ev) => { if (ev.target === stDlg) stDlg.close(); });

/* ===================== composer + toolbar ===================== */
function submit(text) {
  text = (text ?? input.value).trim();
  if (!text) return;
  if (busy) { toast("DevBox is still working on the previous request.", "warn"); return; }
  if (send({ type: "chat", text })) { input.value = ""; autosize(); }
}
function autosize() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; }

$("#composer").addEventListener("submit", (ev) => { ev.preventDefault(); submit(); });
input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); submit(); }
});
input.addEventListener("input", autosize);

$("#btn-new").addEventListener("click", () => send({ type: "reset" }));
$("#btn-clear-log").addEventListener("click", () => events.replaceChildren());
$("#btn-activity").addEventListener("click", (ev) => {
  const layout = $("#layout");
  const hidden = layout.classList.toggle("no-activity");
  ev.currentTarget.setAttribute("aria-pressed", hidden ? "false" : "true");
});
if (window.matchMedia("(max-width: 980px)").matches) $("#btn-activity").click();

setAgent("triage");
connect();
input.focus();
