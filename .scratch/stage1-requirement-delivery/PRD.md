# 阶段 1 冻结需求的目标交付

Status: ready-for-agent
Lifecycle: completed

## Problem Statement

阶段 1 已能创建、提交和冻结唯一需求，阶段交接也已能检查目标分支上的交付，但两者之间缺少正式生产动作。在独立或 detached 工作区完成需求时，用户仍可能遇到临时整合和重复授权；只完成来源提交的成功话术又可能早于真正可进入阶段 2 的时刻。历史现场不是当前仓库的已复现事故，本方案依据当前源码调用链补齐缺口。

唯一需求权威是冻结快照 `2026-09-17-stage1-requirement-delivery`，提交 `452107353fd03e9852dc9f27be8d4624644074f7`，SHA-256 `38c8f871d1de39e788e5802f6b967b331783cfc62d4984845f5207d00707e8ea`。本文不改变需求；当前控制器另已授权连续执行 2→3→4，本方案子任务仍只交付规划。

## Solution

阶段 1 从入口就记录来源工作区、目标检出区与分支、文档范围和交付责任方。普通路径优先直接在规划目标准备需求。需求确认后，沿用现有冻结适配器，再由阶段 1 执行有界文档交付：从冻结 Git 对象读取约定文档，在目标检出区只提交这些文档，不整合来源的其他提交。

只有目标上的实际 Git 对象和文档通过既有交付接收校验，才产生可进入阶段 2 的完成证据。来源已在目标时只验证。失败明确显示“需求已确认，交付待解决”，保留唯一来源、原 intent、真实结果和恢复位置。连续流程自动继续；逐阶段流程只在原有阶段入口等业务决策点等待。

## User Stories

1. 作为阶段 1 用户，我希望开始时就知道需求将交付到哪个目标分支，以免交接时再选目标。
2. 作为普通阶段用户，我希望直接在确定目标冻结合法需求，以免制造额外整合步骤。
3. 作为独立工作区用户，我希望冻结需求按正式路径交付，以便阶段 2 直接读取同一内容。
4. 作为 detached 工作区用户，我希望交付不依赖来源分支名，以便保留来源提交身份。
5. 作为仓库所有者，我希望无关的已暂存、未暂存和未跟踪修改原样保留，以便不中断其他工作。
6. 作为仓库所有者，我希望来源提交夹带的代码或其他文档不会进入交付，以便授权范围可核验。
7. 作为需求负责人，我希望相同路径的冲突明确停下，以免目标中的新需求被覆盖。
8. 作为阶段 2 用户，我希望成功交接包含来源与交付两种提交，以便核实实际冻结来源。
9. 作为恢复操作人，我希望丢失响应后先查实际结果，以免重复提交或重复交付。
10. 作为恢复操作人，我希望未完成交付保留已确认需求，以免重新拷问或生成第二份需求。
11. 作为并发工作用户，我希望无关目标推进可在核对后继续，以免被无关提交迫使重新确认需求。
12. 作为专用任务用户，我希望专用任务承担明确的文档交付责任，控制器仍负责认证接收与归档。
13. 作为 Discussion Topic 用户，我希望 CP 保持 checkpoint ref 语义，而目标交付作为独立动作，以便不混淆冻结和 Phase 推进。
14. 作为流程控制器，我希望 delivery 不授予 Topic 写权或 Phase 权限，以便保持既有生命周期边界。
15. 作为连续流程用户，我希望正常 1→2 不再问整合授权；作为逐阶段用户，我仍能决定是否进入阶段 2。
16. 作为维护者，我希望通过真实 Git/CLI 测试观察目标、索引、文档与恢复结果，而非只验证内部函数的返回值。

## Implementation Decisions

### D1 — 责任和唯一事实来源

保留现有 A（entry/requirement）、B（handoff/dispatch）、C（workflow progression）边界。增加一个窄的 requirement-delivery 生产模块，唯一职责是把已冻结的阶段 1 文档交付到已授权 Git 目标，并返回 B 已接受的 `{commit, paths, receipt}` 或 source-on-target 的 null proof。A 继续拥有创建/冻结；B 保持只读验证；C 持久化原 intent、调用生产者并保存结果。生产者不创建任务、不选择目标、不维护 ledger、不推进 Phase。

普通路径由当前阶段 1 执行者负责；专用路径由已绑定 Dedicated Problem Framing Task 在完成结果发送前负责；讨论附着路径由当时获授权的阶段 1 执行者交付已完成的 CP 文档。Workflow Controller/Phase Source Task 仍认证接收、accept/finalize、后继 ready/activate 与归档。没有写权的控制器不接管需求写入，生产者也不更改 CP/DW 权限。

新增模块与既有协议的分工是可局部替换的适配器选择，遵循 ADR-0001/0007/0008，无需另立难逆架构 ADR。

### D2 — 输入、输出和调用接口

生产者暴露 prepare、deliver、reconcile、verify 四个窄动作，使用有界严格 JSON 和既有错误 envelope；独立标识 `requirement-delivery-v1`。所有字段在写入前校验，不接受任意自报哈希代替冻结收据。

prepare 输入：完整当前阶段 1 entry 请求、完整成功 frozen/attached Git CP 结果、目标 `{repository, branch}`、排序且唯一的 owned Markdown 路径（包含 requirement，最多 32 个）、原控制器授权引用。普通 freeze 的 owned paths 或专用任务冻结授权限定该范围。讨论附着仅交付该 CP 的路径，不加入其他文件。入口角色、当前 actor、来源/目标 Git common directory、阶段、原 CP 状态/写权、包 pin 均须验证。目标必须是明确存在、当前检出该分支的普通检出区，来源可为同仓独立/detached 检出区；不临时切分支或创建替代工作区。

只读 prepare 返回 sealed intent：原始 actor/authority/package/source、source_commit、requirement_identity、目标与观察到的 target HEAD、比较基线、每个路径的来源 blob/mode 和目标 HEAD/index/worktree 的前态、无关文件/索引证据、operation ID。重试保存并复用这些原始事实，不从文件正文重建。

deliver/reconcile 只接收同一原 intent 与当前 entry；verify 接收完整原结果。成功结果保留 source_commit、delivery_commit、requirement_identity、target、target_head、paths、delivery proof、原操作关联和验证事实。此结果只证明目标交付，不等于语义验收、Phase 完成或下一阶段授权。receipt 来自当前可信执行者的已保存交易身份；摘要是完整性证据，不能认证任意 JSON。

### D3 — 有界交付算法与路径冲突

复用现有 repository publication lock、字节级文档检查、literal pathspec 和 exact-path `commit --only` 思路。目标事务在锁内刷新目标及来源；从来源 commit 的 blob 读取最终字节，逐文件安全原子写入，stage/commit 仅约定路径。Git hooks/filters 保持启用；过滤后内容、父提交、实际变更路径、全部 owned 文档的 bytes/mode 必须读回相符。交付提交单父，父是原 intent 的目标 HEAD，非空 diff 只能涉及 owned paths；不 merge/cherry-pick 来源提交，也不添加来源历史。

所有 owned 文档都是普通非 executable Markdown；拒绝软链接路径、冲突索引、不合法模式、目录碰撞、路径逃逸或忽略路径。先完成全部路径检查，再写任一文档。目标 owned 路径的 index/worktree 必须匹配其 HEAD；唯一例外是目标本身就是认证来源文档检出区，当前字节/模式与冻结来源一致且范围授权相同，此时可提交这份既有阶段文档。未跟踪的同名文件即使内容相同也不是授权所有权。部分暂存或未经授权的 owned 路径修改是冲突。

对来源和目标分离的正常投递，以唯一 merge-base 的对应路径为内容基线：目标 blob/mode 为基线或已等于来源方可继续；目标另一版本一律冲突，不自动文本合并。不存在或多重 merge-base 时返回明确需要来源决策的结果，不猜基线。文件缺失作为明确前态参与比较。来源 commit 的其余差异完全忽略且不能进入提交。

同一路径的“已等于来源”只能跳过写入，不能替代交付证明。优先级为：来源可达且所有 owned 路径在目标匹配 → null proof 验证；已有本 intent 的实际交付提交 → 验证复用；原可信 checkpoint 已保存另一合法交付 proof → 验证复用；否则仅在确有 owned 文档变化时创建单父有界提交。若所有字节相同但没有来源可达或合法已保存 proof，返回 `delivery_unverified`，保留现场并请求原证据恢复，不制造空提交、不把任意相同内容当成原交付。本范围不扩展既有 proof。

### D4 — C 的持久化、顺序与失败恢复

在现有 `workflow_requirements` 交易集合增加 delivery 类型，外层仍为原 conversation/runner checkpoint。增加 `deliver-requirement` 窄操作，输入 `{request: <producer prepare request>}`；C 在任何写入前保存完整 prepare request 与 intent，在调用前保存 issued，再调用生产者，最后保存完整结果。不得增加独立注册表或后台工作。现有 freeze/write 交易行为保持。若该交付属于已有受控 attempt，C 的暂停、取消和未决副作用先于新写入生效；只读 reconcile 可核对事实，继续写入须由同一原身份在已恢复授权下执行。不能用新的 delivery 请求绕过原 attempt 的 stop gate。

状态为 prepared → issued → delivered → verified；异常保留原 issued/intents、实际已完成证据和 `downstream_ready=false`，不伪装 frozen/success。这里的 verified 指交付校验，不代表 B accepted。C 重放先 reconcile 同一 intent，核对对象与目标、再决定未执行的尾部动作。新功能为当前 v5 checkpoint 的增量字段和操作；不迁移旧 pin，不改变既有操作/交付 proof 的语义。构建包继续整体 pin。

交付提交附带原 intent digest trailer，仅用于寻找候选；复用前必须验证唯一候选、父提交、完整路径集合约束、全部 bytes/mode、目标可达性与当前实际文档。扫描 Git 对象/历史沿用冻结恢复先例，不能仅凭 trailer 推断成功。对象存在但不在目标历史时返回 detached/unknown 证据，禁止重造提交或移动 ref 来掩盖不确定性。多个候选为 ambiguity。

中断分支：写入前停止可直接重试；部分文档已写但未提交时只允许每个路径保持原前态或本 intent 的期望态，保留已写文件并补齐剩余部分；hook 失败留下本 intent 路径的暂存状态，核实后继续；提交成功但结果丢失先查对象并复用；提交后文档/无关用户工作发生漂移，保留提交并报告有限 completed_evidence，待原所有者核对后复用，不 reset/stash/clean。

目标并发推进的顺序：先查旧 intent 是否已经交付，再检查原目标分支的后代关系与 owned 路径。已交付且目标仍保留全部冻结文档则只读成功；未产生写入/提交且目标仅在 owned 范围外前进时，C 保存旧观察和关联的新 prepare intent，在同一授权与唯一来源下继续，不追加用户确认。发生过部分副作用、非快进、目标/actor/权限改变或 owned 路径改变则停止，保留原 intent，走现有异常决策；绝不静默改目标或重建来源。锁只协调参与协议的写者，外部 Git writer 仍遵守现有唯一写者规则。

### D5 — 冻结成功、交付成功和阶段成功

阶段 1 入口和原完成确认块明确目标分支、文档范围与交付责任，授权范围内的本地交付包含在既有正常动作中；不新增末尾整合确认。完成确认、业务拷问、专用任务创建等原权限点保持。

最终顺序：语义确认 → 最后文档写入 → A freeze/CP 完成 → 目标交付及读回 → 原有 wrapper claim/认证结果发送 → B receive/accept → 由 source 完成/finalize 所需 Phase → 后继阶段入口。附着原 claim/CP 顺序若由已有生命周期契约规定，以原契约保持 working/output evidence 一致为准；交付必须在宣称 stage completed 和 B receive 之前，不能因交付改变 CP 或 Phase 权限。单独来源文档内的旧“拷问完成”语义标记保留为需求确认记录，不作为目标交付成功证据。

普通 footer 必須消费真实交付结果，列出 source_commit、delivery_commit、目标、路径/哈希、proof/recovery 引用。交付不成输出“需求已确认，交付待解决”，不得显示阶段完成或可进入 2方案。专用结果保留原 source commit/hash 构成的 delivery ID，新增携带已有格式的 target delivery proof，避免把原 ID 换成交付提交。重复消息按现有认证 ID ACK。

讨论 CP 的完成仍可能先于目标交付；它只证明来源冻结。非 Git CP 保留原 snapshot 权威，不产生 Git 目标交付成功；报告尚不满足 Git 阶段 2 入口，不把它转换成第二份文档。Stage 0 不因此新增交付权限。

### D6 — B 的接收修正与阶段 2 接手

来源验证保持使用来源 entry/原 discussion_project，目标验证明确使用已确认目标 Git 检出区，不能要求 delivery commit 成为 detached 来源 HEAD 的祖先。抽取或复用只读“目标冻结文档”校验时，显式传目标 root/head，不伪造 host identity、不改变 A entry 的实际 cwd 验证。

B 继续保存 source_commit 和 delivery_commit，证明仍是 `{commit,paths,receipt}`。交付当前目标必须保留全部 owned 文档的来源字节/模式；Stage 2 创建 Flow Worktree 后再次检查同一冻结身份与目标 proof，并验证 Flow 中需求文档。目标推进改变受保护文档则阻止启动，保留原交付；无关推进不要求来源分支 merge。专用 receive/accept 两次均检查实际目标，历史 ACK 不可代替后继入口的新鲜检查。

逐阶段只保留进入阶段 2 的选择，连续模式不增加正常交接确认。阶段 2 不承担补交付；2→3→4 Flow Worktree 机制完全沿用。

### D7 — 变更契约预检结论

每个受影响入口的证据在 Further Notes 的调用链清单中。已发现并在范围内解决四处不一致：B 目标侧校验实际依赖来源 HEAD（D6）；阶段 1 footer 把文档提交当成全部完成（D5）；专用模板“实现区与暂存区干净”不能阻止无关已暂存修改（改为精确范围和保全证据，D3/D5）；C 声明缺交付只能阻塞，与本次新增生产者的编排需要对齐（D4，C 自身仍不写 Git）。

就绪检查：每个行为有唯一 owner；生产者输入/输出/失败、来源与目标测试 seam 已明确；复用/冲突/目标漂移/未知副作用优先级已穷尽；持久化仅使用现有 checkpoint；三种来源与两种流程模式都有接受条件和测试。实现阶段可决定私有辅助函数、错误信息措辞和测试组织，不能重选整合算法、生命周期权限或恢复规则。

## Testing Decisions

原生 testing-seam 问题按连续模式预授权作答：首选现有真实 Git/CLI → C → producer → B → Flow 的最高边界。沿用 entry/requirement/stage-transfer 测试中的临时仓库、真实工作区及认证宿主/设置/registry 测试替身。只替代外部宿主事实；Git、文件读写、CLI JSON 和交付验证使用真实执行。异常点可注入一次失败或终止进程，断言可观察目标/文件/索引/结果，避免镜像内部算法。

| 测试 | 条件与观察结果 | 需求验收 |
| --- | --- | --- |
| T1 | detached 来源通过 A 冻结，C 交付到目标，B receive/prepare 成功，从目标创建 Flow 并读取同一需求；来源 commit 无需在目标历史，delivery commit 无需在来源 HEAD 历史 | 1、4 |
| T2 | 来源提交含无关文件，目标有无关 staged/unstaged/untracked 修改；交付只变 owned 路径，无关 blob/mode/index stage 逐项不变；不把索引缓存字节的正常重写误判为内容变化 | 2、3 |
| T3 | source-on-target 和合法已有 proof 重试均不新增提交；相同字节但无合法 proof 不造空提交 | 1、4、5 |
| T4 | 目标 owned 路径分叉、partial staging、同名 untracked、软链接、模式、忽略路径、越界列表、跨仓身份及未授权 actor 被拒绝且写入前不污染现场 | 3、4、6 |
| T5 | 分别在 intent 持久化、写入一部分、暂存、hook 失败、提交成功/返回前、C 保存返回前中断；重启相同 checkpoint 复用文档和唯一 commit；未知结果不盲目重复 | 5 |
| T6 | 无关目标前进：先对账后安全刷新；已交付后目标前进：读回复用；owned 路径变化、非快进、多个匹配对象和不可达提交：阻塞并保留证据 | 3、5 |
| T7 | hooks/filters 变更内容、提交后文档漂移或无关索引漂移时，有限 completed_evidence 不被 B 当成功；恢复原现场后复用对象 | 3、4、5 |
| T8 | 专用 standalone 任务 freeze→交付→原 ID 发送→认证接收/accept；源不 merge 目标；重复发送/接受不重交付、不提前 archive | 1、4、6 |
| T9 | discussion CP 后普通 branch/index 未变，交付之后目标才前進；pending DW、错误 carrier、闭 gate/无效 Phase 权限仍阻塞；原 working/output/CP identity 不变；same-stage 与 wrapper 的完成及 successor-ready/archive 顺序保留；非 Git 来源不伪造 Git 成功 | 4、6 |
| T10 | 普通/专用/附着的 footer 与结果路径：freeze-only 一律 pending；stepwise 只等原入口选择，continuous 合法交付后直接进入；Stage 2 worktree 创建后重新校验目标 | 4、6 |
| T11 | 生成包可隔离运行新 CLI，资源闭包无漏项；严格 JSON/畸形 envelope 返回有界错误、不泄露文档或 hook 内容；既有 v5 数据没有 delivery 交易仍按原语义运行 | 7 |

项目要求：实现先做 T1 红/绿，再按行为补恢复与附着覆盖。相关 unittest 通过后运行确定性包重建、构建漂移检查、完整 `scripts/validate.sh`（包含全部 runtime/package 测试与 repository validator）。生成包是发布面，必须随源码重建；不运行本机 deploy。真实宿主任务创建/发送、归档和 UI 不能由替身测试声称已现场验证；这次需要的是确定性 Git/CLI 边界和原权限协议回归。

## Out of Scope

Stage 0 的新功能；重建 A 冻结或 B 接收协议；merge-based proof；任意跨仓搬运或冲突文本合并；用户改动清理；第二份需求；新调度器/监控/registry；更换当前运行包 pin 或迁移旧 runner；修改 2→3→4 发布和清理机制；本机安装部署、远程写入和外部 Matt/governance 插件修改。

## Further Notes

### 当前真实调用链与可定位证据

此表记录设计时基线，符号是检查入口而不是新增公共 API 要求。

| 入口/当前调用链 | Owner、现状与本次影响 |
| --- | --- |
| `src/stages/problem-framing/SKILL.md.in` 的 Commit/Complete → `references/repository-and-recovery.md` → `src/shared/references/requirement-preparation.md` | 当前完成出口只要求提交和文档检查；D5 插入交付结果消费，不重做拷问 |
| `src/shared/scripts/workflow_progress.py:prepare_requirement` → `requirement_prepare.handle` → `prepare/freeze/prior_freeze_commits/frozen_result` | 已有原 intent 持久化、单父 exact-path commit 和错误证据；D4 在同一集合增加独立 delivery 交易，不复用 freeze 成功冒充交付 |
| `src/shared/scripts/entry_prepare.py:resolve` → repository_facts / planning target comparison | entry 绑定实际执行 cwd；source entry 的 planning 目标是来源检出区，交付目标需独立明确记录，不能把另一 target 塞入 source entry 绕过校验；detached branch 为 null 的现有来源支持须真实覆盖 |
| `src/shared/scripts/requirement_prepare.py:attached` → `discussion_protocol.handle` → `discussion_core/checkpoints.py` | CP publication 只写 CP ref，pending DW 与原权限由 ledger 负责；生产者读取成功 CP，不修改其实现 |
| `src/stages/problem-framing/references/dedicated-grilling-protocol.md` finalize/send/intake → B `stage_dispatch.received/accepted` | Stage 1 receive 已调用 source 和 delivery；D5 在发送前供给真实 proof，原 source ID 和写权/claim/accept/归档不变 |
| `src/shared/scripts/stage_handoff.py:prepare/source/delivery` → `requirement_prepare.verify` → `merge-base --is-ancestor delivered HEAD` | 当前 delivery 的 root 来自来源 current；`tests/runtime/test_stage_transfer.py:test_distinct_source_and_bounded_delivery_commits_use_real_a` 为通过校验先把 main merge 回 flow。D6 去掉这一隐含依赖，T1/T8 必须无此 merge |
| `src/shared/scripts/stage_dispatch.py:received/accepted` → `handoff.delivery` | 原目标交付 gate 已存在；新生产者复用它，不降低校验；当前 stage<2 payload 已有 requirement/delivery 字段 |
| `src/stages/solution-design/SKILL.md.in` → Stage Handoff 验证 → `supervision_protocol.start-worktree/verify-worktree` → B 再验证 | Stage 2 从目标创建唯一 Flow；D6 只修正交接输入/引用的身份含义与复查，保留工作区协议 |
| `scripts/build_skills.py` → `build/skill-packages.json` resources → 五个 `skills/` 包 | 新 runtime/resource 必须进确定性闭包；生成包只通过构建修改 |

### 精确实施范围

Stage 3 可编辑的源码/配置/测试清单（不存在的文件为本方案新增）：

- `src/shared/scripts/requirement_delivery.py`（新增窄生产者）。
- `src/shared/scripts/workflow_progress.py`（delivery 交易持久化和调用）。
- `src/shared/scripts/stage_handoff.py`（目标侧验证及全 owned 范围读回）。
- `src/shared/scripts/requirement_prepare.py`（仅在需要时提取原只读 committed-document 校验供目标侧使用；不改变冻结/DW/CP 路由）。
- `src/shared/references/requirement-delivery.md`（新增生产者契约）。
- `src/shared/references/requirement-preparation.md`、`stage-transfer.md`、`workflow-progression.md`、`design-discussion/requirement-document-contract.md`、`design-discussion/lifecycle-integration.md`（仅交付/完成/生命周期调用顺序）。
- `src/stages/problem-framing/SKILL.md.in`、`references/repository-and-recovery.md`、`references/dedicated-grilling-protocol.md`、`references/templates.md`。
- `src/stages/solution-design/SKILL.md.in`、`references/subagent-protocol.md`、`references/templates.md`（仅来源/交付身份接收与目标复查；不得重写 Stage 2 工作流）。
- `build/skill-packages.json`（资源闭包）。
- `tests/runtime/test_requirement_delivery.py`（新增）、`test_requirement_prepare.py`、`test_stage_transfer.py`、`test_workflow_progress.py`、`test_attached_flow_progress.py`、`test_dedicated_stage.py`、`test_solution_design_contract.py`、`tests/packages/test_build.py`（仅上述验收的行为/契约覆盖）。
- 上述源码的确定性构建产物：五个 `skills/` 包中对应 runtime/reference/entry 和 package manifest。实现派发前从 build 输出展开为逐文件清单，禁止手改生成包或把目录通配符交给并行写者。

冻结需求与此 Spec/Tickets 是实施只读依据；阶段 4 可更新本特性 PRD/Ticket 的 Lifecycle 与完成证据。无需修改 CONTEXT、ADR、其他功能规划或部署文件。若实现需要扩大上述范围，先回控制器说明具体证据，不静默扩项。

### 原生流程与切片决策

已按完整 `$to-spec` 探索代码和测试 seam，并在连续模式下自主接受测试选择。`$ask-matt` 的 multi-session 分支判定 Tickets 有用：三段可各自端到端验证，避免一次变更同时承担生产路径、所有故障点和讨论生命周期。

`$to-tickets` 的 quiz 已按连续预授权回答：粒度为“detached 正常链”“同一交付恢复”“专用及讨论边界”三条完整行为；第二条依赖第一条，第三条依赖第二条（附着 CP/专用交付必须享有相同恢复保证）。不另设横向重构 Ticket；不需要 prefactor。发布三份 ready-for-agent 本地 Ticket，不改其他父 issue、不建立远程阻塞链接。

## Comments

### 阶段 4 完成证据（2026-09-20）

实现候选：`2233a49b78b18f8711ec25c6d87ea750b8e4b337`。Standards 与 Spec 两轴均已接受同一候选；审查引用分别为 `/root/sg_standard_standards_b5e726d_t_7aa95e6e9616`、`/root/sg_standard_spec_b5e726d_t_b7682d6c274b`。下列勾选表示已接受实现及自动化验证完成。

验证：29 项 requirement-delivery 针对性测试通过；完整 `scripts/validate.sh` 通过，655 项测试、1 项既有跳过，含确定性构建检查、包检查和 repository validator（运行记录 `/tmp/stage1-delivery-gate-full.log`）。恢复快照 SHA-256 与提交 blob 一致，候选工作区干净且 `git diff --check` 通过。阶段 4 仅修改本 PRD 与三个 Ticket 的 Lifecycle、复选框和完成证据，复用上述代码验证结果。

验证边界：真实 Git、文件、索引和 CLI 边界已自动化验证；宿主适配使用测试替身，真实宿主任务创建、消息认证/发送、归档和 UI 未现场验证。未进行本机部署或远程写入。冻结需求与方案语义保持不变。

交付范围：有界冻结文档生产者、原 checkpoint delivery 交易恢复、目标侧 proof 验证、普通/专用/讨论附着出口契约及五个生成包，完成情况见三个 Ticket。Status 保留既有分诊值，Lifecycle 独立记录完成。
