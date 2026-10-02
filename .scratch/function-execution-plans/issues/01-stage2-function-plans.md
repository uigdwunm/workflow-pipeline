# 01 — Stage 2 完整计划封存与可追溯就绪

Status: ready-for-agent
Publication: published — local Markdown tracker
Review: accepted fep-solution-review-20261001-01
Ticket ID: FEP-01
Plan Version: 1

**What to build:** 缺失函数计划、失效 I/O/失败/验证引用或依赖循环时不能封存或进入 Stage 3。 本切片完整覆盖其 C/B/Git/native 接线和行为验证，详细方案取自 Spec D1, D2, D8。

**Blocked by:** None — can start immediately

- [ ] AC-1: 缺失函数计划、失效 I/O/失败/验证引用或依赖循环时不能封存或进入 Stage 3。
- [ ] AC-2: 一个 Ticket 的相关函数可以共用行为验证，完整计划目录与 manifest 一起从 C readiness 读回。
- [ ] AC-3: Spec/Ticket 合同 projection 与整文件 Git 内容身份分别保存；计划内容不替代验收行为。

## 实施与验证依据

Spec: `../PRD.md`；冻结需求 AC 与映射见 PRD N3。本 Ticket 只拥有实施切片和阻塞关系；不改变用户合同或给执行者修订权。多个函数可由一个 Execution Agent 完成并共用上述行为验证。共享实现文件按 Ticket 顺序串行。

首个 TDD：使用真实 C scope-propose 入口，缺函数计划应拒绝且 checkpoint 不变；现行代码会接受，构成真实 red；加入计划后原封存就绪链读取同 digest。

## 函数级执行计划

该 block 是本 Ticket 的正式可修订区域；行为与上方 AC 是独立合同。既有符号已从维护源定位，add 为此次拟新增；执行者不得把私有分解当公共合同调整。

```execution-plan-v1
{
  "schema_version": 1,
  "ticket_id": "FEP-01",
  "plan_version": 1,
  "spec_ref": {
    "path": ".scratch/function-execution-plans/PRD.md",
    "section": "Implementation Decisions"
  },
  "contract_refs": [
    "FEP-01:What to build",
    "FEP-01:AC-1",
    "FEP-01:AC-2",
    "FEP-01:AC-3",
    "PRD:D1:Execution plan schema and public interfaces",
    "PRD:D3:Controller-only revision transaction",
    "PRD:D4:Dispatcher roles and byte provenance",
    "PRD:D5:Public contracts and business impact boundary",
    "PRD:D6:Candidate review and verification contracts",
    "PRD:D7:Compatibility key and local metadata version"
  ],
  "blocked_by": [],
  "verifications": [
    {
      "id": "V1-behavior",
      "behavior": "缺失函数计划、失效 I/O/失败/验证引用或依赖循环时不能封存或进入 Stage 3。; 一个 Ticket 的相关函数可以共用行为验证，完整计划目录与 manifest 一起从 C readiness 读回。; Spec/Ticket 合同 projection 与整文件 Git 内容身份分别保存；计划内容不替代验收行为。",
      "seam": "原 C handle/B prepare-dispatch-bind/Git fixture 的可观察授权、字节与结果资格；外部 native transport downstream doubles",
      "commands": [
        "python3 scripts/run_tests.py --test-file tests/runtime/test_execution_plans.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_stage_scope_progress.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_stage_scope_transfer.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_scope_seal_control.py"
      ]
    }
  ],
  "steps": [
    {
      "id": "FP1-01",
      "target": {
        "path": "src/shared/scripts/execution_plan.py",
        "symbol": "read_ticket_plan",
        "operation": "add"
      },
      "capability": "读取一次已提交的 Ticket 计划",
      "approach": [
        "验证 Git regular file/mode 和精确路径",
        "提取唯一 execution-plan-v1 block",
        "解析 bounded JSON，拒绝重复字段和第二个 block"
      ],
      "inputs": [
        "repository、commit、Ticket path"
      ],
      "outputs": [
        "完整结构化计划及文档 SHA-256/mode"
      ],
      "failures": [
        "缺文件/非 regular/多块/无效 JSON → execution_plan_incomplete"
      ],
      "depends_on": [],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP1-02",
      "target": {
        "path": "src/shared/scripts/execution_plan.py",
        "symbol": "contract_projection",
        "operation": "add"
      },
      "capability": "分离不可修订功能合同",
      "approach": [
        "根据已封存章节与唯一计划块边界提取原字节",
        "包含 Ticket header/行为/验收/Blocked by 和 Spec/ADR 合同章节",
        "输出 canonical digest，保留原 blob 身份"
      ],
      "inputs": [
        "文档 bytes、精确章节 refs、file mode"
      ],
      "outputs": [
        "合同 projection 和 digest"
      ],
      "failures": [
        "边界缺失/重复/不明确 → contract_changed"
      ],
      "depends_on": [],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP1-03",
      "target": {
        "path": "src/shared/scripts/execution_plan.py",
        "symbol": "validate_ticket_plan",
        "operation": "add"
      },
      "capability": "验证可执行函数计划完整性",
      "approach": [
        "逐项校验 schema/IDs/version/target/能力/过程/契约",
        "核对已有 Python 符号与生产 caller 证据",
        "拟新增标 add",
        "核对每函数行为验证和 artifact closure"
      ],
      "inputs": [
        "plan、repository、manifest、contract refs"
      ],
      "outputs": [
        "已核对 Ticket plan"
      ],
      "failures": [
        "缺契约/符号证据/重复 IDs/越界 target → execution_plan_incomplete"
      ],
      "depends_on": [
        "FP1-01",
        "FP1-02"
      ],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP1-04",
      "target": {
        "path": "src/shared/scripts/execution_plan.py",
        "symbol": "collect_execution_plan",
        "operation": "add"
      },
      "capability": "从所有实施 Ticket 建立单一计划目录",
      "approach": [
        "读取 exact committed sources，检查 Spec/Ticket 对应、全部实施文件 coverage、Ticket/step 依赖联合 DAG",
        "按原文顺序稳定排序",
        "与 scope 同 seal"
      ],
      "inputs": [
        "repository、planning commit、plan source paths、scope、contract identity"
      ],
      "outputs": [
        "catalog、plan digest、contract digest 与稳定 ordered step IDs"
      ],
      "failures": [
        "未知前置/循环/manifest 缺覆盖 → execution_plan_incomplete"
      ],
      "depends_on": [
        "FP1-01",
        "FP1-02",
        "FP1-03"
      ],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP1-05",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.scope_propose",
        "operation": "modify"
      },
      "capability": "真实 C 提案入口拒绝不完整计划",
      "approach": [
        "保持旧 source/binding/manifest guards",
        "从 actual candidate commit 收集目录",
        "proposal 同时绑定 catalog/contract，不在提案时授写权限"
      ],
      "inputs": [
        "原 proposal 加计划 source refs"
      ],
      "outputs": [
        "durable proposal（manifest+plan）"
      ],
      "failures": [
        "任何源/目录漂移 → 原状态保留"
      ],
      "depends_on": [
        "FP1-04"
      ],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP1-06",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "Progress.scope_seal",
        "operation": "modify"
      },
      "capability": "范围与计划由原 Controller 一起封存",
      "approach": [
        "原 Controller receipt 和 stopped planning candidate 验真",
        "把 plan/contract digest 加 evidence 与 current authority",
        "同 proposal replay ACK"
      ],
      "inputs": [
        "原 seal + exact catalog identity"
      ],
      "outputs": [
        "一个新 revision 的 Scope Authority"
      ],
      "failures": [
        "另一 Controller/计划漂移 → 拒绝"
      ],
      "depends_on": [
        "FP1-05"
      ],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP1-07",
      "target": {
        "path": "src/shared/scripts/workflow_progress.py",
        "symbol": "read_scope_readiness",
        "operation": "modify"
      },
      "capability": "只暴露计划就绪的真实 handoff",
      "approach": [
        "从原 C record 重读 sealed catalog/文档 commit/hash",
        "核验 publication/stopped/acceptance 条件后输出 readiness"
      ],
      "inputs": [
        "原 readiness/checkpoint identity"
      ],
      "outputs": [
        "含同一 current execution_plan 的 readiness"
      ],
      "failures": [
        "缺 catalog/commit/contract identity → handoff_not_ready"
      ],
      "depends_on": [
        "FP1-06"
      ],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP1-08",
      "target": {
        "path": "src/shared/scripts/stage_handoff.py",
        "symbol": "effective_scope",
        "operation": "modify"
      },
      "capability": "B 验证 sealed 计划与原 Stage-2 launch",
      "approach": [
        "保留 original launch boundary，核对 current Scope Authority 的 manifest/catalog 与原 requirement/target/binding",
        "不让调用者自报 ready"
      ],
      "inputs": [
        "immutable handoff、current authority"
      ],
      "outputs": [
        "验证后的 scope 与计划投影"
      ],
      "failures": [
        "漂移/无原 checkpoint → scope_changed"
      ],
      "depends_on": [
        "FP1-07"
      ],
      "verification_refs": [
        "V1-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    }
  ],
  "artifact_steps": [
    {
      "id": "AP1-01",
      "path": "src/stages/solution-design/SKILL.md.in",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    },
    {
      "id": "AP1-02",
      "path": "src/stages/solution-design/references/design-readiness.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    },
    {
      "id": "AP1-03",
      "path": "src/stages/solution-design/references/subagent-protocol.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    },
    {
      "id": "AP1-04",
      "path": "src/stages/solution-design/references/templates.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    },
    {
      "id": "AP1-05",
      "path": "tests/runtime/test_execution_plans.py",
      "operation": "add",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    },
    {
      "id": "AP1-06",
      "path": "tests/runtime/test_stage_scope_progress.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    },
    {
      "id": "AP1-07",
      "path": "tests/runtime/test_stage_scope_transfer.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    },
    {
      "id": "AP1-08",
      "path": "tests/runtime/test_scope_seal_control.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V1-behavior"
      ]
    }
  ]
}
```

## Comments

Stage 2: 基于冻结需求和真实调用链设计；当前为待 combined review 的草稿，尚未 native publication 或实施。

Stage 2 publication: 用户已以“确认，连续模式”接受本 Ticket 的粒度、blocking edges 与测试 seam；ready-for-agent 和原 Blocked by 已发布至本地 tracker，实施尚未开始。授权引用：user-current-conversation:confirm-continuous:fep-solution-review-20261001-01。
