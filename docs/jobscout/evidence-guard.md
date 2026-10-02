# 阶段 2：结构化证据与固定报告

实现位于 `backend/app/jobscout/`，共用逐字匹配位于 `backend/app/evidence/grounding.py`。tracker 从该模块导入原实现，保留原导出名，没有改变 tracker 的抽取、置信度或浏览器策略。

## 启用与执行路径

继续使用阶段 1 的应用扩展 `app.jobscout.middleware:JobScoutLinkMiddleware`，配置方法见 [link-guard.md](link-guard.md)。更新后重启 Gateway 并刷新独立界面。无需添加模型或 API 密钥。

新版界面自动发送 `config.context.jobscout_evidence_version=2` 和 `jobscout_mode=interview_prep`。Base 模式使用 `base_match`，并传服务器返回的 `jobscout_base_context_ref`。直接 API 调用也需选择 v2，使用根级 `values` 状态流。

普通 DeerFlow 请求行为不变。未选择 v2 的旧客户端保留阶段 1 行为，不能冒充阶段 2 已校验报告。新版界面不展示或导出旧标记或无标记的 AI 回复；旧报告需重新生成。

1. 主任务用服务器所属的 user/thread/run 三元组创建本轮容器；子任务沿用该身份查找，不从历史消息恢复来源。
2. `wrap_tool_call` / `awrap_tool_call` 观察真实成功的搜索和抓取结果，记录 URL 与对应正文或摘要。用户粘贴 URL、模型总结、页面中的其他链接都不创建来源。
3. 子任务额外输出 `jobscout_evidence` JSON 数组。响应在写入子任务状态前被校验并重建；父任务的 task 返回值再校验一次，覆盖 ToolMessage 和 Command。替换重复的原始结果摘要，同时保留 task 状态、停止原因和 token 用量等元数据。
4. 主任务输出 `jobscout_report` 或 `jobscout_match` JSON 块。代码按固定章节渲染，丢弃外围 Markdown 与未知字段。解析失败时显示证据不足说明，不回退原报告。最终继续经过 URL 过滤。
5. 校验后的消息进入 values 和检查点；前端实时展示、历史回放、打印和 Markdown 下载共用版本检查。

字段与三路分工见 [SKILL.md](../../skills/public/jobscout/SKILL.md)。中间件也注入静态协议，避免技能正文压缩后失去格式要求。没有新增模型调用、格式修复调用、入口分类器、线程锚点或工具白名单；后几项不属于阶段 2。

## 校验规则

| 对象 | 代码保证 |
| --- | --- |
| 研究条目 | section/claim/url/quote 类型与长度合规；URL 对应本轮真实工具来源；非空 quote 在该 URL 的某次观察中逐字存在 |
| 空白差异 | 沿用 tracker 的仅空白容错，返回原来源片段；不做改写或同义词匹配 |
| 搜索摘要 | 取实际结果的 snippet/content 字段，来源类型由代码决定，显示“搜索摘要” |
| 近期动态 | ISO 日期在服务器日期往前 12 个自然月内，含起止日；闰年按日历处理；日期原文与内容引文来自同一份来源观察 |
| 差距分析 | 公开岗位来源之外，还需本线程已读取简历的非空原文；不通过则删除该条 |
| Base 得分项 | direction/skills/projects/education/preferences 上限 30/30/20/10/10；只收整数；重复维度、越界、缺失或无简历原文均为 0 分 |
| Base 排名 | 忽略模型总分和公司/岗位名；服务器快照提供记录；代码重算总分、稳定排序、最多 10 条；虚构记录 ID 不入榜 |

逐字校验不证明 claim 必然由 quote 推出；日期片段也可能是页面其他事件的日期，不是文章发布时间。报告因此将正文称为“解读”，附原文，保留人工语义复核。题目统一标为公开资料推导，不自动宣称公司已考真题。Base 分值合理性仍需人工复核；此次保证证据存在和算术约束，不是自动判断候选人能力。

## 授权、隐私与容量

`POST /api/jobscout/base-context` 新增可选 thread_id。有该参数时先检查线程归属，再使用当前用户的只读 CLI 授权读取岗位。随机引用绑定用户和线程；消息或 context 中伪造的岗位列表不能成为计分依据。不带 thread_id 兼容旧接口，但不生成 v2 计分快照。

Base 快照最多 64 份、有效期 30 分钟，继承适配器最多 200 行／60,000 字符的边界，保存分页与裁剪标记。进程重启或过期后需重新读取岗位，不回退消息里的记录。多 Gateway 进程需要请求粘性或后续服务器所属共享存储，当前不跨进程共享。

简历只读取服务器映射的本用户、本线程 uploads 目录，文件名属于最近一次 human 上传元数据。可在后续轮使用同线程先前上传，但本轮仍需成功 read_file。拒绝路径穿越、符号链接、目录和非 UTF-8 文本；优先读取转换后的 .md，也支持 .txt；最多 4 个文件，每份 200,000 字节。只有工具确实返回、与文件快照相符的原文参与校验。被工具截断的完整输出或更早的分段读取可能无法作为当前证据，报告会保守不计分。

每轮最多 200 个来源 URL，每 URL 最多 4 次观察；单份正文最多 100,000 字符，总计 2,000,000 字符。结构化输入上限 100,000 字符／100 条候选。运行注册表沿用最多 256 轮、2 小时绝对过期。结束与取消清理；未捕获终止由过期和容量回收兜底。来源丢失时丢弃相应结论，不恢复旧轮次证据。

新增审计 `additional_kwargs.jobscout_evidence` / `middleware:jobscout_evidence` 仅记录版本、run ID、通过/拒绝数量、原因计数、链接移除数与队列提交状态，不加入原文、URL、简历或岗位内容。供应商原始 token 回调、上游工具日志/原始草稿早于此钩子，不能作为已校验报告；本阶段不改上游通用日志机制。真实评测轨迹、简历、快照只存 gitignore 本地目录，不提交。用户最终报告按需求展示必要证据片段。

## 离线验证与复现

仓库只包含合成 fixture。以下命令不发起真实研究：

```powershell
$env:PYTHONUTF8 = "1"
node jobscout-web/test_pure.js
backend/.venv/Scripts/python.exe -B backend/scripts/jobscout_offline_tests.py --junitxml ../local_eval/jobscout_stage2/pytest.xml
python -B -m unittest discover -s skills/public/jobscout/scripts -p "test_*.py"
python -B skills/public/jobscout/scripts/evaluate_guardrail_fixtures.py --output local_eval/jobscout_stage2/comparison.json
```

最后一条在同一组 11 条合成输入上比较阶段 1 的 URL 过滤和阶段 2 的证据校验；不衡量真实业务准确率、延迟或成本。实际数字见 [metrics.md](metrics.md)。

`grade_runs.py` 的 v2 记录需要 `runtime.evidence_version=2`、`structured_evidence`，包含服务器日期、实际原始模型/子任务输出与配对工具记录。工具记录含 agent_id，避免子任务重复调用 ID 错配。Base 评测还需评测者采集的服务器岗位快照、分页信息和授权上传文本。这些记录必须由可信评测采集器生成，不能让被评模型编造自己的证明。

判分器复用生产校验器与模板，要求重放结果逐字等于报告；v1 评测仍可重放，但结果明确标 version 1，不能当作 v2 证明。人工真实性、语义支持性门槛保留。真实端到端运行和模型费用均待运行。
