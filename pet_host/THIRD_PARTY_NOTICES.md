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

内核源码保持原样。TokenMeter 增加独立 WPF 宿主、窗口级鼠标拖动、仅限本机父子进程的用量通信、统一退出和布局持久化。资源包保留所选鼠标互动、自主移动及待机动作的全部状态与过渡帧，移除投喂、睡眠、工作/学习/玩耍、升级、养成变化、音乐、图库、Steam 与联机内容；删除 `vup.lps` 的工作配置并关闭养成计算。当前为非商业开发验证，公开发布前仍须核对预定分发方式与上述授权条款。

依赖还包括 LinePutScript、LinePutScript.Localization.WPF、Panuon.WPF、Panuon.WPF.UI、SkiaSharp 与 Microsoft .NET。构建清单在 `TokenMeter.Pet.deps.json`；发布包需一并保留其许可证。
