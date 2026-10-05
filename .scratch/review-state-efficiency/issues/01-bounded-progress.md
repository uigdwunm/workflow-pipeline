# 01 — 一个执行片的有界保存与真实恢复

Status: needs-info
Design status: draft — 统一审阅前不可领取
Blocked by: None
Spec: ../PRD.md
Scope inventory: ../manifest.json（T01）

## What to build

从真实 `workflow_progress.handle` 启动一个 Ticket、分配两个行为片、接收结果、停止再恢复；新root仅含当前投影和引用。相同轮询和同一原调用重放不会重复保存全控制上下文或再发宿主调用。runner、handoff和ledger读取同一当前权威的有限投影。该切片先保留既有审查门禁；新格式只在实施集成分支的新运行启用，不提前发布半成品。

## 合同和验收

- [ ] 当前root≤256KiB；相同1000次poll不增加原始证据对象/历史项，count/last_seen变化不改变权限。
- [ ] start→allocation prepare/bind/receive/accept→pause/resume使用真实Git及存储；失去响应只查询原调用。
- [ ] 副作用调用前已有fsync intent与容量预留；root提交前/后崩溃分别保留旧状态/幂等ACK。
- [ ] `inspect`、恢复、readiness、Stage3接收不会扫描全部history或hydrate全部对象；测试以读取计数/字节证明。
- [ ] ledger-backed控制只以原ledger CAS指针为权威，runner镜像丢失可恢复；legacy包/记录不转换。
- [ ] Current Root、Subject Record、receipt和blob的schema/大小/引用在提交前验证；损坏对象fail closed。

## Function Execution Plan（草案）

| Step | 目标符号（现有/拟新增） | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | 新 `workflow_state_store.open_current/read_subject/read_evidence/commit_current` | 原checkpoint owner、expected_revision→有界typed投影/不可变引用；内部canonical encode、CAS、fsync、hash、预留与提交点；冲突/容量不足在副作用前拒绝，已发调用保持unknown | 无 |
| S02 | `workflow_progress.read_record/atomic_save/validate_state`、`Progress.save/control_action/allocation/issue_allocation` | 新格式路由到S01；transaction以base_root/request/result引用代替context副本；保留旧format拒绝语义；不返回伪装旧dict的全hydrate适配 | S01 |
| S03 | `workflow_progress.handle/_view`、`Progress.observe/lifecycle_state/baseline_thread_state/stop_barrier_pending` | operation选择有限read set；poll按identity/status/cursor/result digest合并；身份变化、撤销停止、未知调用独立持久化 | S02 |
| S04 | `workflow.py._read_json/_atomic_save/_stage_projection/_live_progress/CheckpointState.refresh` | 外层runner只持当前typed投影与blob refs；保存按CAS合并其自有字段；history/last_result/dispatch不复制整份Progress | S02 |
| S05 | `stage_dispatch.checkpoint/design_scope`、`stage_handoff.verify_inline_recovery_pin/verify_stage_checkpoint_pin/current_stage3_state/accepted_stage3_progress/current_readiness`、`discussion_core.workflow_control._start_git_authority/workflow_control` | 各调用请求明确的identity/scope/current-transaction/delivery projection；验证ledger revision与projection匹配；拒绝镜像发起控制覆盖 | S02 |
| S06 | `workflow_progress.Progress.native_finalize/record_host/unresolved_host_actions`、新store副作用预留内部实现 | 有界输入→已持久intent→精确receipt，最多4业务未知调用；停止/恢复使用独立预留；超大结果保留truncated/unknown不accept | S03、S04、S05 |

这些step是行为实现计划，不意味着一函数一Agent；S01–S03作为首行为片，S04–S06作为生产读者闭合片。权威适配不能等T06才补：否则测试首片绕开真实路线。read_subject必须由调用操作给定ID，不接受“all”；只读投影不携带写lease。

## Verification references

V01：扩展 `tests/runtime/test_workflow_progress.py`、新 `test_bounded_progress.py`，首红灯即PRD的真实start/allocation/恢复与重复poll场景；不直接测文件字典拼装函数。

V02：扩展 `test_workflow.py`、`test_stage_transfer.py`、`test_unified_recovery.py`、`test_discussion_protocol.py`，覆盖外层runner及ledger提交后镜像丢失。现有其它直连fixture按manifest的verify条目核验，仅确实依赖旧内联结构时修改。

命令：逐文件 `python3 scripts/run_tests.py --test-file <上述路径>`；若已有runner占锁，报告owner并结束该次验证。源模块及新增store资源必须同次生成到五包闭包，package identity采用新key，不手改generated副本。

## 实施约束与演示

不迁移旧状态；新接口可在实现分支内部逐步接入，但本Ticket完成时所有列明生产读者均能读新格式。演示输出包括原/新文件字节、对象读取数、同一调用工具执行计数、暂停后next_action和原native identity。不要用单独store单测通过冒充生产闭环。

## Comments

待统一用户审阅，非Stage3 handoff。
