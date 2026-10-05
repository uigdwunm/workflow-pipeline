# 03 — 原上下文修复与提前集成验证

Status: needs-info
Design status: draft — 统一审阅前不可领取
Blocked by: 02
Spec: ../PRD.md
Scope inventory: ../manifest.json（T03）

## What to build

局部review发现缺陷后回到原Execution Agent的新invocation修复，用相同独立reviewer复查增量；相关片/共享夹具变化时尽早跑集成检查。最终交付只在三层证据有效且当前完整验证通过时完成。

## 合同和验收

- [ ] 原native ref停稳可继续时复用，repair有新invocation/lease，旧回执不能满足新修复。
- [ ] 上下文只含64KiB摘要及引用；超限按段展开，失效host才替换；未知原调用阻止换人重做。
- [ ] 局部regression/affected先跑；两个片整合或共享fixture变化后立即做Ticket检查，不能拖至所有review结束。
- [ ] 最终完整测试失败后只重新派受影响片和对应review；最终完整测试必须针对新候选重跑。
- [ ] 原完整验证过程pause/结果未知保留attempt；新候选不能掩盖运行中命令。
- [ ] 第一次片ID/提交序号保留，重试不美化首次验收通过率。

## Function Execution Plan（草案）

| Step | 目标符号 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | `Progress.allocation/issue_allocation/require_allocation_active/review_activity`、`stage_dispatch.record_for/matching/bound/received` | findings+原ref+已停止证据→新repair调用；预算/身份/未知状态阻止调用，原不可用则按同subject有证据替换 | 无 |
| S02 | `stage_handoff.prepare/_prepare/render`、`workflow.py._stage_prompt` | 当前合同/片/依赖摘要→有限executor/reviewer上下文，scope仍精确；遗漏必要合同拒绝派发 | S01 |
| S03 | `workflow_control.validate_validation_plan/validate_checks/validate_result_source/_transition_single` | verification refs及触发影响→local/Ticket/early-integration/final计划；validation-start保留固定候选；失败/unknown不能pass | S01 |
| S04 | `Progress.control_action/observe/suspend/resume`、`workflow.py._invoke/_advance_in_host` | 验证attempt与host结果→同身份回执、暂停恢复/局部失效；修复后保留无关claim且final pass必须重跑 | S02、S03 |

不另造后台测试调度器，使用现有Dispatcher调用和命令身份。必跑check的集合由计划与影响声明确定，遗漏check在Ticket或final门禁拒绝，不能由执行者任意缩小。

## Verification references

V01：`test_review_first_validation.py` 场景先引入公共fixture失配，在Ticket检查发现，断言无需等交付review；修复后旧完整pass仍无效。

V02：`test_pending_host_actions.py`、`test_lifecycle_snapshot_retry.py`、`test_collaboration_lifecycle.py`、`test_foreground_host.py` 验证原调用继续/宿主失效替换/暂停时结果未知。两种native adapter保留各自身份和停止证明。

V03：`test_layered_review.py` 统计实际新派Agent数、复用ref、摘要读取字节；检查不依赖模型生成的时间估计。

所有测试经 `scripts/run_tests.py --test-file`。现有CLI支持路线只同步新状态/上下文；本Ticket不新增foreground Controller ingress。

## Comments

待统一用户审阅，非Stage3 handoff。
