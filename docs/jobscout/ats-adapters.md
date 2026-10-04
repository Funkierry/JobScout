# 阶段 5：只读 ATS 适配器

适配器只解析浏览器已经捕获的 JSON，不发起额外接口请求，不操作投递、撤回或登录。实现限于 `backend/app/application_tracker/`，没有改变 DeerFlow 的通用 Agent 行为。

## 支持范围

| 适配器 | 严格匹配的接口路径 | 已核实映射与限制 |
|---|---|---|
| 飞书 / 字节同结构 | `/api/v1/search/user/applications` | 成功 `code=0`；每条 `job_post_info.title` 与自己的 `current_stage.stage_id` 配对。`0→已投递`，`1→流程终止`；`2` 和其他代码回退 |
| 腾讯 | `/api/v1/apply/getApplyProcess` | 成功 `status=0`；仅 `currentStatus.status=2` 且 `applyProcessType=4` 映射为简历筛选。岗位来自 `positionInfo.applyPositionTxt`；不按历史步骤的最大编号决定状态 |
| 小红书 | `/websiterecruit/apply/record/list` | 成功 `success=true`、`statusCode=200`、`errorCode=200`；`status=end`、`statusGroup=finished` 且唯一 `terminal` 步骤为 `done` 时映射为流程终止；不据此推断拒信或 Offer |

域名白名单在各适配器模块的 `HOSTS` 中。飞书同结构共 9 个已观察域名，腾讯为 `join.qq.com`，小红书为 `job.xiaohongshu.com`。不按域名后缀或招聘系统品牌模糊匹配；要求 HTTPS、标准端口、同主机与精确路径。新域名、新枚举和额外状态字段需要新证据及 fixture 后才能扩展，不能由数值大小推断招聘环节。

这些映射来自少量已有页面快照与辅助标签，尚无供应商完整状态字典。腾讯和小红书各只有 1 条明确先导标签；实际覆盖率不代表全平台覆盖率。

## 接入与回退

`observations.py` 的 `CapturedJsonResponse` 保留接口信封，单条上限 256,000 字节，适配器最多读取响应窗口的前 20 条。超限响应作为不可用记录保留，不能截断列表后声称完整解析；响应窗口仍可能因其他请求先占满而缺少目标接口。凭据与常见个人字段复用现有脱敏。完整信封仅存在于本次检查内存或 gitignore 的本地回放文件，不放进模型工具输出、轨迹或日志。

`browser/live_session.py` 收集同站 JSON，包括 HTTP 错误；通用模型观察继续排除 HTTP 错误。`agent/tools.py` 在打开、登录完成、点击和重新读取页面时更新结构化响应；`agent/workflow.py` 在初次与点击后的抽取节点先调用 `try_adapters`，命中后直接完成。模型工厂延迟到真正需要原有抽取器或 Agent 时才执行，每次运行单独绑定工具，避免批量检查共享工具状态。

同一接口以当前响应窗口中最后一条为准。最后一条报错、缺字段或超限时，不使用更早成功响应。无适配器、无目标接口、非成功信封、未知状态、重复岗位或证据无法逐字对上，均回退到原有模型流程。固定原因码只含类型，不含申请内容。

“待识别岗位”的列表必须逐条完整解析，任何未识别状态都会使该列表回退。已有明确岗位名时，只接受完全相同的唯一岗位，可解析该岗位并忽略其他岗位的未知状态。标签中的 `expected_role` 永远只用于判分，不能拿来选择解析对象。

状态证据保留真实 JSON 字段片段，例如 `"stage_id": 0`，不把代码映射后的中文名称伪装成接口原文。同一记录的结构关联由适配器验证，逐字证据由共享 grounding 模块再次验证。规则置信分 1.0 表示满足该适配器的确定性条件，不是准确率或概率。

## 日期与已知限制

小红书仅解析明确的 `applyTime` 绝对时间，并校验格式及未来日期。飞书的 `biz_create_time` 未获得足以确认显示日期语义的依据，腾讯样本缺少已确认的申请时间字段，因此这两路暂不新推断日期。现有数据库保留已知投递日期的行为不变；新记录仍可能没有日期。日期判分照实保留在指标中。

只读 JSON 回放无法检验最新门户是否仍使用这些接口，也不能测量浏览器登录、加载或端到端速度。辅助标注仍需要人工独立复核和新增留出样本。

## 无模型回放

从仓库根目录执行，使用现有本地冻结标签与快照：

```powershell
backend/.venv/Scripts/python.exe -B backend/scripts/application_tracker_adapters_eval.py
```

可选参数 `--dataset`、`--snapshots`、`--report` 必须位于 `local_eval/` 内。默认写入 `local_eval/tracker_snapshots/stage5-adapters.json` 及对应 `.manifest.json`；标签文件不会修改。报告包含逐条私有结果，终端只打印汇总。脚本不初始化模型、不调用模型回退，也不需要 `--allow-model-cost`；它是适配器覆盖率评测，不是完整生产流程的 A/B。

口径：覆盖率 = 接管条数 / 全部明确标签；适配器准确率 = 接管且状态正确条数 / 接管条数；未接管在适配器单独评测中记“未知”。分母为零时准确率为 null，未知且无证据的记录不计入证据逐字率的分母。另按适配器统计，并列出回退原因。原模型流程真实效果仍需用户主动运行基线命令，见 [稳定性说明](tracker-stability.md)。

合成测试验证域名/接口/结构边界、未知枚举、登录及接口错误、最新响应失败、多岗位对应、未来日期、字段脱敏、初始化延迟、JSON-only 与点击后接入、隐私输出、标签隔离和指标分母。测试命令与实测数字见 [metrics.md](metrics.md)。
