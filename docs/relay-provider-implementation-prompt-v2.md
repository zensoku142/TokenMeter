# 第三方中转接入：新会话最终实施提示词

复制下面代码块中的全部内容到新的 Codex 会话即可。

```text
请在本地项目中完整实现 TokenMeter/TokenSpider 的第三方中转服务商接入。

项目路径：
D:\phpstudy_pro\WWW\cyf\TokenSpider

开始前必须完整阅读：
1. D:\phpstudy_pro\WWW\cyf\TokenSpider\AGENTS.md
2. D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-integration-requirements.md
3. D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-verified-interface-notes.md
4. C:\Users\Administrator\Desktop\中转请求和响应参数.txt



一、最终目标

1. 接入当前第三方中转服务商 NayutoAI，同时为后续增加其他中转服务商保留简单、明确的适配边界。
2. 不改变现有 TokenMeter 面板布局、视觉风格、操作方式和使用习惯。
3. 中转“今日分时”直接复用当前小米或 DeepSeek 的分时图表、日期切换、图例、滚动条和分钟详情浮层，不开发第三方网站控制台。
4. 分钟详情保持现有字段和顺序：时间、总计、输入（命中缓存）、输入（未命中缓存）、输出、缓存命中率、本分钟消耗金额。
5. 悬浮球完全复用小米现有金额球的组件、样式、尺寸、位置和交互，只替换中转的今日金额、账户余额和美元符号；不要增加百分比、进度环、徽标或中转专属样式。
6. 中转取数和分钟聚合逻辑独立于小米、DeepSeek，不得复用它们的错误字段口径或本地价格公式。
7. 小米、DeepSeek以及其他现有Provider的登录、取数、轮询、缓存、分时、切换和悬浮球必须保持原行为。

二、已经核实的接口与字段口径

1. 登录校验和余额：
   GET https://nayutoai.xyz/portal/auth/me
   使用 balance、status 等真实响应字段。

2. 今日和累计汇总：
   GET https://nayutoai.xyz/portal/user/dashboard/stats
   可用于主面板展示或与请求明细聚合结果对账。

3. 请求明细和分钟分时主数据源：
   GET https://nayutoai.xyz/portal/user/usage?page=1&page_size=50

4. 小时趋势接口只能作为趋势或对账参考，不能代替分钟详情：
   GET /api/v1/usage/dashboard/trend?start_date=...&end_date=...&granularity=hour&timezone=Asia%2FShanghai

5. usage明细的样例已经确认：
   - request_id：优先作为业务去重键。
   - input_tokens：未命中缓存输入Token。
   - cache_read_tokens：命中缓存输入Token。
   - output_tokens：输出Token。
   - total_tokens = input_tokens + cache_read_tokens + output_tokens。
   - actual_cost：用户实际支付金额，分钟金额必须汇总该字段。
   - total_cost：基础/倍率前金额，不能代替actual_cost展示实际消费。
   - created_at：UTC ISO-8601时间，转换为Asia/Shanghai后再按日期和分钟归桶。
   - status：请求状态，必须确认失败/取消请求是否计费以及服务商页面采用的口径。

6. 当前真实请求使用 Authorization: Bearer。不要输出、复制或复用附件中的旧Bearer，它已经视为泄露；联调时通过重新登录获取新凭证。

三、登录与凭证要求

1. 用户体验与小米一致：TokenMeter打开隔离浏览器，用户自行登录，程序监听真实门户API请求并自动捕获凭证，验证成功后安全保存。
2. 复用 api/browser_cookie.py 及项目已有Chrome会话能力前，先阅读实际实现；小米可能捕获Cookie，中转应捕获真实请求中的Authorization Bearer。
3. 优先监听 /portal/auth/me 或 /portal/user/usage 等实际请求，保存最小必要认证信息。
4. 是否还需要Cookie、CSRF或其他请求头，必须从附件和真实请求验证，不能猜。
5. 凭证必须复用项目现有Windows Credential Manager/安全存储方案，不得进入源码、普通配置、日志、异常堆栈、测试快照或Git。
6. 401/403保留历史缓存并提示重新连接；429按现有退避方式处理；网络或5xx失败保留最后成功数据。

四、分钟聚合规则

1. 把created_at从UTC转换为Asia/Shanghai，再按界面所选日期和HH:mm归桶。
2. 每分钟汇总：
   - 命中缓存输入 = sum(cache_read_tokens)
   - 未命中缓存输入 = sum(input_tokens)
   - 输出 = sum(output_tokens)
   - 总计 = 上述三项之和
   - 缓存命中率 = 命中缓存输入 / (命中缓存输入 + 未命中缓存输入)
   - 本分钟消耗金额 = sum(actual_cost)
3. 分母为0时按现有组件规则显示0%或占位符。
4. 金额使用Decimal或项目现有十进制金额方案累计，禁止直接用二进制浮点累计账单。
5. 分页拉取直到空页、到达总页数或记录早于目标窗口，并设置最大页数保护。
6. 优先按request_id去重；如果某类记录缺失request_id，必须根据真实字段设计稳定组合键并测试。重复轮询和跨页重复不能重复累计。
7. 未确认失败/取消请求的账单口径前，不要擅自过滤或计费；先对照第三方面板和真实响应。

五、现有架构和兼容约束

1. 先搜索实际代码，完整追踪：Provider注册与配置 → 设置页凭证 → 浏览器捕获 → FetchTask → 数据解析与data/store → minute_usage/history → qt_panel → 悬浮球。
2. 重点阅读并复用：
   - api/providers/__init__.py
   - api/providers/base.py
   - api/providers/mimo.py
   - api/providers/deepseek.py
   - api/browser_cookie.py
   - data/store.py
   - ui/qt_widget.py
   - ui/qt_panel.py
   - ui/qt_settings.py
   - 相关tests
3. ACTIVE_PROVIDER只是当前显示范围。中转应加入configured_provider_ids和现有后台采集机制；每个完成结果更新所属Provider缓存，只有结果所属Provider仍为ACTIVE_PROVIDER时才更新可见面板和悬浮球。
4. 保留现有_provider_results、_in_flight_requests、_pending_refreshes、请求ID和迟到结果保护；不得让中转迟到响应覆盖其他Provider界面。
5. 各Provider的缓存、错误、最后更新时间、分钟历史和凭证状态必须隔离。中转失败不能阻塞其他Provider采集。
6. 优先扩展现有Provider抽象。只有现有结构不能承载时，才增加最小适配边界；不要建立复杂插件系统，不做无关重构、改名或格式化。
7. URL、认证、分页、字段解析、Token口径、币种和能力差异放在Provider适配层；UI只消费统一展示模型，不直接解析第三方JSON或拼接认证头。

六、UI要求

1. 仅在现有服务商选择和设置能力中增加“中转”项；内部Provider ID与显示名称解耦。
2. 主面板所有区域、位置、主题、日期、刷新和统计布局保持不变。
3. 中转金额使用美元 `$`。
4. 中转Tooltip复用现有组件，顺序固定为：
   时间 / 总计
   输入（命中缓存）
   输入（未命中缓存）
   输出
   缓存命中率
   本分钟消耗金额
5. 图例颜色、柱状图、导航条和悬停行为与小米/DeepSeek一致。
6. 无数据时不得显示其他Provider数据。
7. 悬浮球不要新建样式，直接复用小米金额球组件。

七、开发流程

1. 完整阅读AGENTS.md、需求、接口补充和附件。
2. 执行git status，保护所有已有未提交和未跟踪文件，不覆盖用户改动。
3. 搜索现有Provider、登录、存储、分时、UI和测试调用链，先复用已有实现。
4. 在编码前向我报告：真实调用链、准备复用的代码、已确认接口字段、影响范围和最小修改计划，然后继续实现；只有会改变需求范围的关键歧义才需要停下来询问。
5. 只修改本需求必需文件，不顺手重构、格式化、改名、升级依赖或修改数据库结构。
6. 不提交、不推送、不创建PR、不发布，除非我后续明确要求。

八、测试与验收

1. 使用附件真实响应建立脱敏fixture，绝不能包含Bearer、完整API Key、邮箱、IP等秘密。
2. 测试真实字段解析、UTC转Asia/Shanghai、分钟归桶、Token三项合计、缓存命中率和actual_cost十进制汇总。
3. 测试分页终止、最大页保护、跨页去重、重复轮询、空响应、缺失字段和脏数据。
4. 测试401、403、429、5xx、超时和凭证失效。
5. 测试多Provider后台采集、快速切换和迟到结果，证明不会串数据。
6. 回归小米、DeepSeek的登录、取数、分时和悬浮球。
7. 运行相关测试和现有完整测试；如果环境允许，启动程序进行真实UI和登录/API验证。
8. 不要把静态检查当成运行时验证。未运行的完整测试、真实登录、真实API、UI或打包验证必须逐项说明。

九、最终交付说明

完成后请给出：
1. 修改文件清单。
2. 每条需求对应的实现位置。
3. Provider调用链和关键字段映射。
4. 分页、去重、时区、Token与金额计算规则。
5. 凭证安全处理结果。
6. 已执行测试及结果。
7. 未验证内容和剩余风险。
8. 明确回答小米、DeepSeek原有功能是否保持不变。
```
