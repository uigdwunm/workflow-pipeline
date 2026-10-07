# 分层审查、局部返工与有界状态记录方案

Status: ready-for-agent
Design status: accepted — 用户于当前对话选择连续模式，授权全部六张Ticket实现、审查、验证和本地收尾
Source requirement: docs/requirements/2026-10-05-review-state-efficiency.md
Frozen requirement commit: 61f105f1d8bdb3ebc1b574e3104332621dc31ec8
Frozen requirement SHA256: dcc2adf2141dd49a9d383fc30a62388d267fb35e58fdddbdad1824d367c2dd77

本文件已由用户统一接受，当前对话选择普通本地连续执行，授权六张Ticket实现、必要审查/验证、文档收尾和本地main合并；不授权安装、部署、远程发布或旧运行迁移。这是普通本地授权，不是正式 B/C Stage Handoff。此前冻结需求中的“本轮只做方案”记录当时阶段边界，本次后续授权不改写冻结来源。需求保持唯一且冻结。逐文件实施范围及生成映射见 [manifest.json](manifest.json)，函数级计划见各 Ticket。本文中的数值除现状测量外均为**待验证设计预算**。

## Matt 基础兼容更新（2026-10-07）

在原六 Ticket 实现暂停期间，先同步 main 的 `70822bc`：静态上游基线为 Matt `1.3.1` / `dd400c3ad65e57c06f05e832e0aac92c7992f34d`，本项目词汇表改用 `GLOSSARY.md`；消费项目按其约定选择词汇表，已有 legacy `CONTEXT.md`/`CONTEXT-MAP.md` 不因升级被重命名或产生第二份词汇表。生成包采用同一 canonical 来源，保留本分支 execution-plan-v2、control12/schema7、progress-v20/transfer-v15/runner11。

这一兼容更新不改变分层审查、局部修复、共享 Git 短屏障、固定预算或六 Ticket 依赖；T01/T02 原验收仍保原 source/report，追加兼容增量验证，不重标旧证据。T06 的领域文档目标改为 main 已提供的 `GLOSSARY.md`。main 的登记/入口回读/十项 action preflight 记录仅说明其当时包和宿主；不能冒作本分支新包完整流程、原生交接或效率改善的证据，T06 仍核实际注册的 Matt 入口及本分支 exact package identities。

冻结需求文件及 hash 保持不变。T03–T06 连续实现继续暂停，待用户明确恢复；此次仅基础兼容同步与方案校准，不部署、不迁移旧运行。

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
10. 维护者希望完成历史可归档、证据可核对，自动清理不会删掉在用证明。
11. 老任务使用者希望保持原包、原恢复方式，不被新格式接管。
12. 决策者希望比较质量和时间，不以减少检查或一次测试通过宣称提效。

## 设计决策：三层审查与状态协议

### 行为片、角色和最少上下文

一个行为片选取一个 Ticket 的一组紧密相关 plan_refs，拥有精确写文件分配、合同摘要、相关只读依赖、verification_refs、公共边界和失败行为。它不是每函数一个 Agent。执行片通常 1–4 个相关步骤；超过上下文预算就沿可验证行为拆片，不按行数机械拆分。

每个行为片默认一个新的 Execution Agent 上下文，只在该片内修复时复用原上下文；一组紧密相关步骤可作为一个行为片，不把整个多片Ticket塞入一个执行对话。每个活跃Ticket默认一个独立 reviewer，按有限摘要复用；摘要超预算或独立性失效时轮换，禁止累计全项目历史。同一 Ticket 并行片确有独立收益时可分配多个执行者；文件分配必须不交叠，reviewer 按片读取。reviewer 不得是该片任何版本的实现者，也不得写实现字节。同一个 reviewer 可在完成全部局部审查后做 Ticket 验收，不再额外新建验收 Agent。交付保留两个独立轴 reviewer，均不可为本次实现者；可复用已有独立 reviewer，不强制每次新建。无工作变化时不重复派任何 reviewer。

原 Execution Agent 已停止且可继续时，使用原 native ref 的新 invocation/lease 继续修复；只携带 findings、当前 plan/authority 引用、上次片摘要、直接依赖变化及必跑测试。原宿主不可用、上下文不可信或容量超限才换执行者；新上下文按相同引用重建，不能把未知原调用作为已停止。reviewer 的未解决 findings 和覆盖摘要跨 turn 保留，历史原始输出按需取。

| 层 | 何时/谁 | 必做 | 不重复做 |
| --- | --- | --- | --- |
| 局部 | 每个行为片回归通过后，一个独立 reviewer | 当前变更的 Standards、局部合同/失败分支、相关读写范围和回归证据；findings 可定位到步骤/路径/AC | 整个 Ticket 或整个候选全部 diff |
| Ticket | 全部片有效后，同一独立 reviewer | 完整 AC、组合/顺序/共享夹具、公共输出、依赖 Ticket 合同；运行 Ticket 行为验证 | 已有效覆盖片的逐行规范复审 |
| 交付 | 全部 Ticket 有效后，Standards/Spec 两轴 | Standards 查跨 Ticket 一致性/共享状态/未覆盖文件；Spec 查所有需求→AC→证据覆盖、跨 Ticket 场景和影响扩大 | 重新扫描已证明未变的局部实现 |

交付集成 delta 为空也必须首次执行需求覆盖核对、共享状态和目标分支一致性检查；可以产生“无新代码审查范围”的明确证据。后续候选只含无关已证明不影响的变化时继承既有交付结论；最终完整测试仍绑定最终候选。不能凭空生成空 review 或用 Dispatcher 自我验收替代独立审查。

局部review必须读稳定快照：该片执行者先停稳，Dispatcher核对owned/read指纹并生成只读不可变代码blob或已提交tree引用，reviewer只读该集合；采集前后再次核对全部指纹，其他writer改变依赖时废弃本次快照并重采，禁止混读两个版本。无需为只读review提交每片Git；采集与权限释放用已有文件分配/停止证据协调。

### 证据的内容与有效性

每份不可变 Evidence Claim 包含：run/package identity、claim_id、层/轴、Ticket/plan_refs、contract_digest、authority revision、source commit/tree、owned 与 read 文件指纹（含不存在、mode、symlink type）、公共合同摘要 hash、依赖 claim IDs、verification IDs及命令/环境/夹具摘要、reviewer/native receipt引用、发现与解决引用、结果。原始输出独立 blob；claim 不嵌套上游全文。

三个不同身份不可混用：commit 是交付固定点；claim 的 subject fingerprint 证明局部适用；authority revision 证明当时权限。继承只创建小型 applicability 记录，列原 claim、目标候选、比较过的指纹和依赖、检查算法版本；不修改旧 claim，也不把旧 commit 标签改成新 commit。

失效从变化种子开始：写/读指纹变化、plan 语义或验证变化、AC/公共合同变化、依赖结果变化、未解决 finding、身份/回执不完整。沿显式“读取/调用/共享状态/验证夹具/依赖 claim”边做反向传递闭包，传播至消费者、Ticket 和必要交付 claims。没有可证明覆盖的读取/依赖、动态共享注册、无法界定影响时，保守扩大到 Ticket；跨 Ticket 公共合同/全局配置/共享状态未知则扩大到交付。声明只是一项证据，必须与 Git 变更和计划索引比对；不能以执行者声称“无影响”保留结论。

同一文件内两函数无法仅凭文件 hash证明互不影响，因此本版本按文件保守失效；不引入 AST 语义等价证明。相同文件内紧密步骤优先合为一个行为片；后续片仍改同文件时，可在Ticket闭合前一次补审这些片的累计增量，但受影响片在补审前不可声称当前有效。效率可能低于文件隔离好的任务，计量必须显式报告这种重审；单纯commit变化而文件/依赖指纹不变不触发失效。合同/行为范围改变返回用户确认；单纯测试修复不是自动合同豁免。最终 coverage certificate 流式遍历固定 Ticket 列表，每个 AC 需可达一组仍适用的通过 claims，无遗漏文件、未解决 findings 或未知调用；保存摘要和引用，不复制所有证明。

### 测试提前、修复和候选生命周期

`allocated → implementing → local-checks → local-review → local-accepted → ticket-checks → ticket-accepted`；有 finding 返回同一 executor 的 repair invocation。局部改动重跑对应 regression + affected checks 并复查增量；原 reviewer明确关闭 finding且刷新适用证明。局部首次通过率的分母是**所有第一次提交的片**，修复不能重建片ID改善指标。

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

.state内部预算不等于允许控制记录再次膨胀：正常任务目标为固定活跃元数据≤2.25MiB、每片只增加其delta与唯一原证据；压力门槛验证10000历史片和poll时仍满足同一读取/元数据上限，并检查每片边际字节；2GiB仅为包含原始日志的整个run安全硬上限，不是把325MB检查点放宽到2GiB，也不是正常任务体积目标。

布局位于原 checkpoint旁的 run专属 `.state/`：`objects/aa/bb/<sha256>`、`subjects/<ticket-or-stage-hash>/<batch-number>/<record-hash>`、`events/<batch>/<segment>`、`receipts/aa/bb/<operation-key-hash>`、`archives/<subject-hash>/<generation>`。ID需有界且先hash再作路径；拒绝symlink、路径逃逸和跨run引用。hash只能证明内容完整性，授权仍来自当前owner/lease。

| 项 | 固定设计预算与到达阈值行为 |
| --- | --- |
| Current Root | canonical UTF-8 ≤256 KiB；75%触发固定整理；到上限拒绝新业务动作，允许预留的停止/回执路径 |
| 活跃窗口 | ≤8 Tickets/stage subjects，≤32 allocations，≤64 pending receipts；超出排队；closed subject退出窗口 |
| 每个 subject | ≤256 KiB current；每批≤32行为片或256有意义事件，任一达到即封段；Ticket可多批继续 |
| 日常恢复读取 | root+最多当前8 subjects≤2.25 MiB；按具体未决操作追加最多一个≤16 MiB原证据；不隐式读取archive |
| Agent上下文 | 默认摘要≤64 KiB UTF-8，一次展开≤256 KiB；原证据按段≤64 KiB，并明确截断/下一游标；结构化权威验证不依赖模型摘要 |
| Evidence对象/事件段 | 单对象≤16 MiB，超大日志流式拆成≤1 MiB chunks及≤256 KiB manifest；每事件段≤1 MiB |
| 本run物理预算 | 2 GiB；75%整理、90%暂停新分配并整理、达到上限拒绝新工作；不是可随运行人工调大的参数 |
| 副作用回执预留 | 从上述预算预留64 MiB给至多4个未决外部业务调用，每个16 MiB；另64 MiB用于停止/查询/未知状态恢复，业务不能消费 |

输入在执行副作用前验证大小、schema和下一次可读取性；原始结果由有界stream/spool接收。无法预知的超大响应记录有界摘要、已接收分块引用、truncated标记和unknown结果；保留原调用ID查询，未得到完整可验证结果前不能accept或重发。宿主仅返回原子字符串时先限制adapter接受上限，超限也走unknown，不虚构完整证据。

幂等查找不用随历史增长的内存字典：operation key绑定run+actor+kind+subject+invocation ID，直接hash路径查单个≤16 KiB receipt索引，索引引用原request/result和已应用authority revision。相同key不同digest报冲突。索引与结果对象在root提交前准备，只有已被提交revision证明的receipt可ACK；准备但未提交的索引不能当成功。已完成旧subject的陌生调用拒绝，新调用只能用当前epoch/lease；旧精确重放按原key查receipt，最多读取一个索引和一个必要结果。根不存全部索引，目录按两级hash固定分片；不为查一个receipt扫描整个run。

### 原子性与恢复

现有 `record_lock`、runner锁、ledger锁维持既有所有权顺序；进入ledger adapter时使用固定 progress→ledger顺序，禁止反向调用获得progress锁。只允许原owner CAS同一revision，查询可读已提交不可变root。

1. 准备动作：计算最坏结果预算、幂等key和原调用身份；校验新root/subject序列化体积与schema；分配实际磁盘预留文件并fsync。先持久化 intent 和 reserve，再调用宿主。没有持久intent绝不发调用。
2. 新对象/收据写临时文件，完整hash/schema/引用检查，fsync文件和目录，rename为不可变名称。按旧revision构建新root，验证读取闭包；原子替换权威root/ledger指针，fsync目录。最后清理准备标记。对象已存在只验内容，不覆盖。root内容不含previous指针，原owner envelope以current/previous两个固定槽保存可回退诊断代。根提交后落一个≤16KiB的commit marker（revision、root digest、receipt-set摘要），在允许下一次mutation前完成；若崩溃发生于根提交而marker未写，以当前权威根修复marker。旧receipt ACK验证保留的marker及对应批次manifest membership，不能仅凭一个预写receipt的自报revision；marker只保留提交证明摘要，不pin整份root。ledger路线用其原已应用事务证明生成同样marker，不另设提交权威。
3. root替换前失败：旧root有效；新对象是未提交候选，之后可整理。已发生外部副作用不回滚重发：原durable intent保持unknown，先查原调用，利用预留空间保存结果。
4. root替换后返回丢失：读当前revision与receipt证明ACK；不再次调用。ledger已提交而runner未更新时只刷新镜像。
5. 缺失/损坏当前引用：fail closed，报告subject/ref，禁止派发；读上一有效根只用于诊断/恢复证据，不能自动退回旧权限并重发副作用。只有证明无外部副作用且CAS未应用才允许放弃准备项。
6. ENOSPC/IO故障可能连预留回执也无法可靠写入，不能承诺绝对可用性；保留已fsync的intent并停止新工作，恢复后精确查询原调用。释放业务预留不授权重发。未知停机/调用及未完成事务永久钉住相关证据直至明确解决。

### 归档、去重与删除资格

每批封段、Ticket所有片/验收结束且所有lease停止、stage完成、delivery完成各触发固定整理。先以新root引用不可变archive manifest替代活跃历史，验证下一步/权限/验收/恢复投影等价后提交；归档是读取范围变化，不是删除。archive按subject直接定位，root只保留当前批和当前交付目录根，目录分固定256条页，通过确定ID定位；全交付核验分页读取。

GC只自动删除：超过24小时且不属于未决事务的未提交临时对象；超过30天且经两次完整标记扫描均不可达的重复/孤立原证据。所有有意义的已提交event、幂等receipt、最终claims、archive manifest默认永久保留；控制root仅保留当前/上一有效代，以及未决恢复或已接受交付明确需要的固定代。root不递归引用previous链，上一代由权威外层固定槽引用；完成事务去除临时旧root pin。只要其中任何一者引用某blob，该blob不可删，因此不是“30天后删全部历史”。标记根包括当前/上一有效root、全部已提交archive manifests、所有未决intent、保留handoff/package identity及显式pin。扫描分段有游标，≤256条/轮；扫描代次固定水位，后续新增引用加入保护集。删除前锁内复核当前代次和可达性，扫描不完整/引用损坏/并发代次变化则不删。

receipt索引不常驻且仍消耗磁盘；不承诺无限工作。可达历史占满预算时run保持可恢复但拒绝新工作，用户另开独立交付而非扩容；不得偷删有效审计链腾空间。新run不得接管旧未知动作。此保守政策牺牲无限单run吞吐，换取可靠恢复与有界活跃内存。

## 兼容、既有冲突与范围

- 新格式使用拟定 1.4.0 / progress-v20 / transfer-v15 / control key12、schema7 / runner11 / execution-plan-v2。store-format1单独标识。实际分配时若版本被其他交付占用，只整体提升到下一未用版本，不能复用已有语义key。
- 新运行从创建时固定新包和格式；旧运行遇新运行时明确 `legacy_run_requires_original_runtime`，由原固定包读取原格式，不自动转换、不重新注册/安装旧任务包。
- 改写 execution/originating-task/workflow-control 协议及 Stage 3/4 模板：替换完整候选两轴重审循环；更新 review-first 测试为“早期行为检查 + 最终完整验证”；不能同时保留互斥规则。
- ADR-0013 的全writer短停保留，结果/审查选择性继承由 Proposed ADR-0014说明；单权威外置证据由 Proposed ADR-0015说明。CONTEXT只在实施时添加 Evidence Claim、Current Root、Review Coverage术语，不改成实现说明书。
- 当前已支持的 standalone、continuous/stepwise、app-server/collaboration及已有attached调用方路径保持一致的状态读取和证据验证；其中某路线不支持计划修订就继续明确拒绝，不把拒绝当成功。
- 无A/无Ticket修订初始化桥、foreground Controller ingress、attached真实Stage2/Phase0初始化冲突均继续延期。既有guard回归必须保持；本方案不要求修通这些入口，不借一致性扩充权限。
- 不新增通用调度服务、SQLite服务、每Agent独立Git工作树或全历史自动迁移。避免新存储依赖，用已有POSIX文件锁/fsync/replace能力；Windows支持不属于现有部署目标扩展。

## 验证与首个生产边界 TDD 切片

最高共同seam选择 `workflow_progress.handle` →真实Progress/dispatch/control/Git→临时文件系统与原生宿主adapter。宿主工具响应可确定性fixture，但不mock控制转移、存储或Git。runner再用其公开start/resume确认同一路径，已有 `test_workflow_progress`、`test_plan_driven_dispatch`、`test_review_first_validation`、`test_plan_revision_progress`、`test_unified_recovery`为先例。

运行器补充验证：公开start/pause/resume及已有需求准备的首次初始化保持为主seam。Stage 2当前动作集没有无需额外退役历史便可生成exact receipt的控制动作，因此另以真实Stage 3 handle分配→运行器实际调用的save_outer_record系统写边界→handle原回执重放，验证outer/C交错、storage CAS与C业务revision分离、原提交证明保留和过期owner写拒绝。这是公开链路之外的补充集成测试，不mock内部业务、不伪造历史，也不替代最高seam。

首个红灯：真实临时Git中的一个Ticket、两个行为片，经过start→allocation prepare/bind/receive/accept→暂停/恢复；注入1000次相同poll，当前文件不能随次数增长，重复副作用调用只一次，恢复只读有限subject而不碰归档。第01 Ticket先证明不改变业务语义的完整切片；后续Ticket逐步启用新审查契约，集成分支在最终Ticket之前不声明可发布。

必要反例：单文件内相邻函数变动、公共合同/共享fixture变动、漏报读取、原reviewer消失、旧lease回执、相同key不同结果、停止证据撤销、命令运行中pause、空集成delta、写root前/后崩溃、ledger提交后runner崩溃、损坏/缺失/跨run引用、16MiB边界、10000已完成片、2GiB预算、GC扫描中新增pin。每个反例应断言下一可执行动作/权限/验收与调用次数，不测私有函数调用顺序。

对比基准：先在同一机器、同一冻结需求/任务集/模型与测试环境采集旧v19至少10个行为片、3个Ticket的真实任务日志；新规则至少同规模，任务难度分层，记录失败和中断，不只成功样本。自动压力测试用1/10/100/1000/10000历史项和1000/10000 poll，固定活跃窗口。病理旧样本仅用于字节分解。少于30片的首次通过率只报分子分母，不作可靠改善结论。

报告首次局部通过率、集成新增缺陷（局部通过后才暴露且按严重度列出）、每片返工轮数、总墙钟、协调/停机/验证墙钟与CPU时间、活跃root+subject字节、恢复实际读取字节/文件数、peak RSS、历史磁盘量。并行耗时不可相加冒充墙钟。通过门槛：无权限/证据回归、所有必测失败分支通过、固定活跃窗口下current和普通恢复满足上述预算、重复poll不新增原始证据、10000历史片恢复读取相对10片不超过10%浮动；总物理对象字节须近似O(有意义变化+唯一外部原证据)：固定大小每片的100/1000/10000规模，扣除固定开销后每新增片字节不得随既有历史规模增长超过10%，同样输入总字节不得因保存内部全快照而出现二次增长；时间/通过率改善报告置信限制，若没有改善不得宣称提效成功，分析成本后再决定发布。

全部仓库测试只经 `scripts/run_tests.py`；完整实现验收经 `scripts/validate.sh`。本次仅规划，不运行全量实现测试、不宣称实现通过。实现时生成包 source/output一起提交、build --check、五包资源闭包与原版本拒绝检查必须通过。

## 实施顺序与统一审阅

实施顺序校准：真实首切片确认控制校验与交接pin读取属于保存/恢复的必要前置，因此T01提前适配已批准范围内的有限active校验及必经读者；T02仍负责新增审查语义。T01分为有界保存/幂等恢复首片和共享交接去重/runner/ledger读者闭合次片。该调整不改变功能合同或Ticket依赖；中间片不是整张Ticket或可发布候选。次片核实ledger改store_ref会影响既有Phase Run完成校验，因此将已列verify的phase_runs._check_completion_evidence晋升为精确modify并同步五包映射；只改delivery投影读取，保持原run/attempt/accepted-delivery权限判断，不新增入口。

| Ticket | 交付行为 | Blocked by |
| --- | --- | --- |
| 01 | 一个真实执行片有界保存/重复调用/暂停恢复，所有生产读者采用有限投影 | 无 |
| 02 | 局部→Ticket→交付审查与可验证继承闭环 | 01 |
| 03 | 原上下文修复、提前集成测试和最终候选完整验证 | 02 |
| 04 | 合同内修订短屏障并选择性保留结果 | 02 |
| 05 | 批次归档、幂等索引、GC及容量边界恢复 | 01 |
| 06 | 当前支持路线一致性、生成包、旧版本拒绝和可比测量 | 03、04、05 |

统一审阅已接受：三层角色与独立性、共享Git短停及局部失效、保守GC与固定预算、真实handle/runner测试seam、六张Ticket及其依赖。用户随后明确选择连续模式；按原普通本地执行方式实施，不补造正式阶段交接。工程预算仍必须通过实现验证，不能以本次接受冒充性能达标。

阶段衔接顺序校准：T01 的真实恢复必须包含同一 C 的最小 Stage2→3、Stage3→4 前驱引用及生产读者；这些是现有有界权威合同的必要适配，不等待依赖 T01 的 T05。T05 保留通用历史分页、批次归档、GC 与规模计量。具体符号与组合验收见 T01/T05，禁止复制 authority 或放宽 checkpoint pin。
