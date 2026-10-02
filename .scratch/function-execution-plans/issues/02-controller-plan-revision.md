# 02 — 原 Controller 在合同内原子修订计划

Status: ready-for-agent
Publication: published — local Markdown tracker
Review: accepted fep-solution-review-20261001-01
Ticket ID: FEP-02
Plan Version: 1

**What to build:** 内部函数调整无需新用户决定，只有原 Controller 获得 exact planning-only release。 本切片完整覆盖其 C/B/Git/native 接线和行为验证，详细方案取自 Spec D2, D3, D8。

**Blocked by:** 01

- [ ] AC-1: 内部函数调整无需新用户决定，只有原 Controller 获得 exact planning-only release。
- [ ] AC-2: 全部当前 writer/reviewer/validation 和未决调用停止/核对前，没有任何计划写入或权限 activation。
- [ ] AC-3: planning commit 不包含实现字节，不可修订合同与未获规划权限的 protected 字节不变。
- [ ] AC-4: 同 revision_id 中断/重放恢复唯一权威；pause/cancel/unknown 不复活写入。

## 实施与验证依据

Spec: `../PRD.md`；冻结需求 AC 与映射见 PRD N3。本 Ticket 只拥有实施切片和阻塞关系；不改变用户合同或给执行者修订权。多个函数可由一个 Execution Agent 完成并共用上述行为验证。共享实现文件按 Ticket 顺序串行。

## 函数级执行计划

该 block 是本 Ticket 的正式可修订区域；行为与上方 AC 是独立合同。既有符号已从维护源定位，add 为此次拟新增；执行者不得把私有分解当公共合同调整。

```execution-plan-v1
{
  "schema_version": 1,
  "ticket_id": "FEP-02",
  "plan_version": 1,
  "spec_ref": {
    "path": ".scratch/function-execution-plans/PRD.md",
    "section": "Implementation Decisions"
  },
  "contract_refs": [
    "FEP-02:What to build",
    "FEP-02:AC-1",
    "FEP-02:AC-2",
    "FEP-02:AC-3",
    "FEP-02:AC-4",
    "PRD:D1:Execution plan schema and public interfaces",
    "PRD:D3:Controller-only revision transaction",
    "PRD:D4:Dispatcher roles and byte provenance",
    "PRD:D5:Public contracts and business impact boundary",
    "PRD:D6:Candidate review and verification contracts",
    "PRD:D7:Compatibility key and local metadata version"
  ],
  "blocked_by": [
    "FEP-01"
  ],
  "verifications": [
    {
      "id": "V2-behavior",
      "behavior": "内部函数调整无需新用户决定，只有原 Controller 获得 exact planning-only release。; 全部当前 writer/reviewer/validation 和未决调用停止/核对前，没有任何计划写入或权限 activation。; planning commit 不包含实现字节，不可修订合同与未获规划权限的 protected 字节不变。; 同 revision_id 中断/重放恢复唯一权威；pause/cancel/unknown 不复活写入。",
      "seam": "原 C handle/B prepare-dispatch-bind/Git fixture 的可观察授权、字节与结果资格；外部 native transport downstream doubles",
      "commands": [
        "python3 scripts/run_tests.py --test-file tests/runtime/test_plan_revision_progress.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_workflow_control.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_workflow_control_git.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_attached_flow_progress.py"
      ]
    }
  ],
  "steps": [
    {
      "id": "FP2-01",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.prepare_plan_revision",
        "operation": "add"
      },
      "capability": "为原 Controller 打开有界规划写入窗口",
      "approach": [
        "检查报告和 current authority",
        "关闭分配/accept/candidate，取得全停止与 calls settlement",
        "冻结 Git/index/files 和合同",
        "journal 旧权威与 exact planning release"
      ],
      "inputs": [
        "revision ID、旧 revision/digest、plan/manifest diff proposal、Controller reason/classification refs"
      ],
      "outputs": [
        "stopping 或 prepared planning-only window"
      ],
      "failures": [
        "旧 revision/错误 owner/unknown effect/合同外 → 无 release"
      ],
      "depends_on": [
        "FP1-08",
        "FP2-09"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-02",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.commit_plan_revision",
        "operation": "add"
      },
      "capability": "将规划候选和权限一起切换",
      "approach": [
        "核对 saved window、only-planning commit 父子树、implementation/index 分区和合同",
        "重验计划/manifest闭包",
        "journal control transaction 并 activation 单一 current authority"
      ],
      "inputs": [
        "prepared revision、actual planning commit/inline projection、current host facts"
      ],
      "outputs": [
        "activated authority 与 carry/revalidate disposition"
      ],
      "failures": [
        "任何 drift → 保留 planning-committed barrier"
      ],
      "depends_on": [
        "FP2-01",
        "FP2-09"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-03",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.reconcile_plan_revision",
        "operation": "add"
      },
      "capability": "恢复同一计划事务的剩余效果",
      "approach": [
        "查原 saved intent、Git exact commit/parent/tree、control result",
        "已 commit 不再 commit、已 activate 只 ACK",
        "未决先 query original action"
      ],
      "inputs": [
        "原 revision ID 与原宿主/Git evidence"
      ],
      "outputs": [
        "当前 stopping/prepared/committed/activated 状态及唯一 next_action"
      ],
      "failures": [
        "未核对 commit/host → lookup/blocked，禁止重放"
      ],
      "depends_on": [
        "FP2-02"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-04",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.advance",
        "operation": "modify"
      },
      "capability": "让修订屏障和暂停取消优先于派发",
      "approach": [
        "原 stop/publication-recovery 优先",
        "有 revision transaction 时只返回其 exact stop/write/verify/activate 剩余动作",
        "paused 不继续"
      ],
      "inputs": [
        "原 state 与保存的 revision transaction"
      ],
      "outputs": [
        "精确 next_action"
      ],
      "failures": [
        "pending transaction 不准普通 continuation"
      ],
      "depends_on": [
        "FP2-03"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-05",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "handle",
        "operation": "modify"
      },
      "capability": "公开有界修订操作而不建立第二记录",
      "approach": [
        "在原 record lock 中路由三个 exact operations",
        "保留 strict fields/revision guards/原 native identity"
      ],
      "inputs": [
        "workflow-progress-v18 stdin request"
      ],
      "outputs": [
        "C structured result/next_action"
      ],
      "failures": [
        "unknown fields/错误 stage/actor → 拒绝"
      ],
      "depends_on": [
        "FP2-04"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-06",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "transition",
        "operation": "modify"
      },
      "capability": "唯一 control 决策验证修订与原 owner",
      "approach": [
        "schema6 新 actions 验 current authority/digests/Controller receipt/snapshot",
        "保存 revision history/current plan，无重写 launch/results",
        "拒绝合同变更走内部入口"
      ],
      "inputs": [
        "原 context、plan revision evidence"
      ],
      "outputs": [
        "updated context、明确 effects 或 ACK"
      ],
      "failures": [
        "非 Controller/已取消/contract drift → error"
      ],
      "depends_on": [
        "FP1-04",
        "FP2-07"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-07",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "validate_context",
        "operation": "modify"
      },
      "capability": "校验新的持久 control 结构并拒绝旧可执行记录",
      "approach": [
        "使用 schema6",
        "校验当前 plan authority、revision history/transaction identity",
        "旧 schema要求原 runtime，不补字段"
      ],
      "inputs": [
        "control context"
      ],
      "outputs": [
        "已验证 context"
      ],
      "failures": [
        "legacy_run_requires_original_runtime / invalid context"
      ],
      "depends_on": [
        "FP1-02"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-08",
      "target": {
        "path": "src/shared/scripts/workflow_control_git.py",
        "symbol": "verify_plan_revision",
        "operation": "add"
      },
      "capability": "取得规划写入窗口的真实 Git 证据",
      "approach": [
        "核验 registered Flow、HEAD/branch/index 分区、implementation 和 protected presence/content/mode",
        "比对 exact only-planning commit 和不可修订 projection",
        "绝不清理 bytes"
      ],
      "inputs": [
        "repository、旧 snapshot、plan commit/window"
      ],
      "outputs": [
        "actual derived facts、implementation unchanged、contract unchanged"
      ],
      "failures": [
        "unexpected bytes/index/history → snapshot_changed"
      ],
      "depends_on": [
        "FP1-02",
        "FP1-04"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-09",
      "target": {
        "path": "src/shared/scripts/workflow_control_git.py",
        "symbol": "verified_transition",
        "operation": "modify"
      },
      "capability": "把原有 Git adapter 接入 revision actions",
      "approach": [
        "在纯 control 前调用 verify_plan_revision",
        "由 Git enrich 而非 self-report",
        "普通行为保持整文件 protected guards"
      ],
      "inputs": [
        "原 Git envelope/new revision action"
      ],
      "outputs": [
        "verified control transition"
      ],
      "failures": [
        "违规 planning/impl/protected diff → 拒绝"
      ],
      "depends_on": [
        "FP2-06",
        "FP2-08"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP2-10",
      "target": {
        "path": "src/shared/scripts/discussion_core/workflow_control.py",
        "symbol": "workflow_control",
        "operation": "modify"
      },
      "capability": "附着运行仍在原 ledger 保存决策",
      "approach": [
        "将内嵌 control schema 更新为6",
        "保留原 owner/binding/idempotency/ledger transaction",
        "原 schema5不可更新，新 revision evidence仍经原 control"
      ],
      "inputs": [
        "现有附着 workflow-control envelope"
      ],
      "outputs": [
        "同 ledger 的 control result"
      ],
      "failures": [
        "old embedded schema/owner drift → 拒绝"
      ],
      "depends_on": [
        "FP2-05",
        "FP2-06"
      ],
      "verification_refs": [
        "V2-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    }
  ],
  "artifact_steps": [
    {
      "id": "AP2-01",
      "path": "src/shared/references/workflow-progression.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V2-behavior"
      ]
    },
    {
      "id": "AP2-02",
      "path": "src/shared/references/guided-implementation/workflow-control-protocol.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V2-behavior"
      ]
    },
    {
      "id": "AP2-03",
      "path": "tests/runtime/test_plan_revision_progress.py",
      "operation": "add",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V2-behavior"
      ]
    },
    {
      "id": "AP2-04",
      "path": "tests/runtime/test_workflow_control.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V2-behavior"
      ]
    },
    {
      "id": "AP2-05",
      "path": "tests/runtime/test_workflow_control_git.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V2-behavior"
      ]
    },
    {
      "id": "AP2-06",
      "path": "tests/runtime/test_attached_flow_progress.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V2-behavior"
      ]
    }
  ]
}
```

## Comments

Stage 2: 基于冻结需求和真实调用链设计；当前为待 combined review 的草稿，尚未 native publication 或实施。

Stage 2 publication: 用户已以“确认，连续模式”接受本 Ticket 的粒度、blocking edges 与测试 seam；ready-for-agent 和原 Blocked by 已发布至本地 tracker，实施尚未开始。授权引用：user-current-conversation:confirm-continuous:fep-solution-review-20261001-01。
