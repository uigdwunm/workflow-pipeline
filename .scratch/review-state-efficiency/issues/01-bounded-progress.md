# 01 — 一个执行片的有界保存与真实恢复

Status: ready-for-agent
Lifecycle: completed
Design status: accepted — 普通本地连续实现已授权
Blocked by: None
Spec: ../PRD.md
Scope inventory: ../manifest.json（T01）

## What to build

从真实 `workflow_progress.handle` 启动一个 Ticket、分配两个行为片、接收结果、停止再恢复；新root仅含当前投影和引用。相同轮询和同一原调用重放不会重复保存全控制上下文或再发宿主调用。runner、handoff和ledger读取同一当前权威的有限投影。该切片先保留既有审查门禁；新格式只在实施集成分支的新运行启用，不提前发布半成品。

## 合同和验收

- [x] 当前root≤256KiB；相同1000次poll不增加原始证据对象/历史项，count/last_seen变化不改变权限。
- [x] start→allocation prepare/bind/receive/accept→pause/resume使用真实Git及存储；失去响应只查询原调用。
- [x] 副作用调用前已有fsync intent与容量预留；root提交前/后崩溃分别保留旧状态/幂等ACK。
- [x] `inspect`、恢复、readiness、Stage3接收不会扫描全部history或hydrate全部对象；测试以读取计数/字节证明。
- [x] ledger-backed控制只以原ledger CAS指针为权威，runner镜像丢失可恢复；legacy包/记录不转换。
- [x] Current Root、Subject Record、receipt和blob的schema/大小/引用在提交前验证；损坏对象fail closed。

## Function Execution Plan（草案）

| Step | 目标符号（现有/拟新增） | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | 新 `workflow_state_store.open_current/read_subject/read_evidence/commit_current` | 原checkpoint owner、expected_revision→有界typed投影/不可变引用；内部canonical encode、CAS、fsync、hash、预留与提交点；冲突/容量不足在副作用前拒绝，已发调用保持unknown | 无 |
| S02 | `workflow_progress.read_record/atomic_save/validate_state`、`Progress.save/control_action/allocation/issue_allocation`、新 `workflow_control.validate_current_context` | 新格式路由到S01；transaction以base_root/request/result引用代替context副本；保留旧format拒绝语义；不返回伪装旧dict的全hydrate适配 | S01 |
| S03 | `workflow_progress.handle/_view`、`Progress.observe/lifecycle_state/baseline_thread_state/stop_barrier_pending` | operation选择有限read set；poll按identity/status/cursor/result digest合并；身份变化、撤销停止、未知调用独立持久化 | S02 |
| S04 | `workflow.py._read_json/_atomic_save/_stage_projection/_live_progress/CheckpointState.refresh` | 外层runner只持当前typed投影与blob refs；保存按CAS合并其自有字段；history/last_result/dispatch不复制整份Progress | S02 |
| S05 | `stage_dispatch.checkpoint/design_scope`、`stage_handoff.verify_inline_recovery_pin/verify_stage_checkpoint_pin/current_stage3_state/accepted_stage3_progress/current_readiness`、`discussion_core.workflow_control._start_git_authority/workflow_control`、`discussion_core.phase_runs._check_completion_evidence` | 各调用请求明确的identity/scope/current-transaction/delivery projection；验证ledger revision与projection匹配；拒绝镜像发起控制覆盖 | S02 |
| S06 | `workflow_progress.Progress.native_finalize/record_host/unresolved_host_actions`、新store副作用预留内部实现 | 有界输入→已持久intent→精确receipt，最多4业务未知调用；停止/恢复使用独立预留；超大结果保留truncated/unknown不accept | S03、S04、S05 |

这些step是行为实现计划，不意味着一函数一Agent；S01–S03作为首行为片，S04–S06作为生产读者闭合片。首片把有限active校验及启动必经的stage_handoff pin/current-stage读取提前接入；既有全范围语义校验保留，只限制其输入为当前有界窗口。共享handoff/record构造去重、runner及ledger读者在第二片闭合，整张Ticket验收前必须完成，不能推迟到T06。权威适配不能等T06才补：否则测试首片绕开真实路线。read_subject必须由调用操作给定ID，不接受“all”；只读投影不携带写lease。

## 同一 Controller 的阶段衔接边界

T01 必须完成新存储下最小同 C 阶段衔接，不能等待依赖 T01 的 T05。先独立完成 Stage2→3，再处理 Stage3→4/inline recovery，最后闭合 ledger 读者；每片保留既有身份、停止与接收门禁。

Stage2→3 具体入口：`Progress.start`、`save_current_record`、`read_scope_readiness`、`consumed_scope_readiness`、`read_projection`、`change_decision_journal`；handoff 的 `accepted_design_state`、`inherited_planning_paths/planning_paths_for`、`verify_stage_checkpoint_pin/current_stage3_state`；store 新增精确前驱引用构造与读取。前驱只保留已接受的 run/checkpoint/stage/C revision/controller/requirement/package/worktree 绑定、handoff/acceptance/stop/phase 引用及 readiness/seal/scope/planning/publication 必要摘要，不 pin 整个旧 stage，不递归引用全部祖先。保持同一 checkpoint 与原 C，禁止复制 authority 或放宽 pin 来制造测试通过。

Stage3→4 与恢复按三个独立行为片收敛，逐片停稳、审查，不将整个恢复系统放进同一执行上下文：

1. 正常 Stage3 接受→同 C Stage4 prepare/bind/inspect。真实分配、原两轴停止审查与最终验证后接收；`accepted_stage3_progress`、runner `_stage_projection/_accepted_stage/_validate_handoff` 消费有限 accepted-implementation 事实与 review/final-attempt 引用。普通 Stage4 调度复用紧凑 input/launch 解析。`workflow_control_git.verify_closure_delivery` 与现有调度适配入口解析精确已提交引用；纯 `workflow_control.validate_closure_delivery/_transition_single` 只机械校验有限事实绑定，不读取文件、不依赖 progress、不接受自报 pass。保留 Stage3 原有审查规则至 T02/T03。历史尝试冻结前做完整唯一性验证，当前只保存 count/digest/current-attempt-ref；原证据不删除。
2. 正常 Stage4 `verify_completion`：单独闭合 phase、规划前驱、publication/cleanup 的必要精确引用及 runner 完成投影。第一片不据此宣称归档完成。
3. inline `implementation_recovery/verify_inline_recovery_pin`：独立保持原 Dispatcher、attempt、authority lineage、未发布与停止条件、既有 closure 文档及 protected-source 约束。

三个片均只保存必要事实与明确证据引用，不嵌入整 control/history/allocations/dispatch 或递归祖先。ledger 与 S06 仍另片闭合。T05 负责通用分页交付目录、批次归档、GC 与规模/物理容量，不作为上述生产路径的隐式前置。

## Verification references

V01：扩展 `tests/runtime/test_workflow_progress.py`、新 `test_bounded_progress.py`，首红灯即PRD的真实start/allocation/恢复与重复poll场景；不直接测文件字典拼装函数。

ledger 公开读者补充：`discussion_protocol._read_topic` 的 Phase Results `workflow-control.data_json` 在 store_ref 路线必须有限解析当前权威，与 `discussion_core.workflow_control` 及 `_check_completion_evidence` 同步；不扩展其余 discussion protocol。构建映射按五包实际 roots 纳入 manifest，禁止只改生成副本。

V02：扩展 `test_workflow.py`、`test_stage_transfer.py`、`test_unified_recovery.py`、`test_discussion_protocol.py`，覆盖外层runner及ledger提交后镜像丢失。现有其它直连fixture按manifest的verify条目核验，仅确实依赖旧内联结构时修改。

V03：公开runner start/pause/resume验证同carrier、已有workflow_requirements初始化及真实Controller decision消费；补充真实handle分配→生产save_outer_record→handle精确回执重放，验证存储CAS前进但C业务revision、subjects和原回执证明不变，并拒绝过期owner写。此补充仅为覆盖Stage 2动作集无法自然产生的receipt交错，不替代V01/V02、不mock内部控制。

支持面校准：Stage2+ 的真实 attached 调用方可采用 `topic_ref != null`、`discussion == null` 与 Phase Run 生命周期；不等同 ledger-authoritative 初始化。后者已延期的 Stage2/Phase0 冲突继续拒绝，不造空控制状态修通。`test_attached_flow_progress.py` 因已复现 lifecycle 旧保存路径获精确修改范围：真实 Stage2 生命周期故障/幂等、C subjects、两类 revision 与读取预算；不据此扩展初始化能力。

S06 协作回归补充：`test_collaboration_lifecycle.accepted_execution` 已在原样回归中复现 compact allocation 无内嵌 handoff，按 manifest 精确改为统一解析；special close-readback 的其余身份/停止语义保持原验证，不以夹具调整放宽调用归属。

V04：真实 finish_design→原 C start Stage3→bind→inspect 首先复现 history 容量门禁；修复后验证真实双 Ticket 各分配一片、peer 未停不能通过停止门禁、目标读取量不随另一 subject 原文增长。覆盖前驱引用错误、root CAS 前后故障和公开 continuous/stepwise 阶段衔接。既有单 Ticket 双 allocation 证据不代替双 subject 组合验收。

命令：逐文件 `python3 scripts/run_tests.py --test-file <上述路径>`；若已有runner占锁，报告owner并结束该次验证。源模块及新增store资源必须同次生成到五包闭包，package identity采用新key，不手改generated副本。

## 组合验收边界

各行为片局部通过后仍需累计组合验收，不能据单片报告勾选整张 Ticket。先适配已证实的 baseline 旧夹具：`test_missing_baseline_saved_readback_is_validated_before_use` 通过有限 stage subject 定位原 raw，构造引用链有效但线程身份错误的证据，保留原业务拒绝断言。

固定源码与 HEAD 后，统一 runner 逐文件运行完整 bounded 与 workflow_progress，保留新结果并重新分类最初 106 项检查的失败；旧失败日志不改写。只对实际失败作对应 focused 修复，必要 verify 文件晋升逐项记录。随后核验 workflow、stage_transfer、unified_recovery、discussion_protocol 和实际支持的 attached/协作路径累计行为；已通过检查仅在新变更或未解决疑点影响时重跑。测试占锁按 owner 报告并结束尝试，不绕锁。最终全六 Ticket 候选再运行 validate.sh。

组合覆盖现已支持调用方与 ledger dedicated 路线，不修通延期的 attached 初始化桥。纯模块／模拟权限夹具不能当作真实新运行支持证据。其余原文来源以实际入口和既有输入合同核验，不因静态出现 raw 字段就新增功能片；真实缺陷仍需修复。T05 负责通用长期整理和物理规模验证，不承担 T01 最小前驱／生产读取未闭合的欠账。

## 实施约束与演示

不迁移旧状态；新接口可在实现分支内部逐步接入，但本Ticket完成时所有列明生产读者均能读新格式。演示输出包括原/新文件字节、对象读取数、同一调用工具执行计数、暂停后next_action和原native identity。不要用单独store单测通过冒充生产闭环。

## Comments

用户已统一接受并选择连续实现；此为普通本地授权，非正式Stage3 handoff。


## 验收结果

T01 六项 AC 于固定 target `2db280986df071464e3c37346c076f24d233c952` 独立组合验收通过；runtime 与 `46aa4ed0ede354852a922b8b1ae38f6ac21f0de9` 同树。原 reviewer 关闭 public-review 容量预检 P2，Standards 与局部合同均无 remaining finding，再逐项接受六 AC。各局部 claim 保留原 source/base，未重标旧报告。

证据包括原 `82d1ba0` 固定 core 246 PASS、原 19 项失败的逐项修复 GREEN、`9916785` 完整 attached 恢复链，以及 `46aa4ed` 九项受影响检查和五包执行/build check。独立报告及源点适用性索引位于本次运行的 `implementation/t01-independent-six-ac-review-2db2809.md` 与 `t01-six-ac-final-target-2db2809.md`；原累计失败和诊断日志保留，不宣称最新候选重新跑过全部 core。

验收限于列明现有调用路线；T05 物理增长、归档及 GC 仍待完成。延期 ledger 初始化/normal launch 继续拒绝，adapter 证据不替代实际宿主现场；全六 Ticket 完整验证、交付审查和本地 main 集成仍未完成。
