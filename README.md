# Workflow Pipeline

一套面向 Codex 的工程工作流。可选的持久化设计讨论位于既有四阶段之前；没有唯一、可验证讨论上下文时，1—4 保持原有流程：

0. **0讨论 — `$design-discussion`（可选）**：为需要跨对话延续的设计讨论维护可验证话题、文档与检查点。
1. **1拷问 — `$problem-framing`**：创建或继承唯一需求草案，通过追问持续更新并冻结目标、范围、行为契约与验收条件。
2. **2方案 — `$solution-design`**：创建 Flow Worktree，把已确认需求转成 Spec、ADR 和必要的 Tickets，并把方案合并到目标分支。
3. **3实现 — `$guided-implementation`**：依据2方案产物复用其 Flow Worktree，或根据明确、完整的显式请求独立创建一个，完成实现与验收并保留候选。
4. **4归档 — `$change-closure`**：继续复用 Flow Worktree，对齐归档文档，统一合并实现与归档并清理。

五个 Skill 必须以同一版本安装。跨阶段继承模型与推理强度时统一使用
`guided-implementation` 提供的 `thread-settings-v5` 只读协议；协议缺失或
版本不一致会停止流程，不会猜测默认值。

这些 Skill 来自一个真实使用中的工作流，重点是阶段边界、Git worktree 隔离、并发合并校验，以及让文档与实现各自拥有明确的写入权限。

## 环境要求

- Codex Desktop / Codex CLI，且运行时支持原生子 Agent 协作工具。
- Python 3.10+、Git 与 Bash，运行在支持 POSIX 文件语义的 macOS 或 Linux。
- 协议依赖 `flock` advisory locks、同文件系统原子 rename、`fsync`、hard link、`dir_fd` 与 `O_NOFOLLOW`。协调目录和项目文档必须位于可靠实现这些原语的本地或等价文件系统；原生 Windows 当前不受支持。
- [Matt Pocock's Skills](https://github.com/mattpocock/skills)，至少安装：
  `setup-matt-pocock-skills`、`ask-matt`、`grill-with-docs`、`grilling`、
  `domain-modeling`、`to-spec`、`to-tickets`、`implement`、`tdd`、`code-review`。

## 安装

先安装 Matt 的依赖：

```bash
npx skills@latest add mattpocock/skills
```

在安装器中选择上面列出的 Skill，并确保包含 `setup-matt-pocock-skills`。随后安装本仓库：

```bash
npx skills@latest add uigdwunm/workflow-pipeline
```

也可以克隆本仓库后，把 `skills/` 下的五个目录链接或复制到 Codex 的 Skill 目录。安装完成后运行：

```bash
./scripts/check-dependencies.sh
```

首次在一个工程仓库里使用 Matt 系列 Skill 时，还应运行一次 `$setup-matt-pocock-skills`，配置 Issue Tracker、标签词汇和领域文档布局。

## 使用方式

从一个明确目标开始：

```text
$problem-framing 帮我明确这次需求
```

需要持久化设计上下文时可显式从 `$design-discussion` 开始，并按验证后的路由进入 1 或 2；也可直接从 1 开始。独立进入 2 时，可直接使用当前对话中的已确认需求：主 Agent 只追问实质缺口，自动保存并提交需求快照后交给方案子 Agent，无需先走 0/1 或另行确认快照。已有需求文档或阶段交接仍按路径、Git commit 和 SHA-256 验证，不能用对话绕过无效交接。3实现既可来自已验证的2方案交接，也可由明确且完整的显式实现请求独立进入；后者不推进讨论阶段，也不补造 Spec。权威路由与恢复规则只在 [生命周期集成引用](skills/design-discussion/references/lifecycle-integration.md) 中维护。阶段完成后，可用明确的自然语言同意逐阶段继续，或明确要求在无需用户决策时连续执行剩余阶段；确认绑定的是最近披露的单一动作，不是固定口令。

0讨论不会自动创建实现 worktree，也不会扩大远程写入、付费、破坏性操作或其它外部副作用权限；这些动作仍按原阶段规则逐项确认。

## Matt 依赖需要特殊处理吗？

需要，但不需要把 Matt 的源码复制进本仓库。

- **把它声明为外部运行时依赖。** Skill 之间通过名称调用；缺少依赖时流程会在运行中断，因此 README 和检测脚本必须列清楚。
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

该命令自动发现并验证全部 Skill、渐进引用和内外部依赖注册，自动运行 `skills/**/scripts/test_*.py`，并检查 Skill 文件中是否残留个人绝对路径。

`check-dependencies.sh` 只能检查文档列出的文件系统搜索根：它会发现其中同一外部注册名的重复来源，但不能读取 Codex 当前 active registry，也不能证明安装版本与兼容基线一致。运行时仍以 active registry 为准；版本边界需按依赖文档人工核对。

## 许可证

[MIT](LICENSE)

Workflow Controller retains confirmation, routing and acceptance. Dedicated Discussion Task and Dedicated Problem Framing Task are visible 0/1 carriers. solution-designer, Implementation Dispatcher, Execution Agent and Closure Agent are native roles. See [Workflow Control Protocol](skills/guided-implementation/references/workflow-control-protocol.md).
