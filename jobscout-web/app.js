// JobScout standalone frontend — talks directly to the DeerFlow Gateway API.
// No build step: plain fetch + manual SSE parsing against the documented
// Gateway shapes (login/CSRF, threads, uploads, runs/stream).

const GATEWAY_BASE = "http://localhost:8001";

// ---------------------------------------------------------------- helpers --

function $(id) { return document.getElementById(id); }

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

async function api(path, { method = "GET", json, form, headers = {} } = {}) {
  const opts = { method, credentials: "include", headers: { ...headers } };

  if (json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  } else if (form !== undefined) {
    opts.body = form; // FormData or URLSearchParams — browser sets Content-Type
  }

  if (method !== "GET" && method !== "HEAD") {
    const csrf = getCookie("csrf_token");
    if (csrf) opts.headers["X-CSRF-Token"] = csrf;
  }

  const res = await fetch(GATEWAY_BASE + path, opts);
  return res;
}

async function apiJson(path, opts) {
  const res = await api(path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const msg = body?.detail?.message || body?.detail || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

function show(view) {
  for (const v of ["authView", "chatView"]) {
    $(v).classList.toggle("hidden", v !== view);
  }
}

// ------------------------------------------------------------------ auth --

let currentUserEmail = null;

async function checkSession() {
  const res = await api("/api/v1/auth/me");
  if (res.ok) {
    const me = await res.json().catch(() => null);
    currentUserEmail = me?.email || me?.id || "已登录";
    onLoggedIn();
    return true;
  }
  show("authView");
  return false;
}

const WELCOME_MESSAGE =
  "你好,我是 JobScout —— 面向中国大陆求职者的面试准备助手。\n\n" +
  "告诉我你想准备**哪家公司**、**什么岗位**、**校招 / 社招 / 实习**,可以贴 JD 原文或链接,也可以点 " +
  "左下角回形针上传简历(用于差距分析)。一句话触发,我会自动跑完整套调研:\n\n" +
  "- **公司速览** —— 业务、产品、近期新闻、融资情况\n" +
  "- **岗位拆解** —— 解析 JD,提取必备/加分技能与核心职责,核对公司真实技术栈\n" +
  "- **面试题预测** —— 该公司/岗位高频面试题,技术题 + 行为题,均带来源\n" +
  "- **差距分析** —— 拿你的简历对比 JD,指出优势、短板,给 3-5 条具体准备建议\n\n" +
  "每条事实都带来源链接,查不到就如实说明,绝不编造。";

let hasShownWelcome = false;

function onLoggedIn() {
  $("userBox").classList.remove("hidden");
  $("userEmail").textContent = currentUserEmail;
  show("chatView");
  if (!hasShownWelcome) {
    addChatBubble("assistant", WELCOME_MESSAGE);
    hasShownWelcome = true;
  }
  loadThreadList();
}

let authMode = "login"; // 'login' | 'register'

function setupAuthForm() {
  $("authToggle").addEventListener("click", () => {
    authMode = authMode === "login" ? "register" : "login";
    $("authSubmit").textContent = authMode === "login" ? "登录" : "注册";
    $("authToggle").textContent = authMode === "login" ? "还没有账号?注册一个" : "已有账号?去登录";
    $("authPasswordHint").classList.toggle("hidden", authMode !== "register");
    $("authPassword").setAttribute("autocomplete", authMode === "login" ? "current-password" : "new-password");
  });

  $("authForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("authError").classList.add("hidden");
    $("authSubmit").disabled = true;
    const email = $("authEmail").value.trim();
    const password = $("authPassword").value;
    try {
      if (authMode === "login") {
        const form = new URLSearchParams();
        form.set("username", email);
        form.set("password", password);
        const res = await api("/api/v1/auth/login/local", { method: "POST", form });
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail?.message || "登录失败,请检查邮箱/密码");
        }
      } else {
        await apiJson("/api/v1/auth/register", { method: "POST", json: { email, password } });
      }
      currentUserEmail = email;
      onLoggedIn();
    } catch (err) {
      $("authError").textContent = err.message || String(err);
      $("authError").classList.remove("hidden");
    } finally {
      $("authSubmit").disabled = false;
    }
  });

  $("logoutBtn").addEventListener("click", async () => {
    await api("/api/v1/auth/logout", { method: "POST" }).catch(() => {});
    currentUserEmail = null;
    $("userBox").classList.add("hidden");
    show("authView");
  });
}

// ------------------------------------------------------------ constants --

// A slash-activated skill gets its SKILL.md force-injected for that turn,
// which is the only reliable trigger path found in testing (autonomous
// discovery — the model deciding on its own to read_file the skill — was
// not deterministic). Every outgoing message is prefixed so the
// skill stays active turn over turn, not just on the first message.
const SKILL_PREFIX = "/jobscout";

function withSkillPrefix(text) {
  const t = (text || "").trim();
  if (t.startsWith(SKILL_PREFIX)) return t;
  return `${SKILL_PREFIX}\n${t}`;
}

let activeThreadId = null;
let chatStartTime = null;
let chatTimerHandle = null;
let pendingFile = null;

// Composer message history (up-arrow recall, shell-style). Populated both by
// messages sent live in this session and by replaying a thread's past human
// messages (see renderHistoryMessages) — oldest first, most recent last.
let sentHistory = [];
let historyIndex = -1; // -1 = not currently navigating history
let historyDraft = ""; // whatever the user had typed before they started navigating

// ------------------------------------------------------------- chat ui --

function resetChat() {
  activeThreadId = null;
  clearAttachment();
  $("chatMessages").innerHTML = "";
  $("composerInput").value = "";
  sentHistory = [];
  historyIndex = -1;
  historyDraft = "";
  autoGrowComposer();
  addChatBubble("assistant", WELCOME_MESSAGE);
}

function scrollChatToBottom() {
  const el = $("chatMessages");
  el.scrollTop = el.scrollHeight;
}

/** Render a chat bubble. `text` is treated as inline-markdown (bold/links),
 *  not a full document — this is the conversational layer, not the report. */
function addChatBubble(role, text) {
  const wrap = document.createElement("div");
  wrap.className = `bubble-row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.innerHTML = inlineMd(text).replace(/\n/g, "<br/>");
  wrap.appendChild(bubble);
  $("chatMessages").appendChild(wrap);
  scrollChatToBottom();
  return bubble;
}

function showThinking() {
  hideThinking();
  const wrap = document.createElement("div");
  wrap.className = "bubble-row assistant";
  wrap.id = "thinkingBubbleRow";
  wrap.innerHTML = '<div class="bubble assistant thinking"><span></span><span></span><span></span></div>';
  $("chatMessages").appendChild(wrap);
  scrollChatToBottom();
}
function hideThinking() { $("thinkingBubbleRow")?.remove(); }

// Real work here (company research + JD parsing + interview-question search,
// each possibly running several web_search/web_fetch rounds across 3 parallel
// subagents) genuinely takes a while — a minute or more is expected, not a
// bug. We deliberately don't stream raw tool/reasoning noise into the UI
// (that noise includes retry/loop-detection text that would look broken),
// but we DO surface which *tool* the lead agent is currently
// calling — a clean, honest signal, not fake progress. `currentPhaseLabel`
// is updated by streamRunToText's onProgress callback each time a fresh
// `values` snapshot arrives; the timer falls back to a generic rotating
// hint whenever we haven't seen a recognizable tool call yet.
const TOOL_PHASE_LABELS = {
  task: "正在委派子任务并行调研公司/岗位/面试题",
  web_search: "正在搜索",
  web_fetch: "正在抓取网页内容确认细节",
  read_file: "正在阅读文件",
  grep: "正在检索文件内容",
  glob: "正在查找文件",
};
const THINKING_HINTS = [
  "生成中...",
  "生成中... 正在核实公司与岗位信息",
  "生成中... 正在检索面试题与面经,可能要一点时间",
  "生成中... 信息比较多,感谢耐心等待",
];

let currentPhaseLabel = null;

/** Called on every `values` SSE frame (not just the final one) so the chat
 *  status line can show which tool is actively running, e.g. "正在搜索"
 *  instead of a generic spinner caption. Best-effort: falls back silently
 *  if the message shape doesn't have a recognizable tool call. */
function updatePhaseFromMessages(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m?.additional_kwargs?.hide_from_ui) continue;
    if (m?.type === "ai" && Array.isArray(m.tool_calls) && m.tool_calls.length) {
      const names = [...new Set(m.tool_calls.map((c) => c?.name).filter(Boolean))];
      const label = names.map((n) => TOOL_PHASE_LABELS[n]).find(Boolean);
      if (label) currentPhaseLabel = label;
      return;
    }
    if (m?.type === "ai" || m?.type === "tool") return; // reached a non-tool-call turn, stop looking
  }
}

function startChatTimer() {
  chatStartTime = Date.now();
  currentPhaseLabel = null;
  $("chatStatus").textContent = THINKING_HINTS[0];
  clearInterval(chatTimerHandle);
  chatTimerHandle = setInterval(() => {
    const elapsed = Math.round((Date.now() - chatStartTime) / 1000);
    let label = currentPhaseLabel;
    if (!label) {
      const hintIndex = elapsed >= 45 ? 3 : elapsed >= 20 ? 2 : elapsed >= 6 ? 1 : 0;
      label = THINKING_HINTS[hintIndex];
    }
    $("chatStatus").textContent = `${label}(已用时 ${elapsed}s)`;
  }, 1000);
}
function stopChatTimer(label) {
  clearInterval(chatTimerHandle);
  if (label !== undefined) $("chatStatus").textContent = label;
}

// ------------------------------------------------------------ composer --
// Chat-first UI: no upfront form. Company/role/jobType are collected by the
// agent itself (SKILL.md's ask_clarification loop) from whatever the user
// types. Resume attachment is a one-shot-per-message affair, like a normal
// chat app's paperclip button, not a pre-submission form field.

function clearAttachment() {
  pendingFile = null;
  $("composerFile").value = "";
  $("attachedChip").classList.add("hidden");
  $("attachedChipText").textContent = "";
}

/** Shared by both the paperclip file picker and drag-and-drop onto the chat
 *  card — one file attaches at a time, same as a normal chat app. */
function setPendingFile(file) {
  pendingFile = file;
  $("attachedChipText").textContent = `📄 ${file.name}`;
  $("attachedChip").classList.remove("hidden");
}

/** Drag-and-drop a file anywhere onto the chat card (messages or composer)
 *  attaches it, same as clicking the paperclip. Uses a counter, not a single
 *  dragenter/dragleave pair, because dragleave also fires when the pointer
 *  crosses into a child element — a naive show-on-enter/hide-on-leave toggle
 *  flickers constantly as the cursor moves over child nodes. */
let dragDepth = 0;

function setupDragDrop() {
  const card = $("chatCard");
  const overlay = $("dropOverlay");
  if (!card || !overlay) return;

  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");

  card.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    overlay.classList.remove("hidden");
  });
  card.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); // required, or the browser refuses the drop
  });
  card.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) overlay.classList.add("hidden");
  });
  card.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    overlay.classList.add("hidden");
    const file = e.dataTransfer?.files?.[0];
    if (file) setPendingFile(file);
    $("composerInput").focus();
  });
}

function autoGrowComposer() {
  const el = $("composerInput");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

/** Shell-style history recall on ArrowUp/ArrowDown, like a terminal — not
 *  tied to a specific message, just the composer's own send history.
 *  ArrowUp walks backward through sentHistory (most recent first); ArrowDown
 *  walks forward and, past the newest entry, restores whatever the user had
 *  typed before they started navigating (their in-progress draft isn't lost). */
function recallHistory(direction) {
  if (!sentHistory.length) return;
  const input = $("composerInput");
  if (direction === "up") {
    if (historyIndex === -1) historyDraft = input.value;
    if (historyIndex < sentHistory.length - 1) {
      historyIndex++;
      input.value = sentHistory[sentHistory.length - 1 - historyIndex];
      autoGrowComposer();
    }
  } else {
    if (historyIndex === -1) return;
    historyIndex--;
    input.value = historyIndex === -1 ? historyDraft : sentHistory[sentHistory.length - 1 - historyIndex];
    autoGrowComposer();
  }
  // Cursor to the end — recalling a message to re-edit its tail, not its head.
  requestAnimationFrame(() => input.setSelectionRange(input.value.length, input.value.length));
}

function setComposerBusy(busy) {
  $("composerInput").disabled = busy;
  $("composerSend").disabled = busy;
  $("attachBtn").disabled = busy;
}

function setupComposer() {
  $("attachBtn").addEventListener("click", () => $("composerFile").click());

  $("composerFile").addEventListener("change", () => {
    const file = $("composerFile").files[0];
    if (!file) return;
    setPendingFile(file);
  });

  $("attachedChipRemove").addEventListener("click", () => clearAttachment());
  setupDragDrop();

  const input = $("composerInput");
  input.addEventListener("input", () => {
    historyIndex = -1; // real typing always exits history-recall mode
    autoGrowComposer();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("composerForm").requestSubmit();
      return;
    }
    if (e.key === "ArrowUp" && (historyIndex !== -1 || input.value === "")) {
      e.preventDefault();
      recallHistory("up");
      return;
    }
    if (e.key === "ArrowDown" && historyIndex !== -1) {
      e.preventDefault();
      recallHistory("down");
    }
  });

  $("composerForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    const file = pendingFile;
    if (!text && !file) return;

    if (text) {
      sentHistory.push(text);
      historyIndex = -1;
      historyDraft = "";
    }
    input.value = "";
    autoGrowComposer();
    setComposerBusy(true);

    let displayText = text || "(已上传简历,请查看并纳入分析)";
    if (file) displayText += `\n\n📄 已附加文件:${file.name}`;
    addChatBubble("user", displayText);
    if (file) clearAttachment();

    try {
      if (!activeThreadId) {
        const thread = await apiJson("/api/threads", { method: "POST", json: { metadata: {} } });
        activeThreadId = thread.thread_id;
      }

      // DeerFlow's UploadsMiddleware does NOT scan the thread's upload folder
      // for "new" files on its own — it only looks at additional_kwargs.files
      // on the *current* human message. We must carry the upload response's
      // filename/size
      // through to the run request ourselves, or the agent never learns the
      // resume exists and silently skips the gap-analysis section.
      let uploadedFilesMeta = null;
      if (file) {
        const form = new FormData();
        form.append("files", file);
        const res = await api(`/api/threads/${activeThreadId}/uploads`, { method: "POST", form });
        if (!res.ok) throw new Error("简历上传失败,请重试或换个文件格式");
        const uploadResult = await res.json();
        // `read_file` hard-rejects binary formats (.pdf/.docx/.xlsx/...) with
        // a UnicodeDecodeError — see sandbox/tools.py. DeerFlow's upload
        // pipeline auto-converts those to a sibling <name>.md via markitdown
        // and reports it as `markdown_file`; point the agent at THAT file,
        // not the original, or its first read_file call on a PDF resume
        // fails outright and it has to guess its way to the .md version.
        // Plain-text formats (.txt/.md) have no markdown_file and are used
        // as-is.
        uploadedFilesMeta = (uploadResult.files || []).map((f) => ({
          filename: f.markdown_file || f.filename,
          size: f.size,
          status: "uploaded",
        }));
      }

      const message = withSkillPrefix(text || "已上传简历,请查看并纳入差距分析。");
      await runTurn(message, uploadedFilesMeta);
    } catch (err) {
      addChatBubble("assistant", "⚠️ " + (err.message || String(err)));
      stopChatTimer("出错了,可以重新发一次");
    } finally {
      setComposerBusy(false);
      input.focus();
    }
  });
}

// --------------------------------------------------------- run + stream --

/** POST a message on the active thread and stream the run to completion.
 *  Returns the last visible assistant-authored text (string).
 *  `filesMeta` (optional): [{filename, size, status}] for files uploaded
 *  immediately before this turn — see the additional_kwargs.files note above.
 *  `onProgress` (optional): called with the messages array on every `values`
 *  frame, not just the final one, so the caller can show live tool-call phase. */
async function streamRunToText(threadId, messageText, filesMeta, onProgress) {
  // Mirrors DeerFlow's own frontend wire format (type/content-blocks, not the
  // simplified role/content string form) so additional_kwargs reliably
  // survives to UploadsMiddleware server-side.
  const humanMessage = {
    type: "human",
    content: [{ type: "text", text: messageText }],
    additional_kwargs: filesMeta && filesMeta.length ? { files: filesMeta } : {},
  };

  const res = await api(`/api/threads/${threadId}/runs/stream`, {
    method: "POST",
    headers: { Accept: "text/event-stream" },
    json: {
      input: { messages: [humanMessage] },
      // `subagent_enabled` defaults to false server-side (deerflow.agents.
      // lead_agent.agent: `cfg.get("subagent_enabled", False)`) — the caller
      // must opt in explicitly, or the `task()` tool (SKILL.md Step 2's
      // three-way parallel research delegation) is never actually registered
      // on the model's toolset at all. This was missing here the whole time
      // (2026-09-19 root-cause investigation): the model wasn't disobeying
      // SKILL.md's "call task() before writing a report" rule, it genuinely
      // never had the tool — every prior "asks how to proceed" / "writes an
      // uncited generic answer" failure traces back to this one missing flag,
      // not to prompt wording or reasoning_effort.
      //
      // reasoning_effort: "low" was the original default (trading reasoning
      // depth for latency); bumped to "medium" while root-causing the above —
      // keeping it, since a real 3-way delegation + synthesis benefits from
      // more reasoning than a single-shot reply did. Revisit if latency/cost
      // becomes the bigger problem. Requires models.gpt-5.
      // supports_reasoning_effort: true in config.yaml or the Gateway
      // silently strips the field.
      //
      // `recursion_limit` is LangGraph's step budget for the whole run. The
      // server default (100) is far too low for this workflow — one real run
      // (6 lead web_searches + 3 task() delegations + result polling) died at
      // step 100 with GraphRecursionError before the report was assembled.
      // 1000 matches what DeerFlow's own frontend sends
      // (frontend/src/core/threads/hooks.ts). It sits at config.recursion_limit,
      // a sibling of `configurable`, not inside it.
      config: {
        recursion_limit: 1000,
        configurable: { reasoning_effort: "medium", subagent_enabled: true },
      },
      stream_mode: ["values"],
    },
  });

  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail?.message || `请求失败 (HTTP ${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let lastMessages = [];

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = rawEvent.split("\n").find((l) => l.startsWith("data:"));
      const eventLine = rawEvent.split("\n").find((l) => l.startsWith("event:"));
      if (!dataLine) continue;
      const eventName = eventLine ? eventLine.slice(6).trim() : "message";
      let data;
      try { data = JSON.parse(dataLine.slice(5).trim()); } catch (_) { continue; }

      if (eventName === "values" && Array.isArray(data.messages)) {
        lastMessages = data.messages;
        onProgress?.(data.messages);
      }
    }
  }

  return extractLastVisibleAiText(lastMessages);
}

function contentToText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => (typeof part === "string" ? part : part?.text || ""))
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function extractLastVisibleAiText(messages) {
  // `ask_clarification` is `return_direct=True`: LangGraph ends the run with
  // its ToolMessage (type "tool") as the final state, and the model's own
  // preceding AIMessage carries the tool *call*, not the question text the
  // user is meant to see. Only checking type "ai" here silently swallowed
  // every clarification question — a real bug found via a live user report,
  // not code review (Gateway logs proved the server side worked fine; the
  // Gateway's actual response just wasn't the shape this function assumed).
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m?.additional_kwargs?.hide_from_ui) continue;
    if (m?.type === "ai" || m?.type === "tool") {
      const text = contentToText(m.content).trim();
      if (text) return text;
    }
  }
  return "";
}

/** A real report has these as standalone section-title lines. The preferred
 *  shape is a Markdown heading, but live models may emit a numbered outline
 *  (`1) 公司速览`, `2) 岗位拆解（...）`) while still returning the complete
 *  document. A prose mention must NOT count, or the UI renders print/download
 *  buttons for an offer to create a report rather than the report itself. */
function looksLikeReport(text) {
  const markers = ["公司速览", "岗位拆解", "面试题预测"];
  const sectionPrefix = "(?:#{1,3}\\s+|\\d+\\s*[)）.、]\\s*)?";
  const sectionSuffix = "(?:[（(][^\\r\\n]*[）)])?\\s*$";
  const hitCount = markers.filter((m) =>
    new RegExp(`^${sectionPrefix}${m}${sectionSuffix}`, "m").test(text)
  ).length;
  return hitCount >= 2;
}

/** Keep the JobScout document contract stable when a model appends generic
 *  coaching chapters of its own. This is deliberately conservative: only
 *  known top-level drift is removed, so numbered questions and preparation
 *  steps inside an allowed section are never mistaken for new chapters. */
function sanitizeJobScoutReportMarkdown(markdown) {
  const allowedSections = ["公司速览", "岗位拆解", "面试题预测", "差距分析", "证据边界与后续建议"];
  const unwantedSections = [
    "使用说明",
    "一周上岸计划",
    "上岸计划",
    "冲刺计划",
    "话术模板",
    "专项准备资料",
    "清单与打卡",
    "执行凭据",
    "工具收据",
    "sources",
  ];
  const startsWithAny = (value, candidates) => {
    const normalized = value.trim().toLowerCase();
    return candidates.some((candidate) => normalized.startsWith(candidate.toLowerCase()));
  };

  let keep = true;
  const keptLines = [];
  for (const line of String(markdown || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    const numbered = trimmed.match(/^\d+\s*[)）.、]\s*(.+)$/);
    const levelTwo = trimmed.match(/^##(?!#)\s+(.+)$/);
    const bareStop = /^(?:执行凭据(?:（工具收据）)?|工具收据|sources)$/i.test(trimmed);
    const candidate = numbered?.[1] || levelTwo?.[1] || (bareStop ? trimmed : "");

    if (candidate) {
      if (startsWithAny(candidate, allowedSections)) {
        keep = true;
      } else if (levelTwo || bareStop || startsWithAny(candidate, unwantedSections)) {
        keep = false;
      }
    }

    if (keep) keptLines.push(line);
  }

  return keptLines.join("\n").trimEnd() + "\n";
}

/** Normalize common model formatting drift for the rendered/printed view.
 *  The sanitized Markdown is also used for download. */
function normalizeReportMarkdownForRender(markdown) {
  let seenNonEmpty = false;
  return markdown
    .split(/\r?\n/)
    .map((line) => {
      const trimmed = line.trim();
      if (!trimmed) return line;

      if (!seenNonEmpty) {
        seenNonEmpty = true;
        if (!trimmed.startsWith("#") && trimmed.includes("面试准备包")) {
          return `# ${trimmed}`;
        }
      }

      const numberedSection = trimmed.match(
        /^\d+\s*[)）.、]\s*(公司速览|岗位拆解|面试题预测|差距分析)(.*)$/
      );
      if (numberedSection) {
        return `## ${numberedSection[1]}${numberedSection[2]}`;
      }

      if (
        !trimmed.startsWith("#") &&
        /^(?:业务与产品|近期动态|融资\s*[\/／]\s*规模(?:（.*）)?|公司技术栈核对(?:（.*）)?|技术\s*[\/／]\s*岗位题|行为题|证据清单(?:（.*）)?)$/.test(trimmed)
      ) {
        return `### ${trimmed}`;
      }

      return line;
    })
    .join("\n");
}

/** Run one turn (any user message: the opening ask or a mid-conversation
 *  reply) and either post the assistant's reply as a bubble — conversation
 *  continues via the composer, same as any chat turn — or, once the
 *  response looks like a finished report, hand off to the standalone
 *  document view. `filesMeta` is only passed on the turn immediately after
 *  an upload. */
async function runTurn(messageText, filesMeta) {
  startChatTimer();
  showThinking();
  try {
    const text = await streamRunToText(activeThreadId, messageText, filesMeta, updatePhaseFromMessages);
    hideThinking();

    if (!text) {
      throw new Error("没有收到有效回复,可能是上游模型出错或被限流了,请稍后重试。");
    }
    loadThreadList(); // fire-and-forget: picks up a newly created thread / title update
    if (looksLikeReport(text)) {
      stopChatTimer("已完成");
      addChatBubble("assistant", "✅ 报告已生成,见下方:");
      renderReport(text);
    } else {
      stopChatTimer("");
      addChatBubble("assistant", text);
    }
  } catch (err) {
    hideThinking();
    stopChatTimer("出错了,可以重新发一次");
    addChatBubble("assistant", "⚠️ " + (err.message || String(err)));
  }
}

// ------------------------------------------------------------ report ui --
// The report stays inline in the conversation (not a separate page/view —
// changed 2026-09-19 per user feedback) and ships with both a real
// downloadable .md file and a print-to-PDF path via a dedicated print window
// (simpler and more reliable than hiding chat chrome with @media print).

function downloadMarkdown(markdown) {
  const titleMatch = markdown.match(/^#\s+(.+)$/m);
  const safeName = (titleMatch ? titleMatch[1] : "JobScout报告").replace(/[\\/:*?"<>|]/g, "_");
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safeName}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function openPrintWindow(html) {
  const printWin = window.open("", "_blank");
  if (!printWin) {
    addChatBubble("assistant", "⚠️ 浏览器拦截了打印窗口的弹出,请允许弹窗后重试。");
    return;
  }
  // Absolute URL — a blank popup's relative-path resolution for a
  // document.write'd page is inconsistent across browsers.
  const styleHref = new URL("style.css", location.href).href;
  printWin.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>JobScout 报告</title>` +
      `<link rel="stylesheet" href="${styleHref}" /></head>` +
      `<body><article class="report-doc report-doc-print">${html}</article></body></html>`
  );
  printWin.document.close();
  printWin.addEventListener("load", () => {
    printWin.focus();
    printWin.print();
  });
}

function renderReport(markdown) {
  const sanitizedMarkdown = sanitizeJobScoutReportMarkdown(markdown);
  const html = markdownToHtml(normalizeReportMarkdownForRender(sanitizedMarkdown));

  const wrap = document.createElement("div");
  wrap.className = "bubble-row report-row";

  const doc = document.createElement("article");
  doc.className = "report-doc report-doc-inline";
  doc.innerHTML = html;
  wrap.appendChild(doc);

  const actions = document.createElement("div");
  actions.className = "report-actions";
  const printBtn = document.createElement("button");
  printBtn.type = "button";
  printBtn.className = "primary-btn";
  printBtn.textContent = "打印 / 导出为 PDF";
  printBtn.addEventListener("click", () => openPrintWindow(html));
  const downloadBtn = document.createElement("button");
  downloadBtn.type = "button";
  downloadBtn.className = "link-btn";
  downloadBtn.textContent = "下载 Markdown 文件";
  downloadBtn.addEventListener("click", () => downloadMarkdown(sanitizedMarkdown));
  actions.appendChild(printBtn);
  actions.appendChild(downloadBtn);
  wrap.appendChild(actions);

  $("chatMessages").appendChild(wrap);
  scrollChatToBottom();
}

function wireNewChatButton() {
  $("newChatBtn")?.addEventListener("click", () => {
    resetChat();
    renderThreadList();
    show("chatView");
  });
}

// ------------------------------------------------------------ sidebar --
// Thread history is DeerFlow's own storage (POST /api/threads/search to
// list, GET /api/threads/{id}/state to replay one) — nothing custom is
// persisted client-side, so history survives across browsers/devices as
// long as you're logged into the same account.

let threadListCache = [];

/** Inverse of withSkillPrefix, for display only — history replay shows the
 *  user's real words, not the invisible /jobscout activation prefix. */
function stripSkillPrefix(text) {
  return (text || "").replace(/^\/jobscout\s*\n?/, "");
}

async function loadThreadList() {
  try {
    const threads = await apiJson("/api/threads/search", { method: "POST", json: { limit: 50 } });
    threadListCache = (threads || [])
      .slice()
      .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    renderThreadList();
  } catch (err) {
    // Non-fatal — the sidebar just stays empty/stale if this fails; it
    // shouldn't block the actual chat functionality.
    console.error("loadThreadList failed", err);
  }
}

function renderThreadList() {
  const container = $("threadList");
  if (!container) return;
  container.innerHTML = "";
  if (!threadListCache.length) {
    const empty = document.createElement("div");
    empty.className = "thread-empty muted small";
    empty.textContent = "还没有历史对话";
    container.appendChild(empty);
    return;
  }
  for (const t of threadListCache) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "thread-item" + (t.thread_id === activeThreadId ? " active" : "");
    const title = t.values?.title || "未命名对话";
    item.textContent = title;
    item.title = title;
    item.addEventListener("click", () => selectThread(t.thread_id));
    container.appendChild(item);
  }
}

async function selectThread(threadId) {
  if (threadId === activeThreadId) return;
  activeThreadId = threadId;
  clearAttachment();
  $("chatMessages").innerHTML = "";
  sentHistory = [];
  historyIndex = -1;
  historyDraft = "";
  renderThreadList(); // reflect the new active selection immediately
  try {
    const state = await apiJson(`/api/threads/${threadId}/state`);
    renderHistoryMessages(state?.values?.messages || []);
  } catch (err) {
    addChatBubble("assistant", "⚠️ 加载历史对话失败:" + (err.message || String(err)));
  }
}

/** Replay a thread's full message history into the chat panel: every
 *  visible human/ai/tool message becomes a bubble, in order. If the last
 *  one looks like a finished report, it gets the same report-card
 *  treatment a live run would give it (with working print/download
 *  actions), not just plain text. */
function renderHistoryMessages(messages) {
  const visible = (messages || []).filter((m) => !m?.additional_kwargs?.hide_from_ui);
  if (!visible.length) {
    addChatBubble("assistant", WELCOME_MESSAGE);
    return;
  }
  visible.forEach((m, i) => {
    const isLast = i === visible.length - 1;
    if (m.type === "human") {
      const original = m.additional_kwargs?.original_user_content;
      const text = stripSkillPrefix(typeof original === "string" ? original : contentToText(m.content)).trim();
      if (text) {
        addChatBubble("user", text);
        sentHistory.push(text); // so ArrowUp recall works after replaying a past thread too
      }
    } else if (m.type === "ai" || m.type === "tool") {
      const text = contentToText(m.content).trim();
      if (!text) return;
      if (isLast && looksLikeReport(text)) {
        renderReport(text);
      } else {
        addChatBubble("assistant", text);
      }
    }
  });
}

// ---------------------------------------------------- minimal markdown --
// Small, dependency-free converter covering exactly what the jobscout
// report template uses: #/##/### headings, **bold**, [text](url) links,
// bullet/numbered lists, "|" tables, "> " blockquotes, and paragraphs.

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineMd(s) {
  let out = escapeHtml(s);
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  return out;
}

function markdownToHtml(md) {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  let html = "";
  let i = 0;
  let inList = null; // 'ul' | 'ol' | null

  function closeList() {
    if (inList) { html += `</${inList}>`; inList = null; }
  }

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*$/.test(line)) { closeList(); i++; continue; }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html += `<h${level}>${inlineMd(heading[2])}</h${level}>`;
      i++; continue;
    }

    if (/^>\s?/.test(line)) {
      closeList();
      const quoteLines = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      html += `<blockquote>${inlineMd(quoteLines.join(" "))}</blockquote>`;
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line)) {
      closeList();
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].trim());
        i++;
      }
      if (rows.length >= 2 && /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$/.test(rows[1])) {
        const headCells = rows[0].replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        html += "<table><thead><tr>" + headCells.map((c) => `<th>${inlineMd(c)}</th>`).join("") + "</tr></thead><tbody>";
        for (let r = 2; r < rows.length; r++) {
          const cells = rows[r].replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
          html += "<tr>" + cells.map((c) => `<td>${inlineMd(c)}</td>`).join("") + "</tr>";
        }
        html += "</tbody></table>";
      } else {
        rows.forEach((r) => { html += `<p>${inlineMd(r)}</p>`; });
      }
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (inList !== "ul") { closeList(); html += "<ul>"; inList = "ul"; }
      html += `<li>${inlineMd(bullet[1])}</li>`;
      i++; continue;
    }

    const numbered = line.match(/^\s*\d+\.\s+(.*)$/);
    if (numbered) {
      if (inList !== "ol") { closeList(); html += "<ol>"; inList = "ol"; }
      html += `<li>${inlineMd(numbered[1])}</li>`;
      i++; continue;
    }

    if (/^-{3,}$/.test(line.trim())) { closeList(); html += "<hr/>"; i++; continue; }

    closeList();
    const paraLines = [line];
    i++;
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,4})\s+/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i]) && !/^>\s?/.test(lines[i])) {
      paraLines.push(lines[i]);
      i++;
    }
    html += `<p>${inlineMd(paraLines.join(" "))}</p>`;
  }
  closeList();
  return html;
}

// -------------------------------------------------------------- bootstrap --
// Guarded so this file can also be loaded under Node (no `document`) to unit
// test the pure functions above (markdownToHtml, extractLastVisibleAiText,
// looksLikeReport, withSkillPrefix) without a real browser.
if (typeof document !== "undefined") {
  setupAuthForm();
  setupComposer();
  wireNewChatButton();
  checkSession();
}

if (typeof module !== "undefined") {
  module.exports = {
    markdownToHtml,
    extractLastVisibleAiText,
    contentToText,
    looksLikeReport,
    normalizeReportMarkdownForRender,
    sanitizeJobScoutReportMarkdown,
    withSkillPrefix,
  };
}
