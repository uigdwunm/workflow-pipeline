# Ticket 下函数级执行计划与实现期间内部修订

Status: ready-for-agent
Publication: published — local Markdown tracker
Review: accepted fep-solution-review-20261001-01
Flow mode: continuous
Authorization: user-current-conversation:confirm-continuous:fep-solution-review-20261001-01
Requirement: `docs/requirements/2026-10-01-function-execution-plans.md`
Requirement commit: `229bbf9ea9bb6a0227a8871a721a1c4783f59ddd`
Requirement SHA-256: `0bc6ab5e6dbbd7fc44cb896afe0f35b6a5075ddd5c58f010dd6ac09707573c72`

## Problem Statement

现有 Ticket 已描述交付行为、验收和依赖，但执行者仍需临场决定具体函数、契约和调用顺序。Stage 3 的原生分配虽然已有文件与测试范围，没有可靠的函数计划身份；规划文件又是整体保护字节，无法在实现开始后由上层修正内部设计并同步权限。Dispatcher 目前还可在 bounded direct 例外中编码，与此次确认的三层分工冲突。

用户需要在 Stage 2 看到可执行的函数级计划，同时允许原上层对话依据事实调整内部实现，不因私有函数拆分、移动或新增辅助函数反复被打断；功能、公共契约和重大架构变化仍由用户决定。

## Solution

保留方案 → Ticket → 执行计划。每个实施 Ticket 在原有行为、验收、Blocked by 之后包含版本化函数计划块；一个计划说明一个具体函数的能力和大致过程，多个紧密关联计划可组成一次 Execution Agent 分配，也可共用行为验证。Spec 拥有设计/测试决策，Ticket 拥有切片和局部计划，冻结需求拥有功能范围，Git 与原工作流记录共同追溯版本。

Stage 2 在封存下游文件范围时同时封存完整计划目录，Stage 3 只从当前目录派发。原上层对话拥有内部修订权：Dispatcher 报告原因及影响、暂停受影响任务；上层核对证据，在短暂全停止窗口中修改精确规划字节、提交规划、同步文件范围与当前计划权威，然后原 Dispatcher 重新派发。Dispatcher 安排、收集、验收、统一验证和提交候选，所有实现与修复字节来自已验收 Execution Agent。

新协议移除 Dispatcher 编码的 direct 入口，独立 Stage 3 仍可使用自包含 brief，但也需 inline 函数计划并通过相同分配规则。历史运行继续原固定包；没有迁移或追溯授权。

## User Stories

1. 作为用户，我希望 Ticket 保留可独立验收的交付行为，使函数细节不会取代功能合同。
2. 作为用户，我希望进入实现前看到具体函数能力和过程，使局部设计不再等到编码才出现。
3. 作为 solution_designer，我希望把函数计划放在对应 Ticket 内，使方案、切片与执行顺序可直接追踪。
4. 作为 solution_designer，我希望记录必要输入、输出、失败行为和行为验证，使实现者知道正确结果。
5. 作为 solution_designer，我希望声明真实已有函数和拟新增函数，使计划可以核对生产调用链。
6. 作为 Dispatcher，我希望领取当前有效计划身份及版本，使过期文档不能继续派发。
7. 作为 Dispatcher，我希望依 Ticket 和计划依赖安排执行，使前置输出先于调用适配完成。
8. 作为 Dispatcher，我希望把紧密关联函数分给同一 Execution Agent，使分工服从行为与文件边界。
9. 作为 Execution Agent，我希望获得精确文件权限和只读依赖，使写入范围与计划一致。
10. 作为 Execution Agent，我希望遇到计划不可执行时报告证据，使我不擅自改写上层计划。
11. 作为原上层对话，我希望在功能和接口合同内自行移动、拆分函数，使用户不被内部技术调整打断。
12. 作为用户，我希望功能、验收、公共接口及重大架构变化先被说明，使技术修订不会扩大业务范围。
13. 作为原上层对话，我希望规划提交和范围切换受停止屏障保护，使旧 writer 不会越过新权限。
14. 作为 Dispatcher，我希望保留未受影响的已验收工作，使局部调整无需重做整个实现。
15. 作为 Dispatcher，我希望受影响旧结果只作为待复核字节保留，使它不会被误算当前交付。
16. 作为审查者，我希望候选绑定当前计划和范围，使旧审查无法覆盖已改变的设计。
17. 作为运行恢复者，我希望重复请求返回同一事务，使中断不会生成第二次修订或重放未知 Git 副作用。
18. 作为运行恢复者，我希望 pause/cancel 优先于计划修订，使内部调整不能恢复已撤销的任务。
19. 作为独立 Stage-3 用户，我希望已自包含请求仍能直接实现，使普通明确小改动无需额外规划阶段。
20. 作为包使用者，我希望旧运行保留原语义，使更新只影响新运行。

## Implementation Decisions

### D1 — 载体、函数身份与就绪

新增只读 Execution Plan 模块拥有结构、Markdown 提取、计划目录、依赖和引用校验；它不持久化、不授权、不创建任务。Ticket 使用一个 `execution-plan-v1` fenced JSON block，正文保留自然语言功能/验收/Comments。计划根包含 schema_version=1、ticket_id、plan_version、spec_ref、contract_refs、blocked_by、verifications、steps、artifact_steps。step 包含稳定 id、target（精确文件、符号、add/modify/delete）、capability、approach、inputs、outputs、failures、depends_on、verification_refs、read_only。approach 为有序自然语言步骤，不是强制逐行伪代码；关键 I/O/失败写明具体结果，确实无失败分支可有带理由的 not-applicable。verifications 用稳定 ID 指向行为和可运行的测试 seam，可由多个 step 共用。

id 在同一交付内稳定；函数移动保留 ID，拆分由原 ID 保留主要能力并为新增能力分配新 ID，删去的 ID 保留 retired 历史，不复用。plan_version 是 Ticket 内单调递增正整数；Git commit + 文档 SHA-256 + 模式是内容身份，不能只相信版本数字。引用目录绑定 Ticket 路径/版本/commit/hash、Spec section、函数 ID 与依赖。没有序列化一个任意可执行调度 DSL。

已有函数必须核对符号、签名和真实 production caller；拟新增函数标记 add、精确文件/符号和拟接入 caller，不要求提前存在。Python 符号通过 AST 只读确认；其他语言使用当前设计者的真实源位置证据，机器只验证完整引用，不能声称自动理解任意语言。Prompt 与生成包修改写在 artifact_steps（精确源和输出），无需虚构函数；每个代码能力仍须有函数 step。所有实施文件被 step/artifact_steps 覆盖，测试对应行为 verifications，闭包含新增/删除文件。

Stage 2 全部实施 Ticket 完成计划、无缺失依赖/循环、验证引用存在、Spec/Ticket/manifest 一致后才可 seal。Ticket Blocked by 和函数 depends_on 联合拓扑排序；同级按 Ticket 源顺序再按 step 源顺序。函数依赖不得暗藏未声明的跨 Ticket 前置关系。计划初次封存位于当前 Scope Authority 的 execution_plan 字段，不另建权威；其 authority revision 随范围 revision 一起变化。没有 Tickets 的微小 staged 改动用 Spec 内同一块表示一个逻辑切片，不能省略计划。

### D2 — 功能合同与可修订区域

冻结需求始终整文件 protected，原 path/commit/hash/mode 不变。Ticket 的标题、ticket_id、行为描述、验收 IDs/正文、Blocked by、Spec 的 Problem Statement/Solution/User Stories、行为验收表、Spec/Ticket 声明的全部公共接口契约与业务影响边界（不论它们出现在何章节）以及适用 ADR 决定正文构成初始 contract projection。这些声明使用稳定 contract_refs 并列出 exact section/subsection 边界；缺失、重复或不可提取时不得封存。上层将这些确切章节的内容/模式与需求身份封存为 contract_digest。函数计划块中的私有函数映射/过程/顺序，以及 Spec Further Notes 的 N2 私有调用链证据与 N4 inventory/manifest 表是列明的可修订区域；Spec D1/D3/D4/D5/D6/D7 中的公共协议、角色职责、事务/候选契约与版本声明封存为 exact contract sections，其他未列明区域默认逐字节保护。验收行为、公共接口承诺和业务职责不可经内部窗口修改。

常规 allocation/bind/result/candidate/review 继续核对当前整文件字节身份，不放松现有 protected-path 检查。只有原 Controller 的已准备 plan-revision 写入窗口允许列明的规划文件变更；Git 在窗口前冻结所有 protected 文件，在 commit 后逐字节核对不可修订 projection 和所有未获规划写入权限的 protected 文件，并证明精确规划路径提交。Dispatcher 和 Execution Agent 不获得规划路径权限。把 Ticket 的函数计划块与功能合同放在同一个文件不会让整文件保护失效。

语义内部分类由原 Controller 依据 before/after 契约、调用链、受影响行为/文件和验证做出，脚本只能验证身份、结构、范围和证据关联，不能自动证明“公共接口不变”。业务影响边界在初次计划中声明为责任/模块/行为 ID。函数移动、拆分、辅助函数、私有调用适配及执行顺序调整，必须留在此边界、保持 contract_digest、公共契约和验收不变。即使原文件列表不足，上层也可增加边界内必要的精确文件，但需 manifest/生成闭包完整与来源证据；目录和任意路径扩张不算授权。

### D3 — 原 Controller 内部修订事务

保留现有 pre-Stage-3 scope-revise，仅用于接受规划后且尚无 Stage-3 preparation/launch/session 的范围校正；不把它放宽为活动阶段通用入口。新增 C prepare-plan-revision / commit-plan-revision / reconcile-plan-revision 三个有界操作，走同一 checkpoint、record lock、control transaction 和附着 ledger port。上层是唯一调用者；Dispatcher 只提交报告与证据。

报告包含 revision_id、当前 authority revision/digest、Ticket/step IDs、原因与生产事实、私有 before/after 契约、依赖影响闭包、拟规划路径/manifest diff、保留/复核结果建议和验证影响。上层检查 frozen requirement、当前包/身份/绑定、现有合同与业务影响边界；unknown public-contract/模块影响一律进入用户决定，不猜测为内部。纯内部结论不发用户审批或报告；工作流原记录保留说明。

prepare 阶段先关闭新 allocation、release、result acceptance、candidate/review/validation 入口；受影响工作立即暂停。在当前 Dispatcher、所有 Execution Agents、审查者、统一验证命令及相关未决 native/Git 调用均经原宿主核对停止/完成后，Git 冻结完整 HEAD、branch、index 的逐项内容、全 implementation/protected presence/content/mode 指纹，以及已验收分配。没有全停止证据只返回停止/lookup 动作，没有规划写入权。旧结果可以在屏障前按原 authority 完成收集和验收；屏障封闭后收到的晚结果只留审计，不重新授予写入或自动验收。

所有会改变正式计划或文件授权的局部调整都需要这个短暂全停止窗口，包括只移动一个函数、单 Ticket 顺序变化、新增边界内辅助文件和只改函数依赖。理由是所有 Agent 共用一套 HEAD/index、保护字节及整工作树快照；规划 commit 和权限 revision 不能与实现 writer 并发。只在已授权函数体内采用另一种等价编码方式、不改变正式计划的普通技术选择不算修订。付出的成本是暂停当前无关 writer 和重新核验快照；不停止其他交付、其他 Worktree 或已终结的原生身份。

prepared 持久化旧 authority、原报告/分类引用、snapshot、精确规划写入路径和原 Controller-only release。只有该 release 可编辑相关 Ticket 计划块、Spec 允许区域及必要 manifest。保持实现文件及其 index 条目不变，使用仅列明规划路径的 Git commit（即使已有 staged/unstaged 实现字节也不得顺带提交）；Git 对提交父子树和 index 分区分别验真，不能用一个 clean 布尔值掩盖实施字节。禁止 reset/stash/清理实现内容。提交 unknown 时先 inspect exact commit/tree/parent 和保存的意图；能确认同一提交才继续，否则保持 barrier，不重复 commit。

commit 阶段要求当前文件和不可修订合同核对通过、计划和 manifest 闭包有效、旧 writer/未决调用屏障仍成立，并验证旧规划提交是新提交祖先、提交只含允许规划路径。C 先 journal 完整候选和 derived Git facts，再由原 Controller 的 classification/reference 通过 control transaction 一次性切换当前 Scope Authority（新 revision、execution_plan catalog/commit/digest、manifest/projections、原 baseline）、分配资格和修订历史；原 Stage-2/3 launch、delivery 和历史结果不覆写。无文件变化也有 plan revision；无 plan/scope 变化的重放只 ACK。

状态为 reported → stopping → prepared → planning-committed → activated。失败留在确切状态，重放同 revision_id/输入 ACK；同 ID 不同输入、旧 revision、snapshot/contract 漂移、外部改动均 fail closed。planning-committed 之后中断只重做剩余校验/activation，不重写文档或重复提交。activated 之后重复请求不能重获规划写权限。paused 可保留已准备事务但不 activation/continue；cancelled 撤销窗口并保留字节/历史，不可由 revision 恢复。取消期间有未知 commit 仍先核对副作用。恢复由原 C 和同一宿主/身份完成；宿主丢失沿用原 controlled recovery，不开启新宿主冒充。

### D4 — 分配和结果资格

Stage-3 launch 的 semantic 带 execution_plan 目录引用，C 当前 sealed authority 为实际来源。B prepare/render、Dispatcher plan-execution、assign/write_release 都读取同一 current revision，分配含 plan_refs（Ticket/step/版本/文档身份）、authority_revision/digest、plan_digest、精确 paths/read_only、行为/验证和无 Git 权限。refs 的依赖必须已由当前有效结果完成，或作为同一分配中按确定拓扑顺序执行；一组 plans 不得跨并行共享文件。未绑定 native ready receipt 的子 Agent 保持只读；release digest 绑定计划和 authority，不只绑定 agent ref。

execution-result 与 accept-execution 回显 refs/revision/release，检查真实字节/模式、原 allocation 全 delta 和 peer attribution。旧 release 不能在 activation 后写入；原 Agent 不接收“更新一下计划继续”式无版本指令。重新派发形成新 task_id/allocation_digest，保留旧 refs。

未受影响已验收结果只有在 step 内容身份（不把同一 Ticket 中其他 step 导致的整文档版本变化算成本 step 改变）、函数合同、相关 verification、read_only/依赖、文件归属及最终字节/模式都未变，且依赖影响闭包不涉及它时，才由 Controller 的 carry-forward 记录变为新 revision 的有效证据；原 result 不改写。不能只比较文件 hash。受影响已验收和未验收字节保留为 unaccepted revalidation_source，释放旧 reservation 前要求 writer 停止和 snapshot 匹配；原 adoption 机制扩展一个 plan-revision 来源，新的真实 Execution Agent 领取 exact adopt_paths，验证/修复后提交真实 stopped result 才能重新验收。已回到 baseline 的路径无需虚构交付。

Dispatcher 只安排、验收、运行统一验证、Git 集成和候选 commit；新流程任何实现/修复编辑必须来自 Execution Agent allocation。生成构建也在拥有所有精确 outputs 的 Agent 内执行；Dispatcher 的统一验证选用不写 tracked implementation 文件的 check 模式。candidate-ready 保留原 every-changed-path accepted delivery 约束，并要求 current refs/carry-forward/revalidation 完整；没有 Dispatcher 自写字节来源或仅由 commit 身份推断作者的通道。

### D5 — 合同外分支、独立入口与 direct

功能增减、其他业务职责/行为、公共接口、验收、较大架构或不确定影响，关闭受影响工作并通过现有原 Controller pending user-decision 说明具体变化与替代方案。internal transaction 不接受改变 contract_digest 的候选，也不把一条任意 approval reference 当扩大权限。用户可选择保留原合同、停止重做需求/方案，或明确修订上层合同；后一种必须先由原来源责任方完成新的冻结/设计决定及其原工作流授权，才可按既有受控恢复进入新合同工作，不能在 internal revision 里修改原 frozen bytes。此功能不自动回退 Stage/Topic Phase，不迁移已接受 Stage-2 结果，不默默建立新交付。用户拒绝变更时原计划可在旧 authority 和恢复屏障核对后继续。

独立 Stage 3 保留 ADR-0005 的 self-contained brief/no planning-artifact 行为，新增同一 schema 的 inline plan 与用户合同投影，由原 Controller 根据明确请求和代码事实准备，不要求每个独立调用新建 Ticket；缺失实质决策仍回 Stage 1/2。inline plan 在原 frozen B input 中保留；内部修订同 C transaction 使用新的 inline effective projection（无需 Git 文档 commit，但仍全停止、Git snapshot、scope 与结果资格切换），原输入不可改写。

新 package/runner/control 拒绝 explicit direct 和 direct escalation，在 staged、standalone、interactive、foreground 路由统一 full。历史 direct 运行仍用其原包执行、转换和恢复。此次变更明确取代 ADR-0007 的新运行 direct 例外，并仅为合同内函数计划修订取代 ADR-0011 对活动 Stage-3 scope correction 的全面禁止；不恢复旧 takeover 或任意活动范围扩张。

Foreground runner 继续只拥有 outer fields 和 host。计划修订决策/文档字节由原上层 Controller 提供；carrier 只将原决定和 exact prepared patch 引到 C，不能自称原 Controller。原 Controller 在 C window 内写入和提交规划文件；carrier/Dispatcher 只读观察和恢复。如果宿主不能提供这种 controller ingress，返回 technical_error 并保留状态，不让 carrier 代判或代写。独立 foreground brief 的 inline revision 同样由原 Controller 决定。与真实宿主验收分开记录，不能用模型 prose 证明作者或 writer 停止。

### D6 — 候选、审查、验证和恢复

候选 binds commit、original dispatcher_ref/attempt、original implementation baseline、当前 authority/plan/contract digest、validation_plan digest 和所有 effective accepted results。两轴 reviewer 读取当前合同/计划以及原 frozen source，review/convergence/result/Stage-4 handoff 带同一版本。任何正式计划 revision 都使现有候选及审查、验证尝试成为历史；即便实现字节未变，也重新形成候选并执行 affected review_required、两轴审查和完整 final_required，防止审查依据悄变。不能把旧计划验证字符串迁移成 passed。

原 final-integration recovery 仍由 Stage 4 发现 target race/冲突后交还同一 Dispatcher，在 full allocations 下采用 integrated bytes，再以当前 authority 重审重验。规划修订不是 publication recovery；有未知/已发布 merge 时先原 reconcile-publication，禁止在已接受 delivery 原地修订。Stage 4 不改 implementation、函数计划或合同；普通实施记录/术语/使用说明为 closure-owned。

### D7 — 兼容与生成包

生成本地候选包 metadata 版本 1.3.0（source_revision=workflow-pipeline-v1.3.0）；这是仓库候选版本，不授权实际对外发布版本、安装或部署。compatibility key control=11、control JSON schema=6、workflow_progress=workflow-progress-v18、stage_transfer=workflow-stage-transfer-v14、runner outer=10，新增 execution_plan=execution-plan-v1。保持 entry v4、preparation v5、supervision v3、requirement-delivery v2、thread-settings v5、Topic/ledger 格式不变；附着 ledger 内旧 control schema 检查拒绝，外层 ledger 不迁移。五包仍要求完全相等 compatibility key，新代码对旧可执行记录在写入/host calls 前 legacy_run_requires_original_runtime。

计划校验模块经现有资源映射进入五包完整 import closure。生成 Prompt `.md` 和 package README 如改变均属 implementation；README/CONTEXT/ADR 历史说明属 closure。修改维护源和映射后由 Execution Agent 运行现有 builder，生成所有受影响副本及五个 package.json，精确 source→outputs 见 Further Notes。Matt 原生能力不复制、不替代，不新增外部依赖。不安装、部署或修改 active registrations。

### D8 — 变更契约预检与真实生产调用链

已从真实维护源及现有测试核对以下链。主入口是 C JSON handle；它在原 checkpoint lock 下调用 Progress，B 准备/派发后进入纯 control transition，Git adapter 再核验分配和候选；只有宿主 authenticated native receipt 能 bind/write-release/accept。详细源位置与下游文件在 Further Notes，避免把路径当业务名。

| 改变行为 | 现行生产调用链和边界 | 输入/输出/失败与状态 | 决定 |
| --- | --- | --- | --- |
| Stage-2 计划就绪 | solution-designer 原生 Spec/Ticket → C scope_propose/scope_seal → Scope validator → B effective_scope/current_readiness | 原 manifest/candidate/requirement/binding → sealed scope → ready；缺 basis/漂移拒绝，原文档保护 | 新目录在同一 seal；catalog 不授写权限；缺计划不 ready |
| Stage-3 函数派发 | C allocation → B stage_handoff._prepare/render → stage_dispatch.prepared → control plan-execution → Git verify_execution_start → native assign/release | 精确 scope + semantic → allocation digest/ref/release；当前只含行为和 tests，缺函数身份 | 分配与 release 加 refs/current authority，依赖和覆盖先验检查 |
| 计划内部修订 | 原 scope_revise 限 accepted Stage 2；require_scope_correction_available 对任何 saved Stage 3 拒绝 | Stage 2 revision → seal；活动实现无入口；保护整体 bytes | 新 C 有界事务，不复用旧 scope-revise 绕限制；Controller-only 规划窗口 |
| 字节来源 | Git allocation snapshot/execution_delta → execution-result/accept → candidate-ready | 完整 delta、presence/mode、peer 结果；unallocated drift 拒绝 | 未受影响 carry-forward，受影响 adoption；新运行禁 direct |
| 候选和验证 | control candidate-ready → Originating Task 两轴 review → validation-start/result → B receive/accept → Stage 4 | frozen baseline/plan digests/exact candidate；旧结果和 unknown 不 pass | 增加 execution plan/authority binding，修订后不沿用旧 review |
| 前台与附着 | workflow._stage_prompt/_advance_in_host → C next_action；discussion_core.workflow_control → 现有 ledger transaction | outer runner state 与 inner C/control 独立；载体不能伪装 Controller | 原 owner 决策和 plan bytes，carrier 传输；新 schema guard |

持久状态仅原 checkpoint/runner 内 current Scope Authority、control handoff_progress、transactions/history；attached control 仍由现有 ledger port 持久化，Git 持规划字节和实施字节。顺序假设是 persist intent → 原 native stop/lookup 与 calls settlement → prepare snapshot → Controller planning write/commit → Git/contracts/catalog/manifest verify → control activation → same-identity continuation → fresh allocations。暂停取消与 publication reconciliation 优先，任何未知 effect 阻止重放。

已发现并解决的矛盾：Dispatcher glossary/direct 允许编码（D4/D5 收敛 full）；整体 protected Ticket 无修订通路（D2/D3 精确窗口、projection 与整文件更新）；Stage-3 scope-revise 禁止（保持原入口、新合同内事务）；候选仅 check 字节无计划版本（D6）；standalone 不建规划文档（D5 inline）；runner 不拥有用户决策（D5 Controller ingress）。没有以仅模块内单测替代真实生产链。

## Testing Decisions

### T1 — 首个真实 production-boundary TDD 切片

首个 failing test 放在现有 ScopeProgress fixture 的 C `handle`/`Progress.scope_propose` 入口：实际临时 Git Flow、冻结 requirement、已绑定设计者、planning candidate 与完整 manifest 已准备，但 Ticket 缺函数计划时，应返回 execution_plan_incomplete 且 checkpoint/权限不变。加入合法 Ticket 中两个相互依赖函数和共用 behavior verification 后，scope-propose/seal/原 ready publication 链成功，并从 readiness 读回同一 plan digest。现行代码不检查计划，red 是“缺计划却接受”的真实行为；最小 green 通过现有 C→scope validator 调用只读计划解析器，不测试一个未接线 helper。外部 registry/native transport可用已有 fixture doubles，C/B/Git 与被改边界保持真实。

### T2 — 行为验证矩阵

| 验收/分支 | 直接可观察 seam | 级别 |
| --- | --- | --- |
| Stage-2 完整函数计划、成组/共用验证 | C scope proposal/seal/readiness；真实 Ticket/commit bytes；缺 block、重复 IDs/版本、未知 refs/循环/缺 manifest coverage 拒绝 | runtime Git fixture + 文档契约 |
| 原 Ticket 合同保持、保护字节更新 | C prepare/commit/reconcile；修改仅 plan 块成功；修改行为/验收/需求/未获规划路径、scope aliases 拒绝并保留 barrier | runtime end-to-end |
| 新计划和范围原子切换 | 故障注入 journal 前后、planning commit before/after、control transaction result 丢失；inspect/reconcile 后唯一 revision/权限，无第二 commit | runtime 原 checkpoint/Git |
| 内部/用户分支 | original Controller receipt + contract/impact refs；内部无 pending user approval，公共/未知影响先 user-decision 且不能通过 internal commit | runtime + real host |
| 旧计划拒绝、新 refs 成组派发 | B prepare/bind/write_release/result/accept；current refs 才成功，unknown allocation/late response/stale revision 拒绝，无 planner/Dispatcher write authority | runtime 原 C/B/Git/native fixture |
| 保留与复核 | 同 bytes 但 dependencies/verification 改变不能 carry；未变 accepted 保留；受影响 dirty bytes adoption 需要真实 stopped agent result | runtime Git fixture |
| Dispatcher 无代码来源 | new direct start/preparation rejected；未分配 byte drift 阻断；every changed path 须最新有效 delivery；生成输出也由 allocation | runtime + real host |
| 旧审查不能证明新计划 | current candidate/review/convergence/final attempt digest 不同即拒绝；修订即使无 code delta 仍 new review/full validation | runtime candidate lifecycle |
| pause/cancel/unknown/native loss | revision 无 resume 授权；屏障缺 writer/call 持续闭合；真实 same-ref/resumable receipt 才 followup；已 publication unknown 先 reconcile | runtime lifecycle + real host |
| standalone/foreground/attached | inline plan 不建 Ticket；root Controller decision/bytes 不能由 carrier 代写；ledger port 同 control transaction；原 owner/phase identity 保留 | runtime routes + real host |
| 包兼容 | 五包 module import/resource closure、精确 compat、旧 schema/runner 升级拒绝、临时 installer copy 及不改变用户安装 | package regression |

测试验证外部行为与拒绝效果，不每函数机械新增测试。多个 step 可共用同一 runtime scenario。既有 StageScopeProgress、StageTransfer、WorkflowControlGit、ReviewFirstValidation、DispatchRecovery、CollaborationLifecycle 和 PublicationRecovery fixture 是 prior art。

### T3 — checks、宿主与验证声明

开发 focused checks 一律 `python3 scripts/run_tests.py --test-file <exact tests/runtime or tests/packages module>`；review_required 包括新增 execution-plan/revision 场景、相关 C/B/Git/runner/attached/recovery/package checks。final_required 为 `./scripts/validate.sh`（全仓测试 + build/resource/contract 检查）和真实宿主 environment acceptance。遇 runner lock 的 existing run，报告 owner PID/worktree 并结束该次验证，不绕锁。builder 的 `--check` 只读 package comparison；实现时 generated build 是受 exact allocation 管理的写入。

现场验收在专用可丢弃项目和明确获准原生角色中进行，流程：Stage 2 真实 to-spec/to-tickets 输出计划 → full Dispatcher 成组计划 TDD → 私有函数移动/新增 helper 的原 Controller revision → 验收 unchanged accepted 保留/affected 复核、stale release 拒绝 → 公共契约变化提议先用户 gate → pause/unknown-call 恢复 → 新候选两轴 review/full validation → Stage 4 接收 current revision。保留实际工具 requests/responses、计划 commit/hash/revisions、角色 ref/停止证明、Git/index before/after、外部能力入口及 verdict。另覆盖独立 inline、foreground carrier 和 attached ledger 至少一次 controller ingress；缺宿主能力保持未验证并阻止宣称支持该 route。不得通过模拟回执、账户 catalog 或子 Agent prose 声称现场通过。

此次仅规划，未运行实施测试、未取得新版本现场验收证据。Stage 3 开始前必须从 Spec 固定上述 review/final check 命令、cwd、pass_condition、allowed_skips 和 environment fingerprint；现场验收的 pass_condition=accepted，必须原可信 receipt，不能用 worksheet 生成命令的 exit 0 替代。

### T4 — readiness 与 Tickets 追踪

每个改变行为有唯一 owner（Execution Plan 只读验证、C 生命周期/权限事务、control 决策、Git 字节证据、B native envelope、runner transport），接口/I/O/失败、分支优先级、durable states/重试均由 D1–D8 明确。无需新宿主 seam，复用原 JSON C/B/Git 和 native adapter。Ticket 追踪及完整函数计划在 Further Notes；没有留待 Stage 3 的产品/范围/架构选择。

Draft readiness：通过。已核对 8 个 stage-owned 草稿、5 个 Ticket、42 个函数计划（31 existing qualified symbols真实存在，11拟新增）、联合依赖 DAG、verification_refs、136 个 implementation路径（56源/测试+80生成物）、13个closure路径、逐文件basis与source→outputs反向一致性；全部实施源/测试均被Ticket step/artifact覆盖。N4明示basis commit只在真实candidate产生后加入，不把draft当封存。冻结需求SHA-256保持不变、工作树未写实现文件/包/原C状态，未运行实施测试或现场验收。

## Out of Scope

每函数单独 Agent/测试；后台调度、通用计划服务、独立 plan ledger；自动语义判定公共接口安全；其他业务模块或未确认功能；旧运行迁移/新包接管；安装、部署、active registry 修改、PR/远程发布；Stage 4 改代码/合同；改 Matt capabilities 内容。

## Further Notes

### N1 — 规划文件与原生行为

本 Spec 按原生 to-spec 模板完整综合。测试 seam 的用户核对并入唯一 combined review；写入当前分配的本地草稿，原生 ready-for-agent label、完整 Tickets 及阻塞关系 publication 在审阅接受后完成。术语和 ADR 通过已注册 domain-modeling 原生行为；新 ADR 暂 proposed，CONTEXT 术语更新归 Stage 4 exact closure scope，Stage 2 无该文件写权限。

### N2 — 生产证据

- `src/shared/scripts/workflow_progress.py`：handle、Progress.scope_propose/scope_seal/scope_revise、control_action、advance/continuation_gate、observe/decide、resume_control，原 checkpoint/stop/call/replay owner。
- `src/shared/scripts/stage_scope.py`：validate_manifest/validate_scope/manifest_projection/manifest_diff/validate_snapshot；basis Git blob、protected、source_outputs 同时检查。
- `src/shared/scripts/stage_handoff.py`：_prepare、effective_scope/current_readiness、planning_paths_for/inherited_planning_candidate、_render_verified；真实 B source/scope/role/compat admission。
- `src/shared/scripts/stage_dispatch.py`：checkpoint/prepared、record_for/matching、execution_release_token/received；start-dispatch/plan-execution/assign/result acceptance 真正 wire。
- `src/shared/scripts/workflow_control.py`：transition 的 start-dispatch/plan-execution/assign/execution-result/accept-execution/candidate-ready/review/validation、implementation_policy/adoption_source/delivery_verification；唯一 control decision projection。
- `src/shared/scripts/workflow_control_git.py`：verified_transition、verify_execution_start、execution_delta/recovery_snapshot/scope_revision_snapshot；真实全 delta/index/presence/mode attribution。
- `src/shared/scripts/discussion_core/workflow_control.py`：workflow_control，经原 ledger 的 _write_ledger_transaction 保存 control；不新建 ledger。
- `src/stages/guided-implementation/scripts/workflow.py`：_stage_prompt、_stage_projection、_advance_in_host、validate_confirmed/_validate_record，outer carrier/host 单一管理。
- `scripts/build_skills.py`：build 读取 `build/skill-packages.json` resource closure，生成每包 files/digest 与 metadata；无手改生成文件。

### N3 — Ticket 拆分、函数计划与验收映射

原生 ask-matt 按“multi-session build”分支选择 Tickets：Stage-2 admission、Controller/Git 事务、原生派发、候选恢复、五包/宿主验收分别可闭合验证，适合 fresh context。原生 to-tickets 按 tracer-bullet 组织以下五项；协议 schema 的 mechanical fan-out 使用同一 integration branch，最终整仓/五包绿色由 05 integrate-and-verify 收敛，不发布中间包。

| Ticket | Blocked by | 完整交付行为 | Spec / 冻结需求验收 / seam |
| --- | --- | --- | --- |
| 01-stage2-function-plans | None | C proposal→seal→readiness 拒绝缺函数计划，读回完整目录 | D1/D2/D8；验收1/7；T1/T2 Stage-2 |
| 02-controller-plan-revision | 01 | 原 Controller 全停止→规划 commit→单一权威切换/重放恢复 | D2/D3；验收3/4/5/6；T2 合同/原子事务 |
| 03-plan-driven-dispatch | 01,02 | 有效 refs 成组 native bind/release/result/accept，新 Dispatcher 不编码 | D1/D4/D5；验收2/3/4/5；T2 派发/入口 |
| 04-review-and-recovery | 02,03 | carry-forward/adoption→current candidate→两轴审查/完整验证与恢复 | D3/D4/D6；验收5/6；T2 结果/候选/恢复 |
| 05-compatibility-and-packages | 01,02,03,04 | 新五包及所有路由真实宿主验收，旧运行拒绝迁移 | D5/D6/D7；验收6/7；T3 package/environment |

Blocking edges 是真实能力前置，非任意任务序号；实现文件重叠进一步要求串行。Native quiz（粒度是否合理、依赖是否正确、是否合并拆分）与 to-spec 测试 seam 核对一起交由本次唯一 combined review，用户已以“确认，连续模式”接受完整方案、粒度、依赖和测试 seam。全部 Ticket ready-for-agent、Blocked by 和 Comments 草稿已准备；native publication 延迟到该审阅决定。

代表性例子位于 `execution-plan-example.md`；五个 Ticket 自身也含全部 42 项具体函数能力/过程/依赖/I/O/失败/共用行为验证。existing targets 已用维护源 AST qualified names 核对为真实符号，add 为拟新增；依赖 DAG、Ticket blockers、verifications、paths coverage 使用草稿结构核对。没有每函数单派 Agent 或强制单测。

### N4 — 下游逐文件清单与 source→outputs

全部路径相对于仓库，按每条用途判定 owner/kind；没有目录/glob。operation 相对于 immutable scope baseline（冻结提交），所以 Stage-2 新建而 Stage-4 继续更新的规划文件仍为 add。draft_inventory 的 basis path/section 已确定，实际规划 candidate 存在后 completion 为每条 basis 加同一个真实 commit；此草稿 inventory 不可直接当 C 的 sealed manifest 或写权限。

implementation_required: true

实施文件：136（维护源/测试 56；生成物 80）。归档文件：13。

closure: CONTEXT 补计划/修订术语和角色；README/CHANGELOG 说明1.3.0行为/版本；ADR0007/0011保留历史并链接新决定；ADR0013记录接受/实施证据；Spec、样例和五Ticket只追加当前实施/验证/Lifecycle记录，不改合同与运行源。

```draft-inventory-v1
[
  {
    "path": ".scratch/function-execution-plans/PRD.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": ".scratch/function-execution-plans/execution-plan-example.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": ".scratch/function-execution-plans/issues/01-stage2-function-plans.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": ".scratch/function-execution-plans/issues/02-controller-plan-revision.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": ".scratch/function-execution-plans/issues/03-plan-driven-dispatch.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": ".scratch/function-execution-plans/issues/04-review-and-recovery.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": ".scratch/function-execution-plans/issues/05-compatibility-and-packages.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "CHANGELOG.md",
    "owner": "closure",
    "kind": "document",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "CONTEXT.md",
    "owner": "closure",
    "kind": "document",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "README.md",
    "owner": "closure",
    "kind": "document",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "build/skill-packages.json",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "docs/adr/0007-separate-workflow-controller-from-stage-carriers.md",
    "owner": "closure",
    "kind": "document",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "docs/adr/0011-finalize-stage-scope-after-design.md",
    "owner": "closure",
    "kind": "document",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "docs/adr/0013-revise-function-plans-within-contract.md",
    "owner": "closure",
    "kind": "document",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "scripts/field_acceptance.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "scripts/validate_repository.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "skills/change-closure/package.json",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "build/skill-packages.json",
      "scripts/build_skills.py",
      "src/shared/LICENSE",
      "src/shared/references/design-discussion/confirmation-contract.md",
      "src/shared/references/design-discussion/ledger-protocol.md",
      "src/shared/references/design-discussion/lifecycle-integration.md",
      "src/shared/references/entry-preparation.md",
      "src/shared/references/guided-implementation/workflow-control-protocol.md",
      "src/shared/references/guided-implementation/worktree-execution.md",
      "src/shared/references/package-execution.md",
      "src/shared/references/requirement-delivery.md",
      "src/shared/references/requirement-preparation.md",
      "src/shared/references/stage-transfer.md",
      "src/shared/references/workflow-progression.md",
      "src/shared/scripts/discussion_core/__init__.py",
      "src/shared/scripts/discussion_core/checkpoint_authority.py",
      "src/shared/scripts/discussion_core/checkpoints.py",
      "src/shared/scripts/discussion_core/handoffs.py",
      "src/shared/scripts/discussion_core/phase_runs.py",
      "src/shared/scripts/discussion_core/question_resolution.py",
      "src/shared/scripts/discussion_core/registry.py",
      "src/shared/scripts/discussion_core/request.py",
      "src/shared/scripts/discussion_core/state.py",
      "src/shared/scripts/discussion_core/topic_dependencies.py",
      "src/shared/scripts/discussion_core/topic_dependency_authority.py",
      "src/shared/scripts/discussion_core/topic_dependency_gates.py",
      "src/shared/scripts/discussion_core/topic_dependency_lifecycle.py",
      "src/shared/scripts/discussion_core/topic_dependency_schema.py",
      "src/shared/scripts/discussion_core/workflow_control.py",
      "src/shared/scripts/discussion_protocol.py",
      "src/shared/scripts/entry_prepare.py",
      "src/shared/scripts/execution_plan.py",
      "src/shared/scripts/host_skill_registry.py",
      "src/shared/scripts/model_inventory.py",
      "src/shared/scripts/requirement_delivery.py",
      "src/shared/scripts/requirement_prepare.py",
      "src/shared/scripts/skill_preflight.py",
      "src/shared/scripts/stage_dispatch.py",
      "src/shared/scripts/stage_handoff.py",
      "src/shared/scripts/stage_scope.py",
      "src/shared/scripts/supervision_core/__init__.py",
      "src/shared/scripts/supervision_core/commands.py",
      "src/shared/scripts/supervision_protocol.py",
      "src/shared/scripts/thread_settings.py",
      "src/shared/scripts/workflow_control.py",
      "src/shared/scripts/workflow_control_git.py",
      "src/shared/scripts/workflow_progress.py",
      "src/stages/change-closure/SKILL.md.in",
      "src/stages/change-closure/agents/openai.yaml",
      "src/stages/change-closure/references/closure-actions.md",
      "src/stages/change-closure/references/closure-protocol.md",
      "src/stages/solution-design/references/conversation-source.md"
    ]
  },
  {
    "path": "skills/change-closure/references/change-closure/closure-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/change-closure/references/closure-protocol.md"
    ]
  },
  {
    "path": "skills/change-closure/references/shared/entry-preparation.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/entry-preparation.md"
    ]
  },
  {
    "path": "skills/change-closure/references/shared/guided-implementation/workflow-control-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/workflow-control-protocol.md"
    ]
  },
  {
    "path": "skills/change-closure/references/shared/guided-implementation/worktree-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/worktree-execution.md"
    ]
  },
  {
    "path": "skills/change-closure/references/shared/package-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/package-execution.md"
    ]
  },
  {
    "path": "skills/change-closure/references/shared/stage-transfer.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/stage-transfer.md"
    ]
  },
  {
    "path": "skills/change-closure/references/shared/workflow-progression.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/workflow-progression.md"
    ]
  },
  {
    "path": "skills/change-closure/scripts/discussion_core/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/discussion_core/workflow_control.py"
    ]
  },
  {
    "path": "skills/change-closure/scripts/execution_plan.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/execution_plan.py"
    ]
  },
  {
    "path": "skills/change-closure/scripts/stage_dispatch.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_dispatch.py"
    ]
  },
  {
    "path": "skills/change-closure/scripts/stage_handoff.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_handoff.py"
    ]
  },
  {
    "path": "skills/change-closure/scripts/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control.py"
    ]
  },
  {
    "path": "skills/change-closure/scripts/workflow_control_git.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control_git.py"
    ]
  },
  {
    "path": "skills/change-closure/scripts/workflow_progress.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_progress.py"
    ]
  },
  {
    "path": "skills/design-discussion/package.json",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "build/skill-packages.json",
      "scripts/build_skills.py",
      "src/shared/LICENSE",
      "src/shared/references/design-discussion/checkpoint-protocol.md",
      "src/shared/references/design-discussion/child-topic-protocol.md",
      "src/shared/references/design-discussion/confirmation-contract.md",
      "src/shared/references/design-discussion/ledger-protocol.md",
      "src/shared/references/design-discussion/lifecycle-integration.md",
      "src/shared/references/design-discussion/requirement-document-contract.md",
      "src/shared/references/design-discussion/split-gate-contract.md",
      "src/shared/references/design-discussion/templates.md",
      "src/shared/references/design-discussion/topic-document-protocol.md",
      "src/shared/references/entry-preparation.md",
      "src/shared/references/guided-implementation/thread-settings-protocol.md",
      "src/shared/references/guided-implementation/workflow-control-protocol.md",
      "src/shared/references/package-execution.md",
      "src/shared/references/requirement-delivery.md",
      "src/shared/references/requirement-preparation.md",
      "src/shared/references/stage-transfer.md",
      "src/shared/references/workflow-progression.md",
      "src/shared/scripts/discussion_core/__init__.py",
      "src/shared/scripts/discussion_core/checkpoint_authority.py",
      "src/shared/scripts/discussion_core/checkpoints.py",
      "src/shared/scripts/discussion_core/handoffs.py",
      "src/shared/scripts/discussion_core/phase_runs.py",
      "src/shared/scripts/discussion_core/question_resolution.py",
      "src/shared/scripts/discussion_core/registry.py",
      "src/shared/scripts/discussion_core/request.py",
      "src/shared/scripts/discussion_core/state.py",
      "src/shared/scripts/discussion_core/topic_dependencies.py",
      "src/shared/scripts/discussion_core/topic_dependency_authority.py",
      "src/shared/scripts/discussion_core/topic_dependency_gates.py",
      "src/shared/scripts/discussion_core/topic_dependency_lifecycle.py",
      "src/shared/scripts/discussion_core/topic_dependency_schema.py",
      "src/shared/scripts/discussion_core/workflow_control.py",
      "src/shared/scripts/discussion_protocol.py",
      "src/shared/scripts/entry_prepare.py",
      "src/shared/scripts/execution_plan.py",
      "src/shared/scripts/host_skill_registry.py",
      "src/shared/scripts/model_inventory.py",
      "src/shared/scripts/requirement_delivery.py",
      "src/shared/scripts/requirement_prepare.py",
      "src/shared/scripts/skill_preflight.py",
      "src/shared/scripts/stage_dispatch.py",
      "src/shared/scripts/stage_handoff.py",
      "src/shared/scripts/stage_scope.py",
      "src/shared/scripts/supervision_core/__init__.py",
      "src/shared/scripts/supervision_core/commands.py",
      "src/shared/scripts/supervision_protocol.py",
      "src/shared/scripts/thread_settings.py",
      "src/shared/scripts/workflow_control.py",
      "src/shared/scripts/workflow_control_git.py",
      "src/shared/scripts/workflow_progress.py",
      "src/stages/design-discussion/SKILL.md.in",
      "src/stages/design-discussion/agents/openai.yaml",
      "src/stages/problem-framing/references/dedicated-grilling-protocol.md",
      "src/stages/problem-framing/references/templates.md",
      "src/stages/solution-design/references/conversation-source.md"
    ]
  },
  {
    "path": "skills/design-discussion/references/problem-framing/templates.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/problem-framing/references/templates.md"
    ]
  },
  {
    "path": "skills/design-discussion/references/shared/entry-preparation.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/entry-preparation.md"
    ]
  },
  {
    "path": "skills/design-discussion/references/shared/guided-implementation/workflow-control-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/workflow-control-protocol.md"
    ]
  },
  {
    "path": "skills/design-discussion/references/shared/package-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/package-execution.md"
    ]
  },
  {
    "path": "skills/design-discussion/references/shared/stage-transfer.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/stage-transfer.md"
    ]
  },
  {
    "path": "skills/design-discussion/references/shared/workflow-progression.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/workflow-progression.md"
    ]
  },
  {
    "path": "skills/design-discussion/scripts/discussion_core/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/discussion_core/workflow_control.py"
    ]
  },
  {
    "path": "skills/design-discussion/scripts/execution_plan.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/execution_plan.py"
    ]
  },
  {
    "path": "skills/design-discussion/scripts/stage_dispatch.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_dispatch.py"
    ]
  },
  {
    "path": "skills/design-discussion/scripts/stage_handoff.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_handoff.py"
    ]
  },
  {
    "path": "skills/design-discussion/scripts/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control.py"
    ]
  },
  {
    "path": "skills/design-discussion/scripts/workflow_control_git.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control_git.py"
    ]
  },
  {
    "path": "skills/design-discussion/scripts/workflow_progress.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_progress.py"
    ]
  },
  {
    "path": "skills/guided-implementation/SKILL.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/guided-implementation/SKILL.md.in"
    ]
  },
  {
    "path": "skills/guided-implementation/package.json",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "build/skill-packages.json",
      "scripts/build_skills.py",
      "src/shared/LICENSE",
      "src/shared/references/design-discussion/confirmation-contract.md",
      "src/shared/references/design-discussion/ledger-protocol.md",
      "src/shared/references/design-discussion/lifecycle-integration.md",
      "src/shared/references/entry-preparation.md",
      "src/shared/references/guided-implementation/execution-protocol.md",
      "src/shared/references/guided-implementation/originating-task-protocol.md",
      "src/shared/references/guided-implementation/thread-settings-protocol.md",
      "src/shared/references/guided-implementation/workflow-control-protocol.md",
      "src/shared/references/guided-implementation/worktree-execution.md",
      "src/shared/references/package-execution.md",
      "src/shared/references/requirement-delivery.md",
      "src/shared/references/requirement-preparation.md",
      "src/shared/references/stage-transfer.md",
      "src/shared/references/workflow-progression.md",
      "src/shared/scripts/discussion_core/__init__.py",
      "src/shared/scripts/discussion_core/checkpoint_authority.py",
      "src/shared/scripts/discussion_core/checkpoints.py",
      "src/shared/scripts/discussion_core/handoffs.py",
      "src/shared/scripts/discussion_core/phase_runs.py",
      "src/shared/scripts/discussion_core/question_resolution.py",
      "src/shared/scripts/discussion_core/registry.py",
      "src/shared/scripts/discussion_core/request.py",
      "src/shared/scripts/discussion_core/state.py",
      "src/shared/scripts/discussion_core/topic_dependencies.py",
      "src/shared/scripts/discussion_core/topic_dependency_authority.py",
      "src/shared/scripts/discussion_core/topic_dependency_gates.py",
      "src/shared/scripts/discussion_core/topic_dependency_lifecycle.py",
      "src/shared/scripts/discussion_core/topic_dependency_schema.py",
      "src/shared/scripts/discussion_core/workflow_control.py",
      "src/shared/scripts/discussion_protocol.py",
      "src/shared/scripts/entry_prepare.py",
      "src/shared/scripts/execution_plan.py",
      "src/shared/scripts/host_skill_registry.py",
      "src/shared/scripts/model_inventory.py",
      "src/shared/scripts/requirement_delivery.py",
      "src/shared/scripts/requirement_prepare.py",
      "src/shared/scripts/skill_preflight.py",
      "src/shared/scripts/stage_dispatch.py",
      "src/shared/scripts/stage_handoff.py",
      "src/shared/scripts/stage_scope.py",
      "src/shared/scripts/supervision_core/__init__.py",
      "src/shared/scripts/supervision_core/commands.py",
      "src/shared/scripts/supervision_protocol.py",
      "src/shared/scripts/thread_settings.py",
      "src/shared/scripts/workflow_control.py",
      "src/shared/scripts/workflow_control_git.py",
      "src/shared/scripts/workflow_progress.py",
      "src/stages/guided-implementation/SKILL.md.in",
      "src/stages/guided-implementation/agents/openai.yaml",
      "src/stages/guided-implementation/scripts/foreground_host.py",
      "src/stages/guided-implementation/scripts/workflow.py",
      "src/stages/guided-implementation/scripts/workflow_stage_result.schema.json",
      "src/stages/solution-design/references/conversation-source.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/entry-preparation.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/entry-preparation.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/guided-implementation/execution-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/execution-protocol.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/guided-implementation/originating-task-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/originating-task-protocol.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/guided-implementation/workflow-control-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/workflow-control-protocol.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/guided-implementation/worktree-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/worktree-execution.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/package-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/package-execution.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/stage-transfer.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/stage-transfer.md"
    ]
  },
  {
    "path": "skills/guided-implementation/references/shared/workflow-progression.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/workflow-progression.md"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/discussion_core/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/discussion_core/workflow_control.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/execution_plan.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/execution_plan.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/stage_dispatch.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_dispatch.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/stage_handoff.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_handoff.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/workflow.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/guided-implementation/scripts/workflow.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/workflow_control_git.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control_git.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/workflow_progress.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_progress.py"
    ]
  },
  {
    "path": "skills/guided-implementation/scripts/workflow_stage_result.schema.json",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/guided-implementation/scripts/workflow_stage_result.schema.json"
    ]
  },
  {
    "path": "skills/problem-framing/package.json",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "build/skill-packages.json",
      "scripts/build_skills.py",
      "src/shared/LICENSE",
      "src/shared/references/design-discussion/confirmation-contract.md",
      "src/shared/references/design-discussion/ledger-protocol.md",
      "src/shared/references/design-discussion/lifecycle-integration.md",
      "src/shared/references/design-discussion/requirement-document-contract.md",
      "src/shared/references/design-discussion/split-gate-contract.md",
      "src/shared/references/entry-preparation.md",
      "src/shared/references/guided-implementation/thread-settings-protocol.md",
      "src/shared/references/guided-implementation/workflow-control-protocol.md",
      "src/shared/references/package-execution.md",
      "src/shared/references/requirement-delivery.md",
      "src/shared/references/requirement-preparation.md",
      "src/shared/references/stage-transfer.md",
      "src/shared/references/workflow-progression.md",
      "src/shared/scripts/discussion_core/__init__.py",
      "src/shared/scripts/discussion_core/checkpoint_authority.py",
      "src/shared/scripts/discussion_core/checkpoints.py",
      "src/shared/scripts/discussion_core/handoffs.py",
      "src/shared/scripts/discussion_core/phase_runs.py",
      "src/shared/scripts/discussion_core/question_resolution.py",
      "src/shared/scripts/discussion_core/registry.py",
      "src/shared/scripts/discussion_core/request.py",
      "src/shared/scripts/discussion_core/state.py",
      "src/shared/scripts/discussion_core/topic_dependencies.py",
      "src/shared/scripts/discussion_core/topic_dependency_authority.py",
      "src/shared/scripts/discussion_core/topic_dependency_gates.py",
      "src/shared/scripts/discussion_core/topic_dependency_lifecycle.py",
      "src/shared/scripts/discussion_core/topic_dependency_schema.py",
      "src/shared/scripts/discussion_core/workflow_control.py",
      "src/shared/scripts/discussion_protocol.py",
      "src/shared/scripts/entry_prepare.py",
      "src/shared/scripts/execution_plan.py",
      "src/shared/scripts/host_skill_registry.py",
      "src/shared/scripts/model_inventory.py",
      "src/shared/scripts/requirement_delivery.py",
      "src/shared/scripts/requirement_prepare.py",
      "src/shared/scripts/skill_preflight.py",
      "src/shared/scripts/stage_dispatch.py",
      "src/shared/scripts/stage_handoff.py",
      "src/shared/scripts/stage_scope.py",
      "src/shared/scripts/supervision_core/__init__.py",
      "src/shared/scripts/supervision_core/commands.py",
      "src/shared/scripts/supervision_protocol.py",
      "src/shared/scripts/thread_settings.py",
      "src/shared/scripts/workflow_control.py",
      "src/shared/scripts/workflow_control_git.py",
      "src/shared/scripts/workflow_progress.py",
      "src/stages/problem-framing/SKILL.md.in",
      "src/stages/problem-framing/agents/openai.yaml",
      "src/stages/problem-framing/references/dedicated-grilling-protocol.md",
      "src/stages/problem-framing/references/repository-and-recovery.md",
      "src/stages/problem-framing/references/templates.md",
      "src/stages/solution-design/references/conversation-source.md"
    ]
  },
  {
    "path": "skills/problem-framing/references/problem-framing/templates.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/problem-framing/references/templates.md"
    ]
  },
  {
    "path": "skills/problem-framing/references/shared/entry-preparation.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/entry-preparation.md"
    ]
  },
  {
    "path": "skills/problem-framing/references/shared/guided-implementation/workflow-control-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/workflow-control-protocol.md"
    ]
  },
  {
    "path": "skills/problem-framing/references/shared/package-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/package-execution.md"
    ]
  },
  {
    "path": "skills/problem-framing/references/shared/stage-transfer.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/stage-transfer.md"
    ]
  },
  {
    "path": "skills/problem-framing/references/shared/workflow-progression.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/workflow-progression.md"
    ]
  },
  {
    "path": "skills/problem-framing/scripts/discussion_core/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/discussion_core/workflow_control.py"
    ]
  },
  {
    "path": "skills/problem-framing/scripts/execution_plan.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/execution_plan.py"
    ]
  },
  {
    "path": "skills/problem-framing/scripts/stage_dispatch.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_dispatch.py"
    ]
  },
  {
    "path": "skills/problem-framing/scripts/stage_handoff.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_handoff.py"
    ]
  },
  {
    "path": "skills/problem-framing/scripts/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control.py"
    ]
  },
  {
    "path": "skills/problem-framing/scripts/workflow_control_git.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control_git.py"
    ]
  },
  {
    "path": "skills/problem-framing/scripts/workflow_progress.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_progress.py"
    ]
  },
  {
    "path": "skills/solution-design/SKILL.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/solution-design/SKILL.md.in"
    ]
  },
  {
    "path": "skills/solution-design/package.json",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "build/skill-packages.json",
      "scripts/build_skills.py",
      "src/shared/LICENSE",
      "src/shared/references/design-discussion/confirmation-contract.md",
      "src/shared/references/design-discussion/requirement-document-contract.md",
      "src/shared/references/entry-preparation.md",
      "src/shared/references/guided-implementation/thread-settings-protocol.md",
      "src/shared/references/guided-implementation/workflow-control-protocol.md",
      "src/shared/references/guided-implementation/worktree-execution.md",
      "src/shared/references/package-execution.md",
      "src/shared/references/requirement-delivery.md",
      "src/shared/references/requirement-preparation.md",
      "src/shared/references/stage-transfer.md",
      "src/shared/references/workflow-progression.md",
      "src/shared/scripts/discussion_core/__init__.py",
      "src/shared/scripts/discussion_core/checkpoint_authority.py",
      "src/shared/scripts/discussion_core/checkpoints.py",
      "src/shared/scripts/discussion_core/handoffs.py",
      "src/shared/scripts/discussion_core/phase_runs.py",
      "src/shared/scripts/discussion_core/question_resolution.py",
      "src/shared/scripts/discussion_core/registry.py",
      "src/shared/scripts/discussion_core/request.py",
      "src/shared/scripts/discussion_core/state.py",
      "src/shared/scripts/discussion_core/topic_dependencies.py",
      "src/shared/scripts/discussion_core/topic_dependency_authority.py",
      "src/shared/scripts/discussion_core/topic_dependency_gates.py",
      "src/shared/scripts/discussion_core/topic_dependency_lifecycle.py",
      "src/shared/scripts/discussion_core/topic_dependency_schema.py",
      "src/shared/scripts/discussion_core/workflow_control.py",
      "src/shared/scripts/discussion_protocol.py",
      "src/shared/scripts/entry_prepare.py",
      "src/shared/scripts/execution_plan.py",
      "src/shared/scripts/host_skill_registry.py",
      "src/shared/scripts/model_inventory.py",
      "src/shared/scripts/requirement_delivery.py",
      "src/shared/scripts/requirement_prepare.py",
      "src/shared/scripts/skill_preflight.py",
      "src/shared/scripts/stage_dispatch.py",
      "src/shared/scripts/stage_handoff.py",
      "src/shared/scripts/stage_scope.py",
      "src/shared/scripts/supervision_core/__init__.py",
      "src/shared/scripts/supervision_core/commands.py",
      "src/shared/scripts/supervision_protocol.py",
      "src/shared/scripts/thread_settings.py",
      "src/shared/scripts/workflow_control.py",
      "src/shared/scripts/workflow_control_git.py",
      "src/shared/scripts/workflow_progress.py",
      "src/stages/solution-design/SKILL.md.in",
      "src/stages/solution-design/agents/openai.yaml",
      "src/stages/solution-design/references/conversation-source.md",
      "src/stages/solution-design/references/design-readiness.md",
      "src/stages/solution-design/references/subagent-protocol.md",
      "src/stages/solution-design/references/templates.md"
    ]
  },
  {
    "path": "skills/solution-design/references/shared/entry-preparation.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/entry-preparation.md"
    ]
  },
  {
    "path": "skills/solution-design/references/shared/guided-implementation/workflow-control-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/workflow-control-protocol.md"
    ]
  },
  {
    "path": "skills/solution-design/references/shared/guided-implementation/worktree-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/guided-implementation/worktree-execution.md"
    ]
  },
  {
    "path": "skills/solution-design/references/shared/package-execution.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/package-execution.md"
    ]
  },
  {
    "path": "skills/solution-design/references/shared/stage-transfer.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/stage-transfer.md"
    ]
  },
  {
    "path": "skills/solution-design/references/shared/workflow-progression.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/references/workflow-progression.md"
    ]
  },
  {
    "path": "skills/solution-design/references/solution-design/design-readiness.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/solution-design/references/design-readiness.md"
    ]
  },
  {
    "path": "skills/solution-design/references/solution-design/subagent-protocol.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/solution-design/references/subagent-protocol.md"
    ]
  },
  {
    "path": "skills/solution-design/references/solution-design/templates.md",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/stages/solution-design/references/templates.md"
    ]
  },
  {
    "path": "skills/solution-design/scripts/discussion_core/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/discussion_core/workflow_control.py"
    ]
  },
  {
    "path": "skills/solution-design/scripts/execution_plan.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/execution_plan.py"
    ]
  },
  {
    "path": "skills/solution-design/scripts/stage_dispatch.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_dispatch.py"
    ]
  },
  {
    "path": "skills/solution-design/scripts/stage_handoff.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/stage_handoff.py"
    ]
  },
  {
    "path": "skills/solution-design/scripts/workflow_control.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control.py"
    ]
  },
  {
    "path": "skills/solution-design/scripts/workflow_control_git.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_control_git.py"
    ]
  },
  {
    "path": "skills/solution-design/scripts/workflow_progress.py",
    "owner": "implementation",
    "kind": "generated",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    },
    "source_paths": [
      "src/shared/scripts/workflow_progress.py"
    ]
  },
  {
    "path": "src/shared/references/entry-preparation.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "src/shared/references/guided-implementation/execution-protocol.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "src/shared/references/guided-implementation/originating-task-protocol.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "src/shared/references/guided-implementation/workflow-control-protocol.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "src/shared/references/guided-implementation/worktree-execution.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "src/shared/references/package-execution.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "src/shared/references/stage-transfer.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "src/shared/references/workflow-progression.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "src/shared/scripts/discussion_core/workflow_control.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "src/shared/scripts/execution_plan.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "src/shared/scripts/stage_dispatch.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D4"
    }
  },
  {
    "path": "src/shared/scripts/stage_handoff.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "src/shared/scripts/workflow_control.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "src/shared/scripts/workflow_control_git.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "src/shared/scripts/workflow_progress.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "src/stages/change-closure/references/closure-protocol.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "src/stages/guided-implementation/SKILL.md.in",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "src/stages/guided-implementation/scripts/workflow.py",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D4"
    }
  },
  {
    "path": "src/stages/guided-implementation/scripts/workflow_stage_result.schema.json",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "src/stages/problem-framing/references/templates.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "src/stages/solution-design/SKILL.md.in",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "src/stages/solution-design/references/design-readiness.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "src/stages/solution-design/references/subagent-protocol.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "src/stages/solution-design/references/templates.md",
    "owner": "implementation",
    "kind": "source",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "tests/packages/test_build.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/packages/test_cross_package.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/packages/test_preflight.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_attached_design_recovery.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "tests/runtime/test_attached_flow_progress.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "tests/runtime/test_business_recovery.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "tests/runtime/test_design_recovery.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "tests/runtime/test_discussion_protocol.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_dispatch_recovery.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "tests/runtime/test_execution_plans.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "tests/runtime/test_plan_driven_dispatch.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "tests/runtime/test_plan_revision_progress.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "add",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "tests/runtime/test_repository_validation.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_requirement_delivery.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_review_first_validation.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "tests/runtime/test_scope_seal_control.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "tests/runtime/test_solution_design_contract.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_stage_scope.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_stage_scope_progress.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "tests/runtime/test_stage_scope_runner.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_stage_scope_transfer.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D1"
    }
  },
  {
    "path": "tests/runtime/test_stage_start_verification.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "tests/runtime/test_stage_transfer.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "tests/runtime/test_topic_dependency_checkpoint.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D7"
    }
  },
  {
    "path": "tests/runtime/test_unified_recovery.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D6"
    }
  },
  {
    "path": "tests/runtime/test_workflow.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  },
  {
    "path": "tests/runtime/test_workflow_control.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "tests/runtime/test_workflow_control_git.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D3"
    }
  },
  {
    "path": "tests/runtime/test_workflow_progress.py",
    "owner": "implementation",
    "kind": "test",
    "operation": "modify",
    "basis": {
      "path": ".scratch/function-execution-plans/PRD.md",
      "section": "D5"
    }
  }
]
```

生成映射按 builder resource manifest 实際闭包生成，新增 execution_plan 资源在五包 roots显式声明。每个普通generated资源列唯一直接维护源；package.json列 build mapping、builder和该包全部实际resource source（含不变输入），因为内容哈希表/digest依赖整个闭包。source→outputs 显式枚举这次计划拥有的所有受影响output；未修改resource的自身副本不需写入，仍参与metadata输入。generated item source_paths与下面反向map严格一致；包含每个owned source的全部受影响generated output。

```generated-outputs-v1
{
  "build/skill-packages.json": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "scripts/build_skills.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/LICENSE": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/references/design-discussion/checkpoint-protocol.md": [
    "skills/design-discussion/package.json"
  ],
  "src/shared/references/design-discussion/child-topic-protocol.md": [
    "skills/design-discussion/package.json"
  ],
  "src/shared/references/design-discussion/confirmation-contract.md": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/references/design-discussion/ledger-protocol.md": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json"
  ],
  "src/shared/references/design-discussion/lifecycle-integration.md": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json"
  ],
  "src/shared/references/design-discussion/requirement-document-contract.md": [
    "skills/design-discussion/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/references/design-discussion/split-gate-contract.md": [
    "skills/design-discussion/package.json",
    "skills/problem-framing/package.json"
  ],
  "src/shared/references/design-discussion/templates.md": [
    "skills/design-discussion/package.json"
  ],
  "src/shared/references/design-discussion/topic-document-protocol.md": [
    "skills/design-discussion/package.json"
  ],
  "src/shared/references/entry-preparation.md": [
    "skills/change-closure/package.json",
    "skills/change-closure/references/shared/entry-preparation.md",
    "skills/design-discussion/package.json",
    "skills/design-discussion/references/shared/entry-preparation.md",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/entry-preparation.md",
    "skills/problem-framing/package.json",
    "skills/problem-framing/references/shared/entry-preparation.md",
    "skills/solution-design/package.json",
    "skills/solution-design/references/shared/entry-preparation.md"
  ],
  "src/shared/references/guided-implementation/execution-protocol.md": [
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/guided-implementation/execution-protocol.md"
  ],
  "src/shared/references/guided-implementation/originating-task-protocol.md": [
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/guided-implementation/originating-task-protocol.md"
  ],
  "src/shared/references/guided-implementation/thread-settings-protocol.md": [
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/references/guided-implementation/workflow-control-protocol.md": [
    "skills/change-closure/package.json",
    "skills/change-closure/references/shared/guided-implementation/workflow-control-protocol.md",
    "skills/design-discussion/package.json",
    "skills/design-discussion/references/shared/guided-implementation/workflow-control-protocol.md",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/guided-implementation/workflow-control-protocol.md",
    "skills/problem-framing/package.json",
    "skills/problem-framing/references/shared/guided-implementation/workflow-control-protocol.md",
    "skills/solution-design/package.json",
    "skills/solution-design/references/shared/guided-implementation/workflow-control-protocol.md"
  ],
  "src/shared/references/guided-implementation/worktree-execution.md": [
    "skills/change-closure/package.json",
    "skills/change-closure/references/shared/guided-implementation/worktree-execution.md",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/guided-implementation/worktree-execution.md",
    "skills/solution-design/package.json",
    "skills/solution-design/references/shared/guided-implementation/worktree-execution.md"
  ],
  "src/shared/references/package-execution.md": [
    "skills/change-closure/package.json",
    "skills/change-closure/references/shared/package-execution.md",
    "skills/design-discussion/package.json",
    "skills/design-discussion/references/shared/package-execution.md",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/package-execution.md",
    "skills/problem-framing/package.json",
    "skills/problem-framing/references/shared/package-execution.md",
    "skills/solution-design/package.json",
    "skills/solution-design/references/shared/package-execution.md"
  ],
  "src/shared/references/requirement-delivery.md": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/references/requirement-preparation.md": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/references/stage-transfer.md": [
    "skills/change-closure/package.json",
    "skills/change-closure/references/shared/stage-transfer.md",
    "skills/design-discussion/package.json",
    "skills/design-discussion/references/shared/stage-transfer.md",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/stage-transfer.md",
    "skills/problem-framing/package.json",
    "skills/problem-framing/references/shared/stage-transfer.md",
    "skills/solution-design/package.json",
    "skills/solution-design/references/shared/stage-transfer.md"
  ],
  "src/shared/references/workflow-progression.md": [
    "skills/change-closure/package.json",
    "skills/change-closure/references/shared/workflow-progression.md",
    "skills/design-discussion/package.json",
    "skills/design-discussion/references/shared/workflow-progression.md",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/references/shared/workflow-progression.md",
    "skills/problem-framing/package.json",
    "skills/problem-framing/references/shared/workflow-progression.md",
    "skills/solution-design/package.json",
    "skills/solution-design/references/shared/workflow-progression.md"
  ],
  "src/shared/scripts/discussion_core/__init__.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/checkpoint_authority.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/checkpoints.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/handoffs.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/phase_runs.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/question_resolution.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/registry.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/request.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/state.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/topic_dependencies.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/topic_dependency_authority.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/topic_dependency_gates.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/topic_dependency_lifecycle.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/topic_dependency_schema.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/discussion_core/workflow_control.py": [
    "skills/change-closure/package.json",
    "skills/change-closure/scripts/discussion_core/workflow_control.py",
    "skills/design-discussion/package.json",
    "skills/design-discussion/scripts/discussion_core/workflow_control.py",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/discussion_core/workflow_control.py",
    "skills/problem-framing/package.json",
    "skills/problem-framing/scripts/discussion_core/workflow_control.py",
    "skills/solution-design/package.json",
    "skills/solution-design/scripts/discussion_core/workflow_control.py"
  ],
  "src/shared/scripts/discussion_protocol.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/entry_prepare.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/execution_plan.py": [
    "skills/change-closure/package.json",
    "skills/change-closure/scripts/execution_plan.py",
    "skills/design-discussion/package.json",
    "skills/design-discussion/scripts/execution_plan.py",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/execution_plan.py",
    "skills/problem-framing/package.json",
    "skills/problem-framing/scripts/execution_plan.py",
    "skills/solution-design/package.json",
    "skills/solution-design/scripts/execution_plan.py"
  ],
  "src/shared/scripts/host_skill_registry.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/model_inventory.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/requirement_delivery.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/requirement_prepare.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/skill_preflight.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/stage_dispatch.py": [
    "skills/change-closure/package.json",
    "skills/change-closure/scripts/stage_dispatch.py",
    "skills/design-discussion/package.json",
    "skills/design-discussion/scripts/stage_dispatch.py",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/stage_dispatch.py",
    "skills/problem-framing/package.json",
    "skills/problem-framing/scripts/stage_dispatch.py",
    "skills/solution-design/package.json",
    "skills/solution-design/scripts/stage_dispatch.py"
  ],
  "src/shared/scripts/stage_handoff.py": [
    "skills/change-closure/package.json",
    "skills/change-closure/scripts/stage_handoff.py",
    "skills/design-discussion/package.json",
    "skills/design-discussion/scripts/stage_handoff.py",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/stage_handoff.py",
    "skills/problem-framing/package.json",
    "skills/problem-framing/scripts/stage_handoff.py",
    "skills/solution-design/package.json",
    "skills/solution-design/scripts/stage_handoff.py"
  ],
  "src/shared/scripts/stage_scope.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/supervision_core/__init__.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/supervision_core/commands.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/supervision_protocol.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/thread_settings.py": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/shared/scripts/workflow_control.py": [
    "skills/change-closure/package.json",
    "skills/change-closure/scripts/workflow_control.py",
    "skills/design-discussion/package.json",
    "skills/design-discussion/scripts/workflow_control.py",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/workflow_control.py",
    "skills/problem-framing/package.json",
    "skills/problem-framing/scripts/workflow_control.py",
    "skills/solution-design/package.json",
    "skills/solution-design/scripts/workflow_control.py"
  ],
  "src/shared/scripts/workflow_control_git.py": [
    "skills/change-closure/package.json",
    "skills/change-closure/scripts/workflow_control_git.py",
    "skills/design-discussion/package.json",
    "skills/design-discussion/scripts/workflow_control_git.py",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/workflow_control_git.py",
    "skills/problem-framing/package.json",
    "skills/problem-framing/scripts/workflow_control_git.py",
    "skills/solution-design/package.json",
    "skills/solution-design/scripts/workflow_control_git.py"
  ],
  "src/shared/scripts/workflow_progress.py": [
    "skills/change-closure/package.json",
    "skills/change-closure/scripts/workflow_progress.py",
    "skills/design-discussion/package.json",
    "skills/design-discussion/scripts/workflow_progress.py",
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/workflow_progress.py",
    "skills/problem-framing/package.json",
    "skills/problem-framing/scripts/workflow_progress.py",
    "skills/solution-design/package.json",
    "skills/solution-design/scripts/workflow_progress.py"
  ],
  "src/stages/change-closure/SKILL.md.in": [
    "skills/change-closure/package.json"
  ],
  "src/stages/change-closure/agents/openai.yaml": [
    "skills/change-closure/package.json"
  ],
  "src/stages/change-closure/references/closure-actions.md": [
    "skills/change-closure/package.json"
  ],
  "src/stages/change-closure/references/closure-protocol.md": [
    "skills/change-closure/package.json",
    "skills/change-closure/references/change-closure/closure-protocol.md"
  ],
  "src/stages/design-discussion/SKILL.md.in": [
    "skills/design-discussion/package.json"
  ],
  "src/stages/design-discussion/agents/openai.yaml": [
    "skills/design-discussion/package.json"
  ],
  "src/stages/guided-implementation/SKILL.md.in": [
    "skills/guided-implementation/SKILL.md",
    "skills/guided-implementation/package.json"
  ],
  "src/stages/guided-implementation/agents/openai.yaml": [
    "skills/guided-implementation/package.json"
  ],
  "src/stages/guided-implementation/scripts/foreground_host.py": [
    "skills/guided-implementation/package.json"
  ],
  "src/stages/guided-implementation/scripts/workflow.py": [
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/workflow.py"
  ],
  "src/stages/guided-implementation/scripts/workflow_stage_result.schema.json": [
    "skills/guided-implementation/package.json",
    "skills/guided-implementation/scripts/workflow_stage_result.schema.json"
  ],
  "src/stages/problem-framing/SKILL.md.in": [
    "skills/problem-framing/package.json"
  ],
  "src/stages/problem-framing/agents/openai.yaml": [
    "skills/problem-framing/package.json"
  ],
  "src/stages/problem-framing/references/dedicated-grilling-protocol.md": [
    "skills/design-discussion/package.json",
    "skills/problem-framing/package.json"
  ],
  "src/stages/problem-framing/references/repository-and-recovery.md": [
    "skills/problem-framing/package.json"
  ],
  "src/stages/problem-framing/references/templates.md": [
    "skills/design-discussion/package.json",
    "skills/design-discussion/references/problem-framing/templates.md",
    "skills/problem-framing/package.json",
    "skills/problem-framing/references/problem-framing/templates.md"
  ],
  "src/stages/solution-design/SKILL.md.in": [
    "skills/solution-design/SKILL.md",
    "skills/solution-design/package.json"
  ],
  "src/stages/solution-design/agents/openai.yaml": [
    "skills/solution-design/package.json"
  ],
  "src/stages/solution-design/references/conversation-source.md": [
    "skills/change-closure/package.json",
    "skills/design-discussion/package.json",
    "skills/guided-implementation/package.json",
    "skills/problem-framing/package.json",
    "skills/solution-design/package.json"
  ],
  "src/stages/solution-design/references/design-readiness.md": [
    "skills/solution-design/package.json",
    "skills/solution-design/references/solution-design/design-readiness.md"
  ],
  "src/stages/solution-design/references/subagent-protocol.md": [
    "skills/solution-design/package.json",
    "skills/solution-design/references/solution-design/subagent-protocol.md"
  ],
  "src/stages/solution-design/references/templates.md": [
    "skills/solution-design/package.json",
    "skills/solution-design/references/solution-design/templates.md"
  ]
}
```


### N5 — Runtime 结构与保护核验的精确衔接

`scope_authority.execution_plan` 保存 schema_version=1、catalog、plan_digest、contract_digest、plan_commit、sources、original_scope_revision；catalog 的每 Ticket source 用 path/plan_version/commit/sha256/mode，step_ref 用稳定 ID 与 step_digest。Scope Authority 的 revision 是唯一 current revision，目录不再单独增长一个权威计数。Stage-2 proposal/seal 和 Stage-3 start-dispatch 都绑定这个字段；独立 inline source 用原 B input_digest + inline plan digest，plan_commit=null 表示没有新造规划文档，不能被拿来信任 filesystem bytes。

Allocation 用 `plan_refs:[{ticket_id,step_id,plan_version,source_commit,source_sha256,step_digest}]`、authority_revision/authority_digest/plan_digest；release/received/accepted/current candidate 与 review/final verification 回显这些身份。inline ref 的 source_commit=null，必须绑定 inline source/input_digest；staged null commit 不合法。有界 bounds 沿用现有 bounded JSON/paths 规则，新增数量上限与现有 4096 manifest 项、128 brief 文字项相同数量级（Ticket/step 各最多4096，depends/refs 不超过 catalog 大小），禁止 float、重复键、globs、symlink 和 aliases；错误包含 ticket/step ID 或精确 repo relative path，不输出全文或凭据。

`plan_revision_transaction` 属于原 C state，不是可独立启动的记录；包含 revision_id、原报告 input/digest、old_authority、Controller classification/reference、stopped original host/call evidence、snapshot、planning_paths/release、planning_candidate/commit facts、proposed_authority、carry_forward/revalidation 和 control result。报告不是授权，只有 prepared Controller release 才能写；仅原 owner ingress 可 commit/activate，内嵌 ledger port 仍按现有事务保存 controller_ref。

内部方案影响边界和公共接口的 contract_refs 明确冻结 D4/D5 中的角色权限、production-boundary 输入/输出/失败及用户行为，不因为它们位于可修改设计章节而豁免。Controller 只能更新 Ticket plan block、Spec N2 私有调用链证据和 N4 inventory/manifest；exact projection 对这些 mutable 区域以外仍逐字节核对。常规 protected 文件校验从原 Stage-3 launch 更新为“原 frozen identity + 已验真 revision commit lineage + 当前规划 blob”投影：原 requirement 整文件不变，规划变化只来自已经激活的精确 only-planning commits。Git cumulative-diff/candidate/new-allocation 检查仅排除这些已验真规划提交的 exact paths/deltas，并逐项证明 implementation/index 与原 snapshot 匹配；不能把整个 planning 目录加入 allowed implementation_paths。

所有正式可修订本地规划文件必须在 current manifest 的 closure-owned planning 路径中声明（允许与 planning_paths 精确重叠）。这是为了 Stage-4 final publisher 能发布 Controller 的规划 commits；revision 同步 closure 项而不授 Stage 3 implementation writer 文档权限。原 implementation baseline 始终不变，allocation prepare 的 snapshot HEAD 可随正常 Git commit/规划 revision 前进，manifest_baseline 仍为原封存基线，以免 add/delete operation 判断漂移。B current_readiness 验证新 current Scope Authority 的 revision lineage、原 Stage-2 acceptance/publication/requirement 与 binding，不能只读历史 Stage-2 seal，也不能用最近 HEAD 重造 source。

新增文件授权是同一 activation 的 manifest refinement：先完整新 source/output closure 与明确责任边界、Controller 证据，再 derived implementation/closure paths，再 fresh allocation；老 prepared/bound/release 的 authority 不被追溯替换。新路径尚不存在时保留 absence fingerprint；不因新增路径缺之前 file hash 而放松窗口/归属。修订只改 inline 或文档，无 implementation delta 时仍需更新 current authority，旧 review/final evidence仍失效。


Internal revision 可同步与受影响函数相同 seam 的 focused/affected check 文件/命令，但必须保留原验收行为、full/environment要求、runner入口与允许skip条件；proposed_validation_plan 和当前 validation_plan digest在同一 activation 更新，原计划/尝试保留历史。改变测试层级/seam的material选择或削弱验收不得归入此分支。carry-forward映射将未变step的旧Ticket版本ref对应到当前版本ref，保存原result/ref和新对应关系，不改写旧回执；新source byte身份必须先整体核验，不能用step相等豁免文档漂移。


## 交付记录（2026-10-05）

Lifecycle: completed

### 本次确认的范围与完成含义

用户最新明确选择“先交付原需求吧”：本次交付 Ticket → 函数执行计划 → Dispatcher 调度/整合 → 真实 Execution Agent 实现/TDD → 原 Controller 合同内修订主流程，并授权解释性文档收尾和最终本地集成。无 A / 无 Ticket 的独立入口、foreground Controller ingress、attached ledger 初始化的补充路线延期，不纳本次交付；没有安装、部署、全局配置或远程发布授权。

Lifecycle 表示该最新授权范围的工作已完成，原 Spec、User Stories、AC、函数计划、计划版本与合同正文完整保留。上方“此次仅规划”等文字是当时规划记录；本节追加后续实施事实，不把原全路线承诺或未勾选的扩展 AC 改写为通过。补充路线需后续单独授权、实现和现场验收，本次不自动开启。

这次生产收尾按用户最新授权作为普通本地交付进行，不是保留中的旧 production C 的正式 Stage 4，不创建新的 C，不补造 handoff 或 acceptance。旧 production C v17 仍为 pausing（序号 1520）；原记录、固定包和失败试验保留，不迁移、不标成功。下述 C417 只属于真实宿主样例。

### 生产候选与验证

生产实施候选为 `6d5bbb23129beae6ed071a49501df0b7c026b321`。完整 `scripts/validate.sh` 验证覆盖 57 个测试文件、1,166 项测试，零失败、零错误、1 项既有跳过，进程自然退出码 0；五包生成一致性及 81 个资源闭包通过。I340 主体及 f52/6d5 增量的独立 Standards / Spec 两轴审查均保留零接受问题的原证据。本次文档收尾不重新跑全仓，也不把先前验证冒充文档提交后的新测试。

当前五包 metadata 为 `1.3.0`，control key 11 / JSON schema 6、runner outer 10、progress-v19、transfer-v14、execution-plan-v1。D7 的 progress-v18 是冻结规划时的声明；实际生产候选已在后续增量更新为 v19，此处记录差异，不改写原合同或版本正文。旧可执行记录使用其原固定运行时，本次没有升级旧记录或本机安装。

### 真实 Ticket 主流程验收

真实宿主样例由原 Controller 使用与生产候选相同的包字节运行；269 个包文件的 Git blob 与 modes 匹配。Stage 2 实际产出并封存/发布 Spec、Ticket 和函数计划；原 Dispatcher 成组调度真实嵌套 Execution Agents，按当前 refs 完成实际 TDD 和验收，Dispatcher 没有实现写入来源。

原 Controller 在完整停止屏障后独占精确规划 release，实际仅提交 Ticket 计划修订 `c9bb167bc654e685eac504840c40f5cf1f796307` 并激活 authority revision 2；实现、index 与 protected 字节保持不变。未受影响已验收结果有 carry-forward 依据；受影响 CLI 及只读依赖改变的 Facade 经独立真实分配复核/采用后重新验收。旧 v1 入口因 `entry_changed`、旧 refs 因 `execution_plan_stale` 被拒绝，记录与源码不变；这不额外声称独立 token 算法验收。

公共及不明影响实际进入 `user_decision_required`，C/B/Git 保持不写入。暂停后通过 fresh query 和原 Dispatcher 身份恢复；实际中断的原 native continuation 先保留 await-host-recovery，再核对原调用/响应和 parent event，使用同 refs 恢复完成交付。这证明已知原调用的不确定业务完成恢复，不是未知创建验收，也不声称宿主丢失后启动第二宿主通过。

当前实施候选 `25827238ef05ae6fd13891fbd0c0b8c883922bd1` 完成新的独立 Standards / Spec 两轴审查（零接受问题）与原 Dispatcher 的 31 项完整测试（零跳过），Stage 3 在当前 revision 接收。实际 Closure Agent 形成候选 `bfd4975c3aff7d1da5e88a4b4f755754908543bd`，合并 `5d05367be07c415638d45874fd3c708985d1c02d`，核对 worktree/branch 删除及主检出保留，原 Controller 于 C417 接收当前计划 Stage-4 结果。该样例完整结果不替代生产旧 C 的状态。

### 延期与未验收路线

| 路线 | 实际证据与当前边界 | 本次结论 |
| --- | --- | --- |
| 无 A / 无 Ticket 的 standalone inline | 只读审计确认纯 control 初始入口存在，但缺合法 C 计划修订初始化桥；没有完整现场 ingress 验收 | 用户延期，不宣称支持该修订路线 |
| foreground | 固定 runner 明确记录 `controller_revision_ingress` unavailable，拒绝 prepared revision；没有实际 foreground host 通过证据 | 用户延期，不由 carrier 冒充原 Controller |
| attached ledger | 真实隔离 bootstrap 和 0→2 Phase Run 已准备，C 初始 Stage 2 与 ledger 初始 Phase 0 不一致，start 返回 `invalid_request`；没有创建 native role，原 transaction/Flow 保留 | 用户延期，初始化拒绝记录原样保留 |

原 T3 对上述三路线的计划承诺与 Ticket FEP-03 AC-4 保留，未标通过；自动化 fixture、只读入口检查、worksheet 生成或主线 C417 均不能替代其现场验收。取消、未知创建、宿主丢失及目标竞态下的 publication recovery 也不因本样例的暂停/已知调用恢复与正常归档而获得额外现场通过声明。

### 可追溯证据

证据位于本机运行目录，不随生成 Skill 包分发：

- `/Users/zhaolaiyuan/.codex/workflow-runs/01a0fa75-e2b2-72a0-9ad7-ece250b691da/function-execution-plans.baseline-lifecycle-full-root-verification.json`：生产候选及原完整验证读回。
- 同目录 `evidence/t3-gpt61-high/acceptance-evidence-index.json`：非权威证据索引；权威样例状态在 `evidence/t3-gpt61-high/workflow.json`（C417）。
- 同目录 `evidence/t3-attached-ingress/actual-attached-start-conflict-diagnostic.json`：真实 attached 初始化拒绝。
- 同目录 `evidence/production-ticket-delivery/confirmed-delivery-scope.json`：用户最新范围选择、本地交付授权与排除的副作用。

本节是完成事实与范围决定的追加记录，不是运行时权威、旧 C 状态迁移或新的阶段交接凭据。
