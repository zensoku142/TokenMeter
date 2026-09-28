# VPet 精简集成试用版：来源与授权

本模块复用 VPet-Simulator.Core 和原版默认角色的鼠标互动、自主移动与待机动画。

- 上游项目：https://github.com/LorisYounger/VPet
- 核心源码导入版本与动画固定版本：`b6f7b00363529bafe3e7fc14bf51e17640941691`
- 核心源码维护目录：`third_party/VPet/VPet-Simulator.Core/`，修改随 TokenMeter 仓库提交；下载缓存中的核心副本不参与编译。
- 默认角色与动画版权所有：虚拟主播模拟器制作组。
- 源码许可：Apache License 2.0，源码仓库见 `third_party/VPet/LICENSE`，发布包见同目录 `VPet-LICENSE.txt`。
- 动画及图片采用单独授权，完整上游声明见源码仓库的 `third_party/VPet/README.md` 或发布包同目录 `VPet-README.md` 的“动画版权声明与授权”和“图片版权声明与授权”。

## 默认动画授权摘要

非商业用途：向用户告知动画来源，并提供上游项目链接后可免费使用。

商业用途：首次使用需醒目提示来源及上游链接，在可便捷访问的页面继续注明来源；禁止通过出售动画文件盈利，并须联系原作者。不要将本试用包视为已取得额外商业授权。

分发动画文件：必须保留完整授权信息与上游链接，禁止付费/收费分发动画文件。内置图片授权同上；上游 Zip 照片图库禁止商用，本包不包含该图库。

## 本集成的修改范围

内核源码保持原样。TokenMeter 增加独立 WPF 宿主、窗口级鼠标拖动、仅限本机父子进程的用量通信、统一退出和布局持久化。资源包保留所选鼠标互动、自主移动及待机动作的全部状态与过渡帧，移除投喂、睡眠、工作/学习/玩耍、升级、养成变化、音乐、图库、Steam 与联机内容；删除 `vup.lps` 的工作配置并关闭养成计算。扩展保持免费并保留上游授权声明；商业用途仍须遵守上述额外要求。

依赖还包括 LinePutScript、LinePutScript.Localization.WPF、Panuon.WPF、Panuon.WPF.UI、SkiaSharp 与 Microsoft .NET。构建清单在 `TokenMeter.Pet.deps.json`；发布包需一并保留其许可证。

## 鲸鱼娘图片与动画（开发批次）

- 来源：https://github.com/PC2005-cloud/dsh-pet
- 固定源码与素材提交：`a2993705466438f82954b9547f7b4f151e1f773f`。
- 原项目 `LICENSE` 为代码的 MIT 许可；原项目 README 的素材声明另行规定动画、提示词和源视频允许开源使用、禁止商用，并要求在衍生作品的介绍、展示和分发处附原作者 GitHub 地址。本仓库免费开源，保留原作者地址；素材不得用于商业用途。代码许可不能解释为图片或动画的商业授权。
- 移植图片：`dsh-pet/assets/memes/可爱.png` → `resources/pet/whale/portrait.png`，用于角色选择菜单图标。其图片授权边界按原项目的素材声明审慎处理。
- 原始动画：完整移植固定提交的 `dsh-pet/assets/webm/*.webm` 共 106 段，位于 `resources/pet/whale/webm/`；`actions.json` 仅摘取同一提交 `assets/config.jsonc` 中的动画池、权重、事件档位和物理参数，106 个名字逐一与原始文件核对。点击、拖拽和自动动画链的调度及物理计算参考该项目代码，MIT 文本随包位于 `resources/pet/whale/DSH-CODE-LICENSE.txt`。

WPF 通过现有 VPet APNG 播放器显示源片段的真实帧。常用 11 段预转码资源：用 FFmpeg `libvpx-vp9` 解码 Alpha，以 15 fps 采样，裁剪原 640×360 画面的中央 480×360 区域，缩放到 250×188，再在下方补 62 像素透明边，形成 250×250 的 APNG。`idle/wave/bubbles/tail/blue-whale/yawn.png` 分别对应 `待机呼吸休闲`、`点击回应-元气挥手`、`鲸鱼吐泡泡特效`、`用鲸鱼尾巴拍打地面`、`蓝鲸现世`、`哈欠连天`；`say/head/body/drag/move` 的 `-start/-loop/-end.png` 分别取 `碎碎念-发呆碎碎念`、`点击回应-开心跃动`、`点击回应-挠痒咯咯笑`、`被鼠标拖拽悬空反馈`、`螃蟹走路` 的 0–1、1–9、9–10 秒。`side-start/loop/end.png` 由 `待机呼吸休闲.webm` 分段，供贴边姿态使用。

全部 106 段动作在构建阶段从同名 `webm/<动作名>.webm` 转为包内 `apng/<动作名>.png`，完整画面以 15 fps 缩放到 250×141 并补透明边。公开包保留原始 WebM 和真实 APNG 帧，不包含 FFmpeg 可执行文件。用户 `main-animation/webm/<动作名>.webm` 仍优先于包内同名素材；播放自定义 WebM 时需用户自行安装 FFmpeg 并加入 `PATH`，内容变化会生成新缓存。GIF 仅为参考项目预览，未作为播放资源。
