# 04 — 共享Git短屏障下的局部计划修订

Status: ready-for-agent
Lifecycle: completed
Design status: accepted — 普通本地连续实现已授权
Plan revision: 2026-10-07 — 用户确认执行规则调整；实现保持暂停
Blocked by: 02
Spec: ../PRD.md
Scope inventory: ../manifest.json（T04）
Execution rules: ../execution-policy.md（划片耦合、重复审查原因及返工触发）

## What to build

一个Ticket的私有计划调整只修改必要规划文件，短暂停止全部共享writer完成Git提交和authority切换；未影响另一Ticket的执行结果、审查和上下文继续有效。公共合同或用户可见范围变化继续交用户确认。

## 合同和验收

- [x] 函数体内等价选择不进入正式修订；计划/文件/依赖/验证改变进入Controller修订，修订前重做划片耦合与AC归属预检。
- [x] 未决宿主调用、活跃writer或验证命令阻止规划写release；不能按文件独立锁绕开全worktree HEAD/index屏障。
- [x] Controller只改精确planning_paths，唯一父提交/无关index/实现指纹不变；失败不覆盖原权限。
- [x] activation一次CAS；原lease撤销，无关片新lease复用原ref和证据，受影响片重新验收；每次重查有具体变化/未知影响依据，不追求无法证明的最大化继承。
- [x] 旧planning refs、旧scope receipts不能通过新authority；公共合同不变必须机械比较加Controller判断。
- [x] 中断恢复找到原修订事务，不再开第二写入窗口、不迁移旧运行。

## Function Execution Plan（草案）

| Step | 目标符号 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | `execution_plan.contract_projection/affected_plan_closure/classify_retained_results` + `review_evidence.affected_claims/applicable_claims`（T02新增） | 旧/新计划和Git差异→耦合预检、失效及重查原因/carry证明；公共合同变化返回用户门禁，unknown扩大而不假定无影响 | 无 |
| S02 | `Progress.prepare_plan_revision/advance_plan_revision/plan_revision_path_changes` | 原Controller请求→预计算闭包与短停intent，确认全部writer真实停稳才给planning release | S01 |
| S03 | `workflow_control_git.plan_revision_snapshot/verify_plan_revision/verify_stopped_snapshot` | 停止快照+精确planning commit→不可变验证receipt；HEAD/index/保护路径意外变化拒绝激活 | S02 |
| S04 | `Progress.commit_plan_revision/activate_plan_revision/reconcile_plan_revision`、`workflow_control._transition_single` | 已验证提交→一次authority切换/新leases；只退役受影响claims，所有未决操作仍钉住；异常恢复原transaction | S03 |

T03修复复用是优化配合但不是本Ticket前置：本Ticket可用现有原host continuation表达新lease；T06验收两者组合。保持既有计划修订可用路线，不实现无Ticket初始化桥或foreground ingress。

先核清影响闭包和规划分区，再完成“短停→提交→一次激活→原事务恢复”的完整行为。属于同一公共边界的修订步骤合片；必须拆分时采用串行累计增量审查。同片反复失效或同边界连续补洞按执行规则短诊断，保留原事务而非建立新修订窗口。

## 审查归属

| 验收项 | 首次检查层/证据 | 上层责任 |
| --- | --- | --- |
| 1、2、3 | 局部：V01的合同比较/Git分区，V02的真实stop未知 | Ticket查整次修订顺序与拒绝路径 |
| 4、5、6 | Ticket：V01/V02的一次CAS、选择性保留、旧scope拒绝和原事务恢复 | 交付查与T03修复/最终覆盖组合；重查列具体影响依据 |

## Verification references

V01：`test_plan_revision_progress.py`、`test_execution_plans.py`、`test_workflow_control_git.py` 真实临时Git：两Ticket，一处私有helper拆分；无关claim/字节保留，旧plan拒绝，index原有无关staged分区保持。

V02：`test_pending_host_actions.py`、`test_unified_recovery.py` 注入stop未知/提交前后崩溃/authority已切而返回丢失，只有原事务能继续；不可自动rollback已经应用的权限。

V01 先在真实计划/指纹模块边界验证同文件、共享依赖与unknown闭包，再用真实临时Git的完整修订链核验权限。V02仅实际受影响的verify夹具按manifest精确晋升；已有适用路径不机械重复整套测试。

计量停止请求→新lease与规划实际写提交两个时段。通过runner运行focused tests；最终不把“短屏障”写成未测得的毫秒承诺。

## Comments

用户已统一接受并选择连续实现；此为普通本地授权，非正式Stage3 handoff。

## 已核续调依赖（2026-10-07）

真实修订链已核：激活将无关片接受结果转入 carried，原 allocation slot 移入 history；现有 B `prepared()` 的 repair 分支仅查询当前 executions，无法取得原 task_id 继续。manifest 将 `stage_dispatch.py` 与五包输出加入 T04，仅接入原 Controller 校验的 repair-only carry/source lookup。原 native ref、nonce、停止回执和原结果不改；新 plan refs/authority、invocation 与 lease 必须重新验证，旧 slot/lease不能复活，不增加宿主入口或修改 stage_handoff。验证仍在 T04-owned 两测试文件。

## 本地工程验收（2026-10-07）

候选 `28e67527efb5dfeaa98aa44ecc12ea9aa158c152` 经原独立 reviewer 的局部增量与完整六 AC Ticket 验收通过，Standards / Spec 均无开放 finding。原共享闭包两项 finding 和历史引用 resume 中断 P1 均关闭；停止、规划分区、一次 authority 切换、原 ref 新 lease、旧回执拒绝及同 revision 恢复有真实模块、handle、Git 与 Store 证据。

代表性五 Ticket／七 allocation 的 history 从 71,551 字节收敛为 2,153 字节原证据引用；最新完整故障恢复 subject 为 257,316 字节，固定 262,144 字节上限不变。七份原槽内容、nonce、receipt、config、source 和首次提交保持原值。原容量、准备和故障 RED 日志保留，没有用小 fixture 消掉规模失败。

审查来源索引在本任务 implementation：`t04-independent-local-and-ticket-review-2655bad.md`、`t04-shared-closure-and-ticket-review-d7a6e7b.md` 与最终 `t04-reference-io-closure-and-ticket-review-28e6752.md`。未变证明保留原来源；后片影响公共边界时核验累计增量。工程结论不替代原生／ledger 现场、最终全仓验证、最大输入或效率对照，后续由 T05/T06/交付明确核验。
