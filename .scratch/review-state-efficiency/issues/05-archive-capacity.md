# 05 — 批次归档与精确恢复闭合（收缩修订）

Status: paused
Design status: accepted with scope amendment — 用户明确要求收缩，未恢复实现
Plan revision: 2026-10-07
Blocked by: 01
Spec: ../PRD.md
Scope inventory: ../manifest.json（T05）
Execution rules: ../execution-policy.md

## 当前交付

只闭合批次归档、精确原回执/调用恢复、分页交付和固定当前/普通恢复读取预算。T05-A 在 `526c55cfe7f265a3ef2f39770f4315fc7883475f` 已有独立局部验收；保留原报告，不将它重标为整张旧 Ticket 通过。

恢复后的第一步是保存并隔离未提交 T05-B 六文件 WIP，确认核心候选未带入 GC、物理配额、双预留、账本物理归属和宿主 log spool。当前只改规划，不操作 WIP。

## 修订验收

以下替代旧八项本轮 AC；旧方案见 Git `35d5a57876badce5a4beb01c99ac2a5db62c404e`。全部尚待修订 Ticket 覆盖核对，不因 A 局部通过自动勾选。

- [ ] 32片/256事件封批、完成且停稳 subject 退出当前窗口；root 不保存累计ID，固定 root/subject/事件段预算保持。
- [ ] 归档前后 next_action、权限、claim 适用性与原调用恢复一致；未知/未停稳不能借归档获得权限。归档后的 C/序列化 B/ledger 消费者核原 source、pointer、digest 和 CAS，不信发送者自报投影。
- [ ] 原 receipt 按 exact key/hash 直接定位，只能证明已提交的原结果；同 key 不同 digest 拒绝，提交前准备项不能 ACK，提交后响应丢失不能重发。
- [ ] 普通恢复总读取符合 PRD 当前预算；分页交付能核全部必要 claims 与原 native/stop 证明。缺失/损坏/跨run 证据 fail closed，第33片/归档后 repair 由已有代表流程证据核验。
- [ ] 管理当前入口≤16KiB，关闭历史及旧下一步退出当前；原报告和恢复证据保留。未审查 B 明确排除，已有证据实际失效才补受影响检查。

## 执行步骤与验证

| 步骤 | 行为与边界 | 证据及审查 |
| --- | --- | --- |
| S01 | 保存/隔离 B，确认已审查 A 核心候选与源指纹 | WIP 可恢复、候选不混 B；不新建实现机制 |
| S02 | 核对已完成 store.compact、Progress 生命周期与 exact replay、runner/ledger/dispatch/控制消费者 | 复用 A 独立报告与原日志；只补缺口/实际变化的公开边界回归 |
| S03 | 汇总修订 AC、普通读取与分页交付覆盖，移交 T06 | 同独立 reviewer 做 Ticket 组合/覆盖核对，上层复用有效局部证据 |

V01：已接受 A 的 `tests/runtime/test_state_archive.py` 及原归档/marker/native proof/CLI/ledger/repair/final completion 日志；原报告 `t05-a-finding-closure-review-526c55c.md`。数据通过真实存储提交和公开流程取得，不手写 JSON 权限。

V02：原 `test_bounded_progress.py`、publication/business/query recovery 的适用证据，核原调用、unknown、停止与 CAS。受影响检查只经 `scripts/run_tests.py`，完整候选由 T06 经 `scripts/validate.sh`；本次规划不运行。

A 已核必要消费者为 `workflow_control.py`、`workflow_control_git.py`、`discussion_core/workflow_control.py` 和 `stage_dispatch.py`；保留其历史范围与五包映射，不能恢复旧内嵌权威或弱化权限。原始夹具适配由 T06 完成。

## 延期且未验收

T05-B 自动 GC/24小时/30天/两代扫描、整个 run 2GiB 物理配额、75/90%背压、64MiB双预留、log spool 与账本物理归属、真实 ENOSPC 专项、10000历史/轮询及100/1000/10000物理边际增长门槛全部移出本轮。B 局部日志不是整体通过证明，未提交源码不参与交付。默认不自动删临时/孤立/已提交证据，历史磁盘增长仍是已知限制；不能宣称无限运行或整个 run 物理用量已受保证。后续须另行确认范围。
