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

# 普通 Key 只读取该密钥的预算和费用，不是 OpenRouter 全账户余额。
OPENROUTER_API_KEY = ""
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# 国内站人民币；国际站地址为 https://api.moonshot.ai/v1，使用独立的国际站密钥。
MOONSHOT_API_KEY = ""
MOONSHOT_BASE = "https://api.moonshot.cn/v1"

# Copilot 内部额度接口是实验性接入；令牌必须有本人账号的额度读取权限。
COPILOT_TOKEN = ""

# 可选：指定快照路径时优先读取该文件；留空先读取本机 OAuth 登录，无登录时回退默认快照。
CLAUDE_STATUSLINE_FILE = ""
CLAUDE_ACCESS_TOKEN = ""  # 可选：默认只读本机 Claude Code OAuth 登录
CLAUDE_CREDENTIALS_FILE = ""  # 可选：指定非默认的登录文件

# 配置 scripts/antigravity_statusline.py；只读官方 CLI 状态栏快照，不发送模型请求。
ANTIGRAVITY_STATUSLINE_FILE = ""  # 默认 ~/.gemini/antigravity-cli/tokenmeter-usage.json

# 仅显示已知套餐/工具额度比例；国内 API 根地址为 https://open.bigmodel.cn。
ZAI_API_KEY = ""
ZAI_BASE = "https://api.z.ai"

# 编程订阅与API余额是独立入口，Kimi Coding使用其自己的密钥。
KIMI_API_KEY = ""
KIMI_BASE = "https://api.kimi.com/coding/v1"
MINIMAX_API_KEY = ""
MINIMAX_BASE = "https://api.minimax.io/v1"  # 国内站：https://api.minimaxi.com/v1
ELEVENLABS_API_KEY = ""
ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"

# 适用于仍由Code Assist提供额度的Gemini CLI OAuth账号，不是AI Studio API Key。
GEMINI_ACCESS_TOKEN = ""
GEMINI_CREDENTIALS_FILE = ""
GEMINI_PROJECT_ID = ""

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
