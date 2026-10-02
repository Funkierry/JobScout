# 阶段 1：本轮 URL 来源校验

本文记录阶段 1 行为。阶段 2 已新增结构化证据、日期与计分校验；新版界面的协议和边界见 [evidence-guard.md](evidence-guard.md)。

本阶段取消技术题、行为题数量下限，只列有证据的问题，不足时说明证据边界。URL 校验由代码执行：链接必须出现在本次运行的真实 `web_search` / `web_fetch` 工具结果中，否则替换为“（来源未核验）”。这不能证明网页内容真实或支持相邻结论；逐字引用、新闻日期与结构化证据属于阶段 2，本阶段未实现。

## 开启方式

在本地 `extensions_config.json` 的 `middlewares` 数组中加入下列类路径，保留已有 MCP、技能及其他中间件配置，然后重启 Gateway：

```json
"middlewares": ["app.jobscout.middleware:JobScoutLinkMiddleware"]
```

如果在 `config.yaml` 显式配置了 `extensions`，应在那里合并同一项，YAML 的显式值优先。配置示例见 `config.example.yaml`。类导入或构造失败时，现有配置加载器会阻止 Agent 创建，不会静默跳过护栏。

独立 JobScout 界面自动发送 `config.context.jobscout_mode`：面试准备为 `interview_prep`，Base 为 `base_match`。普通 DeerFlow 请求没有该标志时行为不变。这个开关是产品选择，不是阶段 3 的入口分类器。直接 API 调用者也须显式选择面试准备模式并使用 `stream_mode: ["values"]`。

## 挂载点与数据流

- 实际回复路径是 `routers/thread_runs.py` 的线程运行接口；`routers/jobscout.py` 处理岗位与 tracker，并不生成面试报告。
- `extensions.middlewares` 的现有加载器把同一应用中间件加入主 Agent 和子 Agent，无需修改 DeerFlow 通用工厂。子任务沿用服务器的 `user_id/thread_id/run_id`，从共享注册表识别受保护的研究轮次。
- `wrap_tool_call` / `awrap_tool_call` 只读取已经完成的工具返回值。搜索只采集结果对象的 `url` 字段，不采集摘要中随意出现的 URL。Markdown-only 抓取结果与该次真实调用的 URL 参数配对；错误、空结果和未执行调用不入白名单。网页中的其他链接不会自动获得信任。
- `wrap_model_call` / `awrap_model_call` 在模型节点写入状态前过滤主 Agent 的可见文本，覆盖行内、引用式、自动、裸 URL 及 HTML 链接。只保留 HTTP(S)；统一协议/主机大小写、默认端口并去掉片段，保留业务路径、查询参数及 HTTP/HTTPS 区别。HTML 链接转换为 Markdown，其他 HTML 标签移除。代码块里的 URL 也会检查。
- 最终消息 `additional_kwargs.jobscout_links` 保存版本、run ID、观测 URL 数、`removed_count` 和审计提交状态；`middleware:jobscout_links` 事件保存同类计数，不新增页面正文、简历或 URL 日志。审计调用异常时仍保存已过滤消息，并标注 `trace_submitted=false`；`true` 仅表示已调用异步审计队列，实际持久化仍取决于上游事件存储。
- JobScout 实时输出、历史回放、打印和 Markdown 下载共用已过滤正文；工具原文和未完成的 AI 工具调用不作为报告展示。无护栏标记的旧报告不会被追认为已核验，需要重新生成。

## 隔离与边界

注册表只在本轮主 Agent 开始时创建，不读取旧轮次状态。支持三个研究任务并行，按用户、线程、运行三元组隔离；后到的子任务不能重新创建已结束条目。成功结束、包装器捕获的取消或系统退出会清理主任务条目。普通模型/工具异常可能被上游重试或恢复，因此保留同轮已成功观测的来源，避免一次抓取失败抹掉其他研究结果；最终失败或发生在钩子之外的终止由容量与过期清理兜底。注册表最多保留 256 个运行，每轮最多 2048 个 URL，绝对有效期 2 小时，每次访问清理过期条目。超限、进程重启或丢失来源时按空白名单过滤，不复用历史来源。

当前主任务与子任务在同一 Gateway 进程执行，注册表为进程内共享；未来若把子任务分发到另一进程，应先引入服务器所属的共享证据存储，不能绕过校验。重复运行或从中断恢复时只信任当前运行重新观测的来源，因此可能比旧报告更保守。

JobScout 产品使用 `values` 状态流。供应商原始 token 回调及上游 RunJournal 的原始模型草稿早于过滤钩子，不能作为已核验报告；本阶段没有修改通用 DeerFlow token 流和审计存储。工具成功抓取一个页面也不能证明其不是登录页或错误页面，不能代替阶段 2 的内容支持检查。

## 离线验证与评测格式

`backend/tests/test_jobscout_links.py` 覆盖 URL 规范化、失败抓取、参数与结果配对、引用式及 HTML 绕过。`test_jobscout_middleware.py` 使用假模型驱动真实 LangGraph，覆盖三路并行子图、逐次 `values`、最终检查点、跨轮/用户/线程隔离及清理；不调用真实模型。

`evals/run_result.schema.json` 新增 `tool_messages`：它必须是本轮工具轨迹（含子任务），不能由最终报告链接反推。搜索条目形如 `{"type":"tool","name":"web_search","content":"[{\"url\":\"https://example.cn/a\"}]"}`；抓取先记录携带 `id/name/args.url` 的 AI tool call，再记录同 `tool_call_id` 的成功 ToolMessage。合并子任务轨迹时先给 tool-call ID 加子任务前缀，避免供应商 ID 重复导致错误配对。真实完整轨迹仅保存在 gitignore 的 `local_eval/`，不能提交或打印。

`grade_runs.py` 共用生产 URL 校验函数。缺少轨迹不能自动通过，所有报告链接必须可追溯；保留“来源是否支持结论”等人工复核门槛。`metrics.removed_count` 可记录中间件实际输出的移除数。合成 fixture 中的人工标签仅测试判分分支，不代表真实调研质量。

```powershell
node jobscout-web/test_pure.js
python -B -m unittest discover -s skills/public/jobscout/scripts -p "test_*.py"
python -B skills/public/jobscout/scripts/validate_eval_set.py
python -B skills/public/jobscout/scripts/grade_runs.py --input skills/public/jobscout/evals/fixtures/grader_smoke.jsonl --output-json local_eval/jobscout_stage1/grader.json
```

该 fixture 包含故意编造的链接，应报告硬门槛失败；不能据此把判分器正确拒绝记为测试失败。实测结果见 [metrics.md](metrics.md)。真实端到端研究、token、费用及质量提升均为待运行。

后端测试需设置 `CI=true`、`PYTHON_DOTENV_DISABLED=1`、`DEER_FLOW_RUN_LIVE_TESTS=0`、`JOBSCOUT_REAL_RESEARCH=0`，移除模型密钥、使用无模型的独立 fixture 配置并在测试进程阻断外网；仅 `-m "not live"` 不足以保证上游全量测试离线。具体执行器见 `backend/scripts/jobscout_offline_tests.py`。
