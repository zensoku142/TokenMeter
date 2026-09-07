# Gemini CLI 与 Claude Code 额度接入（2026-09-05）

本次属于功能新增与功能优化：新增 Gemini CLI 额度适配器，将 Claude Code 从仅支持状态栏快照扩展为默认只读本机 OAuth 登录。

## Gemini CLI

使用 Google 自己的 Gemini CLI 源码定义的 `retrieveUserQuota`：HTTPS POST 到 `cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`，Bearer OAuth 认证，请求包含 `project`。响应 `buckets[]` 提供 `modelId`、`remainingFraction` 和 `resetTime`；缺失百分比时不使用剩余次数推算总量。[Google CLI 请求实现](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/server.ts) · [第一方请求/响应类型](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/types.ts)

配置方式：

- 留空 `GEMINI_ACCESS_TOKEN`：读取 `~/.gemini/oauth_creds.json`。使用不同文件时填写 `GEMINI_CREDENTIALS_FILE`。
- 也可显式填写本人 OAuth `GEMINI_ACCESS_TOKEN`；此时不读取本机凭据。
- `GEMINI_PROJECT_ID` 优先；留空时通过只读 `loadCodeAssist` 发现项目，兼容字符串及对象两种项目响应。不调用 onboarding、不创建项目、不枚举云项目，也不猜测未返回的项目。
- OAuth 文件中的毫秒时间戳过期时提示回原 CLI 登录，不刷新或写回 `refresh_token`。本机 CLI 若已切换到 API Key/Vertex 模式，不继续使用遗留 OAuth 文件。

模型出现多个额度桶时，显示该模型最低剩余额度，并使用同一桶对应的重置时间；保留未知模型，不写死免费请求总数或窗口时长。此映射已对照 [CodexBar Gemini 实现](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/Gemini/GeminiStatusProbe.swift) 核对。

适用范围是仍由 Code Assist 提供的 Gemini CLI OAuth 账号，属于实验性客户端接口；不是 AI Studio API Key 余额，也不是 Vertex AI 项目用量。Google 明确说明个人免费版、Google AI Pro/Ultra 自 2026-06-18 起迁往 Antigravity。响应出现迁移信号时显示专门提示，不伪造成功或替用户开启其他供应商。[Google 发布说明](https://docs.cloud.google.com/gemini/docs/release-notes)

## Claude Code

Claude 官方文档明确 Windows/Linux 登录文件为 `.claude/.credentials.json`，`CLAUDE_CONFIG_DIR` 可改变目录；`claude setup-token` 为仅推理权限，不能代替额度查询所需的 `user:profile`。[Claude 认证文档](https://code.claude.com/docs/en/authentication)

选择顺序：

1. 显式填写 `CLAUDE_STATUSLINE_FILE`：保留原状态栏路径、15 分钟有效期与错误行为。
2. `CLAUDE_ACCESS_TOKEN`：显式 OAuth，或 `CLAUDE_CREDENTIALS_FILE` 指定的登录文件。
3. 默认 `.claude/.credentials.json` 内 `claudeAiOauth`；尊重 `CLAUDE_CONFIG_DIR`。
4. 仅默认登录文件不存在时回退默认状态栏快照。已选择的登录过期、无权访问、格式错误时不会回退到可能属于另一账号的快照。

OAuth 路径固定 GET `https://api.anthropic.com/api/oauth/usage`，包含 `anthropic-beta: oauth-2025-04-20`，采用 CodexBar 已使用的兼容客户端 User-Agent。解析 5 小时、每周、Sonnet/Opus、Routines 及 `limits[].weekly_scoped`；无利用率的窗口保持缺失。此接口是客户端内部额度接口，尚无公共第三方稳定性承诺。本次不接入组织 API 费用、额外用量扣费或 Web Cookie。[CodexBar OAuth 请求和响应类型](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/Claude/ClaudeOAuth/ClaudeOAuthUsageFetcher.swift) · [CodexBar Claude 接入说明](https://github.com/steipete/CodexBar/blob/main/docs/claude.md)

两个适配器只读取自有登录与额度，不执行推理，不修改登录文件。传输禁止重定向，错误消息不包含响应正文或凭据。账号缓存使用令牌与文件路径的不可逆指纹；Gemini 额外包含项目；Claude OAuth 与状态栏有不同身份与 `source` 标签。

## 已执行验证

- `python -m pytest tests/test_claude_provider.py tests/test_gemini_provider.py -q`：120 项测试、30 个子测试通过。
- `python -m ruff check api/providers/claude.py api/providers/gemini.py`：通过。
- `python -m pyright api/providers/claude.py api/providers/gemini.py`：0 错误、0 警告。
- 测试使用合成 JSON、本机临时目录和模拟 HTTP；没有读取真实凭据、请求真实账号、改写 OAuth 登录或使用重置卡。

仍需真实授权账号联调验证供应商实际返回；接口变化和账号权限差异会以明确错误返回，不能由离线测试保证远端永不变化。
