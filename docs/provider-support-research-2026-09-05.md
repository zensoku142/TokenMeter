# 主流 AI 额度监控接入研究（2026-09-05）

本次属于功能新增、Bug 修复与文档更新的接入准备。研究只访问公开官方文档与第一方开源客户端，未请求真实账户额度，未读取账户密钥。下列“可接入”表示有可验证的数据源，并不代表所有账号均已完成实测。供应商会调整接口与套餐，应以更新日志优先于旧 API 页面或旧客户端源码。

## 数据口径与当前架构

`api/providers/base.py` 已区分 `ProviderBalance`、`ProviderSummary` 和 `ProviderQuota`，本批次继续复用：

| 数据 | 正确含义 | 不应混用 |
| --- | --- | --- |
| 订阅额度 | 官方返回的窗口百分比、权益数量、重置时间 | 本地累计 Token、上下文长度、按 API 单价估算的费用 |
| API 账户余额 | 可用于账户 API 调用的货币余额 | 单个 Key 花费上限、套餐额度、赠送次数 |
| Token 使用 | 实际请求计量，必须标明账号、时间、统计范围 | 尚可使用的订阅资源 |
| 费用 | 官方已计费金额或明确标注的估算 | API 充值总额、订阅购买金额 |
| Key 预算 | 该 Key 被允许花费的上限与剩余额度 | 全账户可用余额 |

未知值必须保持未知；接口错误不能显示为 0。不同币种不得直接相加。比例的分母、已用/剩余方向、时间窗口和账号范围必须来自对应数据源。新增供应商只开启实际实现的能力，不为了显示图表填充虚构历史。

## 支持矩阵与实施顺序

| 顺序 | 产品与监控目标 | 最可靠入口 | 建议 |
| --- | --- | --- | --- |
| 1 | Moonshot / Kimi API 余额 | 公开余额 REST API | 首批接入；与 Kimi 会员分开 |
| 2 | OpenRouter Key 预算与费用 | `/key` | 首批只接普通 Key；账户 `/credits` 与 Management Key 留待后续 |
| 3 | Claude Code Pro/Max 订阅 | 官方 `statusLine` JSON | 首批通过本地快照接入 |
| 4 | Z.ai / 智谱 GLM Coding Plan | 官方用量插件使用的监控 GET | 首批可做有限接入；不猜未知时间窗口 |
| 5 | GitHub Copilot | 官方 SDK `account.getQuota`；第一方内部 entitlement API | SDK 优先；内部接口须标明兼容风险 |
| 6 | OpenAI API | Usage / Costs Admin API | 第二批，需要组织权限及 USD 历史费用路径 |
| 7 | Anthropic API / Claude Enterprise | 各自官方组织 Analytics API | 第二批；不能代替个人 Pro/Max 剩余额度 |
| 8 | Gemini CLI / Code Assist | 官方 CLI 的 `retrieveUserQuota` | 第二批；涉及 OAuth 与 project 生命周期 |
| 9 | MiniMax Token Plan | 官方 `mmx quota` | 第二批；先确认 CLI 版本与新旧套餐响应 |
| 10 | Gemini API / Google AI | AI Studio 用量、GCP 配额与账单 | 单独产品接入，避免与 Google AI 订阅混淆 |
| 11 | Windsurf / Devin Desktop | 账户 Plan Info / 官方套餐页面 | 等待稳定订阅读取接口或用户提供可核实快照 |
| 暂缓 | SiliconFlow 国内余额 | 旧 `/user/info` 已退役 | 不再接旧接口；等待官方替代接口 |

这是按数据可靠性与当前桌面项目改动成本排序的工程建议。不是供应商质量排名，也不承诺一次迭代覆盖全部市场产品。

## 1. Moonshot / Kimi API

公开 `GET https://api.moonshot.cn/v1/users/me/balance`，鉴权为 `Authorization: Bearer <API Key>`，返回单个对象，无分页。`data.available_balance` 是人民币可用余额；`voucher_balance` 与 `cash_balance` 分别是券和现金。现金允许负值，此时 `available_balance` 不一定等于二者相加，必须使用官方可用值。成功以业务 `code=0` 与 `status=true` 核验。国内和国际 Key 独立，不能混用。该接口不报告会员用量、历史 Token 或累计费用。[Kimi 查询余额](https://platform.kimi.com/docs/api/balance)

首批只提供余额，不把余额反推 Token 数。国际站已核实 `GET https://api.moonshot.ai/v1/users/me/balance`，可用余额单位为美元，使用国际站 Key。本轮通过 BASE 切换已确认的国内/国际主机，分别显示 CNY/USD。[国际 Kimi 余额接口](https://platform.kimi.ai/docs/api/balance)

## 2. OpenRouter

`GET https://openrouter.ai/api/v1/key` 使用普通 Bearer Key，返回该 Key 的 `limit`、`limit_remaining`、`limit_reset`、`usage`、按日/周/月 usage 及 BYOK 字段，无分页。`limit=null` 表示未设置 Key 上限，不能视为无限账户资金；`limit_remaining` 是 Key 预算余量。`usage` 是货币费用，不是 Token。不能把不同统计范围和 BYOK 字段简单相加。[当前 Key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key)

`GET https://openrouter.ai/api/v1/credits` 的当前官方文档明确要求 **Management Key**。响应 `total_credits` 为购买总额、`total_usage` 为消费总额，账户剩余为两者差额；无分页。普通 Key 的 403 不应被当作余额为零，也不能退回 Key 预算伪装成账户余额。OpenRouter credits 以美元计价。[账户 Credits](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits)、[计费 FAQ](https://openrouter.ai/docs/faq)

本轮仅实现 `/key` 的剩余预算与当日、本月、累计费用，界面明确为“密钥剩余额度”；没有接入 `/credits` 或 Management Key 配置。首批读取为 GET，禁止带密钥跟随跳转。账户总余额作为后续独立能力，不与当前 Key 预算混淆。

## 3. Claude / Claude Code

### 个人订阅：官方状态栏快照

Claude Code 官方 `statusLine` 输入包含 `rate_limits.five_hour` 和 `rate_limits.seven_day`，每个窗口有 `used_percentage` 与 Unix 秒 `resets_at`。目前该对象仅为 Claude.ai Pro/Max 用户在会话首次 API 响应后提供，各窗口可独立缺失。`context_window` 是上下文占用；`cost` 是会话成本统计，二者均不能用于推算订阅额度。[Claude Code 状态栏](https://code.claude.com/docs/en/statusline)

本项目的 `scripts/claude_statusline.py` 是纯 Python 标准库脚本，可单独放在固定目录。它只保存两个额度窗口、`observed_at`、`schema_version=1` 与用户可选的 `account_scope`，不保存输入中的 prompt、目录、transcript 路径或凭据。不增加 HTTP 请求。

配置示例：把下面的 `statusLine` 合并到已有 `~/.claude/settings.json`，将脚本和 Python 路径替换为本机实际位置。示例中的正斜杠可用于 Windows JSON 路径。项目不会自动覆盖用户的 Claude 设置。

```json
{
  "statusLine": {
    "type": "command",
    "command": "python \"E:/github/TokenSpider/scripts/claude_statusline.py\""
  }
}
```

默认输出 `~/.claude/tokenmeter-usage.json`。TokenMeter 的 Claude Code 设置中，“状态栏快照文件”留空即可读取该位置。自定义输出时使用 `--output "绝对路径"`，并在 TokenMeter 中配置同一文件。运行该脚本需要 Python 3.10 或更新版本；若 `python` 不在 PATH 中，命令中使用 Python 可执行文件的绝对路径。

已有状态栏命令的用户可以选择替换，或在原状态栏脚本读取 stdin 后，将同一 JSON 输入再传给本脚本，并保留原状态栏输出。不要串接成两个命令直接争读同一个 stdin；第一个会把输入消耗完。需要多账户时使用不同输出文件；可选 `--account-scope personal-account` 是用户自定的非敏感别名，**换账号时必须换 scope 并重新生成快照**。官方状态栏本身没有稳定账号标识，未指定 scope 时不启用持久快照缓存。

首次响应前的空窗口会覆盖上一份快照，避免显示旧会话数据。超过 15 分钟未更新时，Provider 返回 `STALE_DATA`，不把过期内容当作本轮成功结果。状态栏停止更新后，TokenMeter 不会主动查询 Claude，也不声称实时。`observed_at` 是本机收到状态栏输入的时间，不是服务器账单结算时间。改变账号后，必须等新会话生成额度再查看；同一文件不可供不同账号并发写入。

当前只覆盖官方输出的两个窗口，不声称覆盖 Claude 网页所有套餐、额外用量消费或组织报表。

### Anthropic API 与 Claude Enterprise

组织 API 用量可读取 `GET /v1/organizations/usage_report/messages`，费用可读取 `GET /v1/organizations/cost_report`，主机为 `api.anthropic.com`。官方示例使用 `x-api-key: <Admin API Key>` 和 `anthropic-version: 2023-06-01`。当前还支持文档指定的组织级凭据，但 workspace Key 不适用；个人账号无该 Admin API。时间参数为 `starting_at` / `ending_at`，分页为 `has_more` / `next_page` / `page`。费用只按日，金额是美元的 fractional cents，需要除以 100；不含 Priority Tier 费用。数据通常数分钟延迟，持续轮询建议每分钟最多一次。[Usage & Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)

Claude Enterprise 使用 Analytics Key 与另一套组织 Analytics 接口；例如 `/v1/organizations/analytics/cost_report`。这些组织费用不能当作个人 Pro/Max 剩余配额。[Enterprise Analytics Cost](https://platform.claude.com/docs/en/api/admin/analytics/cost)

## 4. GitHub Copilot

官方 Copilot SDK 已公开 `account.getQuota` RPC，响应 `quotaSnapshots` 包含 remaining percentage、used/entitlement requests 与 reset date。它使用当前连接的认证上下文，官方也记录按指定用户 token 查询的方式。适合后续复用官方运行时读取账户配额，无需生成对话来测量额度。[SDK usage and billing](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/usage-and-billing)

个人/组织/企业的已计费用量有官方 REST Billing Usage API，例如 `GET /users/{username}/settings/billing/ai_credit/usage` 和 `/organizations/{org}/settings/billing/ai_credit/usage`。须按实际计费归属选择账号范围；组织分配席位的用户不会出现在个人账单端点。具体 token 类型与权限应以端点说明为准，不能把一般教程里的权限要求套用所有接口。时间过滤为 year/month/day，还可按用户、模型或产品过滤；这些报表反映已用资源及费用，不自动给出个人订阅剩余额度。[Billing Usage](https://docs.github.com/en/rest/billing/usage)

如需内部兼容路径，第一方证据链为：

- VS Code 配置中的 entitlement URL 是 `https://api.github.com/copilot_internal/user`。[官方配置](https://github.com/microsoft/vscode/blob/main/extensions/copilot/CONTRIBUTING.md)
- 旧 Copilot Chat 第一方 `fetchCopilotUserInfo` 使用 GitHub token、`Authorization: token ...` 和版本请求头读取 user info，并保留 `quota_snapshots`。[官方读取源码](https://raw.githubusercontent.com/microsoft/vscode-copilot-chat/main/src/platform/authentication/node/copilotTokenManager.ts)
- 当前 VS Code 的 `parseQuotas` 解析 `quota_snapshots.chat/completions/premium_interactions`，包括 `percent_remaining`、`unlimited`、`quota_reset_at`、`entitlement`、`quota_remaining`、`credits_used` 和 `token_based_billing`。零 entitlement 的无配额快照会跳过。[当前官方解析源码](https://raw.githubusercontent.com/microsoft/vscode/main/src/vs/workbench/services/chat/common/chatEntitlementService.ts)

这是客户端内部 API，不是稳定的公开 REST 合同，不能保证任意 PAT 都有权限。旧 `vscode-copilot-chat` 仓库已经归档，应跟踪当前 `microsoft/vscode`。应分别显示无限权益、AI credits、旧 premium requests，不要固定为每月 300 次或把新的 credits 显示成请求数。旧 premium request 文档目前仅适用于保留旧计费的既有年付 Pro/Pro+ 用户。[旧计费适用范围](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/github-copilot-premium-requests)

## 5. Gemini / Google AI

Gemini API 配额按 **project**，不是按 API Key；RPM、输入 TPM、RPD 等限额并行生效，RPD 按太平洋时区午夜重置。官方要求在 AI Studio 查看账号实际限额，不能硬编码静态套餐表。额度限制不等于 Google AI Pro/Ultra 订阅权益。[Gemini API Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)

API 花费通过 AI Studio / Cloud Billing 查看。后续组织级接入应分别实现 GCP IAM、配额指标和账单导出；仅有普通 Gemini inference Key 时，本次未确认可读取完整账户余额的公开 REST 接口。[Gemini API Billing](https://ai.google.dev/gemini-api/docs/billing)

Gemini CLI 第一方实现使用 `POST https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`，请求带 project，可选 userAgent，由 Google AuthClient 鉴权；这是读取性质的 POST，不能把同一主机上的生成或 onboarding 请求视作监控。响应 `buckets` 有 `remainingAmount`、`remainingFraction`、`resetTime`、`tokenType`、`modelId`，无分页结构。官方源码明确属于内部 Code Assist API；OAuth/项目选择和 token 生命周期应先经独立验证。[官方 server.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/code_assist/server.ts)、[官方 types.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/code_assist/types.ts)

后续只把官方 fraction 转成百分比，不根据本地 Token 日志推导；保留模型桶，不假定所有模型共享一份日额度。

## 6. OpenAI API

使用组织 Admin API Key，而不是普通项目 inference Key。`GET https://api.openai.com/v1/organization/usage/completions` 返回 Token 与调用计量，`GET https://api.openai.com/v1/organization/costs` 返回费用。两者均要求 inclusive `start_time`，可选 exclusive `end_time`，按 `has_more` 和 `next_page` 传 `page` 完整翻页。用量桶支持 `1m/1h/1d`，各自 bucket 上限不同；费用只支持 `1d`，limit 为 1–180。费用和用量可按接口支持的 project/key/line item 维度分组。[Usage Completions](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions)、[Costs](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)

这些接口不是余额接口，也不提供 ChatGPT/Codex 订阅剩余额度。本次未核实公开的预付余额 REST API，旧 dashboard credit-grants 不能当作稳定支持。组织 Owner 才能创建与使用 Admin Key。[Admin keys](https://platform.openai.com/docs/api-reference/admin-api-keys)

后续接入要避免 `ModelUsage.cost_cny` 等现有字段直接承接 USD 原值；先明确货币与费用数据路径。分钟聚合 Token 不能写成请求级精确事件，也不能用 completion 用量宣称覆盖音频、图像与工具全部成本。

## 7. GLM / Z.ai Coding Plan

官方推荐 `glm-plan-usage` 插件，当前文档限定个人套餐。[官方用量插件](https://docs.z.ai/devpack/extension/usage-query-plugin)

第一方脚本在国际 `https://api.z.ai` 和国内 `https://open.bigmodel.cn`（也识别 `dev.bigmodel.cn`）读取以下 GET：

- `/api/monitor/usage/quota/limit`：无时间参数，返回 `data.limits`。
- `/api/monitor/usage/model-usage` 与 `/api/monitor/usage/tool-usage`：参数为 `startTime` / `endTime`，脚本使用 `yyyy-MM-dd HH:mm:ss` 字符串。

鉴权为 `Authorization` 直接传配置的 `ANTHROPIC_AUTH_TOKEN`，不是在脚本中无条件加 Bearer。脚本将 `TOKENS_LIMIT.percentage` 作为用量进度，将 `TIME_LIMIT` 的 percentage/currentValue/usage/usageDetails 作为 MCP 用量信息；没有公开分页合同。[第一方 query-usage.mjs](https://raw.githubusercontent.com/zai-org/zai-coding-plugins/main/plugins/glm-plan-usage/skills/usage-query-skill/scripts/query-usage.mjs)

当前 Coding Plan 文档同时说明 5 小时与周限制，旧脚本仍把 TOKENS_LIMIT 文案称为 5 小时。因此仅凭 `type` 不能可靠区分所有新窗口。本轮仅把该脚本标为 Token usage / MCP usage 的百分比显示为已用比例：`TOKENS_LIMIT` 标“套餐额度”，`TIME_LIMIT` 标“工具调用额度”，同类型多条保留独立序号。不展示未证实的时长、reset、Token 数或费用。只含 `CREDIT_LIMIT` 等未核实类型时明确返回 `QUOTA_UNAVAILABLE`；混合响应显示已知条目并提示部分类型暂不支持。工具总配额为零时不生成百分比窗口。API 余额与 Coding Plan 另算；后续取得第一方新套餐 schema 或脱敏的核实响应后再扩展。[当前套餐规则](https://docs.z.ai/devpack/overview)

## 8. SiliconFlow

**国内旧 `GET https://api.siliconflow.cn/v1/user/info` 已于 2026-08-14 停止服务。** 2026-08-11 官方公告明确要求移除调用，并表示后续账户替代 API 将另行发布。旧 ReadMe / OpenAPI 页面仍被搜索引擎收录，不能据此新增 Provider。[官方退役公告](https://docs.siliconflow.cn/docs/release-notes/overview)

国际 `https://api.siliconflow.com/v1/user/info` 的官方页面目前仍列出 Bearer 读取，字段包括 `balance`、`chargeBalance`、`totalBalance`，无分页。但这不能证明国内接口仍可用，也不能证明国际未来无迁移。首批暂缓，待分站更新日志、实际字段单位与账号范围一起验证后再上。[国际接口文档](https://docs.siliconflow.com/en/api-reference/userinfo/get-user-info)

## 9. MiniMax

当前官方产品已使用 Token Plan 名称，控制台进度条覆盖套餐内资源，采用 5 小时固定窗口和周窗口；订阅 Key 与按量 API Key 独立，不能互换。因此旧 Coding Plan 按 prompt 数量的实现必须先核验新旧响应，不能仅改产品名。[Token Plan 概要](https://platform.minimaxi.com/docs/token-plan/intro)

官方 `mmx-cli` 提供 `mmx quota` 查看 Token Plan 用量与剩余额度；官方文档说明国内/国际 region 与 Key 来源须匹配。后续优先基于该官方 CLI 或其第一方请求实现做接入，不试探猜测的余额端点；本次未运行真实 `mmx quota`。[官方 CLI 文档](https://platform.minimaxi.com/docs/token-plan/minimax-cli)、[第一方 CLI 仓库](https://github.com/MiniMax-AI/cli)

按量账户余额可在官方账户管理页面查看并设置预警，但本次未确认稳定的公开只读余额 API。不能把“余额不足”错误码当作余额测量方式，更不能为了测试额度发起收费推理。[账户与余额](https://platform.minimaxi.com/docs/faq/about-account)

## 10. Windsurf / Devin Desktop

旧 Windsurf 文档目前重定向 Devin Desktop 文档。官方分别讨论 self-serve、企业 ACU 与旧企业 credits，个人计划于 2026 年 3 月引入新的使用量模式。账户额度可通过 IDE Plan Info 或登录后的 manage-plan 查看。不同模式的单位和窗口不能合并成统一“剩余 prompt credits”。[Plans and Usage](https://docs.devin.ai/desktop/accounts/usage)、[Quota-Based Usage](https://docs.devin.ai/desktop/accounts/quota)

本次未核实适用于全部个人套餐的稳定只读订阅 REST API。企业 Analytics / Devin 云代理 API 也不能直接替代个人 Desktop 额度。后续应优先争取官方支持的账户用量入口，再考虑显式标注版本与数据来源的本地快照适配。

## 后续迭代与验收

1. 首批完成余额、Key 预算和可证明的订阅窗口接入；设置页直接说明实际支持范围、所需凭据与来源。
2. 按账户隔离缓存；源数据过期、401/403、接口退役、业务失败、结构变化要明确显示，不使用旧数据冒充刷新成功。
3. 第二批处理组织 USD 费用、完整分页、不同产品统计范围，再增加 Gemini/Copilot/MiniMax 官方客户端接入。
4. 每个新增 Provider 使用脱敏固定响应验证成功、缺失、零权益、无限权益、NaN/Infinity、错误凭据、超时与跳转；本地快照验证部分写入、过期、账号切换、超大文件和敏感字段丢弃。
5. 真实账户核验应只做已配置供应商的只读请求，并与官方页面核对单位与时间。未完成真实核验的接入必须如实记录，不以单元测试替代实际兼容性证据。

本轮执行 `python -m pytest tests/test_zai_provider.py tests/test_claude_provider.py -q` 验证 Claude 与 Z.ai 适配器；包含 helper 子进程到 Provider 的端到端读取、非法 URL、业务错误及未支持类型处理。具体测试计数以最终运行结果为准。没有调用真实账户接口。没有对“零 Bug”作无法证明的保证。
