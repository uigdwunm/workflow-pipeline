# Thread settings v3：支持原生子任务身份

Status: `ready-for-agent`
Lifecycle: `completed`

日期：2026-08-31

## 结论

这不是临时部署抖动，也不是单纯的旧安装问题。当前
`thread-settings-v2` 把 `CODEX_THREAD_ID` 与 `CODEX_SESSION_ID` 必须相等
当成身份不变量；但 Codex 原生子任务拥有独立线程，同时继承父任务的运行
设置。当前运行时的根任务和一级子任务分别呈现以下形态：

| 运行形态 | rollout `session_meta.id` | rollout `session_meta.session_id` | `source` |
| --- | --- | --- | --- |
| 根任务 | 当前线程 ID | 当前线程 ID | 非 `subagent` 来源 |
| 原生子任务 | 子线程 ID | 会话谱系 ID；当前观测为父线程 ID | `subagent.thread_spawn`，含父线程 ID |

`guided-implementation` 的 Dedicated Implementation Task 正是在第二种形态下
执行 `verify --current`，所以 v2 会在读取模型设置前稳定报
`runtime task identity is inconsistent`。现有单元测试把这个错误假设写成了
预期行为，因此“测试全绿”不能证明子任务路径可用。

修复应升级为 `thread-settings-v3`：把当前线程身份、会话谱系身份和父线程
关系作为不同字段验证，不再比较前两个字段彼此相等。所有公开命令和成功回执
字段保持不变，仅协议版本变化。

Codex 官方文档只保证每个 subagent 有独立的 agent thread，并继承父任务的模型、
推理强度等设置；它没有承诺环境变量的具体编码。因此 v3 只把“独立线程与设置
继承”作为产品语义，把本机 rollout 字段关系作为需要失败关闭验证的运行时格式，
而不把当前字段形态误写成永久平台保证。

官方参考：<https://learn.chatgpt.com/docs/agent-configuration/subagents>

## 目标

- 让合法的根任务和原生子任务都能执行 `resolve --current` 与
  `verify --current`。
- 继续把模型和 reasoning effort 绑定到当前线程的唯一 rollout，而不是父任务、
  标题、任务列表或调用者提供的猜测。
- 对字段缺失、身份冲突、多个 rollout、符号链接、未知身份形态和工作流版本混装
  保持失败关闭。
- 不改变 `resolve --thread-id`、`verify --thread-id` 的调用方式和既有语义。
- 用真实子任务形态的脱敏 fixture 和一次只读原生子任务 smoke test 覆盖原故障路径。

## 非目标

- 不修改 Codex 本身的环境变量或 rollout 格式。
- 不修改 subagent governance、派发协议、worktree 协议或 Stage 3 权限边界。
- 不允许通过删除身份校验、忽略 `CODEX_SESSION_ID` 或回退到默认模型来绕过故障。
- 不把父线程 ID、会话谱系 ID、消息内容或 rollout 路径加入公开回执。
- 不顺带让 `--current` 支持尚未观测到的未知 `source` 对象形态；新形态应失败
  关闭并显式升级协议。显式 `--thread-id` 仍可读取不参与当前身份验证的历史字段。

## 术语和身份模型

### 当前线程 ID

当前执行任务自己的线程 ID。`--current` 从 `CODEX_THREAD_ID` 读取，并要求它是
canonical lowercase UUID。它必须同时匹配：

1. 唯一 rollout 文件名末尾的线程 ID；
2. 该 rollout 中唯一稳定的 `session_meta.payload.id`。

这是查找和读取当前任务模型设置的唯一公开身份。

### 会话谱系 ID

`session_meta.payload.session_id` 表达当前 rollout 所属的会话谱系。运行时存在
`CODEX_SESSION_ID` 时，两者必须逐字相等；不存在时不猜测、不补写，只依赖 rollout
内其余身份事实完成验证。

会话谱系 ID 不是当前线程 ID 的别名。根任务中两者当前相等；原生子任务中两者
当前不同。

### 父线程 ID

原生子任务从
`session_meta.payload.source.subagent.thread_spawn.parent_thread_id` 取得直接父线程
ID。它必须是 canonical lowercase UUID，且不得等于当前线程 ID。它只用于验证
rollout 的子任务形态，不用于读取父任务设置，也不进入公开回执。

## 协议不变量

### 所有调用

- 目标线程 ID 必须是 canonical lowercase UUID。
- 必须通过现有的逐层目录绑定、`O_NOFOLLOW`、inode/device 复核机制打开唯一
  rollout；不得降低现有文件系统防护。
- rollout 文件名与 `session_meta.payload.id` 必须精确绑定目标线程 ID。
- `session_meta` 的身份字段在一个 rollout 内必须唯一且稳定；同一字段出现冲突值
  时失败。
- 只返回最新完整 `turn_context` 的模型、reasoning effort 和 turn ID，不返回消息
  或身份谱系细节。

### `--thread-id`

- 继续信任调用方已经获得的 exact authenticated/tool-returned thread ID。
- 继续验证 rollout 文件名和 `session_meta.id`，但不依赖当前进程的环境变量。
- 历史 rollout 若没有 v3 当前任务适配器所需的谱系字段，或使用旧式 `source`
  结构，仍可按既有线程绑定读取；这条兼容只适用于显式 `--thread-id`，不能降级
  `--current`。

### `--current` 根任务形态

- `CODEX_THREAD_ID == session_meta.id`。
- `session_meta.session_id` 必须是 canonical lowercase UUID，并等于当前线程 ID。
- `CODEX_SESSION_ID` 存在时必须等于 `session_meta.session_id`。
- `source` 不得伪装成不完整或未知的 `subagent` 对象。

### `--current` 原生子任务形态

- `CODEX_THREAD_ID == session_meta.id`。
- `source.subagent.thread_spawn.parent_thread_id` 必须存在、canonical 且不同于当前
  线程 ID。
- `session_meta.session_id` 必须存在、canonical 且不同于当前线程 ID。
- `CODEX_SESSION_ID` 存在时必须等于 `session_meta.session_id`。
- 不额外假定谱系 ID 永远等于直接父线程 ID；当前一级子任务观测到二者相等，但
  嵌套子任务可能采用更长生命周期的谱系。两者分别校验，避免把未文档化关系再次
  固化成错误协议。
- `source` 中的非身份附加字段可以忽略；身份字段冲突或结构不完整必须失败。

## Module 设计

身份复杂度应收敛在 `thread_settings.py` 内部的一个深模块中，对调用方继续暴露
原来的小接口：

```text
protocol-version
resolve (--current | --thread-id <id>)
verify  (--current | --thread-id <id>) --model <model> --reasoning-effort <effort>
```

内部拆分为三个职责：

1. `open_bound_rollout(thread_id)`：保留当前安全文件查找与打开逻辑。
2. `read_rollout_facts(file)`：单次扫描提取私有身份事实和最新设置，检测冲突值。
3. `validate_current_identity(environ, rollout_facts)`：区分根任务与原生子任务形态，
   把环境变量绑定到 rollout 事实。

建议使用私有不可变记录承载下列字段，名称可按现有 Python 风格微调：

```text
RolloutFacts
  thread_id
  session_lineage_id
  parent_thread_id | none
  source_kind: root | subagent
  model
  reasoning_effort
  turn_id
```

`resolve_thread_settings` 和 `resolve_current_thread_settings` 共用文件扫描；前者只执行
显式目标绑定，后者再执行当前运行时身份验证。公开 JSON 仍只含：

```text
protocol, source, thread_id, model, reasoning_effort, turn_id
```

这样调用方无需理解父子身份编码，运行时格式变化也只需修改一个适配器。

## 错误分类

保持 CLI 退出码语义不变：不可用或身份异常为 `1`，设置变化为 `2`，匹配为 `0`。
错误消息应能区分以下边界，但不得包含完整本地路径或消息内容：

- `current thread identity unavailable/invalid`
- `rollout thread identity conflict`
- `runtime session lineage conflict`
- `invalid root task identity shape`
- `invalid subagent task identity shape`
- `unsupported runtime task identity shape`

工作流调用者仍把退出码 `1` 路由到已有稳定 anomaly/recovery footer，不做自动重试
或身份猜测。

## 代码和文档改动范围

### 运行时 owner

- `skills/guided-implementation/scripts/thread_settings.py`
  - 升级 `PROTOCOL_VERSION` 到 `thread-settings-v3`；
  - 用 rollout 事实验证替换 `_runtime_thread_id` 中的直接相等判断；
  - 保留现有安全文件访问和公开输出边界。
- `skills/guided-implementation/scripts/test_thread_settings.py`
  - 修正 fixture 字段命名，避免把 `session_meta.id` 误称为 session ID；
  - 增加根任务、原生子任务和篡改形态测试；
  - 把所有共享消费者契约更新到 v3。

### 协议和消费者

- `skills/guided-implementation/references/thread-settings-protocol.md`
- `skills/guided-implementation/references/execution-protocol.md`
- `skills/design-discussion/references/child-topic-protocol.md`
- `skills/problem-framing/references/dedicated-grilling-protocol.md`
- `skills/solution-design/references/subagent-protocol.md`
- `docs/dependencies.md`

调用命令不变，只更新协议版本、身份语义和失败条件。`change-closure` 不直接调用该
resolver，无需制造无意义的内容改动，但发布时仍必须与另外四个 Workflow Skills
使用同一版本安装。

## 测试方案

### 单元测试

至少覆盖：

1. 根任务：thread ID、lineage ID、rollout ID 一致，成功返回 v3 回执。
2. 子任务：当前 thread ID 与 lineage ID 不同；rollout ID 匹配当前线程；存在合法
   `subagent.thread_spawn.parent_thread_id`，成功返回 v3 回执。
3. 运行时 lineage ID 与 rollout `session_id` 不同，失败。
4. rollout 文件名与 `session_meta.id` 不同，失败。
5. 子任务父线程 ID 缺失、非 canonical 或等于当前线程，失败。
6. 子任务 lineage ID 缺失、非 canonical 或等于当前线程，失败。
7. 根任务伪装成不完整 `subagent` source，失败。
8. 同一 rollout 出现相互冲突的 `session_meta` 身份字段，失败。
9. 缺失 `CODEX_THREAD_ID`，失败；缺失 `CODEX_SESSION_ID` 时仍通过 rollout 事实
   完成严格验证。
10. 多个匹配 rollout、符号链接目录、inode 变化、损坏 JSONL 和尾部半条记录的
    既有测试继续通过。
11. 最新 turn 的设置相同返回 `match`，变化返回 `changed` 和退出码 `2`。
12. 成功回执不含消息、父线程、lineage ID 或本地路径。

子任务 fixture 应从本次真实故障的 `session_meta` 和 `turn_context` 形态脱敏提取，
只保留结构和虚构 UUID，不复制会话文本、真实任务 ID、绝对路径或 agent 名称。

### 仓库契约测试

- `protocol-version` 精确返回 `thread-settings-v3`。
- 三个文档消费者和 owner 引用同一 v3 协议。
- `docs/dependencies.md` 要求五个 Workflow Skills 同版本安装，并把 v2 视为
  `workflow_runtime_version_mismatch`。
- `./scripts/validate.sh` 全量通过。

### 真实运行 smoke test

安装同一构建的五个 Workflow Skills 后，启动一个只读、受治理的原生子任务；该
子任务只执行：

1. `protocol-version`；
2. `verify --current`，使用派发时绑定的 model/reasoning effort；
3. 返回公开回执并退出。

要求返回 v3、退出码 `0`、`status: match`，且没有
`runtime task identity is inconsistent`。smoke test 不编辑仓库、不创建 worktree、
不提交或发布。

最后再以一个隔离的最小 fixture 工作流验证 `guided-implementation` 的 Dedicated
Implementation Task 能通过 thread-settings gate 并进入 `verify-worktree`。到达该门
槛即可停止；无需借此实现无关功能。

## 发布顺序

1. 在独立短期 worktree 中修改 owner、测试、协议引用和依赖文档。由于修复对象正是
   `guided-implementation` 的启动门槛，本次不得用故障中的 Stage 3 流程自举实现。
2. 运行 thread-settings 聚焦测试和 `./scripts/validate.sh`。
3. 复核 diff，确认没有公开谱系身份、消息或用户绝对路径。
4. 以同一个 workflow-pipeline commit 构建并同步安装五个 Workflow Skills；不得只
   热替换 `guided-implementation`。
5. 对五份安装副本做来源 commit/内容哈希核对，并确认所有消费者要求 v3。
6. 执行只读原生子任务 smoke test。
7. 执行隔离 fixture 的 Stage 3 门槛测试，随后再恢复正常 Guided Implementation。

技能同步本身不应假设必须重启 Codex；若活动任务仍解析到 v2，先把它视为安装或
注册表缓存不一致并停止。只有产品明确要求时才重启，重启不能代替版本和哈希核对。

## 回滚

- 回滚单位是同一历史 commit 的五个 Workflow Skills 完整集合，不能单独回滚 owner。
- 回滚后明确把 Guided Implementation 标记为不可用，直到 v3 修复重新安装；不得
  通过关闭身份检查维持运行。
- 本修复只读取 rollout 并更新本地 Skill 文件，没有数据迁移；回滚不需要恢复用户
  仓库或远端状态。

## 验收标准

- 脱敏后的原故障子任务身份形态能通过 `resolve --current` 和
  `verify --current`。
- 任一线程 ID、lineage ID、父线程 ID 或 rollout 绑定被篡改时失败关闭。
- 根任务路径和显式 `--thread-id` 路径没有回归。
- `thread-settings.py` 的公开成功回执字段没有扩张，且协议为
  `thread-settings-v3`。
- 所有聚焦测试与 `./scripts/validate.sh` 通过。
- 五个 Workflow Skills 来自同一 commit，所有动态消费者只接受 v3；v2/v3 混装会
  报 `workflow_runtime_version_mismatch`。
- 真实只读子任务 smoke test 返回 `status: match`。
- 隔离 fixture 中的 Dedicated Implementation Task 能通过 thread-settings gate 并
  到达 `verify-worktree`。

## 风险与处置

- **未文档化运行时格式再次变化**：v3 对未知身份形态失败关闭，并用新的脱敏 fixture
  和协议升级显式支持，不增加宽松 fallback。
- **只更新 owner 导致消费者混装**：所有消费者精确检查 v3，并把五 Skill 同 commit
  安装和哈希核对设为发布门槛。
- **测试继续只覆盖根任务**：真实子任务 smoke test 是发布门槛，不允许仅凭单元测试
  宣称修复。
- **错误地读取父任务设置**：rollout 文件名和 `session_meta.id` 始终绑定当前线程；
  lineage 和 parent 字段仅用于身份形态验证，不参与设置来源选择。

## 实现完成后的记录

实现时在本文件 `## Comments` 下追加：实现 commit、聚焦测试和全量验证结果、五 Skill
安装来源核对、真实子任务 smoke test 的脱敏回执，以及是否执行过回滚。不要记录真实
任务 ID、完整 rollout 路径或会话文本。

## Comments

- 2026-08-31：根据原始故障、当前源码、现有测试、根任务与原生子任务的脱敏
  `session_meta` 形态完成修复设计；尚未修改运行代码或安装副本。
- 2026-08-31：实现 commit `ff9ff090cc7ccc7d853a1233035a1d18d89da63e` 已快进
  集成到 `main`。thread-settings 聚焦测试 19 项通过；仓库全量验证 144 项通过，
  repository state 为 `valid`。
- 2026-08-31：项目内与 cc-switch 两组安装副本的五个 Workflow Skills 已同步，
  每个 Skill 的内容 manifest 均与 `main` 来源一致；两组 owner 均报告
  `thread-settings-v3`。
- 2026-08-31：只读原生子任务 smoke 返回 `status: match`；隔离 fixture 的
  Dedicated Implementation Task 依次通过 thread-settings gate 与
  `verify-worktree`（`verified: true`），前后工作区均干净。fixture worktree 和
  分支已清理，临时目录已移入废纸篓；未执行回滚。
