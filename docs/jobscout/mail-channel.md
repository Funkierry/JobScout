# 招聘邮件：只读接入与证据合并

阶段 6 方案已确认并实现。默认 `enabled=false`，未配置任何真实邮箱；没有发送、删除或修改邮件的功能。开发仅使用合成 MIME 和 mock transport，真实效果待运行。

## 接入与本地配置

Gmail 使用安装应用 OAuth、随机 state、PKCE 与 `gmail.readonly` scope；客户端只读取消息，token 交换不写邮箱。IMAP 使用 TLS 993、只读 SELECT（EXAMINE）和 `BODY.PEEK`，不标记已读、不执行 CLOSE/EXPUNGE/STORE。IMAP 只读会话不等于服务商提供只读凭据：使用专用授权，并在本地设置 `imap_readonly_acknowledged=true` 才启用。

凭据保存到 DeerFlow 本地数据目录的 `jobscout-mail/<用户哈希>/config.json`，不进 Git、不返回浏览器、不打印正文、token、主题或邮件 ID。Windows 文件权限继承本机用户目录 ACL；POSIX 创建文件为 0600。默认 `.deer-flow/` 已 gitignore，不要将自定义数据目录放入受 Git 跟踪的位置。

从仓库根目录创建关闭状态的配置：

```powershell
backend/.venv/Scripts/python.exe -B backend/scripts/application_tracker_mail.py init --user-id <当前登录用户ID>
```

Gmail 安装应用 client JSON 放在 `local_eval/mail/`，用户需要接入时自行执行：

```powershell
backend/.venv/Scripts/python.exe -B backend/scripts/application_tracker_mail.py authorize-gmail --user-id <用户ID> --client-secrets local_eval/mail/client.json --allow-network
```

授权后仍关闭同步。编辑本地配置，设置 `sender_domains` 为实际招聘发件域名、`enabled=true`。域名精确匹配，不自动允许子域名，不将显示名当身份。白名单是筛选条件，不能证明发件人未被伪造；应通过官方招聘渠道确认重要录用结论。

IMAP 本地配置需要 `provider=imap`、`imap_host`、`imap_username`、`imap_password`、`imap_readonly_acknowledged=true`，以及同样的域名白名单和启用开关。密码不能粘贴到聊天、网页表单或命令行参数。

用户主动同步命令：

```powershell
backend/.venv/Scripts/python.exe -B backend/scripts/application_tracker_mail.py sync --user-id <用户ID> --allow-network
```

也可在“进度追踪 → 招聘邮件”点击同步。配置接口只返回启用状态、provider、域名数量和数量上限。初版按回看窗口扫描并以 provider/message ID 哈希幂等去重，不声称已实现全邮箱增量游标。

## 筛选和抽取

先按发件域名和时间范围搜索，再按精确发件域名＋主题招聘关键词筛选，合格才读取正文。默认回看 30 天、每次检查最多 30 封（最多可配 100），单封原始 MIME 不超过 1 MB，解析正文不超过 256,000 字符。窗口内超过上限的邮件需要后续扩大配置或缩小筛选范围；不宣称同步完整。

不读取附件，不请求图片/远程资源；忽略 HTML 脚本、引用块和常见转发历史。初版采用严格模板识别笔试、面试邀请、正式录用与明确拒信，无模型成本。不明确的邮件保留待核对结果，不能把规则覆盖率当真实准确率。

事件保存公司、岗位、类型、状态、收件时间、可核实的安排时间、逐字引文和待核对原因。公司和岗位必须唯一匹配同一用户的已有申请，并在邮件中找到原文。邮件出现多家匹配公司、多个事件、否定语句或仅有历史引用时不自动确认。普通面试邀请显示“面试（轮次待确认）”，不推断一面或下一轮。时间只解析明确绝对日期＋时分；默认时区 Asia/Shanghai，可在本地配置，日期不全时留空。

## 与官网合并

`jobscout_mail_events` 保存独立事件；原有 `applications`、检查历史和手动环节不被邮件覆盖。接口增加 `source_summary`，前端按派生结果展示，并保留官网详情、邮件原文及两种时间。

邮件收件时间是“收到该通知”的时间，安排时间可以在未来；官网 `checked_at` 是观察时间，均不伪装成官方阶段开始时间。只有更新且更具体的邮件结论才覆盖展示；官网未知时采用可核实邮件。较旧邮件与较新官网冲突、同刻不同邮件结论或终态冲突时保留官网并标记来源冲突，不按招聘阶段编号推测高低。有效邮件 Offer/拒信视图可停止后续非终态自动刷新。手动阶段继续优先显示。

查询始终按当前登录用户限定；删除申请级联删除关联事件。未匹配邮件在该用户的待核对列表中，初版通过原邮件与现有手动环节功能人工核对，不自动创建猜测的申请。

## 验证

合成测试覆盖只读协议、凭据不返回、严格域名、关闭时无网络、附件/历史排除、伪造证据、否定与多事件、模糊岗位、重复同步、用户隔离、日期与收件时间区别、终态冲突及界面派生状态。运行结果见 [metrics.md](metrics.md)；真实邮箱准确率、覆盖率及收件延迟均待运行。
