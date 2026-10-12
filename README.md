# Workflow Pipeline

本项目正在重新编写工程工作流 Skills。

旧实现保存在分支 `codex/history/2026-10-09`，需要时可从该分支查阅。新 Skill 的范围和结构以重新确认的需求为准。

## 准备本机更新

使用 Python 3.9+、Git 和能够运行 `codex-manager` 的 PyYAML 环境。部署准备脚本只生成候选包；本机安装、备份、数据库、登记信息和项目入口由 `$codex-manager` 管理。

```sh
# 只读检查当前仓库
python3 scripts/prepare-deployment.py check

# 输出必须是尚不存在的绝对目录，父目录须已存在
python3 scripts/prepare-deployment.py build --output /tmp/workflow-pipeline-candidate

# 根据候选包自身复核，不依赖原仓库仍然存在
python3 scripts/prepare-deployment.py verify --package /tmp/workflow-pipeline-candidate
```

每个命令成功时输出 JSON，退出码为 0；检查或生成失败为 1，参数错误为 2。生成失败时可能保留不完整的候选目录，需检查原因并使用新的输出位置；已有目录不会被覆盖。

候选包包含 `manifest.json` 和以下四个独立目录，每个目录保留实际资源和文件权限，并附带 MIT LICENSE：

| 候选路径 | 阶段 |
| --- | --- |
| `skills/design-discussion` | 1拷问 |
| `skills/solution-design` | 2方案 |
| `skills/guided-implementation` | 3实现 |
| `skills/change-closure` | 4归档 |

`problem-framing` 不在本次候选中；占位文件和 Python 缓存也不打包。脚本检查 YAML 元数据、Python 语法和包内文件引用，拒绝软链接资源。`verify` 还检查文件增加、删除、内容、权限及清单是否一致。它不会执行候选脚本、查询宿主 Skill 登记、检查目标项目的配置，或运行完整工作流。

清单记录 Git 提交及工作区是否有改动、实际 Skill 元数据、调用策略、直接依赖、可选阶段交接、项目配置要求和逐文件哈希。未提交的内容也可以打包，实际候选文件及其哈希才是本次交付依据。直接依赖与交接声明维护在脚本的 `SKILLS` 中；修改 Skill 的依赖时同步更新，并由 manager 对照候选正文核实条件性依赖。

`package_sha256` 是候选文件清单的摘要，**不是 cc-switch 的 `content_hash`**；manager 应按 cc-switch 的算法重新计算数据库哈希。清单也不是可直接写入组件登记的 `component.yaml`。

## 交给 codex-manager

在本仓库中显式调用 `$codex-manager`，直接提出本机部署请求即可：

```text
$codex-manager 在本机部署
```

收到这个明确请求时，manager 以本仓库当前工作区中的四个新版 Skill 为候选来源，核实本机安装和登记，保持已有项目使用范围。在其更新流程的候选准备步骤，自行调用 `check`、`build` 和 `verify`；输出目录选在已有父目录下的一个尚不存在的绝对路径。用户已指定候选包时，先复核该包再按指定来源处理。

本机安装、备份、数据库、组件登记和项目入口仍由 manager 管理，并遵循适用的授权要求。本次候选不包含 `problem-framing`；旧版依赖关系妨碍接受新版时，报告具体影响并等待处理决定。

manager 可以把包中每个 `candidate_source` 解析为绝对目录，使用现有入口逐项检查，例如：

```sh
python3 /Users/zhaolaiyuan/.cc-switch/skills/codex-manager/scripts/codex_components.py \
  admission-check design-discussion \
  --components-dir /Users/zhaolaiyuan/.cc-switch/component-registry/components \
  --candidate-source /tmp/workflow-pipeline-candidate/skills/design-discussion
```

接入这次重写时，manager 需按其完整更新流程处理：

1. 复核候选及变更的可执行资源，完成适用的安全审查、更新计划和回滚准备。
2. 核对旧阶段职责、初始化声明、调用策略、入口、套件关系和验证命令与新版的差异；候选包检查和 `admission-check` 通过不能代替这些检查。
3. 安装获准的完整候选目录，清除对应旧版包中不再存在的资源，保留项目已有文档与运行状态；同步变更的数据库描述、哈希、登记等实际状态。
4. 独立核验安装内容、现有消费者和登记。数据库字段更新、备份及登记验证遵循 manager 的规则。

## 验证脚本

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

测试使用临时仓库和候选目录，覆盖打包与独立复核、资源损坏或越界、拒绝覆盖及 CLI 行为，不操作本机安装。
