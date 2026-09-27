# 设计后封存阶段范围

Status: revised — 2026-09-27 用户确认收缩范围

## 当前决定与完成条件

本次用户决定取代旧 D4 的活动 Stage-3 修订、全部 D5 旧交接接管及其组合验收要求。保留未知范围 → 提案 → 封存、planning_complete 与 handoff_ready 分离，以及尚未进入 Stage 3 的范围校正。

原 Controller 仅可在已接受 Stage 2 且从未保存任何 Stage-3 preparation、launch intent 或 session 时校正范围；已经取消或尚未创建 writer 的记录也不能豁免。拒绝时保留原阶段、接受结果与写入权威，不自动回退，不创建新的 writer attempt。业务变更、宿主与 Dispatcher 恢复继续采用原有路径，且不能扩大封存范围。发送前只读生命周期查询的锁忙延后行为继续保留。

当前协议为 workflow-progress-v15、workflow-stage-transfer-v10、control compatibility key 9 / JSON schema 5、runner outer 9；其余协议版本沿用。旧记录只由原固定运行时处理，当前包不提供接管、补充桥接、预留或替换补充决定的入口。五包由源码构建，精确兼容键及资源闭包同步；不修改冻结需求。

## 保留的设计

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

Stage 3 prepare 必须消费当前 sealed scope_authority 的精确 revision，并重验来源、规划、保护、工作区、当前 pin 和原 Controller 决定。其执行分配可以缩小 implementation 集合，但 stage-level ceiling 不缩写；这样未分配路径不会丢失。未定稿、必要实现为空、manifest 漏掉已有批准路径、陈旧封存或未完成事务均在宿主派发前拒绝，返回缺项 paths 与原因；仅在没有任何已保存 Stage-3 边界时可回到范围提案或校正。

独立 Stage 3 没有前驱设计时，沿用自包含任务和原 Controller 授权，但同样要求 sealed 完整 manifest；basis 可指向该冻结实现输入的具体 requirement 段落，不强制补做 Stage 2。直接实现政策的既有授权要求不变；Stage 3 前范围校正后按原政策校验新的 scope_digest，不自动升级 full/direct。

Stage 4 使用 Stage-3 已验收候选绑定的范围 revision 与 closure 清单，不接受后续扩大范围。需要修改实现时返回 Stage 3；不允许 Closure Agent 自行加路径。最终出版 allowed_paths 来自 implementation ∪ closure；protected_paths 按 D1 计算。

### D4 — 仅 Stage 3 前范围校正

校正绑定原 Controller、需求、已接受规划提交、Flow binding、原 revision/digest、完整新清单、逐项差异和理由。生成文件映射继续验证。需求或已接受方案变化先走原业务决定与规划出版。原身份、Git 与出版证据通过后才封存校正结果；相同请求重用保存结果，异内容重放拒绝。任何已保存 Stage-3 边界直接拒绝，不启动停写、回退或新实现 attempt。

旧 D4 中活动执行者停写后修订、已接受 Stage-3 新 revision attempt 和附着活动修订 CAS 计划已撤回，相应未完成验证不再是待办。

### D5 — 已撤回

旧方案曾为 v12/v7 已接受 Stage 2 设计一次性兼容接管，实施中还加入补充决定替换和附着预留。用户现已明确移除此能力；ADR-0011 的 ADR-0008 兼容例外同时撤回。旧方案及历史测试不再构成实现授权，禁止据此恢复已删除能力。

## 当前验证要求

验证正常提案/封存、规划接受与就绪分离、Stage 3 前校正，以及保存 Stage-3 prepare/intent/session 后无写入拒绝。保留业务/宿主/Dispatcher 恢复和锁忙延后回归；检查五包自包含及版本一致。移除能力只保留拒绝测试，不要求旧兼容正向验收或活动修订故障注入。

本次删减已完成。`./scripts/validate.sh --jobs 2` 退出码 0：51 个测试文件、943 个用例，942 通过、1 个既有跳过，failures/errors 均为 0；五包构建一致性、Skill 校验和 repository validator 全部通过。保留的范围补齐测试覆盖连续两次补齐、Controller receipt 保存后中断重放，以及补齐后以最新范围进入 Stage 3。旧接管与活动修订在源码、生成包中已无生效入口，普通恢复及锁竞争回归通过。

以下仅为撤回前的历史记录。本次未进行真实 checkpoint 迁移、本机安装或部署。

## 历史实施与验证记录（撤回前）

实施从 e24136f 基线继续，该基线已是 progress-v13、transfer-v8、control key 7/schema 5、runner outer 7；当时该修复代码与五包构建配置采用 progress-v14、transfer-v9、control key 8/schema 5、runner outer 8。原 v12/v7 checkpoint、原 pin 与 ADR-0008 限制仍是历史事实；这次只修改项目实现和合成测试记录，不接管用户的真实旧运行。

当时源码加入共享 `stage_scope`，B/C 的范围提案、封存、当前 readiness、受控修订与旧 Stage-2 白名单接管，以及 runner/附着 Topic 的相应入口。生成物使用 source→outputs 清单逐文件校验；需求外变更需引用原 Controller 持久用户决定。旧接管保留原 checkpoint 字节与 pin，在同一路径创建当前补充授权；附着 Stage-3 派发前还要在原 ledger 预留具体 Phase Run。已接受规划的 `planning_complete` 与可派发的 `handoff_ready` 分开。

当时源码与五个生成包已同步。`./scripts/validate.sh --jobs 2` 完整执行 56 个模块、989 项测试，发现 1 项旧分配 fixture 错把新 HEAD 的文件操作当作原封存清单；修正该 fixture 后 `test_dispatch_recovery.py` 13/13 整组复测通过，生产代码未因此改动。其余模块均通过；另补充 `test_stage_scope_revision_edges.py` 6/6 和 `test_stage_scope_legacy_locks.py` 5/5。最终覆盖 58 个模块、1,000 个用例，999 项通过、1 项既有跳过。初次全量命令的退出码为 1，后续失败模块复测和独立构建检查、repository validator 均为 0；不把初次运行记为一次全绿。validator 报告 5 个 Skill、81 个引用、58 个测试文件且 issues 为空；`build_skills.py --check` 与 `git diff --check` 通过。

补充验证包含真实 prep-v4 无 confirmation 历史包、真实多进程 RunLock/record-lock 竞争、唯一接管和旧进程迟到写入拒绝，以及未结清执行分配、运行中 reviewer、在途文件删除/转交、Stage 4 已启动和旧 direct 策略复用的拒绝。范围模型将 146 项同依据清单的 Git 调用从 588 次降至 9 次；同次接受复用已验证的就绪结果。前台只对发送前的只读生命周期查询锁忙延后，已发送请求及其他错误不被吞掉，4 项确定性测试与前台 31 项整组均通过。

历史未验证边界：真实附着旧 schema 4 正向恢复缺少带原 WC 链的历史 fixture，当前已验证缺链记录被拒绝和真实临时 Topic 的组合事务；没有执行用户真实 checkpoint 接管、本机安装或现场宿主验收。D4 各持久化边界的逐点故障注入和附着修订 CAS 全链验证尚未穷尽，这些未完成项现随能力撤回而关闭，不再驱动后续实现。

## Tickets 状态

01 保留正常范围生命周期；02 缩减为 Stage 3 前校正，旧活动修订待办撤回；03 整项撤回；04 只验收保留契约和已移除入口的拒绝行为。冻结需求文档保持原样，当前用户决定对本次实现范围具有优先效力。


## 删减后的最终审核修复（2026-09-27）

最终审核发现并修复三处边界问题：

- 通用 `control` 可绕过专用范围入口提交不匹配的回执。现在在写入前拒绝 `control / scope-seal|scope-revise`，范围决定只走专用入口；中断后的合法补齐仍可继续。
- 流程与交接读取未完整拒绝旧 runner 封套。两处均只接受 standalone 或 runner 9，版本 7/8 拒绝且 checkpoint 不变。
- 逐阶段模式下校正范围后未同步阶段确认。现在按是否需要实现维护待确认项，同一 accepted design 的待确认 ID 和已完成确认保留，不跳过确认也不重复询问。

新增回归先复现问题再验证修复；本轮相关模块合计 93 项通过（范围 18、前台 31、交接 14、scope runner 9、workflow 17、锁竞争 4）。此前 943 项全量回归属于删减提交；本次对受影响模块复测，未把它记为新一轮全量。五包重新生成并执行一致性与仓库校验；未安装或迁移真实任务。
