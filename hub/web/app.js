// Chat window (Milestones 2 and 3). Plain JavaScript, no build step.
// The launch token travels as an HttpOnly cookie, so fetch() sends it automatically.
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  messages: $("messages"), empty: $("empty"), input: $("input"), composer: $("composer"),
  send: $("send"), stop: $("stop"), status: $("status"), notices: $("notices"),
  activity: $("activity"), personality: $("personality"), model: $("model"), web: $("web"),
  editCustom: $("edit-custom"), dialog: $("custom-dialog"), customText: $("custom-text"),
  newChat: $("new-chat"), title: $("chat-title"), flash: $("flash"), exportBtn: $("export"),
  sidebar: $("sidebar"), toggleSidebar: $("toggle-sidebar"), search: $("search"),
  chatList: $("chat-list"), chatListEmpty: $("chat-list-empty"), menu: $("chat-menu"),
  settingsBtn: $("open-settings"), settingsDialog: $("settings-dialog"), settingsForm: $("settings-form"),
};

const LABELS = { fun: "Fun", straight: "Straight", explainer: "Explainer", custom: "Custom" };
let settings = {};
let builtinPrompts = {};
let streaming = false;
let currentChatId = null; // null = a new chat that is not saved yet
let switching = false; // New chat or delete in flight: no sending until it is done (review M3)

// N-REL-1 / R-AUTH-4 / R-AUTH-6: plain words for errors.
const ERRORS = {
  authentication_failed: "You are not signed in to Claude. Run `claude` in a terminal, type /login, choose \"Claude account with subscription\", then restart the app.",
  rate_limit: "You have reached your Claude subscription usage limit.",
  billing_error: "Claude reported a billing problem with this account.",
  server_error: "Claude's servers had a problem.",
  exception: "Something went wrong talking to Claude.",
  network: "Lost connection to the app. Is it still running?",
};

// Small fetch helper: JSON in, JSON out, throws with the server's reason.
async function api(method, url, body) {
  const opts = { method };
  if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function flash(text, bad) {
  els.flash.textContent = text;
  els.flash.className = "flash" + (bad ? " bad" : "");
  els.flash.hidden = false;
  clearTimeout(flash.timer);
  flash.timer = setTimeout(() => (els.flash.hidden = true), 6000);
}

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
  // F-PERS-4: the bot's name and avatar from Settings. textContent, never HTML.
  const div = document.createElement("div");
  div.className = "msg bot";
  div.innerHTML = '<div class="who"><span><span class="avatar"></span><span class="name"></span></span><button class="ghost copy-reply" hidden>Copy</button></div><div class="body"></div>';
  div.querySelector(".avatar").textContent = settings.avatar || "";
  div.querySelector(".name").textContent = settings.bot_name || "Bot";
  els.messages.appendChild(div);
  return { div, body: div.querySelector(".body"), copy: div.querySelector(".copy-reply"), text: "" };
}

// Final look of a bot reply: Markdown, highlighted code, Copy buttons.
function finishBot(bot) {
  bot.body.innerHTML = renderMarkdown(bot.text);
  enhanceCode(bot.body);
  if (bot.text) { bot.copy.hidden = false; bot.copy.onclick = () => copyText(bot.text, bot.copy); }
}

function addChatNote(text, before) {
  const n = document.createElement("div"); n.className = "chat-note"; n.textContent = text;
  els.messages.insertBefore(n, before || null);
}

function clearMessages() {
  els.messages.querySelectorAll(".msg, .chat-note").forEach((m) => m.remove());
  els.empty.hidden = false;
}

function scrollDown(force) {
  const m = els.messages;
  if (force || m.scrollHeight - m.scrollTop - m.clientHeight < 120) m.scrollTop = m.scrollHeight;
}

function setStreaming(on) {
  streaming = on;
  els.send.hidden = on; els.stop.hidden = !on;
  els.newChat.disabled = on;
  els.chatList.classList.toggle("locked", on);
  if (!on) { els.activity.hidden = true; els.input.focus(); }
}

function setActivity(text) { els.activity.textContent = text; els.activity.hidden = !text; }

function setCurrentChat(id, title) {
  currentChatId = id;
  els.title.textContent = title || "New chat";
  els.exportBtn.hidden = id === null;
  els.chatList.querySelectorAll("li").forEach((li) => li.classList.toggle("active", Number(li.dataset.id) === id));
}

// ---------- send / stream ----------
async function send(text) {
  if (streaming || switching || !text.trim()) return;
  addUserMessage(text);
  const bot = addBotMessage();
  setStreaming(true);
  setActivity("thinking…");

  let rafId = 0; // queued redraw, 0 = none
  const render = () => { rafId = 0; bot.body.innerHTML = renderMarkdown(bot.text); scrollDown(); };
  let errorShown = false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, chat_id: currentChatId }),
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
          if (!rafId) rafId = requestAnimationFrame(render);
        } else if (ev.kind === "chat") {
          setCurrentChat(ev.id, ev.title); // F-HIST-1: the new chat is saved now
          refreshChats();
        } else if (ev.kind === "chat_removed") {
          // The first message failed, so the server dropped the empty chat; Retry starts a new one.
          if (currentChatId === ev.id) setCurrentChat(null);
          refreshChats();
        } else if (ev.kind === "note") {
          addChatNote(ev.text, bot.div); // F-HIST-4: restored from saved history
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

  // Cancel any queued redraw first: it would run after enhanceCode and wipe the
  // highlighting and code Copy buttons (found in review).
  if (rafId) cancelAnimationFrame(rafId);
  finishBot(bot);
  if (!bot.text && !errorShown) addNote(bot, "(no reply)");
  setStreaming(false);
  refreshChats(); // this chat moves to the top
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

// ---------- history sidebar (F-HIST-2, F-HIST-3, F-HIST-4) ----------
function formatDate(ts) {
  const d = new Date(ts * 1000);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString();
}

let refreshSeq = 0; // only the newest list request may draw (fast typing in search)
let renaming = false; // a redraw would remove the rename box; finishing a rename redraws anyway
async function refreshChats() {
  if (renaming) return;
  const seq = ++refreshSeq;
  try {
    const q = els.search.value.trim();
    const { chats } = await api("GET", "/api/chats" + (q ? `?q=${encodeURIComponent(q)}` : ""));
    if (seq !== refreshSeq || renaming) return;
    els.chatList.replaceChildren(...chats.map(chatItem));
    els.chatListEmpty.hidden = chats.length > 0;
    els.chatListEmpty.textContent = q ? "No chats match." : "No chats yet.";
  } catch { /* the status line already reports a dead server */ }
}

function chatItem(c) {
  const li = document.createElement("li");
  li.dataset.id = c.id;
  li.classList.toggle("active", c.id === currentChatId);
  const info = document.createElement("div"); info.className = "info";
  const name = document.createElement("div"); name.className = "name"; name.textContent = c.title; name.title = c.title;
  const date = document.createElement("div"); date.className = "date"; date.textContent = formatDate(c.updated_at);
  info.append(name, date);
  const more = document.createElement("button"); more.type = "button"; more.className = "more"; more.textContent = "⋯";
  more.title = "Rename, export or delete";
  more.onclick = (e) => { e.stopPropagation(); const r = more.getBoundingClientRect(); openMenu(c, r.left, r.bottom); };
  li.append(info, more);
  li.onclick = () => openChat(c.id);
  li.oncontextmenu = (e) => { e.preventDefault(); openMenu(c, e.clientX, e.clientY); }; // right-click
  return li;
}

async function openChat(id) {
  if (streaming || switching || id === currentChatId) return;
  switching = true; // no sending until the chat is on screen (review M3)
  try {
    const { chat, messages } = await api("GET", `/api/chats/${id}`);
    // N-PERF-3: only the open chat keeps a live Claude process, so close the old one now.
    await api("POST", "/api/new").catch(() => {});
    clearMessages();
    for (const m of messages) {
      if (m.role === "user") { addUserMessage(m.text); continue; }
      const bot = addBotMessage(); bot.text = m.text; finishBot(bot);
      if (m.status === "stopped") addNote(bot, "(stopped)");
    }
    els.empty.hidden = messages.length > 0;
    setCurrentChat(chat.id, chat.title);
    scrollDown(true);
    els.sidebar.classList.remove("open");
    els.input.focus();
  } catch (err) {
    flash(`Could not open the chat: ${err.message}.`, true);
    refreshChats();
  } finally { switching = false; }
}

// Right-click (or ⋯) menu: rename, export, delete.
let menuChat = null;
function openMenu(c, x, y) {
  menuChat = c;
  els.menu.hidden = false;
  const w = els.menu.offsetWidth, h = els.menu.offsetHeight;
  els.menu.style.left = Math.min(x, innerWidth - w - 8) + "px";
  els.menu.style.top = Math.min(y, innerHeight - h - 8) + "px";
  els.menu.querySelector("button").focus();
}
function closeMenu() { els.menu.hidden = true; }
document.addEventListener("click", (e) => { if (!els.menu.contains(e.target)) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
window.addEventListener("blur", closeMenu);

els.menu.onclick = async (e) => {
  const act = e.target.dataset.act;
  if (!act || !menuChat) return;
  const c = menuChat; closeMenu();
  if (act === "rename") startRename(c);
  else if (act === "export") exportChat(c.id);
  else if (act === "delete") deleteChat(c);
};

function startRename(c) {
  const li = els.chatList.querySelector(`li[data-id="${c.id}"]`);
  if (!li) return;
  const info = li.querySelector(".info");
  const input = document.createElement("input");
  input.value = c.title; input.maxLength = 200;
  info.replaceChildren(input);
  input.onclick = (e) => e.stopPropagation();
  input.focus(); input.select();
  renaming = true;
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    renaming = false;
    const title = input.value.trim();
    if (save && title && title !== c.title) {
      try {
        await api("PATCH", `/api/chats/${c.id}`, { title });
        if (c.id === currentChatId) els.title.textContent = title;
      } catch (err) { flash(`Could not rename: ${err.message}.`, true); }
    }
    refreshChats();
  };
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  };
  input.onblur = () => finish(true);
}

async function deleteChat(c) {
  if (c.id === currentChatId && streaming) return;
  if (!confirm(`Delete "${c.title}"? This also deletes its saved transcript and cannot be undone.`)) return;
  switching = true;
  try {
    const res = await api("DELETE", `/api/chats/${c.id}`);
    if (c.id === currentChatId) { clearMessages(); setCurrentChat(null); }
    if (res.transcripts_failed) flash("Chat deleted, but its transcript file could not be removed (another program may have it open).", true);
    else flash("Chat deleted.");
  } catch (err) { flash(`Could not delete: ${err.message}.`, true); }
  finally { switching = false; }
  refreshChats();
}

async function exportChat(id) { // F-HIST-5
  try {
    const { path } = await api("POST", `/api/chats/${id}/export`);
    flash(`Saved to ${path}`);
  } catch (err) { flash(`Could not export: ${err.message}.`, true); }
}

let searchTimer = 0;
els.search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(refreshChats, 250); });
els.toggleSidebar.onclick = () => els.sidebar.classList.toggle("open");
els.exportBtn.onclick = () => currentChatId !== null && exportChat(currentChatId);

// ---------- status line (R-AUTH-8) ----------
function showStatus(s) {
  els.status.textContent = s.ok ? `● ${s.text}` : `▲ ${s.text}`;
  els.status.className = "status " + (s.ok ? "ok" : "bad");
}

// ---------- settings: top bar quick choices ----------
async function saveSettings(patch) {
  const data = await api("POST", "/api/settings", patch); // throws with the server's reason
  applySettings(data.settings);
}

function applySettings(s) {
  settings = s;
  els.personality.value = s.personality;
  els.editCustom.hidden = s.personality !== "custom";
  els.model.value = s.model;
  els.web.checked = s.web_enabled;
  applyTheme(s.theme);
}

// N-USE-2: "system" follows the OS. Light or dark is forced on <html> and on the code colours.
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme; else delete root.dataset.theme;
  const light = $("hljs-light"), dark = $("hljs-dark");
  light.media = theme === "light" ? "all" : theme === "dark" ? "not all" : "(prefers-color-scheme: light)";
  dark.media = theme === "dark" ? "all" : theme === "light" ? "not all" : "(prefers-color-scheme: dark)";
}

function openCustomDialog() {
  els.customText.value = settings.custom_prompt || "";
  els.dialog.returnValue = ""; // Escape keeps the old value, so reset it (else Escape would save)
  els.dialog.showModal();
}

els.dialog.addEventListener("close", async () => {
  try {
    if (els.dialog.returnValue === "save" && els.customText.value.trim()) {
      await saveSettings({ custom_prompt: els.customText.value, personality: "custom" });
    } else if (!settings.custom_prompt) {
      const p = settings.personality === "custom" ? "straight" : settings.personality;
      await saveSettings({ personality: p });
    } else {
      applySettings(settings); // cancelled: show the saved choice again
    }
  } catch (err) { flash(`Could not save: ${err.message}.`, true); applySettings(settings); }
});

const quickSave = (patch) => saveSettings(patch).catch((err) => { flash(`Could not save: ${err.message}.`, true); applySettings(settings); });
els.personality.onchange = () => {
  const p = els.personality.value;
  els.editCustom.hidden = p !== "custom";
  if (p === "custom" && !settings.custom_prompt) return openCustomDialog();
  quickSave({ personality: p }); // F-PERS-3: applies from the next message
};
els.editCustom.onclick = openCustomDialog;
els.model.onchange = () => quickSave({ model: els.model.value });
els.web.onchange = () => quickSave({ web_enabled: els.web.checked });

// ---------- Settings page (M3 check 3) ----------
const promptBoxes = {}; // personality -> textarea

function buildPromptEditors() {
  const box = $("set-prompts");
  box.replaceChildren();
  for (const [key, text] of Object.entries(builtinPrompts)) {
    const head = document.createElement("div"); head.className = "prompt-head";
    const label = document.createElement("span"); label.textContent = LABELS[key] || key;
    const reset = document.createElement("button"); reset.type = "button"; reset.className = "ghost"; reset.textContent = "Reset";
    const ta = document.createElement("textarea"); ta.rows = 3; ta.maxLength = 20000;
    reset.onclick = () => { ta.value = text; };
    head.append(label, reset);
    box.append(head, ta);
    promptBoxes[key] = ta;
  }
}

function openSettings() {
  $("set-bot-name").value = settings.bot_name;
  $("set-avatar").value = settings.avatar;
  $("set-model").value = settings.model;
  $("set-theme").value = settings.theme;
  $("set-web").checked = settings.web_enabled;
  for (const [key, ta] of Object.entries(promptBoxes)) ta.value = settings.prompts[key] || builtinPrompts[key];
  $("set-custom").value = settings.custom_prompt;
  $("settings-error").hidden = true;
  $("delete-all-confirm").hidden = true; $("delete-all").hidden = false;
  els.settingsDialog.showModal();
}

els.settingsForm.onsubmit = async (e) => {
  e.preventDefault();
  const prompts = {};
  for (const [key, ta] of Object.entries(promptBoxes)) {
    // Only edited prompts are stored. Built-in text (or empty) means "use the built-in".
    if (ta.value.trim() && ta.value.trim() !== builtinPrompts[key].trim()) prompts[key] = ta.value;
  }
  const custom = $("set-custom").value;
  const patch = {
    bot_name: $("set-bot-name").value, avatar: $("set-avatar").value,
    model: $("set-model").value, theme: $("set-theme").value, web_enabled: $("set-web").checked,
    prompts, custom_prompt: custom,
  };
  if (!custom.trim() && settings.personality === "custom") patch.personality = "straight";
  try {
    await saveSettings(patch);
    els.settingsDialog.close();
    flash("Settings saved.");
  } catch (err) {
    const box = $("settings-error"); box.textContent = `Could not save: ${err.message}.`; box.hidden = false;
  }
};
$("settings-cancel").onclick = () => els.settingsDialog.close();
els.settingsBtn.onclick = openSettings;

// D-3: "Delete all history" needs a second click to confirm.
$("delete-all").onclick = () => { $("delete-all").hidden = true; $("delete-all-confirm").hidden = false; };
$("delete-all-no").onclick = () => { $("delete-all").hidden = false; $("delete-all-confirm").hidden = true; };
$("delete-all-yes").onclick = async () => {
  switching = true;
  try {
    const res = await api("POST", "/api/history/delete-all", { confirm: "DELETE" });
    clearMessages(); setCurrentChat(null);
    els.settingsDialog.close();
    if (res.transcripts_failed) flash(`All chats deleted, but ${res.transcripts_failed} transcript files could not be removed (another program may have them open).`, true);
    else flash("All history deleted.");
  } catch (err) {
    const box = $("settings-error"); box.textContent = `Could not delete: ${err.message}.`; box.hidden = false;
  } finally { switching = false; }
  $("delete-all").hidden = false; $("delete-all-confirm").hidden = true;
  refreshChats();
};

// ---------- new chat ----------
els.newChat.onclick = async () => {
  if (streaming || switching) return;
  switching = true;
  try { await api("POST", "/api/new"); } catch { return; } // server kept the old chat (still busy)
  finally { switching = false; }
  clearMessages();
  setCurrentChat(null);
  els.sidebar.classList.remove("open");
  els.input.focus();
};

// ---------- composer (N-USE-1) ----------
els.composer.onsubmit = (e) => {
  e.preventDefault();
  const text = els.input.value;
  if (!text.trim() || streaming || switching) return;
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
    builtinPrompts = data.builtin_prompts;
    els.personality.innerHTML = data.personalities.map((p) => `<option value="${p}">${LABELS[p] || p}</option>`).join("");
    buildPromptEditors();
    applySettings(data.settings);
    showStatus(data.status);
    if (data.status.pending) setTimeout(refreshStatus, 1500); // sign-in check still running
    else if (!data.status.confirmed && data.status.ok) els.status.textContent += " (confirmed after the first message)";
    if (data.notices.length) { els.notices.textContent = data.notices.join("\n"); els.notices.hidden = false; }
    refreshChats();
  } catch {
    showStatus({ ok: false, text: "This window is not connected. Start the app with start.bat or python -m hub.server." });
  }
})();
