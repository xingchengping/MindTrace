/* MindTrace Phase 1：四视图（对话/任务/经验/时间线）+ 会话管理 + 记忆对话 */
(function () {
  "use strict";

  const el = {
    status: document.getElementById("status"),
    messages: document.getElementById("messages"),
    input: document.getElementById("input"),
    send: document.getElementById("send"),
    newChat: document.getElementById("newChat"),
    sessionList: document.getElementById("sessionList"),
    viewNav: document.querySelector(".view-nav"),
    tasksContent: document.getElementById("tasksContent"),
    experiencesContent: document.getElementById("experiencesContent"),
    timelineContent: document.getElementById("timelineContent"),
    maintenanceContent: document.getElementById("maintenanceContent"),
    settingsContent: document.getElementById("settingsContent"),
  };

  let sessions = [];
  let activeClientId = localStorage.getItem("mt_session") || null;

  // ---------- 状态 ----------

  function setStatus(text, cls) {
    el.status.textContent = text;
    el.status.className = "status" + (cls ? " " + cls : "");
  }

  async function loadHealth() {
    try {
      const resp = await fetch("/api/health");
      const h = await resp.json();
      const ok = h.status === "ok";
      const mem = h.memory_ready ? " · 记忆" + h.vector_count : " · 记忆未启用";
      setStatus(
        (ok ? "● 在线" : "○ 模型未就绪") + " · " + (h.profile || "") + mem,
        ok ? "ok" : "warn"
      );
    } catch (e) {
      setStatus("○ 无法连接后端", "warn");
    }
  }

  // ---------- 视图切换 ----------

  function switchView(name) {
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
    document.querySelectorAll(".view-nav button").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === name)
    );
    if (name === "tasks") loadTasks();
    if (name === "experiences") loadExperiences();
    if (name === "timeline") loadTimeline();
    if (name === "graph") loadGraph();
    if (name === "maintenance") loadMaintenance();
    if (name === "settings") loadSettings();
  }

  el.viewNav.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-view]");
    if (btn) switchView(btn.dataset.view);
  });

  // ---------- 消息渲染 ----------

  function scrollBottom() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  function showWelcome() {
    if (el.messages.querySelector(".msg")) return;
    el.messages.innerHTML =
      '<div class="welcome"><div class="welcome-title">你的第二大脑</div>' +
      '<div class="welcome-sub">Phase 1 · 本地记忆已启用 · 用「记住：xxx」主动记录</div></div>';
  }

  function addFeedback(msgEl, mid) {
    const fb = document.createElement("div");
    fb.className = "msg-feedback show";
    const useful = document.createElement("button");
    useful.textContent = "👍 有用";
    const useless = document.createElement("button");
    useless.textContent = "👎 没用";
    const mark = async (btn, val) => {
      if (!mid) return;
      try {
        await fetch("/api/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message_id: mid, useful: val }),
        });
        useful.classList.add("done");
        useless.classList.add("done");
        useful.disabled = useless.disabled = true;
      } catch (e) { /* 忽略 */ }
    };
    useful.addEventListener("click", () => mark(useful, true));
    useless.addEventListener("click", () => mark(useless, false));
    fb.appendChild(useful);
    fb.appendChild(useless);
    msgEl.appendChild(fb);
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
      editBtn.addEventListener("click", () => enterEditMode(div, mid, content));
      actions.appendChild(editBtn);
    }
    const delBtn = document.createElement("button");
    delBtn.textContent = "删除";
    delBtn.className = "danger";
    delBtn.addEventListener("click", () => deleteMessage(div, mid));
    actions.appendChild(delBtn);
    div.appendChild(actions);

    el.messages.appendChild(div);
    scrollBottom();
    return bubble;
  }

  async function deleteMessage(msgEl, mid) {
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

  function enterEditMode(msgEl, mid, original) {
    const bubble = msgEl.querySelector(".bubble");
    if (!bubble || !mid) return;
    bubble.replaceWith(createEditBox(original, async (newContent) => {
      if (!newContent.trim()) return;
      await saveEdit(msgEl, mid, newContent.trim());
    }));
  }

  function createEditBox(value, onSave) {
    const box = document.createElement("div");
    box.className = "edit-box";
    const ta = document.createElement("textarea");
    ta.value = value;
    const bar = document.createElement("div");
    bar.className = "edit-bar";
    const saveBtn = document.createElement("button");
    saveBtn.className = "save";
    saveBtn.textContent = "保存并重新生成";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "cancel";
    cancelBtn.textContent = "取消";
    bar.appendChild(saveBtn);
    bar.appendChild(cancelBtn);
    box.appendChild(ta);
    box.appendChild(bar);

    const cleanup = () => {
      const b = document.createElement("div");
      b.className = "bubble";
      b.textContent = ta.value;
      box.replaceWith(b);
    };
    saveBtn.addEventListener("click", () => { cleanup(); onSave(ta.value); });
    cancelBtn.addEventListener("click", cleanup);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveBtn.click(); }
    });
    setTimeout(() => ta.focus(), 0);
    return box;
  }

  async function saveEdit(msgEl, mid, newContent) {
    try {
      const resp = await fetch("/api/chat/messages/" + encodeURIComponent(mid), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: newContent, regenerate: true }),
      });
      if (resp.status === 503) { alert("模型未就绪"); return; }
      if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);

      const bubble = msgEl.querySelector(".bubble");
      if (bubble) bubble.textContent = newContent;

      let node = msgEl.nextElementSibling;
      while (node && node.classList.contains("msg")) {
        const next = node.nextElementSibling;
        node.remove();
        node = next;
      }

      const newBubble = addMessage("assistant", "");
      const lastMsg = await streamResponse(resp, newBubble);
      if (!lastMsg.ok && lastMsg.error) newBubble.textContent += "\n[错误] " + lastMsg.error;
      if (!newBubble.textContent) newBubble.textContent = "（空回复）";
    } catch (e) {
      alert("编辑失败：" + e.message);
    }
  }

  // ---------- SSE 流式解析 ----------

  async function streamResponse(resp, bubble) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let ok = true;
    let error = null;
    let doneMid = null;
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
        if (event === "error") { ok = false; error = payload.error || "未知错误"; }
        else if (event === "done") {
          doneMid = payload.message_id || null;
          const last = el.messages.lastElementChild;
          if (last && last.classList.contains("msg") && doneMid) last.dataset.mid = doneMid;
        } else if (payload.delta) {
          bubble.textContent += payload.delta;
        }
      }
      scrollBottom();
    }
    return { ok, error, messageId: doneMid };
  }

  // ---------- 发送 ----------

  async function send() {
    const text = el.input.value.trim();
    if (!text) return;
    if (!activeClientId) await createSession(true);

    el.input.value = "";
    el.input.style.height = "auto";
    addMessage("user", text);

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: activeClientId }),
      });
      if (resp.status === 503) {
        const err = await resp.json();
        addMessage("assistant", "[模型未就绪] " + (err.error || ""));
        return;
      }
      // 记忆写入指令返回 JSON（记住：/记住经验：）
      const ctype = resp.headers.get("content-type") || "";
      if (ctype.includes("application/json")) {
        const data = await resp.json();
        const bubble = addMessage("assistant", data.reply || "已处理");
        void bubble;
        await refreshSessions();
        return;
      }
      if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
      const bubble = addMessage("assistant", "");
      const result = await streamResponse(resp, bubble);
      if (!result.ok && result.error) bubble.textContent += "\n[错误] " + result.error;
      if (!bubble.textContent) bubble.textContent = "（空回复）";
      const lastMsg = el.messages.lastElementChild;
      if (lastMsg && lastMsg.classList.contains("msg") && result.messageId) {
        addFeedback(lastMsg, result.messageId);
      }
      await refreshSessions();
    } catch (e) {
      addMessage("assistant", "[错误] " + e.message);
    }
  }

  // ---------- 会话管理 ----------

  function renderSessions() {
    el.sessionList.innerHTML = "";
    if (!sessions.length) {
      const li = document.createElement("li");
      li.className = "sidebar-empty";
      li.textContent = "还没有对话，点上方新建";
      el.sessionList.appendChild(li);
      return;
    }
    for (const s of sessions) {
      const li = document.createElement("li");
      li.className = "session-item" + (s.client_id === activeClientId ? " active" : "");
      li.dataset.client = s.client_id;

      const title = document.createElement("span");
      title.className = "s-title";
      title.textContent = s.title;
      title.title = "双击重命名";
      li.appendChild(title);

      const actions = document.createElement("span");
      actions.className = "s-actions";
      const renameBtn = document.createElement("button");
      renameBtn.textContent = "✎";
      renameBtn.title = "重命名";
      renameBtn.addEventListener("click", (e) => { e.stopPropagation(); startRename(li, s); });
      const delBtn = document.createElement("button");
      delBtn.textContent = "✕";
      delBtn.className = "danger";
      delBtn.title = "删除对话";
      delBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteSessionItem(s); });
      actions.appendChild(renameBtn);
      actions.appendChild(delBtn);
      li.appendChild(actions);

      li.addEventListener("click", () => switchSession(s.client_id));
      title.addEventListener("dblclick", (e) => { e.stopPropagation(); startRename(li, s); });
      el.sessionList.appendChild(li);
    }
  }

  function startRename(li, s) {
    const titleEl = li.querySelector(".s-title");
    const input = document.createElement("input");
    input.className = "s-title-input";
    input.value = s.title;
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    const commit = async (save) => {
      const val = input.value.trim();
      if (save && val) {
        try {
          const resp = await fetch("/api/chat/sessions/" + encodeURIComponent(s.client_id), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: val }),
          });
          if (resp.ok) { s.title = val; renderSessions(); return; }
        } catch (e) { /* fallthrough */ }
      }
      renderSessions();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit(true);
      else if (e.key === "Escape") commit(false);
    });
    input.addEventListener("blur", () => commit(true));
  }

  async function deleteSessionItem(s) {
    if (!confirm('删除对话「' + s.title + '」？该对话的所有消息将被删除。')) return;
    try {
      const resp = await fetch("/api/chat/sessions/" + encodeURIComponent(s.client_id), { method: "DELETE" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      sessions = sessions.filter((x) => x.client_id !== s.client_id);
      if (activeClientId === s.client_id) {
        activeClientId = null;
        localStorage.removeItem("mt_session");
        if (sessions.length) await switchSession(sessions[0].client_id);
        else {
          renderSessions();
          showWelcome();
          await createSession(true);
        }
      } else {
        renderSessions();
      }
    } catch (e) {
      alert("删除失败：" + e.message);
    }
  }

  async function refreshSessions() {
    try {
      const resp = await fetch("/api/chat/sessions");
      const data = await resp.json();
      sessions = data.sessions || [];
      renderSessions();
    } catch (e) { /* 忽略 */ }
  }

  async function createSession(activate) {
    try {
      const resp = await fetch("/api/chat/sessions", { method: "POST" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const s = await resp.json();
      sessions.unshift({ client_id: s.client_id, title: s.title });
      renderSessions();
      if (activate) await switchSession(s.client_id);
      return s;
    } catch (e) {
      alert("新建对话失败：" + e.message);
      return null;
    }
  }

  async function switchSession(clientId) {
    activeClientId = clientId;
    localStorage.setItem("mt_session", clientId);
    renderSessions();
    el.messages.innerHTML = "";
    try {
      const resp = await fetch("/api/chat/sessions/" + encodeURIComponent(clientId) + "/messages");
      const data = await resp.json();
      const msgs = data.messages || [];
      if (!msgs.length) showWelcome();
      for (const m of msgs) {
        const bubble = addMessage(m.role, m.content, m.id);
        if (m.role === "assistant") addFeedback(el.messages.lastElementChild, m.id);
        void bubble;
      }
    } catch (e) {
      showWelcome();
    }
  }

  // ---------- 任务视图 ----------

  async function loadTasks() {
    el.tasksContent.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
      const resp = await fetch("/api/tasks");
      const data = await resp.json();
      const tasks = data.tasks || [];
      let html = "";
      if (!tasks.length) {
        html = '<div class="empty-state">还没有任务记录。<br>系统正在自动跟踪你的工作过程…</div>';
      } else {
        for (const t of tasks) {
          const stage = t.stage || "working";
          const actions = (stage !== "solved" && stage !== "abandoned")
            ? '<div class="t-actions">' +
              '<button class="done" data-act="solved" data-id="' + t.id + '">✓ 标记完成</button>' +
              '<button class="abandon" data-act="abandoned" data-id="' + t.id + '">放弃</button></div>'
            : "";
          html +=
            '<div class="task-card" data-id="' + t.id + '">' +
            '<div class="t-head"><span class="t-name">' + esc(t.task_name) + '</span>' +
            '<span class="t-stage">' + esc(stage) + '</span></div>' +
            (t.goal ? '<div class="t-field"><b>目标：</b>' + esc(t.goal) + '</div>' : "") +
            (t.current_problem ? '<div class="t-field"><b>当前困难：</b>' + esc(t.current_problem) + '</div>' : "") +
            (t.project ? '<div class="t-field"><b>项目：</b>' + esc(t.project) + '</div>' : "") +
            actions + "</div>";
        }
      }
      html += '<div class="mnt-section"><h3>💡 实时联想（最近匹配的记忆）</h3><div id="recallList"></div></div>';
      el.tasksContent.innerHTML = html;
      el.tasksContent.querySelectorAll(".task-card .t-actions button").forEach((btn) => {
        btn.addEventListener("click", () => updateTaskStage(parseInt(btn.dataset.id, 10), btn.dataset.act));
      });
      loadRecalls();
    } catch (e) {
      el.tasksContent.innerHTML = '<div class="empty-state">加载失败：' + esc(e.message) + "</div>";
    }
  }

  async function loadRecalls() {
    const box = document.getElementById("recallList");
    if (!box) return;
    try {
      const resp = await fetch("/api/recall/recent");
      const data = await resp.json();
      const recalls = data.recalls || [];
      if (!recalls.length) {
        box.innerHTML = '<div class="mnt-result">暂无（卡点持续时会自动联想过去的经验）</div>';
        return;
      }
      box.innerHTML = "";
      for (const r of recalls) {
        const row = document.createElement("div");
        row.className = "recall-item";
        row.innerHTML = '<div class="recall-title">' + esc(r.title) + "</div>" +
          (r.body ? '<div class="recall-body">' + esc(r.body) + "</div>" : "") +
          '<div class="recall-meta">' + esc((r.created_at || "").slice(0, 16)) + " · " + esc(r.status) + "</div>";
        box.appendChild(row);
      }
    } catch (e) { /* 忽略 */ }
  }

  async function updateTaskStage(id, stage) {
    try {
      const resp = await fetch("/api/tasks/" + id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage }),
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      loadTasks();
    } catch (e) {
      alert("操作失败：" + e.message);
    }
  }

  // ---------- 经验库视图 ----------

  async function loadExperiences() {
    el.experiencesContent.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
      const resp = await fetch("/api/experiences");
      const data = await resp.json();
      const exps = data.experiences || [];
      if (!exps.length) {
        el.experiencesContent.innerHTML =
          '<div class="empty-state">经验库还是空的。<br>用「记住经验：问题 => 方案」沉淀你的第一条经验。</div>';
        return;
      }
      el.experiencesContent.innerHTML = "";
      for (const e of exps) {
        const card = document.createElement("div");
        card.className = "exp-card";
        const badge = e.confirmed
          ? '<span class="e-badge confirmed">✓ 已确认</span>'
          : '<span class="e-badge">待确认</span>';
        const tags = (e.tags || []).map((t) => "<span>" + esc(t) + "</span>").join("");
        card.innerHTML =
          '<div class="e-head"><span class="e-problem">' + esc(e.problem) + "</span>" + badge + "</div>" +
          (e.final_solution ? '<div class="e-solution"><b>方案：</b>' + esc(e.final_solution) + "</div>" : "") +
          (e.scenarios ? '<div class="e-solution"><b>适用场景：</b>' + esc(e.scenarios) + "</div>" : "") +
          '<div class="e-meta">' +
          (e.created_at ? "<span>" + esc(String(e.created_at).slice(0, 10)) + "</span>" : "") +
          '<span>重要度 ' + (e.importance || 0).toFixed(2) + "</span>" +
          (tags ? '<span class="e-tags">' + tags + "</span>" : "") +
          "</div>" +
          '<div class="e-actions"></div>';
        const actions = card.querySelector(".e-actions");
        const confirmBtn = document.createElement("button");
        confirmBtn.className = "confirm";
        confirmBtn.textContent = e.confirmed ? "取消确认" : "✓ 确认";
        confirmBtn.addEventListener("click", () => updateExperience(e.id, e.confirmed ? "draft" : "confirmed", card));
        const delBtn = document.createElement("button");
        delBtn.className = "danger";
        delBtn.textContent = "删除";
        delBtn.addEventListener("click", () => deleteExperience(e.id, card));
        actions.appendChild(confirmBtn);
        actions.appendChild(delBtn);
        el.experiencesContent.appendChild(card);
      }
    } catch (e) {
      el.experiencesContent.innerHTML = '<div class="empty-state">加载失败：' + esc(e.message) + "</div>";
    }
  }

  async function updateExperience(id, status, card) {
    try {
      const resp = await fetch("/api/experiences/" + id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      loadExperiences();
    } catch (e) {
      alert("操作失败：" + e.message);
    }
  }

  async function deleteExperience(id, card) {
    if (!confirm("删除这条经验？")) return;
    try {
      const resp = await fetch("/api/experiences/" + id, { method: "DELETE" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      loadExperiences();
    } catch (e) {
      alert("删除失败：" + e.message);
    }
  }

  // ---------- 时间线视图 ----------

  async function loadTimeline() {
    el.timelineContent.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
      // 事件 + 经验都进时间线（经验 = "项目怎么解决的"类重要记忆）
      const [evResp, expResp] = await Promise.all([
        fetch("/api/events?limit=500"),
        fetch("/api/experiences"),
      ]);
      const evData = await evResp.json();
      const expData = await expResp.json();
      const items = [];
      for (const e of evData.events || []) {
        items.push({
          time: e.time, text: e.activity || e.intent || "",
          app: e.app, project: e.project, source: e.source,
          importance: e.importance, kind: "event",
        });
      }
      for (const x of expData.experiences || []) {
        items.push({
          time: x.created_at, text: x.problem,
          app: null, project: null,
          source: x.confirmed ? "已确认" : "待确认",
          importance: x.importance, kind: "experience",
        });
      }
      if (!items.length) {
        el.timelineContent.innerHTML =
          '<div class="empty-state">还没有记忆。<br>聊几句，或用「记住：xxx」开始记录。</div>';
        return;
      }
      const days = {};
      for (const it of items) {
        const raw = it.time ? String(it.time).slice(0, 10) : "";
        const day = raw ? raw.slice(0, 4) + "年" + raw.slice(4) : "未知日期";
        (days[day] = days[day] || []).push(it);
      }
      el.timelineContent.innerHTML = '<div class="timeline"></div>';
      const tl = el.timelineContent.querySelector(".timeline");
      for (const day of Object.keys(days)) {
        const dayDiv = document.createElement("div");
        dayDiv.className = "tl-day";
        dayDiv.textContent = day;
        tl.appendChild(dayDiv);
        for (const it of days[day]) {
          const item = document.createElement("div");
          item.className = "tl-item";
          const time = it.time ? String(it.time).slice(11, 16) : "";
          const imp = it.importance > 0.6 ? '<span class="tl-badge tl-imp">重要</span>' : "";
          const kindBadge = it.kind === "experience"
            ? '<span class="tl-badge tl-exp">经验</span>' : "";
          item.innerHTML =
            '<div class="tl-time">' + esc(time) + "</div>" +
            '<div class="tl-text">' + esc(it.text) + "</div>" +
            '<div class="tl-badges">' +
            kindBadge +
            (it.app ? '<span class="tl-badge">' + esc(it.app) + "</span>" : "") +
            (it.project ? '<span class="tl-badge">' + esc(it.project) + "</span>" : "") +
            (it.source ? '<span class="tl-badge">' + esc(it.source) + "</span>" : "") +
            imp +
            "</div>";
          tl.appendChild(item);
        }
      }
    } catch (e) {
      el.timelineContent.innerHTML = '<div class="empty-state">加载失败：' + esc(e.message) + "</div>";
    }
  }

  // ---------- 图谱视图 ----------

  const GRAPH_COLORS = {
    project: "#0a84ff", file: "#5e5ce6", code: "#af52de", paper: "#ff9f0a",
    problem: "#ff3b30", solution: "#34c759", decision: "#ff2d55", experience: "#00c7be",
  };
  const GRAPH_TYPE_ORDER = ["project", "file", "code", "paper", "problem", "solution", "decision", "experience"];
  let graphData = { nodes: [], edges: [] };
  let graphEditMode = false;      // 编辑模式开关
  let graphEditTarget = null;     // 当前编辑的 {type:'node'|'edge', id}

  async function loadGraph() {
    try {
      const resp = await fetch("/api/graph/summary");
      const s = await resp.json();
      const types = Object.entries(s.types || {}).map(([k, v]) => k + ":" + v).join(" · ");
      document.getElementById("graphSummary").innerHTML =
        '<span>' + (s.nodes || 0) + " 节点 · " + (s.edges || 0) + " 边</span> " +
        '<span class="graph-type-legend">' + esc(types) + "</span>";
      const vresp = await fetch("/api/graph/view?limit=200");
      const v = await vresp.json();
      graphData = v;
      drawGraph();
    } catch (e) {
      document.getElementById("graphSummary").textContent = "图谱加载失败：" + e.message;
    }
  }

  function drawGraph() {
    const canvas = document.getElementById("graphCanvas");
    const wrap = document.querySelector(".graph-wrap");
    if (!canvas || !wrap) return;
    const W = wrap.clientWidth || 800;
    const H = wrap.clientHeight || 520;
    canvas.width = W * 2;
    canvas.height = H * 2;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W * 2, H * 2);
    ctx.scale(2, 2);
    ctx.clearRect(0, 0, W, H);

    const nodes = graphData.nodes || [];
    const edges = graphData.edges || [];
    if (!nodes.length) {
      ctx.fillStyle = "#86868b";
      ctx.font = "14px sans-serif";
      ctx.fillText("图谱还是空的（系统会随你的工作自动构建）", W / 2 - 130, H / 2);
      return;
    }
    // 按类型分列布局
    const colW = W / Math.max(GRAPH_TYPE_ORDER.length, 1);
    const pos = {};
    const counts = {};
    for (const n of nodes) {
      const t = n.type || "?";
      counts[t] = (counts[t] || 0) + 1;
    }
    nodes.forEach((n) => {
      const t = n.type || "?";
      const idx = GRAPH_TYPE_ORDER.indexOf(t);
      const col = idx >= 0 ? idx : GRAPH_TYPE_ORDER.length;
      const colCount = counts[t] || 1;
      const i = (counts["_i_" + t] = (counts["_i_" + t] || 0));
      counts["_i_" + t] = i + 1;
      pos[n.id] = {
        x: colW * (col + 0.5),
        y: H * ((i + 0.5) / colCount),
      };
    });

    // 边（编辑模式高亮可点）
    ctx.lineWidth = 1;
    for (const e of edges) {
      const a = pos[e.from], b = pos[e.to];
      if (!a || !b) continue;
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      e._mid = mid;
      ctx.strokeStyle = graphEditMode ? "rgba(10,132,255,0.7)" : "rgba(134,134,139,0.4)";
      ctx.lineWidth = graphEditMode ? 2.5 : 1;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      if (graphEditMode) {
        // 边中点小标记（可点编辑）
        ctx.beginPath();
        ctx.arc(mid.x, mid.y, 5, 0, Math.PI * 2);
        ctx.fillStyle = "#0a84ff";
        ctx.fill();
      }
    }
    // 节点
    for (const n of nodes) {
      const p = pos[n.id];
      if (!p) continue;
      const color = GRAPH_COLORS[n.type] || "#8e8e93";
      ctx.beginPath();
      ctx.arc(p.x, p.y, graphEditMode ? 10 : 7, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      if (graphEditMode) {
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      ctx.fillStyle = "#1d1d1f";
      ctx.font = "10px sans-serif";
      ctx.fillText(String(n.name || "").slice(0, 8), p.x + 9, p.y + 3);
    }
    canvas._pos = pos;
    canvas._nodes = nodes;
    canvas._edges = edges;
    canvas.onclick = (ev) => {
      const rect = canvas.getBoundingClientRect();
      const mx = (ev.clientX - rect.left) * (W / rect.width);
      const my = (ev.clientY - rect.top) * (H / rect.height);
      if (graphEditMode) {
        // 编辑模式：先查边中点（小范围），再查节点
        for (const e of edges) {
          const m = e._mid;
          if (m && Math.hypot(mx - m.x, my - m.y) < 12) {
            openEdgeEdit(e);
            return;
          }
        }
        for (const n of nodes) {
          const p = pos[n.id];
          if (!p) continue;
          if (Math.hypot(mx - p.x, my - p.y) < 16) {
            openNodeEdit(n);
            return;
          }
        }
        return;
      }
      for (const n of nodes) {
        const p = pos[n.id];
        if (!p) continue;
        if (Math.hypot(mx - p.x, my - p.y) < 14) {
          expandNode(n.id, n.name);
          break;
        }
      }
    };
  }

  async function expandNode(id, name) {
    try {
      const resp = await fetch("/api/graph/node/" + id + "/neighbors?hops=2");
      const data = await resp.json();
      const detail = document.getElementById("graphDetail");
      const nbrs = data.neighbors || [];
      detail.innerHTML = '<div class="graph-detail-title">「' + esc(name) + '」的关联：</div>' +
        nbrs.map((b) =>
          '<div class="graph-nbr"><span class="graph-nbr-type" style="color:' +
          (GRAPH_COLORS[b.type] || "#8e8e93") + '">' + esc(b.type) + "</span> " +
          esc(b.name) + ' <span class="graph-nbr-rel">(' + esc((b.relations || []).join("/")) + ")</span></div>"
        ).join("") || '<div class="mnt-result">无关联</div>';
    } catch (e) { /* 忽略 */ }
  }

  // ---------- 图谱编辑 ----------

  function toggleGraphEdit() {
    graphEditMode = !graphEditMode;
    const btn = document.getElementById("graphEditToggle");
    const panel = document.getElementById("graphEditPanel");
    btn.textContent = graphEditMode ? "✅ 完成编辑" : "✏️ 编辑图谱";
    btn.classList.toggle("active", graphEditMode);
    panel.classList.toggle("hidden", !graphEditMode);
    closeEditForms();
    drawGraph();
  }

  function closeEditForms() {
    graphEditTarget = null;
    const nf = document.getElementById("geNodeForm");
    const ef = document.getElementById("geEdgeForm");
    if (nf) nf.classList.add("hidden");
    if (ef) ef.classList.add("hidden");
  }

  function openNodeEdit(n) {
    graphEditTarget = { type: "node", id: n.id };
    closeEditForms();
    const f = document.getElementById("geNodeForm");
    f.classList.remove("hidden");
    document.getElementById("geNodeTitle").textContent = "编辑节点 #" + n.id;
    document.getElementById("geNodeName").value = n.name || "";
    document.getElementById("geNodeSummary").value = n.summary || "";
  }

  function openEdgeEdit(e) {
    graphEditTarget = { type: "edge", id: e.id };
    closeEditForms();
    const f = document.getElementById("geEdgeForm");
    f.classList.remove("hidden");
    document.getElementById("geEdgeTitle").textContent =
      "编辑边 #" + e.id + "（" + e.from + " → " + e.to + "）";
    document.getElementById("geEdgeRel").value = e.relation || "";
  }

  async function apiGraph(path, opts) {
    const resp = await fetch(path, opts);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.detail || "HTTP " + resp.status);
    }
    return resp.json();
  }

  function bindGraphEdit() {
    const $ = (id) => document.getElementById(id);
    $("graphEditToggle").addEventListener("click", toggleGraphEdit);
    $("geAddNode").addEventListener("click", async () => {
      const name = $("geNewName").value.trim();
      if (!name) return alert("请输入节点名称");
      try {
        await apiGraph("/api/graph/nodes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, ntype: $("geNewType").value }),
        });
        $("geNewName").value = "";
        await loadGraph();
      } catch (e) { alert("新增失败：" + e.message); }
    });
    $("geNodeSave").addEventListener("click", async () => {
      if (!graphEditTarget || graphEditTarget.type !== "node") return;
      try {
        await apiGraph("/api/graph/node/" + graphEditTarget.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: $("geNodeName").value,
            summary: $("geNodeSummary").value,
          }),
        });
        closeEditForms();
        await loadGraph();
      } catch (e) { alert("保存失败：" + e.message); }
    });
    $("geNodeDel").addEventListener("click", async () => {
      if (!graphEditTarget || graphEditTarget.type !== "node") return;
      if (!confirm("删除该节点及其全部关联边？")) return;
      try {
        await apiGraph("/api/graph/node/" + graphEditTarget.id, { method: "DELETE" });
        closeEditForms();
        await loadGraph();
      } catch (e) { alert("删除失败：" + e.message); }
    });
    $("geNodeCancel").addEventListener("click", closeEditForms);
    $("geEdgeSave").addEventListener("click", async () => {
      if (!graphEditTarget || graphEditTarget.type !== "edge") return;
      try {
        await apiGraph("/api/graph/edge/" + graphEditTarget.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ relation: $("geEdgeRel").value }),
        });
        closeEditForms();
        await loadGraph();
      } catch (e) { alert("保存失败：" + e.message); }
    });
    $("geEdgeDel").addEventListener("click", async () => {
      if (!graphEditTarget || graphEditTarget.type !== "edge") return;
      if (!confirm("删除该边？")) return;
      try {
        await apiGraph("/api/graph/edge/" + graphEditTarget.id, { method: "DELETE" });
        closeEditForms();
        await loadGraph();
      } catch (e) { alert("删除失败：" + e.message); }
    });
    $("geEdgeCancel").addEventListener("click", closeEditForms);
  }

  // ---------- 图谱导出（图片 / 交互网页） ----------

  function computeGraphLayout(nodes, W, H) {
    // 按类型分列布局（与 drawGraph 一致）
    const colW = W / Math.max(GRAPH_TYPE_ORDER.length, 1);
    const pos = {};
    const counts = {};
    for (const n of nodes) {
      const t = n.type || "?";
      counts[t] = (counts[t] || 0) + 1;
    }
    nodes.forEach((n) => {
      const t = n.type || "?";
      const idx = GRAPH_TYPE_ORDER.indexOf(t);
      const col = idx >= 0 ? idx : GRAPH_TYPE_ORDER.length;
      const colCount = counts[t] || 1;
      const i = (counts["_i_" + t] = (counts["_i_" + t] || 0));
      counts["_i_" + t] = i + 1;
      pos[n.id] = { x: colW * (col + 0.5), y: H * ((i + 0.5) / colCount) };
    });
    return pos;
  }

  function downloadFile(name, blob) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function exportGraphImage() {
    const nodes = graphData.nodes || [];
    const edges = graphData.edges || [];
    if (!nodes.length) return alert("图谱为空，无法导出");
    // 高清画布（3x 缩放）
    const W = 1600, H = Math.max(900, Math.round(900 * (nodes.length / 12)));
    const canvas = document.createElement("canvas");
    canvas.width = W * 3;
    canvas.height = H * 3;
    const ctx = canvas.getContext("2d");
    ctx.scale(3, 3);
    // 深色背景（与网页主题一致）
    ctx.fillStyle = "#1d1d1f";
    ctx.fillRect(0, 0, W, H);
    const pos = computeGraphLayout(nodes, W, H);
    // 边
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "rgba(134,134,139,0.55)";
    for (const e of edges) {
      const a = pos[e.from], b = pos[e.to];
      if (!a || !b) continue;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    // 节点
    ctx.font = "13px sans-serif";
    for (const n of nodes) {
      const p = pos[n.id];
      if (!p) continue;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 9, 0, Math.PI * 2);
      ctx.fillStyle = GRAPH_COLORS[n.type] || "#8e8e93";
      ctx.fill();
      ctx.fillStyle = "#f5f5f7";
      ctx.fillText(String(n.name || "").slice(0, 14), p.x + 12, p.y + 4);
    }
    // 标题
    ctx.font = "bold 22px sans-serif";
    ctx.fillStyle = "#f5f5f7";
    ctx.fillText("MindTrace 知识图谱", 20, 34);
    ctx.font = "12px sans-serif";
    ctx.fillStyle = "#86868b";
    ctx.fillText(nodes.length + " 节点 · " + edges.length + " 边 · " + new Date().toLocaleString(), 20, 56);
    canvas.toBlob((blob) => {
      if (blob) downloadFile("mindtrace-graph.png", blob);
    }, "image/png");
  }

  function exportGraphHtml() {
    const nodes = graphData.nodes || [];
    const edges = graphData.edges || [];
    if (!nodes.length) return alert("图谱为空，无法导出");
    const colors = JSON.stringify(GRAPH_COLORS);
    const data = JSON.stringify({ nodes, edges });
    // 自包含交互页面：缩放(滚轮) / 拖拽平移 / 点击节点高亮+详情
    const html = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>MindTrace 知识图谱</title>
<style>
  body{margin:0;background:#1d1d1f;color:#f5f5f7;font:13px -apple-system,"Segoe UI",sans-serif;overflow:hidden}
  #wrap{position:fixed;inset:0}
  canvas{display:block;background:#1d1d1f}
  #tip{position:fixed;left:14px;top:12px;opacity:.7;pointer-events:none}
  #info{position:fixed;right:14px;top:12px;max-width:320px;background:rgba(38,38,42,.92);
        border:1px solid #3a3a40;border-radius:10px;padding:10px 12px;display:none;font-size:12.5px}
  #info b{color:#0a84ff}
</style></head><body>
<div id="wrap"><canvas id="c"></canvas></div>
<div id="tip">滚轮缩放 · 拖拽平移 · 点击节点查看详情</div>
<div id="info"></div>
<script>
var COLORS = ${colors};
var DATA = ${data};
var canvas = document.getElementById('c'), ctx = canvas.getContext('2d');
var W = 0, H = 0;
function resize(){ W = innerWidth; H = innerHeight;
  canvas.width = W * devicePixelRatio; canvas.height = H * devicePixelRatio;
  ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0); draw(); }
addEventListener('resize', resize); resize();

// 力导向近似：类型分列 + 轻微抖动
var pos = {}, counts = {};
var order = Object.keys(COLORS);
DATA.nodes.forEach(function(n){ counts[n.type||'?'] = (counts[n.type||'?']||0)+1; });
var colW = Math.max(W, 1200) / order.length;
DATA.nodes.forEach(function(n, i){
  var t = n.type||'?'; var col = order.indexOf(t); if(col<0) col = order.length;
  var idx = (counts['_'+t] = (counts['_'+t]||0)); counts['_'+t] = idx+1;
  pos[n.id] = { x: colW*(col+0.5) + (Math.random()-0.5)*40,
                y: (idx+0.5)/counts[t]*Math.max(H,700) + (Math.random()-0.5)*40,
                name: n.name, type: t, summary: n.summary||'' };
});
var zoom = 1, ox = 0, oy = 0, drag = null, sel = null;
function tx(x){ return x*zoom + ox; }
function ty(y){ return y*zoom + oy; }
function draw(){
  ctx.clearRect(0,0,W,H);
  DATA.edges.forEach(function(e){
    var a=pos[e.from], b=pos[e.to]; if(!a||!b) return;
    ctx.strokeStyle = sel && (sel.id===e.from||sel.id===e.to) ? 'rgba(10,132,255,.9)' : 'rgba(134,134,139,.4)';
    ctx.lineWidth = sel && (sel.id===e.from||sel.id===e.to) ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(tx(a.x),ty(a.y)); ctx.lineTo(tx(b.x),ty(b.y)); ctx.stroke();
  });
  DATA.nodes.forEach(function(n){
    var p=pos[n.id]; if(!p) return;
    var x=tx(p.x), y=ty(p.y);
    ctx.beginPath(); ctx.arc(x,y, sel&&sel.id===n.id ? 10 : 7, 0, 7);
    ctx.fillStyle = COLORS[p.type]||'#8e8e93'; ctx.fill();
    if(sel&&sel.id===n.id){ ctx.strokeStyle='#fff'; ctx.lineWidth=2; ctx.stroke(); }
    ctx.fillStyle='#f5f5f7'; ctx.font='12px sans-serif';
    ctx.fillText(String(p.name||'').slice(0,10), x+10, y+4);
  });
}
canvas.addEventListener('wheel', function(ev){
  ev.preventDefault();
  var f = ev.deltaY < 0 ? 1.1 : 0.9;
  var mx = ev.clientX, my = ev.clientY;
  var wx = (mx-ox)/zoom, wy = (my-oy)/zoom;
  zoom = Math.min(4, Math.max(0.3, zoom*f));
  ox = mx - wx*zoom; oy = my - wy*zoom;
  draw();
}, {passive:false});
canvas.addEventListener('mousedown', function(ev){
  var hit = null;
  DATA.nodes.forEach(function(n){
    var p=pos[n.id]; if(!p) return;
    var dx=tx(p.x)-ev.clientX, dy=ty(p.y)-ev.clientY;
    if(dx*dx+dy*dy < 144) hit = n;
  });
  if(hit){
    sel = hit;
    var info = document.getElementById('info');
    info.style.display='block';
    info.innerHTML = '<b>['+(hit.type||'?')+']</b> '+esc(hit.name||'')+
      (hit.summary ? '<br>'+esc(hit.summary) : '');
    draw();
  } else { drag = {x:ev.clientX, y:ev.clientY}; }
});
function esc(s){ return String(s||'').replace(/[&<>"]/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
addEventListener('mousemove', function(ev){
  if(!drag) return;
  ox += ev.clientX - drag.x; oy += ev.clientY - drag.y;
  drag = {x:ev.clientX, y:ev.clientY}; draw();
});
addEventListener('mouseup', function(){ drag = null; });
</script></body></html>`;
    downloadFile("mindtrace-graph.html", new Blob([html], { type: "text/html;charset=utf-8" }));
  }

  function bindGraphExport() {
    document.getElementById("graphExportImg").addEventListener("click", exportGraphImage);
    document.getElementById("graphExportHtml").addEventListener("click", exportGraphHtml);
  }

  // ---------- 维护视图 ----------

  async function loadMaintenance() {
    el.maintenanceContent.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
      const resp = await fetch("/api/maintenance");
      const data = await resp.json();
      if (!data.enabled) {
        el.maintenanceContent.innerHTML = '<div class="empty-state">巩固服务未启用。</div>';
        return;
      }
      const counts = data.counts || {};
      const caps = data.caps || {};
      const ret = data.retention || {};
      const items = [
        ["短期日志", counts.memories_short, caps.memories_short],
        ["事件", counts.events, caps.events],
        ["经验", counts.experiences, caps.experiences],
        ["回收站", counts.trash, null],
      ];
      let html = '<div class="mnt-stats">';
      for (const [label, value, cap] of items) {
        html += '<div class="mnt-card"><div class="m-label">' + label + "</div>" +
          '<div class="m-value">' + (value || 0) + "</div>" +
          (cap ? '<div class="m-cap">上限 ' + cap + "</div>" : "") + "</div>";
      }
      html += "</div>";
      html +=
        '<div class="mnt-section"><h3>保留策略</h3>' +
        '<div class="mnt-card" style="margin-bottom:0"><div class="m-label">短期日志 ' + ret.short_hours + " 小时后降级 · 事件 " +
        ret.events_days + " 天后降级 · 回收站保留 " + ret.trash_days + " 天</div></div></div>";
      html +=
        '<div class="mnt-section"><h3>手动触发</h3>' +
        '<div class="mnt-run"><button id="mntHourly">运行每小时巩固</button>' +
        '<button id="mntEpisodic">运行情景记忆</button>' +
        '<button id="mntDaily">运行每日巩固</button></div>' +
        '<div id="mntResult" class="mnt-result"></div></div>';
      html += '<div class="mnt-section"><h3>最近巩固运行记录</h3><div id="mntRuns"></div></div>';
      html += '<div class="mnt-section"><h3>回收站（30 天内可恢复）</h3><div id="trashList"></div></div>';
      el.maintenanceContent.innerHTML = html;

      document.getElementById("mntHourly").addEventListener("click", () => runMaintenance("hourly"));
      document.getElementById("mntEpisodic").addEventListener("click", () => runMaintenance("episodic"));
      document.getElementById("mntDaily").addEventListener("click", () => runMaintenance("daily"));
      const runs = data.runs || [];
      const runsBox = document.getElementById("mntRuns");
      runsBox.innerHTML = runs.length ? runs.map((r) =>
        '<div class="trash-item"><div class="trash-text">[' + esc(r.job) + "] " +
        esc(JSON.stringify(r.stats || {})) + '</div>' +
        '<div class="trash-meta">' + esc((r.run_at || "").slice(0, 16)) + "</div></div>"
      ).join("") : '<div class="mnt-result">暂无运行记录</div>';
      loadTrash();
    } catch (e) {
      el.maintenanceContent.innerHTML = '<div class="empty-state">加载失败：' + esc(e.message) + "</div>";
    }
  }

  async function loadTrash() {
    const box = document.getElementById("trashList");
    if (!box) return;
    try {
      const resp = await fetch("/api/trash");
      const data = await resp.json();
      const items = data.trash || [];
      if (!items.length) { box.innerHTML = '<div class="mnt-result">回收站是空的</div>'; return; }
      box.innerHTML = "";
      for (const t of items) {
        const p = t.payload || {};
        const row = document.createElement("div");
        row.className = "trash-item";
        row.innerHTML =
          '<div class="trash-text">[' + esc(t.target_type) + '] ' + esc(p.text || "") + "</div>" +
          '<div class="trash-meta">' + esc(t.expires_at || "") + '</div>' +
          '<div class="trash-actions"><button data-id="' + t.id + '">恢复</button></div>';
        row.querySelector("button").addEventListener("click", async () => {
          const r = await fetch("/api/trash/" + t.id + "/restore", { method: "POST" });
          alert(r.ok ? "已恢复" : "恢复失败");
          loadTrash();
        });
        box.appendChild(row);
      }
    } catch (e) {
      box.innerHTML = '<div class="mnt-result">加载失败</div>';
    }
  }

  async function runMaintenance(job) {
    const resultEl = document.getElementById("mntResult");
    resultEl.textContent = "运行中…";
    try {
      const resp = await fetch("/api/maintenance/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job }),
      });
      const data = await resp.json();
      resultEl.textContent = JSON.stringify(data.stats || data, null, 2);
    } catch (e) {
      resultEl.textContent = "失败：" + e.message;
    }
  }

  // ---------- 设置视图 ----------

  async function loadSettings() {
    el.settingsContent.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
      const [mon, col, qh] = await Promise.all([
        fetch("/api/monitor").then(r => r.json()),
        fetch("/api/collector/settings").then(r => r.json()),
        fetch("/api/quiet-hours").then(r => r.json()),
      ]);
      const checked = (v) => v ? "checked" : "";
      el.settingsContent.innerHTML =
        '<div class="mnt-section"><h3>🎯 监控目标（文字/截图只对该目标操作；文件/Git 信号始终全局）</h3>' +
        '<div class="set-group">' +
        '<label><input type="radio" name="monMode" value="follow"' + (mon.mode === "follow" ? " checked" : "") + '> 跟随前台窗口（默认）</label><br>' +
        '<label><input type="radio" name="monMode" value="locked"' + (mon.mode === "locked" ? " checked" : "") + '> 锁定单个窗口</label><br>' +
        '<label><input type="radio" name="monMode" value="off"' + (mon.mode === "off" ? " checked" : "") + '> 关闭窗口采集</label>' +
        '</div>' +
        '<div id="lockPick" class="set-lock"></div>' +
        '<div class="set-actions"><button id="monSave">保存监控目标</button><button id="monRefresh">刷新窗口列表</button></div></div>' +

        '<div class="mnt-section"><h3>🔓 采集授权（默认关）</h3>' +
        '<div class="set-group">' +
        '<label><input type="checkbox" id="colClip"' + checked(col.clipboard) + '> 剪贴板内容</label><br>' +
        '<label><input type="checkbox" id="colBrowser"' + checked(col.browser_history) + '> 浏览器历史</label><br>' +
        '<label><input type="checkbox" id="colGit"' + checked(col.git) + '> Git 提交记录</label>' +
        '</div>' +
        '<div class="set-actions"><button id="colSave">保存采集开关</button></div></div>' +

        '<div class="mnt-section"><h3>🌙 免打扰时段（系统弹窗静默）</h3>' +
        '<div class="set-group">' +
        '<label>从 <input type="time" id="quietStart" value="' + esc(qh.start || "23:00") + '"> 到 ' +
        '<input type="time" id="quietEnd" value="' + esc(qh.end || "07:00") + '"></label>' +
        '</div>' +
        '<div class="set-actions"><button id="quietSave">保存免打扰</button></div></div>' +

        '<div class="mnt-section"><h3>🗑 记忆管理</h3>' +
        '<div class="set-actions"><button id="clearMemory" class="danger-btn">一键清空记忆（事件/经验/向量）</button></div>' +
        '<div id="clearResult" class="mnt-result"></div></div>' +

        '<div class="mnt-section"><h3>⚖️ 重要性评分权重（w1-w6）</h3>' +
        '<div class="set-group" id="weightsBox">加载中…</div>' +
        '<div class="set-actions"><button id="weightsSave">保存权重</button></div></div>';

      await renderLockPicker(mon.mode, mon.pattern || "");
      loadWeights();

      document.querySelectorAll('input[name="monMode"]').forEach(r => {
        r.addEventListener("change", () => renderLockPicker(r.value, ""));
      });
      document.getElementById("monSave").addEventListener("click", async () => {
        const mode = document.querySelector('input[name="monMode"]:checked').value;
        const sel = document.querySelector('select[name="lockWindow"]');
        const pattern = sel ? sel.value : "";
        const resp = await fetch("/api/monitor", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, pattern }),
        });
        alert(resp.ok ? "监控目标已保存" : "保存失败");
      });
      document.getElementById("monRefresh").addEventListener("click", () => renderLockPicker(
        document.querySelector('input[name="monMode"]:checked').value, ""
      ));
      document.getElementById("colSave").addEventListener("click", async () => {
        await fetch("/api/collector/settings", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            clipboard: document.getElementById("colClip").checked,
            browser_history: document.getElementById("colBrowser").checked,
            git: document.getElementById("colGit").checked,
          }),
        });
        alert("采集开关已保存（立即生效）");
      });
      document.getElementById("quietSave").addEventListener("click", async () => {
        await fetch("/api/quiet-hours", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start: document.getElementById("quietStart").value,
            end: document.getElementById("quietEnd").value,
          }),
        });
        alert("免打扰时段已保存");
      });
      document.getElementById("clearMemory").addEventListener("click", async () => {
        if (!confirm("确定清空全部记忆（事件/经验/短期日志/回收站/向量）？此操作不可恢复！")) return;
        const resp = await fetch("/api/settings/clear-memory", { method: "POST" });
        document.getElementById("clearResult").textContent = resp.ok ? "已清空" : "清空失败";
      });
      document.getElementById("weightsSave").addEventListener("click", async () => {
        const w = {};
        for (let i = 1; i <= 6; i++) {
          const el = document.getElementById("w" + i);
          w["w" + i] = el ? parseFloat(el.value) : 0.1;
        }
        await fetch("/api/scoring/weights", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ weights: w }),
        });
        alert("重要性权重已保存");
      });
    } catch (e) {
      el.settingsContent.innerHTML = '<div class="empty-state">加载失败：' + esc(e.message) + "</div>";
    }
  }

  async function renderLockPicker(mode, currentPattern) {
    const box = document.getElementById("lockPick");
    if (mode !== "locked") { box.innerHTML = ""; return; }
    box.innerHTML = '<div class="set-group"><label>选择要锁定的窗口：</label><select name="lockWindow" class="set-select">' +
      '<option value="">— 选择窗口 —</option></select></div>';
    try {
      const resp = await fetch("/api/monitor/windows");
      const data = await resp.json();
      const sel = box.querySelector('select[name="lockWindow"]');
      for (const w of (data.windows || [])) {
        const opt = document.createElement("option");
        opt.value = w.title;
        opt.textContent = (w.app ? w.app + " — " : "") + w.title;
        if (w.title === currentPattern) opt.selected = true;
        sel.appendChild(opt);
      }
    } catch (e) { /* 忽略 */ }
  }

  async function loadWeights() {
    const box = document.getElementById("weightsBox");
    if (!box) return;
    try {
      const resp = await fetch("/api/scoring/weights");
      const data = await resp.json();
      const w = data.weights || {};
      const labels = ["任务相关性", "同类频率", "新异性", "用户信号", "时间衰减×基准", "引用加成"];
      box.innerHTML = labels.map((l, i) => {
        const k = "w" + (i + 1);
        return '<label>' + l + ' <input type="number" id="' + k + '" value="' + (w[k] ?? 0.15) +
          '" min="0" max="1" step="0.05" style="width:70px"></label><br>';
      }).join("");
    } catch (e) { box.innerHTML = "加载失败"; }
  }

  // ---------- 通知中心 ----------

  async function refreshNotifBadge() {
    try {
      const resp = await fetch("/api/notifications/count");
      const data = await resp.json();
      const badge = document.getElementById("notifBadge");
      const count = data.pending || 0;
      if (count > 0) {
        badge.textContent = count > 99 ? "99+" : String(count);
        badge.classList.remove("hidden");
      } else {
        badge.classList.add("hidden");
      }
    } catch (e) { /* 忽略 */ }
  }

  async function toggleNotifPanel() {
    const panel = document.getElementById("notifPanel");
    if (!panel.classList.contains("hidden")) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    panel.innerHTML = '<div class="notif-empty">加载中…</div>';
    try {
      const resp = await fetch("/api/notifications");
      const data = await resp.json();
      const items = data.notifications || [];
      if (!items.length) {
        panel.innerHTML = '<div class="notif-empty">没有待处理通知</div>';
        return;
      }
      panel.innerHTML = "";
      for (const n of items) {
        const item = document.createElement("div");
        item.className = "notif-item";
        let actionsHtml = "";
        if (n.kind === "recall_reminder") {
          actionsHtml = '<div class="n-actions"><button data-act="useful">👍 有用</button>' +
            '<button data-act="useless">👎 无关</button></div>';
        } else if (n.kind === "breakthrough" || n.kind === "pending_experience" || n.kind === "daily_candidate") {
          actionsHtml = '<div class="n-actions"><button data-act="confirm">保留</button>' +
            '<button data-act="keep">仅记录</button>' +
            '<button data-act="discard">丢弃</button></div>';
        } else {
          actionsHtml = '<div class="n-actions"><button data-act="done">知道了</button>' +
            '<button data-act="dismiss">忽略</button></div>';
        }
        item.innerHTML =
          '<div class="n-title">' + esc(n.title) + "</div>" +
          (n.body ? '<div class="n-body">' + esc(n.body) + "</div>" : "") +
          actionsHtml;
        for (const btn of item.querySelectorAll("button")) {
          const act = btn.dataset.act;
          btn.addEventListener("click", () => notifAction(n, act));
        }
        panel.appendChild(item);
      }
    } catch (e) {
      panel.innerHTML = '<div class="notif-empty">加载失败</div>';
    }
  }

  async function notifAction(n, act) {
    try {
      let url = null, body = null;
      if (act === "useful" || act === "useless") {
        url = "/api/notifications/" + n.id + "/feedback";
        body = { useful: act === "useful" };
      } else if (act === "confirm" || act === "keep" || act === "discard") {
        url = "/api/notifications/" + n.id + "/experience-action";
        body = { action: act };
      } else {
        url = "/api/notifications/" + n.id + "/resolve";
        body = { status: act === "dismiss" ? "dismissed" : "done" };
      }
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await refreshNotifBadge();
      toggleNotifPanel();
      toggleNotifPanel(); // 重新加载
    } catch (e) { /* 忽略 */ }
  }

  async function resolveNotif(id, status) {
    try {
      await fetch("/api/notifications/" + id + "/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await refreshNotifBadge();
      toggleNotifPanel();
      toggleNotifPanel(); // 重新加载
    } catch (e) { /* 忽略 */ }
  }

  // ---------- 工具 ----------

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---------- 初始化 ----------

  el.send.addEventListener("click", send);
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  el.input.addEventListener("input", () => {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 160) + "px";
  });
  el.newChat.addEventListener("click", () => createSession(true));
  document.getElementById("notifBtn").addEventListener("click", toggleNotifPanel);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".notif-wrap")) {
      document.getElementById("notifPanel").classList.add("hidden");
    }
  });
  setInterval(refreshNotifBadge, 20000);
  refreshNotifBadge();
  // 任务视图自动刷新
  setInterval(() => {
    if (document.getElementById("view-tasks").classList.contains("active")) loadTasks();
  }, 30000);

  (async function init() {
    bindGraphEdit();
    bindGraphExport();
    await loadHealth();
    await refreshSessions();
    if (!sessions.length) {
      const s = await createSession(false);
      if (s) await switchSession(s.client_id);
    } else if (sessions.some((s) => s.client_id === activeClientId)) {
      await switchSession(activeClientId);
    } else {
      await switchSession(sessions[0].client_id);
    }
  })();
})();
