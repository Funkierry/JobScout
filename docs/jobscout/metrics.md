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
