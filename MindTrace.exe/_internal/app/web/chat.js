/* MindTrace 原生对话窗：仅消息 + 输入框，与网页端共享会话与流式同步 */
(function () {
  "use strict";

  const el = {
    messages: document.getElementById("messages"),
    input: document.getElementById("input"),
    send: document.getElementById("send"),
  };

  // 与网页端共享同一会话（同源 localStorage）
  let sessionId = localStorage.getItem("mt_session") || null;

  function scrollBottom() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  function showWelcome() {
    if (el.messages.querySelector(".msg")) return;
    el.messages.innerHTML =
      '<div class="welcome"><div class="welcome-title">MindTrace</div>' +
      '<div class="welcome-sub">和你的第二大脑对话</div></div>';
  }

  function addMessage(role, content, mid) {
    document.querySelector(".welcome")?.remove();
    const div = document.createElement("div");
    div.className = "msg " + role;
    if (mid) div.dataset.mid = mid;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = content;
    div.appendChild(bubble);

    const actions = document.createElement("div");
    actions.className = "msg-actions";
    if (role === "user") {
      const editBtn = document.createElement("button");
      editBtn.textContent = "编辑";
      editBtn.addEventListener("click", () => enterEdit(div, mid, content));
      actions.appendChild(editBtn);
    }
    const delBtn = document.createElement("button");
    delBtn.textContent = "删除";
    delBtn.className = "danger";
    delBtn.addEventListener("click", () => delMessage(div, mid));
    actions.appendChild(delBtn);
    div.appendChild(actions);

    el.messages.appendChild(div);
    scrollBottom();
    return bubble;
  }

  function enterEdit(msgEl, mid, original) {
    const bubble = msgEl.querySelector(".bubble");
    if (!bubble || !mid) return;
    const ta = document.createElement("textarea");
    ta.value = original;
    ta.className = "edit-ta";
    bubble.replaceWith(ta);
    ta.focus();
    const commit = async (save) => {
      if (!save) { renderBubble(ta, original); return; }
      const val = ta.value.trim();
      if (!val) { renderBubble(ta, original); return; }
      try {
        const resp = await fetch("/api/chat/messages/" + encodeURIComponent(mid), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: val, regenerate: true }),
        });
        if (resp.status === 503) { alert("模型未就绪"); return; }
        if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
        renderBubble(ta, val);
        let node = msgEl.nextElementSibling;
        while (node && node.classList.contains("msg")) {
          const next = node.nextElementSibling;
          node.remove();
          node = next;
        }
        const newBubble = addMessage("assistant", "");
        await streamResponse(resp, newBubble);
        if (!newBubble.textContent) newBubble.textContent = "（空回复）";
      } catch (e) {
        alert("编辑失败：" + e.message);
      }
    };
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commit(true); }
      else if (e.key === "Escape") commit(false);
    });
    ta.addEventListener("blur", () => commit(true));
  }

  function renderBubble(node, text) {
    const b = document.createElement("div");
    b.className = "bubble";
    b.textContent = text;
    node.replaceWith(b);
  }

  async function delMessage(msgEl, mid) {
    if (!mid) return;
    if (!confirm("删除这条消息及其后的所有消息？")) return;
    try {
      const resp = await fetch("/api/chat/messages/" + encodeURIComponent(mid), { method: "DELETE" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      let node = msgEl;
      while (node && node.classList.contains("msg")) {
        const next = node.nextElementSibling;
        node.remove();
        node = next && next.classList.contains("msg") ? next : null;
      }
      if (!el.messages.querySelector(".msg")) showWelcome();
    } catch (e) {
      alert("删除失败：" + e.message);
    }
  }

  async function streamResponse(resp, bubble) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        let event = "message";
        let dataLine = null;
        for (const line of part.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
        }
        if (!dataLine) continue;
        let payload;
        try { payload = JSON.parse(dataLine); } catch (e) { continue; }
        if (event === "done") {
          const last = el.messages.lastElementChild;
          if (last && last.classList.contains("msg") && payload.message_id) last.dataset.mid = payload.message_id;
        } else if (event === "error") {
          bubble.textContent += "\n[错误] " + (payload.error || "未知错误");
        } else if (payload.delta) {
          bubble.textContent += payload.delta;
        }
      }
      scrollBottom();
    }
  }

  // ---------- 语音对话（循环模式） ----------
  // 点一次 🎤 进入持续对话：录音→转写→发送→回答→播报→再录音……
  // 播报完成后（speakDoneSignal）自动开始下一轮；再点一次按钮退出。

  let voiceLoopActive = false;   // 是否处于循环对话
  let voiceListening = false;    // 正在录音/转写
  let voiceResolve = null;       // 等待转写结果的 promise resolver
  let voiceEpoch = 0;            // 世代计数：退出/重开时自增，使在途回调失效

  function setMicUI(live) {
    const mic = document.getElementById("micBtn");
    if (!mic) return;
    mic.classList.toggle("mic-live", live);
    mic.textContent = live ? "⏹" : "🎤";
    mic.title = live ? "对话中… 点击结束" : "语音对话：点一下开始/结束对话";
  }

  window.__toggleVoiceLoop = function () {
    if (voiceLoopActive) {
      stopVoiceLoop();
      return;
    }
    voiceLoopActive = true;
    voiceEpoch++;
    const epoch = voiceEpoch;
    setMicUI(true);
    voiceLoopStep(epoch);
  };

  function stopVoiceLoop() {
    voiceLoopActive = false;
    voiceEpoch++;
    setMicUI(false);
    if (voiceResolve) {          // 正在等转写结果 → 立即释放（回调按世代失效）
      const r = voiceResolve;
      voiceResolve = null;
      r(null);
    }
  }

  function voiceLoopStep(epoch) {
    if (!voiceLoopActive || voiceListening || !window.__bridge) return;
    voiceListening = true;
    const p = new Promise((resolve) => { voiceResolve = resolve; });
    window.__bridge.voiceInput();          // 开始录音（静音检测说完即停）
    p.then(function (text) {
      voiceListening = false;
      if (epoch !== voiceEpoch) return;    // 已退出或已重开 → 忽略旧回调
      voiceResolve = null;
      if (!text) {
        setTimeout(() => voiceLoopStep(epoch), 600);   // 没听清/超时 → 重试
        return;
      }
      window.__voiceInitiated = true;
      sendVoice(text, epoch);
    });
  }

  async function sendVoice(text, epoch) {
    if (!sessionId) {
      try {
        const r = await fetch("/api/chat/sessions", { method: "POST" });
        const s = await r.json();
        sessionId = s.client_id;
        localStorage.setItem("mt_session", sessionId);
      } catch (e) { /* 忽略 */ }
    }
    el.input.value = "";
    el.input.style.height = "auto";
    addMessage("user", text);
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      if (resp.status === 503) {
        const err = await resp.json();
        addMessage("assistant", "[模型未就绪] " + (err.error || ""));
        scheduleNextRound(epoch);
        return;
      }
      const ctype = resp.headers.get("content-type") || "";
      if (ctype.includes("application/json")) {
        const data = await resp.json();
        addMessage("assistant", data.reply || "已处理");
        speakAndNext(data.reply || "", epoch);
        return;
      }
      if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
      const bubble = addMessage("assistant", "");
      await streamResponse(resp, bubble);
      if (!bubble.textContent) bubble.textContent = "（空回复）";
      speakAndNext(bubble.textContent || "", epoch);
    } catch (e) {
      addMessage("assistant", "[错误] " + e.message);
      scheduleNextRound(epoch);
    }
    refreshSessions(); // 首条消息后标题会更新
  }

  function speakAndNext(reply, epoch) {
    if (!voiceLoopActive || epoch !== voiceEpoch) return;   // 已退出 → 不播报不继续
    if (!window.__bridge || !window.__bridge.speak) {
      scheduleNextRound(epoch);   // 无播报能力 → 直接下一轮
      return;
    }
    window.__bridge.speak(reply || "");   // 播报完成会触发 __onSpeakDone
  }

  window.__onSpeakDone = function () {
    if (voiceLoopActive) scheduleNextRound(voiceEpoch);
  };

  function scheduleNextRound(epoch) {
    if (voiceLoopActive && epoch === voiceEpoch) {
      setTimeout(() => voiceLoopStep(epoch), 400);   // 播报完/出错后开始下一轮
    }
  }

  // 兼容旧单次入口：桥/其他页面直接调 __voiceResult(text) 时走单次问答
  window.__voiceResult = function (text) {
    if (!text) return;
    el.input.value = text;
    window.__voiceInitiated = true;
    send();
  };

  // 循环模式下的转写结果入口（chat.html 已把 voiceResultSignal 接到这里）
  window.__onVoiceResult = function (text) {
    if (voiceLoopActive) {
      // 交给正在等待的 voiceLoopStep
      if (voiceResolve) {
        const r = voiceResolve;
        voiceResolve = null;
        r(text || "");
      }
      return;
    }
    window.__voiceResult(text);   // 非循环：单次问答
  };

  async function send() {
    const text = el.input.value.trim();
    if (!text) return;
    if (!sessionId) {
      try {
        const r = await fetch("/api/chat/sessions", { method: "POST" });
        const s = await r.json();
        sessionId = s.client_id;
        localStorage.setItem("mt_session", sessionId);
      } catch (e) { /* 忽略 */ }
    }
    el.input.value = "";
    el.input.style.height = "auto";
    addMessage("user", text);
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      if (resp.status === 503) {
        const err = await resp.json();
        addMessage("assistant", "[模型未就绪] " + (err.error || ""));
        return;
      }
      const ctype = resp.headers.get("content-type") || "";
      if (ctype.includes("application/json")) {
        const data = await resp.json();
        addMessage("assistant", data.reply || "已处理");
        return;
      }
      if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
      const bubble = addMessage("assistant", "");
      await streamResponse(resp, bubble);
      if (!bubble.textContent) bubble.textContent = "（空回复）";
      // 语音单次入口（非循环模式）→ 播报回复；循环模式由 sendVoice 统一播报
      if (window.__voiceInitiated && !voiceLoopActive && window.__bridge && window.__bridge.speak) {
        window.__bridge.speak(bubble.textContent || "");
      }
      window.__voiceInitiated = false;
    } catch (e) {
      addMessage("assistant", "[错误] " + e.message);
    }
    refreshSessions(); // 首条消息后标题会更新
  }

  // ---------- 会话切换（下拉） ----------

  let sessionsCache = [];
  const sessionBar = {
    btn: document.getElementById("sessionBtn"),
    dropdown: document.getElementById("sessionDropdown"),
    newBtn: document.getElementById("newSession"),
  };

  function fmtTime(iso) {
    if (!iso) return "";
    const t = new Date(iso);
    if (isNaN(t.getTime())) return "";
    const diff = Date.now() - t.getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return "刚刚";
    if (m < 60) return m + " 分钟前";
    const h = Math.floor(m / 60);
    if (h < 24) return h + " 小时前";
    const d = Math.floor(h / 24);
    if (d === 1) return "昨天";
    if (d < 7) return d + " 天前";
    return (t.getMonth() + 1) + "月" + t.getDate() + "日";
  }

  async function refreshSessions() {
    try {
      const resp = await fetch("/api/chat/sessions", { cache: "no-store" });
      const data = await resp.json();
      sessionsCache = data.sessions || [];
    } catch (e) { /* 忽略 */ }
    renderSessionBar();
  }

  function renderSessionBar() {
    if (!sessionBar.btn) return;
    const cur = sessionsCache.find((s) => s.client_id === sessionId) || null;
    sessionBar.btn.textContent = (cur && cur.title ? cur.title : "对话") + " ▾";
    sessionBar.btn.title = cur ? cur.title : "切换对话";
    sessionBar.dropdown.innerHTML = "";
    if (!sessionsCache.length) {
      const d = document.createElement("div");
      d.className = "chat-sess-empty";
      d.textContent = "还没有对话";
      sessionBar.dropdown.appendChild(d);
      return;
    }
    for (const s of sessionsCache) {
      const item = document.createElement("div");
      item.className = "chat-sess-item" + (s.client_id === sessionId ? " active" : "");
      const t = document.createElement("span");
      t.className = "s-t";
      t.textContent = s.title || "新对话";
      const d = document.createElement("span");
      d.className = "s-d";
      d.textContent = fmtTime(s.updated_at) + (s.message_count ? " · " + s.message_count + " 条" : "");
      item.appendChild(t);
      item.appendChild(d);
      item.addEventListener("click", () => switchSession(s.client_id));
      sessionBar.dropdown.appendChild(item);
    }
  }

  function closeDropdown() {
    sessionBar.dropdown.classList.add("hidden");
  }

  async function switchSession(clientId) {
    sessionId = clientId;
    localStorage.setItem("mt_session", clientId);
    closeDropdown();
    el.messages.innerHTML = "";
    showWelcome();
    try {
      const resp = await fetch(
        "/api/chat/sessions/" + encodeURIComponent(clientId) + "/messages",
        { cache: "no-store" }
      );
      const data = await resp.json();
      el.messages.innerHTML = "";
      const msgs = data.messages || [];
      if (!msgs.length) showWelcome();
      for (const m of msgs) addMessage(m.role, m.content, m.id);
    } catch (e) {
      showWelcome();
    }
    renderSessionBar();  // 立即用本地缓存更新按钮/列表
    refreshSessions();   // 后台刷新列表（不阻塞切换）
  }

  async function newSession() {
    try {
      const resp = await fetch("/api/chat/sessions", { method: "POST" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const s = await resp.json();
      await refreshSessions();
      await switchSession(s.client_id);
    } catch (e) {
      alert("新建对话失败：" + e.message);
    }
  }

  sessionBar.btn.addEventListener("click", (e) => {
    e.stopPropagation();
    sessionBar.dropdown.classList.toggle("hidden");
  });
  sessionBar.newBtn.addEventListener("click", newSession);
  document.addEventListener("click", (e) => {
    if (!sessionBar.dropdown.classList.contains("hidden") &&
        !sessionBar.dropdown.contains(e.target)) {
      closeDropdown();
    }
  });

  async function init() {
    if (!sessionId) {
      try {
        const r = await fetch("/api/chat/sessions", { method: "POST" });
        const s = await r.json();
        sessionId = s.client_id;
        localStorage.setItem("mt_session", sessionId);
      } catch (e) { /* 忽略 */ }
    }
    if (sessionId) {
      try {
        const resp = await fetch(
          "/api/chat/sessions/" + encodeURIComponent(sessionId) + "/messages",
          { cache: "no-store" }
        );
        const data = await resp.json();
        const msgs = data.messages || [];
        if (!msgs.length) showWelcome();
        for (const m of msgs) addMessage(m.role, m.content, m.id);
      } catch (e) {
        showWelcome();
      }
    } else {
      showWelcome();
    }
    refreshSessions();
  }

  el.send.addEventListener("click", send);
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  el.input.addEventListener("input", () => {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 140) + "px";
  });

  init();
})();
