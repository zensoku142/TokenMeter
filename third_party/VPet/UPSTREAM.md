# VPet 核心源码维护说明

- 上游项目：https://github.com/LorisYounger/VPet
- 导入提交：`b6f7b00363529bafe3e7fc14bf51e17640941691`
- 导入范围：`VPet-Simulator.Core/`、`.editorconfig`、`LICENSE` 和原版 `README.md`。
- 导入时上述文件保持原样；本目录不包含上游 `.git`、编译产物或动画资源，也不是 Git 子模块。

## 开发与协作

宿主默认编译本目录下的 `VPet-Simulator.Core/VPet-Simulator.Core.csproj`。核心改动直接在本仓库提交，可与 `pet_host/`、`ui/vpet_host.py` 的修改放在同一个 PR 中。不要编辑 `build/vpet-upstream/` 的旧核心副本，构建不会使用它。

在仓库根目录执行：

```powershell
python scripts/build_vpet.py
python -m pytest tests/test_packaging.py -q
python examples/vpet_preview.py --verify
```

需要 Python、Git 和 .NET SDK 8 或更新版本；首次构建会下载 NuGet 依赖、运行时及固定版本动画。`bin/`、`obj/` 和 `build/` 下的缓存不纳入版本管理。只编译核心时可运行 `dotnet build third_party/VPet/VPet-Simulator.Core/VPet-Simulator.Core.csproj -c Release`，无需先下载动画。

## 更新上游

本地维护补丁：

- `GraphCore.cs`：空闲缓存清理统一使用 UTC，与动画访问时间保持一致。
- `PNGAnimation.cs`：精灵图构建最多并发 2 个，原始帧逐张解码、绘制并释放，避免冷启动同时持有全部帧。

升级时对比本文件记录的上游提交，合并核心源码改动，保留本仓库的修改，并更新此处的导入版本和修改说明。构建脚本不会下载或覆盖本目录。若同时升级动画，需同步修改 `scripts/build_vpet.py` 的 `REVISION` 和 `pet_host/THIRD_PARTY_NOTICES.md`，重新构建并验证动作与接入流程；不要直接覆盖有本地改动的资源缓存。

源码许可证原文保存在 `LICENSE`。原版 `README.md` 保留完整的动画与图片授权声明，其截图和多语言文档仍需到上游查看。本目录纳入核心源码不改变动画的授权或分发条件。
