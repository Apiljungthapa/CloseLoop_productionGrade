/*
 * CloseLoop chat widget: a floating icon that opens a chat box.
 * Self-contained: it builds its own DOM, so a host page only needs the CSS and this script.
 *
 *   <link rel="stylesheet" href="closeloop-chat.css">
 *   <script src="closeloop-chat.js"
 *           data-gateway="http://127.0.0.1:8010"
 *           data-api-key=""            optional, sent as X-API-Key
 *           data-demo="false"          "true" = canned answers, no backend
 *           data-title="CloseLoop Assistant"></script>
 *
 * Optional JS API: window.CloseLoopChat.open() / .close() / .toggle()
 */
(() => {
  "use strict";
  if (window.CloseLoopChat) return; // already mounted

  const script = document.currentScript;
  const attr = (name, fallback) => script?.dataset[name] ?? fallback;

  const DEFAULTS = {
    gateway: attr("gateway", "http://127.0.0.1:8010"),
    key: attr("apiKey", ""),
    topK: 5,
    demo: attr("demo", "false") === "true",
  };
  const TITLE = attr("title", "Close Bhai");

  // ---------- helpers ----------
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const load = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } };

  const ICON_CHAT = '<svg viewBox="0 0 24 24" width="WW" height="WW" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/></svg>';
  const icon = (w) => ICON_CHAT.replaceAll("WW", w);

  // ---------- state ----------
  const state = {
    settings: { ...DEFAULTS },
    messages: load("cl.widget.messages", []),
    scope: null, // document_id to restrict search to, or null
    busy: false,
    open: false,
  };
  const SESSION = (() => {
    let id = load("cl.widget.session", "");
    if (!id) { id = (crypto.randomUUID?.() ?? String(Math.random()).slice(2) + Date.now()).replace(/[^A-Za-z0-9_-]/g, ""); save("cl.widget.session", id); }
    return id;
  })();
  const persist = () => save("cl.widget.messages", state.messages.slice(-100));

  // ---------- DOM (static template; the title is set via textContent) ----------
  const root = el("div", "cl-widget");
  root.innerHTML = `
    <section class="cl-panel" role="dialog" aria-label="Chat assistant" aria-hidden="true">
      <header class="cl-head">
        <div class="cl-avatar">${icon(20)}</div>
        <div class="cl-who"><b data-ref="title"></b>
          <small class="cl-status" data-ref="status"><span class="cl-dot"></span><span data-ref="statusText">Checking…</span></small>
        </div>
        <button class="cl-head-btn" data-ref="resetBtn" title="New conversation" aria-label="New conversation">↺</button>
        <button class="cl-head-btn" data-ref="closeBtn" title="Close" aria-label="Close">✕</button>
      </header>
      <div class="cl-messages" data-ref="messages" aria-live="polite"></div>
      <form class="cl-composer" data-ref="composer">
        <div class="cl-scope" data-ref="scopeRow" hidden>Only searching <strong data-ref="scopeName"></strong>
          <button type="button" data-ref="clearScope">clear</button></div>
        <div class="cl-row">
          <textarea class="cl-input" data-ref="input" rows="1" maxlength="2000" placeholder="Ask CloseLoop anything…"></textarea>
          <button type="submit" class="cl-send" data-ref="send" aria-label="Send">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M3.4 20.4 21 12 3.4 3.6 3.3 10l12.6 2-12.6 2z"/></svg>
          </button>
        </div>
      </form>
    </section>
    <button class="cl-fab" data-ref="fab" aria-label="Open chat" aria-expanded="false">
      <span class="cl-i-chat">${icon(28)}</span>
      <svg class="cl-i-close" viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>
      <span class="cl-unread" data-ref="unread" hidden>1</span>
    </button>`;

  const ui = {};
  root.querySelectorAll("[data-ref]").forEach((n) => { ui[n.dataset.ref] = n; });
  ui.panel = root.querySelector(".cl-panel");
  ui.title.textContent = TITLE;
  ui.input.placeholder = "Ask about CloseLoop or its docs…";

  // ---------- API ----------
  async function ask(question) {
    if (state.settings.demo) return demoAnswer(question);
    const headers = { "Content-Type": "application/json" };
    if (state.settings.key) headers["X-API-Key"] = state.settings.key;
    const res = await fetch(`${state.settings.gateway}/workflow/ask`, {
      method: "POST", headers,
      body: JSON.stringify({ question, top_k: state.settings.topK, session_id: SESSION }),
    });
    if (!res.ok) {
      let detail = "";
      try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? ""); } catch { /* not json */ }
      const names = { 401: "Invalid API key", 422: "Invalid request", 502: "The language model is unavailable", 503: "The vector database is unavailable", 501: "That capability is disabled" };
      throw new Error(`${names[res.status] || "Request failed"} (${res.status})${detail ? ": " + detail : ""}`);
    }
    return res.json();
  }

  // Streaming: POST /workflow/stream, parsed as Server-Sent Events (status, route, sources, token, done, error).
  async function askStream(question, on) {
    const headers = { "Content-Type": "application/json" };
    if (state.settings.key) headers["X-API-Key"] = state.settings.key;
    const res = await fetch(`${state.settings.gateway}/workflow/stream`, {
      method: "POST", headers,
      body: JSON.stringify({ question, top_k: state.settings.topK, session_id: SESSION }),
    });
    if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    const dispatch = (block) => {
      let ev = "message", data = "";
      block.split("\n").forEach((line) => {
        if (line.startsWith("event:")) ev = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      });
      if (!data) return;
      let payload; try { payload = JSON.parse(data); } catch { return; }
      if (ev === "error") throw new Error(payload.detail || payload.message || "The assistant hit an error");
      on[ev]?.(payload);
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.search(/\r?\n\r?\n/)) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i).replace(/^\r?\n\r?\n/, "");
        dispatch(block);
      }
    }
    if (buf.trim()) dispatch(buf);
  }

  async function checkHealth() {
    ui.status.className = "cl-status";
    if (state.settings.demo) { ui.status.classList.add("demo"); ui.statusText.textContent = "Demo mode"; return; }
    try {
      const res = await fetch(`${state.settings.gateway}/health`);
      if (!res.ok) throw 0;
      ui.status.classList.add("ok"); ui.statusText.textContent = "Online";
    } catch {
      ui.status.classList.add("bad"); ui.statusText.textContent = "Offline";
    }
  }

  // Demo mode: canned replies so the UI works without a backend.
  function demoAnswer(q) {
    return new Promise((resolve) => setTimeout(() => {
      resolve(/^(hi|hello|hey|thanks|thank you)\b/i.test(q.trim())
        ? { answer: "Hi! How can I help with your close today?", route: "chitchat", sources: [] }
        : {
            answer: `Demo answer for: "${q}"\n\nWhen the backend is connected, the grounded answer from your documents appears here, with its sources below.`,
            route: "doc",
            sources: [
              { document_id: "close-checklist", title: "Close Checklist", section: "Carry forward", score: 0.62 },
              { document_id: "policy", title: "Accounting Policy", section: null, score: 0.41 },
            ],
          });
    }, 900));
  }

  // ---------- rendering ----------
  // Text is always set via textContent (never innerHTML), with minimal `code` / ```block``` support.
  function formatInto(container, text) {
    text.split(/```/).forEach((part, i) => {
      if (i % 2) {
        const pre = el("pre");
        pre.append(el("code", "", part.replace(/^\w*\n/, "")));
        container.append(pre);
      } else {
        part.split(/`([^`\n]+)`/).forEach((seg, j) => {
          if (j % 2) { container.append(el("code", "", seg)); return; }
          seg.split(/\*\*([^*\n]+)\*\*/).forEach((t, k) => {
            container.append(k % 2 ? el("strong", "", t) : document.createTextNode(t));
          });
        });
      }
    });
  }

  // Safe markdown for bot replies, built with DOM nodes (never innerHTML): headings, lists, **bold**, *italic*, `code`, ``` blocks, links.
  function inlineInto(parent, text) {
    const re = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*?\*|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.append(document.createTextNode(text.slice(last, m.index)));
      const t = m[0];
      if (t[0] === "`") parent.append(el("code", "", t.slice(1, -1)));
      else if (t.startsWith("**")) { const b = el("strong"); inlineInto(b, t.slice(2, -2)); parent.append(b); }
      else if (t[0] === "*") { const i = el("em"); inlineInto(i, t.slice(1, -1)); parent.append(i); }
      else {
        const [, label, url] = /^\[([^\]]+)\]\((.+)\)$/.exec(t);
        const a = el("a", "", label); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; parent.append(a);
      }
      last = m.index + t.length;
    }
    if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
  }

  function renderMarkdown(container, text) {
    container.replaceChildren();
    const lines = text.replace(/\r/g, "").split("\n");
    let list = null, para = null, fence = null;
    const flush = () => { list = null; para = null; };
    for (const line of lines) {
      if (fence) {
        if (/^\s*```/.test(line)) { fence = null; } else { fence.firstChild.textContent += (fence.firstChild.textContent ? "\n" : "") + line; }
        continue;
      }
      if (/^\s*```/.test(line)) {
        flush(); fence = el("pre"); fence.append(el("code")); container.append(fence); continue;
      }
      let m;
      if (!line.trim()) { flush(); continue; }
      if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
        flush(); const h = el("div", "cl-h"); inlineInto(h, m[2]); container.append(h);
      } else if ((m = /^\s*(?:[-*•])\s+(.*)$/.exec(line))) {
        if (!list || list.tagName !== "UL") { list = el("ul"); container.append(list); para = null; }
        const li = el("li"); inlineInto(li, m[1]); list.append(li);
      } else if ((m = /^\s*(\d+)[.)]\s+(.*)$/.exec(line))) {
        if (!list || list.tagName !== "OL") { list = el("ol"); list.start = +m[1]; container.append(list); para = null; }
        const li = el("li"); inlineInto(li, m[2]); list.append(li);
      } else {
        if (list && /^\s{2,}\S/.test(line) && list.lastChild) { list.lastChild.append(document.createTextNode(" " + line.trim())); continue; }
        list = null;
        if (!para) { para = el("p"); container.append(para); } else para.append(el("br"));
        inlineInto(para, line);
      }
    }
  }

  function messageNode(m) {
    const wrap = el("div", `cl-msg ${m.role}`);
    const bubble = el("div", "cl-bubble" + (m.error ? " cl-error" : ""));
    if (m.role === "bot" && !m.error) renderMarkdown(bubble, m.text); else formatInto(bubble, m.text);
    wrap.append(bubble);

    const meta = el("div", "cl-meta");
    if (m.role === "bot" && !m.error && !m.streaming) {
      if (m.route && m.route !== "chitchat") meta.append(el("span", "cl-tag", m.route));
      const seen = new Set();
      (m.sources || []).filter((s) => { const k = `${s.title || s.document_id}|${s.section || ""}`; return seen.has(k) ? false : seen.add(k); }).forEach((s) => {
        const chip = el("button", "cl-src", `${s.title || s.document_id}${s.section ? " · " + s.section : ""}`);
        chip.type = "button";
        chip.title = `Score ${(s.score * 100).toFixed(0)}%`;
        meta.append(chip);
      });
      const copy = el("button", "cl-mini", "Copy");
      copy.type = "button";
      copy.onclick = () => navigator.clipboard?.writeText(m.text).then(() => { copy.textContent = "Copied"; });
      meta.append(copy);
    }
    if (m.error && m.retry) {
      const b = el("button", "cl-chip", "Retry");
      b.type = "button";
      b.onclick = () => { state.messages = state.messages.filter((x) => x !== m); persist(); render(); respond(m.retry); };
      meta.append(b);
    }
    if (meta.childNodes.length) wrap.append(meta);
    return wrap;
  }

  function render() {
    ui.messages.replaceChildren();
    if (!state.messages.length) {
      const w = el("div", "cl-welcome");
      const big = el("div", "cl-big");
      big.innerHTML = icon(26);
      w.append(big, el("h4", "", `Hello, I am ${TITLE}`), el("p", "", "You can ask me anything related to CloseLoop and its docs."));
      const chips = el("div", "cl-chips");
      ["What is a close pack?", "How does carry-forward work?", "What period cadence types are supported?"].forEach((s) => {
        const b = el("button", "cl-chip", s);
        b.type = "button";
        b.onclick = () => submit(s);
        chips.append(b);
      });
      w.append(chips);
      ui.messages.append(w);
      return;
    }
    state.messages.forEach((m) => ui.messages.append(messageNode(m)));
    scrollDown();
  }

  const scrollDown = () => { ui.messages.scrollTop = ui.messages.scrollHeight; };

  function showTyping() {
    const w = el("div", "cl-msg bot cl-typing");
    const b = el("div", "cl-bubble");
    b.append(el("span"), el("span"), el("span"));
    w.append(b);
    ui.messages.append(w);
    scrollDown();
    return w;
  }

  function setScope(id, title) {
    state.scope = id;
    ui.scopeRow.hidden = !id;
    ui.scopeName.textContent = title || id || "";
  }

  // ---------- sending ----------
  async function submit(textOverride) {
    const text = (textOverride ?? ui.input.value).trim();
    if (!text || state.busy) return;
    state.messages.push({ role: "user", text });
    ui.input.value = "";
    autosize();
    persist(); render();
    await respond(text);
  }

  async function respond(question) {
    state.busy = true; ui.send.disabled = true;
    const typing = showTyping();
    const label = el("small", "cl-status-line");
    typing.append(label);
    let msg = null, bubble = null, full = "", shown = 0, streamDone = false, raf = 0, drained = null;
    // Chunks from the model can arrive in bursts; reveal them smoothly (faster when far behind) so it reads as typing.
    const tick = () => {
      const behind = full.length - shown;
      if (behind > 0) {
        shown += Math.max(2, Math.ceil(behind / 18));
        msg.text = full.slice(0, shown);
        renderMarkdown(bubble, msg.text);
        const tail = [...bubble.querySelectorAll("p, li, .cl-h")].pop() || bubble;
        tail.append(el("span", "cl-caret"));
        scrollDown();
      }
      if (shown >= full.length && streamDone) { bubble.querySelector(".cl-caret")?.remove(); drained?.(); return; }
      raf = requestAnimationFrame(tick);
    };
    try {
      if (state.settings.demo) {
        const r = await ask(question);
        state.messages.push({ role: "bot", text: r.answer, route: r.route, sources: r.sources || [] });
      } else {
        const meta = { route: null, sources: [] };
        await askStream(question, {
          status: (d) => { label.textContent = d.message || ""; },
          route: (d) => { meta.route = d.route; },
          sources: (d) => { meta.sources = d.items || []; },
          token: (d) => {
            if (!msg) {
              typing.remove();
              msg = { role: "bot", text: "", route: null, sources: [], streaming: true };  // chips + Copy appear when the answer is complete
              state.messages.push(msg);
              render();
              bubble = ui.messages.querySelector(".cl-msg.bot:last-of-type .cl-bubble");
              bubble.classList.add("cl-streaming");
              raf = requestAnimationFrame(tick);
            }
            full += d.text || "";
          },
          done: () => { if (msg) { msg.route = meta.route; msg.sources = meta.sources; } },
        });
        if (!msg) throw new Error("No answer was received. Please try again.");
        streamDone = true;
        await new Promise((res) => { drained = res; if (shown >= full.length) { cancelAnimationFrame(raf); bubble.querySelector(".cl-caret")?.remove(); res(); } });
        msg.text = full;
        delete msg.streaming;
      }
    } catch (e) {
      cancelAnimationFrame(raf);
      if (msg) state.messages = state.messages.filter((x) => x !== msg);
      const offline = e instanceof TypeError;
      state.messages.push({
        role: "bot", error: true, retry: question,
        text: offline ? `Can't reach the assistant at ${state.settings.gateway}. Is the backend running?` : e.message,
      });
      if (offline) checkHealth();
    } finally {
      typing.remove();
      state.busy = false; ui.send.disabled = false;
      persist(); render();
      if (state.open) ui.input.focus(); else ui.unread.hidden = false;
    }
  }

  function autosize() {
    ui.input.style.height = "auto";
    ui.input.style.height = Math.min(ui.input.scrollHeight, 110) + "px";
  }

  // ---------- open / close ----------
  function setOpen(open) {
    state.open = open;
    root.classList.toggle("cl-open", open);
    ui.fab.setAttribute("aria-expanded", String(open));
    ui.fab.setAttribute("aria-label", open ? "Close chat" : "Open chat");
    ui.panel.setAttribute("aria-hidden", String(!open));
    if (open) {
      ui.unread.hidden = true;
      scrollDown();
      setTimeout(() => ui.input.focus(), 150);
    }
  }

  // ---------- wiring ----------
  ui.fab.onclick = () => setOpen(!state.open);
  ui.closeBtn.onclick = () => setOpen(false);
  root.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.open) setOpen(false); });

  ui.resetBtn.onclick = () => {
    if (state.messages.length && !confirm("Start a new conversation? This clears the current one.")) return;
    state.messages = []; setScope(null); save("cl.widget.session", ""); location.reload();
  };
  ui.clearScope.onclick = () => setScope(null);

  ui.composer.onsubmit = (e) => { e.preventDefault(); submit(); };
  ui.input.addEventListener("input", autosize);
  ui.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });

  // ---------- mount ----------
  function mount() { document.body.append(root); render(); checkHealth(); }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  window.CloseLoopChat = { open: () => setOpen(true), close: () => setOpen(false), toggle: () => setOpen(!state.open) };
})();