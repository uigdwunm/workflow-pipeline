# 04 — 共享Git短屏障下的局部计划修订

Status: ready-for-agent
Design status: accepted — 普通本地连续实现已授权
Blocked by: 02
Spec: ../PRD.md
Scope inventory: ../manifest.json（T04）

## What to build

一个Ticket的私有计划调整只修改必要规划文件，短暂停止全部共享writer完成Git提交和authority切换；未影响另一Ticket的执行结果、审查和上下文继续有效。公共合同或用户可见范围变化继续交用户确认。

## 合同和验收

- [ ] 函数体内等价选择不进入正式修订；计划/文件/依赖/验证改变进入Controller修订。
- [ ] 未决宿主调用、活跃writer或验证命令阻止规划写release；不能按文件独立锁绕开全worktree HEAD/index屏障。
- [ ] Controller只改精确planning_paths，唯一父提交/无关index/实现指纹不变；失败不覆盖原权限。
- [ ] activation一次CAS；原lease撤销，无关片新lease复用原ref和证据，受影响片重新验收。
- [ ] 旧planning refs、旧scope receipts不能通过新authority；公共合同不变必须机械比较加Controller判断。
- [ ] 中断恢复找到原修订事务，不再开第二写入窗口、不迁移旧运行。

## Function Execution Plan（草案）

| Step | 目标符号 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | `execution_plan.contract_projection/affected_plan_closure/classify_retained_results` + `review_evidence.affected_claims/applicable_claims`（T02新增） | 旧/新计划和Git差异→精确失效及carry证明，公共合同变化返回用户门禁，unknown扩大而不假定无影响 | 无 |
| S02 | `Progress.prepare_plan_revision/advance_plan_revision/plan_revision_path_changes` | 原Controller请求→预计算闭包与短停intent，确认全部writer真实停稳才给planning release | S01 |
| S03 | `workflow_control_git.plan_revision_snapshot/verify_plan_revision/verify_stopped_snapshot` | 停止快照+精确planning commit→不可变验证receipt；HEAD/index/保护路径意外变化拒绝激活 | S02 |
| S04 | `Progress.commit_plan_revision/activate_plan_revision/reconcile_plan_revision`、`workflow_control._transition_single` | 已验证提交→一次authority切换/新leases；只退役受影响claims，所有未决操作仍钉住；异常恢复原transaction | S03 |

T03修复复用是优化配合但不是本Ticket前置：本Ticket可用现有原host continuation表达新lease；T06验收两者组合。保持既有计划修订可用路线，不实现无Ticket初始化桥或foreground ingress。

## Verification references

V01：`test_plan_revision_progress.py`、`test_execution_plans.py`、`test_workflow_control_git.py` 真实临时Git：两Ticket，一处私有helper拆分；无关claim/字节保留，旧plan拒绝，index原有无关staged分区保持。

V02：`test_pending_host_actions.py`、`test_unified_recovery.py` 注入stop未知/提交前后崩溃/authority已切而返回丢失，只有原事务能继续；不可自动rollback已经应用的权限。

计量停止请求→新lease与规划实际写提交两个时段。通过runner运行focused tests；最终不把“短屏障”写成未测得的毫秒承诺。

## Comments

用户已统一接受并选择连续实现；此为普通本地授权，非正式Stage3 handoff。
