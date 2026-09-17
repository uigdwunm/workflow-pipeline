# B：阶段交接与任务派发

Status: ready-for-human

用户已在本任务明确批准 B 完整方案。实现位于 `codex/stage-handoff-dispatch`，
基线为 `f01033b5773c0e762d23555a37a7a50d58106fb7`。精确提交号由交付回复记录。

## 公共接口与边界

- `src/shared/scripts/stage_handoff.py`：完整 A 输入、原始包身份、路径/权限边界、
  目标交付与前序结果的核验；生成机器输入、完成交接及现有 runner 结果投影。
- `src/shared/scripts/stage_dispatch.py`：准备一次派发、绑定真实宿主回执、精确对账、
  接收及验收。控制状态仍由既有 Workflow Control/讨论 ledger/调用方 checkpoint 拥有。
- `src/shared/references/stage-transfer.md`：严格字段、正常/失败/结果不明/重放路径、
  宿主信任边界、持久化顺序与 A/C/D 接入合同。
- 新兼容键为 `stage_transfer=workflow-stage-transfer-v1`；旧运行保留原 pinned package。
  所有 `skills/` 副本由 `scripts/build_skills.py` 生成。

没有真实创建业务任务，没有调用原生子 Agent 进行现场验收，没有部署或修改本机安装，
没有推送、合并 main、归档任务、清理用户 Worktree 或修改外部治理插件。

## 共享所有权

A 的 entry_prepare/requirement_prepare 实现保持原样。讨论 read-topic 增加当前
workflow_control 和 topic_document_path 的只读投影，用于响应丢失后的权威对账。
control 增加设计者及原生创建结果转换、dedicated reservation；Git adapter 校验其绑定。
non-Git Stage 0 交付保留 snapshot 权威，不能伪造 Git commit。

C 拥有 workflow.py 的循环、等待、恢复、记录升级和实际调用点接入。B 的 render
可产生当前 schema 的 stage_result，但没有修改或自动升级既有 runner。
D 拥有发布/合并/清理动作；B 检查已接受候选、原范围基线、实际 ancestry 与清理事实。

独立 Stage1→2 交付任务负责真正交付。B 不实现整合，只检查目标分支。当前不同源/目标
提交的接收合同为 `{commit,paths,receipt}`，支持单父限定文档交付提交，并核验所有列出
文档的源/目标字节和模式。merge 型交付需要先协调更完整的证明合同；不得仅凭相同需求
字节放行。普通源提交已在目标历史上的情况不需要另造交付操作。

无 A requirement 的既有显式 standalone Stage 3 继续使用原 standalone gate/control
路径，不虚构文档来套用 B。该保留路径与旧 pinned 运行都不是 B 自动迁移对象。

## 验证证据索引

`tests/runtime/test_stage_transfer.py` 使用真实临时 Git、A 的写入/冻结/核验、
Flow Worktree 创建/规划发布/最终发布/清理，以及真实讨论 ledger。仅替换宿主设置、
注册和任务回执边界，没有复制被测 A/Git/ledger 实现作为 mock。

| 关键情况 | 测试 |
| --- | --- |
| A 源冻结、脏主检出保护、freshness | test_real_a_freeze_and_flow_transfer_preserve_user_changes |
| partial evidence、来源漂移、保护路径重叠 | test_source_drift_partial_evidence_and_protected_scope_block |
| 不同源/交付提交，目标先缺失后交付 | test_distinct_source_and_bounded_delivery_commits_use_real_a |
| pending/unknown 后精确 lookup，不再次 bind/create | test_unknown_pending_and_late_receipts_never_relaunch |
| 明确未创建、迟到拒绝 | test_explicit_not_created_releases_only_original_attempt |
| 错 attempt、client ID、配置漂移 | test_wrong_attempt_and_client_id_cannot_bind |
| idle/turn 完成不代表业务完成 | test_idle_and_turn_complete_are_not_business_completion |
| 模式漂移、设计者不能拥有代码路径 | test_target_mode_change_and_non_document_design_scope_are_rejected |
| 规划实际发布、接收/接受、重复 ACK/冲突 | test_real_design_publication_receive_accept_and_duplicate |
| 实际 Stage 2→3→4 Git 交接，零新增收尾文档 | test_accepted_design_consumed_by_stage3_and_actual_candidate_review |
| 接收后漂移拒绝验收 | test_result_drift_between_receive_and_accept_blocks |
| dedicated 重复 reservation 无 host_call | test_dedicated_reservation_replay_has_no_host_call |
| execution allocation 实际 delta、接受不冒充阶段完成 | test_execution_allocation_uses_actual_git_delta_and_is_not_stage_completion |
| ledger 真正绑定及回执丢失恢复 | test_real_ledger_binds_exact_carrier_and_recovers_lost_response |
| stale caller context 不能复活已取消 ledger attempt | test_cancelled_ledger_attempt_cannot_bind_using_stale_context |
| 非 Git CP 完成与接收，不伪造提交 | test_non_git_snapshot_can_complete_stage0_without_git_claims |
| CLI 重复键、错误边界 | test_cli_rejects_duplicate_keys_and_hides_host_errors |

上述不是实际宿主行为验证。真实创建、异步 ready 身份解析、原生停止证明、丢失响应后
的精确 lookup，以及宿主对模型/强度的认证仍需单独现场验收。工具不提供精确关联时保持
unknown，不能从标题、时间或候选列表猜测身份。摘要及 receipt 字符串不提供身份认证。

附着下游 Phase Run 接入依赖原 claim/ready/activate 规则；B 不推进 Phase。新的
Stage-2 wrapper 边界在代码中核验 exact run/attempt/source checkpoint，实际跨宿主
附着 2→3→4 连续执行未作现场验收。不得把本地测试通过称为 A/B/C/D 全流程已完成。

## 必要检查

实施检查命令为 `python3 scripts/build_skills.py`、`bash scripts/validate.sh` 和
`git diff --check`。首次全量运行发现 UUIDv5 与既有 UUIDv4 幂等键约束不兼容；
已改为保留可重复派生的 UUIDv4 格式，并通过真实 ledger 响应丢失恢复测试。
最终 `bash scripts/validate.sh` 通过：424 项测试，5 个有效 Skill 包，仓库验证
`state=valid`、`issues=[]`、22 个测试文件。生成包一致性和 `git diff --check`
均通过。新增 transfer 测试共 19 项（包含 Git/非 Git 两种 ledger 绑定场景）。

## 固定提交审查后的三项修复

协调任务对 `9d34a983781eeb112246b21106b7d367f1440a01` 的 Standards/Spec 审查
提出三项 P1 覆盖遗漏。先新增三项真实行为回归，在未修代码时分别观察到
allocation is no longer eligible、write scope overlaps protected paths、
control stage or requirement differs 三个预期失败，再修复 B 层。

1. 取消后的未绑定 allocation：仅 reconcile 精确 lookup 的 unknown/pending/
   not-created 可继续；已证明未创建的分配释放并可重放 ACK。父 dispatcher 保持
   cancelled，late ready 仍拒绝，后续 recover-dispatch 仍必须证明旧 writer 停止。
   对应 test_cancelled_dispatcher_can_reconcile_unbound_allocation_before_recovery。
2. 双 slot 派发：authorization.control_plan_id 绑定已确认的精确 plan，先选择
   slot，再核验阶段/需求/配置和 reservation。完整 ledger context 与旧任务接受/
   归档时序不变。真实 ledger 测试覆盖 B prepare、pending bind、精确 lookup、丢失
   响应、错误/缺失 selector、旧 attempt 和取消后的迟到回执。
   对应 test_successor_dispatch_uses_real_ledger_slot_without_promoting_old_carrier。
3. 阶段文档权限：Stage 3 的 protected_paths 继续包含规划文档，closure_paths
   只是未来授权；Stage 2→3 不扩张该清单。Stage 4 仅可移除已授权 closure_paths
   的保护，冻结需求永不移除。Stage-3 render 的下游投影按此生成 Stage-4 保护范围，
   embedded accepted_transfer 保留原实施只读范围。真实 Git 链路证明实施修改已提交
   Spec 会拒收；合法收尾 Spec 更新可发布/清理并 receive/accept；冻结需求、实现
   代码和未授权 ADR 改动都拒收。
   对应 test_planning_read_only_in_implementation_but_authorized_for_closure。

修复仅改 B 的核验/投影、合同和测试，未改 A、C runner 或 D 发布/清理实现。
双 slot 消费者需要传入从原 ledger plan 取得的 control_plan_id；单 slot 可省略。
真实宿主现场未验证项、独立 Stage1→2 交付 producer 和 merge 证明边界不变。
修复后定向套件 23 项：22 项通过，1 项因非 Git 不适用 Git wrapper 场景跳过；
该场景已在 Git ledger fixture 中真实执行。修复后 `bash scripts/validate.sh`
完整通过：428 项中 427 项成功、1 项上述适用性跳过；5 个 Skill 包有效，仓库
`state=valid`、`issues=[]`。生成包一致性及暂存差异检查通过。精确修复提交由交付回复记录。
