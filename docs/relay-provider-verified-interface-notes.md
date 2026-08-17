# 中转服务商已核实接口补充

根据 `C:\Users\Administrator\Desktop\中转请求和响应参数.txt` 中的真实浏览器请求样例，实施时采用以下已确认口径：

- `GET /portal/auth/me`：账户信息和余额，重点字段为 `balance`、`status`。
- `GET /portal/user/dashboard/stats`：今日与累计汇总，可用于主面板展示或对账。
- `GET /portal/user/usage?page=1&page_size=50`：请求级分钟明细主数据源。
- `GET /api/v1/usage/dashboard/trend?...&granularity=hour&timezone=Asia%2FShanghai`：小时趋势或对账参考，不能替代分钟详情。
- `input_tokens`：未命中缓存输入。
- `cache_read_tokens`：命中缓存输入。
- `output_tokens`：输出。
- `total_tokens = input_tokens + cache_read_tokens + output_tokens`。
- `actual_cost`：用户实际支付金额，分钟金额汇总该字段；不能使用 `total_cost` 替代。
- `created_at`：UTC ISO-8601时间，转换为 `Asia/Shanghai` 后再按日期和分钟归桶。
- 当前请求使用 `Authorization: Bearer ...`；附件中的旧 Bearer 视为已暴露，不得保存、打印或复用。

本文件是 `relay-provider-integration-requirements.md` 与 `relay-provider-implementation-prompt.md` 的已核实字段补充；如三者存在口径差异，以本文件和实际接口响应为准。
