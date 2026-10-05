# Workflow Pipeline

当前仓库候选版本：**v1.3.0** · [更新日志](CHANGELOG.md)

五个可独立安装、按需组合的 Codex 工程工作流 Skill，覆盖讨论、需求、方案、实现与归档。可选的持久化设计讨论位于既有四阶段之前；没有唯一、可验证讨论上下文时，1—4 保持原有流程：

0. **0讨论 — `$design-discussion`（可选）**：为需要跨对话延续的设计讨论维护可验证话题、文档与检查点。
1. **1拷问 — `$problem-framing`**：创建或继承唯一需求草案，通过追问持续更新并冻结目标、范围、行为契约与验收条件。
2. **2方案 — `$solution-design`**：优先复用并绑定独立 Flow Worktree，把已确认需求转成 Spec、ADR 和必要的 Tickets，并把方案合并到目标分支。
3. **3实现 — `$guided-implementation`**：依据2方案产物复用其 Flow Worktree，或根据明确、完整的显式请求独立选择并绑定一个，完成实现与验收并保留候选。
4. **4归档 — `$change-closure`**：继续复用 Flow Worktree，对齐归档文档，统一合并实现与归档，核验工作区按绑定保留或清理。

五个 Skill 可分别安装，每包自带本阶段所需规则和脚本。只有进入其他阶段时，才从当前宿主有效 Skill registry 解析目标包；跨包协议兼容键必须完全相等，发布版本可以不同。共享 `thread-settings-v5` 位于各包内部，不再要求安装 `guided-implementation` 才能读取配置。

这些 Skill 来自一个真实使用中的工作流，重点是阶段边界、Git worktree 隔离、并发合并校验，以及让文档与实现各自拥有明确的写入权限。

v1.3.0 的本次交付围绕 Ticket 主流程：2方案产出函数级执行计划，Dispatcher 按当前计划调度真实 Execution Agent，原 Workflow Controller 在合同内修订内部设计。新运行使用 full 实现模式，Dispatcher 负责调度、验收、统一验证和 Git 集成；实现与修复字节由 Execution Agent 提供。历史 direct 运行继续使用其原固定包。

## Ticket 与函数级执行计划

Ticket 保留交付行为、验收条件和前置依赖，函数计划说明具体函数的能力、大致过程、关键输入输出、失败行为及行为验证。相关函数可以组成一次分配、共用验证；正式计划在 Stage 2 与文件范围一起封存，执行者领取当前版本的计划引用与精确文件权限。

实现中发现私有函数需要移动、拆分或增加辅助函数时，Execution Agent 提供事实，Dispatcher 上报原 Controller。原 Controller 核对功能合同，在当前 writer 和未决调用全部停止后独占规划写入窗口，提交规划并切换当前计划权威。未受影响的有效结果可以保留，受影响字节须由真实 Execution Agent 重新验证；旧计划权限不能继续派发。功能、公共接口、验收、业务职责、重大架构变化或不明影响先交给用户决定。

本次用户确认先交付上述 Ticket 主流程。真实宿主已覆盖 Stage 2 计划产出、嵌套执行与 TDD、Controller 私有修订、结果保留与复核、旧计划拒绝、用户门禁、原身份恢复，以及当前计划的 Stage 4 接收、合并与清理。以下补充入口延期，尚未完成验收，不能据主线通过宣称可用：

- 无 A / 无 Ticket 的独立入口：纯 control 起点尚缺合法的 C 计划修订初始化桥。
- foreground：固定 runner 的 `controller_revision_ingress` 当前不可用。
- attached ledger：实际 Stage-2 起点与台账 Phase-0 初始化不一致，启动被拒绝。

这是仓库代码与文档的本地交付，没有执行安装、部署或远程发布；旧运行的固定记录与失败试验继续保留。详细完成记录见 [PRD](.scratch/function-execution-plans/PRD.md#交付记录2026-10-05)。

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

前台 runner 仅在 `guided-implementation` 包内。新记录为 v10，工作流进度协议为 `workflow-progress-v19`，stage transfer 为 v14，control 兼容键为 11（JSON schema 6），函数计划格式为 `execution-plan-v1`；固定 runner 与 Stage 2/3/4 身份。Stage 2 在方案完成后封存实施与归档文件范围及函数计划；Stage 3 只消费当前已验收的范围、计划和就绪证明。start 的 confirmed JSON 包含可信当前 `registry`；runner 将该文件固定为 `registry_input`，每次 executor 启动前重读其中证据，resume 重验剩余目标。宿主/控制器须在注册变化后刷新该文件，或显式改用新证据文件：

```bash
python3 <固定包根>/scripts/workflow.py resume <record> <answer> --registry-input <current-evidence.json>
```

新文件须包含当前 `registry` 对象；这只更新注册证据，不替换原固定包身份。普通恢复遇旧 v1–v9 runner 记录返回 `legacy_run_requires_original_runtime`，旧记录必须使用其原固定运行时；本次不迁移或接管旧运行。

新运行在一个前台 app-server 内跨轮执行。confirmed input 还需冻结 Controller 提供的实际 CLI 版本、权限配置和来源；首次业务轮前回读 model/effort/cwd/权限，不能推断默认值。完整字段见[前台生命周期协议](src/shared/references/workflow-progression.md#foreground-carrier-and-compatibility)。等待子角色、问题答案或暂停时，前台进程继续持有宿主。

前台 runner 的 `run_record` 须放在仓库和 Flow Worktree 之外、由当前用户独占的目录（建议权限 `0700`）。新目录由 runner 按此权限创建；已存在的目录若对其他用户开放，会在启动宿主前被拒绝。宿主原始日志和阶段结果可能包含敏感内容，务必保留这个目录及旧运行固定的安装包。

另一终端可用 `resume RECORD ANSWER --decision-id ID` 提交答案，返回 `queued` 只表示已保存；同一 carrier 经 C 消费后才生效。裸 `resume RECORD` 明确请求解除已完成的暂停，单纯答案不会解除暂停。`pause RECORD` 和 `cancel RECORD` 由原 owner 消费；取消必须等所有已绑定角色、执行者和审查者及未解决调用闭合后结束。宿主丢失保留现场并返回 `await-host-recovery`，不会启动第二宿主或重复派发。Ticket 主流程的真实嵌套宿主证据与自动测试分开记录；这不覆盖上述延期入口，也不把传输替身通过当成真实宿主通过。

## 使用方式

从一个明确目标开始：

```text
$problem-framing 帮我明确这次需求
```

需要持久化设计上下文时可显式从 `$design-discussion` 开始，并按验证后的路由进入 1 或 2；也可直接从 1 开始。独立进入 2 时，可直接使用当前对话中的已确认需求：主 Agent 只追问实质缺口，自动保存并提交需求快照后交给方案子 Agent，无需先走 0/1 或另行确认快照。已有需求文档或阶段交接仍按路径、Git commit 和 SHA-256 验证，不能用对话绕过无效交接。本次函数计划主流程从已验证的2方案 Ticket 交接进入3实现。原有独立3入口保留自包含请求契约，不推进讨论阶段或补造 Spec；其新计划修订能力仍受上述延期入口边界限制。权威路由与恢复规则只在 [生命周期集成引用](src/shared/references/design-discussion/lifecycle-integration.md) 中维护。阶段完成后，可用明确的自然语言同意逐阶段继续，或明确要求在无需用户决策时连续执行剩余阶段；确认绑定的是最近披露的单一动作，不是固定口令。

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
# 可选：进一步减轻负载；--jobs 1 为串行
./scripts/validate.sh --jobs 1
```

该命令校验生成漂移、五包入口/引用/资源闭包，运行 `tests/runtime` 与 `tests/packages` 的源测试和五包快速验证。测试默认最多使用 2 个独立进程，后台 CPU 优先级设为 nice 10，先做快速检查，再按历史耗时优先启动慢测试，减少资源争抢和末尾等待。`--jobs` 只接受 1 或 2，超过 2 会拒绝运行；`--nice 0` 保留原进程优先级。同一仓库的不同 worktree 共用测试锁，已有测试运行时第二轮立即退出。定向测试也使用 `scripts/run_tests.py --test-file <测试文件>`，可追加 `--match <用例名称片段>`；`validate.sh` 始终运行全量测试并拒绝筛选参数。按文件并发、文件内部串行，汇总跳过项和失败日志；历史耗时仅用于排队，不是测试成功凭据。开发编辑 `src/` 与 `build/skill-packages.json`，再运行 `python3 scripts/build_skills.py`；不要手改 `skills/` 生成副本。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

生产候选 `6d5bbb2` 的完整仓库验证运行了 57 个测试文件、1,166 项测试：零失败、零错误、1 项既有跳过，进程自然退出码为 0；五包生成一致性与 81 个资源闭包校验通过。使用相同生产包字节的真实 Ticket 宿主样例完成 31 项完整测试（零跳过）及独立 Standards / Spec 两轴审查，当前计划的归档结果已接收。自动验证与现场验收记录见 [PRD 交付记录](.scratch/function-execution-plans/PRD.md#交付记录2026-10-05)，延期入口仍未验收。`skills@1.6.0` 的临时项目发现与五次单包安装验证属于 v1.1.0 的既有证据；本次没有执行安装。`python3 scripts/field_acceptance.py` 只输出现场步骤与证据表。

## 许可证

[MIT](LICENSE)

Workflow Controller retains confirmation, routing and acceptance. Dedicated Discussion Task and Dedicated Problem Framing Task are visible 0/1 carriers. solution-designer, Implementation Dispatcher, Execution Agent and Closure Agent are native roles. See [Workflow Control Protocol](src/shared/references/guided-implementation/workflow-control-protocol.md).

工作区选择遵循“先验证原绑定，再复用可用独立空间，最后按需创建”。新建优先使用宿主管理工具，明确指定目标提交；脚本创建只作宿主规则允许的后备路径。复用空间和宿主管理空间完成后保留，只有本流程脚本创建且证据齐全的临时空间执行删除。需求可在合适的既有空间冻结，再通过有界文档交付到目标；范围不变的方案执行者恢复由 Controller 核验停写与成果后处理。第 1 阶段专用对话及普通子话题首轮交接门槛保持原行为。见 [工作区生命周期决策](docs/adr/0012-bind-existing-workspaces-and-preserve-lifecycle.md)。
