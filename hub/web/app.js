// Chat window (Milestone 2). Plain JavaScript, no build step.
// The launch token travels as an HttpOnly cookie, so fetch() sends it automatically.
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  messages: $("messages"), empty: $("empty"), input: $("input"), composer: $("composer"),
  send: $("send"), stop: $("stop"), status: $("status"), notices: $("notices"),
  activity: $("activity"), personality: $("personality"), model: $("model"), web: $("web"),
  editCustom: $("edit-custom"), dialog: $("custom-dialog"), customText: $("custom-text"),
  newChat: $("new-chat"), title: $("chat-title"),
};

const LABELS = { fun: "Fun", straight: "Straight", explainer: "Explainer", custom: "Custom" };
let settings = {};
let streaming = false;

// N-REL-1 / R-AUTH-4 / R-AUTH-6: plain words for errors.
const ERRORS = {
  authentication_failed: "You are not signed in to Claude. Run `claude` in a terminal, type /login, choose \"Claude account with subscription\", then restart the app.",
  rate_limit: "You have reached your Claude subscription usage limit.",
  billing_error: "Claude reported a billing problem with this account.",
  server_error: "Claude's servers had a problem.",
  exception: "Something went wrong talking to Claude.",
  network: "Lost connection to the app. Is it still running?",
};

// ---------- Markdown (F-CHAT-3) ----------
marked.setOptions({ gfm: true, breaks: false });
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
});
const renderMarkdown = (md) => DOMPurify.sanitize(marked.parse(md));

function enhanceCode(container) {
  container.querySelectorAll("pre > code").forEach((code) => {
    hljs.highlightElement(code);
    const btn = document.createElement("button");
    btn.className = "copy-code ghost";
    btn.textContent = "Copy";
    btn.onclick = () => copyText(code.innerText, btn);
    code.parentElement.appendChild(btn);
  });
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    const old = btn.textContent; btn.textContent = "Copied"; setTimeout(() => (btn.textContent = old), 1200);
  } catch { btn.textContent = "Copy failed"; }
}

// ---------- messages ----------
function addUserMessage(text) {
  els.empty.hidden = true;
  const div = document.createElement("div");
  div.className = "msg user";
  div.textContent = text;
  els.messages.appendChild(div);
  scrollDown(true);
}

function addBotMessage() {
  const div = document.createElement("div");
  div.className = "msg bot";
  div.innerHTML = '<div class="who"><span>Bot</span><button class="ghost copy-reply" hidden>Copy</button></div><div class="body"></div>';
  els.messages.appendChild(div);
  return { div, body: div.querySelector(".body"), copy: div.querySelector(".copy-reply"), text: "" };
}

function scrollDown(force) {
  const m = els.messages;
  if (force || m.scrollHeight - m.scrollTop - m.clientHeight < 120) m.scrollTop = m.scrollHeight;
}

function setStreaming(on) {
  streaming = on;
  els.send.hidden = on; els.stop.hidden = !on;
  els.newChat.disabled = on;
  if (!on) { els.activity.hidden = true; els.input.focus(); }
}

function setActivity(text) { els.activity.textContent = text; els.activity.hidden = !text; }

// ---------- send / stream ----------
async function send(text) {
  if (streaming || !text.trim()) return;
  addUserMessage(text);
  if (els.title.textContent === "New chat") els.title.textContent = text.slice(0, 48);
  const bot = addBotMessage();
  setStreaming(true);
  setActivity("thinking…");

  let rafPending = false;
  const render = () => { rafPending = false; bot.body.innerHTML = renderMarkdown(bot.text); scrollDown(); };
  let errorShown = false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      // The server answered but refused (busy, bad token...): show its reason, not "lost connection".
      const reason = (await res.json().catch(() => ({}))).error || res.statusText;
      const e = new Error(reason); e.code = "server"; throw e;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.kind === "text") {
          bot.text += ev.text;
          setActivity("");
          if (!rafPending) { rafPending = true; requestAnimationFrame(render); }
        } else if (ev.kind === "tool") {
          setActivity(ev.text === "WebSearch" ? "searching the web…" : ev.text === "WebFetch" ? "reading a page…" : `using ${ev.text}…`);
        } else if (ev.kind === "status") {
          showStatus(ev);
        } else if (ev.kind === "error") {
          showError(bot, ev, text); errorShown = true;
        } else if (ev.kind === "done" && String(ev.terminal_reason || "").startsWith("aborted")) {
          addNote(bot, "(stopped)");
        }
      }
    }
  } catch (err) {
    showError(bot, { code: err.code || "network", detail: err.message }, text); errorShown = true;
  }

  render();
  enhanceCode(bot.body);
  if (bot.text) { bot.copy.hidden = false; bot.copy.onclick = () => copyText(bot.text, bot.copy); }
  else if (!errorShown) addNote(bot, "(no reply)");
  setStreaming(false);
}

function addNote(bot, text) {
  const n = document.createElement("div"); n.className = "note"; n.textContent = text; bot.div.appendChild(n);
}

function showError(bot, ev, originalText) {
  let msg = ev.code === "server" ? `The app could not send this: ${ev.detail}.`
    : ERRORS[ev.code] || `Something went wrong (${ev.code}).`;
  const reset = ev.rate_limit && ev.rate_limit.resets_at;
  if (ev.code === "rate_limit" && reset) msg += ` It resets at ${new Date(reset * 1000).toLocaleString()}.`;
  const box = document.createElement("div"); box.className = "error";
  box.textContent = msg;
  const retry = document.createElement("button"); retry.textContent = "Retry";
  retry.onclick = () => { if (!streaming) { box.remove(); send(originalText); } };
  box.appendChild(retry);
  bot.div.appendChild(box);
  // R-AUTH-6: keep the typed message so it is not lost.
  if (!els.input.value) els.input.value = originalText;
}

// ---------- status line (R-AUTH-8) ----------
function showStatus(s) {
  els.status.textContent = s.ok ? `● ${s.text}` : `▲ ${s.text}`;
  els.status.className = "status " + (s.ok ? "ok" : "bad");
}

// ---------- settings: personality, model, web ----------
async function saveSettings(patch) {
  const res = await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  });
  if (res.ok) settings = (await res.json()).settings;
}

function openCustomDialog() {
  els.customText.value = settings.custom_prompt || "";
  els.dialog.returnValue = ""; // Escape keeps the old value, so reset it (else Escape would save)
  els.dialog.showModal();
}

els.dialog.addEventListener("close", async () => {
  if (els.dialog.returnValue === "save" && els.customText.value.trim()) {
    await saveSettings({ custom_prompt: els.customText.value, personality: "custom" });
  } else if (!settings.custom_prompt) {
    els.personality.value = settings.personality === "custom" ? "straight" : settings.personality;
    await saveSettings({ personality: els.personality.value });
  }
  els.editCustom.hidden = els.personality.value !== "custom";
});

els.personality.onchange = async () => {
  const p = els.personality.value;
  els.editCustom.hidden = p !== "custom";
  if (p === "custom" && !settings.custom_prompt) return openCustomDialog();
  await saveSettings({ personality: p }); // F-PERS-3: applies from the next message
};
els.editCustom.onclick = openCustomDialog;
els.model.onchange = () => saveSettings({ model: els.model.value });
els.web.onchange = () => saveSettings({ web_enabled: els.web.checked });

els.newChat.onclick = async () => {
  if (streaming) return;
  const res = await fetch("/api/new", { method: "POST" });
  if (!res.ok) return; // server kept the old chat (still busy), so keep the window as it is
  els.messages.querySelectorAll(".msg").forEach((m) => m.remove());
  els.empty.hidden = false;
  els.title.textContent = "New chat";
  els.input.focus();
};

// ---------- composer (N-USE-1) ----------
els.composer.onsubmit = (e) => {
  e.preventDefault();
  const text = els.input.value;
  if (!text.trim() || streaming) return;
  els.input.value = ""; autoGrow();
  send(text);
};
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); els.composer.requestSubmit(); }
});
function autoGrow() { els.input.style.height = "auto"; els.input.style.height = els.input.scrollHeight + "px"; }
els.input.addEventListener("input", autoGrow);
els.stop.onclick = () => fetch("/api/stop", { method: "POST" }); // F-CHAT-4

async function refreshStatus() {
  try {
    const s = (await (await fetch("/api/status")).json()).status;
    if (streaming) { setTimeout(refreshStatus, 1500); return; } // check again after this reply
    showStatus(s);
    if (s.pending) setTimeout(refreshStatus, 1500);
    else if (!s.confirmed && s.ok) els.status.textContent += " (confirmed after the first message)";
  } catch { /* init() already reports a dead server */ }
}

// ---------- start ----------
(async function init() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error("rejected");
    const data = await res.json();
    settings = data.settings;
    els.personality.innerHTML = data.personalities.map((p) => `<option value="${p}">${LABELS[p] || p}</option>`).join("");
    els.personality.value = settings.personality;
    els.editCustom.hidden = settings.personality !== "custom";
    els.model.value = settings.model;
    els.web.checked = settings.web_enabled;
    showStatus(data.status);
    if (data.status.pending) setTimeout(refreshStatus, 1500); // sign-in check still running
    else if (!data.status.confirmed && data.status.ok) els.status.textContent += " (confirmed after the first message)";
    if (data.notices.length) { els.notices.textContent = data.notices.join("\n"); els.notices.hidden = false; }
  } catch {
    showStatus({ ok: false, text: "This window is not connected. Start the app with start.bat or python -m hub.server." });
  }
})();
