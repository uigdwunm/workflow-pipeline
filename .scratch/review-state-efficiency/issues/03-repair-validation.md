# 03 — 原上下文修复与提前集成验证

Status: ready-for-agent
Lifecycle: completed
Design status: accepted — 普通本地连续实现已授权
Plan revision: 2026-10-07 — 用户确认执行规则调整；实现保持暂停
Blocked by: 02
Spec: ../PRD.md
Scope inventory: ../manifest.json（T03）
Execution rules: ../execution-policy.md（开工前预检、验证/审查分层、返工触发与前置采集）

## What to build

局部review发现缺陷后回到原Execution Agent的新invocation修复，用相同独立reviewer复查增量。派发前检查划片/消费者/验证归属；从首片采集成本，局部先走真实模块快反馈，共享变化立即做组合检查，返工触发短诊断。最终交付只在三层证据有效且当前完整验证通过时完成。

## 合同和验收

- [x] 原native ref停稳可继续时复用，repair有新invocation/lease，旧回执不能满足新修复。
- [x] 默认模型输入包合计≤64KiB，一次展开合计≤256KiB；交接/恢复计入管理状态、合同和展开证据的总读取；失效host才替换，未知原调用阻止换人重做。
- [x] 局部regression/affected先跑；两个片整合或共享fixture变化后立即做Ticket检查，不能拖至所有review结束。
- [x] 最终完整测试失败后只重新派受影响片和对应review；最终完整测试必须针对新候选重跑。
- [x] 原完整验证过程pause/结果未知保留attempt；新候选不能掩盖运行中命令。
- [x] 从首片记录首次提交、原命令耗时、审查轮数、返工类别和等待来源；片ID/提交序号保留，TDD RED/测试准备错误单列，缺失计时为unknown。
- [x] 派发前完成读写耦合/生产消费者/快反馈命令预检及AC首次检查层分配；缺项先补计划，不因函数数量派Agent。
- [x] 达到执行规则的返工条件先短诊断；同片/原调用/有效证据保留，处理原因可验证后继续，不以新片ID或放宽检查绕过。

## Function Execution Plan（草案）

| Step | 目标符号 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S00 | `execution_plan.validate_ticket_plan/validate_plan_refs`、`Progress.allocation/review_activity` | 现有计划/路径/依赖/verification refs→完整行为与审查职责预检；耦合或缺消费者返回规划调整，不新增权限权威 | 无 |
| S01 | `Progress.allocation/issue_allocation/require_allocation_active/review_activity`、`stage_dispatch.record_for/matching/bound/received`、`workflow_control_git.verified_transition/verify_execution_start` | findings+原ref+已停止证据→新repair invocation/lease及当前Git快照；旧回执/过期快照/预算/未知状态拒绝，原不可用则有证据替换 | S00 |
| S02 | `stage_handoff.prepare/_prepare/render`、`workflow.py._stage_prompt`、`workflow_state_store.operation_store/open_store` | 当前合同/片/依赖摘要→有限executor/reviewer上下文，scope仍精确；遗漏必要合同拒绝派发 | S01 |
| S03 | `workflow_control.validate_validation_plan/validate_checks/validate_result_source/_transition_single` | verification refs及触发影响→快反馈/局部/Ticket/集成/final计划；固定候选，缺必跑检查/失败/unknown不能pass | S01 |
| S04 | `Progress.control_action/observe/suspend/resume/review_activity`、`workflow.py._invoke/_advance_in_host` | 原attempt/命令/审查结果→有限成本事件、短诊断及同身份暂停恢复；新候选不继承旧final pass，不重发未知调用 | S02、S03 |
| S05 | 新 `scripts/measure_review_state.py.main` | 原事件/命令日志/环境身份→只读计量及明确unknown；保留首次片身份、失败和中断，拒绝混组或相加重叠区间 | S00（从首片起采集，不等待T06） |

不另造后台测试调度器，使用现有Dispatcher调用和命令身份。必跑check的集合由计划与影响声明确定，遗漏check在Ticket或final门禁拒绝，不能由执行者任意缩小。

先确定快反馈命令和计量来源，再完成“停稳原ref→新repair→有限交接→局部审查”闭环，最后闭合验证attempt/返工诊断/最终候选行为。这是验收顺序，不固定每个step一个Agent；共享 `workflow_progress.py` 的步骤按预检合片或串行累计复审，不声称可独立并行。

## 审查归属

| 验收项（按上列顺序） | 首次检查层/证据 | 上层责任 |
| --- | --- | --- |
| 1、2、5 | 局部：V02的原ref/新lease/暂停；V03的完整交接读取 | Ticket查修复到恢复组合 |
| 3、4 | Ticket：V01的提前集成与新候选final | 交付查当前完整验证及跨Ticket保留 |
| 6、7、8 | 局部：V03/V04的计量、预检和返工触发 | Ticket查规则实际阻止错误派发/无限补丁；T06汇总效果 |

## Verification references

V01：`test_review_first_validation.py` 场景先引入公共fixture失配，在Ticket检查发现，断言无需等交付review；修复后旧完整pass仍无效。

V02：`test_pending_host_actions.py`、`test_lifecycle_snapshot_retry.py`、`test_collaboration_lifecycle.py`、`test_foreground_host.py` 验证原调用继续/宿主失效替换/暂停时结果未知。两种native adapter保留各自身份和停止证明。

读计量范围补充：T03在原StateStore操作读取入口汇总本次交接/恢复的跨checkpoint及ledger依赖读取，不改容量/权威/CAS。模型实际输入包另计；Git/entry_prepare/宿主等缺少独立计量来源时明确coverage与unknown，不把局部Store计数宣称为全宿主总读取。

V03：`test_layered_review.py` 统计实际新派Agent数、复用ref和模型输入/结构化恢复总读取，含展开的共享依赖；用原事件/日志验证计量来源、重叠区间及unknown，测试尝试不冒作模型提交。

V04：在已列 `test_execution_plans.py` / `test_layered_review.py` / `test_review_first_validation.py` 中验证后片同文件耦合的预检、缺AC验证归属拒绝，以及三类返工触发保留片身份/权限。先用真实模块公开边界验证反例，再用一条真实handle/CLI链验证接线；不为每个字段重建两Ticket四分配。

所有测试经 `scripts/run_tests.py --test-file`。现有CLI支持路线只同步新状态/上下文；本Ticket不新增foreground Controller ingress。

## Comments

用户已统一接受并选择连续实现；此为普通本地授权，非正式Stage3 handoff。

## 本地工程验收（2026-10-07）

候选 `5a2de9bbf12b5a39d1e4da92913aef176a7429ed` 经同一独立 reviewer 的局部增量复查与八 AC Ticket 覆盖验收通过，Standards / Spec 均无开放 finding。原 ref 修复、有限输入、提前 Ticket 检查、固定 full attempt、原调用恢复和诊断事实归并均有原模块/handle/Git/Store 证据；未变证明保留原来源。

验收索引：本任务 implementation 的 `t03-first-accepted-7d0f341.json`、`t03-second-and-ticket-review-e1e810d.md`、`t03-diagnostic-closure-and-ticket-review-577979b.md` 与最终 `t03-original-call-closure-and-ticket-review-5a2de9b.md`。诊断以原调用身份、首次 base_revision、原 committed receipt 及已消费来源为准；完整回执保留，当前索引固定有限。

本结论限于已验证工程路线；原生/ledger 现场、全仓最终验证和效率对照仍由 T06 / 交付核验，不以 adapter fixture 或局部通过替代。
