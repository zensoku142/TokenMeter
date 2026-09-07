# CodexBar 对照与本轮额度适配（2026-09-05）

本轮属于功能新增和界面优化。新增 Kimi Coding、MiniMax Token Plan、ElevenLabs 和 Gemini CLI，总计 14 个独立平台入口；Claude Code 新增本机 OAuth 直读。各适配器只执行额度/订阅读取，不调用推理、语音或生成接口。

## 对照结果

检查时，CodexBar 的 [Provider 清单](https://github.com/steipete/CodexBar/blob/main/docs/providers.md) 声明注册 69 个 Provider ID；同一公司可有多个凭据与计量方式独立的产品入口。部分入口仅验证部署状态或密钥可用性，不能直接等同于 69 个完整额度监控实现。

本项目仍有明确覆盖差距，尤其是 Antigravity、Windsurf、Alibaba / Qwen、JetBrains AI、Kiro 和其他浏览器会话渠道。Gemini CLI 现已提供实验性接入，其消费账号迁移限制见[专门说明](gemini-quota-2026-09-05.md)。

## 本轮实现

| 产品 | ID / 类 | 凭据与默认地址 | 实际读取内容 |
| --- | --- | --- | --- |
| Kimi Coding | `kimi` / `KimiProvider` | `KIMI_API_KEY`；`KIMI_BASE=https://api.kimi.com/coding/v1` | `GET /usages`，Bearer Key；周额度及实际返回的短周期窗口、重置时间 |
| MiniMax Token Plan | `minimax` / `MiniMaxProvider` | `MINIMAX_API_KEY`；`MINIMAX_BASE=https://api.minimax.io/v1` | `GET /token_plan/remains`，Bearer 订阅 Key；各服务的周期/周额度、无限量与不在套餐中状态 |
| ElevenLabs | `elevenlabs` / `ElevenLabsProvider` | `ELEVENLABS_API_KEY`；`ELEVENLABS_BASE=https://api.elevenlabs.io/v1` | `GET /user/subscription`，`xi-api-key`；字符积分已用、上限、重置时间与套餐名称 |
| Gemini CLI | `gemini` / `GeminiProvider` | 本机 CLI OAuth 或 `GEMINI_ACCESS_TOKEN`；可选 `GEMINI_PROJECT_ID` | Google Code Assist 模型额度、项目发现与重置时间；详见[接入说明](gemini-quota-2026-09-05.md) |

主面板的“＋”入口和设置共享品牌卡片选择器：中英文/别名搜索，默认优先已配置平台；开始输入后搜索全部平台，避免隐藏尚未配置的品牌。星形或 `Ctrl+D` 收藏，标题栏最多显示五个纯图标快捷入口，当前平台始终可见并高亮；完整收藏可在“常用平台”中找到。列表保持固定高度并支持滚动，`Enter` 切换、`Esc` 关闭，滚动设置页不会误切平台。

品牌 SVG 在本地随程序打包，可离线使用并适配明暗主题及高清缩放。图标来自固定版本的 Lobe Icons，保留 MIT 许可和[逐项来源](../assets/providers/README.md)。Nayuto 暂用通用连接标识，未冒充官方 Logo。

峰谷提示显示在今日金额下方，不再占用平台切换栏。已验证 640px 窄窗口和 820px 常规窗口；本地明暗主题截图使用演示数据，不随源码发布。

初次接入阶段验证：无界面全量 **1320 passed、3 skipped、108 subtests passed**；三项跳过的 Windows 原生焦点测试另行运行并通过。窄窗口调整后相关测试 **61 passed、1 skipped**。后续界面与可靠性改动继续通过回归验证。新增适配器、品牌资产、选择器、翻译和打包配置已纳入检查；未使用真实凭据进行远端联调。

MiniMax 国内站使用 `https://api.minimaxi.com/v1`，密钥须与站点匹配。没有自动跨区域重试，避免将一个站点的凭据发送到另一个站点。MiniMax `sk-api-*` 按量付费密钥不能用于 Token Plan 额度入口。

### Kimi 的来源与计量边界

第一方 [Kimi CLI usage.py](https://github.com/MoonshotAI/kimi-cli/blob/main/src/kimi_cli/ui/shell/usage.py) 明确通过 Bearer Key 查询 `/usages`，将顶层 `usage` 标记为周限额，并通过每条 `window.duration/timeUnit` 解释其他周期；`used` 缺失时可从 `limit-remaining` 计算。其 [官方论坛说明](https://forum.moonshot.ai/t/error-code-429-were-receiving-too-many-requests-at-the-moment/191/7) 将这个端点描述为实验性接口，不能视为稳定的公开账单合同。

本实现只显示比例与接口给出的重置时间，不将配额数字标成 Token 或请求次数。只返回周额度时不会制造 5 小时窗口。该入口与已有 Moonshot / Kimi API 余额入口分开，未读取本机登录文件或自动刷新 OAuth。交叉参考 [CodexBar Kimi 说明](https://github.com/steipete/CodexBar/blob/main/docs/kimi.md)。

### MiniMax 的来源与计量边界

第一方 [CLI endpoint 定义](https://github.com/MiniMax-AI/cli/blob/main/src/client/endpoints.ts) 当前使用 `/v1/token_plan/remains`；[CLI 响应类型](https://github.com/MiniMax-AI/cli/blob/main/src/types/api.ts) 定义了百分比、计数、状态、时间和周加成字段。

第一方 [quota 数值解释](https://github.com/MiniMax-AI/cli/blob/main/src/utils/quota.ts) 说明 `*_usage_count` 命名有歧义：旧返回值为剩余数，新返回值也可能表示消耗数。有 `*_remaining_percent` 时本实现直接采用明确的剩余百分比；只有没有百分比时，才按官方 CLI 的兼容规则将计数视为剩余，不把它误当成已用。

根据第一方 [quota-table.ts](https://github.com/MiniMax-AI/cli/blob/main/src/output/quota-table.ts)，两个窗口均为状态 3、总数均为 0 时表示服务不在套餐内；不能渲染为免费无限量。周加成另列详情，进度保留基础额度的已用比例。重置时间按合同中的毫秒时间戳解析，不根据时间数字大小猜单位，也不伪造未返回的重置时间。交叉参考 [CodexBar MiniMax 实现](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/MiniMax/MiniMaxUsageFetcher.swift)。

### ElevenLabs 的来源与计量边界

按第一方 [Get user subscription](https://elevenlabs.io/docs/api-reference/user/subscription/get/) 实现，使用 `character_count` 与 `character_limit`，重置时间为 Unix 秒。字符积分不等于 LLM Token；超额计费和扩展额度不计入免费订阅上限。超额时进度封顶，但原始已用数量保留；上限为零显示无可用订阅额度，不显示虚假的正常 0%。

### 本轮没有添加 Alibaba 空入口的原因

[CodexBar Alibaba 文档](https://github.com/steipete/CodexBar/blob/main/docs/alibaba-coding-plan.md) 自己注明 API Key 模式在部分账号/区域返回 `ConsoleNeedLogin`；受支持的基线是浏览器会话 RPC，涉及 Cookie 与 `sec_token`。本轮未实现该会话流程，不将仅能打开控制台的入口计作额度监控。

## 验证与已知限制

- 本轮新增的 4 个测试文件共 225 项测试通过；新 Provider 与测试文件的 Ruff 检查通过，4 个生产文件的 Pyright 为 0 errors / 0 warnings。
- 测试覆盖成功响应、周期单位、剩余/已用方向、未知与零额度、超额、无限量与不在套餐内、毫秒/秒/纳秒时间、缺失字段、`None`、布尔值、NaN、Infinity、超大数、401/403、429、超时、TLS 异常、非 JSON、API 错误、重定向、无效地址与缓存重置。
- 请求沿用项目 HTTPS 会话，不关闭 TLS 验证；设置超时并禁止跟随重定向。错误消息不包含密钥、异常正文或原始 API 错误正文；会话由 Provider 关闭。
- 全部 fixture 为合成数据，未读取真实凭据，未访问真实账号，未进行生产账号联调。接口或套餐结构可能变化，尤其 Kimi 的实验性端点；不能保证所有账号行为或绝对零 Bug。
- 依据公开 API 合同独立编写 Python 实现与 fixture；参考并归因 CodexBar、MoonshotAI Kimi CLI 与 MiniMax 官方 CLI。未复制其实现代码、图标或真实账号 fixture。

执行命令：

```text
.venv/Scripts/python.exe -m pytest tests/test_quota_api_provider.py tests/test_kimi_provider.py tests/test_minimax_provider.py tests/test_elevenlabs_provider.py -q
.venv/Scripts/python.exe -m ruff check api/providers/quota_api.py api/providers/kimi.py api/providers/minimax.py api/providers/elevenlabs.py tests/test_quota_api_provider.py tests/test_kimi_provider.py tests/test_minimax_provider.py tests/test_elevenlabs_provider.py
.venv/Scripts/python.exe -m pyright api/providers/quota_api.py api/providers/kimi.py api/providers/minimax.py api/providers/elevenlabs.py
```
