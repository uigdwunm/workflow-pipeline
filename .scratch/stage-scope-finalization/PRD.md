# 阶段范围定稿、修订与旧 Stage-2 窄恢复

Status: ready-for-agent
Review: scope-review-20260926-01 — accepted（用户确认方案与测试设计；Tickets 亦经 tickets-review-20260926-01 确认）
Requirement: docs/requirements/2026-09-26-stage-scope-finalization.md
Requirement commit: 8e57bba9fdc034229cf7c114fb7e911296bece00
Requirement SHA-256: faf10fcd96541a88540b910b45bd8cdba396ee46def0b5b6820fc454848718eb

## Problem Statement

Stage 2 在设计尚未完成时冻结 implementation_paths / closure_paths，空数组既表示尚未确定又表示明确无工作。规划成果验收随后无条件授予 downstream_ready，Stage 3 又只能缩小前驱范围。因此完整方案可能无法执行，空清单与非空漏文件都没有受控补齐路径。直接改原清单及其摘要会破坏授权历史；重新做方案、换工作区或升级旧包也不能解决授权因果。

## Solution

Stage 2 启动只冻结已知边界：需求、目标、Flow Worktree、规划写入和保护路径。设计者根据完整方案提出逐文件实现/归档清单及依据，Controller 在发布前核对并封存。规划成果验收独立记录，只有范围、出版、停止屏障及附着讨论均已核验才授予实现交接就绪。漏项通过显式范围修订事务补齐；已有执行者必须先停止并结清调用。旧 v12/v7 Stage-2 已验收记录仅经窄恢复建立补充授权，完整保留原记录历史字节与原运行包身份，显式接管唯一 current，也不重做设计或发布。

用户已确认本设计与下面的测试 seam。ask-matt 判断：范围涉及多次完整实现切片，Tickets 有益；使用 to-tickets 形成四个可独立验收的切片，四项拆分与依赖亦已确认；本阶段完成规划提交及本地发布，不进入实现。

## User Stories

1. 作为 Controller，我希望设计启动时可以明确“后续范围未知”，避免提前猜测文件。
2. 作为设计者，我希望列出代码、测试、新文件、生成物和文档的完整清单，使方案可以实际交付。
3. 作为 Controller，我希望清单每项能追溯 Spec/Ticket 决策，以区分文件细化与业务扩张。
4. 作为用户，我希望范围内的文件细化由 Controller 处理，无需重复批准。
5. 作为用户，我希望功能、权限或副作用扩大时仍收到明确变更决策。
6. 作为 Controller，我希望规划完成不会冒充实现就绪。
7. 作为 Dispatcher，我希望 Stage 3 入口重新验证最新范围、需求、目标和工作区。
8. 作为 Closure Agent，我希望明确空归档清单合法，且不能因此失去冻结需求保护。
9. 作为 Dispatcher，我希望漏掉一个测试文件时能受控补齐，不必换工作区。
10. 作为执行者，我希望修订开始就撤销旧写入租约，避免新旧分配同时写入。
11. 作为 Controller，我希望不确定创建、未决调用或未停止作者阻止范围变更生效。
12. 作为 Controller，我希望修订中断后能从原事务继续，重复决策只返回原结果。
13. 作为旧流程拥有者，我希望复用已发布规划与原工作区补建新交接。
14. 作为审计者，我希望旧接受记录、原包 pin 和宿主证据保持原貌。
15. 作为 runner 使用者，我希望 runner 与 interactive 采用相同范围权威和恢复判定。
16. 作为附着讨论使用者，我希望 Topic 中的 Phase Run 与实际交接保持一致。
17. 作为独立阶段使用者，我希望独立 Stage 3 仍能以自包含完整范围启动。
18. 作为维护者，我希望生成包资源闭包和兼容键共同更新，旧运行不会自动升级。

## Implementation Decisions

### D1 — 唯一范围模型与职责

新增共享内部模块 stage_scope，拥有范围结构、规范化、差异、覆盖与兼容快照校验；它不持久化、不调用宿主、不授权、不发布。B stage_handoff / stage_dispatch 调用它做边界验证；C workflow_progress 拥有事务及投影；workflow_control 拥有原 Controller 的决定和当前授权。Git 事实由 workflow_control_git 获取。附着讨论仍使用现有 ledger 的 workflow-control 事务；standalone 使用原 checkpoint；不新增文件授权表或并行 ledger。

新 Stage-2 scope 保留 baseline、owned_paths、protected_paths，implementation_paths / closure_paths 改为受 lifecycle 管理的值：未定稿必须为 null；明确清单必须为排序去重的逐文件数组，空数组不再代表未知。新增 downstream 对象：state（unresolved / proposed / sealed）、revision、implementation_required、manifest、proposal_ref、seal_ref。unresolved 的 implementation_required 为 null、manifest 为 null；proposed/sealed 为显式 boolean 及完整 manifest。manifest 每项包含 path、owner（implementation / closure）、kind（source / test / generated / document）、operation（add / modify / delete）、basis（已提交 Spec/Ticket 的 commit、path、section）以及生成物的 source_paths。数组是 manifest 按 owner 派生的投影，不允许双写形成不同事实。

planning-owned 与 closure-owned 可重叠：Stage 3 保护所有已发布规划文件，Stage 4 只解除已封存 closure 文件的规划保护。冻结需求永远不能解除保护。implementation 与 closure 严格不重叠；Execution Agent 的分配只来自 implementation，彼此不重叠；Dispatcher 拥有未分配实现路径；Closure Agent 仅写 closure。Stage 2 不获得 implementation/closure 的当前写权限。

路径必须是仓库相对 POSIX 的精确文件，拒绝绝对路径、..、目录、glob、重复、大小写/文件系统别名冲突、符号链接及链接祖先、Git 元数据和保护冲突。新文件允许不存在，但校验其真实父目录与仓库归属；delete 要求基线存在。generated 项必须列出具体输出文件和源路径，不把目录或生成命令当授权。目标大小写行为用真实临时 Git 仓库测试；路径解析沿用现有 document_path / Git regular-file 检查。

### D2 — 提案、封存与发布顺序

新增 C scope-propose 与 scope-seal 操作，B 提供同一纯验证，不让直接 B 调用跳过封存条件。scope-propose 输入 proposal_id、expected_revision、candidate_commit、manifest、implementation_required、requirement_identity、binding、previous_scope_revision。设计者只能提案；当前 Controller 的 scope-seal 决定绑定 proposal_digest、原授权 reference、源/目标/工作区、规划候选及依据 blob 哈希、当前期望 target HEAD、当前范围 revision。决策语义为 in-requirement-refinement 或 reference-to-approved-change；后者必须有原变更决定，不能靠任意字符串扩大需求。

首次封存将 scope revision 从 0 增至 1；旧 unresolved 对象保留在 launch/history，不覆写原 handoff。C 保存当前 scope_authority，B 的新 effective handoff 引用该权威，并保留原 input_digest。每次基于同一输入的提案/封存重放 ACK；相同 ID 不同字节返回 scope_conflict。过期 revision、错误 Controller、漂移的需求/保护/绑定/规划候选、missing basis、未归属的 manifest 项均阻断。

Spec/Tickets 内保存逐文件清单和决策段落引用；含精确 commit 的机器 manifest 在规划候选提交产生后由设计者的结构化完成证据提供，C 从该 commit 读出依据并封存。不得把尚不存在的自身 commit SHA 写进同一提交的 Spec，从而制造自引用哈希。C 不替设计者猜清单。

顺序固定：完整 Spec/Tickets 和清单 → 设计者 clean planning candidate → Controller 范围核对/封存 → 原 stopped-writer / 未决调用屏障 → C publication → 原 receive-publication / accept → 生成 Stage-3 就绪投影。封存前不得发布新的 Stage-2 规划。发布遇合法非冲突 target 前移时，沿用 D 的单次合并重试，只重新验证目标、保护源和新 merge 的范围引用；记录 planning_commit 与 planning_merge_commit 两个事实，不伪造原候选。未知出版结果先 reconcile-publication，不能重新封存或换候选强行重试。

planning_complete 表示真实规划成果已接受；handoff_ready 表示 sealed 范围、非空必要实现、确切出版证明、停止屏障、原 Controller 接受及 phase_complete 全部成立。downstream_ready 是后者的兼容输出，不再由 accepted 自动推导。implementation_required=true 且 implementation_paths=[] 报 scope_incomplete；false 且非空则矛盾。明确 false 且空表示设计工作完成但本方案不产生 Stage-3 交接：planning_complete=true、handoff_ready=false、route=planning-only；不自动清理 worktree，也不另造空实现候选。closure_paths=[] 始终可以表达“无归档文档改动”，Stage 4 仍负责最终出版与清理。

附着讨论的顺序特别固定为：封存和出版已验证 → B accept-design 接受规划（planning_complete=true，此时 phase_complete 可为 false）→ 原 Phase Source Task 使用这份真实接受证据完成并 finalize Phase Run → C 回读 phase_complete → 生成 handoff_ready/downstream_ready=true 的当前投影。B 原 accepted 对象保持不变；Phase 完成不得要求 downstream_ready，接受也不得要求 Phase 已完成。后续入口只消费 C 当前就绪证明与 B 接受的配对，避免循环等待与旧 ready 布尔旁路。

B 的新 render/prepare 接口显式接收 readiness 对象（accepted_digest、scope revision/seal、publication facts、stop evidence reference、phase finalization reference、Controller/control revision）。C 从原权威构造，B 回查 Git 与该权威后验证，不接受调用方自报 ready=true。旧 immutable accepted 内不再承载可变化的下游就绪真值；B 重放 accept 只 ACK 原成果接受，C inspect/render 计算当前就绪。直接调用 B 没有这份可核验的 readiness 时只能得到规划成果，不能准备 Stage 3。

机械检查不能从自然语言发现任意漏文件。它校验已声明 manifest、生成映射闭包、Spec/Ticket 依据和数组的完整一致性；Controller 对需求内覆盖作明确语义决定；运行时仍拒绝清单外的真实 Git 改动。不能把摘要一致或非空当作覆盖证明。

### D3 — Stage 3 / 4 入口及独立入口

Stage 3 prepare 必须消费当前 sealed scope_authority 的精确 revision，并重验来源、规划、保护、工作区、当前 pin 和原 Controller 决定。其执行分配可以缩小 implementation 集合，但 stage-level ceiling 不缩写；这样未分配路径不会丢失。未定稿、必要实现为空、manifest 漏掉已有批准路径、陈旧封存或未完成事务均在宿主派发前拒绝，返回缺项 paths、原因及 scope-propose / scope-revise 恢复动作。

独立 Stage 3 没有前驱设计时，沿用自包含任务和原 Controller 授权，但同样要求 sealed 完整 manifest；basis 可指向该冻结实现输入的具体 requirement 段落，不强制补做 Stage 2。直接实现政策的既有授权要求不变；范围修订会失效其旧 scope_digest 评估，必须由原政策路径重新评估，不自动升级 full/direct。

Stage 4 使用 Stage-3 已验收候选绑定的范围 revision 与 closure 清单，不消费后来无关修订。需要修改实现时返回 Stage 3；不允许 Closure Agent 自行加路径。最终出版 allowed_paths 来自 implementation ∪ closure；protected_paths 按 D1 计算。

### D4 — 受控范围修订

C scope-revise 输入 revision_id、原 scope digest/revision、原 requirement identity、已发布 planning_commit/merge、binding、旧 manifest、逐项 added/removed/changed 差异及理由、Controller 决策。只补文件而不改变需求/已接受设计属于细化；变更业务或已接受设计须先走原 anomaly/change-decision，更新正式规划依据后再提交新提案。不能静默改 Spec 以解释越权路径。

持久状态为 proposed → stopping → quiescent → authorized → active；失败保留当前阶段和已完成证据；旧授权仍在 history，进入 stopping 立即关闭 allocation、native continuation、候选接受、出版和新阶段入口。没有启动实现时，核验不存在 launch intent / pending creation / host call 后可直接 quiescent。已启动实现时，冻结相关 Dispatcher、全部 Execution Agent、活动 reviewer 和直接作者的继续权限，使用 C 当前 stop/lifecycle-query/unresolved_host_actions/stop_barrier_pending 机制取得原生实况；停写意图不等于已停止，历史 completed 字段不等于当前证据。创建未知先 reconcile 原调用，不得以“无 ref”视为未创建。

quiescent 保存真实 Git HEAD、index、未提交/未跟踪相关文件的存在、模式、内容指纹，原范围及拟增路径都入快照；Git 已提交逐 commit 的路径改动也检查，防止增删抵消。修订不能追认在旧授权外已经产生的改动；发现它时保留文件并报告恢复决定，不用新清单使其合法化。删去/转交路径若有未接受工作、旧分配或必要行为尚未覆盖，则拒绝；新增同一 scope 内路径允许 absent。

authorized 必须绑定 snapshot、最新宿主 query/generation、完整 settled calls、当前 ledger/control revision 和 proposal；生效前重验同一快照。active 递增 revision，原 handoff/receipt 不改写；按新 revision 重建未完成分配，保留已接受且字节未变的成果引用。历史 candidate/review/verification 和审查 fixed point 原样保留，只撤销其对新 revision 的当前有效性；新的候选审查绑定新 scope revision，即使 SHA 相同也不能复用旧范围审查。已验收 Stage-3 候选若尚无 Stage-4 intent，则先由原 Controller 建立新的 Stage-3 revision attempt、保留原 accepted 后重新验收；若已有 Stage-4 writer 或 publication intent，先按现有恢复路由结清并回到合法 Stage-3 修复点，不在归档事务中原地修订。原 Dispatcher 可经原身份 continuation 恢复；不可恢复才走既有 dispatch recovery，范围修订本身不创建替代 Agent。直接作者按现有 direct resume 的停止快照恢复。

每个 revision_id 与完整输入摘要存于原 C event/decision 集合，恢复先重放已完成控制/宿主事务，再继续下一未完成步骤；陈旧异内容决定拒绝。并发操作用已有短 record lock + expected_revision CAS；附着 ledger 另用既有 expected_topic/ledger_revision 与 idempotency_key。C 必须先持久化意图，再调用 ledger/宿主，并先保存原返回再投影。中断时 reconcile 原 transaction，不重新创建/接受/发布。范围生效不是自动恢复用户暂停；既有 pause/cancel 意图优先。

### D5 — 旧 v12/v7 Stage-2 窄恢复

只支持 progress-v12、transfer-v7、control schema 4/key 6、runner outer 6（若存在）的明确组合；其他旧版本继续 original-runtime 错误。输入 original_checkpoint 的规范绝对路径、完整原始摘要、原 Controller/host 身份、旧 accepted delivery、规划候选/merge、冻结需求、同一 binding、旧 pins、补充 manifest 和新目标包 pins。由原 Controller 发起新运行的 scope-recover-legacy；这是显式 ADR-0008 例外，不是让新解释器恢复旧执行器。

恢复只在同一 checkpoint 进行。scope_recoveries 是追加恢复事务与历史快照成员，不是另一套活动投影。inspect 只读。prepare-takeover 在原 Controller 授权及停止屏障满足后，取得原 runner RunLock（适用时），再取得原 record_lock，在锁内重读 exact old digest，保存旧 checkpoint 完整原始 UTF-8 字节、SHA-256、规范文件身份及旧 pin/accepted 索引。快照只追加一次并永不覆写，所有旧接受及历史均可从原字节复原。

在同一原子文件替换中，将唯一 current workflow_progress 切为合法 v13 takeover-pending 投影；runner 的当前 outer 切为新 outer 7，新 confirmed/pins 明确属于补充授权的新运行。旧 outer 6/confirmed/sessions/pins 完整留在旧快照，不转换成新版证据。新投影尚无派发权限。旧 v12 handle 的现有 protocol guard、旧 runner 的 version guard 因而拒绝后续操作。这确实改变 current 指针与当前运行封套，不能宣称旧运行完全无状态变化；历史原貌与当前权威接管是不同约束。

接管前停止证据覆盖旧 designer、CLI carrier、相关子 Agent、runner host transport、未决业务/出版/接受调用和已发起的 Controller 写命令。原 Controller 可执行恢复编排，但不能仍有旧包写命令或 continuation 在途。拿到锁不能代替作者停止证据：必须先取得原请求身份关联的完成/终止响应，再在锁内重新核验。旧命令持锁则待其正常释放后重读；仍可迟到写回、原 host 不可查或 runner 持锁则阻断，不杀进程、不删锁、不把超时当终止。顺序固定为 runner RunLock → C record_lock → Topic ledger transaction；宿主等待在短 record lock 外完成。

read-only inspect 首先验证原 accepted Stage-2、downstream_ready 历史事实、真实规划 Git 出版和 frozen bytes/mode、同一工作区、原 package 根/digest 未变、原 host 的准确 child 身份及最新停止证据；原生 host 不可查询或原包缺失均阻断，不采信布尔量。检查旧 checkpoint 全部 history、runner stage result / sessions / calls、控制进度、附着 Phase Run：不能存在 Stage-3 prepared input、dispatch intent、宿主调用或实施分配；即使最终未创建或已取消也不属于本窄恢复。确认无未决出版、无未决接受、无未完成 Phase Run 事务；已有 accepted 规划不再调用发布或接受。

原 Controller 的补充决定绑定上述快照摘要、原记录身份、exact new manifest、同一需求/规划/工作区和新 pins；恢复使用新包正常 preflight/current registry。旧只读 verifier 接收冻结旧对象及真实 Git/host 证据；若调用旧包，只调用其纯验证函数或只读 API，不向新 current 路径调用旧 runner。旧包不可用或 digest 不符就阻断；新解析成功不能替代旧身份核验。旧宿主证据仍按原语义验证；新宿主重新 admission，不能把新 inventory 冒充旧宿主停止证据。

恢复状态 inspected → takeover-pending → authorized → consumed，key 为规范 checkpoint 身份 + 原 accepted digest；同一 key 只有一个有效补充授权。同内容重放 ACK；异内容须对未消费授权显式 supersede 并保留前版，current 不回退。消费与 Stage-3 launch intent 在唯一当前 v13 checkpoint 同次锁内原子保存，宿主调用在其后；lost response 只 reconcile 原 intent。消费后回归普通 v13 start/dispatch/accept/publication，不要求长期 recovery_id 选择另一路径；scope_recoveries 仅保存 provenance 与幂等索引。canonical checkpoint、原 Controller/current control 与原宿主来源共同确证身份，复制 JSON 不获得第二消费权。consumed 后只可用普通 scope-revise。

附着场景采用可恢复的两阶段接管，不假装跨文件原子：先保存本地 takeover-pending，关闭旧 C/runner；再在原 Topic workflow-control 事务保存旧完整 control 值和 checkpoint/snapshot/transaction 引用，将唯一 current control 切至 schema 5 takeover-pending；最后精确回读该事务并使两侧 active。每步绑定原 Controller、Topic binding、expected revisions 和 idempotency key。中断均无新派发权限：本地 pending/ledger 未变则重试原事务；ledger 已切换/本地未回执则按事务 ID 回读；冲突保留接管状态，不回退旧 current。旧讨论入口的 schema 4 guard 拒绝新 current。消费时 ledger 先按原控制事务保存 reservation，C 确认原事务后才落 consumed+launch-intent，未知窗口不能重复派发。Topic 必须仍在已完成 Stage-2 的 Phase 2，不能有 Phase-3 intent、未 finalize 旧 phase 或绑定漂移。

新 runner 从唯一 current outer 7 正常运行，明确从已接受规划的新交接进入 Stage 3，不复用旧 CLI sessions，不把旧 accepted 伪装为新版接受记录。新补充授权构造的是 scope handoff，旧 accepted 只是只读来源证据。后续 Stage 3 走原合法 Phase Run，新记录引用恢复来源；不改写旧 Phase Run。

旧包 preparation key 的 v3 与 v4 是两种明确源组合，分别以 fixture 验证；其他兼容键按原 pin 完整匹配，不靠默认补字段。当前设计执行包 pin 为 preparation-v3，仓库基线构建为 v4，这是既存不同版本，设计工作不修改执行 pin。

### D6 — 版本与兼容边界

新普通协议为 workflow-progress-v13、workflow-stage-transfer-v8、control compatibility key 7 / JSON schema 5、runner outer 7。新增范围 envelope 内部 version=1。entry-v3、preparation-v4（仓库基线现状）、requirement-freeze-v2、requirement delivery、thread-settings-v5、supervision flow-worktree-v2、discussion request 与 ledger 存储版本保持不变；它们的数据语义不需升级。控制嵌入数据以其 schema 5 校验，ledger 3 保留旧嵌入对象只读解释与历史，拒绝普通变更操作隐式转换。

五个生成包使用完全一致的新 compatibility_key。新普通操作遇旧 pin、旧 runner 或旧 checkpoint 一律拒绝；只有 scope-recover-legacy 的独立白名单入口能在通用 version guard 之前执行只读资格验证，再显式原子接管 current 并追加旧原始快照。该白名单不扩展到 pause/cancel/start/accept/publication。仓库构建配置作为兼容键唯一来源，显式声明 stage_scope 资源并放入五包资源闭包，生成物不手改。发行版本号更新、安装及现有注册更新不属于本任务。

### D7 — 变更契约预检与真实调用链

以下证据来自冻结基线；每行同时列出已经消解的矛盾。

| 生产入口与调用者 | 当前所有者/事实与 seam | 本设计解决的矛盾 |
| --- | --- | --- |
| C start → B prepare → entry refresh | stage_handoff.prepare 对四个数组一律 paths(empty=True)，启动 authorization.scope_digest 冻结整个 scope | Stage 2 改为 unresolved 下游范围，scope_authority 独立封存；入口授权仍只授予规划写入 |
| C receive-publication → B receive/accepted → accept-design | stage_dispatch.accepted 最后对除 Execution Agent 外所有角色 downstream_ready=true | accept-design 保留成果接受；就绪由 sealed 权威与全部门禁派生 |
| B Stage-3 predecessor prepare/render | stage_handoff.prepare 要求 implementation 子集且 closure 相等，render 从旧 launch scope 输出 | 消费封存/修订后 effective scope；旧 launch/history 不变；不以重算旧摘要授权 |
| C publication → supervision publish-planning | publication 保存意图、事实与 stopped barrier，D 支持真实 Git target race/reconcile | 在原出版前加封存检查，保留 D 的单次出版与恢复语义，不修改 Git primitive |
| C control/allocation/recover_business/resume | 原有 unresolved calls、stop barrier、原 host inventory/generation；workflow_control_git.recovery_snapshot 验证旧 allowed paths | 修订先停写；快照增加拟增路径与逐 commit 检查，但不追认越权既有改动 |
| runner _accepted_stage → C inspect / B render / verify_result | runner 当前读取 accepted projection，confirmed.authority_scope 是提前静态 ceiling | 新 runner Stage 2 用需求边界 + 当前 sealed 权威；不以启动时未知路径当 ceiling。独立 Stage 3 仍冻结其确切清单 |
| discussion_core.workflow_control → _write_ledger_transaction；phase_runs | Topic revision、Controller binding、Phase Run activation/finalization 为权威；C 投影只是缓存 | scope proposal 不自动完成 phase；就绪等 phase_complete；修订/恢复以原 Topic 原事务核验，没有第二 ledger |
| build_skills.build → 显式 roots/resources → 五 package.json | 清单从 build 配置读取，资源依赖通过声明闭包生成；preflight 对兼容键完全相等 | 新 stage_scope 显式映射、五包闭包、版本键和测试同步；不升级在途包 |

相关公开接口错误均返回 code、缺项路径、当前 scope revision、恢复动作和 downstream_ready=false；不回显敏感环境或完整宿主凭据。纯验证失败无持久副作用；跨 ledger/host 事务错误保留原成功步骤与 transaction ID。

## Testing Decisions

最高测试 seam 为 C workflow_progress.handle 的真实持久 checkpoint + B stage_handoff/stage_dispatch + 临时真实 Git worktree。宿主替身只供应与真实 adapter 契约一致、含原请求/响应、身份及 generation 的认证响应；不替代路径、Git、状态机或 Controller 授权。附着场景使用真实 discussion_protocol 和临时 Topic ledger；runner 场景使用现有 foreground 协议替身和真实 runner 记录锁，不调用用户真实记录。

| 验收/分支 | 可观察断言与级别 |
| --- | --- |
| 未定稿 / true+空 / false+空 / closure 空 | C→B 集成：前两者发布/派发前阻断；planning-only 无 Stage 3；closure 空仍正常 Stage 4 |
| 完整清单与漏项 | 已批准 manifest 中测试/新文件/生成物缺一项即报具体路径；完整闭包封存后正常接受/派发 |
| 越权及漂移 | 保护交集、owner 重叠、glob、symlink、目录、别名、错误基线/源模式/目标分支/包 digest 均无宿主调用 |
| 正常出版 | 非冲突 target 前移、未知 merge 结果与 receive-publication 重放仍只发布/接受一次；旧 launch scope 字节不改 |
| 未启动修订 | 空→完整与非空加测试文件；新 revision 生效、旧 input/decision 保存、旧 revision 入口拒绝 |
| 活动修订 | running/unknown/pending creation/旧 inventory 均不能生效；原请求停止证据和全部 settled calls 满足才保存授权 |
| Git 修订快照 | 已接受字节/模式/存在改变、已提交增删抵消、已越权新文件、未跟踪拟增路径冲突均保留文件并阻断；合法 absent 新文件可加入 |
| 分配和政策 | 旧 allocation/候选/review 失效，接受字节保留；direct 旧 scope_digest 不再有效，pause 不被修订隐式恢复 |
| 故障注入 | 每一持久意图/控制调用/宿主调用/结果保存/生效之间中断；恢复复用事务，零重复 dispatch/publication/accept |
| 旧恢复 | 合成删除记录同结构 v12/v7 Stage-2 accepted、empty/nonempty missing 范围；旧快照原字节/hash 保持、同一 Git/worktree、零重设计/出版、补充消费一次 |
| 旧恢复拒绝 | Stage-3 任一 intent/history/session、未 final Phase、旧 host 不可查、旧包变更、伪造摘要/Controller、复制 checkpoint、新 manifest 复用 ID 均拒绝 |
| 旧进程隔离 | 真实多进程锁测试：旧 runner 持 RunLock 时接管失败；旧 C 已持 record lock 时先完成并使旧摘要失效；旧命令在锁后才读取时被 v13/outer7/schema5 拒绝；不重放旧业务；旧快照能由原 verifier 在 current 切换后只读验证 |
| 附着两阶段接管 | 在本地切换前后、ledger CAS 前后和回执前后逐点故障注入；所有 pending 窗口零派发，回读原事务后一次性激活，历史原值不变；规划接受→Phase finalize→ready 顺序无循环 |
| runner / attached | 同一 C seam 两种 carrier；旧 outer 6 原字节留存、新 outer 7 接管；唯一 current 与 Topic CAS 断点恢复；过期 ledger/绑定拒绝 |
| 独立/连续/包兼容 | 独立 Stage 3 sealed 启动；continuous 不新增人为审批；五包独立导入/混合键拒绝/老普通操作拒绝/显式窄恢复成功 |

沿用 stage-transfer、workflow-progress、publication-progress/recovery、collaboration-lifecycle、unified-recovery、attached-flow-progress、parallel-runner、foreground-host 的真实 fixture；新增 test_stage_scope 只覆盖共享纯规则的难组合，避免重复实现断言。control schema bump 同时更新 requirement-delivery 中调用控制入口的 fixture。

实现时先跑受改动模块对应测试，再完整 repository validator、package build --check、tests/packages 与 tests/runtime。生成输出用临时目录比较精确 diff，不安装。当前阶段只核验文档、路径/闭包和源码事实，以上行为测试是 Stage 3 验收要求，不声称已经实现或运行通过。

## Out of Scope

不修模型切换；不迁移已进入 Stage 3 的旧运行；不支持任意旧协议组合；不修改真实删除记录、原对话或宿主证据；不重新设计已有业务；不全仓授权、不用目录通配；不创建新 worktree/外部 ledger/后台监控；不安装、部署、发布版本、推送或 PR。Stage 2 只写本 Spec、必要 ADR 与审阅后 Tickets。

## Further Notes

ADR-0011 明确覆盖 ADR-0008 的唯一兼容例外。下方文件清单是本设计所需未来改动及角色分配；本轮仍只有规划文档写入授权。路径清单是用户需求与本阶段交接的明确要求，因此在此列出，优先于 to-spec 不列具体路径的默认写作约定。


### 精确实现路径（Dispatcher / Execution Agent，未来 Stage 3）

implementation_required=true。以下逐文件集合是本方案的完整实现清单；所有 add/modify 都绑定 D1–D7 与 Testing Decisions，不包含任何目录授权。两个新增文件为 stage_scope 模块及其单元测试；五包对应 stage_scope 为新增生成物，其余为 modify。具体并行分配在 Stage 3 从这些路径内互斥切片，Dispatcher 保留整合集成职责。

```text
CONTEXT.md
build/skill-packages.json
scripts/validate_repository.py
skills/change-closure/SKILL.md
skills/change-closure/package.json
skills/change-closure/references/shared/entry-preparation.md
skills/change-closure/references/shared/guided-implementation/workflow-control-protocol.md
skills/change-closure/references/shared/package-execution.md
skills/change-closure/references/shared/stage-transfer.md
skills/change-closure/references/shared/workflow-progression.md
skills/change-closure/references/solution-design/conversation-source.md
skills/change-closure/scripts/discussion_core/phase_runs.py
skills/change-closure/scripts/discussion_core/state.py
skills/change-closure/scripts/discussion_core/workflow_control.py
skills/change-closure/scripts/entry_prepare.py
skills/change-closure/scripts/stage_dispatch.py
skills/change-closure/scripts/stage_handoff.py
skills/change-closure/scripts/stage_scope.py
skills/change-closure/scripts/workflow_control.py
skills/change-closure/scripts/workflow_control_git.py
skills/change-closure/scripts/workflow_progress.py
skills/design-discussion/package.json
skills/design-discussion/references/shared/entry-preparation.md
skills/design-discussion/references/shared/guided-implementation/workflow-control-protocol.md
skills/design-discussion/references/shared/package-execution.md
skills/design-discussion/references/shared/stage-transfer.md
skills/design-discussion/references/shared/workflow-progression.md
skills/design-discussion/references/solution-design/conversation-source.md
skills/design-discussion/scripts/discussion_core/phase_runs.py
skills/design-discussion/scripts/discussion_core/state.py
skills/design-discussion/scripts/discussion_core/workflow_control.py
skills/design-discussion/scripts/entry_prepare.py
skills/design-discussion/scripts/stage_dispatch.py
skills/design-discussion/scripts/stage_handoff.py
skills/design-discussion/scripts/stage_scope.py
skills/design-discussion/scripts/workflow_control.py
skills/design-discussion/scripts/workflow_control_git.py
skills/design-discussion/scripts/workflow_progress.py
skills/guided-implementation/SKILL.md
skills/guided-implementation/package.json
skills/guided-implementation/references/shared/entry-preparation.md
skills/guided-implementation/references/shared/guided-implementation/execution-protocol.md
skills/guided-implementation/references/shared/guided-implementation/originating-task-protocol.md
skills/guided-implementation/references/shared/guided-implementation/workflow-control-protocol.md
skills/guided-implementation/references/shared/package-execution.md
skills/guided-implementation/references/shared/stage-transfer.md
skills/guided-implementation/references/shared/workflow-progression.md
skills/guided-implementation/references/solution-design/conversation-source.md
skills/guided-implementation/scripts/discussion_core/phase_runs.py
skills/guided-implementation/scripts/discussion_core/state.py
skills/guided-implementation/scripts/discussion_core/workflow_control.py
skills/guided-implementation/scripts/entry_prepare.py
skills/guided-implementation/scripts/foreground_host.py
skills/guided-implementation/scripts/stage_dispatch.py
skills/guided-implementation/scripts/stage_handoff.py
skills/guided-implementation/scripts/stage_scope.py
skills/guided-implementation/scripts/workflow.py
skills/guided-implementation/scripts/workflow_control.py
skills/guided-implementation/scripts/workflow_control_git.py
skills/guided-implementation/scripts/workflow_progress.py
skills/guided-implementation/scripts/workflow_stage_result.schema.json
skills/problem-framing/package.json
skills/problem-framing/references/shared/entry-preparation.md
skills/problem-framing/references/shared/guided-implementation/workflow-control-protocol.md
skills/problem-framing/references/shared/package-execution.md
skills/problem-framing/references/shared/stage-transfer.md
skills/problem-framing/references/shared/workflow-progression.md
skills/problem-framing/references/solution-design/conversation-source.md
skills/problem-framing/scripts/discussion_core/phase_runs.py
skills/problem-framing/scripts/discussion_core/state.py
skills/problem-framing/scripts/discussion_core/workflow_control.py
skills/problem-framing/scripts/entry_prepare.py
skills/problem-framing/scripts/stage_dispatch.py
skills/problem-framing/scripts/stage_handoff.py
skills/problem-framing/scripts/stage_scope.py
skills/problem-framing/scripts/workflow_control.py
skills/problem-framing/scripts/workflow_control_git.py
skills/problem-framing/scripts/workflow_progress.py
skills/solution-design/SKILL.md
skills/solution-design/package.json
skills/solution-design/references/shared/entry-preparation.md
skills/solution-design/references/shared/guided-implementation/workflow-control-protocol.md
skills/solution-design/references/shared/package-execution.md
skills/solution-design/references/shared/stage-transfer.md
skills/solution-design/references/shared/workflow-progression.md
skills/solution-design/references/solution-design/conversation-source.md
skills/solution-design/references/solution-design/design-readiness.md
skills/solution-design/references/solution-design/subagent-protocol.md
skills/solution-design/references/solution-design/templates.md
skills/solution-design/scripts/discussion_core/phase_runs.py
skills/solution-design/scripts/discussion_core/state.py
skills/solution-design/scripts/discussion_core/workflow_control.py
skills/solution-design/scripts/entry_prepare.py
skills/solution-design/scripts/stage_dispatch.py
skills/solution-design/scripts/stage_handoff.py
skills/solution-design/scripts/stage_scope.py
skills/solution-design/scripts/workflow_control.py
skills/solution-design/scripts/workflow_control_git.py
skills/solution-design/scripts/workflow_progress.py
src/shared/references/entry-preparation.md
src/shared/references/guided-implementation/execution-protocol.md
src/shared/references/guided-implementation/originating-task-protocol.md
src/shared/references/guided-implementation/workflow-control-protocol.md
src/shared/references/package-execution.md
src/shared/references/stage-transfer.md
src/shared/references/workflow-progression.md
src/shared/scripts/discussion_core/phase_runs.py
src/shared/scripts/discussion_core/state.py
src/shared/scripts/discussion_core/workflow_control.py
src/shared/scripts/entry_prepare.py
src/shared/scripts/stage_dispatch.py
src/shared/scripts/stage_handoff.py
src/shared/scripts/stage_scope.py
src/shared/scripts/workflow_control.py
src/shared/scripts/workflow_control_git.py
src/shared/scripts/workflow_progress.py
src/stages/change-closure/SKILL.md.in
src/stages/guided-implementation/SKILL.md.in
src/stages/guided-implementation/scripts/foreground_host.py
src/stages/guided-implementation/scripts/workflow.py
src/stages/guided-implementation/scripts/workflow_stage_result.schema.json
src/stages/solution-design/SKILL.md.in
src/stages/solution-design/references/conversation-source.md
src/stages/solution-design/references/design-readiness.md
src/stages/solution-design/references/subagent-protocol.md
src/stages/solution-design/references/templates.md
tests/packages/test_build.py
tests/packages/test_cross_package.py
tests/packages/test_preflight.py
tests/runtime/test_attached_flow_progress.py
tests/runtime/test_collaboration_lifecycle.py
tests/runtime/test_entry_prepare.py
tests/runtime/test_foreground_host.py
tests/runtime/test_parallel_runner.py
tests/runtime/test_publication_progress.py
tests/runtime/test_publication_recovery.py
tests/runtime/test_repository_validation.py
tests/runtime/test_requirement_delivery.py
tests/runtime/test_solution_design_contract.py
tests/runtime/test_stage_scope.py
tests/runtime/test_stage_transfer.py
tests/runtime/test_unified_recovery.py
tests/runtime/test_workflow.py
tests/runtime/test_workflow_control.py
tests/runtime/test_workflow_control_git.py
tests/runtime/test_workflow_progress.py
```

### 生成闭包及所有权

原始规则只改 src，生成面只由 build_skills 产出。build/skill-packages.json 增加 stage_scope 资源映射及五包 roots，更新兼容键；现有 builder 的显式资源遍历无需增加新机制，scripts/build_skills.py 不改。上面的 generated 文件通过基线 resources/roots 与 Markdown resource 引用传递闭包求得，并加上五个新 stage_scope 与五个 package.json；不存在“只改源码、不授权生成物”的遗漏。implementation 负责 CONTEXT 的新术语以及 Skill 协议文本；closure 只改下面六个规划状态文件，不重复拥有实现文档。

### 精确归档路径（Closure Agent，未来 Stage 4）

```text
.scratch/stage-scope-finalization/PRD.md
.scratch/stage-scope-finalization/issues/01-scope-lifecycle.md
.scratch/stage-scope-finalization/issues/02-progression-and-revision.md
.scratch/stage-scope-finalization/issues/03-legacy-recovery.md
.scratch/stage-scope-finalization/issues/04-packages-and-contracts.md
docs/adr/0011-finalize-stage-scope-after-design.md
```

保护路径始终包括冻结需求；Stage 3 另外保护上面所有六个已发布规划文件。Stage 4 仅对这六个 closure 文件解除规划保护，冻结需求仍受保护。本轮规划权限只允许这六个路径，当前已创建 Spec、ADR 与四个 Tickets；四项拆分已审阅确认。

### 当前旧交接的机械缺口

本次运行仍使用原 v12/v7 pin，不能用拟设计的修订能力修复自己。完整方案所需清单相对启动时预留 ceiling 的缺项为：

- `build/skill-packages.json`
- `skills/change-closure/references/solution-design/conversation-source.md`
- `skills/design-discussion/references/solution-design/conversation-source.md`
- `skills/guided-implementation/references/solution-design/conversation-source.md`
- `skills/problem-framing/references/solution-design/conversation-source.md`
- `tests/runtime/test_requirement_delivery.py`

这些是同一已确认需求的实现关联文件，不构成新增业务。不得为了适配旧 ceiling 省略它们；不得修改原 checkpoint 或重算其授权摘要。当前可完成本轮方案审阅与规划交付，但不能宣称旧交接已经具备 Stage-3 执行条件，须由原 Controller 在进入实现前解决该明确机械限制。

本轮检查：冻结需求 commit/hash/mode、实际 cwd/Flow binding、原包 digest、spec/adr capability preflight 均通过；调用链已按 D7 核对。上述精确路径共 146 个（源/测试/构建 50，生成 96），归档 6 个，角色交集为空。方案已审阅通过；四个 Tickets 的拆分与依赖亦已审阅通过；六份规划文档作为同一规划提交交原 Controller 发布到本地 main。

### Tickets 决策与追溯（tickets-review-20260926-01）

ask-matt 路由结论：采用 to-tickets。正常封存、活动修订、旧接管和包级综合验收各有完整可观察结果，适合多会话实现；不再增加 prefactor-only Ticket，不引入额外抽象。

| Ticket | 决策与验收追溯 | 真正依赖与测试 seam |
| --- | --- | --- |
| 01 范围定稿与正常交接 | D1/D2/D3、D6 正常新协议；未定/空/完整/越权与漂移；正常出版、独立及附着入口 | 无；真实 C→B→Git，正常 runner/Topic 流程 |
| 02 受控修订与停写恢复 | D4；未启动/活动修订、Git 快照、分配/direct、历史审查固定点、故障注入 | 01；C scope-revise + 原生 stop 证据 + Git + Topic CAS |
| 03 旧 Stage-2 一次性接管 | D5、D6 兼容例外；旧只读验证、RunLock/current fence、消费幂等与 cross-ledger 恢复 | 01；旧包 fixture + 真实多进程锁 + 新 C 入口 |
| 04 五包与跨入口契约验收 | D6/D7；正常/修订/接管组合、生成闭包、注册兼容、用户 Skill 文本完整同步 | 02、03；五包构建校验、runner/附着完整回归 |

01 必须带正常生产路径、相应版本及资源闭包和必要文本一起完成；02/03 同样随实现更新对应生成物，不留坏包到 04。04 负责完整发行面的组合验收与剩余跨流程文档同步，不代替前面切片的功能验证。02 与 03 只有语义依赖 01；它们会修改共享生产文件，Dispatcher 应串行整合或分配精确非重叠文件，不能仅因无阻塞边就并发覆盖。全部 Ticket 继承本 Spec 的 146/6 精确集合，不能扩大权限；六项旧 ceiling 缺口在真正实施前仍须解决。
