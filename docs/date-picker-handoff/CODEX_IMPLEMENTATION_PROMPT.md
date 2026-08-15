# 可直接交给 Codex 的实现提示词

```text
请在当前 TokenSpider 仓库中实现“今日分时日期控件”视觉与交互优化。

开始前必须完整阅读：
1. AGENTS.md
2. docs/date-picker-handoff/README.md
3. docs/date-picker-handoff/DESIGN_ADJUSTMENT_PROMPTS.md（仅用于理解视觉边界）
4. ui/qt_panel.py 中 MinuteDateEdit、_update_minute_data、_render_minute_date 及活动区头部布局
5. ui/qt_theme.py 中日期控件和主题 token
6. tests/test_qt_ui.py 中分时日期相关测试

设计参考：
- docs/date-picker-handoff/light-collapsed.png
- docs/date-picker-handoff/light-expanded.png
- docs/date-picker-handoff/dark-collapsed.png
- docs/date-picker-handoff/dark-expanded.png
- 原始布局真值：docs/date-picker-handoff/source-light.png、source-dark.png

注意：生成式设计稿只以日期控件区域为视觉参考。背景图表、文字和比例可能存在生成偏差，禁止据此修改其他区域；主面板布局和业务真值以现有程序与原始截图为准。

目标：
- 保持日期控件现有总尺寸 118×26 和活动标题行布局。
- 未展开改为“左箭头 / yyyy-MM-dd / 右箭头”三段式。
- 左右箭头只逐日切换；保留期首日禁用左箭头，当天/最大可选日期禁用右箭头。
- 只有点击中间日期文字才打开日历；移除日历图标与下拉箭头。
- 展开弹层使用紧凑单行标题“‹ 2026年7月 ›”，年月必须动态生成。
- 周一为首列；周末不使用红色。
- 选中日期为圆角蓝底白字；今天未选中为蓝色描边；非本月和不可选日期使用禁用态。
- 弹层使用单层细边框、8px 圆角和轻量悬浮表面，不使用重阴影，不推动图表布局。
- 浅色、深色和跟随系统主题均复用现有 ThemeTokens。

兼容要求：
- 复用现有日期范围、分时历史读取和 _render_minute_date 链路。
- 不改变数据库、配置、接口、数据保留规则、统计口径、刷新行为、年度活动或图表行为。
- 保持刷新后用户历史日期选择；保持提供商切换时的现有日期重置逻辑。
- 保持 640px 最小宽度和 820×550 标准布局，无重叠、截断或高度变化。
- 不新增依赖，不重构无关模块，不修改无关文件，不覆盖当前未提交改动。

实现建议：
- 如果 QDateEdit 无法稳定满足“只有中间文字打开弹层”，将 MinuteDateEdit 改为固定尺寸的小型组合控件，内部使用前一天按钮、日期按钮、后一天按钮，并提供与当前调用链兼容的 date/dateChanged/setDate/setDateRange/setEnabled 行为或最小等价接口。
- 使用 Qt.Popup 承载日历；可复用 QCalendarWidget 的日期网格、范围和键盘能力，但隐藏原生导航栏，自建紧凑月份标题。
- 不要通过硬编码屏幕坐标或脆弱的鼠标区域判断实现交互。

测试要求：
- 先补充/调整 tests/test_qt_ui.py，再实现。
- 覆盖左右逐日切换、首尾边界禁用、只点击日期打开弹层、日期范围、周一首列、选中历史日期更新图表、刷新保持选择、主题切换与不可用状态。
- 运行相关 Qt 测试和完整测试。
- 本地运行程序，分别截取浅色/深色的展开与未展开状态核对设计。

完成后请给出：
1. 修改文件和每处修改目的；
2. 兼容性说明；
3. 测试命令与结果；
4. 本地视觉验证结果及截图路径；
5. 未验证项或剩余风险。

不要提交、推送或发布，除非我另行明确要求。
```
