# 分层审查、局部返工与有界状态记录方案

Status: paused — 当前交付范围已收缩
Design status: accepted with scope amendment — 用户要求收缩剩余工作；不恢复实现
Plan revision: 2026-10-07 — 保留 T01–T04 和 T05-A；剩余只做核心闭合与必要交付验证
Source requirement: docs/requirements/2026-10-05-review-state-efficiency.md
Frozen requirement commit: 61f105f1d8bdb3ebc1b574e3104332621dc31ec8
Frozen requirement SHA256: dcc2adf2141dd49a9d383fc30a62388d267fb35e58fdddbdad1824d367c2dd77

用户先前授权普通本地连续执行六张 Ticket，随后要求暂停，并明确要求“收缩一下吧”。当前范围以本次修订为准，只有方案与管理记录调整已恢复；实现须等用户明确恢复。不授权安装、部署、远程发布或旧运行迁移，不补造正式 B/C Handoff。

## 当前交付边界（2026-10-07 收缩修订）

目标保持为局部→Ticket→交付审查、原上下文修复，以及固定有界的当前记录与按引用恢复。保留 T01–T04 原验收和 T05-A 在 `526c55cfe7f265a3ef2f39770f4315fc7883475f` 的局部验收，不重跑或重标未受影响证据。T05-A 不等于原完整 T05 通过，最终完整仓库验证和交付审查尚未完成。

剩余只有两步：

1. 恢复后先保存并隔离未提交的 T05-B 六文件 WIP，确认核心候选只含已审查成果；按修订后的 T05 合同核对归档、精确重放、原权限/恢复和读取预算，复用有效 A 证据，仅补实际缺口。
2. T06 修复已确认的原始测试夹具，闭合已有支持路线、协议文档和五包生成一致性；完成最终候选的完整验证与两轴交付审查，并据既有日志报告测量限制。

本轮移出：自动 GC/两代扫描、整个 run 的 2GiB 物理配额与双预留/log spool、新增账本物理归属核算、真实配额压力/ENOSPC 专项、10000 历史片边际增长基准、完整原生宿主现场实验、正式旧新效率对照及为此新增的测量工具功能。它们延期且未经完整验收，不自动排入下一轮；后续须另行确认范围。已有 CAS/原 intent/停止/unknown/IO 拒绝语义和固定 root/subject/证据/管理预算仍保留。

未提交 B WIP 和原日志保留，不在本次规划修订中删除、恢复源码、生成包或运行测试。其隔离必须先于恢复后的构建、核心验收和交付，不能靠未经审查 B 的一次局部测试通过将其纳入候选。

冻结需求是历史来源，不改写原文件/hash。当前验收采用用户后续收缩修订；延期的旧需求明列为未交付，不宣称原完整需求全部完成。旧方案保存在 Git `35d5a57876badce5a4beb01c99ac2a5db62c404e`。逐文件历史范围与当前剩余限制见 [manifest.json](manifest.json)，执行规则见 [execution-policy.md](execution-policy.md)。数值除现状测量外均须由实际证据验证。

## Matt 基础兼容更新（2026-10-07）

在原六 Ticket 实现暂停期间，先同步 main 的 `70822bc`：静态上游基线为 Matt `1.3.1` / `dd400c3ad65e57c06f05e832e0aac92c7992f34d`，本项目词汇表改用 `GLOSSARY.md`；消费项目按其约定选择词汇表，已有 legacy `CONTEXT.md`/`CONTEXT-MAP.md` 不因升级被重命名或产生第二份词汇表。生成包采用同一 canonical 来源，保留本分支 execution-plan-v2、control12/schema7、progress-v20/transfer-v15/runner11。

这一兼容更新不改变分层审查、局部修复、共享 Git 短屏障、固定当前预算或六 Ticket 依赖；T01/T02 原验收仍保原 source/report，追加兼容增量验证，不重标旧证据。T06 的领域文档目标改为 main 已提供的 `GLOSSARY.md`。main 的登记/入口回读/十项 action preflight 记录仅说明其当时包和宿主；不能冒作本分支新包完整流程、原生交接或效率改善的证据，T06 核源码依赖和候选包身份；当前宿主注册与完整原生现场验证延期，不能由历史 preflight 推断通过。

冻结需求文件及 hash 保持不变。以上记录此前基础适配；当前进度与剩余工作以收缩修订为准，不部署、不迁移旧运行。

## 执行成本修订（2026-10-07）

此前执行成本修订保留。T01–T04 与 T05-A 原验收和 source/report 保留；本次只改剩余方案与管理约定，不恢复实现。剩余实施统一遵循 [执行规则](execution-policy.md)：开工前核对完整行为与读写耦合、按 AC 分配三层审查职责、用真实模块公开边界快反馈与少量真实流程组合验证、按确认的返工触发短诊断、维护唯一有界当前入口。T03 起已有的计量只在 T06 汇总，缺失来源明确标 unknown；必要协议增量若受影响须明确复验，不重标既有通过证据。

本次普通本地改造由主控直接协调实现者及独立 reviewer，只有文件/依赖可隔离且确有收益时并行，减少串行转交；产品内既有 Controller/Dispatcher 权限和停止证明照常核验。Function Execution Plan 是实现说明，不直接决定 Agent/审查/提交数量。内部工程修订沿既有权限流程执行，不另建 Ticket、审批层或服务。

## 问题与目标

函数级计划本身不是失败根因。现有流程把独立审查集中到完整候选，替换候选又重跑两轴；正式计划修订虽能保留部分结果，仍同时退役候选、审查和验证。检查点又把控制上下文重复放入每个 transaction、allocation 和历史项。结果是局部修复引发广泛协调，恢复和模型阅读随着累计历史膨胀。

新流程继续保留 Implementation Ticket → Function Execution Plan → 行为执行片，但将审查职责分为局部、Ticket、交付三层；完整交付依据由有效局部证据、Ticket 验收、交付审查和最终完整验证共同构成。当前控制记录只保存当前事实和引用，历史、原始证据退出活跃读取范围。

## 源码事实与真实生产调用链

1. `workflow.py.start/resume/_advance_in_host` 驱动原生宿主，`_live_progress/_atomic_save/_stage_projection` 与外层 runner checkpoint 交互；不能只改独立 CLI。
2. `workflow_progress.handle` 持 `record_lock`，当前 `read_record` 全量 `json.load`，`validate_state` 遍历历史；`Progress.save` 增 revision 后 `atomic_save` 全量 pretty JSON、fsync、replace。`_view` 返回摘要，但构造摘要之前已读全量记录。
3. `Progress.allocation/issue_allocation` → `stage_dispatch.prepared/bound/received/accepted/checkpoint` → `workflow_control_git.verified_transition` → `workflow_control.transition/_transition_single`；`Progress.control_action` 另保存完整请求、`port=deepcopy(control)`、完整 result.context。allocation 还保存 input/handoff/record/result。拆文件后若原样 hydrate，这个问题仍存在。
4. `stage_dispatch.checkpoint` 在 standalone 用纯控制/Git 适配；有 discussion port 时调用 `discussion_protocol.handle` → `discussion_core.workflow_control.workflow_control`，其 ledger 内工作流控制 context 是权威，runner 副本只能作为观察。Stage 2+ attached 路线也可能采用调用方控制加 Phase Run 生命周期，按实际 port 判断，不能按 attached 标签猜测权威。
5. `execution_plan.validate_plan_refs/current_accepted_steps/affected_plan_closure/classify_retained_results` 已提供计划、依赖和指纹基础；复用其语义，补充审查证据图，不能仅比较函数名。
6. `Progress.prepare_plan_revision/advance_plan_revision/commit_plan_revision/activate_plan_revision`、`workflow_control_git.plan_revision_snapshot/verify_plan_revision` 已要求共享 HEAD/index 停止快照；`plan-revision-activate` 目前把旧 allocations 和 reviews 退役。
7. `workflow_control` 的 `candidate-ready → reviewing → review-converged → final-validation-pending → validating → deliverable` 与 `validate_review_axes/validate_deliverable` 当前绑定完整候选。`stage_handoff.accepted_stage3_progress/verify_result`、Stage 4、runner result schema 都消费此契约，必须一起更新。
8. 五个 generated Skill 包通过显式资源闭包生成。当前版本 1.3.0 / progress-v19 / transfer-v14 / control key 11、schema 6 / runner 10。共享运行时实际进入五个包，不能只改 guided-implementation 副本。

| 样本 | 可核实测量 | 解释与限制 |
| --- | --- | --- |
| 旧生产 progress-v17，pausing rev1520 | 文件 325,086,167 B；compact 208,958,218 B；allocations 118,680,142 B/37项；control_transactions 70,478,781 B/27项；native_finalizations 12,813,834 B/41项 | 病理诊断样本，不是正常基线。最大 allocation.result 2,939,645 B；最大 transaction.result 2,076,562 B、port 2,076,105 B，揭示重复控制快照。 |
| 新机制真实小样例 progress-v19，accepted rev417 | workflow.json 39,186,469 B；compact history 23,834,227 B、baseline_thread_queries 2,411,299 B、last_result 945,904 B、dispatch 632,132 B、native_finalizations 440,068 B | 31 项测试的小样例仍膨胀；不能把问题归于旧 v17，亦不能推断一般任务成功率。 |

以上由主 Agent 对既有证据只读测量提供；本方案不载入或改写旧运行。测量来源与单位在后续基准报告保持一致，pretty/compact 不混比。

## 用户故事

1. Controller 希望每个行为片及时被独立审查，避免全部完成后才集中返工。
2. Execution Agent 希望在原上下文修复明确缺陷，只接收当前有效合同和受影响事实。
3. reviewer 希望知道已覆盖内容与增量，避免重读整个交付 diff。
4. Dispatcher 希望只有通过局部回归及审查的结果进入 Ticket 验收。
5. Ticket 验收者希望检验组合行为和所有 AC，不把函数片全部通过等同 Ticket 通过。
6. Originating Task 希望最终候选有完整有效证据，同时只审查跨 Ticket、遗漏与风险。
7. Controller 希望内部修订保留无关成果，不重新询问既有合同内工程选择。
8. 恢复者希望按引用找到原调用结果，不把未知结果误当失败重发。
9. 运行使用者希望数千次相同轮询不撑大活跃状态，也不吞掉一次身份或停止状态变化。
10. 维护者希望完成历史可归档、证据可核对；本轮保留原证据，不自动删除。
11. 老任务使用者希望保持原包、原恢复方式，不被新格式接管。
12. 决策者希望比较质量和时间，不以减少检查或一次测试通过宣称提效。

## 设计决策：三层审查与状态协议

### 行为片、角色和最少上下文

一个行为片选取一个 Ticket 的一组紧密相关 plan_refs，拥有精确写文件分配、合同摘要、相关只读依赖、verification_refs、公共边界和失败行为。派发前按 [开工和划片](execution-policy.md#开工和划片) 核验独立验收、后片改写和完整生产消费者；强耦合步骤优先合片，超过上下文预算时沿可验证行为串行拆分并明确累计补审点，不按函数/行数机械增加 Agent。

每个行为片默认一个新的 Execution Agent 上下文，只在该片内修复时复用原上下文；一组紧密相关步骤可作为一个行为片，不把整个多片Ticket塞入一个执行对话。每个活跃Ticket默认一个独立 reviewer，按有限摘要复用；摘要超预算或独立性失效时轮换，禁止累计全项目历史。同一 Ticket 并行片确有独立收益时可分配多个执行者；文件分配必须不交叠，reviewer 按片读取。reviewer 不得是该片任何版本的实现者，也不得写实现字节。同一个 reviewer 可在完成全部局部审查后做 Ticket 验收，不再额外新建验收 Agent。交付保留两个独立轴 reviewer，均不可为本次实现者；可复用已有独立 reviewer，不强制每次新建。无工作变化时不重复派任何 reviewer。

原 Execution Agent 已停止且可继续时，使用原 native ref 的新 invocation/lease 继续修复；只携带 findings、当前 plan/authority 引用、上次片摘要、直接依赖变化及必跑测试。原宿主不可用、上下文不可信或容量超限才换执行者；新上下文按相同引用重建，不能把未知原调用作为已停止。reviewer 的未解决 findings 和覆盖摘要跨 turn 保留，历史原始输出按需取。

| 层 | 何时/谁 | 必做 | 不重复做 |
| --- | --- | --- | --- |
| 局部 | 每个行为片回归通过后，一个独立 reviewer | 当前变更的 Standards、局部合同/失败分支、相关读写范围和回归证据；findings 可定位到步骤/路径/AC | 整个 Ticket 或整个候选全部 diff |
| Ticket | 全部片有效后，同一独立 reviewer | 完整 AC、组合/顺序/共享夹具、公共输出、依赖 Ticket 合同；运行 Ticket 行为验证 | 已有效覆盖片的逐行规范复审 |
| 交付 | 全部 Ticket 有效后，Standards/Spec 两轴 | Standards 查跨 Ticket 一致性/共享状态/未覆盖文件；Spec 查所有需求→AC→证据覆盖、跨 Ticket 场景和影响扩大 | 重新扫描已证明未变的局部实现 |

每个 AC/必要失败分支在首次提交前指定首次检查层与 verification_ref；再次检查记录触发原因及影响范围，具体规则见 [审查职责](execution-policy.md#审查职责)。同 reviewer 可连续处理单片 Ticket 的局部与组合验收，结论仍保持各自 scope/原生动作/停止回执，不能重标报告或省掉 Ticket AC。

交付集成 delta 为空也必须首次执行需求覆盖核对、共享状态和目标分支一致性检查；可以产生“无新代码审查范围”的明确证据。后续候选只含无关已证明不影响的变化时继承既有交付结论；最终完整测试仍绑定最终候选。不能凭空生成空 review 或用 Dispatcher 自我验收替代独立审查。

局部review必须读稳定快照：该片执行者先停稳，Dispatcher核对owned/read指纹并生成只读不可变代码blob或已提交tree引用，reviewer只读该集合；采集前后再次核对全部指纹，其他writer改变依赖时废弃本次快照并重采，禁止混读两个版本。无需为只读review提交每片Git；采集与权限释放用已有文件分配/停止证据协调。

### 证据的内容与有效性

每份不可变 Evidence Claim 包含：run/package identity、claim_id、层/轴、Ticket/plan_refs、contract_digest、authority revision、source commit/tree、owned 与 read 文件指纹（含不存在、mode、symlink type）、公共合同摘要 hash、依赖 claim IDs、verification IDs及命令/环境/夹具摘要、reviewer/native receipt引用、发现与解决引用、结果。原始输出独立 blob；claim 不嵌套上游全文。

三个不同身份不可混用：commit 是交付固定点；claim 的 subject fingerprint 证明局部适用；authority revision 证明当时权限。继承只创建小型 applicability 记录，列原 claim、目标候选、比较过的指纹和依赖、检查算法版本；不修改旧 claim，也不把旧 commit 标签改成新 commit。

失效从变化种子开始：写/读指纹变化、plan 语义或验证变化、AC/公共合同变化、依赖结果变化、未解决 finding、身份/回执不完整。沿显式“读取/调用/共享状态/验证夹具/依赖 claim”边做反向传递闭包，传播至消费者、Ticket 和必要交付 claims。没有可证明覆盖的读取/依赖、动态共享注册、无法界定影响时，保守扩大到 Ticket；跨 Ticket 公共合同/全局配置/共享状态未知则扩大到交付。声明只是一项证据，必须与 Git 变更和计划索引比对；不能以执行者声称“无影响”保留结论。

同一文件内两函数无法仅凭文件 hash证明互不影响，因此本版本按文件保守失效；不引入 AST 语义等价证明。相同文件内紧密步骤优先合为一个行为片；后续片仍改同文件时，可在Ticket闭合前一次补审这些片的累计增量，但受影响片在补审前不可声称当前有效。效率可能低于文件隔离好的任务，计量必须显式报告这种重审；单纯commit变化而文件/依赖指纹不变不触发失效。合同/行为范围改变返回用户确认；单纯测试修复不是自动合同豁免。最终 coverage certificate 流式遍历固定 Ticket 列表，每个 AC 需可达一组仍适用的通过 claims，无遗漏文件、未解决 findings 或未知调用；保存摘要和引用，不复制所有证明。

### 测试提前、修复和候选生命周期

`allocated → implementing → local-checks → local-review → local-accepted → ticket-checks → ticket-accepted`；有 finding 返回同一 executor 的 repair invocation。局部改动重跑对应 regression + affected checks 并复查增量；原 reviewer明确关闭 finding且刷新适用证明。达到 [返工触发条件](execution-policy.md#返工触发短诊断) 时先处理测试基线、合同或划片问题，再继续当前片，不增加补丁片或削弱门禁。局部首次通过率的分母是**所有第一次提交的片**，修复不能重建片ID改善指标；计划内 RED 与测试准备错误单列。

共享夹具、公共接口或整合两个以上片时立即跑 Ticket/affected integration checks，最迟在 Ticket 验收前完成；不等待所有 review 收敛。初次可评审候选必须已有这些检查结果。最终完整验证仍在交付覆盖收敛后运行；失败后只修复受影响片，保留可证明未影响的 review，完整验证在新最终候选重跑，不继承旧完整 pass。

新的 candidate-ready 接受“Ticket 均有效 + 提前集成已通过”；review-converged 接受带适用证明的三层覆盖而非每份报告都必须原生绑定同一完整 commit。验证运行时任何影响源快照的写入都禁止；暂停/未知命令保留 attempt，确认命令停止和结果后才能换候选。旧 validation_history 变为不可变引用。

## 计划修订的真实安全边界

A. 函数体内等价实现、无步骤/文件/依赖/验证合同变化：Execution Agent 在原授权文件内处理，不新建计划修订。

B. 内部计划修订：原 Controller 计算候选闭包和精确 planning_paths，提早准备文稿；正式写入/提交时仍须所有共享 Worktree writer 短停，确认原调用、读写 lease、验证命令和原生 stop 证据；未知调用阻止放行。原因是 HEAD/index 是全 worktree 共享状态，按文件锁或仅停止受影响 Agent 不能保证规划提交安全。文稿思考可在停机前进行，只有授权写入、Git提交、authority切换处占屏障。

冻结原 HEAD/index entries/允许及保护文件指纹后，只给 Controller 规划文件写权限；使用现有 verify_plan_revision 检查唯一父提交、精确 delta 和未改 index 分区。一次 CAS 激活新 Scope Authority，撤销旧写 lease。未影响步骤、验收、review claims按第上节比较继承；新 lease引用新 authority，原上下文继续。受影响片保留源码但必须复核/修复，不抹掉其停止和调用历史。

C. 公共合同、用户可见功能或业务责任变化：保持停止/未派发状态交用户确认；不能用 B 偷改冻结需求。

与 ADR-0013 一致保留全 writer 短停，但修改其“全部候选/审查退役”的执行效果为选择性失效。不承诺消除共享 Git 屏障。测量 barrier 从“请求停止”至“新 lease可发”与“实际写/提交窗口”，另报未知调用等待；不得混入方案思考时间宣称锁持有时间。

## 状态、事件、证据：一个权威，三个职责

新增内部深模块 `workflow_state_store`，由现有 Progress/runner/ledger adapter调用，无后台进程、无通用数据库服务。接口为 `open_current(owner, expected_revision)`、`read_subject(kind,id)`、`read_evidence(ref, limit)`、`commit_current(expected_revision, mutations, receipts)`、`compact(scope)`。返回有限投影和稳定引用，不提供默认“读取全部历史”功能。新增 `review_evidence` 模块拥有 claim 校验、影响闭包、适用性和 coverage certificate；纯控制状态机只消费对应操作已验证的有限 subject view。

- **Current Root**：run身份、固定包、Controller、store格式、revision、当前阶段、工作批次ID、活跃 subject 引用、未决操作引用、当前 Scope Authority 引用、最近提交根、容量账。它是现有权威记录的一部分，不是另一个控制权威。
- **Subject Record**：固定按 Ticket（无 Ticket阶段按 stage attempt）和 batch 拆分。各自保存当前执行/审查/验证状态、必要调用摘要和原证据引用；已完成且停稳的 subject 从 root退出，仅在 archive目录按ID定位。
- **Event Segment**：有意义转换和事务事实，按 batch 固定容量分段。相同 poll 的同一 native identity/status/cursor/result_digest只更新当前 last_seen/count，不追加原文；身份、状态、停止证明撤销、unknown→known均是新事件。已解决的纯只读poll不需要逐次永久保存完整receipt：按同一逻辑query的identity+cursor+结果摘要保存首次/最近响应与计数，调用只有在未决期间保留exact call ID；非幂等副作用和停止/权限证明回执不可按此规则合并。旧poll重复请求只返回当前观察ACK，不冒充某次副作用完成。
- **Evidence Object**：canonical UTF-8 JSON或原字节，SHA256寻址、size/type/run绑定，写入后不可变。同一控制快照只存一次，transaction只存临时base_root_ref、request_ref、result_ref；握手完成后receipt保留base revision/digest与必要事实，不永久钉住完整旧root。新schema在构造input/handoff/record/result时直接生成共享不可变合同/包/基线引用和subject delta或有限projection，禁止先展开完整context/祖先再blob化。内容寻址只能去掉完全相同字节，不能解决不断变化的大快照。真实外部host原回执保留，内部派生C/B完整副本不按“原始审计证据”永存；完成握手的派生重复projection可在归档时去除，保留实际身份、原请求/结果及适用性所需事实。handoff/acceptance/predecessor 使用引用链，禁止递归内嵌祖先。

standalone由原 checkpoint root CAS提交；ledger-backed控制由原 ledger锁保护的当前 control store_ref/CAS revision作权威提交点，runner只持镜像引用和观察revision。所有对象先落盘，最后替换原权威指针；不跨两个文件声称原子双写。崩溃后以权威指针重建 runner 投影；禁止镜像比权威“更新”就覆盖 ledger。Phase Run的生命周期仍归既有ledger，不迁移无关discussion记录。

外层 runner 的 history/last_result/dispatch/confirmed包快照亦改用引用，否则 v19 的23MB history仍存在。`workflow.py._read_json/_atomic_save`、stage_handoff/current readiness与stage_dispatch所有读取都通过有限投影。生产读者使用四种只读typed projection：`identity`（包、owner、revision、worktree pin），`scope`（当前authority/保护路径/计划索引引用），`transaction`（指定intent、base revision和原调用状态），`delivery`（候选、coverage、final attempt与handoff引用）。`stage_dispatch.checkpoint/design_scope`取scope+指定transaction；`stage_handoff`的pin/current_stage3/accepted_stage3检查取identity/scope/delivery；`discussion_core._start_git_authority`取scope+transaction并与ledger revision比较；runner取identity及自己的当前stage投影。它们不得索取旧内联dict，也不能从projection获得新写权限。恢复只读 root + 当前 batch/目标subject + 未决调用证据；最终交付核验可逐 Ticket流式读取全部必要claims，明确这不是日常恢复。

### 固定布局、容量和按需读取预算

本轮约束当前控制记录、日常恢复和模型输入体积，按固定批次让完成历史退出活跃窗口。历史磁盘仍随有效工作增长；本轮不提供整个 run 的物理总量保证，也不宣称无限运行。已有归档/精确恢复证据和必要代表组合用于核心验收，不增加万级基准任务。

布局位于原 checkpoint旁的 run专属 `.state/`：`objects/aa/bb/<sha256>`、`subjects/<ticket-or-stage-hash>/<batch-number>/<record-hash>`、`events/<batch>/<segment>`、`receipts/aa/bb/<operation-key-hash>`、`archives/<subject-hash>/<generation>`。ID需有界且先hash再作路径；拒绝symlink、路径逃逸和跨run引用。hash只能证明内容完整性，授权仍来自当前owner/lease。

| 项 | 固定设计预算与到达阈值行为 |
| --- | --- |
| Current Root | canonical UTF-8 ≤256 KiB；到上限拒绝新业务动作；不能因此删除原 intent 或重发未知调用 |
| 活跃窗口 | ≤8 Tickets/stage subjects，≤32 allocations，≤64 pending receipts；超出排队；closed subject退出窗口 |
| 每个 subject | ≤256 KiB current；每批≤32行为片或256有意义事件，任一达到即封段；Ticket可多批继续 |
| 日常恢复读取 | root+最多当前8 subjects≤2.25 MiB；按具体未决操作追加最多一个≤16 MiB原证据；不隐式读取archive |
| Agent上下文 | 默认模型输入包（管理状态/摘要/必要合同）合计≤64 KiB UTF-8，一次按需展开合计≤256 KiB；原证据按段≤64 KiB，并明确截断/下一游标；结构化权威验证不依赖模型摘要 |
| 管理当前入口 | 一个 canonical UTF-8 ≤16 KiB 的当前记录，仅存目标/进度/阻塞/活动调用/下一步/证据引用；完成历史移出，必要当前事实仍超限则诊断并阻止新派发 |
| Evidence对象/事件段 | 单对象≤16 MiB，超大日志流式拆成≤1 MiB chunks及≤256 KiB manifest；每事件段≤1 MiB |

输入在执行副作用前验证大小、schema和下一次可读取性。超限或无法完整验证的结果不能 accept 或重发，保留原调用与 unknown 恢复语义。本轮不新增宿主日志 spool/物理配额；不能据此承诺原生响应或日志占用已受整个 run 配额约束。

读取预算按一次实际交接/恢复累计核验，包含展开的依赖和证据，模型输入与结构化读取分列；拆文件不等于满足预算。管理当前入口的字段替换、旧下一步退出及历史按需读取见 [当前记录与读取预算](execution-policy.md#当前记录与读取预算)，历史索引不另存一份当前状态。

幂等查找不用随历史增长的内存字典：operation key绑定run+actor+kind+subject+invocation ID，直接hash路径查单个≤16 KiB receipt索引，索引引用原request/result和已应用authority revision。相同key不同digest报冲突。索引与结果对象在root提交前准备，只有已被提交revision证明的receipt可ACK；准备但未提交的索引不能当成功。已完成旧subject的陌生调用拒绝，新调用只能用当前epoch/lease；旧精确重放按原key查receipt，最多读取一个索引和一个必要结果。根不存全部索引，目录按两级hash固定分片；不为查一个receipt扫描整个run。

### 原子性与恢复

现有 `record_lock`、runner锁、ledger锁维持既有所有权顺序；进入ledger adapter时使用固定 progress→ledger顺序，禁止反向调用获得progress锁。只允许原owner CAS同一revision，查询可读已提交不可变root。

1. 准备动作：核验幂等key、原调用身份、新root/subject序列化体积与schema；先持久化原intent，再调用宿主。没有持久intent绝不发调用。新增物理预留系统已延期。
2. 新对象/收据写临时文件，完整hash/schema/引用检查，fsync文件和目录，rename为不可变名称。按旧revision构建新root，验证读取闭包；原子替换权威root/ledger指针，fsync目录。最后清理准备标记。对象已存在只验内容，不覆盖。root内容不含previous指针，原owner envelope以current/previous两个固定槽保存可回退诊断代。根提交后落一个≤16KiB的commit marker（revision、root digest、receipt-set摘要），在允许下一次mutation前完成；若崩溃发生于根提交而marker未写，以当前权威根修复marker。旧receipt ACK验证保留的marker及对应批次manifest membership，不能仅凭一个预写receipt的自报revision；marker只保留提交证明摘要，不pin整份root。ledger路线用其原已应用事务证明生成同样marker，不另设提交权威。
3. root替换前失败：旧root有效；新对象是未提交候选，之后可整理。已发生外部副作用不回滚重发：原durable intent保持unknown，先查原调用，可靠保存原结果后才推进；不能保证磁盘耗尽时仍可写。
4. root替换后返回丢失：读当前revision与receipt证明ACK；不再次调用。ledger已提交而runner未更新时只刷新镜像。
5. 缺失/损坏当前引用：fail closed，报告subject/ref，禁止派发；读上一有效根只用于诊断/恢复证据，不能自动退回旧权限并重发副作用。只有证明无外部副作用且CAS未应用才允许放弃准备项。
6. IO故障保持原 fail-closed 行为；保留已fsync的intent并停止新工作，恢复后精确查询原调用。未知停机/调用及未完成事务的证据不得清理。真实配额/ENOSPC 与物理预留专项未验收，不承诺磁盘耗尽下的可用性。

### 归档与保留规则

每批封段、Ticket 所有片/验收结束且 lease 停止、stage 完成及 delivery 完成触发整理。以不可变 archive manifest 替代活跃历史，验证下一步、权限、验收和恢复投影后由原权威提交。归档只改变读取范围。archive 按 subject 直接定位，目录固定分页；全交付核验分页读取。

本轮不自动删除历史、临时对象或孤立证据。已提交 event、精确 receipt、claims、archive manifest，以及原 intent/unknown/恢复引用继续保留。管理记录按状态转换移出已完成历史和旧下一步，仅引用原报告；不删除原报告或另维护一份当前指令。当前/上一根固定槽和已完成事务退出临时 pin 的既有设计保留，不引入递归历史根。

自动删除资格、24小时/30天年龄、两次标记扫描、物理配额及预留系统全部延期。不得为了交付清理未审查 B 工作或有效证据；不得将“已归档”当成“可以删除”。历史磁盘增长是本轮已知限制，后续容量治理需要单独设计与验收，不能在运行中扩大当前记录预算。

## 兼容、既有冲突与范围

- 新格式使用拟定 1.4.0 / progress-v20 / transfer-v15 / control key12、schema7 / runner11 / execution-plan-v2。store-format1单独标识。实际分配时若版本被其他交付占用，只整体提升到下一未用版本，不能复用已有语义key。
- 新运行从创建时固定新包和格式；旧运行遇新运行时明确 `legacy_run_requires_original_runtime`，由原固定包读取原格式，不自动转换、不重新注册/安装旧任务包。
- 改写 execution/originating-task/workflow-control 协议及 Stage 3/4 模板：替换完整候选两轴重审循环；更新 review-first 测试为“早期行为检查 + 最终完整验证”；不能同时保留互斥规则。
- ADR-0013 的全writer短停保留，结果/审查选择性继承由 Accepted ADR-0014说明；单权威外置证据由 Accepted ADR-0015说明。领域术语只在实施时按 main 约定写入 `GLOSSARY.md`，不改成实现说明书。
- 当前已支持的 standalone、continuous/stepwise、app-server/collaboration及已有attached调用方路径保持一致的状态读取和证据验证；其中某路线不支持计划修订就继续明确拒绝，不把拒绝当成功。
- 无A/无Ticket修订初始化桥、foreground Controller ingress、attached真实Stage2/Phase0初始化冲突均继续延期。既有guard回归必须保持；本方案不要求修通这些入口，不借一致性扩充权限。
- 不新增通用调度服务、SQLite服务、每Agent独立Git工作树或全历史自动迁移。避免新存储依赖，用已有POSIX文件锁/fsync/replace能力；Windows支持不属于现有部署目标扩展。

## 分层验证与效果验收

快反馈采用真实模块的公开边界，只构造当前反例需要的合法输入；组合边界为 `workflow_progress.handle` →真实Progress/dispatch/control/Git→临时文件系统与宿主adapter，并以实际CLI、runner start/resume及支持的ledger路线确认接线。不要求每个字段反例都重建完整阶段链。权限/CAS/停止等模块仍真实核验，宿主响应可确定性fixture；不得用模拟权限或自报pass替代业务消费者。命令选择、首跑耗时、组合触发与规模构造见 [验证分层](execution-policy.md#验证分层)。

运行器组合验证：公开start/pause/resume及已有需求准备的首次初始化保持。Stage 2当前动作集没有无需额外退役历史便可生成exact receipt的控制动作，因此另以真实Stage 3 handle分配→运行器实际调用的save_outer_record系统写边界→handle原回执重放，验证outer/C交错、storage CAS与C业务revision分离、原提交证明保留和过期owner写拒绝。这是补充集成测试，不mock内部业务、不伪造历史，也不替代公开接线证明。

T01 原首个红灯及验收保留：真实临时Git中的一个Ticket、两个行为片，经过start→allocation prepare/bind/receive/accept→暂停/恢复；1000次相同poll保持当前文件有界，重复副作用调用只一次，恢复只读有限subject。T01/T02 已验收不因本次文档修订重跑；后续协议增量只核实际影响。集成分支在最终Ticket之前不声明可发布。

核心验收核对已有的权限/证据、原 lease/receipt、冲突重放、停止撤销、暂停/未知结果、root 提交前后恢复、ledger/runner 提交交错、损坏/跨run 引用、归档后原调用和分页交付证据。优先复用绑定原 source 的有效报告；源码/依赖发生变化才补受影响检查。已有批次边界、第33片及1000重复 poll 证据可作代表规模，不能外推10000历史或物理边际增长已通过。固定 current/普通恢复/模型输入预算仍是硬门槛。

T06 只汇总既有 T03/T04/T05 命令和审查证据，能确定的 per-command wall/CPU、体积/读取事实按原单位报告；端到端墙钟、协调等待、首次模型提交率、成功率或可比性没有独立来源时标 unknown。测试次数不当作模型提交，重叠区间不相加成总墙钟。当前没有可比的旧 v19 10片/3Ticket基线，效率结论为“证据不足”，不宣称成功率或时间已经改善。

正式三类场景对照、10000历史/轮询及物理增长门槛、完整原生宿主两Ticket/四片现场实验移出本轮，不新增 worksheet 或测量工具功能。工程验收、现场未验证、效率证据不足分别报告。adapter fixture 证明相应接线，不能替代 native IDs/停止/回执现场证据；本轮结论仅为经验证的本地工程候选，不据此宣称宿主上线就绪。

全部仓库测试只经 `scripts/run_tests.py`；完整实现验收经 `scripts/validate.sh`。本次仅方案修订，不运行完整实现验证。实际实现时生成包 source/output一起提交、build --check、五包资源闭包与原版本拒绝检查必须通过。

## 实施顺序与统一审阅

实施顺序校准：真实首切片确认控制校验与交接pin读取属于保存/恢复的必要前置，因此T01提前适配已批准范围内的有限active校验及必经读者；T02仍负责新增审查语义。T01分为有界保存/幂等恢复首片和共享交接去重/runner/ledger读者闭合次片。该调整不改变功能合同或Ticket依赖；中间片不是整张Ticket或可发布候选。次片核实ledger改store_ref会影响既有Phase Run完成校验，因此将已列verify的phase_runs._check_completion_evidence晋升为精确modify并同步五包映射；只改delivery投影读取，保持原run/attempt/accepted-delivery权限判断，不新增入口。

| Ticket | 交付行为 | Blocked by |
| --- | --- | --- |
| 01 | 一个真实执行片有界保存/重复调用/暂停恢复，所有生产读者采用有限投影 | 无 |
| 02 | 局部→Ticket→交付审查与可验证继承闭环 | 01 |
| 03 | 原上下文修复、提前集成测试和最终候选完整验证 | 02 |
| 04 | 合同内修订短屏障并选择性保留结果 | 02 |
| 05 | 已完成 A 的批次归档、精确重放及读取预算闭合；B 延期 | 01 |
| 06 | 原始夹具兼容、已有支持路线集成、五包一致性和最终验证；既有数据限制报告 | 03、04、05 |

当前 T01–T04 已验收，T05-A 已完成局部验收，T05-B 未提交且延期。恢复后的剩余顺序只有“隔离 B 并闭合修订 T05 → T06 必要集成与交付”。不继续 B，不重建已验收 Ticket，不为日志、GC、现场或对照测量另派执行者。DAG 保留；共享文件不并行 writer。

本次收缩来自用户明确要求，替代旧方案中延期项的本轮验收责任。旧八项 T05 和完整现场/效率合同保持历史来源，不重标为通过。ADR-0014 的分层审查与 ADR-0013 的共享 Git 短停仍有效；ADR-0015 同步本轮归档与保留边界。实现仍暂停，待用户恢复。
