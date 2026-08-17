# NayutoAI 模型图与 Tooltip 修复：新会话完整编码提示词

复制下面代码块中的全部内容到新的 Codex 会话。

```text
请在本地项目中完整实现 TokenMeter/TokenSpider 的 NayutoAI 近 7 天模型用量图、今日分时模型名称和 Tooltip 闪烁修复。

项目路径：
D:\phpstudy_pro\WWW\cyf\TokenSpider

这是对当前未提交 NayutoAI 接入的增量开发，不是重新实现 Provider。必须保护工作区中全部已有未提交和未跟踪文件。

一、开始前必须完整阅读

1. D:\phpstudy_pro\WWW\cyf\TokenSpider\AGENTS.md
2. D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-integration-requirements.md
3. D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-verified-interface-notes.md
4. D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-implementation-prompt-v2.md
5. D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\nayuto-model-usage-and-tooltip-fixes-requirements.md
6. C:\Users\Administrator\Desktop\中转请求和响应参数.txt

视觉与 Bug 参考：

1. 正常状态：
   D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-mockups\nayuto-model-usage-7d-normal.png
2. 悬停详情：
   D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-mockups\nayuto-model-usage-7d-hover.png
3. 必须完整保留的 Token 活动和底部区域：
   D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-mockups\nayuto-token-activity-preserve.png
4. Tooltip 闪烁复现：
   D:\phpstudy_pro\WWW\cyf\TokenSpider\docs\relay-provider-mockups\trend-tooltip-flicker.gif

附件中的旧 Bearer 已视为泄露。只读取响应字段和接口结构，绝不能输出、复制、保存或复用旧 Bearer；真实联调必须重新登录获取新凭证。

二、开发前流程

1. 执行 git status 和 git diff --stat，保护所有已有改动。禁止 git reset --hard、git checkout --、git clean 或覆盖未跟踪文件。
2. 搜索并完整追踪：
   Nayuto usage 响应
   -> api/providers/nayuto.py 归一化
   -> ExactMinuteUsage
   -> data/history.py 的 daily_usage/minute_usage/minute_cost_usage
   -> data/store.py 的 PerProviderData/TokenData
   -> ui/qt_panel.py 的 TrendCard/MinuteUsageChart/MinuteUsageTooltip/MainPanel
   -> ui/qt_widget.py 的 Provider 后台刷新和迟到结果保护
   -> tests。
3. 重点阅读：
   - api/providers/base.py
   - api/providers/nayuto.py
   - data/history.py
   - data/store.py
   - ui/qt_panel.py
   - ui/qt_widget.py
   - tests/test_nayuto.py
   - tests/test_qt_ui.py
   - tests/test_refresh.py
4. 编码前先向我报告：真实调用链、当前已有能力、缺失模型维度的位置、Tooltip 闪烁根因、预计修改文件和最小实现计划，然后直接继续实现。只有会改变需求范围的关键歧义才停下来询问。
5. 只修改本需求必需文件。不重构无关模块，不格式化无关代码，不升级依赖，不改版本号。
6. 不提交、不推送、不创建 PR、不创建 Tag、不发布，除非我后续明确要求。

三、已经确认的当前基础

1. NayutoAI usage 明细字段：
   - request_id：优先去重键。
   - model：模型名称。
   - input_tokens：未命中缓存输入。
   - cache_read_tokens：命中缓存输入。
   - output_tokens：输出。
   - actual_cost：用户实际支付金额。
   - created_at：UTC ISO-8601，转换到 Asia/Shanghai 后归入日期和分钟。
2. api/providers/nayuto.py 已经按“日期 -> 模型 -> Token 类型/金额”生成日 payload。
3. daily_usage 表已经以 (usage_date, model, token_type, provider) 为主键持久化模型日数据。
4. 当前 TokenData.daily_usage 只保留按日总计，顶部 TrendCard 还拿不到每天的模型维度。
5. 当前 ExactMinuteUsage 和分钟历史只按日期/分钟/Token 类型或金额保存，Nayuto 模型在分钟归桶时被丢失。
6. 今日分时已支持日期切换、1～60 分钟聚合、柱状/折线、导航条、缓存恢复和美元金额，这些必须全部保留。

四、需求一：顶部增加近 7 天模型使用图

1. 只在主面板顶部现有近 7 天趋势区域内增加切换：
   “近 7 天使用金额” / “模型使用”。
2. 原“近 7 天使用金额”保留且默认行为不变；模型图不能加入下面 Token 活动的 activity_stack。
3. 模型图固定显示今天及之前 6 天，共 7 个连续自然日，不是自然周。
4. 每天是一组柱子；每组中每根柱子代表一个模型。
5. 每根柱子的高度是该日期、该模型的总 Token：
   cache_read_tokens + input_tokens + output_tokens。
6. 模型柱不是 Token 类型堆叠柱。Token 构成只在悬停详情中展示。
7. 7 天内模型集合统一；同一模型每天位置和颜色一致。模型按 7 天总 Token 降序，再按名称稳定排序。
8. 不得静默丢弃模型或默认合并“其他”。模型较多时在原图表区域内动态缩窄柱宽；必要时只在图表内部支持水平平移/滚动，不改变面板高度和下面布局。
9. 模型图数据从现有 daily_usage 读取。不要重新请求接口，不要在 UI 查询 SQLite，不要使用本地价格公式。
10. 增加 Provider 中立的日模型展示数据，例如 TokenData.daily_model_usage；字段必须包含日期、模型、三类 Token、总 Token 和 Decimal 金额。
11. NayutoAI 无数据时显示自己的空态，不能回退显示其他 Provider 数据。
12. 其他 Provider 不显示模型切换，原趋势标题、取数和 Tooltip 保持不变。

模型柱悬停详情顺序固定为：

日期 / 总计
模型
输入（命中缓存）
输入（未命中缓存）
输出
缓存命中率
当日消耗金额

缓存命中率：
命中缓存输入 / (命中缓存输入 + 未命中缓存输入)。

当日金额必须汇总 actual_cost 对应的持久化 Decimal，NayutoAI 显示美元 `$`。不能用 float 累计账单金额。

五、需求二：今日分时详情增加模型名称

1. NayutoAI 分钟/时段详情在“时间 / 总计”后新增“模型”行。
2. 其他原字段顺序不变：
   - 输入（命中缓存）
   - 输入（未命中缓存）
   - 输出
   - 缓存命中率
   - 本分钟消耗金额或本时段消耗金额
3. 同一分钟可能有多个模型，不能只保留最后一个。多个模型按该时间桶内总 Token 降序，再按名称排序，使用 `、` 分隔并允许换行。
4. 当前设置可能把 1～60 分钟聚为一个桶，模型集合必须聚合该桶内全部分钟。
5. 日期切换、重启恢复、网络失败保留缓存时，模型名称仍必须存在。
6. 其他 Provider 没有可靠模型字段时隐藏“模型”行，不显示 unknown，更不能显示 NayutoAI 数据。

为了满足历史日期和重启恢复，不能只把 model 临时传给 UI。请做最小、通用、Provider 隔离的持久化：

1. 在 ExactMinuteUsage 末尾增加可选 model_rows，保持已有位置参数兼容；新增调用优先使用关键字参数。
2. NayutoAI 按 (usage_date, minute, model) 聚合命中缓存、未命中缓存、输出和 actual_cost。
3. 新增独立 minute_model_usage 表或同等最小结构，不修改现有 minute_usage/minute_cost_usage 主键，不重建旧表。
4. 建议一行表示一个 Provider/日期/分钟/模型，保存三类 Token、Decimal 文本金金额和 updated_at。
5. 与 replace_exact_minute_usage 在同一 SQLite 事务内按日期原子替换，重复刷新幂等。
6. 清理保留期时同步清理分钟模型表。
7. 成功空响应可以清理目标日期旧模型行；远程失败不能覆盖最后成功数据。
8. TokenData 增加当前日期和历史日期的 minute_model_usage/minute_model_usage_history 或等价字段；Provider 缓存复制必须包含它们。
9. 对每个分钟验证模型汇总与现有 minute_usage/minute_cost_usage 一致。

六、需求三：修复 Tooltip 闪烁

已确认根因：

- TrendCard._on_mouse_moved() 使用 QToolTip.showText() 手动显示柱子详情。
- MainPanel.update_data() 又对同一 trend.plot 使用 setToolTip()，缓存文案是“当前显示最近一次缓存的近 7 天数据”。
- Qt 原生 Tooltip 与手动 QToolTip 争用同一个全局提示框，因此鼠标移动时两段文案交替闪烁。

必须修复为顶部趋势图只有一种悬停机制：

1. 推荐新增 TrendCard 私有 Tooltip QWidget，复用 MinuteUsageTooltip 的样式和定位方式。
2. 顶部趋势柱不再调用全局 QToolTip.showText()/hideText()。
3. trend.plot 不再设置原生 setToolTip()，其 toolTip 必须为空。
4. “来自接口/当前显示缓存”等来源提示只保留在 trend.title 的 Tooltip 或无障碍描述。
5. 如果保留全局 QToolTip，则也必须保证 trend.plot.toolTip() 为空，且同一控件只有手动柱子详情一种 Tooltip。禁止保留两套机制。
6. 不得通过降低刷新频率、禁止缓存、移除柱子详情或禁用鼠标移动掩盖问题。

七、图 3区域与所有原功能必须保留

图 1、图 2只定义顶部趋势区。以下内容一个都不能删除或替换：

- Token 活动标题；
- 年度活动 / 今日分时切换；
- 日期前后切换、日期弹窗和保留期；
- 平台明细/估算状态；
- 命中缓存、未命中缓存、输出图例；
- 今日分时柱状图/折线图；
- 今日分时导航条、滚动、缩放、悬停和分钟统计；
- 年度热力图和 Tooltip；
- 使用统计五列；
- 底部连接状态和更新时间；
- 面板宽高、主题、刷新、设置、关闭；
- 悬浮球样式、金额、拖动、展开/收起、置顶和失焦收起。

模型图只能切换顶部 TrendCard 内容，绝不能替换下面 Token 活动，也不能复用“年度活动 / 今日分时”的按钮或 activity_stack。

八、多 Provider 兼容要求

1. ACTIVE_PROVIDER 仍然只是当前显示范围。
2. 保留 configured_provider_ids、FetchTask、_provider_results、_in_flight_requests、_pending_refreshes、request ID 和迟到结果保护。
3. 每个结果只写所属 Provider 缓存；结果所属 Provider 仍为 ACTIVE_PROVIDER 时才更新可见面板和悬浮球。
4. 日模型、分钟模型、切换状态、错误和空态必须按 Provider 隔离。
5. NayutoAI 失败不能阻塞小米、DeepSeek、Codex、Cursor 等其他 Provider。
6. 小米、DeepSeek 及其他 Provider 的登录、取数、轮询、缓存、趋势、分时、切换和悬浮球必须保持原行为。
7. 不做与本需求无关的 Provider 抽象、插件系统、重命名或数据库迁移。

九、测试要求

使用脱敏 Fixture，不得包含 Bearer、完整 API Key、邮箱、IP或用户隐私。

至少覆盖：

1. 日模型数据：同日多模型、跨日、跨月、7 天范围、稳定排序、Decimal 金额。
2. 分钟模型数据：同分钟多模型、同模型多请求、1 分钟和多分钟桶、缺失 model。
3. 跨页去重和重复轮询不重复累计。
4. UTC 转 Asia/Shanghai 后日期和分钟正确。
5. minute_model_usage 原子替换、幂等、空成功清理、远程失败保留、保留期清理和 Provider 隔离。
6. 分钟模型汇总与现有分钟 Token/金额总表一致。
7. NayutoAI 顶部显示切换、7 个日期组、每天多个模型柱、颜色与位置稳定。
8. 模型柱悬停详情字段、顺序、命中率和美元金额正确。
9. trend.plot.toolTip() 为空；来源 Tooltip 只在标题；不再发生全局 Tooltip 争用。
10. NayutoAI 今日分时 Tooltip 显示模型；历史日期和多分钟桶正确；其他 Provider 隐藏模型行。
11. Token 活动、年度/分时切换、日期、图例、导航条、统计和底部区域仍存在并工作。
12. 快速切换和迟到结果不串模型数据。
13. 小米、DeepSeek 等原有测试全部通过。

至少运行：

python -m pytest -q tests/test_nayuto.py tests/test_qt_ui.py tests/test_refresh.py
python -m pytest -q
python -m compileall api data ui tests
python -m ruff check .
pyright
git diff --check

如果环境允许，启动程序做真实 UI 验证，并用重新登录的新凭证对照真实 NayutoAI API。静态检查不能冒充运行时验证；未执行的真实登录/API、UI、打包和完整测试必须逐项说明。

十、最终交付说明

完成后请给出：

1. 修改文件清单。
2. 每条需求对应的实现位置。
3. 日模型和分钟模型的完整调用链、字段映射和数据结构。
4. 7 天分组、模型集合、排序、颜色和 Tooltip 计算规则。
5. 分钟模型数据库结构、原子替换、保留期和 Provider 隔离结果。
6. Tooltip 闪烁根因和最终修复点。
7. Token 活动及所有原功能保留证明。
8. 已执行测试与精确结果。
9. 未验证内容和剩余风险。
10. 明确回答小米、DeepSeek、Codex、Cursor 及其他现有 Provider 是否保持不变。
```
