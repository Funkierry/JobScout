# JobScout 阶段 3：入口、锚点与工具限制

阶段 3 只改 JobScout 应用路径；没有改 harness 的通用 agent、工具装配、scheduler 或 tracker。真实研究、分类模型效果与费用未运行。

## 为什么使用专属入口

通用 `assemble_lead_agent` 会在 middleware 运行前装配工具，因此仅加一个 `before_agent` 无法保证跑题不加载工具。Gateway 现在把 `assistant_id=jobscout` 路由到 `app/jobscout/entry_graph.py`，并兼容原有 `jobscout_mode` 请求。JobScout 页面显式发送这个 assistant；不再把岗位记录拼进用户的分类输入。

`classify → respond / research` 是独立外层图。规则无法判断时才尝试分类模型；拒绝、缺字段或无法分类均由代码返回固定文案。只有通过入口后，才在 assembly executor 中创建模型和工具。入口图本身不包含通用 goal、自动续跑、记忆更新或标题模型，checkpoint 读取也不创建模型。

分类结果为 `{in_scope, intent, missing_fields}`，intent 包含 interview_prep、base_match、follow_up_deepen、switch_company、add_resume、off_topic。服务器另存判定来源 rules/model/unresolved，不记录简历或抓取正文。规则是有限启发式，不代表语义边界绝对正确。

## 部署与配置

在 JobScout 的 Gateway 启动环境设置：

```powershell
$env:JOBSCOUT_ENFORCE_THREAD_BINDING = "1"
# 可选：填写 config.yaml -> models 中已配置且授权的低成本模型名称
$env:JOBSCOUT_INTENT_MODEL = "<已配置的低成本模型名称>"
```

然后按原有方式重启 Gateway。本阶段没有启动真实服务或调用模型，也没有替用户选择付费模型。

- `JOBSCOUT_ENFORCE_THREAD_BINDING=1`：认证用户对已绑定线程的后续运行均走 JobScout，即使请求换成 lead_agent 或删除 mode。绑定查询有 5 秒上限，失败返回 503，不回落通用主 agent。开启此开关是完整部署的必要步骤。
- 不设置绑定开关：显式 JobScout 请求仍执行入口护栏，但不能宣称通用 API 改 assistant 后也一定受其约束。这保留上游部署的通用故障降级行为。
- `JOBSCOUT_INTENT_MODEL`：只供规则无法判定时使用。名称必须存在于配置且通过模型授权；8 秒总超时，输出 token 上限配置为 256，不启用思考，不注册工具。底层提供者对 token 参数的支持以其配置为准。
- 未配置、模型不可用、超时或输出结构错误：固定澄清，不使用主模型兜底。实体字段与附件可用性由代码重新检查，模型不能自报“文件存在”来通过入口。
- 专属研究图直接安装阶段 2 证据中间件，无需额外配置扩展。原有扩展仍可服务旧版调用，但不能代替阶段 3 入口。

## 线程与输入边界

`JobScoutState` 保存 `jobscout_anchor={company, role, recruitment_type, mode}` 和本轮 `jobscout_decision`。部分字段可逐轮补齐；跑题不改变锚点。追问可带上前一次已校验报告作研究方向，但本轮引用仍需重新抓取。更换公司或岗位时不把旧报告、旧工具消息传给主模型；模板中的公司、岗位与招聘类型使用服务器锚点覆盖模型值。

旧线程不会从未经验证的历史回复推断锚点。首次使用专属入口后写入保留元数据 `jobscout_entry_version=3`，历史仍可读取；缺字段时重新补齐。新线程直接创建为 jobscout。full / delta 状态均经 `CheckpointStateAccessor` 读写，重建图后保留锚点，分支使用相同应用状态类型。

运行只接受一条新用户消息；拒绝模型/系统消息、恢复命令和跨线程引用。清除客户端消息 id、隐藏标记及未知元数据。`/runs` 和 `/state` 都剥离伪造锚点、分类及校验标记；线程元数据接口不能改写绑定。Gateway 的线程归属校验先于绑定处理。用户数据始终在 HumanMessage 数据通道，不插入 SystemMessage。

## 工具与证据

| 模式 | 真正注册的工具 | 执行约束 |
| --- | --- | --- |
| Base 匹配 | read_file | 只读本线程经过验证的上传文本快照；虚拟路径须精确匹配 |
| 面试准备 / 有目标的追问 | web_search、web_fetch、read_file、task | 仅加载配置中同名的搜索/抓取工具，再应用工具授权 |
| 研究子任务 | web_search、web_fetch、read_file | 无递归 task；每轮最多三个子任务，并发上限三个、单任务超时 180 秒 |

上述工具还受 `JobScoutToolPolicy` 在模型绑定与执行时的白名单检查；不存在通用 MCP、shell、文件写入、网页操作或延迟发现工具。研究子任务使用应用专属的同名 task 包装器，没有改 DeerFlow 的通用子任务执行器。

Base 记录只从原有 `BASE_SNAPSHOTS` 按认证用户及线程取回；过期或跨用户/线程引用返回缺字段提示。主模型通过 read_file 实际读到的简历片段仍须经阶段 2 逐字校验才能计分。真实文件不进入仓库；新增应用审计不记录正文。最终展示仍由结构化证据模板控制。

## 离线复现

在仓库根目录运行，无需模型密钥：

```powershell
python -B skills/public/jobscout/scripts/evaluate_triggers.py --output local_eval/jobscout_stage3/trigger-evaluation.json
python -B skills/public/jobscout/scripts/evaluate_triggers.py --cases skills/public/jobscout/evals/entry_context_cases.json --output local_eval/jobscout_stage3/context-evaluation.json
node jobscout-web/test_pure.js
backend/.venv/Scripts/python.exe -B backend/scripts/jobscout_offline_tests.py --junitxml ../local_eval/jobscout_stage3/pytest.xml
python -B -m unittest discover -s skills/public/jobscout/scripts -p "test_*.py"
```

原有 24 条触发集与新增 12 条上下文集分开报告。in_scope=true 且缺字段仍算正确识别范围，不等于实际放行研究；规则未决按固定澄清处理，并单列。完整明细保存在 gitignore 的 local_eval，终端只打印汇总。分类模型路径只做 mock 测试，不能把 mock 输出当作真实模型准确率。实际数字、误判原文及限制见 [metrics.md](metrics.md)。
