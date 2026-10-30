"""TokenMeter 配置字段示例；请通过设置窗口保存，不要复制真实凭据。"""

# DeepSeek API credentials
DEEPSEEK_API_KEY = ""  # 可选：官方 API Key，用于稳定的余额接口
DEEPSEEK_AUTH = ""  # 填入你的 Bearer token
DEEPSEEK_COOKIE = ""  # 填入你的 Cookie 字符串

# API base URL
DEEPSEEK_BASE = "https://platform.deepseek.com"

# 可选：按北京时间显示 DeepSeek 峰谷计价状态，仅提示、不参与账单计算
DEEPSEEK_PEAK_PRICING_ENABLED = False
DEEPSEEK_PEAK_PERIOD_1_START = "09:00"
DEEPSEEK_PEAK_PERIOD_1_END = "12:00"
DEEPSEEK_PEAK_PERIOD_2_START = "14:00"
DEEPSEEK_PEAK_PERIOD_2_END = "18:00"
DEEPSEEK_PEAK_WEEKDAYS = [0, 1, 2, 3, 4]  # 周一至周日对应 0 至 6
# 周末（包括调休上班的周末）自动按空闲时段；法定节假日可填写日期或日期范围。
DEEPSEEK_OFFPEAK_DATES = "2026-01-01..2026-01-03,2026-02-15..2026-02-23"

# 小米 MiMo 控制台凭据
MIMO_COOKIE = ""  # 通常包含 serviceToken、userId、slh、ph
MIMO_API_PLATFORM_PH = ""  # 兼容旧配置；完整 Cookie 已包含时留空
MIMO_API_KEY = ""  # 推理 API Key；控制台用量查询不会使用
MIMO_BASE = "https://platform.xiaomimimo.com"

# Codex 默认读取本机 CLI 的 OAuth 登录文件；仅在凭据目录不是默认位置时填写。
CODEX_HOME = ""

# Cursor 读取本机登录；NayutoAI 使用控制台凭据。
CURSOR_GLOBAL_STORAGE = ""
NAYUTO_AUTH = ""
NAYUTO_BASE = "https://nayutoai.xyz"

# 可选：指定快照路径时优先读取该文件；留空先读取本机 OAuth 登录，无登录时回退默认快照。
CLAUDE_STATUSLINE_FILE = ""
CLAUDE_ACCESS_TOKEN = ""  # 可选：默认只读本机 Claude Code OAuth 登录
CLAUDE_CREDENTIALS_FILE = ""  # 可选：指定非默认的登录文件

# 可选值见 config/defaults.py 的 PROVIDER_IDS。
ACTIVE_PROVIDER = "deepseek"
BACKGROUND_PROVIDER_IDS = []  # 仅勾选需要后台同步的平台

# Refresh interval in milliseconds
REFRESH_INTERVAL = 60_000  # 60 seconds
# Today intraday chart display interval in minutes; raw cache remains minute-level
MINUTE_USAGE_INTERVAL_MINUTES = 5
# Today intraday chart type: "bar" or "line"
MINUTE_USAGE_CHART_TYPE = "bar"
EDGE_HIDE_ENABLED = True
QUOTA_WEEKLY_RING_COUNTERCLOCKWISE = False

# 可选桌宠；同时显示开关关闭时，桌宠替代悬浮球。
VPET_ENABLED = False
VPET_SHOW_BALL = False

# Widget appearance
WIDGET_COMPACT_SIZE = 96
WIDGET_EXPANDED_SIZE = (820, 564)
UI_THEME = "dark"  # 可选值：system、light、dark
UI_LIGHT_ACCENT_COLOR = "#2F72E8"
UI_DARK_ACCENT_COLOR = "#3478F6"
UI_LIGHT_PANEL_OPACITY = 100  # 70-100
UI_DARK_PANEL_OPACITY = 100  # 70-100
BG_COLOR = "#071427"
ACCENT_COLOR = "#2f6fe4"
TEXT_COLOR = "#edf4ff"
