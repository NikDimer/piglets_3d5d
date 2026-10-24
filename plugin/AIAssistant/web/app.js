/* STE Slicer AI Assistant — SPA logic (vanilla JS, no dependencies).
   Polling transport: /api/events + /api/status.
   Mascot "Pascal" the chameleon: idle (waves) / think / happy states. */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  lastEventId: 0,
  busy: false,
  connected: false,
  attachments: [],       // {name, kind, data(base64), url}
  pendingToolCard: null, // DOM element of the tool call in flight
};

/* ------------------------------------------------------------------ */
/* Pascal the chameleon — bottom-left speaking mascot                  */
/* ------------------------------------------------------------------ */

const MASCOT = {
  idle: { src: "/assets/mascot_idle_alpha.webm", speech: "Готов к работе! Опишите задачу — и я всё настрою сам 🦎" },
  think: { src: "/assets/mascot_think_alpha.webm", speech: "Хм-хм… анализирую геометрию и подбираю режим…" },
  happy: { src: "/assets/mascot_happy_alpha.webm", speech: "Ура! Готово — отчёт выше 🎉" },
};

let mascotState = "idle";
let happyTimer = null;
let speechTimer = null;

function pascalSay(text, autohideMs) {
  const bubble = $("pascal-speech");
  const textField = $("pascal-speech-text");
  if (!bubble || !textField) return;
  textField.textContent = text;
  bubble.classList.add("show");
  clearTimeout(speechTimer);
  if (autohideMs) {
    speechTimer = setTimeout(() => bubble.classList.remove("show"), autohideMs);
  }
}

function setMascot(newState) {
  if (!MASCOT[newState]) newState = "idle";
  mascotState = newState;
  for (const name of ["idle", "think", "happy"]) {
    const el = $("pascal-" + name);
    if (el) el.classList.toggle("show", name === newState);
  }
  pascalSay(MASCOT[newState].speech, newState === "idle" ? 7000 : 0);
}

function mascotHappyTemporarily(replyText) {
  setMascot("happy");
  if (replyText) {
    const snippet = String(replyText).replace(/[#*`>|-]/g, " ").replace(/\s+/g, " ").trim();
    pascalSay(snippet.length > 150 ? snippet.slice(0, 150) + "…" : snippet, 6000);
  }
  clearTimeout(happyTimer);
  happyTimer = setTimeout(() => setMascot("idle"), 4500);
}

/* ------------------------------------------------------------------ */
/* tiny markdown renderer                                              */
/* ------------------------------------------------------------------ */

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdown(md) {
  const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
  let html = [];
  let inCode = false, codeBuf = [];
  let listType = null;
  let tableBuf = [];

  const closeList = () => {
    if (listType) { html.push("</" + listType + ">"); listType = null; }
  };
  const flushTable = () => {
    if (!tableBuf.length) return;
    const rows = tableBuf.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
    const cells = rows.map((r) => r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim()));
    if (cells.length) {
      let t = "<table><thead><tr>";
      for (const c of cells[0]) t += "<th>" + inlineMd(c) + "</th>";
      t += "</tr></thead><tbody>";
      for (let i = 1; i < cells.length; i++) {
        t += "<tr>";
        for (const c of cells[i]) t += "<td>" + inlineMd(c) + "</td>";
        t += "</tr>";
      }
      t += "</tbody></table>";
      html.push(t);
    }
    tableBuf = [];
  };

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      if (inCode) {
        html.push("<pre><code>" + escapeHtml(codeBuf.join("\n")) + "</code></pre>");
        codeBuf = []; inCode = false;
      } else {
        closeList(); flushTable(); inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    if (/^\s*\|.*\|\s*$/.test(line)) { closeList(); tableBuf.push(line); continue; }
    flushTable();

    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeList(); const l = h[1].length + 1; html.push("<h" + l + ">" + inlineMd(h[2]) + "</h" + l + ">"); continue; }

    if (/^\s*[-*]\s+/.test(line)) {
      if (listType !== "ul") { closeList(); html.push("<ul>"); listType = "ul"; }
      html.push("<li>" + inlineMd(line.replace(/^\s*[-*]\s+/, "")) + "</li>");
      continue;
    }
    const ol = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (ol) {
      if (listType !== "ol") { closeList(); html.push("<ol>"); listType = "ol"; }
      html.push("<li>" + inlineMd(ol[2]) + "</li>");
      continue;
    }
    if (/^\s*>\s?/.test(line)) { closeList(); html.push("<blockquote>" + inlineMd(line.replace(/^\s*>\s?/, "")) + "</blockquote>"); continue; }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { closeList(); html.push("<hr>"); continue; }
    if (!line.trim()) { closeList(); continue; }
    closeList();
    html.push("<p>" + inlineMd(line) + "</p>");
  }
  if (inCode) html.push("<pre><code>" + escapeHtml(codeBuf.join("\n")) + "</code></pre>");
  closeList();
  flushTable();
  return html.join("");
}

function inlineMd(text) {
  let t = escapeHtml(text);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  t = t.replace(/(^|\s)\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
  t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return t;
}

/* ------------------------------------------------------------------ */
/* DOM builders                                                        */
/* ------------------------------------------------------------------ */

const chat = $("chat");

function removeEmptyState() {
  const e = $("empty-state");
  if (e) e.remove();
}

function mascotVideoTag(stateName) {
  return '<video src="' + MASCOT[stateName || "idle"].src + '" autoplay loop muted playsinline preload="auto"></video>';
}function addUserMessage(text, attachments) {
  removeEmptyState();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  let pills = "";
  for (const a of attachments || []) {
    pills += '<span class="attach-pill">' + (a.kind === "image" ? "🖼" : "🧊") + " " + escapeHtml(a.name) + "</span>";
  }
  wrap.innerHTML =
    '<div class="avatar">Я</div>' +
    '<div class="bubble"><div>' + renderMarkdown(text) + "</div>" +
    (pills ? '<div class="attach-list">' + pills + "</div>" : "") +
    "</div>";
  chat.appendChild(wrap);
  scrollDown();
}

function addBotMessage(text) {
  removeEmptyState();
  const wrap = document.createElement("div");
  wrap.className = "msg bot";
  wrap.innerHTML =
    '<div class="avatar">' + mascotVideoTag(mascotState === "happy" ? "happy" : mascotState === "think" ? "think" : "idle") + "</div>" +
    '<div class="bubble">' + renderMarkdown(text) + "</div>";
  chat.appendChild(wrap);
  scrollDown();
  return wrap;
}

function addTypingIndicator() {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";
  wrap.id = "typing";
  wrap.innerHTML =
    '<div class="avatar">' + mascotVideoTag("think") + "</div>" +
    '<div class="bubble"><span class="typing"><i></i><i></i><i></i></span> <span style="color:#93a1b3;font-size:14px">Паскаль работает со слайсером…</span></div>';
  chat.appendChild(wrap);
  scrollDown();
}

function removeTypingIndicator() {
  const t = $("typing");
  if (t) t.remove();
}

function addToolCard(name, args) {
  removeEmptyState();
  const card = document.createElement("details");
  card.className = "tool-card";
  card.innerHTML =
    "<summary><span class='spinner'></span>" +
    "<span class='tname'>" + escapeHtml(name) + "</span>" +
    "<span class='targs'>" + escapeHtml(shortArgs(args)) + "</span>" +
    "<span class='tstatus'>выполняется…</span></summary>" +
    "<div class='tbody'>" +
    "<div><div class='label'>Аргументы</div><pre>" + escapeHtml(JSON.stringify(args, null, 2)) + "</pre></div>" +
    "<div><div class='label'>Результат</div><pre>ожидание…</pre></div>" +
    "</div>";
  chat.appendChild(card);
  scrollDown();
  return card;
}

function finishToolCard(card, ok, resultText, durationMs) {
  if (!card) return;
  card.classList.remove("error");
  card.classList.add(ok ? "done" : "error");
  const spinner = card.querySelector(".spinner");
  if (spinner) spinner.remove();
  const status = card.querySelector(".tstatus");
  if (status) status.textContent = (ok ? "✅ " : "❌ ") + durationMs + " мс";
  const pre = card.querySelectorAll(".tbody pre")[1];
  if (pre) pre.textContent = resultText;
}

function shortArgs(args) {
  try {
    const s = JSON.stringify(args);
    return s.length > 60 ? s.slice(0, 60) + "…" : s;
  } catch (e) { return ""; }
}

function scrollDown() {
  requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
}

/* ------------------------------------------------------------------ */
/* attachments                                                         */
/* ------------------------------------------------------------------ */

const MODEL_EXT = ["stl", "obj", "3mf"];
const IMAGE_EXT = ["jpg", "jpeg", "png"];

function fileKind(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (MODEL_EXT.indexOf(ext) >= 0) return "model";
  if (IMAGE_EXT.indexOf(ext) >= 0) return "image";
  return null;
}

function addFiles(fileList) {
  for (const file of fileList) {
    const kind = fileKind(file.name);
    if (!kind) { toast("Неподдерживаемый файл: " + file.name, true); continue; }
    if (state.attachments.length >= 6) { toast("Максимум 6 вложений", true); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const b64 = String(reader.result).split(",")[1] || "";
      state.attachments.push({ name: file.name, kind, data: b64, url: reader.result });
      renderAttachments();
    };
    reader.readAsDataURL(file);
  }
}

function renderAttachments() {
  const zone = $("attach-zone");
  zone.classList.toggle("active", state.attachments.length > 0);
  zone.innerHTML = "";
  state.attachments.forEach((a, i) => {
    const pill = document.createElement("div");
    pill.className = "attach-preview";
    pill.innerHTML =
      (a.kind === "image" ? "<img src='" + a.url + "'>" : "🧊") +
      "<span class='name'>" + escapeHtml(a.name) + "</span>" +
      "<button title='Убрать'>×</button>";
    pill.querySelector("button").onclick = () => {
      state.attachments.splice(i, 1);
      renderAttachments();
    };
    zone.appendChild(pill);
  });
}

/* ------------------------------------------------------------------ */
/* sending                                                             */
/* ------------------------------------------------------------------ */

async function sendMessage() {
  const input = $("input");
  const text = input.value.trim();
  if (!text && !state.attachments.length) return;
  if (state.busy) { toast("Паскаль ещё занят предыдущей задачей", true); return; }

  const attachments = state.attachments.slice();
  addUserMessage(text || "(файлы)", attachments);
  input.value = "";
  autosize();
  state.attachments = [];
  renderAttachments();
  addTypingIndicator();
  setMascot("think");

  try {
    const res = await fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, attachments }),
    });
    const data = await res.json();
    if (!data.ok) {
      removeTypingIndicator();
      setMascot("idle");
      if (data.error === "api_key_missing") {
        openModal("modal-settings");
        toast("Сначала задайте API-ключ в настройках", true);
      } else if (data.error === "busy") {
        toast("Агент занят", true);
      } else {
        toast("Ошибка: " + data.error, true);
      }
      return;
    }
  } catch (e) {
    removeTypingIndicator();
    setMascot("idle");
    toast("Сервер плагина недоступен: " + e, true);
  }
}

/* ------------------------------------------------------------------ */
/* events polling                                                      */
/* ------------------------------------------------------------------ */

async function pollEvents() {
  try {
    const res = await fetch("/api/events?after=" + state.lastEventId);
    const data = await res.json();
    for (const ev of data.events || []) {
      if (ev.id <= state.lastEventId) continue;
      state.lastEventId = ev.id;
      handleEvent(ev);
    }
  } catch (e) { setConnected(false); }
}

function handleEvent(ev) {
  const d = ev.data || {};
  if (ev.type === "tool_call") {
    state.pendingToolCard = addToolCard(d.name, d.arguments);
    setMascot("think");
    pascalSay("Работаю со слайсером: " + d.name + "…");
  } else if (ev.type === "tool_result") {
    finishToolCard(state.pendingToolCard, d.ok, d.summary + (d.duration_ms ? "\n⏱ " + d.duration_ms + " мс" : ""), d.duration_ms || 0);
    state.pendingToolCard = null;
    scrollDown();
  } else if (ev.type === "assistant") {
    removeTypingIndicator();
    state.pendingToolCard = null;
    addBotMessage(d.text);
    mascotHappyTemporarily(d.text);
  } else if (ev.type === "user") {
    removeTypingIndicator();
  } else if (ev.type === "status") {
    if (d.state === "idle") removeTypingIndicator();
  }
}

/* ------------------------------------------------------------------ */
/* status polling                                                      */
/* ------------------------------------------------------------------ */

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const s = await res.json();
    setConnected(true);
    $("hdr-printer").textContent = s.active_printer || "—";
    const mode = s.printing_mode;
    $("hdr-mode").textContent = mode && mode.label ? mode.label : "—";
    $("hdr-time").textContent = s.print_time || "—";
    const w = (s.material_weights_g || []).filter((x) => x > 0);
    $("hdr-material").textContent = w.length ? w.map((x) => x + " г").join(" + ") : "—";

    const slicing = s.slicing || {};
    const bar = $("slice-bar");
    const chip = $("slice-chip");
    if (slicing.state === "slicing") {
      chip.style.display = "";
      $("slice-state").textContent = "нарезка " + Math.round((slicing.progress || 0) * 100) + "%";
      bar.style.width = Math.round((slicing.progress || 0) * 100) + "%";
    } else if (slicing.state === "done") {
      chip.style.display = "";
      $("slice-state").textContent = "нарезка завершена";
      bar.style.width = "100%";
      setTimeout(() => { bar.style.width = "0%"; }, 2500);
    } else {
      chip.style.display = "none";
      bar.style.width = "0%";
    }
    state.busy = !!s.busy;
    $("send").disabled = state.busy;
  } catch (e) { setConnected(false); }
}

function setConnected(ok) {
  if (state.connected === ok) return;
  state.connected = ok;
  const dot = $("conn-dot");
  dot.className = "dot " + (ok ? "ok" : "off");
  $("conn-text").textContent = ok ? "слайсер подключен" : "нет связи";
}

/* ------------------------------------------------------------------ */
/* history                                                             */
/* ------------------------------------------------------------------ */

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    if (!(data.messages || []).length) return;
    removeEmptyState();
    for (const m of data.messages) {
      if (m.role === "user") {
        addUserMessage(m.text, (m.attachments || []).map((n) => ({ name: n, kind: fileKind(n) || "file" })));
      } else {
        for (const c of m.tool_cards || []) {
          const card = addToolCard(c.name, c.arguments);
          finishToolCard(card, c.ok, JSON.stringify(c.result, null, 2).slice(0, 4000), c.duration_ms);
        }
        addBotMessage(m.text);
      }
    }
  } catch (e) { /* first run */ }
}

/* ------------------------------------------------------------------ */
/* modals                                                              */
/* ------------------------------------------------------------------ */

function openModal(id) { $(id).classList.add("open"); }
function closeModal(id) { $(id).classList.remove("open"); }

document.querySelectorAll("[data-close]").forEach((el) => {
  el.addEventListener("click", () => closeModal(el.getAttribute("data-close")));
});
document.querySelectorAll(".modal-backdrop").forEach((bd) => {
  bd.addEventListener("click", (e) => { if (e.target === bd) bd.classList.remove("open"); });
});

/* settings */

$("btn-settings").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/settings");
    const s = await res.json();
    $("set-url").value = s.api_url || "";
    $("set-key").value = s.api_key || "";
    $("set-model").value = s.model_name || "";
  } catch (e) {}
  openModal("modal-settings");
});

$("btn-save-settings").addEventListener("click", async () => {
  const body = {
    api_url: $("set-url").value.trim(),
    api_key: $("set-key").value.trim(),
    model_name: $("set-model").value.trim(),
  };
  if (!body.api_key) { toast("API-ключ не может быть пустым", true); return; }
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    closeModal("modal-settings");
    toast("Настройки сохранены ✓");
    mascotHappyTemporarily();  } catch (e) { toast("Ошибка сохранения: " + e, true); }
});

/* tools */

$("btn-tools").addEventListener("click", async () => {
  openModal("modal-tools");
  const list = $("tools-list");
  list.innerHTML = "<p style='color:#93a1b3'>Загрузка…</p>";
  try {
    const res = await fetch("/api/tools");
    const data = await res.json();
    list.innerHTML = "";
    for (const t of data.tools || []) {
      const item = document.createElement("div");
      item.className = "tool-list-item";
      item.innerHTML = "<div class='tn'>🔧 " + escapeHtml(t.name) + "</div><div class='td'>" + escapeHtml(t.description) + "</div>";
      list.appendChild(item);
    }
  } catch (e) { list.innerHTML = "<p style='color:#f87171'>Не удалось получить список инструментов</p>"; }
});

/* export */

$("btn-export").addEventListener("click", () => openModal("modal-export"));

$("btn-do-export").addEventListener("click", async () => {
  const name = $("export-name").value.trim() || "model";
  const out = $("export-result");
  out.style.color = "#93a1b3";
  out.textContent = "Экспортирую… (нужен завершенный слайсинг)";
  try {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: name }),
    });
    const data = await res.json();
    if (data.ok) {
      const r = data.result;
      out.style.color = "#10b981";
      out.innerHTML =
        "✅ Готово:<br><code>" + escapeHtml(r.project_3mf) + "</code><br><code>" +
        escapeHtml(r.gcode) + "</code><br>Папка: <code>" + escapeHtml(r.directory) + "</code><br>" +
        "Положите скриншоты переписки (chat_*.png) в эту папку для сдачи.";
      toast("Экспорт завершён ✓");
      mascotHappyTemporarily();
    } else {
      out.style.color = "#f87171";
      out.textContent = "❌ " + (data.error || "ошибка экспорта");
    }
  } catch (e) {
    out.style.color = "#f87171";
    out.textContent = "❌ " + e;
  }
});

/* ------------------------------------------------------------------ */
/* composer wiring                                                     */
/* ------------------------------------------------------------------ */

const input = $("input");

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
}

input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

$("send").addEventListener("click", sendMessage);
$("btn-attach").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

const composer = $("composer");
["dragenter", "dragover"].forEach((t) =>
  composer.addEventListener(t, (e) => { e.preventDefault(); composer.classList.add("dragover"); }));
["dragleave", "drop"].forEach((t) =>
  composer.addEventListener(t, (e) => { e.preventDefault(); composer.classList.remove("dragover"); }));
composer.addEventListener("drop", (e) => { if (e.dataTransfer) addFiles(e.dataTransfer.files); });

document.querySelectorAll(".preset").forEach((btn) => {
  btn.addEventListener("click", () => {
    input.value = btn.getAttribute("data-text");
    input.focus();
    autosize();
  });
});

/* toast */

let toastTimer = null;
function toast(message, isError) {
  const el = $("toast");
  el.textContent = message;
  el.className = isError ? "err show" : "show";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, 3500);
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

setMascot("idle");

document.querySelectorAll("#pascal video").forEach((v) => {
  v.addEventListener("click", () => pascalSay(MASCOT[mascotState].speech, 6000));
});

loadHistory().then(() => {
  fetch("/api/events").then((r) => r.json()).then((data) => {
    for (const ev of data.events || []) state.lastEventId = Math.max(state.lastEventId, ev.id);
  }).catch(() => {});
});

pollStatus();
pollEvents();
setInterval(pollStatus, 1500);
setInterval(pollEvents, 800);
