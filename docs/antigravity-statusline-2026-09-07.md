# Antigravity CLI 额度接入

更新类型：功能新增。本入口读取 Antigravity CLI 官方状态栏导出的模型额度，不代表 Antigravity IDE 的独立接口接入，也不是 AI Studio API Key 或 Gemini Code Assist 的额度入口。

## 配置步骤

1. 准备 Python 3.12，以及项目中的 `scripts/antigravity_statusline.py` 和同目录的 `scripts/claude_statusline.py`。后者只提供共用的原子文件写入函数，两份文件需要保留在同一目录。
2. 在 `~/.gemini/antigravity-cli/settings.json` 的现有配置中设置 `statusLine`，按实际路径修改：

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "python \"E:\\github\\TokenSpider\\scripts\\antigravity_statusline.py\""
     }
   }
   ```

3. 在 Antigravity CLI 中打开 `/usage`，使 CLI 更新额度。状态变化时会调用采集脚本。
4. TokenMeter 中选择 Antigravity；快照路径留空时读取 `~/.gemini/antigravity-cli/tokenmeter-usage.json`。

已有自定义状态栏时，可将快照写入集成进现有脚本，或采用上面的额度状态栏。该命令仅消费 CLI 传入的 JSON，不调用 `agy -p`，也不发送提示词。

自定义快照位置可给采集命令增加 `--output "D:\\data\\antigravity-quota.json"`，并在 TokenMeter 的 Antigravity 设置中填写相同路径。路径含空格时保留引号。JSON 配置中的反斜杠按 JSON 规则转义。

## 数据含义与边界

- 只读取 `quota` 中每个模型/额度桶实际给出的剩余份额及重置时间；不从 `context_window` 推算订阅额度，不假定固定的每日/每周时长或 Token 总量。
- 快照显示 `local_snapshot` 来源及导出时间。导出时间不是服务端采集时间；CLI 自身保留旧额度时，应在 CLI 中执行 `/usage` 更新。
- 超过 15 分钟未导出的快照返回过期提示。无额度时写入空快照，清除上一会话的读数；异常桶不会影响同次输入中的其他有效桶。
- 默认用 CLI 提供的邮箱计算不可逆账号指纹，快照不包含原始邮箱、登录令牌、对话、工作目录或 transcript 路径。
- 若 CLI 未提供账号字段，允许显示当前快照，但禁用跨会话缓存和低额度通知。可使用 `--account-scope` 指定非敏感账号别名，换账号时需同步更新该别名。
- 删除 Antigravity 配置只停止 TokenMeter 监控并清除自定义路径，不删除 CLI 登录或快照文件；从“全部平台”显式选择后可重新启用。

## 来源

字段与配置来自 Google 的[状态栏自定义文档](https://antigravity.google/docs/cli/statusline/)；主动更新额度的入口见[官方 `/usage` 说明](https://www.antigravity.google/docs/cli/commands/usage)。核实日期：2026-09-07。

## 验证

使用官方字段形状构造测试输入，验证了独立脚本运行、未知/异常数值、空额度、相对重置时间、时区、文件大小限制、账号切换和快照过期；品牌图标在明暗主题下验证可见。未使用真实 Antigravity 账号进行端到端联调。
