// Chat window (Milestones 2 to 5). Plain JavaScript, no build step.
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
  talkTo: $("talk-to"), personalityBox: $("personality-box"), modelBox: $("model-box"), webBox: $("web-box"),
  rolesBtn: $("open-roles"), rolesDialog: $("roles-dialog"), roleForm: $("role-form"), roleList: $("role-list"),
};

const LABELS = { fun: "Fun", straight: "Straight", explainer: "Explainer", custom: "Custom" };
let settings = {};
let builtinPrompts = {};
let streaming = false;
let currentChatId = null; // null = a new chat that is not saved yet
let switching = false; // New chat or delete in flight: no sending until it is done (review M3)
// The reply in flight: { chatId, title, bot }. It keeps streaming (and is saved by the server)
// while another chat is on screen. Only one runs at a time (N-PERF-3: one live Claude process).
let turn = null;
// Milestone 5 (G-AGENT-3): a chat talks to plain Chat (null) or to one role (its id).
let roles = []; // from /api/roles
let toolCatalog = {};
let currentRole = null; // role of the chat on screen
let newChatRole = null; // "Talk to" choice for the next new chat
const roleById = (id) => roles.find((r) => r.id === id);

// N-REL-1 / R-AUTH-4 / R-AUTH-6: plain words for errors.
const ERRORS = {
  authentication_failed: "You are not signed in to Claude. Run `claude` in a terminal, type /login, choose \"Claude account with subscription\", then restart the app.",
  rate_limit: "You have reached your Claude subscription usage limit.",
  billing_error: "Claude reported a billing problem with this account.",
  server_error: "Claude could not be reached, or its servers had a problem. Check your internet connection, then press Retry.",
  invalid_request: "Claude could not handle this message. Try rewording it or starting a new chat.",
  unknown: "Something went wrong talking to Claude.",
  exception: "Something went wrong talking to Claude.",
  network: "Lost connection to the app. Is it still running?",
  role_missing: "This role was deleted. You can still read this chat, but not continue it. Start a new chat to use another role.",
  role_broken: "This role's file has a problem, so it cannot run. Open Roles to fix it.",
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

function addBotMessage(roleId = currentRole) {
  // F-PERS-4: the bot's name and avatar from Settings, or the role's name (G-AGENT-3).
  // textContent, never HTML.
  const div = document.createElement("div");
  div.className = "msg bot";
  div.innerHTML = '<div class="who"><span><span class="avatar"></span><span class="name"></span></span><button class="ghost copy-reply" hidden>Copy</button></div><div class="body"></div>';
  const role = roleId ? roleById(roleId) : null;
  div.querySelector(".avatar").textContent = roleId ? "🛠" : settings.avatar || "";
  div.querySelector(".name").textContent = roleId ? (role ? role.name : "Deleted role") : settings.bot_name || "Bot";
  els.messages.appendChild(div);
  return { div, body: div.querySelector(".body"), copy: div.querySelector(".copy-reply"), text: "" };
}

// Final look of a bot reply: Markdown, highlighted code, Copy buttons.
function finishBot(bot) {
  bot.body.innerHTML = renderMarkdown(bot.text);
  enhanceCode(bot.body);
  if (bot.text) { bot.copy.hidden = false; bot.copy.onclick = () => copyText(bot.text, bot.copy); }
}

// F-WEB-2: a folded "Sources (n)" list under a reply that used the web. Web data is
// information, not instructions: titles go in as text, only http(s) links, and links open in a
// new tab with no way back to this window (noopener) and no referrer.
function showSources(bot, sources) {
  const list = (sources || []).filter((x) => /^https?:\/\//i.test(x.url));
  bot.sources = list;
  let box = bot.div.querySelector(".sources");
  if (!list.length) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement("details"); box.className = "sources";
    box.append(document.createElement("summary"), document.createElement("ul"));
    bot.body.after(box); // under the reply text, above any note or error
  }
  box.querySelector("summary").textContent = `Sources (${list.length})`;
  box.querySelector("ul").replaceChildren(...list.map((x) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = x.url; a.target = "_blank"; a.rel = "noopener noreferrer";
    let host = ""; try { host = new URL(x.url).hostname.replace(/^www\./, ""); } catch { /* shown as is */ }
    a.textContent = x.title || host || x.url;
    a.title = x.url;
    const h = document.createElement("span"); h.className = "host"; h.textContent = host;
    li.append(a, " ", h);
    return li;
  }));
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
  // Sidebar and New chat stay usable: switching chats mid-reply is allowed.
  if (!on) { els.activity.hidden = true; els.input.focus(); }
  showRoleControls();
}

function setActivity(text) { els.activity.textContent = text; els.activity.hidden = !text; }

// Is the running reply on screen? Switching away detaches its element, switching back re-attaches it.
function turnVisible() { return !!turn && els.messages.contains(turn.bot.div); }
// The chat an openChat is heading to (undefined = none). During that switch the old screen is
// still in the DOM but about to go, so judge "on screen" against where the switch lands (review M3).
let switchTarget = undefined;
function turnOnScreen() {
  return turnVisible() && (switchTarget === undefined || switchTarget === turn.chatId);
}

// After a switch: say where the reply is still being written, so Stop makes sense.
function showTurnActivity() {
  if (!turn) return;
  if (turnVisible()) setActivity(turn.bot.text ? "" : "thinking…");
  else setActivity(`Still answering in "${turn.title}"…`);
}

// role: the chat's role id. Left out: a new chat uses the "Talk to" choice, a saved one keeps its role.
function setCurrentChat(id, title, role) {
  currentChatId = id;
  currentRole = role !== undefined ? role : id === null ? newChatRole : currentRole;
  els.title.textContent = title || "New chat";
  els.exportBtn.hidden = id === null;
  els.chatList.querySelectorAll("li").forEach((li) => li.classList.toggle("active", Number(li.dataset.id) === id));
  showRoleControls();
}

// G-AGENT-3: "Talk to" can be changed only before a chat starts. A role chat runs with the
// role's own instructions, tools and model, so personality, model and web controls are hidden.
function fillTalkTo() {
  const opts = [["", "Chat"], ...roles.map((r) => [r.id, r.problem ? `${r.name} (needs fixing)` : r.name])];
  if (currentRole && !roleById(currentRole)) opts.push([currentRole, "Deleted role"]);
  els.talkTo.replaceChildren(...opts.map(([v, label]) => {
    const o = document.createElement("option"); o.value = v; o.textContent = label; return o;
  }));
}
function showRoleControls() {
  fillTalkTo();
  els.talkTo.value = currentRole || "";
  // Also fixed while the first message of a new chat is being answered on screen.
  els.talkTo.disabled = currentChatId !== null || (!!turn && turn.chatId === null && turnVisible());
  els.talkTo.title = currentChatId !== null ? "A chat keeps the role it started with. Press New chat to talk to another." : "";
  const role = !!currentRole;
  els.personalityBox.hidden = role; els.modelBox.hidden = role; els.webBox.hidden = role;
  els.editCustom.hidden = role || settings.personality !== "custom";
  const r = currentRole ? roleById(currentRole) : null;
  els.empty.textContent = currentRole ? `Give ${r ? r.name : "this role"} a task.` : "Ask me anything.";
}
els.talkTo.onchange = () => {
  if (currentChatId !== null) return;
  newChatRole = els.talkTo.value || null;
  setCurrentChat(null);
  els.input.focus();
};

async function loadRoles() {
  const data = await api("GET", "/api/roles");
  roles = data.roles; toolCatalog = data.tools;
  if (newChatRole && !roleById(newChatRole)) newChatRole = null; // deleted
  if (currentChatId === null) currentRole = newChatRole;
  showRoleControls();
}

// ---------- send / stream ----------
async function send(text) {
  if (streaming || switching || closing || !text.trim()) return;
  addUserMessage(text);
  const bot = addBotMessage();
  turn = { chatId: currentChatId, title: els.title.textContent, bot, role: currentRole };
  setStreaming(true);
  setActivity("thinking…");

  let rafId = 0; // queued redraw, 0 = none
  const render = () => { rafId = 0; bot.body.innerHTML = renderMarkdown(bot.text); if (turnVisible()) scrollDown(); };
  let errorShown = false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      // A new chat says which role it talks to (G-AGENT-3). A saved chat keeps its own.
      body: JSON.stringify({ text, chat_id: currentChatId, role: currentChatId === null ? currentRole : null }),
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
          if (turnVisible()) setActivity("");
          if (!rafId) rafId = requestAnimationFrame(render);
        } else if (ev.kind === "chat") {
          turn.chatId = ev.id; turn.title = ev.title;
          // F-HIST-1: the new chat is saved now. Only take over the screen if it is still shown.
          if (turnVisible()) setCurrentChat(ev.id, ev.title, ev.role ?? null);
          else if (currentChatId === ev.id) reloadChat(ev.id); // opened from the sidebar before this event
          else showTurnActivity();
          refreshChats();
        } else if (ev.kind === "chat_removed") {
          // The first message failed, so the server dropped the empty chat; Retry starts a new one.
          if (currentChatId === ev.id) setCurrentChat(null);
          turn.chatId = null;
          refreshChats();
        } else if (ev.kind === "note") {
          turn.note = ev.text; // kept, so a switch back during the reply shows it again
          if (turnVisible()) addChatNote(ev.text, bot.div); // F-HIST-4: restored from saved history
        } else if (ev.kind === "tool") {
          if (turnVisible()) setActivity(ev.text === "WebSearch" ? "searching the web…" : ev.text === "WebFetch" ? "reading a page…" : `using ${ev.text}…`);
        } else if (ev.kind === "retry") {
          // N-REL-1: Claude is not answering and the app is trying again; Stop still works.
          const d = ev.data || {};
          if (turnVisible()) setActivity(`Claude is not answering, trying again (${d.attempt} of ${d.max_retries})…`);
        } else if (ev.kind === "sources") {
          showSources(bot, ev.sources); // the full list so far
        } else if (ev.kind === "status") {
          showStatus(ev);
        } else if (ev.kind === "error") {
          turn.error = showError(bot, ev, text); errorShown = true;
        } else if (ev.kind === "done" && String(ev.terminal_reason || "").startsWith("aborted")) {
          addNote(bot, "(stopped)");
        }
      }
    }
  } catch (err) {
    turn.error = showError(bot, { code: err.code || "network", detail: err.message }, text); errorShown = true;
  }

  // Cancel any queued redraw first: it would run after enhanceCode and wipe the
  // highlighting and code Copy buttons (found in review).
  if (rafId) cancelAnimationFrame(rafId);
  finishBot(bot);
  if (!bot.text && !errorShown) addNote(bot, "(no reply)");
  const attached = turnOnScreen(), done = turn;
  // Its chat may be on screen without the live element (opened just as the reply was saved).
  // null === null: a new chat that was never saved (or was removed), and a new-chat screen is shown.
  const screenId = switchTarget !== undefined ? switchTarget : currentChatId;
  const onScreen = attached || done.chatId === screenId;
  turn = null;
  setStreaming(false);
  if (errorShown && attached) {
    // R-AUTH-6: the error box with Retry is on screen; also keep the text in the input box.
    // A draft is being typed: also store it, or a switch away would lose it with the box.
    if (!els.input.value) { els.input.value = text; autoGrow(); } else addRetry(done.chatId, text);
  } else if (errorShown) {
    // R-AUTH-6: the error box is not visible. Keep the text for that chat (null = a new chat
    // that was removed), say why, and give it back once that chat is on screen.
    addRetry(done.chatId, text);
    const where = done.chatId === null ? "Press New chat" : "Open that chat";
    flash(`The reply in "${done.title}" failed: ${done.error}${onScreen ? "" : ` ${where} to get your message back.`}`, true);
    if (onScreen && done.chatId !== null) reloadChat(done.chatId); // redraw also gives the text back
    else if (onScreen && switchTarget === undefined) applyPendingRetry(null); // new-chat screen
  } else {
    if (!onScreen && done.chatId !== null) flash(`Reply finished in "${done.title}".`);
    // Not attached but its chat is (or is about to be) on screen: the reply is saved, so redraw.
    if (!attached && done.chatId !== null) reloadChat(done.chatId);
  }
  if (!onScreen) closeIdle(done.chatId); // N-PERF-3: the finished chat is not on screen
  refreshChats(); // this chat moves to the top
}

// N-PERF-3: close the idle Claude process of a chat that is not on screen. `closing` blocks
// sending and makes switches wait while the server closes it (it is busy meanwhile, review M3).
// If a switch is running, close when that switch ends (it may land on that very chat).
let closing = null; // promise while /api/new runs
let closeAfterSwitch = undefined; // chat id waiting for the close, undefined = none
let reloadAfterSwitch = undefined; // chat id to redraw once the switch ends
function closeIdle(chatId) {
  if (switching) { closeAfterSwitch = chatId; return; }
  closing = api("POST", "/api/new").catch(() => { /* the next message closes it anyway */ })
    .finally(() => { closing = null; });
}
// Redraw the open chat from saved history (its reply finished without being on screen).
function reloadChat(id) {
  if (switching) { reloadAfterSwitch = id; return; }
  if (id === currentChatId) openChat(id, true);
}
// Called once openChat, New chat or a delete has finished (switching is false again).
function afterSwitch() {
  const close = closeAfterSwitch, reload = reloadAfterSwitch;
  closeAfterSwitch = reloadAfterSwitch = undefined;
  if (close !== undefined && !turn && close !== currentChatId) closeIdle(close);
  if (reload !== undefined && reload === currentChatId) openChat(reload, true);
}

// R-AUTH-6: messages whose reply failed while their chat was off screen, by chat id. One goes
// back into the input box when its chat (or, for a removed new chat, New chat) is opened.
const pendingRetries = new Map(); // chat id (null = removed new chat) -> [texts]
function addRetry(chatId, text) {
  pendingRetries.set(chatId, [...(pendingRetries.get(chatId) || []), text]);
}
function takeRetry(chatId, text) { // remove one stored copy of text
  const list = pendingRetries.get(chatId) || [];
  const i = list.indexOf(text);
  if (i >= 0) list.splice(i, 1);
  if (!list.length) pendingRetries.delete(chatId);
}
function applyPendingRetry(chatId) {
  for (const text of [...(pendingRetries.get(chatId) || [])]) {
    els.empty.hidden = true;
    if (!els.input.value) { // empty box: put it straight back
      els.input.value = text; autoGrow(); takeRetry(chatId, text);
      addChatNote("Your last message was not answered. It is back in the box below, ready to send again.");
      continue;
    }
    // Never overwrite what is being typed: offer it instead. Kept until it is put back.
    const n = document.createElement("div"); n.className = "chat-note";
    n.textContent = `A message was not answered: "${text.length > 80 ? text.slice(0, 80) + "…" : text}" `;
    const b = document.createElement("button"); b.type = "button"; b.className = "ghost"; b.textContent = "Put it back";
    b.onclick = () => { els.input.value = text; autoGrow(); takeRetry(chatId, text); n.remove(); els.input.focus(); };
    n.appendChild(b);
    els.messages.appendChild(n);
  }
}

function addNote(bot, text) {
  const n = document.createElement("div"); n.className = "note"; n.textContent = text; bot.div.appendChild(n);
}

function showError(bot, ev, originalText) {
  let msg = ev.code === "server" ? `The app could not send this: ${ev.detail}.`
    : ERRORS[ev.code] || `Something went wrong (${ev.code}).`;
  const reset = ev.rate_limit && ev.rate_limit.resets_at;
  if (ev.code === "rate_limit" && reset) msg += ` It resets at ${new Date(reset * 1000).toLocaleString()}.`;
  // R-AUTH-6: no reset time from a rate-limit event, so show Claude's own words, which may have one.
  else if (ev.code === "rate_limit" && ev.detail) msg += ` Claude says: "${ev.detail}"`;
  const box = document.createElement("div"); box.className = "error";
  box.textContent = msg;
  const retry = document.createElement("button"); retry.textContent = "Retry";
  retry.onclick = () => {
    if (streaming || switching || closing) return;
    box.remove();
    takeRetry(currentChatId, originalText); // sent again, so no stored copy is offered later
    send(originalText);
  };
  if (!String(ev.code).startsWith("role_")) box.appendChild(retry); // a retry cannot bring a role back
  bot.div.appendChild(box);
  // R-AUTH-6: the typed message goes back into the input box at the end of send(), once it
  // is known which chat is on screen (a switch may be running now, review M3).
  return msg;
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
  if (c.role) { // G-AGENT-3: which role this chat talks to
    const r = roleById(c.role);
    const tag = document.createElement("span"); tag.className = "chat-role"; tag.textContent = ` · ${r ? r.name : "deleted role"}`;
    date.append(tag);
  }
  info.append(name, date);
  const more = document.createElement("button"); more.type = "button"; more.className = "more"; more.textContent = "⋯";
  more.title = "Rename, export or delete";
  more.onclick = (e) => { e.stopPropagation(); const r = more.getBoundingClientRect(); openMenu(c, r.left, r.bottom); };
  li.append(info, more);
  li.onclick = () => openChat(c.id);
  li.oncontextmenu = (e) => { e.preventDefault(); openMenu(c, e.clientX, e.clientY); }; // right-click
  return li;
}

// reload = redraw the chat already on screen (its reply finished while not attached).
async function openChat(id, reload = false) {
  if (closing) await closing; // the server is busy closing an idle process
  if (switching || (id === currentChatId && !reload)) return;
  switching = true; // no sending until the chat is on screen (review M3)
  switchTarget = id;
  try {
    const { chat, messages } = await api("GET", `/api/chats/${id}`);
    // N-PERF-3: only the open chat keeps a live Claude process, so close the old one now.
    // Not during a reply: its process is still working (the server would refuse anyway).
    // Not on a reload: the process belongs to this very chat.
    if (!turn && !reload) await api("POST", "/api/new").catch(() => {});
    clearMessages();
    for (const m of messages) {
      if (m.role === "user") { addUserMessage(m.text); continue; }
      const bot = addBotMessage(chat.role); bot.text = m.text; finishBot(bot); showSources(bot, m.sources);
      if (m.status === "stopped") addNote(bot, "(stopped)");
    }
    // Back to the chat that is still replying: re-attach the live reply under its question.
    // If the last saved message is already the reply, it finished meanwhile: do not show it twice.
    if (turn && turn.chatId === chat.id && messages.at(-1)?.role === "user") {
      els.messages.appendChild(turn.bot.div);
      if (turn.note) addChatNote(turn.note, turn.bot.div); // F-HIST-4 "restored" note
    }
    els.empty.hidden = messages.length > 0;
    setCurrentChat(chat.id, chat.title, chat.role);
    applyPendingRetry(chat.id);
    showTurnActivity();
    scrollDown(true);
    els.sidebar.classList.remove("open");
    els.input.focus();
  } catch (err) {
    flash(`Could not open the chat: ${err.message}.`, true);
    refreshChats();
  } finally { switching = false; switchTarget = undefined; }
  afterSwitch();
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
  if (turn && c.id === turn.chatId) { flash("Wait for the reply in this chat to finish, or press Stop.", true); return; }
  if (!confirm(`Delete "${c.title}"? This also deletes its saved transcript and cannot be undone.`)) return;
  if (closing) await closing;
  if (switching) return; // another switch is running; never clear its flag early
  switching = true;
  try {
    const res = await api("DELETE", `/api/chats/${c.id}`);
    if (c.id === currentChatId) { clearMessages(); setCurrentChat(null); }
    pendingRetries.delete(c.id);
    if (res.transcripts_failed) flash("Chat deleted, but its transcript file could not be removed (another program may have it open).", true);
    else flash("Chat deleted.");
  } catch (err) { flash(`Could not delete: ${err.message}.`, true); }
  finally { switching = false; }
  afterSwitch();
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
  els.editCustom.hidden = !!currentRole || s.personality !== "custom";
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
  if (closing) await closing;
  if (switching) return;
  switching = true;
  try {
    const res = await api("POST", "/api/history/delete-all", { confirm: "DELETE" });
    clearMessages(); setCurrentChat(null); pendingRetries.clear();
    els.settingsDialog.close();
    if (res.transcripts_failed) flash(`All chats deleted, but ${res.transcripts_failed} transcript files could not be removed (another program may have them open).`, true);
    else flash("All history deleted.");
  } catch (err) {
    const box = $("settings-error"); box.textContent = `Could not delete: ${err.message}.`; box.hidden = false;
  } finally { switching = false; }
  afterSwitch();
  $("delete-all").hidden = false; $("delete-all-confirm").hidden = true;
  refreshChats();
};

// ---------- Roles page (Milestone 5, G-AGENT-1, G-AGENT-2) ----------
let editingRole = null; // id of the role in the editor, null = a new role
const roleBoxes = {}; // tool name -> checkbox

function buildToolBoxes() {
  const box = $("role-tools");
  box.replaceChildren();
  for (const [name, info] of Object.entries(toolCatalog)) {
    const label = document.createElement("label");
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.value = name;
    const text = document.createElement("span"); text.textContent = `${info.label} (${name})`;
    const kind = document.createElement("span"); kind.className = "kind";
    kind.textContent = info.kind === "read-only" ? "read-only" : "changes its own folder";
    label.append(cb, text, kind);
    box.append(label);
    roleBoxes[name] = cb;
  }
}

function showRoleList() {
  els.roleList.replaceChildren(...roles.map((r) => {
    const li = document.createElement("li");
    li.textContent = r.problem ? `⚠ ${r.name}` : r.name;
    li.title = r.problem ? `Needs fixing: ${r.problem}` : r.name;
    li.classList.toggle("active", r.id === editingRole);
    li.classList.toggle("broken", !!r.problem);
    li.onclick = () => editRole(r.id);
    return li;
  }));
  $("role-list-empty").hidden = roles.length > 0;
}

function editRole(id) {
  const r = id ? roleById(id) : null;
  editingRole = r ? r.id : null;
  $("role-name").value = r ? r.name : "";
  $("role-model").value = r ? r.model : "";
  const tools = r ? r.tools : ["WebSearch", "WebFetch"]; // a new role starts with web tools only
  for (const [name, cb] of Object.entries(roleBoxes)) cb.checked = tools.includes(name);
  $("role-instructions").value = r ? r.instructions : "";
  $("role-folder").textContent = r ? `Its folder: ${r.folder}` : "A new role gets its own folder under projects.";
  const problem = $("role-problem");
  problem.textContent = r && r.problem ? `This role cannot run until it is fixed: ${r.problem}. Saving fixes it.` : "";
  problem.hidden = !(r && r.problem);
  $("role-error").hidden = true;
  $("role-delete").hidden = !r; $("role-copy").hidden = !r;
  showRoleList();
  $("role-name").focus();
}

async function openRoles(id) {
  try { await loadRoles(); } catch (err) { flash(`Could not load roles: ${err.message}.`, true); return; }
  buildToolBoxes();
  editRole(id !== undefined ? id : roles[0]?.id ?? null);
  els.rolesDialog.showModal();
}

function roleError(err) { const box = $("role-error"); box.textContent = err; box.hidden = false; }

async function saveRole() { // returns the saved role, or null
  if (!els.roleForm.reportValidity()) return null;
  const body = {
    name: $("role-name").value, model: $("role-model").value,
    tools: Object.entries(roleBoxes).filter(([, cb]) => cb.checked).map(([name]) => name),
    instructions: $("role-instructions").value,
  };
  try {
    const { role } = editingRole === null ? await api("POST", "/api/roles", body)
      : await api("PUT", `/api/roles/${editingRole}`, body);
    await loadRoles();
    editRole(role.id);
    refreshChats(); // role names in the sidebar
    return role;
  } catch (err) { roleError(`Could not save: ${err.message}.`); return null; }
}

els.roleForm.onsubmit = async (e) => { e.preventDefault(); if (await saveRole()) flash("Role saved. Open chats use it from their next message."); };
$("role-new").onclick = () => editRole(null);
$("role-close").onclick = () => els.rolesDialog.close();
$("role-copy").onclick = async () => {
  try {
    const { role } = await api("POST", `/api/roles/${editingRole}/copy`);
    await loadRoles(); editRole(role.id);
  } catch (err) { roleError(`Could not copy: ${err.message}.`); }
};
$("role-delete").onclick = async () => {
  const r = roleById(editingRole);
  if (!r || !confirm(`Delete the role "${r.name}"? Its chats and its folder are kept, but its chats cannot be continued.`)) return;
  try {
    await api("DELETE", `/api/roles/${r.id}`);
    await loadRoles(); editRole(roles[0]?.id ?? null); refreshChats();
  } catch (err) { roleError(`Could not delete: ${err.message}.`); }
};
// G-AGENT-3: save, then open a new chat that talks to this role.
$("role-task").onclick = async () => {
  const role = await saveRole();
  if (!role) return;
  if (role.problem) { roleError(`This role cannot run: ${role.problem}.`); return; }
  els.rolesDialog.close();
  newChatRole = role.id;
  els.newChat.click();
};
els.rolesBtn.onclick = () => openRoles();

// ---------- new chat ----------
els.newChat.onclick = async () => {
  if (closing) await closing; // the server is busy closing an idle process
  if (switching) return;
  if (!turn) { // during a reply its process keeps working; it is closed when the reply ends
    switching = true;
    try { await api("POST", "/api/new"); } catch { return; } // server kept the old chat (still busy)
    finally { switching = false; }
  }
  clearMessages();
  setCurrentChat(null);
  applyPendingRetry(null); // a failed first message of a removed new chat
  showTurnActivity();
  afterSwitch();
  els.sidebar.classList.remove("open");
  els.input.focus();
};

// ---------- composer (N-USE-1) ----------
els.composer.onsubmit = (e) => {
  e.preventDefault();
  const text = els.input.value;
  if (!text.trim() || streaming || switching || closing) return;
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
    await loadRoles().catch(() => {}); // before the chat list, so role chats show their role

    showStatus(data.status);
    if (data.status.pending) setTimeout(refreshStatus, 1500); // sign-in check still running
    else if (!data.status.confirmed && data.status.ok) els.status.textContent += " (confirmed after the first message)";
    if (data.notices.length) { els.notices.textContent = data.notices.join("\n"); els.notices.hidden = false; }
    refreshChats();
  } catch {
    showStatus({ ok: false, text: "This window is not connected. Start the app with start.bat or python -m hub.server." });
  }
})();
