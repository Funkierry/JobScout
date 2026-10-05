# 部署 JobScout：从新机器到自己的求职工作台

这套入口适合**个人自托管或可信小范围使用**：网页、Gateway、数据库与浏览器登录态都运行在你自己的实例中。它不是已经完成租户隔离和运营配套的公开 SaaS。

## 1. 第一次启动

准备 Docker Engine + Compose v2.20 或更新版本（Windows/macOS 可用 Docker Desktop 的 Linux 容器），以及 Python 3.10+。宿主机不需要安装 Node、uv 或 Playwright；镜像构建会安装锁定的依赖与对应 Chromium。

```bash
git clone https://github.com/Funkierry/JobScout.git
cd JobScout
python scripts/jobscout.py init
```

编辑新生成的 `.jobscout/.env`，填写：

```dotenv
JOBSCOUT_MODEL=你的模型标识
JOBSCOUT_MODEL_API_KEY=你的模型API密钥
JOBSCOUT_MODEL_BASE_URL=https://你的服务商/v1
```

模型需支持 OpenAI 兼容的 Chat API 与工具调用。默认关闭视觉输入；使用其他供应商适配器、思考参数或上下文窗口时，修改 `.jobscout/config.yaml`，参考[模型配置](../../backend/docs/CONFIGURATION.md)。初始化不会读取或覆盖原来的根目录 `.env`、`config.yaml`，再次运行也不会重置已有部署文件或密钥。

```bash
python scripts/jobscout.py doctor
python scripts/jobscout.py up
```

首次启动需要下载镜像并编译 Next.js，明显慢于后续启动。`up` 等待健康检查通过后才打印成功；失败时显示容器状态和近期日志。`doctor` 检查必填项、Docker 与 Compose 配置，不会调用模型，也不能验证服务商余额或模型质量。

打开 **http://localhost:2026**。新实例会直接显示“创建你的管理员账号”；创建完成后进入 JobScout。公开注册默认关闭，已有实例显示登录入口。

## 2. 为什么保留四个服务

| 服务 | 职责 | 宿主机访问 |
|---|---|---|
| nginx | JobScout 页面、API 与流式消息的统一入口 | 默认 `127.0.0.1:2026` |
| gateway | 业务接口、Agent、SQLite 与 Playwright | API 不单独发布 |
| frontend | 上游能力中心，管理飞书连接和管理员设置 | 经同一入口的 `/workspace/capabilities` |
| redis | 运行事件流 | 不发布端口 |

浏览器访问 `/api/*`，不再请求访问者电脑上的 `localhost:8001`。保留上游能力中心后，飞书授权不需要手工拼接接口。Gateway 固定一个 worker，使取消、浏览器会话和运行状态保持在同一进程中。

`/api/langgraph/*` 转发到 Gateway 的兼容运行接口；其他 `/api/*` 直接转发。代理关闭流式缓冲，上传上限为 100 MB。后端自身的格式、文件大小与权限检查仍生效。

## 3. 服务器部署与首次访问

在服务器上执行上述命令后，从自己的电脑建立隧道：

```bash
ssh -L 2026:127.0.0.1:2026 user@your-server
```

然后在自己的浏览器打开 `http://localhost:2026`，先创建管理员账号。默认端口绑定回环地址，适合直接通过 SSH 私用，也适合交给服务器上的 HTTPS 反向代理。

需要域名时，在自己的 TLS 反向代理中将站点转发到 `127.0.0.1:2026`，保留原始 Host，并由可信代理设置 `X-Forwarded-Proto: https`。例如宿主机 Caddy：

```caddyfile
jobs.example.com {
    reverse_proxy 127.0.0.1:2026
}
```

将域名替换成自己的域名并完成 DNS 配置。使用同域代理无需额外开启 CORS；分离部署时，浏览器的 `gatewayBase`、Gateway 的 `GATEWAY_CORS_ORIGINS` 及 Cookie 的站点限制需要一起处理。[Caddy 转发说明](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers)。

## 4. 招聘网站需要登录时

**服务器上的 Chromium 不会自动出现在你电脑上。** 默认使用无头检查；页面需要登录时，记录显示“需登录”，不会把登录页误判成投递结果。

需要人工登录时，编辑 `.jobscout/.env`：

```dotenv
JOBSCOUT_BROWSER_LOGIN=1
```

再运行 `python scripts/jobscout.py up`。镜像已包含 Xvfb、x11vnc 与 noVNC，启动后通过以下隧道访问：

```bash
ssh -L 2026:127.0.0.1:2026 -L 6080:127.0.0.1:6080 user@your-server
```

1. 打开 `http://localhost:6080/vnc.html`，点击连接。
2. 回到 JobScout，添加个人申请列表链接，或单独刷新一条需要登录的记录。
3. 在远程浏览器中完成登录或验证码，等待检查继续；随后在 JobScout 查看结果和原文。
4. 登录态保存在数据卷中；批量检查复用已有登录态。过期后重新进行一次单条检查。

该桌面是**实例所有者的维护入口**，不是每个网站用户隔离的浏览器。它使用 SSH 提供访问认证，始终绑定宿主机 `127.0.0.1:6080`；不要把它转发到公开域名或开放给不可信用户。多人独立浏览器接管仍需要单独设计。

## 5. 配置在哪里，数据在哪里

| 位置 | 内容 | 更新时如何处理 |
|---|---|---|
| `.jobscout/.env` | 模型参数、固定认证密钥、端口、时区 | 保留；不提交 Git |
| `.jobscout/config.yaml` | 模型、工具、JobScout 中间件、数据库和注册策略 | 按需修改后重启 |
| `.jobscout/extensions_config.json` | 可由 Gateway 更新的扩展配置 | 保留；以可写方式挂载 |
| `.jobscout/runtime-config.js` | 公开的网页连接地址 | 只能放公开配置，不能放密钥 |
| `jobscout_jobscout-data` 数据卷 | 账号、会话、投递、上传文件与浏览器登录态 | `down` 保留该卷 |
| `jobscout_redis-data` 数据卷 | Redis 事件数据 | `down` 保留该卷 |

部署使用独立的新数据卷，不会自动迁移本机已有账号。JobScout 投递数据仍使用专门的 SQLite 存储；把上游数据库改成 PostgreSQL，不代表投递模块已经完成数据库迁移。

```bash
python scripts/jobscout.py status
python scripts/jobscout.py logs
python scripts/jobscout.py backup
python scripts/jobscout.py down
```

`backup` 会暂停 Gateway 写入，打包完整 `/data`，再恢复 Gateway。备份在 `.jobscout/backups/`，其中包括登录态和个人材料，应按私有文件保存。另行备份部署 `.env` 与配置文件，并保持认证密钥稳定。普通 `down` 不删除数据；不要为例行升级添加 `-v`。

更新代码后再次运行 `python scripts/jobscout.py up` 即可重建并检查健康状态。更新前先备份；有数据库迁移的版本应结合对应变更说明安排回退。

## 6. 可选功能与费用

- **飞书匹配：** 点击“管理连接”进入同域能力中心，完成应用配置和个人授权后，再提交有权限的 Base/Wiki 链接。
- **邮件：** Gmail/IMAP 仍是手动配置的只读集成，见[邮件配置](mail-channel.md)。不要把本机凭据路径原样搬进容器。
- **定时刷新：** 默认关闭，需同时启用服务端调度和用户自己的频率、每日额度，见[调度说明](scheduled-refresh.md)。
- **模型与搜索：** 日常调研、部分解析回退可能产生模型费用。默认提供单次 Token 预算和调用并发限制，它们不是账户总消费上限。搜索默认使用 DDG，网页读取使用 Jina；可按自己的网络环境更换提供方。

## 7. 排查常见问题

| 现象 | 先检查 |
|---|---|
| `doctor` 找不到 Docker | 安装/启动 Docker，并切换到 Linux 容器 |
| 健康检查失败 | `logs` 中的 Gateway 配置错误；模型环境变量是否完整 |
| 网页访问者仍请求 localhost | 检查 `/runtime-config.js` 是否为部署生成的同域配置，刷新缓存 |
| 账号初始化后仍不能注册 | 默认关闭公开注册，这是配置行为；使用已创建的管理员登录 |
| 招聘页一直显示“需登录” | 启用私有桌面、建立隧道，再进行一次单条检查 |
| HTTPS 登录报 403 | 外层代理是否保留 Host 并正确设置转发协议 |
| 研究报告没有来源 | 检查搜索、网页读取和模型工具调用是否可用，信息不足的部分不会补写为事实 |

## 8. 如何验证部署改动

[JobScout deployment CI](../../.github/workflows/jobscout-deploy.yml) 在临时 Linux 环境构建四个服务，检查入口、管理员初始化、Cookie/CSRF、关闭注册、目标岗位保存、能力中心、Chromium 启动，以及容器停止再启动后的账号持久化。测试使用合成账号，不调用模型或真实招聘网站。

`scripts/jobscout_smoke.py` 会创建合成管理员，仅供全新、可丢弃的本地测试实例；不要在自己的正式实例运行。功能回归和离线评测见 [README](../../README.md#开发与验证)。

容器健康不等于模型研究质量或真实招聘网站兼容性。个人部署完成后，分别验收一次自己的岗位检查、面试准备和飞书读取，才能确认所选服务商与站点组合可用。
