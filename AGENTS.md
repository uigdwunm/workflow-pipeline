# Project Agent Instructions

本项目正在重新编写工作流 Skills。

- 旧实现保存在分支 `codex/history/2026-10-09`，按需查阅；新实现遵循当前确认的需求。
- 部署准备：`python3 scripts/prepare-deployment.py check`；生成候选用 `build --output /绝对路径/新目录`，复核用 `verify --package /候选包绝对路径`。环境为 Python 3.9+、Git 和 PyYAML。
- 脚本验证：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v`。
- 本机部署入口：用户显式调用 `codex-manager` 并要求本项目在本机部署或更新时，读取 README.md 的“交给 codex-manager”，在 manager 的更新流程中自行调用准备脚本。默认候选为当前仓库的四个新版 Skill，项目入口沿用已有消费者范围。
