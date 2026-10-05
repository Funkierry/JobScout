// Pure display, evidence and stream helpers. No DOM or application state.
(function (root, factory) {
  const value = factory();
  if (typeof module !== "undefined") module.exports = value;
  else root.JobScoutCore = value;
})(globalThis, function () {
  function resolveDeploymentConfig(config = {}, location = {}) {
    const localSplit =
      location.protocol === "http:" &&
      location.port === "5500" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
    const localHost = location.hostname || "localhost";
    const gatewayBase =
      config.gatewayBase ?? (localSplit ? `http://${localHost}:8001` : "");
    const capabilityCenterUrl =
      config.capabilityCenterUrl ??
      (localSplit
        ? `http://${localHost}:3000/workspace/capabilities`
        : "/workspace/capabilities");
    function validateUrl(value, allowPath) {
      if (typeof value !== "string") throw new Error("部署地址必须是字符串");
      if (allowPath && /^\/(?!\/)/.test(value) && !value.includes("\\"))
        return value;
      if (!value && !allowPath) return "";
      const parsed = new URL(value);
      if (
        !["http:", "https:"].includes(parsed.protocol) ||
        parsed.username ||
        parsed.password ||
        (!allowPath &&
          (parsed.pathname !== "/" || parsed.search || parsed.hash))
      ) {
        throw new Error(
          "部署地址必须是有效的 HTTP(S) 地址，且不能包含账号密码",
        );
      }
      return allowPath ? parsed.href : parsed.origin;
    }
    return {
      gatewayBase: validateUrl(gatewayBase, false),
      capabilityCenterUrl: validateUrl(capabilityCenterUrl, true),
    };
  }

  const TERMINAL_TRACKER_STATUSES = new Set(["Offer", "未通过", "流程终止"]);
  const STOPPED_TRACKER_STATUSES = new Set(["未通过", "流程终止"]);

  function isTerminalTrackerStatus(status) {
    return TERMINAL_TRACKER_STATUSES.has(status);
  }

  function trackerDisplayRow(row) {
    const source = row?.source_summary;
    if (!source || source.conflict || source.source !== "email") return row;
    return {
      ...row,
      portal_status: row.status,
      portal_evidence: row.evidence,
      status: source.status,
      stage: row.stage_manual ? row.stage : source.status,
      raw_status: source.status,
      evidence: source.evidence,
      checked_at: source.received_at,
      changed_at: source.received_at,
      check_result: "成功",
      confidence: 1,
      changed: false,
    };
  }

  function trackerRowPresentation(row) {
    const terminal =
      Boolean(row?.terminal) || isTerminalTrackerStatus(row?.status);
    const needsReview =
      Boolean(row?.checked_at) && Number(row?.confidence) < 0.7;
    const changed = Boolean(row?.changed && row?.previous_status);
    const stopped =
      STOPPED_TRACKER_STATUSES.has(row?.status) ||
      STOPPED_TRACKER_STATUSES.has(row?.stage);
    const unknown =
      needsReview ||
      !row?.status ||
      row.status === "未知" ||
      (row.check_result && row.check_result !== "成功");
    return {
      terminal,
      canRefresh: !terminal,
      needsReview,
      changeText: changed ? `${row.previous_status} → ${row.status}` : "",
      tone: changed ? "changed" : needsReview ? "review" : "normal",
      statusTone: stopped ? "stopped" : unknown ? "unknown" : "active",
    };
  }

  function trackerSummary(rows) {
    const summary = { total: rows.length, active: 0, review: 0, offers: 0 };
    for (const stored of rows) {
      const row = trackerDisplayRow(stored);
      const presentation = trackerRowPresentation(row);
      const review =
        row.source_summary?.conflict ||
        presentation.needsReview ||
        !row.status ||
        row.status === "未知" ||
        (row.check_result && row.check_result !== "成功");
      if (review) summary.review += 1;
      else if (row.status === "Offer") summary.offers += 1;
      else if (!presentation.terminal) summary.active += 1;
    }
    return summary;
  }

  function trackerStageWaitText(row, now = new Date()) {
    if (!row?.status || row.status === "未知") return "阶段待确认";
    if (row.terminal || isTerminalTrackerStatus(row.status))
      return "流程已结束";
    if (row.stage_manual) return "手动环节，起点待确认";
    if (!row.changed_at) return "等待首次检查";
    const started = new Date(row.changed_at);
    const elapsed = now.getTime() - started.getTime();
    if (!Number.isFinite(elapsed) || elapsed < 0) return "起点时间待确认";
    const days = Math.floor(elapsed / 86400000);
    return days < 1
      ? "不足 1 天（自首次识别）"
      : `至少 ${days} 天（自首次识别）`;
  }

  function trackerStageLabel(row) {
    row = trackerDisplayRow(row);
    return row?.stage || row?.status || "未分类";
  }

  function trackerStageFilterOptions(rows, stages = []) {
    const counts = new Map();
    for (const row of rows) {
      const label = trackerStageLabel(row);
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    const ordered = [...new Set(stages)].filter((label) => counts.has(label));
    for (const label of counts.keys()) {
      if (!ordered.includes(label)) ordered.push(label);
    }
    return ordered.map((label) => ({ label, count: counts.get(label) }));
  }

  function filterTrackerRows(rows, stage) {
    return stage === null
      ? rows
      : rows.filter((row) => trackerStageLabel(row) === stage);
  }

  function parseSseFrames(buffer) {
    const normalized = String(buffer || "").replace(/\r\n/g, "\n");
    const frames = normalized.split("\n\n");
    const remainder = frames.pop() || "";
    const events = [];
    for (const frame of frames) {
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) events.push(JSON.parse(data));
    }
    return { events, remainder };
  }

  // ---------------------------------------------------------------- helpers --

  function jobScoutRunContext(mode, contextRef = null) {
    return {
      jobscout_mode: mode === "match" ? "base_match" : "interview_prep",
      jobscout_evidence_version: 2,
      ...(mode === "match" && typeof contextRef === "string" && contextRef
        ? { jobscout_base_context_ref: contextRef }
        : {}),
    };
  }

  /** Canonical display text for both values streams and persisted history.
   * Backend-filtered content also feeds renderReport's print/download actions.
   * Old reports cannot be retroactively verified without their original tools. */
  function guardedMessageText(message, mode = "prep") {
    if (message?.additional_kwargs?.hide_from_ui) return "";
    if (message?.type === "tool" && message.name !== "ask_clarification")
      return "";
    if (message?.tool_calls?.length) return "";
    const text = contentToText(message?.content).trim();
    if (
      !text ||
      (message?.type === "tool" && message.name === "ask_clarification")
    )
      return text;
    const stamp = message?.additional_kwargs?.jobscout_evidence;
    const route = message?.additional_kwargs?.jobscout_route;
    if (
      route?.version === 3 &&
      typeof route.run_id === "string" &&
      route.run_id &&
      typeof route.in_scope === "boolean" &&
      Array.isArray(route.missing_fields) &&
      (!route.in_scope || route.missing_fields.length > 0)
    )
      return text;
    if (
      stamp?.version === 2 &&
      stamp.mode === (mode === "match" ? "base_match" : "interview_prep") &&
      typeof stamp.run_id === "string" &&
      stamp.run_id &&
      Number.isInteger(stamp.accepted_count) &&
      stamp.accepted_count >= 0 &&
      Number.isInteger(stamp.removed_count) &&
      stamp.removed_count >= 0
    )
      return text;
    return "这份内容尚无本次运行的结构化证据与来源校验记录，暂不展示或导出。旧报告需要重新生成；新报告请先启用 JobScout 来源校验中间件。";
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

  function extractLastVisibleAiText(messages, mode = "prep") {
    // `ask_clarification` is `return_direct=True`: LangGraph ends the run with
    // its ToolMessage (type "tool") as the final state, and the model's own
    // preceding AIMessage carries the tool *call*, not the question text the
    // user is meant to see. Only checking type "ai" here silently swallowed
    // every clarification question — a real bug found via a live user report,
    // not code review (Gateway logs proved the server side worked fine; the
    // Gateway's actual response just wasn't the shape this function assumed).
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m?.type === "human") break;
      if (m?.additional_kwargs?.hide_from_ui) continue;
      if (m?.type === "ai" || m?.type === "tool") {
        const text = guardedMessageText(m, mode);
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
    const sectionPrefix = "(?:#{1,3}\\s+|\\d+\\s*[)）.、]\\s*)?";
    const sectionSuffix = "(?:[（(][^\\r\\n]*[）)])?\\s*$";
    const hitCount = (markers) =>
      markers.filter((m) =>
        new RegExp(`^${sectionPrefix}${m}${sectionSuffix}`, "m").test(text),
      ).length;
    return (
      hitCount(["公司速览", "岗位拆解", "面试题预测"]) >= 2 ||
      hitCount(["候选人画像", "推荐岗位", "匹配依据", "风险与数据边界"]) >= 3
    );
  }

  /** Keep the JobScout document contract stable when a model appends generic
   *  coaching chapters of its own. This is deliberately conservative: only
   *  known top-level drift is removed, so numbered questions and preparation
   *  steps inside an allowed section are never mistaken for new chapters. */
  function sanitizeJobScoutReportMarkdown(markdown) {
    const source = String(markdown || "");
    const matchingMode =
      source.includes("简历 × 飞书岗位匹配报告") ||
      ["候选人画像", "推荐岗位", "匹配依据", "风险与数据边界"].filter((s) =>
        source.includes(s),
      ).length >= 3;
    const allowedSections = matchingMode
      ? ["候选人画像", "推荐岗位", "匹配依据", "风险与数据边界"]
      : [
          "公司速览",
          "岗位拆解",
          "面试题预测",
          "差距分析",
          "证据边界与后续建议",
        ];
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
      return candidates.some((candidate) =>
        normalized.startsWith(candidate.toLowerCase()),
      );
    };

    let keep = true;
    const keptLines = [];
    for (const line of source.split(/\r?\n/)) {
      const trimmed = line.trim();
      const numbered = trimmed.match(/^\d+\s*[)）.、]\s*(.+)$/);
      const levelTwo = trimmed.match(/^##(?!#)\s+(.+)$/);
      const bareStop = /^(?:执行凭据(?:（工具收据）)?|工具收据|sources)$/i.test(
        trimmed,
      );
      const candidate =
        numbered?.[1] || levelTwo?.[1] || (bareStop ? trimmed : "");

      if (candidate) {
        if (startsWithAny(candidate, allowedSections)) {
          keep = true;
        } else if (
          levelTwo ||
          bareStop ||
          startsWithAny(candidate, unwantedSections)
        ) {
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
          if (
            !trimmed.startsWith("#") &&
            (trimmed.includes("面试准备包") || trimmed.includes("岗位匹配报告"))
          ) {
            return `# ${trimmed}`;
          }
        }

        const numberedSection = trimmed.match(
          /^\d+\s*[)）.、]\s*(公司速览|岗位拆解|面试题预测|差距分析|候选人画像|推荐岗位|匹配依据|风险与数据边界)(.*)$/,
        );
        if (numberedSection) {
          return `## ${numberedSection[1]}${numberedSection[2]}`;
        }

        if (
          !trimmed.startsWith("#") &&
          /^(?:业务与产品|近期动态|融资\s*[\/／]\s*规模(?:（.*）)?|公司技术栈核对(?:（.*）)?|技术\s*[\/／]\s*岗位题|行为题|证据清单(?:（.*）)?)$/.test(
            trimmed,
          )
        ) {
          return `### ${trimmed}`;
        }

        return line;
      })
      .join("\n");
  }

  function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function inlineMd(s) {
    // Code-rendered evidence escapes Markdown punctuation. Preserve those
    // characters as text before interpreting actual formatting and links.
    let out = escapeHtml(s)
      .replace(
        /\\([\\`*_{}\[\]()#+.!|])/g,
        (_, char) => "&#" + char.charCodeAt(0) + ";",
      )
      .replace(/\\(&lt;|&gt;)/g, "$1");
    out = out.replace(
      /\[([^\]]+)\]\(&lt;(https?:\/\/[^\s]+?)&gt;\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>',
    );
    out = out.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>',
    );
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    return out;
  }

  function markdownTableCells(row) {
    const cells = [""];
    const text = row.replace(/^\||\|$/g, "");
    for (let i = 0; i < text.length; i++) {
      if (text[i] === "\\" && i + 1 < text.length) {
        cells[cells.length - 1] += text[i] + text[++i];
      } else if (text[i] === "|") cells.push("");
      else cells[cells.length - 1] += text[i];
    }
    return cells.map((cell) => cell.trim());
  }

  function markdownToHtml(md) {
    const lines = md.replace(/\r\n/g, "\n").split("\n");
    let html = "";
    let i = 0;
    let inList = null; // 'ul' | 'ol' | null

    function closeList() {
      if (inList) {
        html += `</${inList}>`;
        inList = null;
      }
    }

    while (i < lines.length) {
      const line = lines[i];

      if (/^\s*$/.test(line)) {
        closeList();
        i++;
        continue;
      }

      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        closeList();
        const level = heading[1].length;
        html += `<h${level}>${inlineMd(heading[2])}</h${level}>`;
        i++;
        continue;
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
        if (
          rows.length >= 2 &&
          /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$/.test(rows[1])
        ) {
          const headCells = markdownTableCells(rows[0]);
          html +=
            "<table><thead><tr>" +
            headCells.map((c) => `<th>${inlineMd(c)}</th>`).join("") +
            "</tr></thead><tbody>";
          for (let r = 2; r < rows.length; r++) {
            const cells = markdownTableCells(rows[r]);
            html +=
              "<tr>" +
              cells.map((c) => `<td>${inlineMd(c)}</td>`).join("") +
              "</tr>";
          }
          html += "</tbody></table>";
        } else {
          rows.forEach((r) => {
            html += `<p>${inlineMd(r)}</p>`;
          });
        }
        continue;
      }

      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      if (bullet) {
        if (inList !== "ul") {
          closeList();
          html += "<ul>";
          inList = "ul";
        }
        html += `<li>${inlineMd(bullet[1])}</li>`;
        i++;
        continue;
      }

      const numbered = line.match(/^\s*\d+\.\s+(.*)$/);
      if (numbered) {
        if (inList !== "ol") {
          closeList();
          html += "<ol>";
          inList = "ol";
        }
        html += `<li>${inlineMd(numbered[1])}</li>`;
        i++;
        continue;
      }

      if (/^-{3,}$/.test(line.trim())) {
        closeList();
        html += "<hr/>";
        i++;
        continue;
      }

      closeList();
      const paraLines = [line];
      i++;
      while (
        i < lines.length &&
        !/^\s*$/.test(lines[i]) &&
        !/^(#{1,4})\s+/.test(lines[i]) &&
        !/^\s*[-*]\s+/.test(lines[i]) &&
        !/^\s*\d+\.\s+/.test(lines[i]) &&
        !/^\s*\|.*\|\s*$/.test(lines[i]) &&
        !/^>\s?/.test(lines[i])
      ) {
        paraLines.push(lines[i]);
        i++;
      }
      html += `<p>${inlineMd(paraLines.join(" "))}</p>`;
    }
    closeList();
    return html;
  }

  return {
    resolveDeploymentConfig,
    isTerminalTrackerStatus,
    trackerDisplayRow,
    trackerRowPresentation,
    trackerSummary,
    trackerStageWaitText,
    trackerStageLabel,
    trackerStageFilterOptions,
    filterTrackerRows,
    parseSseFrames,
    jobScoutRunContext,
    guardedMessageText,
    contentToText,
    extractLastVisibleAiText,
    looksLikeReport,
    sanitizeJobScoutReportMarkdown,
    normalizeReportMarkdownForRender,
    escapeHtml,
    inlineMd,
    markdownTableCells,
    markdownToHtml,
  };
});
