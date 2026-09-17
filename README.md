# Workflow Pipeline

当前版本：**v1.1.0** · [更新日志](CHANGELOG.md) · [发布说明](https://github.com/uigdwunm/workflow-pipeline/releases/tag/v1.1.0)

五个可独立安装、按需组合的 Codex 工程工作流 Skill，覆盖讨论、需求、方案、实现与归档。可选的持久化设计讨论位于既有四阶段之前；没有唯一、可验证讨论上下文时，1—4 保持原有流程：

0. **0讨论 — `$design-discussion`（可选）**：为需要跨对话延续的设计讨论维护可验证话题、文档与检查点。
1. **1拷问 — `$problem-framing`**：创建或继承唯一需求草案，通过追问持续更新并冻结目标、范围、行为契约与验收条件。
2. **2方案 — `$solution-design`**：创建 Flow Worktree，把已确认需求转成 Spec、ADR 和必要的 Tickets，并把方案合并到目标分支。
3. **3实现 — `$guided-implementation`**：依据2方案产物复用其 Flow Worktree，或根据明确、完整的显式请求独立创建一个，完成实现与验收并保留候选。
4. **4归档 — `$change-closure`**：继续复用 Flow Worktree，对齐归档文档，统一合并实现与归档并清理。

五个 Skill 可分别安装，每包自带本阶段所需规则和脚本。只有进入其他阶段时，才从当前宿主有效 Skill registry 解析目标包；跨包协议兼容键必须完全相等，发布版本可以不同。共享 `thread-settings-v5` 位于各包内部，不再要求安装 `guided-implementation` 才能读取配置。

这些 Skill 来自一个真实使用中的工作流，重点是阶段边界、Git worktree 隔离、并发合并校验，以及让文档与实现各自拥有明确的写入权限。

## 环境要求

- Codex Desktop / Codex CLI，且运行时支持原生子 Agent 协作工具。
- Python 3.10+、Git 与 Bash，运行在支持 POSIX 文件语义的 macOS 或 Linux。
- 协议依赖 `flock` advisory locks、同文件系统原子 rename、`fsync`、hard link、`dir_fd` 与 `O_NOFOLLOW`。协调目录和项目文档必须位于可靠实现这些原语的本地或等价文件系统；原生 Windows 当前不受支持。
- 当前动作实际需要的 [Matt Pocock Skills](https://github.com/mattpocock/skills)，见[动作依赖表](docs/dependencies.md)。Stage 0 原生讨论没有无条件 Matt 依赖。

## 安装

本次实际核验的安装器为 `skills@1.6.0`。可以按需选择任意一个包，例如：

```bash
npx skills@1.6.0 add uigdwunm/workflow-pipeline --list
npx skills@1.6.0 add uigdwunm/workflow-pipeline --skill design-discussion --agent codex --copy
```

仓库只提供五个生成入口：`design-discussion`、`problem-framing`、`solution-design`、`guided-implementation`、`change-closure`。也可从检出中的 `skills/<name>` 安装，或把单个完整包复制/链接到宿主支持的 Skill 根；不要只复制入口文件。

Matt 内容不随包分发，也不会自动安装。需要某项能力时另行安装/启用，例如使用上述固定版本安装器选择 `mattpocock/skills` 中的所需入口。首次配置项目时，先读已有 tracker、triage、domain 约定，确需初始化才运行 `$setup-matt-pocock-skills`。

安装后刷新宿主任务，读取当前有效 Skills 列表及精确入口。下列命令仅诊断磁盘候选，不能证明当前 Agent 可调用：

```bash
./scripts/check-dependencies.sh --stage design-discussion --action discuss --project .
```

## 固定身份、连续执行与升级

单阶段只检查自身和当前动作依赖；后继未安装不否定本阶段成果。Stage 3 仍保留候选和 Flow Worktree，Stage 4 才最终合并与清理。连续模式在创建 carrier/worktree 或激活前检查剩余路线，缺阶段时不静默改成单阶段；若当前阶段已获授权并开始，则完成该阶段，保存成果后阻止未完成的交接。修复依赖并刷新 registry 后，从原 session/attempt/binding 的第一项未完成动作恢复。

每次运行固定包的真实根、摘要和协议键，脚本启动、恢复、交接前复核。升级应安装到新的不可变目录，再切换新任务的注册链接；保留旧目录直到旧运行结束。原地覆盖导致 `package_changed`，缺失旧包导致 `package_unavailable`；不得把同一运行偷偷换到新版本。

前台 runner 仅在 `guided-implementation` 包内。新记录为 v2，固定 runner 与 Stage 2/3/4 身份。start 的 confirmed JSON 包含可信当前 `registry`；runner 将该文件固定为 `registry_input`，每次 executor 启动前重读其中证据，resume 重验剩余目标。宿主/控制器须在注册变化后刷新该文件，或显式改用新证据文件：

```bash
python3 <固定包根>/scripts/workflow.py resume <record> <answer> --registry-input <current-evidence.json>
```

新文件须包含当前 `registry` 对象；这只更新注册证据，不替换原固定包身份。旧 v1 记录返回 `legacy_run_requires_original_runtime`，必须用保留的原 runner 和原安装树恢复；新 runner 不猜测迁移、重启阶段或清理 worktree。

## 使用方式

从一个明确目标开始：

```text
$problem-framing 帮我明确这次需求
```

需要持久化设计上下文时可显式从 `$design-discussion` 开始，并按验证后的路由进入 1 或 2；也可直接从 1 开始。独立进入 2 时，可直接使用当前对话中的已确认需求：主 Agent 只追问实质缺口，自动保存并提交需求快照后交给方案子 Agent，无需先走 0/1 或另行确认快照。已有需求文档或阶段交接仍按路径、Git commit 和 SHA-256 验证，不能用对话绕过无效交接。3实现既可来自已验证的2方案交接，也可由明确且完整的显式实现请求独立进入；后者不推进讨论阶段，也不补造 Spec。权威路由与恢复规则只在 [生命周期集成引用](src/shared/references/design-discussion/lifecycle-integration.md) 中维护。阶段完成后，可用明确的自然语言同意逐阶段继续，或明确要求在无需用户决策时连续执行剩余阶段；确认绑定的是最近披露的单一动作，不是固定口令。

0讨论不会自动创建实现 worktree，也不会扩大远程写入、付费、破坏性操作或其它外部副作用权限；这些动作仍按原阶段规则逐项确认。

## Matt 依赖需要特殊处理吗？

需要，但不需要把 Matt 的源码复制进本仓库。

- **把它声明为外部运行时依赖。** Skill 之间通过名称调用；在实际调用前检查必要能力，缺失时保留当前恢复点。
- **让使用者分别安装。** 这是最容易更新、也最清晰的版权边界；本项目不锁死或重新发布 Matt 的文件。
- **记录兼容基线。** 本版本按 `mattpocock/skills` 的提交 `84fdeffd12f2ee307994d1eb6feb48173b6e0502`（仓库版本 `1.2.3`）核对。上游升级后应重新验证阶段调用和输出协议。
- **遵守许可证。** Matt 的仓库使用 MIT License。由于这里仅引用和依赖、没有打包其源码，通常无需把其许可证合并进本项目的 LICENSE；仍在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中保留来源与许可证说明。
- **避免同名重复安装。** 如果同一个 Matt Skill 被多个插件或目录同时暴露，Codex 可能无法确定使用哪一个版本。请只保留一个可解析来源。

完整依赖关系见 [docs/dependencies.md](docs/dependencies.md)。

## 可移植配置

- `CODEX_HOME`：Codex 数据目录，默认 `~/.codex`。
- `CODEX_SESSIONS_ROOT`：会话目录覆盖值，默认 `$CODEX_HOME/sessions`。

## 开发与验证

```bash
./scripts/validate.sh
```

该命令校验生成漂移、五包入口/引用/资源闭包，运行 `tests/runtime` 与 `tests/packages` 的源测试和五包快速验证。开发编辑 `src/` 与 `build/skill-packages.json`，再运行 `python3 scripts/build_skills.py`；不要手改 `skills/` 生成副本。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

v1.1.0 的独立安装实现通过 368 项自动测试及双轴审查；`skills@1.6.0` 在临时项目的发现与五次单包安装通过。T13/T14 真实宿主 Agent 单包、组合及附着讨论恢复验收尚未执行，不以脚本测试或临时安装替代。`python3 scripts/field_acceptance.py` 输出现场步骤与证据表；安装同步需单独授权，本次未改用户安装。

## 许可证

[MIT](LICENSE)

Workflow Controller retains confirmation, routing and acceptance. Dedicated Discussion Task and Dedicated Problem Framing Task are visible 0/1 carriers. solution-designer, Implementation Dispatcher, Execution Agent and Closure Agent are native roles. See [Workflow Control Protocol](src/shared/references/guided-implementation/workflow-control-protocol.md).
