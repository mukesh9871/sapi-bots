// Chat window (Milestones 2 to 6). Plain JavaScript, no build step.
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
  approvalsBtn: $("open-approvals"), approvalsBadge: $("approvals-badge"), approvalsDialog: $("approvals-dialog"),
  activityBtn: $("open-activity"), activityDialog: $("activity-dialog"), usageBar: $("usage-bar"),
  pauseToggle: $("pause-toggle"), taskBudget: $("task-budget"),
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
const BUDGET_NOTE = "(stopped: this task reached its budget of turns. Raise it on the Roles page, or ask for less at once.)";

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
  automation_paused: "Automation is paused, so roles cannot take tasks. Press Resume at the bottom to continue. Plain chat still works.",
};

// Small fetch helper: JSON in, JSON out, throws with the server's reason.
async function api(method, url, body) {
  const opts = { method };
  if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(data.error || res.statusText); e.repeat = !!data.repeat; throw e; }
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
  if (!on) { els.activity.hidden = true; els.input.focus(); els.taskBudget.hidden = true; refreshGate(); }
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
          if (turnVisible()) setActivity(toolLabel(ev.text));
        } else if (ev.kind === "turn") {
          showBudget(ev.data); // G-BUDGET-1 / UI 13: turns used by this task
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
        } else if (ev.kind === "done" && ev.subtype === "error_max_turns") {
          // G-BUDGET-1: the task used its whole turn budget, so the run stopped. Say so plainly.
          addNote(bot, BUDGET_NOTE);
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
      if (m.status === "budget") addNote(bot, BUDGET_NOTE); // G-BUDGET-1, also after reopening
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
  loadRules();
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
    kind.textContent = info.kind === "read-only" ? "read-only" : info.kind === "acting" ? "acting: goes through Approvals" : "changes its own folder";
    label.append(cb, text, kind);
    box.append(label);
    roleBoxes[name] = cb;
  }
}

function showRoleList() {
  els.roleList.replaceChildren(...roles.map((r) => {
    const li = document.createElement("li");
    const from = r.pack && r.pack !== "local" ? ` (${r.pack})` : "";
    li.textContent = (r.problem ? `⚠ ${r.name}` : r.name) + from;
    li.title = r.problem ? `Needs fixing: ${r.problem}` : r.name + from;
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
  $("role-turns").value = r ? r.max_turns : 30;
  $("role-folder").textContent = r ? `Its folder: ${r.folder}` : "A new role gets its own folder under projects.";
  const problem = $("role-problem");
  problem.textContent = r && r.problem ? `This role cannot run until it is fixed: ${r.problem}. Saving fixes it.` : "";
  problem.hidden = !(r && r.problem);
  $("role-error").hidden = true;
  const fromPack = !!(r && r.pack && r.pack !== "local"); // Milestone 8: pack roles are read-only
  $("role-pack-note").textContent = fromPack ? `From the ${r.pack} pack: read-only. Copy it to make your own version.` : "";
  $("role-pack-note").hidden = !fromPack;
  $("role-delete").hidden = !r || fromPack; $("role-copy").hidden = !r; $("role-save").hidden = fromPack;
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
    max_turns: Number($("role-turns").value),
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

// ---------- Milestone 6: approvals, activity log, rules, pause, budget ----------
function toolLabel(name) {
  if (name === "WebSearch") return "searching the web…";
  if (name === "WebFetch") return "reading a page…";
  // An acting tool: the approval gate decides whether it runs now, waits for you, or is refused.
  if (name.startsWith("mcp__hub__")) return `acting: ${name.slice(10).replace("_", " ")} (checked against your rules)…`;
  return `using ${name}…`;
}

function showBudget(d) { // UI 13: the running task's budget
  if (!d || !d.max) return;
  els.taskBudget.textContent = `Task: ${d.turn} of ${d.max} turns`;
  els.taskBudget.hidden = false;
}

let paused = false;
async function refreshGate() {
  try {
    const g = await api("GET", "/api/gate/summary");
    els.approvalsBadge.textContent = g.pending; els.approvalsBadge.hidden = !g.pending;
    showPause(g.paused);
    showMissedBadge(g.missed || 0);
    showNotices(g.notice || 0);
  } catch { /* the status line reports a dead server */ }
}
function showPause(p) {
  paused = p;
  $("automation-state").textContent = p ? "Automation: paused" : "Automation: running";
  els.pauseToggle.textContent = p ? "Resume" : "Pause";
  els.pauseToggle.title = p ? "Let roles take tasks and actions run again"
    : "Stop role tasks and hold every action, for example when your usage limit is nearly used. Plain chat keeps working.";
  els.usageBar.classList.toggle("paused", p);
}
els.pauseToggle.onclick = async () => { // G-BUDGET-2
  try { const { settings: s } = await api("POST", "/api/settings", { automation_paused: !paused }); settings = s; showPause(s.automation_paused); }
  catch (err) { flash(`Could not change the pause switch: ${err.message}.`, true); }
};

// G-APPR-2: each held item shows exactly what will be done, with editable fields.
async function openApprovals() {
  let items;
  try { ({ items } = await api("GET", "/api/approvals")); } catch (err) { flash(`Could not load approvals: ${err.message}.`, true); return; }
  const list = $("approvals-list");
  list.replaceChildren(...items.map(approvalCard));
  $("approvals-empty").hidden = items.length > 0;
  if (!els.approvalsDialog.open) els.approvalsDialog.showModal();
}

function approvalCard(item) {
  const card = document.createElement("div"); card.className = "approval";
  const meta = document.createElement("div"); meta.className = "meta";
  meta.textContent = `#${item.id} · ${item.agent} wants to ${item.action_id.replace(".", ": ").replace("_", " ")} · ${new Date(item.created_at * 1000).toLocaleString()}`;
  card.append(meta);
  if (item.reason) { const r = document.createElement("div"); r.className = "reason"; r.textContent = `Held because: ${item.reason}`; card.append(r); }
  const err = document.createElement("div"); err.className = "error-text"; err.hidden = true;
  const actions = document.createElement("div"); actions.className = "actions";
  const reject = document.createElement("button"); reject.type = "button"; reject.className = "danger"; reject.textContent = "Reject";
  const approve = document.createElement("button"); approve.type = "button"; approve.textContent = "Approve";
  let ex = null, previewTimer = 0, previewSeq = 0, previewOk = true;
  if (item.exact) { // G-APPR-2: the exact text the connector will publish, rebuilt as I edit the fields
    ex = document.createElement("div"); ex.className = "exact";
    ex.textContent = `${item.exact_label || "Will publish exactly"}: ${item.exact}`;
    card.append(ex);
  }
  const boxes = {};
  for (const [key, spec] of Object.entries(item.fields)) {
    const label = document.createElement("label"); label.textContent = spec.description || key;
    const ta = document.createElement("textarea"); ta.rows = key === "text" ? 5 : 1; ta.value = item.payload[key] || "";
    if (spec.max) ta.maxLength = spec.max;
    if (spec.hub) ta.readOnly = true; // filled in by the hub (the agent's folder or name): shown, never edited
    label.append(ta); card.append(label); boxes[key] = ta;
    if (ex) ta.addEventListener("input", () => {
      clearTimeout(previewTimer); // one timer for the whole card
      previewOk = false; approve.disabled = true; // Approve waits until the text shown is the text that runs
      const seq = ++previewSeq;
      previewTimer = setTimeout(async () => {
        const payload = Object.fromEntries(Object.entries(boxes).map(([k, t]) => [k, t.value]));
        try {
          const r = await api("POST", `/api/approvals/${item.id}/preview`, { payload });
          if (seq !== previewSeq) return; // an older answer arriving late
          ex.textContent = r.problem ? `Cannot be done like this: ${r.problem}` : `${item.exact_label || "Will publish exactly"}: ${r.exact}`;
          previewOk = !r.problem;
        } catch {
          if (seq !== previewSeq) return;
          ex.textContent = "Could not rebuild the text: check the fields.";
          previewOk = false;
        }
        approve.disabled = !previewOk;
      }, 300);
    });
  }
  const busy = (on) => { reject.disabled = on; approve.disabled = on || !previewOk; };
  approve.onclick = async () => {
    busy(true);
    const payload = Object.fromEntries(Object.entries(boxes).map(([k, ta]) => [k, ta.value]));
    try {
      let r;
      try { r = await api("POST", `/api/approvals/${item.id}/approve`, { payload }); }
      catch (e) {
        // N-REL-3: this exact content already ran recently. Post it again only if I say so.
        if (!e.repeat || !confirm(`Exactly this content was already posted in the last 24 hours. Post it again?`)) throw e;
        r = await api("POST", `/api/approvals/${item.id}/approve`, { payload, confirm_repeat: true });
      }
      flash(r.ok ? `Done${r.edited ? " (with your edits)" : ""}: ${r.result}` : `The action failed: ${r.result}`, !r.ok);
      decided(card);
    } catch (e) { err.textContent = `Could not approve: ${e.message}.`; err.hidden = false; busy(false); }
  };
  reject.onclick = async () => {
    busy(true);
    try { await api("POST", `/api/approvals/${item.id}/reject`); flash("Rejected. Nothing was done."); decided(card); }
    catch (e) { err.textContent = `Could not reject: ${e.message}.`; err.hidden = false; busy(false); }
  };
  actions.append(reject, approve);
  card.append(err, actions);
  return card;
}
// Remove only the decided card, so unsaved edits in the others stay (review M6).
function decided(card) {
  card.remove();
  $("approvals-empty").hidden = !!$("approvals-list").querySelector(".approval");
  refreshGate();
}
els.approvalsBtn.onclick = openApprovals;
$("approvals-close").onclick = () => els.approvalsDialog.close();

// G-LOG-1 / UI 12: the activity log, newest first.
const DECISIONS = { auto: "Auto", approved: "Approved by you", held: "Held for approval", blocked: "Blocked (Never)", rejected: "Rejected by you" };
const OUTCOMES = { started: "started", done: "done", failed: "failed", unknown: "unknown: check it", "": "" };
async function loadActivity() {
  const q = new URLSearchParams({ q: $("activity-q").value, pack: $("activity-pack").value.trim(),
    platform: $("activity-platform").value.trim(), day: $("activity-day").value });
  let entries;
  try { ({ entries } = await api("GET", `/api/activity?${q}`)); } catch (err) { flash(`Could not load the log: ${err.message}.`, true); return; }
  $("activity-table").querySelector("tbody").replaceChildren(...entries.map((e) => {
    const tr = document.createElement("tr");
    const cells = [new Date(e.at * 1000).toLocaleString(), e.agent, `${e.pack} · ${e.action_id}`,
      DECISIONS[e.decision] || e.decision, [OUTCOMES[e.outcome] ?? e.outcome, e.detail].filter(Boolean).join(": ")];
    cells.forEach((text, i) => {
      const td = document.createElement("td"); td.textContent = text;
      if (i === 3) td.className = `decision-${e.decision}`;
      if (i === 4) td.className = `wrap outcome-${e.outcome}`;
      tr.append(td);
    });
    tr.title = JSON.stringify(e.payload, null, 2); // exactly what was sent
    return tr;
  }));
  $("activity-empty").hidden = entries.length > 0;
}
let activityTimer = 0;
for (const id of ["activity-q", "activity-pack", "activity-platform", "activity-day"]) {
  $(id).addEventListener("input", () => { clearTimeout(activityTimer); activityTimer = setTimeout(loadActivity, 250); });
}
els.activityBtn.onclick = async () => { await loadActivity(); els.activityDialog.showModal(); };
$("activity-close").onclick = () => els.activityDialog.close();

// G-APPR-1 / G-APPR-3: rules are changed only here, by you. Each change saves at once.
async function loadRules() {
  let rules;
  try { ({ rules } = await api("GET", "/api/rules")); } catch { return; }
  $("rules-table").querySelector("tbody").replaceChildren(...rules.map((r) => {
    const tr = document.createElement("tr");
    for (const t of [r.pack, r.platform, r.action.replace("_", " ")]) { const td = document.createElement("td"); td.textContent = t; tr.append(td); }
    const mode = document.createElement("select");
    for (const [v, label] of [["ask", "Ask"], ["auto", "Auto"], ["never", "Never"]]) {
      const o = document.createElement("option"); o.value = v; o.textContent = label; mode.append(o);
    }
    mode.value = r.mode;
    const cap = document.createElement("input"); cap.type = "number"; cap.min = 1; cap.max = 1000; cap.value = r.daily_cap; cap.className = "short";
    const save = async () => {
      try { await api("PUT", "/api/rules", { pack: r.pack, platform: r.platform, action: r.action, mode: mode.value, daily_cap: Number(cap.value) }); flash("Rule saved."); }
      catch (err) { flash(`Could not save the rule: ${err.message}.`, true); loadRules(); }
    };
    mode.onchange = save; cap.onchange = save;
    for (const el of [mode, cap]) { const td = document.createElement("td"); td.append(el); tr.append(td); }
    return tr;
  }));
  $("rules-empty").hidden = rules.length > 0;
}

// ---------- Milestone 7: teams and the board ----------
let teamsList = [];
let editingTeam = null; // id of the team in the editor, null = new
let teamMembers = []; // role ids of the workers being edited
let boardRun = null; // run id on the board
let boardTimer = 0;
const roleName = (id) => (roleById(id) || { name: id }).name;

function fillRoleSelect(sel, withNone) {
  const label = (r) => r.pack && r.pack !== "local" ? `${r.name} (${r.pack})` : r.name;
  const opts = (withNone ? [["", "(no reviewer)"]] : []).concat(roles.filter((r) => !r.problem).map((r) => [r.id, label(r)]));
  sel.replaceChildren(...opts.map(([v, t]) => { const o = document.createElement("option"); o.value = v; o.textContent = t; return o; }));
}

async function openTeams() {
  try { await loadRoles(); await loadTeams(); await loadRuns(); } catch (err) { flash(`Could not load teams: ${err.message}.`, true); return; }
  if (boardRun !== null) showBoard(boardRun); else editTeam(teamsList[0]?.id ?? null);
  if (!$("teams-dialog").open) $("teams-dialog").showModal();
}

async function loadTeams() {
  ({ teams: teamsList } = await api("GET", "/api/teams"));
  $("team-list").replaceChildren(...teamsList.map((t) => {
    const li = document.createElement("li");
    const from = t.pack && t.pack !== "local" ? ` (${t.pack})` : "";
    li.textContent = (t.problem ? `⚠ ${t.name}` : t.name) + from;
    li.title = t.problem ? `Needs fixing: ${t.problem}` : t.name + from;
    li.classList.toggle("active", boardRun === null && t.id === editingTeam);
    li.onclick = () => editTeam(t.id);
    return li;
  }));
}

const RUN_LABELS = { running: "running", paused: "paused", done: "done", stopped: "stopped", failed: "failed" };
async function loadRuns() {
  const { runs } = await api("GET", "/api/runs");
  $("run-list").replaceChildren(...runs.map((r) => {
    const li = document.createElement("li");
    li.textContent = `#${r.id} ${r.team_name} · ${RUN_LABELS[r.status] || r.status}`;
    li.title = r.task;
    li.classList.toggle("active", r.id === boardRun);
    li.onclick = () => showBoard(r.id);
    return li;
  }));
  $("run-list-empty").hidden = runs.length > 0;
}

function showMembers() {
  $("team-members").replaceChildren(...teamMembers.map((rid, i) => {
    const li = document.createElement("li"); li.textContent = roleName(rid);
    const x = document.createElement("button"); x.type = "button"; x.textContent = "×"; x.title = "Remove";
    x.onclick = () => { teamMembers.splice(i, 1); showMembers(); };
    li.append(x); return li;
  }));
}

function editTeam(id) {
  stopBoard();
  const t = id ? teamsList.find((x) => x.id === id) : null;
  editingTeam = t ? t.id : null;
  $("run-board").hidden = true; $("team-form").hidden = false;
  fillRoleSelect($("team-lead"), false); fillRoleSelect($("team-reviewer"), true); fillRoleSelect($("team-add-role"), false);
  $("team-name").value = t ? t.name : "";
  $("team-lead").value = t ? t.lead : ""; $("team-reviewer").value = t ? t.reviewer : "";
  teamMembers = t ? [...t.members] : [];
  showMembers();
  $("team-agents").value = t ? t.max_agents : 2; $("team-turns").value = t ? t.max_turns : 100;
  $("team-notes").value = t ? t.notes : "";
  $("team-problem").textContent = t && t.problem ? `This team cannot start until it is fixed: ${t.problem}.` : "";
  $("team-problem").hidden = !(t && t.problem);
  $("team-error").hidden = true;
  const fromPack = !!(t && t.pack && t.pack !== "local"); // Milestone 8: pack teams are read-only
  $("team-pack-note").textContent = fromPack ? `From the ${t.pack} pack: read-only. You can still give it tasks.` : "";
  $("team-pack-note").hidden = !fromPack;
  $("team-delete").hidden = !t || fromPack; $("team-save").hidden = fromPack; $("team-start-box").hidden = !t;
  loadTeams(); loadRuns();
}

function teamError(msg) { $("team-error").textContent = msg; $("team-error").hidden = false; }
$("team-add").onclick = () => { if ($("team-add-role").value) { teamMembers.push($("team-add-role").value); showMembers(); } };
$("team-new").onclick = () => editTeam(null);
$("team-form").onsubmit = async (e) => {
  e.preventDefault();
  const body = { name: $("team-name").value, lead: $("team-lead").value, reviewer: $("team-reviewer").value,
    members: teamMembers, max_agents: Number($("team-agents").value), max_turns: Number($("team-turns").value),
    notes: $("team-notes").value };
  try {
    const { team } = editingTeam === null ? await api("POST", "/api/teams", body) : await api("PUT", `/api/teams/${editingTeam}`, body);
    await loadTeams(); editTeam(team.id); flash("Team saved.");
  } catch (err) { teamError(`Could not save: ${err.message}.`); }
};
$("team-delete").onclick = async () => {
  const t = teamsList.find((x) => x.id === editingTeam);
  if (!t || !confirm(`Delete the team "${t.name}"? Its past runs stay on the board.`)) return;
  try { await api("DELETE", `/api/teams/${t.id}`); await loadTeams(); editTeam(teamsList[0]?.id ?? null); }
  catch (err) { teamError(`Could not delete: ${err.message}.`); }
};
$("team-start").onclick = async () => {
  const task = $("team-task").value.trim();
  if (!task) { teamError("Write the task first."); return; }
  try {
    const { run_id } = await api("POST", `/api/teams/${editingTeam}/runs`, { task });
    $("team-task").value = "";
    showBoard(run_id);
  } catch (err) { teamError(`Could not start: ${err.message}.`); }
};

// G-TEAM-4: one column per member with status, current step and latest output.
function stopBoard() { clearTimeout(boardTimer); boardRun = null; }
async function showBoard(runId) {
  clearTimeout(boardTimer);
  boardRun = runId;
  $("team-form").hidden = true; $("run-board").hidden = false;
  let data;
  try { data = await api("GET", `/api/runs/${runId}`); }
  catch (err) { flash(`Could not load the run: ${err.message}.`, true); editTeam(teamsList[0]?.id ?? null); return; }
  if (boardRun !== runId) return; // another run was opened meanwhile
  const run = data.run, live = run.status === "running" || run.status === "paused";
  const closing = !!data.closing; // ended, but its sessions are still closing: Delete comes after
  $("run-title").textContent = `#${run.id} ${run.team_name}`;
  $("run-status").textContent = RUN_LABELS[run.status] || run.status;
  $("run-meta").textContent = `Turns used ${run.turns_used} of ${run.max_turns} · at most ${run.max_agents} at once · folder ${run.folder}`
    + (run.reason ? ` · ${run.reason}` : "") + (closing ? " · closing…" : "");
  $("run-task").textContent = `Task: ${run.task}`;
  $("run-pause").hidden = !live; $("run-stop").hidden = !live; $("run-delete").hidden = live || closing;
  $("run-message").hidden = !live; $("run-send").hidden = !live;
  $("run-pause").textContent = run.status === "paused" ? "Resume" : "Pause";
  $("run-columns").replaceChildren(...data.board.members.map((m) => {
    const col = document.createElement("div"); col.className = "column";
    const h = document.createElement("h4"); h.textContent = `${m.name} (${m.key})`;
    const st = document.createElement("div"); st.className = `status ${m.status}`; st.textContent = m.status;
    const step = document.createElement("div"); step.className = "step"; step.textContent = m.step ? `Step: ${m.step}` : "";
    const out = document.createElement("pre"); out.textContent = m.output || "";
    col.append(h, st, step, out); return col;
  }));
  // G-TEAM-5: questions a member raises, answered here. Rebuilt only when the questions
  // change, so an answer being typed survives the 2 s refresh.
  const qKey = `${run.id}:${data.questions.map((q) => q.id).join(",")}`;
  const qBox = $("run-questions");
  if (qBox.dataset.key !== qKey) { qBox.dataset.key = qKey; qBox.replaceChildren(...data.questions.map((q) => {
    const box = document.createElement("div"); box.className = "question";
    const p = document.createElement("div"); p.textContent = `${q.member} asks: ${q.question}`;
    const row = document.createElement("div"); row.className = "row";
    const input = document.createElement("input"); input.placeholder = "Your answer";
    const b = document.createElement("button"); b.type = "button"; b.textContent = "Answer";
    b.onclick = async () => {
      try { await api("POST", `/api/runs/${run.id}/answer`, { question_id: q.id, text: input.value }); showBoard(run.id); }
      catch (err) { flash(`Could not answer: ${err.message}.`, true); }
    };
    row.append(input, b); box.append(p, row); return box;
  })); }
  $("run-report-box").hidden = !run.report;
  $("run-report").innerHTML = run.report ? renderMarkdown(run.report) : ""; // sanitized like any reply
  $("run-events").replaceChildren(...data.events.slice(-40).map((e) => {
    const li = document.createElement("li");
    li.textContent = `${new Date(e.at * 1000).toLocaleTimeString()} · ${e.who}: ${e.text}`; return li;
  }));
  loadRuns().catch(() => {});
  if ((live || closing) && $("teams-dialog").open) boardTimer = setTimeout(() => showBoard(runId), 2000);
}

async function runAction(action, body) {
  try { await api("POST", `/api/runs/${boardRun}/${action}`, body); showBoard(boardRun); }
  catch (err) { flash(`Could not ${action}: ${err.message}.`, true); }
}
$("run-pause").onclick = () => runAction($("run-pause").textContent === "Resume" ? "resume" : "pause");
$("run-stop").onclick = () => { if (confirm("Stop this team? Work done so far is kept.")) runAction("stop"); };
$("run-send").onclick = () => {
  const text = $("run-message").value.trim();
  if (!text) return;
  $("run-message").value = "";
  runAction("message", { text });
};
$("run-delete").onclick = async () => {
  if (!confirm("Delete this run's record? Its files in the project folder are kept.")) return;
  try { await api("DELETE", `/api/runs/${boardRun}`); stopBoard(); await loadRuns(); editTeam(teamsList[0]?.id ?? null); }
  catch (err) { flash(`Could not delete: ${err.message}.`, true); }
};
$("open-teams").onclick = openTeams;
$("teams-close").onclick = () => { clearTimeout(boardTimer); $("teams-dialog").close(); };
$("teams-dialog").addEventListener("close", () => clearTimeout(boardTimer));

// ---------- schedules (Milestone 8, G-SCHED-1, G-SCHED-2) ----------
const DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
let schedules = [], schTargets = { roles: [], teams: [] }, editingSchedule = null, schTimer = 0;
const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "—");
const STATUS_LABELS = { running: "running", done: "done", failed: "failed", stopped: "stopped", skipped: "skipped",
  missed: "missed", "ran-late": "ran late", dismissed: "dismissed", budget: "stopped at its turn budget" };

function buildDays() {
  $("sch-days").replaceChildren(...DAY_NAMES.map((d) => {
    const l = document.createElement("label"); const cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = d; l.append(cb, ` ${d[0].toUpperCase()}${d.slice(1)}`); return l;
  }));
}

function showWhenFields() {
  const t = $("sch-type").value;
  $("sch-time-box").hidden = !["daily", "weekly"].includes(t);
  $("sch-days").hidden = t !== "weekly";
  $("sch-minutes-box").hidden = t !== "interval";
  $("sch-folder-box").hidden = t !== "file";
  $("sch-after").hidden = t !== "after";
}

function fillTargets(kind, value) {
  const list = kind === "team" ? schTargets.teams : schTargets.roles;
  $("sch-target-label").textContent = kind === "team" ? "Team" : "Role";
  $("sch-target").replaceChildren(...list.map((x) => {
    const o = document.createElement("option"); o.value = x.id;
    o.textContent = x.pack && x.pack !== "local" ? `${x.name} (${x.pack})` : x.name; return o;
  }));
  if (value) $("sch-target").value = value;
  showCan();
}

const MODE_LABELS = { ask: "asks you first", auto: "runs by itself (Auto)", never: "never" };
function showCan() { // P-ROUT-1: which outside actions it may take, and the rule each follows
  const role = $("sch-kind").value === "role" ? schTargets.roles.find((x) => x.id === $("sch-target").value) : null;
  const can = role ? role.can || [] : [];
  $("sch-can").textContent = !role ? "Each member of the team follows its own approval rules."
    : can.length ? `It may: ${can.map((c) => `${c.label} (${MODE_LABELS[c.mode] || c.mode})`).join("; ")}. Change rules in Settings.`
    : "It takes no outside actions.";
}

async function loadSchedules() {
  const data = await api("GET", "/api/schedules");
  schedules = data.schedules; schTargets = { roles: data.roles, teams: data.teams };
  $("schedule-list").replaceChildren(...schedules.map((x) => {
    const li = document.createElement("li");
    const from = x.pack !== "local" ? ` (${x.pack})` : "";
    li.textContent = `${x.running ? "▶ " : x.enabled ? "" : "○ "}${x.name}${from}`;
    li.title = `${x.when}${x.enabled ? "" : " (switched off)"}`;
    li.classList.toggle("active", x.id === editingSchedule);
    li.onclick = () => editSchedule(x.id);
    return li;
  }));
  $("schedule-list-empty").hidden = schedules.length > 0;
  // G-SCHED-2: missed runs, each with Run late and Dismiss
  $("missed-list").replaceChildren(...data.missed.map((m) => {
    const li = document.createElement("li");
    const text = document.createElement("div");
    text.textContent = `${m.name}: missed ${m.missed_count > 1 ? `${m.missed_count} times, first ` : ""}at ${when(m.due_at)} (${m.reason})`;
    const late = document.createElement("button"); late.type = "button"; late.className = "ghost"; late.textContent = "Run late";
    late.onclick = () => missedAction(m.id, "run");
    const dismiss = document.createElement("button"); dismiss.type = "button"; dismiss.className = "ghost"; dismiss.textContent = "Dismiss";
    dismiss.onclick = () => missedAction(m.id, "dismiss");
    li.append(text, late, dismiss); return li;
  }));
  $("missed-empty").hidden = data.missed.length > 0;
  showMissedBadge(data.missed.length);
  return data;
}

function showMissedBadge(n) {
  $("missed-badge").textContent = n; $("missed-badge").hidden = !n;
}

async function missedAction(id, action) {
  try { await api("POST", `/api/missed/${id}/${action}`); } catch (err) { flash(`Could not do that: ${err.message}.`, true); }
  await refreshSchedules();
}

function editSchedule(id) {
  const x = id ? schedules.find((s) => s.id === id) : null;
  editingSchedule = x ? x.id : null;
  $("sch-name").value = x ? x.name : "";
  $("sch-kind").value = x ? x.kind : "role";
  fillTargets($("sch-kind").value, x ? x.target : null);
  $("sch-task").value = x ? x.task : "";
  $("sch-notify").checked = x ? x.notify : false;
  $("sch-draft-box").hidden = !!x; $("sch-draft-note").hidden = true;
  if (!x) $("sch-words").value = "";
  const spec = x ? x.spec : { type: "daily", time: "08:00" };
  $("sch-type").value = spec.type;
  $("sch-time").value = spec.time || "08:00";
  for (const cb of $("sch-days").querySelectorAll("input")) cb.checked = (spec.days || []).includes(cb.value);
  $("sch-minutes").value = spec.minutes || 60;
  $("sch-folder").value = spec.folder || "";
  $("sch-after").replaceChildren(...schedules.filter((s) => s.id !== editingSchedule).map((s) => {
    const o = document.createElement("option"); o.value = s.id; o.textContent = s.name; return o;
  }));
  if (spec.type === "after") $("sch-after").value = spec.schedule;
  showWhenFields();
  $("sch-error").hidden = true;
  $("sch-delete").hidden = !x; $("sch-status-box").hidden = !x;
  if (x) showScheduleStatus(x);
  loadSchedules().catch(() => {});
}

async function showScheduleStatus(x) {
  $("sch-enabled").checked = x.enabled;
  const last = x.last;
  const lastText = last ? `Last run: ${STATUS_LABELS[last.status] || last.status} at ${when(last.started_at || last.due_at)}`
    + (last.reason ? ` (${last.reason})` : "") + (last.result ? `. Result: ${last.result.slice(0, 300)}` : "") : "Not run yet.";
  const next = x.enabled ? (x.next_run ? `Next run: ${when(x.next_run)}.` : `Runs ${x.when}.`) : "Switched off: it does not run.";
  $("sch-status").textContent = `${x.when}. ${next} ${lastText}`; // P-ROUT-3: last run, result and next run
  $("sch-run").hidden = x.running; $("sch-stop").hidden = !x.running;
  try {
    const { runs } = await api("POST", `/api/schedules/${x.id}/runs`);
    if (editingSchedule !== x.id) return;
    $("sch-runs").replaceChildren(...runs.map((r) => {
      const li = document.createElement("li");
      li.textContent = `${when(r.started_at || r.due_at)} · ${STATUS_LABELS[r.status] || r.status}` + (r.reason ? ` · ${r.reason}` : "")
        + (r.result ? ` · ${r.result.slice(0, 200)}` : "");
      if (r.ref.startsWith("chat:")) { // a role run is a chat in history
        const a = document.createElement("button"); a.type = "button"; a.className = "ghost"; a.textContent = "Open chat";
        a.onclick = () => { $("schedules-dialog").close(); openChat(Number(r.ref.slice(5))); }; li.append(" ", a);
      } else if (r.ref.startsWith("team:")) {
        const a = document.createElement("button"); a.type = "button"; a.className = "ghost"; a.textContent = "Open board";
        a.onclick = () => { $("schedules-dialog").close(); boardRun = Number(r.ref.slice(5)); openTeams(); }; li.append(" ", a);
      }
      return li;
    }));
  } catch { /* the list is extra */ }
}

function scheduleBody() {
  const type = $("sch-type").value, spec = { type };
  if (type === "daily" || type === "weekly") spec.time = $("sch-time").value;
  if (type === "weekly") spec.days = [...$("sch-days").querySelectorAll("input:checked")].map((cb) => cb.value);
  if (type === "interval") spec.minutes = Number($("sch-minutes").value);
  if (type === "file") spec.folder = $("sch-folder").value;
  if (type === "after") spec.schedule = Number($("sch-after").value);
  return { name: $("sch-name").value, kind: $("sch-kind").value, target: $("sch-target").value, task: $("sch-task").value, spec,
    notify: $("sch-notify").checked };
}

async function refreshSchedules() {
  try { await loadSchedules(); } catch (err) { flash(`Could not load schedules: ${err.message}.`, true); return; }
  const x = schedules.find((s) => s.id === editingSchedule);
  if (x) showScheduleStatus(x);
  clearTimeout(schTimer);
  if ($("schedules-dialog").open && schedules.some((s) => s.running)) schTimer = setTimeout(refreshSchedules, 3000);
}

async function openSchedules() {
  buildDays();
  try { await loadSchedules(); } catch (err) { flash(`Could not load schedules: ${err.message}.`, true); return; }
  editSchedule(schedules[0]?.id ?? null);
  $("schedules-dialog").showModal();
  refreshSchedules();
}

$("sch-type").onchange = showWhenFields;
$("sch-kind").onchange = () => fillTargets($("sch-kind").value, null);
$("sch-target").onchange = showCan;
$("sch-notify").onchange = () => { // a desktop notice too, if the browser allows it
  try { if ($("sch-notify").checked && "Notification" in window && Notification.permission === "default") Notification.requestPermission(); }
  catch { /* the window message is enough */ }
};
$("sch-draft").onclick = async () => { // P-ROUT-1: plain words to a draft; never saved without me
  const text = $("sch-words").value.trim();
  if (!text) return;
  const note = $("sch-draft-note");
  note.hidden = false; note.textContent = "Drafting…"; $("sch-draft").disabled = true;
  try {
    const { draft } = await api("POST", "/api/schedules/draft", { text });
    editSchedule(null);
    $("sch-words").value = text;
    $("sch-name").value = draft.name; $("sch-kind").value = draft.kind;
    fillTargets(draft.kind, draft.target || null);
    $("sch-task").value = draft.task; $("sch-notify").checked = draft.notify;
    const spec = draft.spec;
    $("sch-type").value = spec.type; $("sch-time").value = spec.time || "08:00";
    for (const cb of $("sch-days").querySelectorAll("input")) cb.checked = (spec.days || []).includes(cb.value);
    $("sch-minutes").value = spec.minutes || 60;
    showWhenFields();
    note.hidden = false;
    note.textContent = "Drafted. Check who does it, the task and the time, then press Save schedule."
      + (draft.problems.length ? ` Please fix: ${draft.problems.join("; ")}.` : "");
  } catch (err) { note.textContent = `Could not draft it: ${err.message}.`; }
  finally { $("sch-draft").disabled = false; }
};
$("schedule-new").onclick = () => editSchedule(null);
$("schedule-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const { schedule } = editingSchedule
      ? await api("PUT", `/api/schedules/${editingSchedule}`, scheduleBody())
      : await api("POST", "/api/schedules", scheduleBody());
    editingSchedule = schedule.id;
    await loadSchedules(); editSchedule(schedule.id);
  } catch (err) { $("sch-error").textContent = err.message; $("sch-error").hidden = false; }
};
$("sch-delete").onclick = async () => {
  if (!confirm("Delete this schedule and its run history? Chats and team runs it made are kept.")) return;
  try { await api("DELETE", `/api/schedules/${editingSchedule}`); editingSchedule = null; await loadSchedules(); editSchedule(schedules[0]?.id ?? null); }
  catch (err) { flash(`Could not delete: ${err.message}.`, true); }
};
$("sch-enabled").onchange = async () => {
  const x = schedules.find((s) => s.id === editingSchedule);
  try { if ($("sch-enabled").checked && x && x.notify && "Notification" in window && Notification.permission === "default") Notification.requestPermission(); }
  catch { /* the window message is enough */ }
  try { await api("POST", `/api/schedules/${editingSchedule}/enable`, { enabled: $("sch-enabled").checked }); }
  catch (err) { flash(`Could not change it: ${err.message}.`, true); }
  refreshSchedules();
};
$("sch-run").onclick = async () => {
  try { await api("POST", `/api/schedules/${editingSchedule}/run`); } catch (err) { flash(`${err.message}.`, true); }
  refreshSchedules();
};
$("sch-stop").onclick = async () => {
  try { await api("POST", `/api/schedules/${editingSchedule}/stop`); } catch (err) { flash(`${err.message}.`, true); }
  refreshSchedules();
};
$("open-schedules").onclick = openSchedules;
$("schedules-close").onclick = () => $("schedules-dialog").close();
$("schedules-dialog").addEventListener("close", () => clearTimeout(schTimer));

// ---------- packs (Milestone 8, G-PACK-1, G-PACK-2) ----------
async function loadPacks() {
  const { packs } = await api("GET", "/api/packs");
  $("pack-list").replaceChildren(...packs.map((pk) => {
    const card = document.createElement("div"); card.className = "pack-card";
    const head = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = `${pk.name}${pk.version ? ` ${pk.version}` : ""}`;
    const st = document.createElement("span"); st.className = "pill";
    st.textContent = !pk.installed ? "not installed" : pk.enabled ? "on" : "switched off";
    head.append(title, " ", st);
    const desc = document.createElement("div"); desc.className = "muted small"; desc.textContent = pk.description;
    const has = document.createElement("div"); has.className = "small";
    has.textContent = `Roles: ${pk.roles.join(", ") || "none"} · Teams: ${pk.teams.join(", ") || "none"} · `
      + `Connectors: ${pk.connectors.join(", ") || "none"} · Schedules: ${pk.schedules.map((x) => x.name).join(", ") || "none"}`;
    const rules = document.createElement("div"); rules.className = "small";
    rules.textContent = pk.rules.length ? `Suggested rules: ${pk.rules.map((x) => `${x.action} ${x.mode}`).join(", ")} (Auto is installed as Ask)` : "";
    const problem = document.createElement("div"); problem.className = "error-text";
    problem.textContent = pk.problem || pk.load_problem; problem.hidden = !(pk.problem || pk.load_problem);
    const readme = document.createElement("details");
    const sum = document.createElement("summary"); sum.textContent = "About this pack";
    const body = document.createElement("div"); body.className = "report"; body.innerHTML = renderMarkdown(pk.readme || ""); // sanitized
    readme.append(sum, body);
    const menu = document.createElement("menu");
    const btn = (label, cls, fn) => { const b = document.createElement("button"); b.type = "button"; b.className = cls; b.textContent = label; b.onclick = fn; menu.append(b); };
    if (pk.installed && pk.enabled) for (const v of pk.views || []) btn(`Open ${v.label}`, "ghost", () => openView(pk, v));
    if (!pk.installed) btn("Install", "", () => packAction(pk, "install"));
    else {
      btn(pk.enabled ? "Switch off" : "Switch on", "ghost", () => packAction(pk, "enable", { enabled: !pk.enabled }));
      btn("Remove", "danger", () => {
        if (confirm(`Remove ${pk.name}? Its roles, teams and connectors disappear, and its approval rules, schedules and waiting approvals are deleted. Its files in the packs folder, your chats and the activity log are kept.`)) packAction(pk, "remove");
      });
    }
    const settings = document.createElement("div"); // Milestone 9: settings I fill in, for example a folder
    if (pk.installed && pk.settings && pk.settings.length) {
      const fs = document.createElement("fieldset"); const lg = document.createElement("legend"); lg.textContent = "Settings"; fs.append(lg);
      const inputs = {};
      for (const st of pk.settings) {
        const l = document.createElement("label"); l.className = "block"; l.textContent = st.label + " ";
        const current = (pk.values || {})[st.key] || "";
        let inp;
        if (st.type === "longtext") { inp = document.createElement("textarea"); inp.rows = 2; inp.maxLength = 4000; inp.value = current; }
        else if (st.type === "secret") { // Milestone 10: a login, kept in Windows Credential Manager, never shown
          inp = document.createElement("input"); inp.type = "password"; inp.autocomplete = "off"; inp.maxLength = 1200;
          inp.placeholder = current ? "set (leave blank to keep it)" : "not set";
          const forget = document.createElement("button"); forget.type = "button"; forget.className = "ghost"; forget.textContent = "Forget";
          forget.hidden = !current;
          forget.onclick = async () => {
            try { await api("POST", `/api/packs/${encodeURIComponent(pk.id)}/settings`, { values: { [st.key]: "" } }); flash("Removed from Windows Credential Manager."); }
            catch (err) { flash(`Could not remove it: ${err.message}.`, true); }
            loadPacks();
          };
          l.append(inp, forget); inputs[st.key] = inp; inp.dataset.secret = "1"; fs.append(l); continue;
        } else { inp = document.createElement("input"); inp.maxLength = 400; inp.value = current; }
        if (st.type === "folder") inp.placeholder = "C:\\Users\\me\\Downloads";
        if (st.type === "file") inp.placeholder = "C:\\Users\\me\\Documents\\resume.md";
        inputs[st.key] = inp; l.append(inp); fs.append(l);
      }
      const save = document.createElement("button"); save.type = "button"; save.className = "ghost"; save.textContent = "Save settings";
      save.onclick = async () => {
        // a blank secret field means "keep it": only a typed secret is sent
        const values = Object.fromEntries(Object.entries(inputs).filter(([, i]) => !(i.dataset.secret && !i.value)).map(([k, i]) => [k, i.value]));
        try { await api("POST", `/api/packs/${encodeURIComponent(pk.id)}/settings`, { values }); flash("Settings saved."); loadPacks(); }
        catch (err) { flash(`Could not save: ${err.message}.`, true); }
      };
      fs.append(save); settings.append(fs);
    }
    card.append(head, desc, has, rules, problem, settings, readme, menu); return card;
  }));
  $("pack-list-empty").hidden = packs.length > 0;
}

async function packAction(pk, action, body) {
  try {
    const res = await api("POST", `/api/packs/${encodeURIComponent(pk.id)}/${action}`, body);
    if (action === "install") {
      flash(`Installed ${pk.name}.` + (res.suggested_auto.length ? ` Suggested Auto rules were set to Ask (${res.suggested_auto.join(", ")}): switch them in Settings if you want.` : "")
        + (res.schedules ? " Its schedules are switched off: turn them on in Schedules." : ""));
    } else if (action === "remove") flash(`Removed ${pk.name}.`);
  } catch (err) { flash(`Could not do that: ${err.message}.`, true); }
  await loadPacks();
  loadRoles().catch(() => {}); // pack roles appear or disappear in Talk to
}

// Milestone 11: a pack's read-only table (for example the job tracker), shown as plain text cells
let currentView = null;
async function openView(pk, v) {
  currentView = { pk, v };
  $("view-title").textContent = `${pk.name}: ${v.label}`;
  try {
    const t = await api("POST", `/api/packs/${encodeURIComponent(pk.id)}/view`, { action: v.action });
    const head = document.createElement("tr");
    head.append(...t.columns.map((c) => { const th = document.createElement("th"); th.textContent = c; return th; }));
    const rowActions = pk.row_actions || [];
    if (rowActions.length) { const th = document.createElement("th"); head.append(th); }
    $("view-table").replaceChildren(head, ...t.rows.map((r) => {
      const tr = document.createElement("tr");
      tr.append(...r.map((c) => { const td = document.createElement("td"); td.textContent = c; return td; }));
      if (rowActions.length) { // my buttons for this row (the first cell is its number)
        const td = document.createElement("td");
        for (const a of (r[0] === "" ? [] : rowActions)) { // a row with no number (a heading) gets none
          const b = document.createElement("button"); b.type = "button"; b.className = "ghost"; b.textContent = a.label;
          b.onclick = async () => {
            try { const res = await api("POST", `/api/packs/${encodeURIComponent(pk.id)}/row`, { action: a.action, row: r[0] }); flash(res.result); }
            catch (err) { flash(`${err.message}.`, true); }
            openView(pk, v);
          };
          td.append(b);
        }
        tr.append(td);
      }
      return tr;
    }));
    $("view-empty").hidden = t.rows.length > 0;
    $("view-title").textContent = `${pk.name}: ${v.label}` + (t.total > t.rows.length ? ` (showing ${t.rows.length} of ${t.total}, newest first)` : "");
  } catch (err) { flash(`Could not open it: ${err.message}.`, true); return; }
  if (!$("view-dialog").open) $("view-dialog").showModal();
}
$("view-refresh").onclick = () => currentView && openView(currentView.pk, currentView.v);
$("view-close").onclick = () => $("view-dialog").close();

$("open-packs").onclick = async () => {
  try { await loadPacks(); } catch (err) { flash(`Could not load packs: ${err.message}.`, true); return; }
  $("packs-dialog").showModal();
};
$("packs-close").onclick = () => $("packs-dialog").close();

// Milestone 9: a "tell me" schedule (reminder, briefing) shows its result when it is done.
let lastNotice = null, noticeBusy = false;
function storedNotice() { // null = this browser never kept a cursor (0 is a real cursor)
  try { const v = localStorage.getItem("hub-last-notice"); return v === null ? null : Number(v) || 0; } catch { return null; }
}
function keepNotice(n) { lastNotice = n; try { localStorage.setItem("hub-last-notice", String(n)); } catch { /* fine */ } }
async function showNotices(latest) {
  if (noticeBusy) return; // one at a time: the 5 s timer and other callers never show a notice twice
  noticeBusy = true;
  try {
    if (lastNotice === null) { const kept = storedNotice(); keepNotice(kept === null ? latest : kept); }
    if (latest < lastNotice) keepNotice(0); // a new hub.db counts from 1 again: start over, never stay stuck
    if (latest <= lastNotice) return;
    const { notices } = await api("GET", `/api/notices?after=${lastNotice}`);
    if (!notices.length) { keepNotice(latest); return; }
    const lines = notices.map((n) => `${n.name}: ${n.status === "done" ? (n.result || "done").slice(0, 300) : `did not finish: ${n.reason || n.result || n.status}`}`);
    // every notice of a batch stays readable together
    flash(lines.slice(-3).join("  |  ") + (lines.length > 3 ? `  (and ${lines.length - 3} more in Schedules)` : ""),
      notices.some((n) => n.status !== "done"));
    for (const n of notices) {
      try { if ("Notification" in window && Notification.permission === "granted") new Notification(n.name, { body: (n.result || n.reason || "").slice(0, 200) }); }
      catch { /* the window message is enough */ }
    }
    keepNotice(notices[notices.length - 1].id);
  } catch { /* next time */ }
  finally { noticeBusy = false; }
}

// The missed badge shows at start-up too, so a missed run is never hidden (G-SCHED-2).
api("GET", "/api/schedules").then((d) => showMissedBadge(d.missed.length)).catch(() => {});

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
    refreshGate(); setInterval(refreshGate, 5000); // approvals badge and pause state

    showStatus(data.status);
    if (data.status.pending) setTimeout(refreshStatus, 1500); // sign-in check still running
    else if (!data.status.confirmed && data.status.ok) els.status.textContent += " (confirmed after the first message)";
    if (data.notices.length) { els.notices.textContent = data.notices.join("\n"); els.notices.hidden = false; }
    refreshChats();
  } catch {
    showStatus({ ok: false, text: "This window is not connected. Start the app with start.bat or python -m hub.server." });
  }
})();
