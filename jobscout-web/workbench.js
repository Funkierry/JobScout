/* Private workflow notes. No recruitment site or model calls originate here. */
(function (root) {
  "use strict";
  const STEPS = { requirements: "已核对岗位要求与待补材料", experience: "已整理可讲述的真实经历", practice: "已练习问题或完成下一步决策" };
  const CATEGORIES = { correction: "识别纠错", report: "报告反馈", recommendation: "推荐决策", trial: "试用任务记录" };
  function node(tag, text = "", className = "") {
    const el = document.createElement(tag); el.textContent = text; el.className = className; return el;
  }
  function button(text, fn) {
    const el = node("button", text, "secondary-btn"); el.type = "button"; el.addEventListener("click", fn); return el;
  }
  function field(form, title, name, max = 1500) {
    const label = node("label", title); const input = node("textarea"); input.name = name; input.maxLength = max; input.rows = 2;
    label.append(input); form.append(label); return input;
  }
  function create({ api, session, openItem }) {
    let pendingVersion = 0, feedbackVersion = 0, offset = 0;
    const panel = document.getElementById("workbenchPending");
    const list = document.getElementById("workbenchItems");
    const status = document.getElementById("workbenchStatus");
    const nav = document.getElementById("workbenchNavigation");
    const dialog = node("dialog", "", "workbench-dialog"); dialog.setAttribute("aria-label", "反馈与试用记录"); document.body.append(dialog);
    function reset() {
      pendingVersion++; feedbackVersion++; offset = 0; list.replaceChildren(); status.textContent = ""; nav.replaceChildren();
      document.getElementById("workbenchCount").textContent = ""; panel.open = false; dialog.close(); dialog.replaceChildren();
    }
    async function refresh(nextOffset = 0) {
      const version = ++pendingVersion, owner = session();
      const current = () => version === pendingVersion && owner === session();
      status.textContent = "正在读取待处理事项…";
      try {
        let data = await api(`/api/jobscout/workbench?limit=6&offset=${nextOffset}`);
        if (!current()) return;
        if (!data.items.length && nextOffset > 0) return refresh(0);
        offset = nextOffset;
        document.getElementById("workbenchCount").textContent = ` · ${data.total} 项`;
        status.textContent = data.total ? "先处理冲突、登录与新变化。排序不代表截止时间；不会自动刷新招聘网站。" : "当前没有待处理事项。";
        list.replaceChildren(); nav.replaceChildren();
        for (const item of data.items) {
          const card = node("article", "", "workbench-item");
          const copy = node("div"); copy.append(node("strong", `${item.company} · ${item.role}`), node("span", item.title, "workbench-kind"), node("p", item.hint));
          const action = button(({ change: "查看变化", interview: "继续准备", materials: "补充材料" })[item.kind] || "查看并处理", async () => {
            action.disabled = true;
            try { await openItem(item); } catch (error) { if (current()) status.textContent = error.message; }
            finally { if (current()) action.disabled = false; }
          });
          card.append(copy, action); list.append(card);
        }
        if (offset) nav.append(button("上一组事项", () => refresh(Math.max(0, offset - 6))));
        if (data.has_more) nav.append(button("下一组事项", () => refresh(offset + 6)));
      } catch (error) { if (current()) { status.textContent = `待处理事项读取失败：${error.message}`; nav.replaceChildren(button("重试读取事项", () => refresh(offset))); } }
    }
    function feedback(context = {}) {
      const version = ++feedbackVersion, owner = session();
      const current = () => version === feedbackVersion && owner === session() && dialog.open;
      dialog.replaceChildren();
      const head = node("div", "", "workbench-heading"); head.append(node("h2", "反馈与试用记录"), button("关闭记录", () => dialog.close())); dialog.append(head);
      dialog.append(node("p", "仅保存在你的账号中。纠错是用户反馈，不会直接修改官网状态或验证结论。请勿填写密码、验证码等凭据。"));
      const form = node("form", "", "workbench-form"), categoryLabel = node("label", "记录类型"), category = node("select"); category.name = "category"; category.setAttribute("aria-label", "记录类型");
      for (const [value, label] of Object.entries(CATEGORIES)) category.add(new Option(label, value));
      category.value = context.category || "trial"; categoryLabel.append(category); form.append(categoryLabel);
      const errorLabel = node("label", "问题类型"), errorType = node("select"); errorType.name = "error_type"; errorType.setAttribute("aria-label", "问题类型");
      for (const [value, label] of Object.entries({ status: "状态表达", identity: "公司或岗位识别", evidence: "证据或材料不足", matching: "岗位匹配", usability: "操作或流程阻塞", other: "其他 / 无错误" })) errorType.add(new Option(label, value));
      errorType.value = "other"; errorLabel.append(errorType); form.append(errorLabel);
      field(form, "原结果 / 试用任务 / 推荐岗位（便于复现）", "original").value = context.original || "";
      field(form, "更正内容 / 实际结果", "correction");
      field(form, "原因 / 阻塞点（必填）", "reason").required = true;
      field(form, "下次复验应检查什么", "next_check");
      const outcomeLabel = node("label", "处理结果"), outcome = node("select"); outcome.name = "outcome"; outcome.setAttribute("aria-label", "处理结果");
      for (const [value, label] of Object.entries({ unspecified: "尚未判定", accepted: "接受推荐", rejected: "放弃推荐", completed: "任务已完成", blocked: "任务受阻" })) outcome.add(new Option(label, value));
      outcomeLabel.append(outcome); form.append(outcomeLabel);
      const submit = node("button", "保存记录", "primary-btn"); submit.type = "submit";
      const message = node("p"); message.setAttribute("role", "status"); form.append(submit, message); dialog.append(form);
      const history = node("details"), summary = node("summary", "最近 50 条记录"), historyBody = node("div"); history.append(summary, historyBody); dialog.append(history);
      let lastBody = "", requestId = "", savedRows = [], historyVersion = 0;
      async function loadHistory() {
        const version = ++historyVersion;
        try {
          const rows = await api("/api/jobscout/feedback"); if (!current() || version !== historyVersion) return;
          savedRows = rows; historyBody.replaceChildren();
          if (!rows.length) historyBody.append(node("p", "尚无记录。"));
          for (const row of rows) {
            const entry = node("article", "", "workbench-item"); const copy = node("div");
            copy.append(node("strong", `${CATEGORIES[row.category]} · ${new Date(row.created_at).toLocaleString()}`));
            copy.append(node("p", `问题类型：${row.error_type || "other"} · 结果：${({ accepted: "接受推荐", rejected: "放弃推荐", completed: "任务已完成", blocked: "任务受阻" })[row.outcome] || "尚未判定"}`));
            for (const [label, key] of [["原结果 / 任务", "original"], ["更正 / 实际结果", "correction"], ["原因", "reason"], ["复验", "next_check"]]) if (row[key]) copy.append(node("p", `${label}：${row[key]}`));
            entry.append(copy); historyBody.append(entry);
          }
        } catch (error) { if (current() && version === historyVersion) historyBody.replaceChildren(node("p", `历史读取失败：${error.message}`), button("重试读取记录", loadHistory)); }
      }
      history.append(button("导出已读取记录 JSON", () => {
        const blob = new Blob([JSON.stringify(savedRows, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob);
        const link = node("a"); link.href = url; link.download = "jobscout-feedback.json"; link.click(); URL.revokeObjectURL(url);
      }));
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (submit.disabled) return;
        const body = Object.fromEntries(new FormData(form));
        for (const key of ["thread_id", "application_id", "report_key"]) if (context[key]) body[key] = context[key];
        const signature = JSON.stringify(body);
        if (signature !== lastBody) { requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`; lastBody = signature; }
        body.request_id = requestId; [...form.elements].forEach(el => { el.disabled = true; }); message.textContent = "正在保存…";
        try {
          await api("/api/jobscout/feedback", { method: "POST", json: body }); if (!current()) return;
          message.textContent = "已保存。原始投递状态和报告保持不变。"; form.reset(); category.value = context.category || "trial"; lastBody = ""; await loadHistory();
        } catch (error) { if (current()) message.textContent = `保存失败，内容已保留：${error.message}`; }
        finally { if (current()) [...form.elements].forEach(el => { el.disabled = false; }); }
      });
      if (!dialog.open) dialog.showModal(); loadHistory();
    }
    async function mountPlan(parent, threadId, markdown) {
      const owner = session(), current = () => owner === session() && parent.isConnected;
      const box = node("section", "", "preparation-actions"); box.setAttribute("aria-label", "准备进度与个人笔记"); parent.append(box);
      box.append(node("h3", "把报告转成准备行动"), node("p", "先核对证据，再整理经历，最后练习或决策。勾选表示你确认完成；笔记与进度仅属于当前版本的报告。"));
      const form = node("form", "", "workbench-form"), controls = node("fieldset"); controls.disabled = true; form.append(controls);
      for (const [value, title] of Object.entries(STEPS)) {
        const label = node("label", "", "preparation-check"); const input = node("input"); input.type = "checkbox"; input.name = "done"; input.value = value;
        label.append(input, node("span", title)); controls.append(label);
      }
      const experience = field(controls, "准备使用的个人经历（用户笔记）", "experience", 3000);
      const materials = field(controls, "缺失材料 / 待确认问题（用户笔记）", "materials", 3000);
      const save = node("button", "保存准备进度", "primary-btn"); save.type = "submit"; controls.append(save);
      const message = node("p", "正在读取已保存的准备进度…"); message.setAttribute("role", "status"); form.append(message); box.append(form);
      let key, revision = 0, reading = 0;
      async function reload() {
        const version = ++reading; controls.disabled = true;
        try {
          if (!key) {
            // Hashing only names a user's note version; it does not verify the report.
            if (!globalThis.crypto?.subtle) throw new Error("准备笔记需要 HTTPS 或 localhost 安全连接");
            key = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(markdown))), n => n.toString(16).padStart(2, "0")).join("");
          }
          const data = await api(`/api/jobscout/preparation/${encodeURIComponent(threadId)}?report_key=${key}`);
          if (!current() || version !== reading) return;
          revision = data.revision; experience.value = data.experience; materials.value = data.materials;
          controls.querySelectorAll('input[name="done"]').forEach(input => { input.checked = data.done.includes(input.value); });
          message.textContent = revision ? `已恢复保存进度 · ${data.done.length} / 3 项` : "尚未保存。完成一项后勾选并保存。"; controls.disabled = false;
        } catch (error) { if (current() && version === reading) message.textContent = `读取失败：${error.message}`; }
      }
      const reloadButton = button("重新读取（替换未保存内容）", reload); box.append(reloadButton);
      box.append(button("记录报告反馈 / 推荐决策", () => feedback({ category: markdown.includes("简历 × 飞书") ? "recommendation" : "report", thread_id: threadId, report_key: key || "", original: markdown.split("\n")[0] })));
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (controls.disabled) return;
        const data = { revision, done: [...controls.querySelectorAll('input[name="done"]:checked')].map(input => input.value), experience: experience.value, materials: materials.value };
        controls.disabled = true; reloadButton.disabled = true;
        try {
          const saved = await api(`/api/jobscout/preparation/${encodeURIComponent(threadId)}?report_key=${key}`, { method: "PUT", json: data });
          if (current()) { revision = saved.revision; message.textContent = `已保存 · ${saved.done.length} / 3 项准备完成`; }
        } catch (error) { if (current()) message.textContent = `保存失败，内容已保留：${error.message}`; }
        finally { if (current()) { controls.disabled = false; reloadButton.disabled = false; } }
      });
      await reload();
    }
    document.getElementById("workbenchFeedback").addEventListener("click", () => feedback());
    document.getElementById("workbenchRefresh").addEventListener("click", () => refresh());
    return { refresh, feedback, mountPlan, reset };
  }
  root.JobScoutWorkbench = { create };
})(globalThis);
