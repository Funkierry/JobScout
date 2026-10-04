# Tracker 抓取与判定：阶段 4

仅作用于 `app/application_tracker/` 和 JobScout tracker 路由，不改变 DeerFlow 通用 Agent、工具或 scheduler。真实模型效果见 [metrics.md](metrics.md)，当前仍待运行。

## 观察与等待

- `PersistentAgentBrowser` 在导航、只读点击前监听页面的 XHR/fetch JSON。每个观察窗口默认最多 20 个响应、每个 256,000 字节；拒绝异站、非 2xx、非 JSON、超限和无法解析的响应。站点按规范化 hostname 精确匹配，跨子域接口目前也回退到 DOM，避免猜测域名信任范围。
- `observations.py` 与阶段 0 快照共用响应读取及脱敏函数。运行时再去除凭据、联系方式、描述、推荐及分析字段，限制嵌套深度、字段数、数组长度、单字符串长度，最多保留 30,000 字符 JSON。具有岗位和状态字段的对象保留独立边界；这是通用观察投影，不是阶段 5 的 ATS 状态适配器。
- JSON 是不可信数据。模型先看 JSON；抽取失败、未知或规则低分时再看 DOM，两个来源明确冲突时保留未知。不会将 HTTP 错误体当成申请状态。JSON 无法识别、缺少岗位字段或只有未解释状态码时仍可能需要 DOM 或人工核对。
- 导航等待 `networkidle` 或非空正文长度连续三次稳定，默认总上限 3 秒、采样间隔 200 毫秒。页内点击只使用新的正文稳定采样：浏览器可能仍保留点击前的 idle 状态。网络流和文字均不稳定时按总时限继续读取；这不是页面必定完整的保证。
- 从可见主内容读取文本，排除导航、页脚、推荐、脚本与隐藏元素，再限制为 50,000 字符。读取不改变 DOM 或点击 ref。DOM 脚本失败时兼容回退到有长度上限的正文读取。
- Playwright 的 `response.body()` 会先取得浏览器缓冲的完整 body。Content-Length 可提前拒绝，实际长度还会复检；无长度头时不能声称这是 Chromium 网络内存的硬上限。响应 body 等待另有 5 秒上限。

## 规则判定

`confidence.py` 为结构化抽取和 Agent `update_record` 共用入口。`confidence` 模型字段只为 schema 兼容保留，不参与最终评分。

规则检查原始状态词、逐字证据、岗位与证据是否处于同一记录、当前状态冲突，并区分 JSON/DOM 来源。匹配证据基础分为 0.4，状态词命中加 0.35，岗位对应加 0.1，JSON/DOM 来源分别加 0.1/0.05；宽泛进行中归类继续封顶 0.75。分数是启发式规则分，不是校准后的概率。状态词与模型枚举矛盾、证据错配或明确冲突返回未知；未命中词表会降低分数并进入补充观察。

列表中的岗位、状态和投递日期在记录边界内校验。截图仍可帮助导航，但无法逐字核验的截图文字不会写成已确认状态。未知或低分会继续只读导航；达到步数上限仍可能保留未知或低分结果，不承诺模型能补全缺失数据。

## 浏览器和批量刷新

交互式单条检查从开始就打开可见 context，复用同一个 context 完成登录、导航和读取。批量检查关闭人工登录并默认使用无头 context；失效记录返回“需登录”，可随后单条刷新处理。

`PersistentBrowserService` 也复用同一套生命周期。每个用户/站点 profile 的锁在同一事件循环内跨请求工厂共享；取消会关闭 context 并释放锁。不同进程的 profile 协调仍受 Chromium 自身锁约束。

批量任务按 hostname 分组，同组顺序执行，不同组默认并发 2。SSE 的 `index` 是稳定行号，`completed` 是实际完成数，不等于 `index`。关闭流会取消并等待所有 worker；单条失败不停止其他组。

| 配置 | 默认值 | 范围/含义 |
|---|---:|---|
| `APPLICATION_TRACKER_BATCH_CONCURRENCY` | 2 | 每批 1–8 个域名组 |
| `APPLICATION_TRACKER_INTERACTIVE_LOGIN` | true | 单条检查是否使用可见窗口和人工登录；批量强制关闭 |
| `APPLICATION_TRACKER_BROWSER_HEADLESS` | true | 非交互检查是否无头；交互检查始终可见 |
| `APPLICATION_TRACKER_SETTLE_TIMEOUT_MS` | 3000 | 1–30000 毫秒 |
| `APPLICATION_TRACKER_STABLE_POLL_MS` | 200 | 正数且不超过等待上限 |
| `APPLICATION_TRACKER_MAX_JSON_BYTES` | 256000 | 每响应 1–1000000 字节 |
| `APPLICATION_TRACKER_MAX_JSON_RESPONSES` | 20 | 每窗口 1–100 个响应；模型投影只取前 20 个 |

## 同数据回放（用户主动运行）

本阶段未调用真实模型、未访问真实招聘页面。现有 20 条辅助标签能与本地快照匹配，其中 16 条得到符合当前投影规则的同站 JSON，共 48 个观察对象（多个标签可共享同一快照，不能当成 48 条独立申请）。2 条待核对标签不参与比较。原标签文件保持不变，JSON 只在内存中关联；关联依据是正文 SHA-256 和脱敏后的最终 URL，重复匹配会报错。

先在阶段 3 提交 `458a284047214b0ce464a5f433f8d248ffe6128d` 的独立本地 checkout 中，使用旧基线脚本和同一份冻结标签生成旧代码报告。不要用本版本的 `--dom-only` 冒充旧代码基线：该参数仍然使用阶段 4 规则。

下面 PowerShell 命令从当前仓库根目录运行，**会调用模型并产生费用，仅由用户主动运行**。旧版本使用当前已安装的 Python 环境，无需安装依赖；旧 checkout 的配置由 `DEER_FLOW_CONFIG_PATH` 指向当前本地配置。

```powershell
$repo = (Get-Location).Path
git worktree add --detach "$repo/local_eval/stage3-baseline-code" 458a284047214b0ce464a5f433f8d248ffe6128d
$env:DEER_FLOW_CONFIG_PATH = "$repo/config.yaml"
New-Item -ItemType Directory -Force "$repo/local_eval/stage3-baseline-code/local_eval/tracker_snapshots" | Out-Null
Copy-Item -LiteralPath "$repo/local_eval/tracker_snapshots/labels.jsonl" -Destination "$repo/local_eval/stage3-baseline-code/local_eval/tracker_snapshots/labels.jsonl"
& "$repo/backend/.venv/Scripts/python.exe" -B "$repo/local_eval/stage3-baseline-code/backend/scripts/application_tracker_baseline.py" --report "$repo/local_eval/stage3-baseline-code/local_eval/tracker_snapshots/before-stage4.json" --allow-model-cost
& "$repo/backend/.venv/Scripts/python.exe" -B backend/scripts/application_tracker_baseline.py --snapshots local_eval/tracker_snapshots --report local_eval/tracker_snapshots/after-stage4.json --allow-model-cost
```

旧脚本要求输入输出位于其 checkout 的 `local_eval/`，所以命令复制冻结标签到该目录；复制后请核对两份文件的 SHA-256 相同。

两次显式指定相同 `--model`，采用相同配置及标签哈希。若需要成本，两次均传 `--input-usd-per-million` 和 `--output-usd-per-million`，使用自己账户的实际计费价格；缺少价格或 provider usage 时不估算成本。新报告旁保存代码版本、标签与观察哈希、输入模式及 JSON 覆盖数。

比较两个报告的 `status_accuracy`、`unknown_rate`、`verbatim_evidence_rate`、`mean_elapsed_ms` 和 token/成本。旧快照没有 DOM 结构，无法从 `body.txt` 还原降噪前后的浏览器时间；回放耗时只覆盖抽取和校验，完整浏览器耗时仍需用户单独真实运行。报告和 worktree 都在 gitignore 的 `local_eval/`，不得提交页面正文、标签或逐条预测。
