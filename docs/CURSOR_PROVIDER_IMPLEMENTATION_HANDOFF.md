# TokenSpider 接入 Cursor 服务商实施交接

## 1. 交接目标

在 TokenSpider 当前多服务商架构中新增个人订阅类型的 `Cursor` Provider，读取本机 Cursor 客户端已经登录账号的当期用量，并填入现有订阅额度面板。

本需求是“新增数据源”，不是 UI 改版：

- 保持主面板现有布局树、尺寸、区域顺序、间距和交互不变。
- 保持悬浮球与现有 Codex 百分比球完全一致。
- 不新增 Cursor 专属卡片、进度条、图表、区域或弹窗。
- Cursor 当前接口没有提供的数据，继续使用现有空数据状态，不伪造趋势和活动。

## 2. 当前基线

- 仓库：`D:\phpstudy_pro\WWW\cyf\TokenSpider`
- 分支：`master`
- 编写本交接时 HEAD：`b6c7059`
- Provider 注册入口：`api/providers/__init__.py`
- Provider 公共模型：`api/providers/base.py`
- 额度聚合与缓存：`data/store.py`
- 主面板：`ui/qt_panel.py`
- 悬浮球数据接线：`ui/qt_widget.py::_apply_update()`
- 悬浮球绘制：`ui/qt_ball.py::FloatingUsageBall`

开始任务前必须重新检查实际分支、HEAD 和工作区；本节记录可能随时间变化，不能代替现场检查。

当前工作区已有用户未跟踪内容，必须保留，不得删除、移动、覆盖或顺手纳入格式化：

- `docs/date-picker-handoff/`
- `docs/macos-glass-redesign-handoff/`
- `docs/多服务商后台持续采集与分时可信度优化需求文档.md`
- `ui/qt_backdrop.py`

## 3. 最高优先级红线

### 3.1 悬浮球完全复用 Codex 现有实现

`ui/qt_ball.py` 是本任务禁止修改文件。

不得修改：

- `FloatingUsageBall` 的尺寸、圆形玻璃外观和边缘效果。
- `_paint_quota()` 的液面高度、波浪、渐变、高光和百分比绘制。
- 深浅主题效果。
- hover、pressed、drag、peak、边缘吸附和自动隐藏行为。
- 0%、100%、未知额度和加载状态的现有行为。
- 字号、文字区域、动画定时器、物理参数或绘制缓存。

Cursor 成功返回月度额度后，只需将主额度放在 `ProviderQuota.windows[0]`。现有 `ui/qt_widget.py::_apply_update()` 已经执行：

```text
剩余百分比 = 100 - primary.used_percent
ball.set_quota_state(剩余百分比, 重置倒计时, primary.title)
```

因此 Cursor 会自然复用 Codex 当前百分比球，不需要也不允许制作 Cursor 专属悬浮球。

注意：当前球体实际绘制的是百分比和液面；不要为了展示“每月额度”或重置倒计时而向球内新增文字。成功态只允许改变数据值，不改变显示结构。

Cursor 无额度数据时仍必须保持 quota mode，不能回退到金额球显示虚假的 `$0`。如果必须修正不可用状态中的内部标题，只允许在 `ui/qt_widget.py::_apply_update()` 做 Cursor 分支的数据参数适配，不得修改 `ui/qt_ball.py`。

### 3.2 主面板布局不变

不得修改：

- Header 的控件顺序和尺寸。
- Provider 下拉框 `132 × 28px` 的折叠态尺寸。
- 左侧一个主指标和两个次指标的结构。
- 右侧近 7 天趋势区域的尺寸和位置。
- 中部年度活动区域的尺寸和位置。
- 底部五列统计结构。
- 状态栏结构。
- 面板固定高度、宽度适配和展开/收起行为。

允许的 UI 修改只有 Provider 文案泛化和 Cursor 数据填充；不得新增任何可见控件。

### 3.3 凭据安全

- 只读本机 Cursor `accessToken`。
- 不读取、不使用、不刷新 `refreshToken`。
- 不把 access token 写入 TokenSpider 配置、SQLite、日志、异常文案、测试快照或 tooltip。
- 不打印 Cursor 接口的完整响应；响应可能包含账号或套餐信息。
- `snapshot_identity()` 只能保存不可逆 SHA-256 指纹。
- 401/403 只报告认证失效，提示用户在 Cursor 客户端重新登录；TokenSpider 不接管 Cursor 登录和刷新流程。
- 不修改 Cursor 的 `state.vscdb`，不执行写事务，不清理 Cursor 数据。

## 4. 当前本机静态证据

检查日期：`2026-08-14`。

本机 Cursor 安装：

- 安装目录：`E:\Program Files (x86)\cursor`
- 当前版本：`3.15.19`
- 主代码文件包含服务：`aiserver.v1.DashboardService`
- 已发现只读 RPC：
  - `GetCurrentPeriodUsage`
  - `GetPlanInfo`

本机 Cursor 状态库：

```text
%APPDATA%\Cursor\User\globalStorage\state.vscdb
```

已确认存在以下键名；检查过程未输出键值：

```text
cursorAuth/accessToken
cursorAuth/refreshToken
cursorAuth/stripeMembershipAuthId
cursorAuth/cachedEmail
cursorAuth/stripeMembershipType
cursorAuth/stripeSubscriptionStatus
```

Cursor 安装包中确认的主要响应字段：

```text
GetCurrentPeriodUsageResponse
  billing_cycle_start
  billing_cycle_end
  plan_usage
  spend_limit_usage
  enabled
  display_message

PlanUsage
  total_spend
  included_spend
  bonus_spend
  remaining
  limit
  remaining_bonus
  bonus_tooltip
  auto_spend
  api_spend
  auto_limit
  api_limit
  auto_percent_used
  api_percent_used
  total_percent_used

SpendLimitUsage
  total_spend
  pooled_limit
  pooled_used
  pooled_remaining
  individual_limit
  individual_used
  individual_remaining
  limit_type
  overall_limit
  overall_used
  overall_remaining

GetPlanInfoResponse.PlanInfo
  plan_name
  included_amount_cents
  price
  billing_cycle_end
  plan_owner
```

Cursor 当前前端代码对金额字段统一除以 `100`；套餐使用百分比按以下规则计算：

```text
limit > 0
  ? min(included_spend / limit * 100, 100)
  : total_percent_used 或 0
```

以上是本机安装包的静态证据，不代表私有接口长期稳定。实现前必须重新验证当前 Cursor 版本。

## 5. 接口稳定性结论

个人 Cursor 用量依赖客户端私有 RPC，没有公开的个人订阅用量 API。首版必须明确标注为“实验性支持”。

静态代码显示候选主机为：

```text
https://api2.cursor.sh
```

根据 Connect RPC 的常见路径，候选请求为：

```text
POST /aiserver.v1.DashboardService/GetCurrentPeriodUsage
POST /aiserver.v1.DashboardService/GetPlanInfo
```

但请求 Content-Type、Connect 版本头、Authorization 格式、响应封装和时间戳单位必须通过只读 PoC 实际确认，不能只凭静态字符串直接写死为已验证协议。

建议优先验证以下候选形式：

```http
Authorization: Bearer <accessToken>
Connect-Protocol-Version: 1
Content-Type: application/json 或 application/connect+json
```

个人账号请求体候选为 `{}`，不得自行填写 `team_id`。

## 6. 实施前只读 PoC

正式接入前先写最小、临时、只读验证代码；不要先改 UI。

PoC 必须验证：

1. Cursor 关闭和运行中时都能只读打开 `state.vscdb`。
2. 能读取 `cursorAuth/accessToken`，但不输出其值。
3. `GetCurrentPeriodUsage` 的实际 URL、请求头、Content-Type 和响应 JSON 结构。
4. `GetPlanInfo` 的实际 URL、请求头、Content-Type 和响应 JSON 结构。
5. `billing_cycle_start/end` 是秒还是毫秒时间戳。
6. 金额字段确实为 cents。
7. 401、403、429、超时和非 JSON 响应的实际表现。
8. 请求没有造成 Cursor 账号、订阅或本地状态变化。

PoC 输出只允许包含：

- HTTP 状态码。
- 响应字段名。
- 字段类型。
- 脱敏后的数值范围。
- 协议确认结果。

禁止输出 token、邮箱、会员 ID 或完整响应。

若 PoC 无法确认接口，停止正式实现并报告阻塞，不得猜测协议。

## 7. 推荐最小实现范围

### 7.1 新增 `api/providers/cursor.py`

新增 `CursorProvider`，建议基础属性：

```python
id = "cursor"
name = "Cursor"
default_currency = "USD"
supports_subscription_quota = True
official_api_hosts = {"api2.cursor.sh"}
```

只实现本需求需要的能力：

- `is_configured()`
- `snapshot_identity()`
- `fetch_quota()`
- `close()`

不要实现虚假的 balance、summary、daily payload 或分钟用量。

建议提供一个可选目录字段：

```text
CURSOR_GLOBAL_STORAGE
```

默认目录：

```text
%APPDATA%\Cursor\User\globalStorage
```

设置页继续使用现有 `credential_fields` 自动生成目录输入框；不新增 Cursor 专属设置页。

### 7.2 只读状态库访问

建议职责拆分保持小而明确：

```text
_global_storage_dir()
_state_db_path()
_read_auth_state()
_credentials()
```

要求：

- 使用 SQLite read-only URI，例如 `mode=ro`。
- 连接生命周期尽可能短，查询后立即关闭。
- 只查询需要的 key。
- access token 只保留在当前刷新任务内存中。
- `is_configured()` 只判断 DB、token 是否可读，不发网络请求。
- 不用 refresh token 兜底。

`snapshot_identity()` 优先使用 `stripeMembershipAuthId`，其次可使用缓存邮箱，最后才使用 access token；无论采用哪项，都必须加 `cursor:` 前缀后做 SHA-256，只返回哈希。

### 7.3 HTTP 请求

接口确认后复用 `api/providers/base.py::build_session(retry_post=True)`，因为 RPC 是只读 POST。

要求：

- 明确 connect/read timeout，建议从 `(3, 10)` 起步。
- 只对已确认的瞬时错误安全重试。
- 不记录 headers 和 body。
- `GetPlanInfo` 可做短期内存缓存，避免每分钟重复获取稳定套餐信息；缓存必须按账号指纹隔离。
- Provider `close()` 必须关闭 Session。

错误映射建议沿用现有 `FetchError`：

| 场景 | code |
| --- | --- |
| 状态库、access token 不存在 | `NOT_CONFIGURED` |
| 401 / 403 | `AUTH_EXPIRED` |
| 429 | `RATE_LIMITED` |
| 连接失败 | `NETWORK_ERROR` |
| 超时 | `NETWORK_TIMEOUT` |
| 5xx | `SERVER_ERROR` |
| 响应结构或字段非法 | `INVALID_RESPONSE` |
| 其他未分类异常 | `UNKNOWN_ERROR` |

错误消息不得包含 token、请求头或完整响应。

## 8. Cursor 数据到现有 UI 模型的映射

### 8.1 主额度

`ProviderQuota.windows` 只放一个主窗口，并确保它位于第一个：

```python
QuotaWindow(
    id="cursor-monthly",
    title="每月额度",
    used_percent=used_percent,
    resets_at=billing_cycle_end,
    window_minutes=None,
)
```

`used_percent` 必须复用 Cursor 当前客户端口径，不另创新公式。

### 8.2 两个现有次指标

严格使用现有两个位置，不新增第三个位置：

1. `套餐用量`
   - 值：`included_spend / limit`
   - cents 转美元，固定两位小数。
2. `额外消费`
   - 值：优先按 Cursor 客户端当前展示口径使用 `individual_used / individual_limit`。
   - limit 不存在时显示 `--`，不得用 `0` 冒充上限。

示例只用于说明格式：

```text
套餐用量  $8.40 / $20.00
额外消费  $2.10 / $50.00
```

### 8.3 底部现有五列统计

`ProviderQuota.statistics` 最多填五项，顺序固定：

1. `套餐`：`plan_name`
2. `Bonus`：`bonus_spend / 100`，使用金额，不伪造百分比
3. `Auto`：`auto_spend / 100`
4. `指定模型`：`api_spend / 100`
5. `账期`：`billing_cycle_start` 至 `billing_cycle_end`

可选字段缺失时对应值显示 `--`，不要挪用其他字段补位。

建议：

```text
套餐       Pro
Bonus      $0.00
Auto       $6.20
指定模型   $2.20
账期       08-01 — 09-01
```

`ProviderQuota.plan` 可以同时设置为 `plan_name`，但 `account_plan_active_until` 不应使用账期结束时间冒充订阅到期时间。

### 8.4 当前没有数据的位置

当前两个 RPC 没有提供 TokenSpider 所需的逐日 Token 趋势和年度 Token 活动，因此：

```python
activity = ()
weekly_activity = ()
activity_source = ""
weekly_activity_source = ""
```

结果：

- 右侧近 7 天区域保持原位置和原空数据表现。
- 中部年度活动区域保持原位置和原空数据表现。
- 不添加折线、费用进度轨道、模拟柱状图或假的热力格数据。

## 9. 注册与配置修改

### `api/providers/__init__.py`

- 导入 `CursorProvider`。
- 在 `PROVIDERS` 末尾注册 Cursor，避免改变现有三项顺序。
- `configured_provider_ids()` 和后台调度会自动复用现有逻辑。

建议注册顺序：

```text
DeepSeek → Xiaomi MiMo → Codex → Cursor
```

### `config/defaults.py`

- 新增 `CURSOR_GLOBAL_STORAGE = ""`。
- 该字段不是 secret，不保存 access token。
- 不新增 `CURSOR_ACCESS_TOKEN`、`CURSOR_REFRESH_TOKEN`。

### `config/store.py`

- 将 `cursor` 加入 `ACTIVE_PROVIDER` 白名单和错误文案。
- 不改变其他配置验证行为。

### 设置页

现有 `ui/qt_settings.py` 会根据 `credential_fields` 自动生成目录配置，不需要新增 Cursor 专属布局。

## 10. 主面板最小兼容修改

只处理现有订阅额度模式中写死的 Codex 文案，不改布局。

需要检查：

1. `StatisticsCard.set_quota_data()` 当前标题写死为 `Codex 使用统计`。
2. 底部统计来源 tooltip 当前写死为 `来自 Codex 账号统计`。
3. quota mode 无数据占位卡当前写死为 `Codex`。
4. `ui/qt_widget.py::_apply_update()` 的额度不可用标题当前写死为 `周额度`。

最小处理原则：

- 从 `data.per_provider[0].provider_name` 读取名称。
- Codex 的既有显示文案必须保持完全一致。
- Cursor 显示 `Cursor 使用统计` 和 `来自 Cursor 账号统计`。
- Cursor 不可用状态使用月度额度语义，但不改变球体绘制。
- `codex_source_summary()`、`codex_update_time()` 等内部方法名可以保留，避免无意义重构；只要输出文案正确即可。

严禁修改 `MainPanel.__init__()` 的布局结构、固定高度、stretch、margin 或控件数量。

## 11. 多 Provider 与缓存行为

当前架构已经支持：

- `ACTIVE_PROVIDER` 只决定当前显示对象。
- `configured_provider_ids(config)` 决定后台采集对象。
- 每个 Provider 独立 in-flight、pending、result 和刷新时间。
- 非当前 Provider 完成后只更新其缓存，不覆盖当前面板和悬浮球。
- `ProviderQuota` 有 Provider/账号隔离的持久化快照。
- 瞬时网络错误可回退最后成功额度。

Cursor 必须复用以上机制，不新增调度器、不新增数据表、不修改 SQLite 主键语义。

`snapshot_identity()` 返回稳定哈希后，现有 `data/store.py` 即可按账号隔离 Cursor 快照；本需求不需要数据库迁移。

## 12. 预计文件范围

应新增：

- `api/providers/cursor.py`

应修改：

- `api/providers/__init__.py`
- `config/defaults.py`
- `config/store.py`
- `ui/qt_panel.py`：仅 Provider 文案泛化和数据占位文案。
- `ui/qt_widget.py`：仅 Cursor quota 不可用状态的数据参数适配，如实际需要。
- `tests/test_providers.py`
- `tests/test_config.py`
- `tests/test_store.py`：仅补 Cursor 快照/回退缺口时修改。
- `tests/test_qt_ui.py`

禁止修改：

- `ui/qt_ball.py`
- 主面板布局、主题、背景和尺寸相关代码。
- `data/history.py` 的表结构。
- 安装器、更新器、版本号和 release notes。
- 与本需求无关的用户未跟踪文件。

如果实际实现需要超出上述范围，先说明原因和影响，不得自行扩展。

## 13. 必须覆盖的测试

### Provider

- 默认 Cursor globalStorage 路径解析。
- 自定义 globalStorage 路径解析。
- 只读读取 access token，不读取 refresh token。
- 无数据库、无 token、数据库不可读。
- `is_configured()` 不发网络请求。
- `snapshot_identity()` 稳定、不可逆、不同账号隔离。
- 两个 RPC 请求路径、headers、timeout 和空请求体。
- cents 到 USD 的换算。
- 秒/毫秒时间戳解析。
- used percent 与 Cursor 当前客户端公式一致。
- 套餐用量、额外消费和五项统计顺序。
- 可选字段缺失时显示 `--`。
- 401/403/429/超时/5xx/非法 JSON 的错误映射。
- 日志和错误消息不包含 token。

### 配置与注册

- `ACTIVE_PROVIDER=cursor` 校验通过。
- 未知 Provider 仍被拒绝。
- `list_providers()` 包含 Cursor，原顺序不变。
- `configured_provider_ids()` 能识别本机已登录 Cursor。
- Provider 探测后连接已关闭。

### 主面板

- Provider 下拉框出现 Cursor。
- 第四个 Provider 加入后，弹层继续使用现有自动高度公式；当前公式下高度应为 `4 × 34 + 38 = 174px`，不是重新设计弹层。
- 主卡显示 `每月额度 / 已用 N% / 剩余 N% / 重置倒计时`。
- 两个次指标按现有位置显示。
- 底部仍为五列，标题为 `Cursor 使用统计`。
- 趋势区和年度活动区仍存在，空数据时不生成模拟数据。
- Codex 原有 UI 测试全部继续通过。

### 悬浮球

- 不修改现有 Codex 球体绘制测试。
- 新增 Cursor 数据接线测试即可：
  - `_quota_mode is True`
  - `_quota_remaining == 100 - used_percent`
  - 不回退到金额模式
  - 额度不可用时仍为 quota mode
- 不新增 Cursor 球体截图或新绘制断言。

## 14. 验证命令

先运行针对性测试：

```powershell
python -m pytest tests/test_providers.py tests/test_config.py tests/test_store.py tests/test_qt_ui.py -q
```

再运行完整检查：

```powershell
python -m pytest -q
python -m compileall api config data ui tests
python -m ruff check .
python -m pyright
git diff --check
git status --short --branch
```

人工验证必须单独报告，不能用静态检查代替：

- Cursor 已登录、退出登录、token 失效。
- Cursor 正在运行和完全退出。
- 深色、浅色、跟随系统主题。
- Cursor/Codex/DeepSeek/MiMo 快速切换。
- 后台 Cursor 刷新不覆盖当前 Provider UI。
- 展开面板与悬浮球互斥。
- Cursor 悬浮球与 Codex 现有球体外观、液面、动画、拖动和边缘隐藏一致。
- 640px、820px、125%/150% DPI 无截断。
- 无 access token、账号 ID、邮箱或完整响应写入日志和 TokenSpider 数据库。

## 15. 任务外范围

本次不做：

- Cursor Team/Admin API。
- 团队成员用量和审计事件。
- Cursor 登录、登出、token 刷新。
- 读取或使用 refresh token。
- 伪造逐日 Token、年度活动或模型明细。
- Cursor 专属悬浮球。
- UI 改版。
- 新增数据库表或迁移。
- 发布、Tag、GitHub Release、提交或推送，除非用户另行明确要求。

## 16. 完成定义

只有同时满足以下条件才可称为完成：

1. 只读 PoC 已确认实际协议、认证头、响应结构、金额单位和时间戳单位。
2. Cursor 出现在 Provider 列表和设置页。
3. 已登录 Cursor 账号能够获取并显示月度额度、套餐用量、额外消费和五项统计。
4. 无逐日数据时，趋势区和活动区保持原布局及空数据状态。
5. `ui/qt_ball.py` 没有任何改动。
6. Cursor 使用现有 Codex 百分比液面球，没有新增文字、控件或动画。
7. Cursor 网络失败时按现有机制保留最后成功快照，认证失效时不展示其他账号旧数据。
8. 未存储或输出任何 Cursor token。
9. 针对性测试、完整测试、compileall、Ruff、Pyright 和 `git diff --check` 均有实际结果。
10. 人工运行验证结果与未验证项分开说明。

## 17. 新会话直接使用的提示词

```text
请在 D:\phpstudy_pro\WWW\cyf\TokenSpider 中实现 Cursor 个人订阅 Provider。

开始前必须完整阅读：
1. AGENTS.md
2. docs/CURSOR_PROVIDER_IMPLEMENTATION_HANDOFF.md
3. docs/macos-glass-redesign-handoff/PRODUCT_PROTOTYPE.md
4. docs/macos-glass-redesign-handoff/IMPLEMENTATION_HANDOFF.md

硬性要求：
- 先执行 git status --short --branch 和 git diff，保留所有用户已有及未跟踪文件。
- 先阅读现有 Provider、配置、data/store.py、ui/qt_panel.py、ui/qt_widget.py、ui/qt_ball.py 和相关测试，再编码。
- 先做不输出任何敏感值的只读 PoC，确认 Cursor 私有 RPC 的实际 URL、请求头、Content-Type、响应结构、金额单位和时间戳单位；无法确认就停止并报告，不得猜测。
- 只读取 cursorAuth/accessToken，不读取、不使用、不刷新 refreshToken。
- 不把 token 写入配置、SQLite、日志、异常或测试数据。
- 只新增 Cursor 数据源并填入现有面板位置，不改变主面板布局、尺寸、区域、间距或交互。
- ui/qt_ball.py 禁止修改。Cursor 悬浮球完全复用现有 Codex 百分比球，只通过 ProviderQuota.windows[0] 提供剩余百分比和重置时间。
- 不新增 Cursor 卡片、进度条、图表、面板、按钮或专属悬浮球。
- Cursor 没有逐日 Token 和年度活动数据时，保留现有趋势区、活动区及其空数据状态，不生成模拟数据。
- 只对写死的 Codex 可见文案做 Provider 泛化，并保证 Codex 既有显示完全不变。
- 复用现有 Provider 隔离、后台刷新、额度缓存和瞬时错误回退，不新增数据库表。
- 完成后运行针对性测试、完整测试、compileall、Ruff、Pyright 和 git diff --check，并分别报告自动化、静态检查、人工运行和真实 Cursor 接口验证结果。
- 不提交、不推送、不发版，除非我另行明确要求。
```
