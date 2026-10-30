# 小龙娘角色

素材来源：https://github.com/jingh12312-rgb/bailong-desktop-pet ，v0.5.1。
由用户提供本地完整包并要求迁入；保留上游 README 与 ASSETS 声明。

`actions.json` 保留全部 35 段动画的缩放、位移、时长元数据；原始透明动画不纳入 Git。首次构建前运行 `python scripts/fetch_bailong_assets.py`，从上游 v0.5.1 下载并校验 SHA256。
`scripts/build_vpet.py` 使用明确保留透明通道的 libvpx-vp9 解码为 250px APNG，
按原素材的底部居中规则定位，并生成现有 VPet 内核使用的动画槽位。
产物只包含人物与动作资源，不打包原项目 Python 程序、解码器、账户查询、对话或聊天历史。

在主程序“设置 → 桌宠”的角色选择中安装“小龙娘”并设为默认；桌宠右键“角色”支持即时切换。
共享现有窗口、拖动、Ctrl＋滚轮缩放、自主活动、贴边、额度气泡、显示来源与多开。
右键“小龙娘动作”可以查看待机、互动、工作、移动及拖拽/额度共 35 段动画。
这里的“工作动作”仅播放动画，不执行任务或发起 AI 对话。

开启“自主活动”后，空闲时每 15 秒从全部 35 段动画中随机取一段，每轮不重复，
全部取完再开始下一轮。走路、跑步和飞行按素材清单的移动时间窗产生实际位移，
在屏幕边缘自动转向并保持在当前屏幕工作区内；拖拽姿态和移动动作结束后接落地段。
拖动、菜单、提醒、隐藏、休眠和切换角色会取消当前自主动作与移动；贴边时继续暂停自主活动。
关闭自主活动后仍可通过右键手动播放动作。

构建和预览：

```powershell
python scripts/build_vpet.py --output build/vpet-bailong --framework-dependent
python examples/bailong_preview.py
```

独立预览使用构建目录和演示用量，不修改真实用户设置。首次构建需要 FFmpeg，
通过 `TOKENMETER_BUILD_FFMPEG` 指定其可执行文件；发布包无需安装 FFmpeg。
主程序和桌宠的公开版本号仅在准备发布时统一调整。
