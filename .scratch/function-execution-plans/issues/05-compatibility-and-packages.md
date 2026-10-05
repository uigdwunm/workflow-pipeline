# 05 — 新协议五包交付与真实宿主验收

Status: ready-for-agent
Publication: published — local Markdown tracker
Review: accepted fep-solution-review-20261001-01
Ticket ID: FEP-05
Plan Version: 1

**What to build:** 五包完整闭包含同execution-plan模块/新协议；维护源与所有精确generatedoutputs一致。 本切片完整覆盖其 C/B/Git/native 接线和行为验证，详细方案取自 Spec D5, D6, D7。

**Blocked by:** 01, 02, 03, 04

- [ ] AC-1: 五包完整闭包含同execution-plan模块/新协议；维护源与所有精确generatedoutputs一致。
- [ ] AC-2: runner10/control schema6/progress18/transfer14拒绝旧可执行记录，旧pins不迁移、不现场安装。
- [ ] AC-3: 所有受影响focused checks、两轴审查后全仓验证、真实宿主current计划/修订/无Dispatcher编码的环境验收有实际证据。
- [ ] AC-4: closure只拥有解释性文档和实施记录，不改运行契约/生成包。

## 实施与验证依据

Spec: `../PRD.md`；冻结需求 AC 与映射见 PRD N3。本 Ticket 只拥有实施切片和阻塞关系；不改变用户合同或给执行者修订权。多个函数可由一个 Execution Agent 完成并共用上述行为验证。共享实现文件按 Ticket 顺序串行。

协议结构的 mechanical fan-out 会在同一 integration branch 分批完成；各先行 Ticket 验证其闭合行为，整仓/五包绿色承诺在本 integrate-and-verify Ticket。没有发布中间包或升级旧记录。所有源→输出和新模块 import closure 以 PRD N4 精确清单为权限依据。

## 函数级执行计划

该 block 是本 Ticket 的正式可修订区域；行为与上方 AC 是独立合同。既有符号已从维护源定位，add 为此次拟新增；执行者不得把私有分解当公共合同调整。

```execution-plan-v1
{
  "schema_version": 1,
  "ticket_id": "FEP-05",
  "plan_version": 1,
  "spec_ref": {
    "path": ".scratch/function-execution-plans/PRD.md",
    "section": "Implementation Decisions"
  },
  "contract_refs": [
    "FEP-05:What to build",
    "FEP-05:AC-1",
    "FEP-05:AC-2",
    "FEP-05:AC-3",
    "FEP-05:AC-4",
    "PRD:D1:Execution plan schema and public interfaces",
    "PRD:D3:Controller-only revision transaction",
    "PRD:D4:Dispatcher roles and byte provenance",
    "PRD:D5:Public contracts and business impact boundary",
    "PRD:D6:Candidate review and verification contracts",
    "PRD:D7:Compatibility key and local metadata version"
  ],
  "blocked_by": [
    "FEP-01",
    "FEP-02",
    "FEP-03",
    "FEP-04"
  ],
  "verifications": [
    {
      "id": "V5-behavior",
      "behavior": "五包完整闭包含同execution-plan模块/新协议；维护源与所有精确generatedoutputs一致。; runner10/control schema6/progress18/transfer14拒绝旧可执行记录，旧pins不迁移、不现场安装。; 所有受影响focused checks、两轴审查后全仓验证、真实宿主current计划/修订/无Dispatcher编码的环境验收有实际证据。; closure只拥有解释性文档和实施记录，不改运行契约/生成包。",
      "seam": "原 C handle/B prepare-dispatch-bind/Git fixture 的可观察授权、字节与结果资格；外部 native transport downstream doubles",
      "commands": [
        "python3 scripts/run_tests.py --test-file tests/runtime/test_solution_design_contract.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_stage_scope_runner.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_stage_scope.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_repository_validation.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_requirement_delivery.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_discussion_protocol.py",
        "python3 scripts/run_tests.py --test-file tests/runtime/test_topic_dependency_checkpoint.py",
        "python3 scripts/run_tests.py --test-file tests/packages/test_build.py",
        "python3 scripts/run_tests.py --test-file tests/packages/test_cross_package.py",
        "python3 scripts/run_tests.py --test-file tests/packages/test_preflight.py"
      ]
    }
  ],
  "steps": [
    {
      "id": "FP5-01",
      "target": {
        "path": "src/stages/guided-implementation/scripts/workflow.py",
        "symbol": "validate_confirmed",
        "operation": "modify"
      },
      "capability": "新runner confirmed入口保持原Controller证据与计划投影",
      "approach": [
        "outer10固定新compat及Controller ingress refs",
        "不得把旧registry/record补新语义",
        "source/settings/Flow guards保留"
      ],
      "inputs": [
        "confirmed input/current registration"
      ],
      "outputs": [
        "validated new runner confirmed"
      ],
      "failures": [
        "legacy/mismatch → 原 runtime要求"
      ],
      "depends_on": [
        "FP4-09"
      ],
      "verification_refs": [
        "V5-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP5-02",
      "target": {
        "path": "src/stages/guided-implementation/scripts/workflow.py",
        "symbol": "_validate_record",
        "operation": "modify"
      },
      "capability": "恢复拒绝旧outer，不迁移待执行状态",
      "approach": [
        "new version10和progress18兼容",
        "currentauthority/plan lineage重读",
        "原host/C状态继续是唯一记录"
      ],
      "inputs": [
        "saved runner record"
      ],
      "outputs": [
        "validated original new run"
      ],
      "failures": [
        "outer9或不一致 → legacy_run_requires_original_runtime"
      ],
      "depends_on": [
        "FP5-01"
      ],
      "verification_refs": [
        "V5-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP5-03",
      "target": {
        "path": "scripts/validate_repository.py",
        "symbol": "validate_workflow_control_docs",
        "operation": "modify"
      },
      "capability": "发布包角色契约缺少函数计划/Controller修订时报告",
      "approach": [
        "增加必要角色/计划/修订marker检查并拒绝新包Dispatcher direct描述",
        "仍只查生成文档，不编辑文档"
      ],
      "inputs": [
        "repository generated package docs"
      ],
      "outputs": [
        "validation issues或valid"
      ],
      "failures": [
        "缺runtime契约/旧角色描述 → nonzero via validate"
      ],
      "depends_on": [
        "FP5-02"
      ],
      "verification_refs": [
        "V5-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    },
    {
      "id": "FP5-04",
      "target": {
        "path": "scripts/field_acceptance.py",
        "symbol": "main",
        "operation": "modify"
      },
      "capability": "给出新计划流程真实宿主验收worksheet",
      "approach": [
        "在原只读T13/T14 worksheet加入成组计划、内部revision、stale release、preservation/adoption、用户gate/standalone/foreground/attached证明字段",
        "默认not_run"
      ],
      "inputs": [
        "existing packages optional release root"
      ],
      "outputs": [
        "可重复worksheet与明确未执行状态"
      ],
      "failures": [
        "不可读身份 → 原错误；worksheet不是pass"
      ],
      "depends_on": [
        "FP5-02",
        "FP5-03"
      ],
      "verification_refs": [
        "V5-behavior"
      ],
      "read_only": [
        "docs/requirements/2026-10-01-function-execution-plans.md",
        ".scratch/function-execution-plans/PRD.md"
      ]
    }
  ],
  "artifact_steps": [
    {
      "id": "AP5-01",
      "path": "build/skill-packages.json",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-02",
      "path": "src/shared/references/package-execution.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-03",
      "path": "src/shared/references/guided-implementation/worktree-execution.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-04",
      "path": "src/stages/problem-framing/references/templates.md",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-05",
      "path": "src/stages/guided-implementation/scripts/workflow_stage_result.schema.json",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-06",
      "path": "tests/runtime/test_solution_design_contract.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-07",
      "path": "tests/runtime/test_stage_scope_runner.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-08",
      "path": "tests/runtime/test_stage_scope.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-09",
      "path": "tests/runtime/test_repository_validation.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-10",
      "path": "tests/runtime/test_requirement_delivery.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-11",
      "path": "tests/runtime/test_discussion_protocol.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-12",
      "path": "tests/runtime/test_topic_dependency_checkpoint.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-13",
      "path": "tests/packages/test_build.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-14",
      "path": "tests/packages/test_cross_package.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
      ]
    },
    {
      "id": "AP5-15",
      "path": "tests/packages/test_preflight.py",
      "operation": "modify",
      "capability": "实现对应行为的 Prompt/结构/测试或发布资源",
      "approach": "根据本 Ticket 的 Spec 决定更新精确源；测试验证上列行为，不增加独立实现权威；生成输出来自 N4。",
      "verification_refs": [
        "V5-behavior"
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

生产候选 6d5bbb2 的五包生成一致性及 81 个闭包校验通过；完整验证 57 files / 1,166 tests，fail0/error0/skip1，自然退出码 0。实际包为 1.3.0 / progress-v19 / transfer-v14 / runner10 / control key11 schema6；原 AC-2 的 progress18 规划声明保留为历史，不修改原计划或版本正文。相同包字节的 Ticket 主线完成真实宿主验收；standalone-no-A、foreground、attached ingress 用户延期、未验收。此次只追加解释性文档，不改运行契约、生成包、旧 records 或安装。

完整事实、限制及证据路径见 [PRD 交付记录](../PRD.md#交付记录2026-10-05)。本次由用户最新授权作普通本地交付，不补造旧 production C 的 Stage-4 验收或迁移，不创建新的 C；原记录与失败试验保留。
