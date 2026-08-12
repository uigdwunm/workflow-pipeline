# Workflow Pipeline

一套面向 Codex 的四阶段工程工作流，把需求澄清、方案设计、受控实现和变更归档拆成四个可组合的 Skill：

1. **1拷问 — `$problem-framing`**：通过追问冻结目标、范围、行为契约与验收条件。
2. **2方案 — `$solution-design`**：把已确认需求转成 Spec、ADR 和必要的 Tickets。
3. **3实现 — `$guided-implementation`**：在仓库租约、文档租约和独立验收约束下实施并合并。
4. **4归档 — `$change-closure`**：对齐规划文档、提交归档变更并安全清理已合并分支。

这些 Skill 来自一个真实使用中的工作流，重点是阶段边界、可恢复协议、Git 并发安全，以及让文档与实现各自拥有明确的写入权限。

## 环境要求

- Codex Desktop / Codex CLI，且运行时支持原生子 Agent 协作工具。
- Python 3.10+ 与 Git。
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

也可以克隆本仓库后，把 `skills/` 下的四个目录链接或复制到 Codex 的 Skill 目录。安装完成后运行：

```bash
./scripts/check-dependencies.sh
```

首次在一个工程仓库里使用 Matt 系列 Skill 时，还应运行一次 `$setup-matt-pocock-skills`，配置 Issue Tracker、标签词汇和领域文档布局。

## 使用方式

从一个明确目标开始：

```text
$problem-framing 帮我明确这次需求
```

阶段完成后，按成功页脚提示用 `确认` 逐阶段继续，或在明确提供该选项时用 `执行后续全部流程`。也可以在拥有合格输入时显式进入 `$solution-design`、`$guided-implementation` 或 `$change-closure`。

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
- `CC_SWITCH_HOME`：cc-switch 数据目录，默认 `~/.cc-switch`。
- `CC_SWITCH_RUNTIME_ROOT`：四阶段运行时状态目录，默认 `$CC_SWITCH_HOME/runtime`。

## 开发与验证

```bash
./scripts/validate.sh
```

该命令验证四个 Skill 的结构、运行 Python 单元测试，并检查仓库内是否残留个人绝对路径。

## 许可证

[MIT](LICENSE)
