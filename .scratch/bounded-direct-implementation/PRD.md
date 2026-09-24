# 局部修改的有限直接实现例外

Status: ready-for-agent
Review state: accepted
Lifecycle: completed

用户已确认本方案及 Testing Decisions，并授权按完整 Stage-3 流程实施。本文件是实施依据；实现由主任务编排，Stage 2 仅发布规划。

## Problem Statement

现有 Stage 3 始终要求 Implementation Dispatcher 再派发 Execution Agent，连行为清晰、位置已知、可单独验证的局部修改也承担派发、绑定、停止与交付验收成本。不能仅凭 Agent 声称“简单”或修改文件少而放宽：单文件 checkpoint、恢复或并发变更同样可能影响整个流程。

需求是保留完整流程为默认，仅在 Controller 有具体证据时允许同一个 Dispatcher 直接编码、测试和提交，同时保持真实身份、独立审查、同候选验证、Flow Worktree 与 Stage 4 归档。

## Solution

在现有 Stage-3 handoff 和 control checkpoint 中增加一份有限的执行方式决策。Controller 在启动 Dispatcher 前，根据冻结需求、已定位的修改位置和针对性验证方式判断是否满足全部条件。缺省使用完整流程；满足例外时简短披露理由并授权 Dispatcher 直接执行。Dispatcher 不能批准自己的例外，也不能将 Controller 变成实现者。

直接实现候选以真实 Dispatcher 身份和原生停止/结果证据归属，不构造 Execution Agent。独立 Standards/Spec 审查及最终验证照常进行。发现超出准入条件时，Dispatcher 停止继续编辑，Controller 将原 checkpoint 单向转为完整流程，保留同一 Dispatcher、同一 worktree、原始累计 diff 和已有字节，并让真实 Execution Agent 验证和接管未验收修改。

## User Stories

1. 作为用户，我希望普通任务默认走完整流程，避免例外成为默认捷径。
2. 作为用户，我希望局部且有证据的任务减少额外执行者协调，同时保留独立审查。
3. 作为 Controller，我希望准入依据来自已冻结目标、明确位置和实际测试 seam，而不是实现者的复杂度自评。
4. 作为 Controller，我希望条件未知、设计未定或任何排除项出现时自动选择完整流程。
5. 作为用户，我希望单文件 checkpoint、恢复、并发、状态机修改仍使用完整流程。
6. 作为 Dispatcher，我希望只按绑定的执行方式工作，不能临时自行启用直接实现。
7. 作为 Dispatcher，我希望直接模式仍读取并执行 implement/tdd，以真实生产调用边界完成 red/green。
8. 作为 Controller，我希望候选能证明来自实际绑定 Dispatcher，不能用空执行列表或伪造执行身份过门。
9. 作为用户，我希望文件内容、存在性和模式的变化都受到候选归属检查。
10. 作为 Dispatcher，我希望发现局部假设失效时保留已完成的脏文件或提交，无需重建工作区。
11. 作为 Execution Agent，我希望接管时知道哪些字节是继承的未验收工作、需要重新验证哪些行为。
12. 作为 Controller，我希望无新增编辑但确已检查通过的继承字节也能被真实执行者明确验收，而不是制造无意义修改。
13. 作为审查者，我希望升级后的审查仍覆盖原始目标基线到候选的全部变化，不遗漏直接阶段提交。
14. 作为用户，我希望未知派发结果、停止请求与未知发布状态沿用原恢复规则，不被方式切换重放。
15. 作为用户，我希望旧记录保守执行，运行中的固定旧包不被迁移或偷偷升级。
16. 作为 Closure Agent，我希望仍只消费已审查、已验证的准确候选和原归档权限。

## Implementation Decisions

### 1. 准入由 Controller 负责，证据随原交接冻结

共享准入规则由现有 Workflow Control 模块拥有。Stage Handoff 校验并封装，Stage Dispatch 传入 start-dispatch，Workflow Progress 继续拥有事务和 checkpoint 持久化。Entry Prepare 仍只提供真实项目、注册、来源、身份及 binding 事实，不增加另一个风险分类器。

在 Stage-3 dispatcher 的 semantic 输入新增可选 implementation_policy；缺失等价 full。显式 direct 必须带一个 Controller assessment：决策 reference、controller_ref、冻结需求 identity、scope digest、原 baseline、单一职责说明、已定位的准确实现/测试路径、行为与验收引用、针对性检查及 seam、四项准入条件的逐项事实依据，以及全部排除项的否定判断及依据。这里是固定需求的证据清单，不设置分数、等级、文件数/行数阈值或额外分类体系。

四项必须同时明确成立：行为/验收确定且无待定设计；单一职责且无跨模块接口、数据格式、权限或流程状态变化；针对性检查足以验证且不依赖多方集成；修改位置已经定位且不需探索扩大。状态机、并发、恢复、迁移、公共接口以及上述跨边界变化任一成立，或事实未知，结果都是 full。测试文件可与实现分属多个文件；文件数量不参与决定。

Controller 只在已有授权范围内作此技术判断，不新增用户审批。完整流程不要求提交四项冗长论证；只有申请例外才需完整证据。用户要求 full 优先；“很简单”“只有一行”、Dispatcher 自荐或缺字段均不能构成 direct 授权。格式错误/冲突的显式 direct 请求拒绝并要求修正为 full 或补齐现有证据，不静默丢弃输入。

所有证据绑定进现有 sealed handoff 和 start-dispatch attempt；绑定后不可改 direct 事实、路径或模式。新字段只对 Stage 3 的 implementation-dispatcher 有意义；Execution Agent、Stage 2/4 的直接请求拒绝。交互和附着讨论的原 Controller 可以批准 direct。当前 foreground scripted carrier 的已冻结 runner 输入没有原 Controller 的 direct 决定，因此始终使用 full；不把 carrier 自填的字段或回执视为 Controller 授权。若将来宿主能提供可核验的原始决定，再单独设计该入口。

脚本能机械证明字段、来源 identity/hash、scope、Controller receipt、绑定、文件快照、检查结果和真实角色；不能从 diff 自动证明“单一职责”“没有状态机影响”或测试语义充分。后者由 Controller 阅读冻结方案和相关生产代码判断，并接受独立审查复核。验收声明不得把结构通过说成自动风险证明。

启动披露用一句具体说明，例如“已定位到输出文本职责；行为与验收已确定，针对性渲染检查覆盖修改；无接口/格式/状态/权限或恢复并发影响，本次由 Dispatcher 直接实现”。不得复用该示例替代实际证据。

### 2. 真正的直接实现 provenance，保持原候选固定点

Control 的 handoff_progress 保存 implementation_policy；直接 provenance 存于原 candidate_evidence，引用 policy digest、实际 dispatcher_ref/attempt、原 baseline、候选 commit、完整候选路径及内容/存在性/模式指纹、实际检查结果和对应的原生结果/停止回执。没有独立执行结果数据库，没有虚拟 Execution Agent，也不往 executions 数组塞 Dispatcher。

直接授权只在 Dispatcher 实际绑定后生效；直接模式 executions 必须为空，禁止 plan-execution 绕过方式转换。Controller 仍不获得实现写权限。Dispatcher 自己执行原 implement/tdd、必要检查及 Git 整合；独立 reviewer 不得等于 Dispatcher。

candidate-ready 仍通过 Workflow Progress 的控制事务调用 Git adapter。完整模式保留“至少一个真实已接受 Execution Agent、所有 writer 已停止、每个变更文件均有 accepted 指纹”的现有硬门禁。直接模式仅替换这一个来源分支：要求原 Controller direct 决策、同一绑定 Dispatcher、无 Execution allocation、实际原生 stopped/result 证据与该 candidate/attempt 对应；实际 HEAD clean、原 baseline 祖先、expected_target_head 祖先和当前目标一致、protected 不变、累计 diff 在允许范围内、候选检查通过。普通来源字符串、child 文本声称 stopped 或 caller 提交的 clean/hash 均不足。

Git adapter 对真实文件计算指纹并与提交对象相核验；候选 evidence 冻结后仍适用原 review-start、review-converged、validation-start/result、delivery-ready 和 accept-delivery 的 exact-candidate 检查。直接模式不会把 Dispatcher 自测升级为独立审查。

直接 provenance 是授权角色和实际 Git 结果的证据，不声称 Git 能法证式识别键盘作者。共享 worktree 的外来变化仍按现有异常处理；未解释变更不能因 direct 自动被认领。

### 3. 越界时同一 Dispatcher 单向转换，保留但不验收旧字节

本次不使用 prepare-dispatch-recovery 来做正常方式升级：该操作要求原 host 不可恢复且要创建替代 Dispatcher，与“同一 Dispatcher 继续”相矛盾。给现有 Workflow Control/Progress 增加一个有界控制 action escalate-implementation，复用既有 control_transactions 的 intent/save/result/replay 和 Controller receipt 校验；这只是原 implementing 状态内撤销 direct 权限并转 full，不新增执行状态机、runner 或派发协议。

顺序固定：Dispatcher 发现条件失效即停止编辑并报告具体新事实；Controller 收到同一 ref 的真实停止/空闲且已停止写入证据，核对无待决 native call、allocation、review/validation/发布事务，冻结 Git snapshot，再通过一次控制事务撤销 direct。成功后向同一 Dispatcher 发原生 follow-up，继续原工作区的完整流程，不另起 Dispatcher、不改变 attempt/source/binding/git_baseline_commit。若当前候选已进入审查/验证，先依原 invalidate-candidate 流程停止相关活动；新目标或扩大写入路径需原业务异常/授权决策，升级不隐含扩大 scope。

升级 snapshot 使用现有 recovery_snapshot 的取证方法：HEAD、branch、index hash、原 base、目标 HEAD、全 allowed/protected 文件的存在性/内容/模式；分开检查 committed/staged/unstaged/untracked，不能让新增后删除的净零 diff 隐去越界。保留 dirty index 和已有提交，不 reset、不 stash、不重放 Git 副作用。任何来源/权限越界或无法解释的变化先阻塞，不借“升级”认领。

Controller 记录同一 assessment 的撤销理由、snapshot digest、实际 dispatcher identity、decision reference 和所有待复核继承路径。该历史 immutable；只允许 direct→full，一旦升级不能在本次 attempt 再回 direct。重复相同已完成事务 ACK；不同快照、重复消费旧决定或发生漂移则拒绝，不能重拍快照掩盖未经授权的后续写入。

升级 snapshot 仅是保留字节的来源证明，不是 accepted implementation。verify_execution_start 将它作为经过 Controller 接收的初始工作快照核对，随后才可 plan-execution。原 git_baseline_commit 一直不变；每个 Execution Agent 的 allocation Git snapshot 仍取当前 HEAD/index/全范围字节，以禁止该 Agent 变更 Git。当前 HEAD 可以包含 direct 已提交代码，但绝不取代审查和最终发布的累计基线。

为解决“继承字节没有新 diff 就无法验收”，仅对升级产生的待复核路径，在既有 execution allocation 中增加显式 adopt_paths：它必须是当前 task 精确 paths 的子集、属于本次升级 snapshot 待复核集合，绑定其完整指纹和 snapshot digest。真实 Execution Agent 获得 baseline→继承 snapshot 的累计变更说明、验收目标及测试要求；核查这些字节并修复必要问题后，报告实际 changed_paths 与 adopted_paths 两个事实，不能把未改动文件谎报为新编辑。

execution-result/accept-execution 的原有全范围 delta 和 peer 检查保留；adapter 分别核对实际新 delta 与显式 adopted 路径，结果指纹覆盖两者并集。对 adopted 路径，即使无新编辑，也需真实被绑定并已停止的 Execution Agent、针对继承行为的检查证据和当前完整指纹才能接受。candidate-ready 仍要求每个累计变化文件有 accepted 执行交付；升级后不得使用 direct snapshot 替代 accepted 指纹。尚未派发、仅声称看过、未测试或字节漂移的继承路径不能通过候选。

这个扩展不改变普通 allocations 的 delta 语义；没有升级快照时 adopt_paths 必须为空。并发共享路径仍禁止，未知创建仍不得换身份重派，Agent Git 操作仍禁止。分配后的 HEAD/index 改动仍拒绝；Dispatcher 只能在所有相关 writer 停止后整合提交。对于升级后被还原到原始 baseline 的路径，无最终变化无需伪造交付，但保留已发生的 provenance 历史。

### 4. 停止、恢复、修复和兼容

直接运行的停止屏障仍跟踪实际 Dispatcher 和所有已发出的原生调用；executions 为空不意味着 Dispatcher 已停。pause/resume 在 exact identity、policy 和实际 snapshot 未变时按原流程继续；证据不足或条件变化一律先升级 full。cancel 保留字节，取消后不得通过升级复活写权限。unknown 创建、结果、提交/发布副作用保持原 checkpoint 和查询路径，禁止重放。

需要替换不可恢复 Dispatcher 时仍走原 prepare-dispatch-recovery→intent/result→recover-dispatch，证明所有旧 writer 停止。新 Dispatcher 默认 full，不继承旧 direct 准入；Controller 的现有 ownership.dispatcher 接收作为未验收来源，按与升级相同的显式 adopt_paths 实际执行者验证语义处理。不得伪造旧原生记录，不改 baseline；继承检查只作诊断历史，重建当前候选/审查/验证。此项只扩展本次新增 direct 来源的恢复，普通历史 allocation 的 release/revalidate 行为保持。

审查发现局部修复时：原独立审查先停止，invalidate-candidate，原 Dispatcher 仍满足准入才能继续原 direct 范围并产出新候选；条件不满足则升级 full。每个替换候选重跑受影响检查、双轴审查和最终验证，旧候选证据不复用。目标分支前进需 merge/rebase冲突、恢复或跨模块整合时 direct 条件未知/失效，先回 full，再使用原目标竞争恢复；不将例外扩展到并发整合。

没有 implementation_policy 的同代新输入/checkpoint 按 full 解释，仍要求真实 accepted 执行交付。已有旧版本记录只能由原固定包继续运行；不修改记录、不替换包、不自动迁移。因为新的候选证明和接管语义旧 runtime 不理解，发布时提升现有 stage-transfer/workflow-progress 协议版本和包兼容键，并更新对应契约测试；不新增 direct 专用协议。Control 的严格 schema 兼容处理须明示缺省 full，不能给旧 candidate 补造 provenance。打包按现有构建产物闭包更新；不部署到本机、不修改注册或 cc-switch。

### 5. 最小关联文档和架构提议

ADR-0007 目前将 Stage 3 描述为 Dispatcher with bounded Execution Agents；当前 Stage-3 Skill 和 Workflow Control 文档更严格，要求至少一个 accepted Execution Agent。本 Spec 明确提议将其改为“默认必须真实 Execution Agent；有 Controller 证据的有限 direct 例外由 Dispatcher 实施，候选仍须真实 provenance 与独立接受”。Stage 2 不修改 ADR；实施时须同步该 ADR 的决策文字、Stage-3 Skill、执行协议、workflow-control、stage-transfer/progression 和 runner 提示/契约，避免业务文档与脚本门禁冲突；不弱化 Stage 0–2、Stage 4、模型/包预检规则。

本功能自身改变候选、恢复和持久化契约，明确不符合 direct 准入，未来实施本 Spec 必须走完整流程。不能用正在开发的例外来授权它自己。

## Testing Decisions

测试外部可观察行为与拒绝边界，不测试措辞评分或实现内部函数排列。首个 TDD slice 使用现有真实生产 seam：Workflow Progress → Stage Handoff/Dispatch → Workflow Control Git → 临时 Git repository；输入 Controller direct evidence，经实际绑定 native 结果到候选，再到独立审查/最终验证。允许替身的只有原生宿主回执、模型目录或外部命令响应等下游外部依赖；不能 fake 中间 control/Git 边界来获得绿色结果。

| 验收/分支 | 必要观察及现有测试层 |
| --- | --- |
| 缺省、未知、条件失败、排除项 | control 参数化断言 full 或拒绝 direct；单文件 checkpoint/恢复/并发作为显式拒绝案例，不按路径数量判断 |
| 合格例外 | 真实 A/B/C 生产链：恰好一个 Dispatcher，零 Execution allocation，真实 candidate 能进入 reviewable；Controller 未获得写权限 |
| 防自评/伪造 | Dispatcher 决策、错 Controller/attempt/source/scope、篡改 sealed policy、假 stopped 或虚拟 execution 记录均拒绝 |
| 真实 direct provenance | 临时 Git 下增删改、mode变化；clean/HEAD/target/祖先/protected/越界/指纹漂移的失败分支，候选 immutable |
| 升级保留字节 | 脏文件、暂存修改、direct 已提交修改分别升级；相同 ref/worktree/base 不变，无 reset；实际复核前 candidate 必失败 |
| 继承无新 diff | 真实 Execution Agent 显式 adopt 并通过检查可接受；没有 adopt、未知快照、未停止、缺测试、accept前漂移均失败；累计 direct 提交仍在审查范围 |
| 完整流程不退化 | 原全范围 delta、nonoverlap、peer stopped、Git 禁写、每个候选路径 accepted 约束继续通过；普通 allocation 不可利用 adopt |
| 顺序与重放 | upgrade前后事务响应丢失、重复同一决定ACK、旧决定错用、新快照漂移、pause/cancel优先级、unknown native阻塞而不重派 |
| direct 恢复 | 替换前完整停止屏障；默认 full；真实执行者复核 direct 字节；原host可恢复时不走替换流程 |
| 独立审查和 Stage 4 | 两轴都针对exact candidate，reviewer != Dispatcher；最后验证失败不交付；runner handoff保留accepted transfer；同candidate归档/清理与未知发布不重放 |
| 包和兼容 | 缺policy full、旧协议只读拒绝、固定包digest不升级、跨包兼容键一致、生成闭包与源码文档断言同步 |

优先扩展现有 stage-transfer、workflow-progress、workflow-control/git、workflow runner 和 packages 测试，不新建专用 direct runner/test harness。首次 production-boundary red/green 后完成针对性测试以及仓库要求的构建、验证检查；不启动本机 deployment。方案阶段只做只读代码检查，不声称这些新增测试已存在或已通过。

## Out of Scope

不改变 Stage 0–2 的流程；不减少快照、回执、恢复、模型选择或包预检；不改变独立审查轴、同候选严格验证、Stage 4 发布清理。没有基于大小的自动分类、成本打分、可扩展风险插件、第二 ledger、独立 runner、独立简单任务协议或新增用户批准步骤。不修复无关历史恢复缺陷，不迁移在跑的旧记录，不安装、部署或远程发布。

## Further Notes

### 需求与审阅身份

唯一需求源：`docs/requirements/2026-09-23-bounded-direct-implementation.md`；commit `f006e55a0ef8c9dc665ba2687197c6116337e62e`；SHA-256 `2a929e1d3a2f18dbe29bdb75e4ac72fa55b8f330daced7e992f2745798bea464`。该文档全部已确认，本 Spec 未补充聊天需求。目标仓库为 workflow-pipeline，target main；Flow Worktree 分支 codex/bounded-direct-implementation。本 Spec 按项目约定发布到本地 Markdown tracker。依已注册 `$ask-matt` 的多会话构建判断，不创建 Tickets：修改共同维护一条候选归属契约，完整 Spec 的决策和测试表可由同一 Implementation Dispatcher 上下文承载；无需拆成独立会话实施切片。

### 变更契约预检：真实调用链证据

以下是基于上述 commit 的代码定位证据，路径只用于核对当前设计，不作为长期接口契约。

| 生产入口/owner | 当前行为与证据 | 必要最小改动及不变边界 |
| --- | --- | --- |
| `src/shared/scripts/entry_prepare.py:201` registration_inputs | 委派/Flow入口要求原host注册证据；repository/source/binding由本入口提供 | 不在此分类风险；保留真实入口事实，供handoff绑定policy；不以旧安装包推断工作区业务逻辑 |
| `src/shared/scripts/stage_handoff.py:296` prepare；`:344` semantic；`:440` render | semantic严格字段、scope baseline=current HEAD、Stage3 validation_plan；sealed handoff用于payload | 增加可选policy及Controller来源校验并纳入digest；execution semantic传adopt事实；stage3完成结构可继续由accepted_transfer带完整证据 |
| `src/shared/scripts/stage_dispatch.py:34` checkpoint；`:129` start-dispatch；`:134` plan-execution；`:355` received | control port带原授权baseline，非execution用原scope基线；execution使用原dispatcher git_baseline而非当前allocation HEAD | 传递policy/adopt来源；保留原native launch/bind/write-release/receive/accept路径，不造子角色；升级不重建B dispatcher记录 |
| `src/shared/scripts/workflow_control.py:672` start-dispatch；`:694` plan-execution；`:872` candidate-ready | implementing→reviewable要求至少一个accepted execution及全部changed paths/hash覆盖 | policy及单向撤销由此拥有；仅direct候选切换真实provenance分支；full硬门禁保持，采用真实adopt结果补齐覆盖 |
| `src/shared/scripts/workflow_control_git.py:87` execution_delta；`:119` recovery_snapshot；`:162` verify_execution_start；`:293` candidate-ready | allocation前要求来源；结果比较全scope delta、HEAD/index；候选再次强制accepted指纹；snapshot检查净diff以外的已提交/暂存/工作区变化 | direct Git证明；升级snapshot与adopt完整指纹；原基线不变，继承来源不当接受；不能只改control的accepted数量判断 |
| `src/shared/scripts/workflow_progress.py:1683` control_action；`:285` stop_barrier_pending；`:592` start | action allowlist、controller receipt、持久化intent/result、暂停取消/未知调用屏障；runner复用同checkpoint | 新upgrade控制action必须走同事务/屏障并绑定真实dispatcher停止；direct candidate从真实native结果推导provenance，不接受自报；没有第二状态记录 |
| `src/stages/guided-implementation/scripts/workflow.py:575` prompt；`:663` result验证；`:747` continuity | runner只协调原stage carrier；Stage3完成需独立review/verification；Stage4必须同candidate/binding | 提示和协议兼容更新；消费原Progress checkpoint及accepted_transfer，不自己分类/实现或新增状态机 |
| `src/shared/scripts/stage_handoff.py:238` verify_result；`workflow_control.py:901` review-converged | Git完成核查、独立双轴结果、exact candidate；Stage4只改closure路径 | 不删减，测试直接和升级候选都经过这条完成链 |

现有测试依据：`tests/runtime/test_stage_transfer.py` 的真实 A/freeze/Flow、execution allocation、accepted design→Stage3 测试；`tests/runtime/test_workflow_progress.py` 的停止/未知回执、decision消费和恢复测试；`tests/runtime/test_workflow_control_git.py` 的真实 Git 归属测试；`tests/runtime/test_workflow.py` 的runner完成与连续性测试；`tests/packages/` 的固定包与跨包契约测试。

发现并解决的矛盾：安装中的旧 Stage-2 包文档写 optional Execution Agents，但当前源码明确强制至少一个；设计以当前源码为基准。只改提示会撞到 control和Git双门禁。只改mode会撞到verify_execution_start；本方案保存未验收快照并通过真实adopt验证解决。把当前HEAD作为新的总基线会漏审direct提交；本方案固定原基线。复用replacement recovery做正常升级会改变Dispatcher；本方案只在原implementing内增加有界控制动作。

方案就绪：所有变化均有owner、输入输出/失败及测试seam；已覆盖默认优先、单向升级、未知/停止优先、候选不可漂移、恢复和旧包兼容。当前设计无未决产品选择。Testing Decisions 与 ADR-0007/Stage3 约束修订已获用户确认；Stage 2 未修改 ADR 或执行实现。


### 实施与归档完成记录

实现候选：`f063106075cb13d0675451dec12900e71541916f`。Controller 已接受独立 Standards 与 Spec 双轴审查；两轴均针对该候选通过。有限 direct 准入、真实 provenance、单向升级与继承字节复核、恢复及协议兼容已完成实现，本功能自身按完整执行流程交付。

最终验证：该候选执行 `./scripts/validate.sh` 退出码 0，792 项测试通过（1 项按协议条件跳过），五个发布包验证通过，仓库检查 `valid` 且 `issues` 为空。跳过项为 `NonGitAttachedTransferTests.test_successor_dispatch_uses_real_ledger_slot_without_promoting_old_carrier`，原因是 wrapper transfer 使用 Git stage-entry checkpoint。验证前后候选提交一致，工作区干净。

归档仅追加完成记录，保留原方案、验收标准和冻结需求；本次交付限本地 Git 发布与 Flow Worktree 清理，不含 push、安装或部署。
