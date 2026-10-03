# JobScout tracker 效果基线

更新日期：2026-10-02。阶段 0 已建立采集、标注和离线判分流程，并在用户授权后完成第一批真实页面采集和辅助标注。**当前有 20 条证据明确的辅助标注、2 条待核对样例，尚未独立人工复核，也未运行模型基线。真实效果指标仍为「待运行」；不得把标注校验、单元测试或合成 fixture 当作模型效果。**

## 当前数据与结果

| 项目 | 当前值 | 说明 |
| --- | ---: | --- |
| 已提交的 tracker 合成评测样例 | 12 条 | `backend/tests/fixtures/application_tracker/eval_cases.jsonl`；只用于离线回归 |
| 本轮采集入口 | 19 个 | 用户提供的私有 URL 清单；不等于独立申请数 |
| 已取得申请证据的入口 | 14 个 | 其余 5 个仍需完成登录或重新采集 |
| 本地快照目录 | 18 个 | 含重采前的旧快照和无有效登录的快照，不能全部计作有效样本 |
| 证据明确的真实申请辅助标注 | 20 条 | 私有 `labels.jsonl`；尚未独立人工复核 |
| 待核对的辅助标注 | 2 条 | 单独保存在 `labels.pending_review.jsonl`，暂不并入基线输入 |
| 标注数据校验 | 205 项通过，0 项失败 | 原文对应、源文件哈希、岗位与状态/日期关联、输入与答案隔离等检查；不是抽取准确率 |
| 真实状态准确率 | 待运行 | 需在真实标注集上运行当前抽取器 |
| 真实「未知」占比 | 待运行 | 预测为「未知」的样例数 / 全部标注样例数 |
| 真实证据逐字率 | 待运行 | 有证据义务的结果中，证据能在页面正文逐字找到的比例 |
| 单条耗时 | 待运行 | 离线抽取每条调用的实测毫秒数；不含浏览器加载 |
| 模型 token / 成本 | 待运行 | 仅使用供应商返回的 token 用量；成本需显式提供该模型价格 |
| 浏览器到结果的端到端效果与耗时 | 待运行 | 本阶段的离线重放不包含浏览器导航、登录和 Agent 点击 |

### 第一批数据的覆盖边界

20 条明确标注来自 13 份不同页面快照，状态分布为：已投递 13 条、流程终止 5 条、简历筛选 2 条。尚未覆盖测评、笔试、各轮面试、Offer、明确拒绝等状态，也未达到 50–100 条目标。因此这批数据只能作为先导样本，不能代表所有招聘系统和招聘阶段。

标注由助手依据本地页面正文、截图和有明确语义的接口响应辅助完成；独立人工复核状态为「待运行」。两条待核对样例分别涉及未解释的接口阶段编号和缺少具体环节的“进行中”话术；不借用被评测代码的归类规则来反推参考答案。暂时排除这些困难样例会产生选择偏差，后续报告必须保留该边界，不能把待核对数量当作模型预测的「未知」占比。

本轮采集观察到三类问题：页面初始正文显示空列表而 JSON 已有申请；入口先打开个人资料页，需要进入申请记录；页面未出现明显登录表单，但申请接口明确返回未登录。此处仅记录采集现象，阶段 4 再改抓取策略。登录失败和失效快照未被编造成「未知」申请样例。

私有核对材料包括 `annotation_review.csv`（逐条复核表）、`annotation_review.json`（证据及标注来源）、`validation_report.json`（校验结果）和 `collection_inventory.json`（采集清单与覆盖情况），均位于 gitignore 的 `local_eval/tracker_snapshots/`。真实 URL、公司/岗位明细、截图、页面正文与逐条预测不进入本页或 GitHub。

本地 `baseline_manifest.json` 记录先导数据版本 `stage0-pilot-20261002-v1`、数据集 SHA-256、抽取/判分代码版本及文件哈希，供实际运行前核对。增补或修改标签后须重新记录数据版本；阶段 4 的前后比较应固定同一份已复核数据。

### 本轮验证记录（2026-10-02）

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| `node jobscout-web/test_pure.js` | 通过 | 前端纯函数检查 |
| JobScout 技能脚本 unittest | 5 项通过 | fixture / mock |
| JobScout / tracker 后端 pytest | 117 项通过 | 22 个测试文件；CI 模式、禁用 dotenv 自动加载，并在测试进程中阻断外网；外网拦截触发次数为 0 |
| 后端默认全量 pytest | 未完成 | 已确认一个 BoxLite Unix 执行权限位断言在 Windows 下失败，随后中止运行；不能据局部结果声明全量通过 |

全量测试的首次隔离不充分：`-m "not live"` 未排除 `test_client_e2e.py`，该模块可读取本地 `.env`，并进入真实模型调用路径。本次在 `TestBasicChat::test_basic_chat` 运行期间终止了测试进程，未获得可确认的模型用量或费用，不能记为零成本验证。上述 117 项是随后单独进行的离线验证；本次没有修改上游测试或运行时行为。真实 tracker 基线仍未运行。

`mean_elapsed_ms` 是评测进程内从调用 `StatusExtractor.extract` 到返回或异常的平均时长，单条明细在私有 `baseline.json` 的 `results[].elapsed_ms`。失败记录仍计入状态准确率分母，并按错判处理；`unknown_rate` 只计明确预测为「未知」的记录，抓取／模型失败在 `schema_valid_rate` 和 `check_result_accuracy` 中单独呈现。旧字段 `evidence_grounded_rate` 保留原有全样例口径；新增 `verbatim_evidence_rate` 只把预测为已知状态或提供非空证据的结果放进分母，已知状态却没有证据算失败。无合格分母时该值为 `null`。

列表页包含多条申请时，判分器按标注的目标岗位选择 `discovered_applications` 中的对应项；找不到该岗位时预测视为「未知」，不会借用页面上其他岗位的状态或证据。同一快照可对应多个岗位样例：当前指标按申请条目计算，不能把这些条目视为相互独立的页面样本。

`expected_role` 与 `expected_applied_at` 是参考答案字段，不能作为抽取器输入。岗位准确率只在输入岗位为「待识别岗位」且标注了 `expected_role` 时计算，避免把从输入原样复制的岗位误算为识别成功；日期准确率只在标注了 `expected_applied_at` 的样例上计算。页面确无可核实投递日期时，将 `expected_applied_at` 标为 `null`。token 覆盖不足 100% 时，总 token 和总成本为 `null`，不以文字长度估算。价格由运行者以美元／百万 token 明确提供；没有价格则成本为 `null`。

## 本地采集与标注

以下命令需由用户在 `backend/` 目录主动运行。采集命令会访问真实页面，**不会调用模型**。使用与现有 tracker 相同的 `--user-id` 和 profile 根目录；如之前设置了 `APPLICATION_TRACKER_BROWSER_PROFILE_DIR`，无需另传 `--profile-dir`。不要同时用两个 Chromium 进程打开同一 profile。

```powershell
$url = Read-Host "申请页 URL"
uv run --locked --extra browser python scripts/application_tracker_snapshot.py $url --user-id local-user
```

批量采集时，把真实 URL 清单放在 gitignore 的 `local_eval/tracker_snapshots/url_manifest.json`，格式为 `{"entries":[{"id":1,"url":"https://example.com/applications","category":"application"}]}`。先用 `--list` 核对条数，再运行采集；脚本会在需要登录时调用现有的人工登录流程，并在同目录保存不含 URL 的 `capture_progress.json`，下次运行会跳过已完成项。两条命令都不会调用模型。

```powershell
python -B scripts/application_tracker_capture_manifest.py --list
python -B scripts/application_tracker_capture_manifest.py --user-id local-user
```

若提示登录过期，先用已有的 `scripts/application_tracker_browser_check.py` 完成人工登录，然后重试快照命令。快照保存在 gitignore 的 `local_eval/tracker_snapshots/<编号>/`：`body.txt` 是原始可见文本，`responses.json` 是限定大小并尽力脱敏的 XHR/fetch JSON，`metadata.json` 含脱敏后的最终 URL，`screenshot.png` 是未脱敏的页面截图。XHR/fetch 仅覆盖从导航开始至读取正文的有限窗口；没有 `Content-Length` 的超大响应仍可能先被 Playwright 读入内存，再被上限剔除。正文和截图可能包含个人信息，不能提交或直接分享。

查看本地快照后运行标注命令；它不会把正文打印到终端。一个列表页可连续标注多条已申请岗位，目标为 50–100 条真实样例。

```powershell
uv run --no-sync python scripts/application_tracker_annotate.py
```

标注文件使用 UTF-8 JSONL，沿用 `OfflineExtractionCase` 的 `case_id`、`company`、`role`、`url`、`page_text`，另含 `expected_status`、`expected_role`、`expected_applied_at`。`role` 是本次检查的目标岗位；不确定时可填「待识别岗位」。`expected_role` 仅在页面明确显示对应岗位时填写。日期必须是页面明确支持的投递或申请日期，不用职位发布日期、截止日期或截图时间推断。

## 用户显式运行当前抽取器基线

标注完成后，下面的命令会逐条调用当前配置的模型，**产生模型成本**。模型选择沿用 `APPLICATION_TRACKER_MODEL` 或 DeerFlow 默认模型；如需指定可传 `--model`。价格参数可省略，此时成本显示不可得。运行报告和含原文的预测都只写入 `local_eval/`。

```powershell
uv run --no-sync python scripts/application_tracker_baseline.py --allow-model-cost
```

本机已安装 backend 虚拟环境时，可直接在仓库根目录运行以下命令，无需同步依赖。它只重放本地页面正文，不再访问招聘网站，但会把抽取所需的页面文本发送给当前配置的模型供应商；本次任务没有执行该命令。

```powershell
Set-Location backend
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -B scripts/application_tracker_baseline.py --allow-model-cost
```

如已核实模型计价，可加 `--input-usd-per-million <价格>` 与 `--output-usd-per-million <价格>`。运行后再把实际结果与模型、日期、样例量、数据集版本写入本页。阶段 4 应使用同一批标注样例做前后比较；端到端浏览器指标另行测量，不能与离线抽取耗时混写。

## 阶段 1：URL 来源校验与取消题量配额（2026-10-02）

本阶段没有运行真实搜索或模型研究。以下数字来自本地 fixture / mock，不代表业务准确率、事实正确率或真实幻觉下降幅度。阶段 0 的真实 tracker 模型基线仍为待运行。

### 同一合成样例的判分前后对比

对比使用改动前提交 `a9c421d3` 的 `grade_runs.py` 与阶段 1 判分器，在同一份 `evals/fixtures/grader_smoke.jsonl` 中的三个新增合成样例上实际执行：

| 合成样例 | 改动前 | 改动后 | 解释 |
| --- | --- | --- | --- |
| 一道有来源技术题，行为题不足并说明边界 | 拒绝 | 通过 | 移除技术/行为题数量下限，避免为了配额凑题 |
| 零题、零来源，明确说明证据不足 | 通过 | 通过 | 保留透明报告证据不足的能力 |
| 题量充足，但附带一个未出现在工具结果中的链接 | 通过 | 拒绝 | 新增 `observed_source_links` 硬门槛 |

完整判分 fixture 共 5 条：4 条通过，1 条因故意编造的链接被拒绝，与预期一致。fixture 内置的模拟分数、耗时和 token 是判分器输入，不能当作实际模型用量或延迟。本地对比记录在 gitignore 的 `local_eval/jobscout_stage1/comparison.json`。

### 实际验证结果

| 检查 | 实际结果 | 说明 |
| --- | --- | --- |
| 前端 `node jobscout-web/test_pure.js` | 通过 | 包含新增模拟 SSE、历史渲染、打印和 Markdown 下载链路 |
| 技能脚本 unittest | 10 项通过 | 新增少量题、零题、编造链接、缺少轨迹与规范化 URL 用例 |
| 后端专项 pytest | 173 项通过，43.45 秒 | JobScout、tracker、配置扩展加载与 harness 依赖边界；新增 URL 与中间件用例 37 项，另加技能契约检查 |
| 后端全量离线 pytest 尝试 | 1840 通过、1 失败、30 跳过、3 取消选择 | `-x` 在既有 BoxLite Unix 执行权限位断言处停止；不是全量通过 |
| blocking-I/O pytest | 129 通过、16 失败、1 跳过 | Windows chmod / 长路径和缺少 Lark CLI；本阶段未改相关通用代码 |
| 评测集与 Schema | 通过 | 30 个行为、24 个触发用例定义及 5 条合成判分记录；没有执行真实研究或入口分类 |
| 修改的后端 Python Ruff | 通过 | 格式与静态检查 |
| 本轮离线测试进程的外网拦截触发次数 | 0 | CI 模式、禁用 dotenv、移除密钥、无模型 fixture 配置、进程内网络拦截 |
| 真实面试准备端到端效果、耗时、token、费用 | 待运行 | 没有从合成样例推算 |

首次全量尝试在收集阶段因缺少本地配置引用的 `OPENAI_API_KEY` 停止；首次 blocking-I/O 为 127 通过、18 失败、1 跳过，其中两项同样受本地配置依赖影响。随后使用无模型、无密钥的独立 fixture 配置重跑，得到表内最终结果；未恢复真实凭据。执行器为 `backend/scripts/jobscout_offline_tests.py`，不改变上游 pytest 或运行时行为。外网拦截仅限当前 Python 进程，子进程继承 CI / 禁用 dotenv 设置，此执行器不是系统级网络沙箱。

当前代码检查 URL 是否由本轮工具实际观测，仍不能判断其正文是否支持结论。三路假模型图测试证实：三个子任务的来源能够进入本轮白名单，只有子任务总结提到的链接不能进入；每次主 Agent `values` 输出及最终检查点均使用已过滤文本。挂载点、启用方法、审计及生命周期边界见 [link-guard.md](link-guard.md)。

## 阶段 2：结构化证据与计分校验（2026-10-03）

本阶段没有执行真实搜索、招聘门户访问或真实模型调用。以下为实际运行的 fixture/mock 结果，不代表真实幻觉率、业务准确率或候选人匹配质量。阶段 0 真实 tracker 基线和面试准备端到端指标仍待运行。

### 同一组合成输入的前后行为

使用 `evals/fixtures/structured_guardrails.json` 的 11 条合成输入，通过 `scripts/evaluate_guardrail_fixtures.py` 实际运行阶段 1 的 URL 过滤函数和阶段 2 的生产校验器／模板。阶段 1 链接函数沿用提交 `b0880e10`，未修改。Base 的“之前”是模型草稿原值，因为此前没有代码计分校验。

| 合成情形 | 条数 | 阶段 1 处理后 | 阶段 2 处理后 |
| --- | ---: | --- | --- |
| 引文不存在、旧闻、伪造日期、未来日期、缺失日期、空引文 | 6 | 6 条目标断言均仍在草稿中 | 6 条均剔除 |
| 有效正文、有来源摘要、有效近期动态 | 3 | 3 条保留 | 3 条保留；摘要标明来源类型 |
| 简历不存在的技能片段 | 1 | 30/100 | 0/100 |
| 简历存在的技能片段 | 1 | 30/100 | 30/100 |

11/11 条符合 fixture 预期。对比明细保存在 gitignore 的 `local_eval/jobscout_stage2/comparison.json`。这是确定性护栏测试，不是完整 Agent 前后 A/B，也没有把模拟 token 或时间当成真实指标。

### 验证结果

| 检查 | 实际结果 | 范围和限制 |
| --- | --- | --- |
| 前端 `node jobscout-web/test_pure.js` | 通过 | prep/Base 的模拟 SSE、历史、打印/下载、旧报告拦截；转义证据不会变成额外表格列或链接 |
| 技能脚本 unittest | 16 项通过 | 比阶段 1 新增 6 项；包括结构重放、损坏或缺失证据、子任务 ID 隔离、计分、追问重新核验来源、11 条 fixture |
| 后端专项 pytest | 202 项通过，62.61 秒 | 比阶段 1 增加 29 项；包括缺失引文、日期、简历计分、线程归属、task Command、失效注册表、三路假模型图、非阻塞读取及 tracker 回归 |
| 后端全量离线 pytest 尝试 | 1840 通过、1 失败、30 跳过、3 取消选择；271.69 秒 | `-x` 在既有 BoxLite Unix 执行权限位断言停止，不是全量通过 |
| blocking-I/O pytest | 129 通过、16 失败、1 跳过；36.53 秒 | 与阶段 1 相同的 Windows chmod、长路径和缺少 Lark CLI 问题；未修改上游实现 |
| 评测定义与 Schema | 通过 | 30 条行为、24 条触发定义、5 条旧判分 fixture；另验证 11 条结构化 fixture 的 Schema，没有执行真实研究 |
| 后端修改文件 Ruff | 通过 | 静态检查与格式检查 |
| 本轮离线测试进程外网拦截触发数 | 0 | 专项、全量尝试与 blocking-I/O 均为 0；仍是进程内防护，非系统级沙箱 |
| 真实状态准确率、“未知”占比、研究质量、耗时、token、成本 | 待运行 | 未用测试通过率或 fixture 数量代替真实效果 |

首次新增用例因模块尚未实现而按预期收集失败。首轮实现后出现一处测试按数组位置寻找维度的错误，改为按维度名称定位后通过；后续集成与最终专项结果如上。对比脚本最初未考虑 Markdown 转义导致有效目标字符串误判，修正为按相同渲染转义查找后，11 条结果与结构化通过数量一致。

语义真实性仍需要人工复核：引用存在不能证明 claim 的推导正确；页面里的日期也不必然是文章发布日期；简历原文存在不保证分值合理。实现、开关、私有数据边界和复现命令见 [evidence-guard.md](evidence-guard.md)。

## 阶段 3：入口分类、线程锚点与工具白名单（2026-10-04）

以下数字来自实际执行的生产规则分类器和 fixture/mock。没有调用真实分类模型或主模型，没有访问真实搜索、招聘门户或岗位表。已有 24 条触发集曾用于规则开发，不能视为独立留出集，更不能推算真实用户请求的准确率。

### 范围分类

运行 `scripts/evaluate_triggers.py`，对“是否属于产品范围”计分；缺必填项但 in_scope=true 算范围识别正确，随后仍由代码追问而不进入研究。规则未决会返回固定澄清：正例未决计入误拒，同时单列未决数。分母为零时指标输出 null。

| 指标 | 原有 trigger_eval_set（24 条） | 新增上下文集（12 条） |
| --- | ---: | ---: |
| 正例 / 反例 | 12 / 12 | 7 / 5 |
| 跑题漏放 | 0/12（0%） | 0/5（0%） |
| 误拒 | 0/12（0%） | 1/7（14.29%，四舍五入） |
| 规则未决 | 0/24 | 2/12 |

原有触发集无误判。新增上下文集的误判逐条如下：

| 编号 | 输入与上下文 | 期望 | 实际 |
| --- | --- | --- | --- |
| 12 | “请围绕这个机会做一下预演”；已有腾讯 / 后端开发 / 校招锚点 | 范围内追问 | 规则未决，未配置分类模型时固定澄清，计误拒 |

另一条规则未决为编号 8：“继续做饭”，同样带上述面试锚点；固定澄清而不放行，范围判断与反例标签一致。没有为了消除未决而给分类器添加该句专用规则。

完整逐条结果保存在被 gitignore 的 `local_eval/jobscout_stage3/trigger-evaluation.json` 和 `context-evaluation.json`，脚本终端只打印汇总。模型回退路径的 mock 测试只验证配置、超时、结构和数据通道，不计作真实模型分类效果。

### 前后边界

| 行为 | 阶段 2 | 阶段 3 |
| --- | --- | --- |
| 跑题、缺字段入口 | 主要依赖 SKILL.md；主图已装配 | 外层图固定回复；测试确认不创建主 agent |
| 多轮目标 | 提示词及对话历史 | 服务器 checkpoint 锚点；full / delta 重建恢复；切换目标隔离旧报告 |
| Base 工具 | 提示词与技能限制 | 只实际注册 read_file；绑定及执行白名单 |
| 改 assistant 绕过 | 无专属线程绑定 | 开启 JOBSCOUT_ENFORCE_THREAD_BINDING=1 后服务器强制专属入口 |
| 真实分类误判率、端到端耗时、token、费用 | 待运行 | 待运行 |

前后未执行真实模型 A/B；表中的行为变化来自代码检查和 mock 集成验证，没有把“之前没有代码分类器”写成某个臆造的漏放率。

### 验证结果

| 检查 | 实际结果 | 范围与限制 |
| --- | --- | --- |
| 前端 `node jobscout-web/test_pure.js` | 通过 | 固定入口回复的实际 SSE / 历史展示、专属 assistant 请求，以及原有报告打印、下载和 tracker 回归 |
| 技能脚本 unittest | 18 项通过 | 比阶段 2 新增 2 项；未决、误拒及漏放分母和逐条明细 |
| 后端专项 pytest | 235 项通过，45.78 秒 | 比阶段 2 新增 33 项：32 项入口/工具/图集成测试，1 项技能契约；包含 full/delta 恢复、旧目标隔离、伪造状态、配置及超时、三路研究、只读简历计分、归属隔离、资源失效与绑定时机 |
| Gateway / 线程 / 仓库说明专项回归 | 344 项通过，26.35 秒 | 198 项 Gateway services，134 项线程路由与 checkpoint 模式，12 项说明文件规范检查 |
| 后端全量离线 pytest 尝试 | 1840 通过、1 失败、30 跳过、3 取消选择；239.13 秒 | `-x` 在既有 `test_boxlite_shim_workaround_retries_after_fixing_permissions` 的 Unix 权限位断言停止，不是全量通过 |
| blocking-I/O pytest | 129 通过、16 失败、1 跳过；29.32 秒 | 与阶段 2 相同的 Windows chmod、长路径、缺少 Lark CLI；未改上游相关实现 |
| 修改的 Python Ruff 与格式检查 | 通过 | 应用、Gateway 接入、测试、离线执行器与新增评测脚本 |
| 上述离线 pytest 进程的外网拦截触发数 | 0 | 仍为进程内网络防护，不是系统级沙箱；GitHub 同步属于用户授权的独立操作 |
| 真实模型分类效果、业务质量、token 与费用 | 待运行 | 不用 mock 指标替代 |

最终结果前已修复新增代码/集成问题：delta 节点状态类型冲突、简历虚拟路径与证据校验不一致、被拒运行提前写入绑定、资源在分类后失效，以及新增说明超出仓库字节预算。通用 Gateway 的元数据降级回归由 JobScout 部署开关隔离解决；没有修改上游测试预期来掩盖失败。第一次全量尝试因说明字节预算失败而停止，压缩后重跑得到上表结果。

部署方式与约束见 [entry-routing.md](entry-routing.md)。本地 gitignore 的 `.env` 已设置严格线程绑定开关；没有重启 Gateway，实际服务生效待重启确认。本轮未新增模型配置，也没有自动选择付费模型。
