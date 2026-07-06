"""TokenSpider 配置字段示例；v1.1 请通过设置窗口保存，不要复制真实凭据。"""

# DeepSeek API credentials
DEEPSEEK_API_KEY = ""  # 可选：官方 API Key，用于稳定的余额接口
DEEPSEEK_AUTH = ""  # 填入你的 Bearer token
DEEPSEEK_COOKIE = ""  # 填入你的 Cookie 字符串

# API base URL
DEEPSEEK_BASE = "https://platform.deepseek.com"

# 小米 MiMo 控制台凭据
MIMO_COOKIE = ""  # 通常包含 serviceToken、userId、slh、ph
MIMO_API_PLATFORM_PH = ""  # 兼容旧配置；完整 Cookie 已包含时留空
MIMO_API_KEY = ""  # 推理 API Key；控制台用量查询不会使用
MIMO_BASE = "https://platform.xiaomimimo.com"

# Active provider: "deepseek" or "mimo"
ACTIVE_PROVIDER = "deepseek"

# Refresh interval in milliseconds
REFRESH_INTERVAL = 60_000  # 60 seconds
EDGE_HIDE_ENABLED = True

# Widget appearance
WIDGET_COMPACT_SIZE = 96
WIDGET_EXPANDED_SIZE = (820, 564)
BG_COLOR = "#071427"
ACCENT_COLOR = "#2f6fe4"
TEXT_COLOR = "#edf4ff"
