# Workflow Pipeline

当前版本：**v3.5.0** · [更新日志](CHANGELOG.md) · [版本源码](https://github.com/uigdwunm/workflow-pipeline/tree/v3.5.0) · [发布与下载](https://github.com/uigdwunm/workflow-pipeline/releases)

三个可独立调用、按需衔接的工程工作流 Skill，覆盖需求澄清、技术方案、实现与归档。

| 阶段 | Skill | 职责 |
| --- | --- | --- |
| 1拷问 | [`design-discussion`](skills/design-discussion/SKILL.md) | 澄清想法、查漏补缺，形成用户确认的理解及必要的测试范围。 |
| 2方案 | [`solution-design`](skills/solution-design/SKILL.md) | 根据已确认需求生成 Spec 和必要的 Tickets，明确设计取舍与验收方式。 |
| 3实现 | [`guided-implementation`](skills/guided-implementation/SKILL.md) | 按单一实现或任务图路径完成实现、验收整改、文档收尾、交付、项目状态更新、资源清理和最终报告。 |

可以从符合当前任务状态的阶段进入，默认连续执行后续阶段；用户明确限定执行范围时，在指定阶段结束。仅调用某个 Skill 表示选择入口，不视为只执行该阶段。各阶段沿用同一份临时进度文件，交接时核对真实成果和仍有效的确认依据；阶段内的设计确认、授权和验收仍按原规则执行。

1拷问和 2方案的报告始终展示下一步建议：1拷问按成果建议进入 2方案或 3实现，2方案建议进入 3实现。执行范围只影响是否接续，不影响建议展示；“先讨论”等顺序表述不视为阶段限制，确认问题中也不自行加入停止条件。

v3.5.0 将执行终点及用户限制依据保存在同一任务进度中，普通讨论确认不改变范围。两个阶段报告直接读取记录，不再接受 `--continuous-mode`；2方案报告改为传入 `--file`、`--task-id`、`--project`，开始时间也从记录读取。三个 Skill 须同步更新；旧进度缺少执行范围时不自动补成连续模式，进行中的旧任务沿用对应旧版本收尾。

方案和实现以用户已确认的要求和方案界定额外改造，新增改造工作或改变既定设计先取得用户确认；自主处理此类变化须有用户明确授予的决定权。检查覆盖方案发布、实现、测试和审查整改、冲突处理及最终实际改动。

三阶段由主对话适时用一两句话说明当前任务、已完成和剩余工作；有 Tickets 时沿用编号和完成数量，返工时说明进度变化。进度说明复用已有状态，与进度文件的大步骤登记分开。

用户答复与具体待决事项对应，未回答部分继续保持未决；“可以”“继续”等简短答复结合上下文理解，仅对影响结果的歧义补问对应事项。

按实际工作量、耦合程度及独立交付、验收和并行收益选择执行方式；已有 Spec 可直接用于单一实现。拆票和委派先判断收益，主对话可短小连贯完成的工作直接执行，已有测试、审查和工作区保护要求继续沿用。

测试失败先做最小必要对照，已确认不影响本次验收的既有失败记录依据并复用结论，没有新疑点时停止扩展排查；仍阻断必要验收的保持未完成，额外修复或验收调整需取得用户决定。

v3.4.0 为 1拷问增加讨论要点准备、用户确认及入口检查，并通过报告脚本统一生成、保存固定报告和登记收尾。普通 `complete` 仅可登记 1拷问的第 2 步；报告调用改为传入同一进度文件、任务标识和目标项目，开始时间从记录读取。升级时同步更新三个 Skill 的进度脚本及调用说明；进行中的旧任务应保留原版本收尾。详见[更新日志](CHANGELOG.md)。

v3.3.1 修复单阶段报告遗漏下一步建议的问题，连续模式仅控制是否自动接续；同时明确阶段限制须来自用户要求。详见[更新日志](CHANGELOG.md)。

v3.3.0 默认连续执行后续阶段，用户明确限定范围时在指定阶段结束。阶段报告先展示再接续，单阶段报告不再询问是否继续，也不因未使用的后续入口不可用而提示交接受阻。详见[更新日志](CHANGELOG.md)。

v3.2.0 支持通过用户文件以自然语言定义子 Agent 选型偏好；仅在用户文件不存在时读取内置默认规则。默认选型由运行时判断，并按模型级别和价格限制推理强度；下级委派沿用同一份规则。详见[选型偏好](#子-agent-选型偏好)和[更新日志](CHANGELOG.md)。

v3.1.1 明确阶段报告先展示再接续，最终报告直接展开并反映当前任务状态；完善任务分支清理，并按模型实际可用情况启用子 Agent 任务选型偏好。Skill 名称、脚本接口和进度格式保持不变。详见[更新日志](CHANGELOG.md)。

v3.1.0 按实际影响确定小改动测试范围，精简最终报告，并限制高成本子 Agent 的使用。最终报告脚本新增三个必填摘要参数，升级时须同步更新调用方式；三阶段和进度文件结构不变。详见[更新日志](CHANGELOG.md)。

归档属于 3实现的内部收尾。实现验收通过后继续交付和收尾，不再单独询问是否进入归档阶段；具体操作仍遵守已有授权和项目约定。

v3.0.0 不再提供独立的 `change-closure` 入口，进度登记统一使用 `complete --steps` JSON 列表，单步也传入一个元素；原 `complete --step/--note/--at` 参数不再支持。v2.x 未完成任务应保留对应版本及进度现场，按原版本收尾；本次不提供旧四阶段进度的自动迁移。详见[更新日志](CHANGELOG.md)。

方案和实现在独立任务 Worktree 中开展，保留用户常用工作区的分支与编辑器选择。3实现核实实际交付结果后，主动清理已交付且不再使用的临时分支，包括子 Agent 分支；托管 Worktree 的归档和移除交给宿主处理。

v2.0.0 重写了 v1.x 工作流，`1拷问` 由 `design-discussion` 承担，发布包不再包含 `problem-framing`，也不提供旧 runner 或运行记录的自动迁移。旧任务需保留原安装和现场，按对应旧版本处理；旧版源码及更新记录可从 [v1.2.2](https://github.com/uigdwunm/workflow-pipeline/tree/v1.2.2) 查阅。

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

候选包包含 `manifest.json` 和以下三个独立目录，每个目录保留实际资源和文件权限，并附带 MIT LICENSE：

| 候选路径 | 阶段 |
| --- | --- |
| `skills/design-discussion` | 1拷问 |
| `skills/solution-design` | 2方案 |
| `skills/guided-implementation` | 3实现 |

`problem-framing` 和 `change-closure` 不在本次候选中；占位文件和 Python 缓存也不打包。脚本检查 YAML 元数据、Python 语法和包内文件引用，拒绝软链接资源。`verify` 还检查文件增加、删除、内容、权限及清单是否一致。它不会执行候选脚本、查询宿主 Skill 登记、检查目标项目的配置，或运行完整工作流。

## 子 Agent 选型偏好

2方案和 3实现先检查当前运行环境中用户主目录下的 `~/.config/workflow-pipeline/subagent-models.md`。文件存在时完整读取；只有确认不存在时，才读取[内置默认偏好](preferences/subagent-models.md)。每次只使用一份偏好规则，下级委派沿用同一份文件。

用户可以用自然语言描述选型条件、模型、推理强度或判断原则，无需固定格式。选定文件无法读取，或文件内容存在任何问题时，说明问题并询问用户，等待回复后继续；不改读另一份规则。

用户文件由用户自行维护。准备脚本把同一份内置默认偏好复制为两个候选 Skill 各自的 `references/subagent-model-preferences.md`，并把入口引用改为包内路径；源码引用和其他资源保持原样，候选可独立使用。

## 候选包清单

清单记录 Git 提交及工作区是否有改动、实际 Skill 元数据、调用策略、直接依赖、可选阶段交接、项目配置要求和逐文件哈希。未提交的内容也可以打包，实际候选文件及其哈希才是本次交付依据。直接依赖与交接声明维护在脚本的 `SKILLS` 中；修改 Skill 的依赖时同步更新，并由 manager 对照候选正文核实条件性依赖。

`package_sha256` 是候选文件清单的摘要，**不是 cc-switch 的 `content_hash`**；manager 应按 cc-switch 的算法重新计算数据库哈希。清单也不是可直接写入组件登记的 `component.yaml`。

## 交给 codex-manager

在本仓库中显式调用 `$codex-manager`，直接提出本机部署请求即可：

```text
$codex-manager 在本机部署
```

收到这个明确请求时，manager 以本仓库当前工作区中的三个新版 Skill 为候选来源，核实本机安装和登记，保持已有项目使用范围。在其更新流程的候选准备步骤，自行调用 `check`、`build` 和 `verify`；输出目录选在已有父目录下的一个尚不存在的绝对路径。用户已指定候选包时，先复核该包再按指定来源处理。

本机安装、备份、数据库、组件登记和项目入口仍由 manager 管理，并遵循适用的授权要求。本次候选不包含 `problem-framing` 和 `change-closure`。本机旧 `change-closure` 的安装、登记及相关引用由 manager 在更新流程中核实处理，候选包排除不代表本机自动卸载。旧版依赖关系妨碍接受新版时，报告具体影响并等待处理决定。

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
