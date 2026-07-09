"use strict";

const messages = document.getElementById("messages");
const decisions = document.getElementById("decisions");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusTools = document.getElementById("status-tools");
const memorySearch = document.getElementById("memory-search");
const memoryList = document.getElementById("memory-list");
const personaText = document.getElementById("persona-text");

let busy = false;
let pollTimer = null;

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

// --- chat -------------------------------------------------------------

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || busy) return;
  input.value = "";
  addMessage("user", message);
  setBusy(true);
  startPolling(); // confirmations/approvals can arrive mid-turn
  try {
    const data = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    addMessage("assistant", data.reply || "(no reply)");
  } catch (error) {
    addMessage("system", `error: ${error.message}`);
  } finally {
    setBusy(false);
    stopPolling();
    renderDecisions([]); // clear any stale cards
    refreshMemory();
  }
});

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  statusDot.classList.toggle("busy", value);
  statusDot.title = value ? "thinking…" : "idle";
}

// --- decisions (confirmations + TOFU/drift approvals) ------------------

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const data = await api("/api/pending");
      renderDecisions(data.items);
    } catch { /* server briefly busy; try again next tick */ }
  }, 700);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

function renderDecisions(items) {
  const seen = new Set(items.map((item) => item.id));
  for (const el of [...decisions.children]) {
    if (!seen.has(el.dataset.id)) el.remove();
  }
  for (const item of items) {
    if (decisions.querySelector(`[data-id="${item.id}"]`)) continue;
    decisions.appendChild(decisionCard(item));
  }
}

function decisionCard(item) {
  const card = document.createElement("div");
  card.className = `decision ${item.kind === "approve" ? "approve-kind" : ""}`;
  card.dataset.id = item.id;

  const kind = document.createElement("div");
  kind.className = "kind";
  kind.textContent = item.kind === "approve" ? "tool approval" : "confirm destructive action";
  card.appendChild(kind);

  const body = document.createElement("pre");
  body.textContent = item.kind === "approve"
    ? item.prompt
    : `${item.tool_name}\n${JSON.stringify(item.arguments, null, 2)}`;
  card.appendChild(body);

  const actions = document.createElement("div");
  actions.className = "actions";
  for (const [label, allow, cls] of [["Allow", true, "allow"], ["Deny", false, "deny"]]) {
    const button = document.createElement("button");
    button.textContent = label;
    button.className = cls;
    button.addEventListener("click", async () => {
      card.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await api("/api/decision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: item.id, allow }),
        });
      } catch { /* already resolved or timed out */ }
      card.remove();
    });
    actions.appendChild(button);
  }
  card.appendChild(actions);
  return card;
}

// --- side pane: memory + persona ---------------------------------------

let searchTimer = null;
memorySearch.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshMemory, 250);
});

async function refreshMemory() {
  const q = memorySearch.value.trim();
  try {
    const data = await api(`/api/memory${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    memoryList.replaceChildren(
      ...data.items.map((item) => {
        const li = document.createElement("li");
        li.textContent = item.content;
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = item.similarity !== undefined
          ? `similarity ${item.similarity}`
          : new Date(item.created_at).toLocaleString();
        li.appendChild(meta);
        return li;
      })
    );
    if (!data.items.length) {
      const li = document.createElement("li");
      li.textContent = q ? "no matches" : "nothing remembered yet";
      li.className = "muted";
      memoryList.appendChild(li);
    }
  } catch { /* leave the list as-is */ }
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.getElementById("tab-memory").classList.toggle("hidden", tab.dataset.tab !== "memory");
    document.getElementById("tab-persona").classList.toggle("hidden", tab.dataset.tab !== "persona");
  });
}

// --- init ---------------------------------------------------------------

(async function init() {
  try {
    const [persona, status] = await Promise.all([api("/api/persona"), api("/api/status")]);
    personaText.textContent = persona.persona || "(no persona authored)";
    statusTools.textContent = status.started ? status.tools.join(" · ") : "tools connect on first message";
  } catch (error) {
    addMessage("system", `error: ${error.message}`);
  }
  refreshMemory();
})();
