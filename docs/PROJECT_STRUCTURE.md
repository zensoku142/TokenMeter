# 项目目录结构

仓库按运行时职责、交付工具和文档资产分类：

```text
TokenMeter/
├── api/                 # 平台 API、Provider 和计价规则
├── config/              # 配置、凭据、迁移和运行时状态
├── core/                # 应用身份等跨模块基础元数据
├── data/                # 数据目录、聚合和 SQLite 历史记录
├── updater/             # 更新检查、安装和独立更新器入口
├── ui/                  # PySide6 用户界面
├── packaging/           # PyInstaller、Inno Setup 和 Windows 版本资源
├── scripts/             # 构建与发布自动化
├── assets/              # 图标等运行时静态资源
├── docs/                # 项目结构说明和 README 图片
├── examples/            # 示例配置
├── release-notes/       # 按版本维护的发布说明
├── tests/               # 单元测试、UI 测试和打包检查
└── main.py              # 保持 `python main.py` 可用的应用入口
```

根目录中的 `app_identity.py`、`app_update.py`、`config_manager.py`、
`data_directory.py`、`deepseek_pricing.py` 和 `updater_main.py` 是兼容入口。
新代码应直接导入对应分类目录中的实现；兼容入口用于保证旧脚本、插件及测试钩子
在目录调整后仍能工作。

常用命令：

```powershell
python main.py
python -m pytest -q
python -m ruff check .
python -m pyright
python scripts/build_release.py
```
