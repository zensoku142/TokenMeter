# README 产品图原始截图

采集日期：2026-08-31。主程序源码版本为 `1.14.0-beta.1`，桌宠扩展版本为 `0.1.0-beta.1`；本次截图不改变项目版本号。

这些图片来自当前项目实际运行的 Qt / WPF 组件，未用 AI 重绘界面。为避免披露账户信息，截图使用隔离预览中的演示数据，不读取账户凭据或私人用量，也不修改用户配置。它们是组件窗口的运行截图，不是用户桌面的整屏截图。

## 原始图片

| 画面 | 图片 | 尺寸 |
| --- | --- | --- |
| Codex 浅色面板 | [panel-light.png](panel-light.png) | 1640 × 992 |
| Codex 深色面板 | [panel-dark.png](panel-dark.png) | 1640 × 992 |
| Codex 自定义紫色面板 | [panel-violet.png](panel-violet.png) | 1640 × 992 |
| DeepSeek 深色余额与今日分时面板 | [panel-deepseek.png](panel-deepseek.png) | 1640 × 1100 |
| 浅色额度悬浮球 | [ball-light.png](ball-light.png) | 248 × 248 |
| 深色额度悬浮球 | [ball-dark.png](ball-dark.png) | 248 × 248 |
| 自定义紫色额度悬浮球 | [ball-violet.png](ball-violet.png) | 248 × 248 |
| DeepSeek 金额悬浮球 | [ball-deepseek.png](ball-deepseek.png) | 248 × 248 |
| 桌宠与头顶额度气泡 | [pet-floating.png](pet-floating.png) | 244 × 286 |
| 桌宠贴边与额度气泡 | [pet-docked.png](pet-docked.png) | 192 × 378 |

DeepSeek 示例重点展示“今日分时”：按 5 分钟显示缓存命中、未命中和输出 Token 的消耗变化，保留程序的“估算”标记。同时显示今日使用金额 `¥3.94` 和账户余额 `¥128.64`；金额悬浮球沿用同一组演示数据。Codex 展示订阅额度和年度活动，额度及桌宠气泡示例为剩余 `65%`。

## 采集与排版

- 面板和悬浮球：运行 `ui/qt_widget.py`、`ui/qt_panel.py` 和 `ui/qt_ball.py` 的生产组件，以 Qt 2 倍缩放导出窗口原始像素。Codex 面板状态栏明确标注“演示数据”。
- 桌宠与气泡：运行当前本地宿主构建，通过 `pet_host/PetWindow.CloudChecks.cs` 中的 `CaptureCloudPreview` 导出实际 WPF 控件，保留宿主生成的背景及贴边裁切。
- [产品介绍图](../readme-hero.webp)：将原图等比排版为 1920 × 1624 的 WebP，添加标题、说明和来源，不更改截图中的界面文字或控件。额外保留 Codex 深色原图供对照。
- 本次仅进行图片与文档验收；隔离桌面下的宿主检查不能替代真实桌面的鼠标捕获、拖拽和焦点验证。

## 官网多语言面板

GitHub Pages 会随界面语言切换对应的 Codex 浅色、Codex 深色和 DeepSeek 深色面板。英文、繁体中文、日文和韩文截图位于 `site/assets/panel-*-<locale>.png`，通过 `scripts/render_site_localized_panels.py` 使用生产 Qt 组件、固定演示数据和对应语言资源导出；不读取账户凭据、用户配置或私人用量。

在 Windows 桌面会话中运行：

```powershell
.\.venv\Scripts\python.exe scripts\render_site_localized_panels.py
```

## 桌宠素材声明

桌宠核心、默认角色和动画来源：[LorisYounger/VPet](https://github.com/LorisYounger/VPet)。默认角色与动画版权归虚拟主播模拟器制作组所有。核心代码使用 Apache License 2.0，角色、动画及图片另有授权，详见 [来源与授权](../../../pet_host/THIRD_PARTY_NOTICES.md) 及 [上游完整声明](../../../third_party/VPet/README.md)。这些素材不属于 TokenMeter 自有代码的 MIT 授权范围。
