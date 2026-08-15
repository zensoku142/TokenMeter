# TokenMeter macOS 毛玻璃界面改造实施交接

## 1. 交接目标

在不改变 TokenMeter 现有功能、业务行为、接口协议、配置协议和操作习惯的前提下，将主面板与悬浮球改造成已确认的 macOS 风格深浅色毛玻璃视觉。

本次改造属于纯 UI 视觉优化，不是功能重构。实现时必须继续复用现有 PySide6 控件、信号、数据模型、Provider、刷新链路、主题控制器和窗口行为。

## 2. 已确认设计基准

| 主题 | 设计稿 |
| --- | --- |
| 深色主题 | [approved-dark.png](approved-dark.png) |
| 浅色主题 | [approved-light.png](approved-light.png) |

两张图片尺寸均为 `1487 × 1058`，属于设计展示画布，不是程序窗口尺寸真值。

> 设计图中的桌面背景只用于展示透明材质、模糊和色彩折射。程序不得内置该背景，不得覆盖用户桌面。设计图把主面板与两个悬浮球同时放在一张画布中，仅用于展示组件；实际运行必须继续保持“悬浮球 / 展开面板”互斥。

## 3. 当前代码基线

- 仓库：`D:\phpstudy_pro\WWW\cyf\TokenSpider`
- 分支：`release/1.11.3`
- HEAD：`1c91f82`
- UI：PySide6，自绘控件与 QSS 混合实现。
- Provider 注册顺序：DeepSeek、Xiaomi MiMo、Codex。
- 标准展开宽度：`820px`；最小宽度：`640px`。
- “今日分时”面板高度：`550px`。
- Codex 年度活动面板高度：`496px`。
- 悬浮球默认尺寸：`88px`，配置范围最高 `124px`。

开始实现前必须执行：

```powershell
git status --short
git diff -- ui/qt_panel.py tests/test_qt_ui.py
```

当前工作区已有以下用户修改，必须保留：

- `ui/qt_panel.py`：Codex 摘要不显示 `codex-extra-*` 专项额度；套餐到期只显示月日。
- `tests/test_qt_ui.py`：覆盖 Spark 专项额度不占卡片及到期年份隐藏。
- `docs/date-picker-handoff/`：已有未跟踪交接目录，与本任务无关，不得删除、移动或纳入格式化。

本交接目录本身也是新增未跟踪内容。

## 4. 功能兼容红线

### 4.1 Provider 与数据

必须保留：

- 顶部 Provider 下拉框继续来自 `list_providers()`，不得写死 Provider 列表。
- 选择项继续通过 `provider_selected` 信号进入 `FloatingWidget._switch_provider`。
- 当前 Provider、选中项、弹层定位、屏幕边界处理、键盘焦点和无障碍名称保持有效。
- DeepSeek、MiMo、Codex 的请求、解析、缓存、历史数据、错误降级和统计口径不得修改。
- 不得为了视觉效果改变 `TokenData`、`ProviderQuota`、`QuotaWindow`、数据库或配置结构。
- Codex 继续使用主额度窗口驱动悬浮球，不得回退成虚假的金额视图。

### 4.2 顶部操作

必须保留现有位置和行为：

- 应用图标与 TokenMeter 标题。
- Provider 快速切换。
- DeepSeek 峰谷计价提示。
- 浅色 / 深色主题切换及跟随系统状态同步。
- 设置、刷新、收起按钮。
- 标题栏拖拽窗口。
- 设置窗口打开时不因失焦误收起主面板。

### 4.3 主面板

必须保留：

- 左侧主指标与两个次指标。
- 右侧近 7 天趋势图。
- Codex 年度 Token 活动热力图。
- DeepSeek / MiMo 的“年度活动 / 今日分时”切换、日期选择、分钟图、图例与摘要。
- 底部五项统计、连接状态、刷新状态、错误 / 部分成功 / 缓存状态。
- 加载中、刷新中、无数据、认证失效、网络失败等原有文案和状态。
- `640px` 至 `820px` 宽度适配，以及 `496px` / `550px` 两种高度切换。

### 4.4 悬浮球与窗口行为

必须保留：

- 单击悬浮球展开主面板；单击收起按钮或按 `Esc` 收起。
- 拖动悬浮球和标题栏移动窗口，移动阈值保持 `5px`。
- 悬浮球位置保存、多显示器、DPI、工作区限制。
- 左右屏幕边缘吸附、自动隐藏、恢复与动画。
- 悬浮球置顶与展开面板获得焦点的现有切换。
- 失焦自动收起设置及设置窗口例外。
- Codex 百分比模式与 API 金额 / 余额模式。
- Codex 液面波动动画、悬停、按下、峰值提醒与无障碍信息。
- 展开状态继续隐藏悬浮球；不得把设计稿中的两个球同时显示在主面板下方。

### 4.5 任务外功能

不得修改：

- `api/` Provider 请求与认证。
- `data/`、SQLite、历史数据和统计口径。
- `config/`、凭据、配置保存和数据目录迁移。
- 自动更新、安装器、版本号、Release Notes。
- 系统托盘、通知、MiMo Cookie 续期。
- 设置窗口字段、保存 / 取消语义。
- 日期控件交接目录中的用户内容。

## 5. 现有代码入口

| 文件 | 主要入口 | 本任务职责 |
| --- | --- | --- |
| `ui/qt_theme.py` | `ThemeTokens`、`LIGHT_THEME`、`DARK_THEME`、`build_app_style()`、`fluent_icon()` | 深浅色视觉 token、QSS、共享图标；不得改变主题切换协议。 |
| `ui/qt_panel.py` | `MainPanel` | 主面板结构和数据渲染保持不变，只调整视觉。 |
| `ui/qt_panel.py` | `ProviderQuickCombo.paintEvent()` | 顶部 macOS Pop-up Button 的折叠态自绘。 |
| `ui/qt_panel.py` | `ProviderOptionDelegate`、`ProviderQuickCombo.showPopup()` | 下拉菜单行、选中态、悬停态、尺寸和屏幕边界。 |
| `ui/qt_panel.py` | `MetricCard`、`TrendCard`、活动区、统计区 | 视觉层级、分隔、字体和绘制色；不改数据与布局关系。 |
| `ui/qt_ball.py` | `FloatingUsageBall.paintEvent()`、`_paint_quota()` | 深浅玻璃球壳、进度 / 液面、文字与状态绘制。 |
| `ui/qt_widget.py` | `FloatingWidget` | 只负责窗口组合与交互；除可选原生背景接入外，原则上不改。 |
| `tests/test_qt_ui.py` | 主面板、主题、Provider、悬浮球、窗口行为测试 | 增加视觉状态的结构性断言，保留全部旧测试。 |

关键现状：

- `ProviderQuickCombo` 是自绘控件，QSS 中的 `headerProviderCombo` 只提供基础样式；设计稿中的毛玻璃下拉框应优先在其 `paintEvent()` 中实现。
- 下拉框固定 `132 × 28`，弹层宽 `132`；当前 3 个 Provider 时弹层为 `132 × 134`，每行 `36px`。
- 主面板外层窗口已经使用 `WA_TranslucentBackground`，但仓库当前没有 Acrylic、Mica 或 DWM 系统背景实现。
- 悬浮球完全由 QPainter 绘制，现有百分比状态包含动态液面，不是静态环形图。

## 6. 推荐的最小实现顺序

### 第 0 步：固定行为基线

在改造前运行 `tests/test_qt_ui.py`，保存深浅色、Codex、DeepSeek、MiMo 的运行截图。所有失败先判断是否为现有失败，不得为通过新视觉测试修改业务逻辑。

### 第 1 步：扩展现有主题 token

只在多个组件确实复用时扩展 `ThemeTokens`，例如：

- 玻璃表面与抬升表面。
- 内高光、外边缘、弱分隔线。
- 控件默认 / hover / focus / disabled 状态。
- 窗口阴影、悬浮球边缘和进度光。

不要新增第二套主题管理器，不要把颜色散落在 `qt_panel.py`。如果少量 alpha 只用于单个自绘控件，可由现有 token 在绘制时派生。

### 第 2 步：先完成 Qt 安全降级视觉

先用现有 QPainter、渐变、透明色、边缘高光和阴影完成可用的玻璃观感，确保在所有支持版本上都能启动和操作。

真实背景模糊作为增强能力，不能成为主界面正常工作的前置条件。

### 第 3 步：评估可选原生背景

仓库目前没有原生背景代码。如果必须接入 Windows 11 系统背景：

- 先对当前 `Qt.Tool + FramelessWindowHint + WA_TranslucentBackground` 组合做独立可行性验证。
- 原生调用必须隔离为小型、无状态、可失败的辅助函数；不要散落在 Panel、Provider 或刷新代码里。
- 推荐接口语义：`try_apply_window_backdrop(hwnd, theme) -> bool`。
- 仅在 Windows 且系统支持时执行；异常、返回失败或系统版本不支持时立即使用 Qt 降级视觉。
- 不新增第三方依赖，不调用未确认的私有 API，不因效果失败阻止窗口显示。
- 主题切换时可以重新应用材质，但不得重建窗口或丢失用户状态。

Microsoft 文档说明 `DWMWA_SYSTEMBACKDROP_TYPE` 从 Windows 11 Build 22000 开始支持；这不等于它已证明适配本项目的分层无边框 Tool 窗口，实现者仍需实机验证：

<https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute>

### 第 4 步：改造主面板，不改布局树

- 保留 `MainPanel` 现有控件结构、固定尺寸与伸缩比例。
- 先改 `panelFrame`、Header、分隔线、按钮、图表和热力图的颜色 / 透明度 / 高光。
- 不根据 `1487 × 1058` 设计画布重做程序尺寸。
- 不在每个区域外套新卡片；设计稿依赖间距、分组、弱分隔和材质层级。

### 第 5 步：单独改造 Provider 下拉框

- 保持 `132 × 28`、数据模型、信号和弹层定位。
- 折叠态使用半透明圆角 Pop-up Button；箭头使用清晰的单个 `chevron-down`。
- 保持 default、hover、focus/open、disabled 状态。
- 弹层继续使用 `ProviderOptionDelegate`，保留选中项、check、悬停与最大 6 行。
- 不替换成新框架控件，不重新实现 Provider 切换。

### 第 6 步：改造悬浮球绘制

- 保留 `FloatingUsageBall` 的信号、定时器、尺寸与文本压缩规则。
- 在现有球壳上增加透明玻璃、边缘折射、内高光和柔和阴影。
- 百分比模式继续按真实剩余百分比显示液面和动画；设计稿中的环形光可作为外层辅助视觉，不能替代数据语义。
- 金额模式继续显示今日使用与余额。
- 保留 hover、pressed、peak、dark/light 以及 0%、未知值、长文本状态。

### 第 7 步：回归与实机视觉 QA

- 先跑针对性 UI 测试，再跑完整测试。
- 在 Windows 10 / Windows 11 或至少“原生材质支持 / 不支持”两种路径验证。
- 用相同窗口尺寸分别截图，对照两张设计稿检查主面板、下拉框、悬浮球和主题切换。

## 7. 预计修改范围

优先限制在：

- `ui/qt_theme.py`
- `ui/qt_panel.py`
- `ui/qt_ball.py`
- `tests/test_qt_ui.py`

仅当原生背景可行且确有必要时，允许：

- 在 `ui/` 下新增一个小型 Windows 背景辅助模块。
- 在 `ui/qt_widget.py` 的窗口初始化和主题变更位置接入该辅助模块。

除上述情况外，不应修改其他文件。

## 8. 验证命令

```powershell
python -m pytest tests/test_qt_ui.py -q
python -m pytest -q
python main.py
git diff --check
git status --short
```

静态检查、自动化测试与人工运行验证必须分开报告。`git diff --check` 通过不代表程序已运行，也不代表原生毛玻璃在目标系统生效。

## 9. 人工验证矩阵

- [ ] 浅色、深色、跟随系统三种主题模式。
- [ ] Codex、DeepSeek、MiMo 三个 Provider 快速切换。
- [ ] Provider 下拉框 default / hover / focus / open / selected / disabled。
- [ ] Codex 每周额度、套餐、到期日、无额度、刷新中、网络失败。
- [ ] DeepSeek / MiMo 金额、余额、月累计、峰谷提示。
- [ ] 年度活动 / 今日分时、日期切换、图例开关、历史日期。
- [ ] 悬浮球百分比 / 金额、0%、未知值、hover、pressed、peak。
- [ ] 单击展开、收起、`Esc`、标题栏拖动、球拖动。
- [ ] 左右边缘吸附、自动隐藏、恢复、多屏、125% / 150% DPI。
- [ ] 设置窗口打开、失焦自动收起、刷新、系统托盘与通知。
- [ ] 640px 与 820px 宽度下无截断、重叠或高度跳动。
- [ ] 原生材质不可用时仍可启动并呈现安全降级视觉。

## 10. 完成定义

只有同时满足以下条件才可称为完成：

1. 深浅主题视觉与设计稿方向一致。
2. Provider 下拉框与顶部工具控件形成统一的 macOS 玻璃组件语言。
3. 悬浮球与主面板材质统一。
4. 所有现有自动化测试通过，新视觉结构有回归覆盖。
5. 所有兼容红线均通过人工验证。
6. 不修改 Provider、数据、配置、更新、安装和设置语义。
7. 对原生背景支持范围和降级结果给出实机证据，不把推测写成已验证。

## 11. 新会话可直接使用的提示词

```text
请在当前 TokenMeter 仓库中实现 macOS 毛玻璃风格的深浅主题界面。

开始前必须完整阅读：
1. AGENTS.md
2. docs/macos-glass-redesign-handoff/IMPLEMENTATION_HANDOFF.md
3. docs/macos-glass-redesign-handoff/PRODUCT_PROTOTYPE.md

设计基准：
- docs/macos-glass-redesign-handoff/approved-dark.png
- docs/macos-glass-redesign-handoff/approved-light.png

要求：
- 先检查 git status 和现有 diff，保留所有用户修改。
- 先阅读 ui/qt_theme.py、ui/qt_panel.py、ui/qt_ball.py、ui/qt_widget.py 和相关测试，再编码。
- 只做 UI 视觉改造，不改变现有功能、数据口径、接口、配置、窗口行为或操作习惯。
- 优先复用现有 ThemeTokens、自绘控件、信号和布局。
- 原生毛玻璃必须可失败并安全降级，不得影响启动。
- 按最小修改分阶段实施，每阶段运行针对性测试。
- 不修改版本号，不发布，不提交，不推送，除非我另行明确要求。
- 完成后分别报告静态检查、自动化测试、人工运行和原生材质实机验证结果。
```
