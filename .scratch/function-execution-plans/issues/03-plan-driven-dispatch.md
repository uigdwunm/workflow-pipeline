# 03 — 按有效计划成组派发与 Dispatcher 不编码

Status: ready-for-agent
Publication: published — local Markdown tracker
Review: accepted fep-solution-review-20261001-01
Ticket ID: FEP-03
Plan Version: 1

**What to build:** execution allocation/release/result 明确绑定当前计划、版本与 authority；旧 refs/release 不能继续写入或验收。 本切片完整覆盖其 C/B/Git/native 接线和行为验证，详细方案取自 Spec D1, D4, D5。

**Blocked by:** 01, 02

- [ ] AC-1: execution allocation/release/result 明确绑定当前计划、版本与 authority；旧 refs/release 不能继续写入或验收。
- [ ] AC-2: 函数组依赖有序，允许共用行为验证；同文件写入仍串行。
- [ ] AC-3: 新 staged/standalone/foreground 只接受 full，Dispatcher 自写或 direct candidate 不成为交付。
- [ ] AC-4: standalone 保留无 Ticket 的 inline plan；foreground 由原 Controller 提供修订决定/字节。

## 实施与验证依据

Spec: `../PRD.md`；冻结需求 AC 与映射见 PRD N3。本 Ticket 只拥有实施切片和阻塞关系；不改变用户合同或给执行者修订权。多个函数可由一个 Execution Agent 完成并共用上述行为验证。共享实现文件按 Ticket 顺序串行。

## 函数级执行计划

该 block 是本 Ticket 的正式可修订区域；行为与上方 AC 是独立合同。既有符号已从维护源定位，add 为此次拟新增；执行者不得把私有分解当公共合同调整。

```execution-plan-v1
{
  "schema_version": 1,
  "ticket_id": "FEP-03",
  "plan_version": 1,
  "spec_ref": {
    "path": ".scratch/function-execution-plans/PRD.md",
    "section": "Implementation Decisions"
  },
  "contract_refs": [
    "FEP-03:What to build",
    "FEP-03:AC-1",
    "FEP-03:AC-2",
    "FEP-03:AC-3",
    "FEP-03:AC-4",
    "PRD:D1:Execution plan schema and public interfaces",
    "PRD:D3:Controller-only revision transaction",
    "PRD:D4:Dispatcher roles and byte provenance",
    "PRD:D5:Public contracts and business impact boundary",
    "PRD:D6:Candidate review and verification contracts",
    "PRD:D7:Compatibility key and local metadata version"
  ],
  "blocked_by": [
    "FEP-01",
    "FEP-02"
  ],
  "verifications": [
    {
      "id": "V3-behavior",
      "behavior": "execution allocation/release/result 明确绑定当前计划、版本与 authority；旧 refs/release 不能继续写入或验收。; 函数组依赖有序，允许共用行为验证；同文件写入仍串行。; 新 staged/standalone/foreground 只接受 full，Dispatcher 自写或 direct candidate 不成为交付。; standalone 保留无 Ticket 的 inline plan；foreground 由原 Controller 提供修订决定/字节。",
      "seam": "原 C handle/B prepare-dispatch-bind/Git fixture 的可观察授权、字节与结果资格；外部 native transport downstream doubles",
      "commands": [
        "python3 scripts/run_tests.py --test-file tests/runtime/test_plan_driven_dispatch.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_stage_transfer.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_workflow_progress.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_workflow.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_stage_start_verification.py"
      ]
    }
  ],
  "steps": [
    {
      "id": "FP3-01",
      "target": {
        "path": "src/shared/scripts/execution_plan.py",
        "symbol": "validate_plan_refs",
        "operation": "add"
      },
      "capability": "在真实分配之前检查函数组与前置资格",
      "approach": [
        "解析 current refs exact IDs/version/source/step digest",
        "完成依赖或组内 topo",
        "核对 assigned files/readonly/verification cover并拒绝 old authority"
      ],
      "inputs": [
        "current authority、plan_refs、paths、effective completed results"
      ],
      "outputs": [
        "稳定函数组顺序与绑定摘要"
      ],
      "failures": [
        "unknown/stale/dependency/coverage → execution_plan_stale"
      ],
      "depends_on": [
        "FP2-02"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-02",
      "target": {
        "path": "src/shared/scripts/stage_handoff.py",
        "symbol": "_prepare",
        "operation": "modify"
      },
      "capability": "Stage3/Execution Agent 接收有效函数计划",
      "approach": [
        "扩展 semantic execution_plan/plan_refs",
        "staged 重读 C current authority，standalone 只验 sealed inline brief",
        "new direct fail closed",
        "保留 frozen source和配置"
      ],
      "inputs": [
        "原 B request/current readiness/inline catalog"
      ],
      "outputs": [
        "sealed stage input 含 current refs/digest"
      ],
      "failures": [
        "旧计划/无计划/direct → 不 dispatch"
      ],
      "depends_on": [
        "FP3-01",
        "FP3-06"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-03",
      "target": {
        "path": "src/shared/scripts/stage_handoff.py",
        "symbol": "_render_verified",
        "operation": "modify"
      },
      "capability": "原生 payload 给出完整可执行计划和角色职责",
      "approach": [
        "输出 referenced plan block/当前 scope/revision/contract digest",
        "Dispatcher 不编码、不改计划",
        "Agent 只读直到 exact release"
      ],
      "inputs": [
        "verified saved B input"
      ],
      "outputs": [
        "bounded native payload"
      ],
      "failures": [
        "不完整 source/ref → 拒绝而非重建"
      ],
      "depends_on": [
        "FP3-02"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-04",
      "target": {
        "path": "src/shared/scripts/stage_dispatch.py",
        "symbol": "prepared",
        "operation": "modify"
      },
      "capability": "现有 plan-execution 真正绑定计划组",
      "approach": [
        "start-dispatch 冻结 current plan authority",
        "execution-agent 将 B refs 放入 control evidence",
        "prepared allocation 只 reserve，不授写权限"
      ],
      "inputs": [
        "sealed B input/current control"
      ],
      "outputs": [
        "allocation_digest、native launch/read-only payload"
      ],
      "failures": [
        "old authority/no plan/direct → 拒绝"
      ],
      "depends_on": [
        "FP3-02",
        "FP3-03",
        "FP3-07",
        "FP3-08"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-05",
      "target": {
        "path": "src/shared/scripts/stage_dispatch.py",
        "symbol": "execution_release_token",
        "operation": "modify"
      },
      "capability": "防止 Agent 在旧计划 release 下继续编辑",
      "approach": [
        "在 native ready receipt/ref/attempt 之外绑定 authority revision和plan refs digest",
        "bind 再验 current authority"
      ],
      "inputs": [
        "bound dispatch record/current control"
      ],
      "outputs": [
        "唯一 write_release digest"
      ],
      "failures": [
        "修订后 old token → 不 release"
      ],
      "depends_on": [
        "FP3-04"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-06",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "implementation_policy",
        "operation": "modify"
      },
      "capability": "新运行统一禁止 Dispatcher direct 编码",
      "approach": [
        "只接受 omitted/full",
        "明确 direct 返回新协议不支持并提示原历史 runtime，删除新路径的 escalation 权限"
      ],
      "inputs": [
        "semantic implementation policy"
      ],
      "outputs": [
        "full policy"
      ],
      "failures": [
        "explicit direct/malformed → invalid_policy"
      ],
      "depends_on": [
        "FP2-07"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-07",
      "target": {
        "path": "src/shared/scripts/workflow_control.py",
        "symbol": "transition",
        "operation": "modify"
      },
      "capability": "让分配、bind、result、accept 尊重当前计划",
      "approach": [
        "plan-execution 校验 refs/dag/group/files",
        "assign/release/read result核验 revision",
        "有效变更路径均来自 Execution Agent",
        "旧未决分配不偷换 refs"
      ],
      "inputs": [
        "原 actions + plan_refs/authority/release"
      ],
      "outputs": [
        "exact bound roster 或拒绝"
      ],
      "failures": [
        "stale/overlap/Dispatcher self-source → 拒绝"
      ],
      "depends_on": [
        "FP3-01",
        "FP3-06"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-08",
      "target": {
        "path": "src/shared/scripts/workflow_control_git.py",
        "symbol": "verify_execution_start",
        "operation": "modify"
      },
      "capability": "旧权威或无归属字节不能进入新分配",
      "approach": [
        "保留全 unallocated paths 指纹",
        "planning-controller commit只在已 activate 的 ancestry 允许，implementation baseline 不变",
        "current refs/accepted qualification 驱动 bytes owner"
      ],
      "inputs": [
        "repository/current progress"
      ],
      "outputs": [
        "actual clean allocation snapshot"
      ],
      "failures": [
        "unexplained/old assignment bytes → error"
      ],
      "depends_on": [
        "FP3-07"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-09",
      "target": {
        "path": "src/stages/guided-implementation/scripts/workflow.py",
        "symbol": "_stage_prompt",
        "operation": "modify"
      },
      "capability": "前台载体只运输原 Controller 修订输入",
      "approach": [
        "显示 current plan authority 和 exact revision transaction/Controller ingress",
        "载体与 Dispatcher 不规划写入",
        "旧 confirmed input不改",
        "unsupported ingress technical_error"
      ],
      "inputs": [
        "原 runner/C state、Controller decision/patch refs"
      ],
      "outputs": [
        "current bounded carrier prompt"
      ],
      "failures": [
        "无真实 Controller ingress → 不代理"
      ],
      "depends_on": [
        "FP3-05"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP3-10",
      "target": {
        "path": "src/stages/guided-implementation/scripts/workflow.py",
        "symbol": "_stage_projection",
        "operation": "modify"
      },
      "capability": "runner 读取当前权威而非旧 Stage2 ceiling",
      "approach": [
        "从 C projection 更新当前 scope/plan refs，保留 frozen prior stage_results 和 source identities"
      ],
      "inputs": [
        "原 saved outer/C state"
      ],
      "outputs": [
        "有效 current stage projection"
      ],
      "failures": [
        "inconsistent inner/outer → scope_changed"
      ],
      "depends_on": [
        "FP3-09"
      ],
      "verification_refs": [
        "V3-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    }
  ],
  "artifact_steps": [
    {
      "id": "AP3-01",
      "path": "src/stages/guided-implementation/SKILL.md.in",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-02",
      "path": "src/shared/references/guided-implementation/execution-protocol.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-03",
      "path": "src/shared/references/guided-implementation/originating-task-protocol.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-04",
      "path": "src/shared/references/stage-transfer.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-05",
      "path": "src/shared/references/entry-preparation.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-06",
      "path": "tests/runtime/test_plan_driven_dispatch.py",
      "operation": "add",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-07",
      "path": "tests/runtime/test_stage_transfer.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-08",
      "path": "tests/runtime/test_workflow_progress.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-09",
      "path": "tests/runtime/test_workflow.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    },
    {
      "id": "AP3-10",
      "path": "tests/runtime/test_stage_start_verification.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V3-behavior"
      ]
    }
  ]
}
```

## Comments

Stage 2: 基于冻结需求和真实调用链设计；当前为待 combined review 的草稿，尚未 native publication 或实施。

Stage 2 publication: 用户已以“确认，连续模式”接受本 Ticket 的粒度、blocking edges 与测试 seam；ready-for-agent 和原 Blocked by 已发布至本地 tracker，实施尚未开始。授权引用：user-current-conversation:confirm-continuous:fep-solution-review-20261001-01。
