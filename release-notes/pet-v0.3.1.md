更新类型：发布流程修正

本版本将桌宠资源构建来源调整为 TokenMeter 自己的仓库，保留萝莉斯、鲸鱼娘和小龙娘；桌宠扩展独立发布，不占用仓库 Latest。最低兼容主程序为 `1.17.0`，平台 Windows x64，通信协议为 `1`。

### 修正

- 鲸鱼娘和小龙娘素材使用本仓库内的文件进行构建；小龙娘原始 35 段动画已纳入仓库。
- 资源说明变更导致资源校验值变化，因此以新版本发布，避免覆盖 `0.3.0` 后与用户已安装的角色资源混用。
- 程序内安装或添加角色时，从 TokenMeter 自己的 GitHub Release 下载对应角色包。

### 验证

- 本地桌宠扩展、更新、打包与小龙娘相关测试 169 项通过、1 项跳过。
- 桌宠发布流水线执行扩展安装、打包和更新测试，构建完整包及分角色包并生成校验文件。

### 安装、兼容与校验

- 附件包括完整包、宿主包、萝莉斯、鲸鱼娘和小龙娘分角色包，以及 `extension.json`、`SHA256SUMS.txt`。
- 已安装 `0.3.0` 的用户可先更新桌宠，再添加其他角色；更新保留已安装角色与偏好。
- SHA256 用于完整性校验，不等同于独立发布签名。

### 来源与授权

- 小龙娘素材来自 [bailong-desktop-pet](https://github.com/jingh12312-rgb/bailong-desktop-pet) v0.5.1，来源和授权声明随资源保留。
- 鲸鱼娘素材来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，仅限非商业使用；萝莉斯来自 [LorisYounger/VPet](https://github.com/LorisYounger/VPet)，各自授权声明随包保留。
- 完整差异：[pet-v0.3.0 → pet-v0.3.1](https://github.com/zensoku142/TokenMeter/compare/pet-v0.3.0...pet-v0.3.1)。
