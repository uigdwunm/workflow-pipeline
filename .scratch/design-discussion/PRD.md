# Spec: `design-discussion`（0讨论）与 0—4 生命周期集成

Status: `ready-for-agent`
Lifecycle: `completed`

## Problem Statement

现有 `workflow-pipeline` 从 `1拷问` 开始，适合把已经可聚焦的目标推进到方案、实现和归档；但它没有一个能够长期、递归、跨对话且文档驱动地维护设计上下文的前置阶段。长讨论容易重复提问、遗失已确认决定、一次暴露过多问题、在总体方向变化后保留失效细节，也难以把父子话题、阶段结果、实现占用和恢复状态安全地协调起来。

项目需要新增 `design-discussion` Skill（中文界面身份为 `0讨论`），并把它作为现有 `1拷问 → 2方案 → 3实现 → 4归档` 的可选前置阶段。新能力必须在存在可验证讨论上下文时提供稳定来源、阶段路由、并行与恢复协调；在没有安全上下文时保持现有 1—4 行为不变。

## Solution

新增一个精简的 `design-discussion` Skill、五份按动作渐进加载的 reference、界面元数据和一个确定性 `discussion_protocol.py`。Skill 负责人类可理解的单问题讨论、推荐、确认、成熟判断和路由；脚本独占管理讨论账本、话题、关系、影响、对话绑定、检查点、Phase Runs、待写入载荷和生命周期状态，不允许调用方直接补丁权威 Markdown。

`0讨论` 与 `1拷问` 共用一份话题设计文档。文档写入继续复用 `supervision_protocol.py` 的共享 document lease；Git、实现 handoff、监督、worktree execution lease、集成和归档检查点也继续由该脚本负责。两套确定性协议通过类型化 JSON 和稳定错误码协作，不复制对方的状态机或权威数据。

现有四阶段包装 Skill 采用可选接入：入口先验证显式 handoff、active binding、精确文档/footer，再进行只读 `locate`；只有唯一高置信度的 `ledger` 或 `document_only` 上下文才接入讨论生命周期。`none` 和 `ambiguous` 立即沿用当前流程，不创建账本或绑定。阶段执行通过 `PR-*`/`PA-*` Phase Run 的 `prepare → ready → activate → claim → complete` 协议，把用户意图、外部载体和稳定阶段结果分开。

Git 实现支持 `exclusive-checkout-v2` 与用户逐次明确授权的 `isolated-worktree-v1`。安全判断不等于 worktree 授权；所有实现来源都必须是带 SHA-256 的已提交只读检查点。`4归档` 负责文档三方收敛和资源清理，但只有无剩余影响、运行、吸收、阻塞或未验证协调事实时才关闭 topic。

## User Stories

1. 作为设计发起者，我想显式使用 `$design-discussion` 或 `0讨论` 进入持久化讨论，以便长讨论不只依赖聊天历史。
2. 作为设计发起者，我想普通咨询保持普通咨询，以便 Skill 不会因为语义相似就擅自创建讨论状态。
3. 作为设计发起者，我想在持久化初始化完成后再开始实质讨论，以便文档、账本或绑定失败时不会得到虚假的“已进入0讨论”。
4. 作为设计发起者，我想每轮只回答一个最重要的问题，并先看到 AI 的推荐和理由，以便讨论负担保持可控。
5. 作为设计发起者，我想已确认问题不被重复询问，以便长期讨论持续前进。
6. 作为设计发起者，我想随时插入新想法而不必先回答旧问题，以便真实探索不会被固定问卷打断。
7. 作为设计发起者，我想总体方向变化后逐项复核受影响决定，以便旧细节不会被自动保留或一次性批量改写。
8. 作为设计发起者，我想每个话题拥有一份当前有效设计文档，以便需求、候选、假设、事实和待决项有清晰权威。
9. 作为设计发起者，我想暂停、拆分、结束或进入下一阶段前拥有稳定检查点，以便后续对话和阶段能验证来源。
10. 作为设计发起者，我想把成熟的大话题拆成新的 Codex 子话题，并让子话题只读取直接相关上下文，以便递归讨论不会被完整父历史淹没。
11. 作为设计发起者，我想子话题结果可回流父级，而跨边界建议必须逐项确认，以便局部探索不会静默重写总体设计。
12. 作为设计发起者，我想旧对话不可用时创建 continuation 而不是宣称恢复原对话，以便身份与历史事实保持准确。
13. 作为维护者，我想每棵讨论树只有一个权威 `ledger.md`，以便协调状态不存在 JSON、数据库或索引等第二权威。
14. 作为维护者，我想账本更新只通过确定性领域操作完成，以便并发、幂等、迁移和恢复规则可以机械验证。
15. 作为维护者，我想不同话题可以并行思考并短时串行写文档，以便共享 checkout 不会阻止独立讨论。
16. 作为维护者，我想文档写入同时校验草案写入所有权和 document lease，以便取得锁的任务不能越权修改其他话题。
17. 作为维护者，我想 Git 与非 Git 项目都能形成不可变需求检查点，以便后续阶段拥有稳定来源。
18. 作为维护者，我想 Git 历史改写后通过显式 repair-checkpoint 修复，而不是按相同摘要自动改绑，以便来源证据不可被模糊替换。
19. 作为阶段调用者，我想 1—4 只在高置信度上下文中接入0讨论，以便无上下文、歧义上下文或旧调用保持兼容。
20. 作为阶段调用者，我想 `document_only` 先只读吸收文档，并在用户确认状态变化后才初始化本地账本，以便新克隆不伪造旧协调事实。
21. 作为阶段调用者，我想合法前进只允许 `0→1`、`0→2`、`1→2`、`1→3`、`2→3` 和 `3→4`，以便阶段不能静默跳跃。
22. 作为阶段调用者，我想返回0必须显式 reopen 并复核受影响结果，以便已发布方案和实现不会在来源变化后继续假装有效。
23. 作为阶段调用者，我想阶段运行意图、外部载体 attempt 和稳定结果分开记录，以便创建失败、结果未知、迟到终态和重复执行可以安全对账。
24. 作为阶段调用者，我想外部阶段载体先 ready，再由来源 topic 重检并 activate，以便路由或来源变化时载体不会提前工作。
25. 作为 `1拷问` 使用者，我想0与1更新同一话题草案，以便需求叙事不会被复制成第二份 draft。
26. 作为 `1拷问` 使用者，我想在上下文质量不足时使用专用 grilling 任务，但仍维护同一草案和来源链，以便载体变化不改变文档权威。
27. 作为 `2方案` 使用者，我想冻结最新0/1检查点并只创建 Spec、ADR 和 Tickets，以便方案阶段不会反向改写已确认需求。
28. 作为 `2方案` 使用者，我想 solution designer 先 ready 后执行原生 `$to-spec`、ADR、`$ask-matt` 和 `$to-tickets`，以便方案工作与生命周期激活一致。
29. 作为实现负责人，我想在1阶段已充分约束实现时直接进入3，并把2按当前 scope 记为 `not_applicable`，以便不创建虚假方案产物。
30. 作为实现负责人，我想每次实现冻结唯一 execution mode，以便运行中不能从普通 checkout 静默切换到 worktree 或反向切换。
31. 作为实现负责人，我想 worktree 只在并行性为 safe 且用户确认具体安排后创建，以便技术可行性不会扩大为外部副作用授权。
32. 作为实现负责人，我想 v2 和 isolated 模式都基于已提交、带摘要的只读需求检查点，以便未提交文档变化不会暗中改变运行来源。
33. 作为实现负责人，我想 isolated 运行绑定精确 worktree、分支、基础提交和长期 execution lease，以便平台不能另建或替换工作区。
34. 作为实现负责人，我想并行实现可独立推进但进入共享基础分支必须串行重检，以便候选不会基于过期来源或依赖集成。
35. 作为实现负责人，我想来源变化只暂停实际受影响或 unknown 的运行，并通过 source-refresh ACK 前移无影响来源，以便并行工作不被无关变化全部阻塞。
36. 作为归档负责人，我想在最新基础分支对文档提案执行 apply/no-op/conflict 三方判断，以便归档不会覆盖独立文档变化。
37. 作为归档负责人，我想采用非强制、可恢复的 worktree、分支和租约清理，以便未知工作不会被删除。
38. 作为归档负责人，我想归档完成与 topic 关闭分开判断，以便剩余影响或协调事项仍然可见。
39. 作为维护者，我想 legacy handoff/checkpoint 按其内嵌协议完成，以便新协议不会迁移或破坏在途运行。
40. 作为维护者，我想远程写入、破坏性、付费和新增权限继续独立确认，以便0讨论集成不扩大现有授权。
41. 作为测试维护者，我想通过 CLI 黑盒测试观察 ledger、修订、事件、Git refs/worktree 和错误码，以便测试锁定外部契约而不是内部实现。
42. 作为测试维护者，我想在每个账本、租约、Git、载体、激活、终态、文档和清理边界注入故障，以便恢复不会重复副作用或丢失状态。

## Implementation Decisions

### 1. 新增 Skill 的结构与职责

- 新增 `design-discussion` Skill，内部名称固定为 `design-discussion`，界面名称固定为 `0讨论`。
- Skill 根文档控制在 500 行以内，只保留触发、初始化、单问题循环、确认、文档更新、成熟判断、路由、异常停止条件和 reference 导航。
- 采用同级一层 reference：话题文档协议、账本与状态协议、子话题交接协议、生命周期集成协议和模板。超过 100 行的 reference 在开头提供目录与适用场景。
- 不新增 README、快速参考、CHANGELOG 或无实际用途的 assets。
- 生成 `agents/openai.yaml`，并把新 Skill 加入依赖检查、仓库验证和用户文档中。

### 2. 话题、文档与项目身份

- 一个 discussion topic 对应一个 Codex 对话；子话题和 continuation 使用新对话，但 continuation 保留原 `topic_id`。
- Git 与非 Git 话题文档统一位于项目的 `docs/discussions/<root-slug>/`，项目定位清单位于 `docs/discussions/.codex-project.md`。
- 每份话题文档保存 `project_id`、`tree_id`、`topic_id` 和父级引用快照；正文区分 confirmed decisions、candidate solution、tentative assumptions、facts 和 pending questions。
- confirmed decision 使用稳定 `decision_id`；文末只保留简洁决定演变，完整历史由 Git 或不可变非 Git checkpoint 承担。
- 明确进入持久化0讨论即授权最小初始化；普通设计咨询不得初始化。初始化必须在第一个实质问题前完成并回读验证。

### 3. 单问题讨论与影响复核

- 普通轮次只暴露一个当前问题：先承接、说明必要性、给出推荐与理由，再请求一个决定。
- 不把倾向、探索、沉默或未反对当成确认；疑问和反对先回应。
- 新想法会挂起当前问题；确认后由模型判断旧问题继续、调整、替代或失效。
- 总体方向变化先确认新方向，再登记受影响 decision；逐项让用户选择保留、调整、替换或废弃，脚本不自动替用户裁决。
- 成熟度由目标、范围、用户场景、关键行为、约束、异常、待决项和下一阶段输入完整性判断；不按轮次或文档长度判断。

### 4. 单一权威 Ledger

- 每棵讨论树只有一份 `ledger.md`。Git 项目存放在 Git common dir 的 cc-switch 协调根；非 Git 项目存放在项目根 `.codex/design-discussion/v1/`。
- ledger 使用机器可解析 frontmatter、固定章节和每章节唯一严格 YAML 记录块。章节至少包括 Current Topics、Pending Items、Phase Results、Checkpoints、Phase Runs、Pending Document Writes、Impacts、Relations and Coverage、Dependencies and Active Implementations、Conversation Bindings 和 Recent Events。
- YAML 禁用 tag、anchor、alias 与 merge key；输出固定键序、稳定 ID 排序、UTF-8 与 LF。`content_digest` 对排除自身后的规范内容计算 SHA-256。
- `current_phase`、`phase_state`、`review_state`、`topic_state` 正交建模；树状态只派生、不持久化为可写字段。
- 关系使用 typed relation，至少覆盖 parent、covers、absorbs、depends、blocks、affected 和 continuation；父级关系唯一且无环，覆盖与吸收不能隐式推导。
- 最近事件与幂等键窗口固定保留 200 条；创建类记录永久保留创建幂等键。压缩只删除已终结、无引用且结果已折叠的旧运行记录。

### 5. `discussion_protocol.py` 领域接口

- 新脚本通过 stdin 接收单个类型化 JSON 请求，通过 stdout 返回单个 JSON 响应，诊断写 stderr；JSON 不落盘成为第二权威。
- 查询包括定位、树/话题/phase-run/handoff 读取、claim 校验、route、parallelism、pending impacts、checkpoint、pending document write 和 validate。
- 更新按领域边界分组实现：树/话题/问题/结果/关系/实现；handoff/binding；impact；checkpoint；pending document write；phase run；迁移/repair/压缩/GC。
- 所有更新携带项目、树、操作者话题、expected ledger/record revision、UUIDv4 幂等键与类型化参数；一次成功只递增一次 revision 并追加一个事件。
- 更新采用树级文件锁、持锁重读、记录级乐观并发、last-good、同目录暂存、fsync 和原子替换。普通锁最多等待 5 秒，迁移锁最多 30 秒。
- 正式 ledger 有效时遗留临时文件不能覆盖；只有正式文件缺失/损坏且存在唯一、连续、校验通过的恢复候选时才自动恢复。
- 路径、Git ref、conversation ref、project/tree/topic identity 与 common-dir 归属执行严格验证；不得按名称、时间或“唯一候选”猜测身份。

### 6. 文档写入与稳定检查点

- `0讨论` 与 `1拷问` 共用同一话题草案。每次写入先在 ledger 创建不可变 `DW-*` 载荷和 before/after 摘要，再取得 `supervision_protocol.py` 的共享 document lease。
- `supervision_protocol.py` 的 document lease stage 枚举新增 `design-discussion`；lease 验证结果作为 `discussion_protocol.py` 应用 DW 载荷的类型化凭证。
- document lease 只授权一次有界文档写；调用者还必须是目标草案当前写入所有者。写后验证字节、释放 lease，再完成 DW 记录；等待用户或其他租约时不得持锁。
- Git 稳定来源统一使用 `CP-*`：prepared 意图冻结文档摘要、decision 摘要、路径、purpose、预期基础 ref 和提交标记；提交使用 `Codex-Discussion-Checkpoint` 与 `Codex-Document-SHA256` trailer。
- Git 提交结果未知时先按 trailer、parent、tree、精确路径、blob 和摘要对账；唯一完全匹配才收养，不存在才允许重试，多候选或不匹配阻塞。
- 非 Git 稳定来源使用协调根 `checkpoints/sha256/` 的内容寻址不可变快照；相同摘要复用，内容冲突视为损坏。显式 GC 先 dry-run 固定清单，再取得用户对清单与 ledger revision 的确认。
- document lease 与 Git/repository coordination 分离。普通0/1稳定点先保存文档并释放 document lease，再取得短期 Git 协调权限提交精确路径。

### 7. 子话题、continuation 与外部创建

- 子话题创建采用三段式：持久化 handoff 意图与 child topic → 调用 `create_thread` → 真实 conversation ref 完成 active binding。
- handoff payload 分为身份信封、工作快照和权威引用；采用白名单与 SHA-256，工作快照软上限约 4,000 tokens，完整 prompt 硬上限约 8,000 tokens。
- 新任务第一轮只验证身份、摘要、项目与协调状态并 accept handoff；下一轮才开始实质讨论。
- handoff 与 attempt 分别使用 `H-*` 与 `A-*`。`setup_pending`/`outcome_unknown` 不得盲目重试；明确失败可在同一 handoff 下新 attempt；强制重试需要用户确认并撤销旧 attempt 的绑定资格。
- 每个 topic 最多一个 active conversation binding。continuation 通过原子切换 binding 和 `continuation_of` 关系承接原 topic，不创建新 topic。

### 8. 1—4 可选发现与生命周期

- 入口发现优先级为：认证 phase/handoff → active binding → 精确设计文档或稳定 footer → 只读 `locate`。
- `ledger` 读取完整协调状态；`document_only` 只吸收可验证文档和结果，活动实现、覆盖、依赖、阻塞和旧 binding 保持 unknown；`none`/`ambiguous` 保持原流程且零写入。
- 强证据冲突返回 `discussion_identity_conflict` 并停止；不得让用户从内部 ID 候选中选择。
- 合法正常转换固定为 `0→1`、`0→2`、`1→2`、`1→3`、`2→3`、`3→4`。返回0只能显式 reopen 并逐 decision 复核。
- 阶段完成与进入下一阶段是独立事务。Phase Run 正常序列为 `prepared → setup_pending → ready → active → completion_claimed → completion_pending → completed`，并支持 blocked、failed、outcome_unknown 和 cancelled。
- 用户确认先创建 `PR-*`，每次外部载体调用创建 `PA-*`。外部载体只在可信身份 claim 后 ready；来源 topic 重检来源、route、影响、覆盖、依赖和活动实现后原子激活。
- 载体只提交 completion claim；来源 topic 验收后原子完成 run、phase result、topic state、关系/实现状态和单一事件。

### 9. 四个包装 Skill 的集成

- `problem-framing`：在安全 discussion 上下文中接管0/1共用草案、支持当前对话或专用 grilling phase run、增加 ready/activate 与 `1→3` 路由；无安全上下文时完全保留现行 draft/migration 流程。
- `solution-design`：接受0/1 checkpoint 与 route，solution designer 先 ready 后 activate；2只拥有 Spec/ADR/Tickets。保留逐阶段/连续模式，但0不产生连续模式。
- `guided-implementation`：在安全上下文中验证 phase authority、来源 checkpoint、覆盖吸收和 parallelism；支持 `exclusive-checkout-v2` 与 `isolated-worktree-v1`，冻结 execution mode，精确绑定实现任务与工作区。
- `change-closure`：在来源 topic 内执行4，验证3结果和 closure authority，在最新基础分支收敛文档，按 execution mode 非强制清理并条件关闭 topic。
- 所有包装 Skill 在 `none`/`ambiguous` 下保留当前入口、footer、发布和权限行为；legacy 在途协议继续按嵌入版本完成。

### 10. 实现并行、来源刷新与归档

- `check-implementation-parallelism` 比较仓库相对代码范围、模块/符号/API/数据库对象、依赖、基础提交、分支关系与 worktree 占用，返回 safe、blocked 或 unknown；相关记录变化使旧判断失效。
- `isolated-worktree-v1` 只有 safe 且用户确认精确工作区安排后创建。运行绑定 topic、PR、implementation、base commit、branch、规范化 worktree 与长期 execution lease；平台无法绑定时阻塞。
- v2 使用普通 checkout、长期 repository lease 和零 worktree；若仓库有活动 worktree execution lease、集成或归档临界区则排队并重检。
- 两种模式都从共享基础分支上已提交、带 SHA-256 的 `implementation-source` checkpoint 启动；运行中不编辑需求草案。
- 来源变化先按 decision 与 scope 产生 impact。无实现影响时采用 source-refresh candidate → 实现载体 ACK → 来源 topic commit 的三段式；有影响或 unknown 时保持 paused/pending review。
- isolated 候选进入共享基础分支时在短期协调 lease 下逐个重检与集成。合并后保留 worktree、分支、execution lease 和文档提案直到4归档。
- 4在验证过的基础 checkout 中对文档 proposal 三方判断，随后非强制清理 worktree、已合并分支、execution lease 和临时证据。归档完成与 topic close 分开记录。

### 11. 协议所有权与 ADR

- `discussion_protocol.py` 独占 discussion ledger、topic、binding、relations、impacts、checkpoints、Phase Runs、DW 与 lifecycle transitions。
- `supervision_protocol.py` 独占 repository/document/repository-coordination/worktree-execution lease、implementation handoff、supervision、integration/closure checkpoint。
- 两脚本只通过类型化接口和凭证协作，不直接编辑或复制对方的权威状态。该边界记录在 ADR-0001。

### 12. 错误与兼容

- 协议响应统一包含 `ok`、稳定 error code、中文说明、相关非敏感权威 ID、当前 revision/state、retryable 与底层 cause。
- 稳定错误覆盖 validation、identity、revision、idempotency、migration、recovery、handoff、checkpoint/DW，以及 phase/source/route、execution/worktree、integration/closure 分组。
- 调用方不得解析自然语言决定恢复动作；状态/来源冲突先重读，协调繁忙可等待后重验，身份/绑定/确认/文档冲突与关闭前置失败必须停止协调。
- 不改变 Matt 原生 `$ask-matt`、`$grill-with-docs`、`$to-spec`、`$to-tickets`、`$implement`、`$tdd` 和 `$code-review` 的专业流程。

## Testing Decisions

### Primary seam

- 主要测试接缝是 `discussion_protocol.py` 的进程级 CLI：每个测试向 stdin 发送单个类型化 JSON 请求，解析 stdout 的单个 JSON 响应，并检查临时 Git/非 Git 项目中的可观察 ledger、文档、快照、Git refs/worktrees 与恢复文件。
- 测试不直接调用脚本内部 helper，不断言 Markdown 序列化实现细节；只验证固定 schema、规范化输出、revision/event 数量、状态与错误契约。
- 对需要与租约协作的用例，通过 `supervision_protocol.py` 的 CLI 获得真实 document/repository/worktree lease 凭证，再交给 discussion CLI；不 mock 另一协议的内部函数。

### Secondary seams

- `supervision_protocol.py` 继续使用进程级 CLI 契约测试，新增 `design-discussion` document lease stage、短期 repository coordination lease、长期 worktree execution lease、双 execution mode handoff 与新版 closure checkpoint 的 acquire/verify/release/reconcile 行为。
- Skill 与 reference 使用仓库级静态验证：quick validator、依赖解析、禁止用户绝对路径、reference 链接存在、Skill 行数与 reference 目录规则、稳定模板字段，以及 none/ambiguous 下可机械观察的旧流程契约 invariants；不承诺生成式对话文本逐字节相同。
- 包装 Skill 的生命周期行为以确定性协议的集成场景测试为主，不构造依赖自由文本措辞的脆弱单测。

### Behaviour coverage

- 初始化、单 active pending item、新想法挂起/恢复决策、decision impact 逐项复核、成熟/路由检查。
- Git common-dir 与非 Git 存储、document-only、none、ambiguous、强身份冲突和非 Git→Git 迁移。
- optimistic concurrency、幂等重放/冲突/过期、锁超时、last-good、临时文件、人工篡改、schema migration 与 event/record compaction。
- handoff/continuation 的 prepare、setup pending、active、accepted、failed、outcome unknown、cancelled、迟到和双 claim。
- checkpoint 的 prepared/queued/outcome unknown/completed/pending verification/broken/repair，commit trailer 对账和非 Git immutable snapshot/GC。
- DW payload 的 before/after、lease 验证、所有权冲突、超时恢复、重复应用、孤儿/缺失/损坏载荷。
- 六条合法阶段转换、非法跳转、reopen、Phase Run 全状态、重复 ready/terminal、未授权 attempt、source/route drift 与 completion pending。
- 当前/专用1、2 solution designer ready/activate、3精确工作区、4来源 topic 的载体边界。
- safe/blocked/unknown parallelism、worktree 确认、v2排队、两个 isolated 实现并行、串行集成与精确工作区绑定失败。
- 父子 covers/absorbs、活动实现阻塞、partial integration、`no-code-integration` 与 `1→3` 的 scoped `not_applicable`。
- 文档 proposal apply/no-op/conflict、正常/部分/取消清理、归档完成但 topic 保持 open、满足条件后 close。
- 新 v2、isolated、legacy v1/旧 worktree handoff/checkpoint 的版本分派与不迁移。

### Fault injection

- 在账本事务、document/repository/worktree lease、Git commit/worktree/merge、外部 task/agent 创建、ready/activate、completion claim、文档提交和清理的每个边界注入明确失败与 outcome unknown。
- 每个故障场景验证：最终 ledger 可解析且 revision 连续；每个成功事务只有一个事件；ID 不回收；重复外部副作用不发生；无关文件、staged paths、refs 和 worktrees 保持不变；恢复点与 error code 指向同一权威状态。

### Existing prior art

- 沿用 `skills/guided-implementation/scripts/test_supervision_protocol.py` 的临时仓库与 CLI 并发测试模式，但新测试优先通过 subprocess 调 CLI，减少对脚本内部 API 的耦合。
- 沿用 `scripts/validate.sh` 统一执行 Skill quick validation 与 Python unittest；扩展为发现 `skills/**/scripts/test_*.py`，避免新增测试文件需要重复手工登记。

## Out of Scope

- 本 Spec 不重写 Matt Pocock Skills 的问答、方案、Tickets、TDD 或 review 流程。
- 不提供按 topic ID、名称或别名搜索并恢复话题的用户流程。
- 不把内部 topic/tree/PR/PA ID 暴露为普通 footer 的用户操作界面。
- 不为0讨论创建 Git worktree；worktree 只属于3实现且必须逐次明确授权。
- 不提供跨根讨论树的隐藏原子协调；外部代码库/服务依赖仅为只读引用。
- 不自动迁移或升级 legacy 在途 handoff/checkpoint。
- 不自动执行远程写入、PR、部署、发布、付费或破坏性操作。
- 不在本阶段实现任何 Skill、reference、template、协议脚本或测试。

## Completion

- 实现结果：九项 Tickets 均由3实现逐项验收，候选提交为 `07f80210be6dd2b7d3c7a35662ef5d5085851e81`，并通过合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成到 `main`。
- 最终决策与偏差：实现保持可选0讨论、无安全上下文时1—4兼容、单问题讨论、可递归子话题、共享文档锁、双实现模式和逐次 worktree 授权等既定边界；没有需要回写 Spec 的实质偏差。
- 验证：主任务在合并后独立运行 `bash scripts/validate.sh`，145 项测试通过；仓库验证报告 5 个 Skill、15 个渐进引用、5 组测试且无问题。
- 剩余风险：未发现阻塞性风险；本次未请求也未执行 push、PR、部署或其它远程写入。

## Further Notes

- 2026-08-21 后续加固：子话题/continuation 已接到真实 Codex task seam；fallback 验收改为可机械观察的 legacy contract invariants；discussion 使用 66 个 canonical operation 加 2 个兼容 alias 的单一 registry 和懒解析 `RequestContext`；supervision 使用 40 个 `CommandSpec` 的单一 registry 与参数化 holder policy；旧 Skill 改为 action-specific 渐进加载。`bash scripts/validate.sh` 最终 153 项通过，仓库验证为 5 个 Skill、17 个 reference、5 组自动发现测试且无问题。
- 权威需求来源：`/Users/zhaolaiyuan/Documents/Codex/2026-08-06/wo/outputs/design-discussion-overall-design.md`，SHA-256 `9578fa64ba31461b4ada1d3245b5271bf732185286d0cfa21a56ba775fd86d1f`。
- 迁移承接：`/Users/zhaolaiyuan/Documents/Codex/2026-08-06/wo/outputs/design-discussion-workflow-pipeline-continuation.md`，SHA-256 `e88a1ee1736ecce271d52bf719ef9323579c2eab60e79acda15ed640c32c051c`。
- 本地 tracker 目标为 `.scratch/design-discussion/PRD.md` 与 `.scratch/design-discussion/issues/`；`Status` 继续使用 Matt triage vocabulary，独立的 `Lifecycle` 表示工作闭环。自动领取必须同时要求 `Lifecycle` 不是 `completed`；验收项全部完成后必须写入 `Lifecycle: completed`，不能伪造一个 `completed` triage label。
- `CONTEXT.md` 当前不存在；本 Spec 沿用权威总体设计中的 canonical terms。实施时如新增真正的领域术语缺口，另走 domain-modeling，不在方案阶段创建占位 glossary。
