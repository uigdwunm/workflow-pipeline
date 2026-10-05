# 04 — 有效结果保留、受影响复核与候选恢复

Status: ready-for-agent
Publication: published — local Markdown tracker
Review: accepted fep-solution-review-20261001-01
Ticket ID: FEP-04
Plan Version: 1

**What to build:** 未变计划/依赖/验证/只读输入/文件指纹的 accepted 结果有 carry-forward 依据，不重做。 本切片完整覆盖其 C/B/Git/native 接线和行为验证，详细方案取自 Spec D3, D4, D6。

**Blocked by:** 02, 03

- [ ] AC-1: 未变计划/依赖/验证/只读输入/文件指纹的 accepted 结果有 carry-forward 依据，不重做。
- [ ] AC-2: 受影响和未验收字节保留，但只能经 fresh allocation/adoption 真实复核后成为当前交付。
- [ ] AC-3: 正式计划修订使旧候选、两轴审查和 final attempt 成为历史，即使 code bytes 未变。
- [ ] AC-4: pause/cancel/native unknown/宿主恢复/Stage4 publication recovery 使用原身份、worktree 与当前计划，不重放未知结果。

## 实施与验证依据

Spec: `../PRD.md`；冻结需求 AC 与映射见 PRD N3。本 Ticket 只拥有实施切片和阻塞关系；不改变用户合同或给执行者修订权。多个函数可由一个 Execution Agent 完成并共用上述行为验证。共享实现文件按 Ticket 顺序串行。

## 函数级执行计划

该 block 是本 Ticket 的正式可修订区域；行为与上方 AC 是独立合同。既有符号已从维护源定位，add 为此次拟新增；执行者不得把私有分解当公共合同调整。

```execution-plan-v1
{
  "schema_version": 1,
  "ticket_id": "FEP-04",
  "plan_version": 1,
  "spec_ref": {
    "path": ".scratch/function-execution-plans/PRD.md",
    "section": "Implementation Decisions"
  },
  "contract_refs": [
    "FEP-04:What to build",
    "FEP-04:AC-1",
    "FEP-04:AC-2",
    "FEP-04:AC-3",
    "FEP-04:AC-4",
    "PRD:D1:Execution plan schema and public interfaces",
    "PRD:D3:Controller-only revision transaction",
    "PRD:D4:Dispatcher roles and byte provenance",
    "PRD:D5:Public contracts and business impact boundary",
    "PRD:D6:Candidate review and verification contracts",
    "PRD:D7:Compatibility key and local metadata version"
  ],
  "blocked_by": [
    "FEP-02",
    "FEP-03"
  ],
  "verifications": [
    {
      "id": "V4-behavior",
      "behavior": "未变计划/依赖/验证/只读输入/文件指纹的 accepted 结果有 carry-forward 依据，不重做。; 受影响和未验收字节保留，但只能经 fresh allocation/adoption 真实复核后成为当前交付。; 正式计划修订使旧候选、两轴审查和 final attempt 成为历史，即使 code bytes 未变。; pause/cancel/native unknown/宿主恢复/Stage4 publication recovery 使用原身份、worktree 与当前计划，不重放未知结果。",
      "seam": "原 C handle/B prepare-dispatch-bind/Git fixture 的可观察授权、字节与结果资格；外部 native transport downstream doubles",
      "commands": [
        "python3 scripts/run_tests.py --test-file tests/runtime/test_review_first_validation.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_dispatch_recovery.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_unified_recovery.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_business_recovery.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_attached_design_recovery.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_design_recovery.py"
      ]
    }
  ],
  "steps": [
    {
      "id": "FP4-01",
      "target": {
        "path": "src/shared/scripts/execution_plan.py",
        "symbol": "affected_plan_closure",
        "operation": "add"
      },
      "capability": "从事实推出需要暂停与重新验证的依赖闭包",
      "approach": [
        "比较 step body/契约/target/read_only/verification",
        "把变化沿函数/Ticket DAG传播，保留 retired IDs及未知风险"
      ],
      "inputs": [
        "old/new catalogs、reason/changed IDs"
      ],
      "outputs": [
        "impacted IDs、verification/文件集合"
      ],
      "failures": [
        "缺依赖/ambiguous contract → 阻断"
      ],
      "depends_on": [
        "FP3-10"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-02",
      "target": {
        "path": "src/shared/scripts/execution_plan.py",
        "symbol": "classify_retained_results",
        "operation": "add"
      },
      "capability": "判定当前 revision 可保留的 accepted 实现",
      "approach": [
        "要求 plan step digest/依赖/验证/readonly/归属均未变且 actual fingerprints匹配",
        "给出 carry-forward与exact revalidation/adopt集合，原result不改"
      ],
      "inputs": [
        "old/new catalog、历史 results、Git snapshot"
      ],
      "outputs": [
        "carry-forward mapping 与 unaccepted revalidation_source"
      ],
      "failures": [
        "未知归属/hash/依赖变化 → 不可 carry"
      ],
      "depends_on": [
        "FP4-01"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-03",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "adoption_source",
        "operation": "modify"
      },
      "capability": "让受影响字节使用现有真实 Agent 复核",
      "approach": [
        "在原 integration/recovery source之外接受已 activate plan-revision snapshot",
        "不恢复 direct资格",
        "不同来源不能混写"
      ],
      "inputs": [
        "current progress/revision source"
      ],
      "outputs": [
        "exact unaccepted adoption snapshot"
      ],
      "failures": [
        "来源漂移/旧 revision → 拒绝"
      ],
      "depends_on": [
        "FP4-02"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-04",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "validate_review_axes",
        "operation": "modify"
      },
      "capability": "两个独立审查必须指向当前计划",
      "approach": [
        "要求每 axis 的 candidate/base/plan-validation digest之外 current scope/exec-plan/contract digests一致",
        "reviewer真实 stopped receipt保持"
      ],
      "inputs": [
        "current candidate、review/receipts"
      ],
      "outputs": [
        "verified axes"
      ],
      "failures": [
        "old review/current plan mismatch → 拒绝"
      ],
      "depends_on": [
        "FP4-02"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-05",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "delivery_verification",
        "operation": "modify"
      },
      "capability": "Stage3交付只来自当前合格结果与完整验证",
      "approach": [
        "投影 current execution authority/carry/adoption qualification及原 dispatcher身份",
        "latest passed final attempt必须同current candidate和plans"
      ],
      "inputs": [
        "progress、dispatcher ref"
      ],
      "outputs": [
        "strict current verification projection"
      ],
      "failures": [
        "history passed/旧 plan/未 adoption → not deliverable"
      ],
      "depends_on": [
        "FP4-04"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-06",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "transition",
        "operation": "modify"
      },
      "capability": "计划切换后重建候选与结果资格",
      "approach": [
        "activate时历史 accepted不改字节、有效集合按 disposition重建",
        "candidate-ready每changed path current有效",
        "review/validation旧尝试失效保留",
        "cancel/paused优先"
      ],
      "inputs": [
        "原 lifecycle actions/current revision"
      ],
      "outputs": [
        "current effective roster/candidate/history"
      ],
      "failures": [
        "old release/result/attempt不得晋升"
      ],
      "depends_on": [
        "FP4-03",
        "FP4-05"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-07",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.control_action",
        "operation": "modify"
      },
      "capability": "原 C 统一把 candidate/review/recovery 绑定当前权威",
      "approach": [
        "保留 stop/call来源",
        "new candidate/review/final/controlled recovery需current plan绑定",
        "未决 revision不开放candidate",
        "旧delivery不可原地改写"
      ],
      "inputs": [
        "原 bounded control request/actual receipts"
      ],
      "outputs": [
        "durable transaction/next_action"
      ],
      "failures": [
        "unknown、错revision、旧accepted delivery → 拒绝"
      ],
      "depends_on": [
        "FP4-06"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-08",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.continuation_gate",
        "operation": "modify"
      },
      "capability": "修订和恢复只继续原有效身份",
      "approach": [
        "读取current effective authority而非重写old launch",
        "fresh same-ref resumability与所有原调用settled",
        "pause/cancel优先",
        "revalidate current roster"
      ],
      "inputs": [
        "saved intent/current original host proof"
      ],
      "outputs": [
        "continue original identity only"
      ],
      "failures": [
        "idle/旧receipt/未决调用 → lookup/wait"
      ],
      "depends_on": [
        "FP4-07"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-09",
      "target": {
        "path": "src/shared/scripts/stage_handoff.py",
        "symbol": "verify_result",
        "operation": "modify"
      },
      "capability": "B接收和Stage4 handoff不得漏掉当前版本",
      "approach": [
        "验证current revision/plan/contract/candidate/review/final evidence",
        "保留original stage input ancestry及revision history，Stage4只接最新accepted candidate"
      ],
      "inputs": [
        "原 completion/current control"
      ],
      "outputs": [
        "verified current delivery projection"
      ],
      "failures": [
        "old result/partial hashes → 拒绝"
      ],
      "depends_on": [
        "FP4-05",
        "FP4-07"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP4-10",
      "target": {
        "path": "src/shared/scripts/workflow_control_git.py",
        "symbol": "execution_delta",
        "operation": "modify"
      },
      "capability": "planning-controller提交不破坏真实implementation attribution",
      "approach": [
        "仅认可已activate planning ancestry",
        "implementation baseline与index变化guard不弱化",
        "adopt范围和peer结果按current资格核验"
      ],
      "inputs": [
        "repository/progress/allocation"
      ],
      "outputs": [
        "actual assigned delta/pending peers"
      ],
      "failures": [
        "额外字节/旧adoption/drift → error"
      ],
      "depends_on": [
        "FP4-03",
        "FP4-07"
      ],
      "verification_refs": [
        "V4-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    }
  ],
  "artifact_steps": [
    {
      "id": "AP4-01",
      "path": "src/stages/change-closure/references/closure-protocol.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V4-behavior"
      ]
    },
    {
      "id": "AP4-02",
      "path": "tests/runtime/test_review_first_validation.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V4-behavior"
      ]
    },
    {
      "id": "AP4-03",
      "path": "tests/runtime/test_dispatch_recovery.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V4-behavior"
      ]
    },
    {
      "id": "AP4-04",
      "path": "tests/runtime/test_unified_recovery.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V4-behavior"
      ]
    },
    {
      "id": "AP4-05",
      "path": "tests/runtime/test_business_recovery.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V4-behavior"
      ]
    },
    {
      "id": "AP4-06",
      "path": "tests/runtime/test_attached_design_recovery.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V4-behavior"
      ]
    },
    {
      "id": "AP4-07",
      "path": "tests/runtime/test_design_recovery.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V4-behavior"
      ]
    }
  ]
}
```

## Comments

Stage 2: 基于冻结需求和真实调用链设计；当前为待 combined review 的草稿，尚未 native publication 或实施。

Stage 2 publication: 用户已以“确认，连续模式”接受本 Ticket 的粒度、blocking edges 与测试 seam；ready-for-agent 和原 Blocked by 已发布至本地 tracker，实施尚未开始。授权引用：user-current-conversation:confirm-continuous:fep-solution-review-20261001-01。


### 本次交付完成记录 — 2026-10-05

Lifecycle: completed

用户最新选择“先交付原需求吧”，本 Ticket 在本次 Ticket 主流程交付范围内完成；原函数计划、版本、合同与 AC checkbox 保持原样。补充 standalone-no-A / foreground / attached ingress 延期，不纳本次交付、未标通过。

实际 revision 2 保留未受影响的已验收结果；受影响 CLI 与只读依赖改变的 Facade 经 fresh allocation/adoption 真实复核后验收，旧结果不覆写。新当前候选完成独立两轴审查与 31 项完整验证，实际 current-revision Stage 4 接收、合并和清理通过。暂停及原 continuation 恢复已验；取消、未知创建、宿主丢失、目标竞态 publication recovery 不因该样例正常归档获得额外现场通过声明。

完整事实、限制及证据路径见 [PRD 交付记录](../PRD.md#交付记录2026-10-05)。本次由用户最新授权作普通本地交付，不补造旧 production C 的 Stage-4 验收或迁移，不创建新的 C；原记录与失败试验保留。
