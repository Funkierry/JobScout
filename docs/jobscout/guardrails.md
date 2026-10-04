# JobScout 护栏与验证边界

JobScout 在输入、工具、证据、存储和展示五处增加代码约束。下表是当前实现，历史阶段说明中的“后续实现”以此为准。上游 DeerFlow 提供认证、Agent runtime、检查点与 scheduler；下列业务策略位于 JobScout 应用路径，不修改上游通用行为。

## 每层挡什么、挂在哪里

| 层 | 约束与挂载点 | 仍需核对的边界 |
|---|---|---|
| 请求入口 | `app/jobscout/entry_graph.py`、`intent.py`：先规则后可选配置模型；跑题和缺字段固定回复，研究图尚未创建 | 规则未决会澄清；分类模型真实效果待运行 |
| 多轮锚点 | `state.py`、`admission.py` 与 Gateway：公司、岗位、招聘类型写入线程状态，按意图更新；full/delta 支持恢复 | 严格线程绑定需要部署开关，不能只信前端模式 |
| 工具权限 | `tool_policy.py`、`agent.py`：Base 模式仅绑定并执行 `read_file`；公开研究按模式注册工具 | 私有 Base 数据在认证 Gateway 中只读获取，不作为公开证据 |
| 来源白名单 | `links.py`、`middleware.py`：收集本轮成功搜索/抓取的 URL，在主回复写状态前过滤未观测链接，记录移除数量 | URL 被观测不等于网站可靠；网页内其他链接不会自动受信 |
| 结构化证据 | `evidence.py`、`run_evidence.py`：三路研究提交 claim/url/quote；来源和逐字片段不匹配则丢弃；搜索摘要标明来源类型 | 引文存在不证明解读必然成立，不是自动语义事实核查 |
| 近期信息 | `evidence.py`：日期原文与引文属于同一观察，发布时间在过去 12 个自然月内 | 页面中的日期可能对应其他事件，仍需人工判断语义 |
| 报告模板 | `render.py`：代码组织章节；前端 SSE、历史、打印与下载使用带校验标记的正文 | 旧的无标记报告需要重新生成；原始模型 token 不是已校验报告 |
| 简历计分 | `matching.py`、`inputs.py`：引用本用户本线程实际读取的简历原文；无原文、重复或超限得分为 0，代码重算排序 | 只保证证据存在及算术，不保证每个分值合理；不会代投 |
| Tracker 观察 | `application_tracker/observations.py` 与 `browser/`：同站有界 JSON 优先、DOM 降噪兜底、稳定等待、单 context 登录 | JSON/网页都是不可信数据；观察窗口可能漏接口，登录可能过期 |
| Tracker 判定 | `confidence.py`、`status_semantics.py`、`dates.py`：状态词、岗位行与逐字证据决定规则分；未知日期不猜 | 规则分不是统计校准概率；不能据此宣称准确率 |
| ATS 适配器 | `application_tracker/adapters/`：主机、路径、结构均匹配后确定性映射；未知状态或歧义回退 | 当前三类系统及已观察结构，不宣称覆盖平台全部版本 |
| 只读邮件 | `application_tracker/email/`：Gmail readonly / IMAP EXAMINE + BODY.PEEK，域名和主题双过滤、逐字证据、唯一岗位关联 | 邮件正文不进日志；模糊匹配、旧事件与终态冲突保留核对；凭据只在本地 |
| 状态合并 | `email/store.py`：以收件时间比较信息新旧，更具体且不冲突才采用；原门户历史保留 | 安排的面试时间不当作事件发生时间；未知轮次不硬猜一面 |
| 定时与通知 | `scheduling.py`、`scheduled_graph.py`：内部专属任务入口、非终态筛选、原子每日额度、去重、仅已知状态变化通知 | 默认关闭；手动检查不计入调度额度；模型回退另需显式开启 |
| 隐私与归属 | tracker/mail/notification 均按认证用户隔离；真实快照、标签、简历、邮件与凭据只在 gitignore 本地目录 | 本地管理员仍能访问本机数据；开启模型抽取会发送必要正文给配置供应商 |

共用逐字校验位于 `app/evidence/grounding.py`，tracker、研究报告、简历和邮件复用，不保留多份不同规则。

## 部署隔离

独立界面使用 JobScout assistant。应用中间件按 [证据护栏说明](evidence-guard.md) 注册，服务器设置 `JOBSCOUT_ENFORCE_THREAD_BINDING=1` 后阻止已绑定 JobScout 线程改用其他 assistant 绕过入口；普通 DeerFlow 请求保持原行为。可选分类器只使用 `JOBSCOUT_INTENT_MODEL` 指定的已配置模型，无配置时澄清，不默认调用主模型。

定时刷新同时需要 `config.yaml` 中的 `scheduler.enabled`、环境变量 `JOBSCOUT_SCHEDULED_REFRESH_ENABLED=1` 和用户开关。邮箱默认关闭，需要用户自行完成只读授权、设置精确招聘发件域名。完整步骤见 [邮件渠道](mail-channel.md)、[定时刷新](scheduled-refresh.md)。

研究来源注册表与 Base 快照目前为进程内存储，按用户/线程/运行隔离并有容量、过期限制；多进程分发需共享可信存储或请求粘性。来源丢失时保守丢弃，不恢复旧轮次证据。

## 已测结果与前后对照

以下数字引自 [metrics.md](metrics.md)，不是本页新跑的评测。阶段 0 模型基线和完整生产流程仍待运行，不能计算真实幻觉或准确率提升幅度。

| 观察项 | 改动前 | 当前实测 | 口径 |
|---|---|---|---|
| 虚构链接判分 fixture | 编造链接样例通过 | 同样例拒绝 | 阶段 1 的离线判分回归 |
| 少量有证据的题目 | 一道技术题样例被数量配额拒绝 | 同样例通过 | 取消配额；并非降低事实门槛 |
| 原触发集跑题漏放 / 误拒 | 待运行 | 0/12 / 0/12 | 阶段 3，24 条合成规则评测 |
| 新增上下文集跑题漏放 / 误拒 | 待运行 | 0/5 / 1/7 | 12 条合成样例，规则未决正例计误拒 |
| ATS 接管率 | 无适配器 | 15/20（75%） | 阶段 5，本地开发快照回放 |
| 接管内状态正确 / 证据逐字 | 待运行 | 15/15 / 15/15 | 辅助标签，未独立复核、无留出集 |
| 全集日期正确率 | 待运行 | 2/20（10%） | 日期不明确时留空，缺口公开记录 |
| 真实端到端状态准确率、耗时、token / 成本 | 待运行 | 待运行 | 不以 fixture 或纯解析耗时替代 |
| 真实邮件质量、调度成功率与通知延迟 | 未实现 | 待运行 | 当前只完成 fixture/mock 验证 |

阶段 7 的 JobScout/tracker 专项为 331 项通过，技能 unittest 18 项通过，前端纯函数测试通过；全量后端 `-x` 仍停在既有 BoxLite Windows 权限断言，blocking-I/O 仍有 16 项环境失败。完整执行次数、耗时和历史变化均留在指标文档，没有把专项通过写成全量通过。

## 如何复现

在仓库根目录运行前端和技能离线检查：

```powershell
node jobscout-web/test_pure.js
backend/.venv/Scripts/python.exe -B -m unittest discover -s skills/public/jobscout/scripts -p "test_*.py"
```

后端使用阻断外网并禁用 dotenv 的测试入口（在 `backend/` 运行）：

```powershell
.venv/Scripts/python.exe -B scripts/jobscout_offline_tests.py
```

真实页面与模型基线的采集、标注和显式付费命令在 [metrics.md](metrics.md)。扩大至 50–100 条、独立复核、增加笔试/面试/Offer 等状态及留出集后，才适合比较真实前后效果；当前 20 条先导标签不能替代该步骤。
