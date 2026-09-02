更新类型：Bug 修复

本版本修复桌宠保存贴边状态后重新启动时可能异常退出的问题。最低兼容主程序仍为 `1.14.0-beta.1`，平台为 Windows x64，通信协议保持 `1`。

### 修复

- 等待 VPet 两个动画画布完成首帧初始化后再恢复已保存的贴边状态，避免启动动画切换期间访问空画布。
- 为贴边入口增加初始化保护，防止其他早期调用再次触发同类异常。
- 保留左右贴边、重启后恢复贴边、额度气泡和自主活动的现有行为。

### 验证

- 桌宠宿主 Release 构建通过，0 个编译错误。
- 使用真实故障布局 `dockedEdge: false` 启动成功，宿主进入 `ready` 状态并加载 `318` 组动画；标准错误为空，未生成 `host-error.log`。
- 扩展安装、更新、打包与更新客户端相关测试 `147` 项通过；`git diff --check` 通过。
- 完整桌宠冒烟检查未出现启动崩溃或动画加载错误；其中既有的 `cloudHiddenAfterNormalRestore` 时序断言在修复前后均可能波动，与本次改动无关。

### 安装、兼容与校验

- 主程序 `1.14.1` 或更新版本可在启动检查时提示已安装桌宠更新，也可进入“设置 → 桌宠”手动检查并升级。
- 附件：`TokenMeter-Pet-v0.1.3-x64.zip`、`TokenMeter-Pet-Host-v0.1.3-x64.zip`、`extension.json`、`SHA256SUMS.txt`。校验文件覆盖两个 ZIP 与版本清单；SHA256 不等同于独立发布签名。
- 通信协议和最低兼容主程序版本未变，`pet-v0.1.2` 用户可直接升级；稳定主程序只选择稳定桌宠扩展。

### 来源与授权

- 默认角色及动画来自 [LorisYounger/VPet](https://github.com/LorisYounger/VPet)，版权所有：虚拟主播模拟器制作组；核心源码采用 Apache License 2.0，动画和图片采用上游单独授权。
- 本扩展免费分发并附完整上游授权声明、来源链接和依赖许可证；不得收费分发动画文件，商业用途需遵守上游额外要求。详见包内 `VPet-README.md`、`VPet-LICENSE.txt` 和 `THIRD_PARTY_NOTICES.md`。
- 完整差异：[pet-v0.1.2 → pet-v0.1.3](https://github.com/zensoku142/TokenMeter/compare/pet-v0.1.2...pet-v0.1.3)。
