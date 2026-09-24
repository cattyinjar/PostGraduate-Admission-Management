# 个人版研究生招生信息监视系统

这是一个面向个人用户的 Windows 桌面程序，用于监视高校和科研院所公开招生的静态网页与 RSS/Atom 源。系统自动识别新通知，抓取详情正文，调用 OpenAI-compatible LLM 生成中文结构化摘要，并通过 SMTP 发送邮件提醒。

## 核心能力

- 分条管理监视任务，支持启用、暂停、编辑、删除和手动检查。
- 支持静态 HTML 通知列表与 RSS/Atom；可通过高级 CSS 选择器精确适配异常页面。
- 首次运行只建立 baseline，不会把历史公告全部当作新消息。
- 新消息与详情正文变化会生成持久化事件，程序重启后不会重复通知。
- LLM 摘要失败不阻塞邮件，会降级发送标题、原文链接和附件链接。
- 支持 TLS SMTP、HTML 邮件与纯文本 fallback，并提供测试邮件。
- 本地 SQLite 存储，密钥保存在 Windows Credential Manager。
- 最小化到系统托盘后继续监视，真正退出前会确认。

## 安装与运行

需要 Python 3.11 或更新版本。

```powershell
conda activate pgam
python -m pip install -r requirements-dev.txt
python run_app.py
```

如需以包入口启动：

```powershell
pgam
```

## 首次使用

1. 打开“设置”页，填写：
   - LLM Base URL、API Key、模型名；
   - SMTP 主机、端口、账号、密码、发件人和收件人；
   - 邮件主题前缀与默认检查间隔。
2. 使用“测试连接”和“发送测试邮件”验证外部服务。
3. 在“任务”页新增任务，填入公开列表页或 feed 地址。
4. 使用“识别预览”确认标题、链接和日期；自动识别不准时可填写 CSS 选择器。
5. 首次检查建立 baseline；之后发布的新通知会进入处理流水线。
6. 最小化窗口会保留在系统托盘；托盘菜单中选择“退出”才会停止调度。

## 数据与密钥

- Windows 数据库：`%APPDATA%\PostGraduateAdmissionMonitor\app.db`
- 快照目录：`%APPDATA%\PostGraduateAdmissionMonitor\snapshots`
- LLM API Key 与 SMTP 密码：Windows Credential Manager
- 日志与数据库不保存明文密钥。

## 架构

Verify all configuration and credential persistence:

```powershell
D:\anaconda\envs\pgam\python.exe -m pgam.testing.verify_config_persistence --mode verify
```

The command uses an isolated temporary SQLite database and isolated Windows Credential Manager test entries. It verifies all 13 global settings, task adapter settings, the LLM API key, and the SMTP authorization code across separate processes, then removes the test credentials.

```text
desktop/          PySide6 GUI、托盘、线程桥接
services/         应用服务、任务管理、调度与流水线编排
core/             领域模型与端口接口
sources/          HTTP 抓取、列表适配、详情抽取
intelligence/     OpenAI-compatible LLM 客户端
notifications/    SMTP 邮件通知
storage/          SQLite repository、迁移与凭据访问
testing/          localhost 测试平台与验收脚本
```

依赖方向保持为 `desktop -> services -> core`，外部实现通过 core ports 注入，便于替换和测试。

## 本地测试

```powershell
python -m pytest pgam\tests -q
python -m ruff check pgam
```

### 三站点基础检测平台

覆盖普通静态列表、GBK 表格和 RSS：

```powershell
python -m pgam.testing.verify_local_monitor
```

### 十六站点真实结构仿真平台

`pgam/testing/realistic_platform.py` 基于 16 个公开招生页面的服务端渲染结构，在 localhost 上创建互相独立的仿真站点。它不会监听真实网站，也不等待真实网站自然更新，而是主动发布合成新通知来验证检测能力。

覆盖的结构包括：

- 普通 `<ul><li><a>` 列表；
- `javascript:void(0)` 加 `data-val` 的伪链接；
- `onclick="window.open(...)"` 构成的可点击行；
- 日历卡片、表格、query 详情链接；
- 日期在链接内、括号内或兄弟节点中的页面；
- 大量目录链接干扰、多栏目表格干扰；
- GBK 编码页面。

执行完整验收：

```powershell
python -m pgam.testing.verify_realistic_monitor
```

验收流程：

1. 为 16 个 localhost 站点创建任务并建立 baseline；
2. 修改装饰性内容，确认不产生事件；
3. 每个站点发布 1 条新通知；
4. 确认 16 个任务都识别出 `new_item`；
5. 抓取并抽取 16 个详情页；
6. 确认事件停留在 `fetched`；
7. 确认不调用 LLM，不产生 summary；
8. 确认不调用 SMTP，不产生 notification；
9. 清理临时数据。

最近一次验收摘要：

```json
{
  "site_count": 16,
  "event_count": 16,
  "baseline_event_count": 0,
  "decorative_event_count": 0,
  "summary_records": 0,
  "notification_records": 0,
  "all_passed": true
}
```

结构映射与适配说明见 `docs/realistic-site-test-platform.md`。

To run these 16 realistic-structure sites through the live LLM and email pipeline, first ensure the project root contains:

```text
token.txt
email_account.txt
email_password.txt
```

Then run:

```powershell
D:\anaconda\envs\pgam\python.exe -m pgam.testing.verify_realistic_full_chain
```

This command makes 16 real DeepSeek calls, sends 16 real notification emails to `2710936865@qq.com`, and verifies that every event reaches `notified`. It consumes real API quota.

## Packaging and install verification

Build the official installer:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-installer.ps1
```

The script runs dependency checks, pytest, Ruff, PyInstaller, and Inno Setup. It generates:

```text
dist/installer/PGAM-0.1.0-x64.exe
```

Verify the installer:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-installer.ps1 -InstallerPath .\dist\installer\PGAM-0.1.0-x64.exe
```

The verifier performs silent installation, validates uninstall registration, launches the installed executable, creates and reopens persistent SQLite data under `%APPDATA%`, verifies task retention, uninstalls the program, and confirms user data remains preserved.

User data location:

```text
%APPDATA%\PostGraduateAdmissionMonitor
```
## 边界与后续方向

- 首版不处理登录、验证码、付费内容或需要模拟登录的教务系统。
- 不抓取 JavaScript 动态渲染页面；Source Adapter 已为后续 Playwright 支持预留接口。
- 不解析 PDF、Word、Excel 附件内容，只在邮件中保留附件链接。
- 首版是单用户本地桌面软件，不提供云部署、注册登录或多租户权限。
