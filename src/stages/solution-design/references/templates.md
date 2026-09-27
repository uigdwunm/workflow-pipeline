# Fixed Formats

Use these formats verbatim and replace every placeholder, except for the
conditional continuous launch disclosure below. Read only the formats needed
by the current role and seam. Interpret user replies
through
[`{{resource:design-discussion/references/confirmation-contract.md}}`]({{resource:design-discussion/references/confirmation-contract.md}}).

For foreground runs, these business messages retain C's original pending ID,
native ref and checkpoint. The runner keeps its host while waiting; queued is
only a saved answer, and the original carrier consumes it. Apply the shared
progression lifecycle gate before any native followup or host release.

## Stepwise automatic launch disclosure

An explicit Stage-2 request or inherited stage authorization covers this launch.
Explain the configuration and proceed. A continuous request selects the remaining
standard route without another approval, including when that route is first
explained now.

```text
逐阶段模式自动动作：启动 2方案子 agent
本次目标：<goal>
需求来源：<immutable draft path, commit and hash>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<repository identity>
规划载体：<exact target>
业务角色：solution_designer
派发方式：<current native-subagent orchestration adapter>
目标模型：<selected supported model>
模型来源：<selection source: user | confirmed | role | inherited; current adapter receipt>
推理强度：<selected supported reasoning effort>
强度来源：<selection source: user | confirmed | role | inherited; current adapter receipt>
上下文方式：隔离上下文；不继承历史；仅读取需求来源和明确交接材料
执行环境：新建 Flow Worktree
Worktree：已创建；绑定=<exact binding including canonical path and branch>
允许动作：执行标准 $to-spec、ADR、$ask-matt、$to-tickets、原生发布；只写入并提交本阶段拥有的本地规划文档
禁止动作：修改实现代码、创建 PR、部署、发布版本、进入 3实现或处理无关任务
异常规则：执行失败、结果或副作用不确定、意外情况、与原计划不符或需要调整计划时停止并报告
流程模式：逐阶段确认
执行状态：本条披露后立即启动；不等待启动确认
```

## Continuous automatic launch disclosure

Show the current adapter's required disclosure unchanged, followed by a compact
stage supplement. Together they are the final user-visible commentary before
child launch. Omit a supplement field only if the adapter disclosure already
states the same complete value; do not repeat facts or add another introduction.
If disclosures disagree, resolve the mismatch before launch, never hide it by
omitting a field. This rendering rule does not shorten the child payload.

The combined disclosure must cover the goal, semantic role, requirement path,
commit and hash, project/repository, exact planning target, full Worktree binding,
model and effort with their source/receipt, isolated context, dispatch method,
permissions, completion conditions, anomaly rule and authorized flow mode.
Add any of these missing facts to the supplement, grouping related facts on one
line. If there is no adapter disclosure, render all of them in the supplement.
Model and effort may share one configuration line; neither may disappear.

Use this supplement skeleton, removing already-covered facts and filling any
missing facts from the coverage list above. A field is not covered merely because
it appeared in an earlier turn or in the machine payload.

```text
连续模式自动动作：启动 2方案子 agent
需求来源：<immutable draft path, commit and hash>
目标：<goal>; 角色=solution_designer; 项目=<project identity>; 仓库=<repository identity>
规划载体：<exact target>
Worktree：已创建；绑定=<exact binding including canonical path and branch>
配置补充：<only missing model/effort, source/receipt, isolated context or dispatch facts>
权限补充：<only missing allowed/forbidden actions, completion conditions and anomaly rule>
流程模式：连续执行后续全部流程
执行状态：本条披露后立即启动；不等待阶段确认
```

## Canonical child bootstrap payload

```text
$solution-design

协议：solution-design-subagent-v2
业务角色：solution_designer
角色说明：你是本次 2方案 的唯一阶段负责人；主 agent只负责用户决策和阶段编排。
本次目标：<goal>
需求来源类型：<immutable-problem-framing-draft | immutable-conversation-snapshot>
需求草案：<absolute path>
草案提交：<commit>
来源提交：<source_commit>
交付提交：<delivery_commit>
目标交付证明：<original verified proof or null>
草案 SHA-256：<hash>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<repository identity>
规划载体：<exact target>
任务设置：model=<exact model>; reasoning_effort=<exact effort>; source=<selection source and current adapter receipt>; context=isolated
Flow Worktree：<exact binding returned by start-worktree>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
工作区要求：在首次写入前以实际工作目录调用 `verify-worktree`；所有本地规划写入和提交只在该 Flow Worktree 内完成。
交付复查：创建 Flow 后核验原目标 proof 与 Flow 中冻结需求的字节和模式；不足时回交付责任方，不自行补交付。
允许动作：完整执行 $to-spec、必要 ADR、$ask-matt、$to-tickets、精确目标原生发布；只写入并提交本阶段拥有的本地规划文档。
禁止动作：修改实现代码、创建 PR、部署、发布版本、改变规划目标、进入 3实现、修改冻结需求文档或处理无关任务。
文档权威：冻结需求文档拥有需求；CONTEXT.md 拥有术语；ADR 拥有难逆决策；Spec 拥有实施方案与测试决策；Tickets 拥有实施切片和阻塞关系。不要创建单独的 2方案草案。
逐阶段模式：先完成 Spec、必要 ADR 和 Tickets 草稿及就绪检查；发布与终态前返回一次合并 SOLUTION_REVIEW_REQUIRED，询问接受完整方案、发布并进入实现；按匹配 review ID 的 PARENT_DECISION 修订或发布，主 Agent 沿用该授权进入实现。
连续模式：此前的 执行后续全部流程 是标准方案选择和原生审阅问题的预授权；不发出 review 消息；仍完成方案就绪检查、原生发布和规划提交，通过后直接交付。
范围扩展：认为需求边界外的改动是形成完整方案的必要条件时，按 $solution-design 的 visible-scope 规则返回 SOLUTION_DESIGN_ANOMALY，并等待用户明确决定。
异常：执行失败、结果不确定、意外情况、与需求或已发布计划不符、或者需要调整计划时，返回 SOLUTION_DESIGN_ANOMALY 并停止；不要擅自调整。
启动：第一条状态必须使用 SOLUTION_DESIGN_STARTED。
完成：只在规划提交已完成且 Flow Worktree 干净后返回一次 SOLUTION_DESIGN_COMPLETE，并包含完整本地规划路径清单；不要自行进入 3实现。
协议文件：完整遵循 $solution-design 的 {{resource:solution-design/references/subagent-protocol.md}} 和 {{resource:solution-design/references/templates.md}}。
```

Pass this payload unchanged to the current native-subagent orchestration
adapter. The adapter may wrap it in transport metadata or choose a different
native task name; neither changes the semantic role or stage authority.

## Started status

```text
SOLUTION_DESIGN_STARTED
协议：solution-design-subagent-v2
本次目标：<goal>
需求来源：<source>
规划载体：<exact target>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
```

## Combined solution review

```text
SOLUTION_REVIEW_REQUIRED
协议：solution-design-subagent-v2
审阅 ID：<unique current review id>
需求来源：<source>
Spec 草稿：<path>
ADR：<paths or none>
Tickets 草稿：<paths, implementation order and dependencies | none with reason>
方案摘要：<summary and key decisions>
测试依据：<Spec testing decisions>
方案就绪检查：<Spec readiness and Ticket traceability evidence>
实现范围：<included scope>
既有行为变化：<behavior, compatibility, data and API effects | none>
必要关联改动：<required collateral changes | none>
明确不纳入：<out-of-scope improvements | none>
规划载体：<exact target and publication authority>
已完成：完整规划草稿与就绪检查；尚未发布或结束阶段
恢复位置：<live review checkpoint>
确认事项：接受完整方案（含必要 Tickets）、发布规划并进入 3实现
修改方式：直接说明方案或 Tickets 的修改；同一子 Agent 更新后重新展示完整方案
确认方式：明确同意上述事项；也可明确只发布方案，或连续执行剩余标准流程
```

## Parent decision

```text
PARENT_DECISION
协议：solution-design-subagent-v2
目标子 agent：<trusted agent id>
关联类型：<solution-review | anomaly>
关联 ID：<review or anomaly id>
用户决定：<normalized confirmation intent or requested change>
恢复位置：<checkpoint>
```

## Anomaly

The child sends the complete internal anomaly record to the primary. The primary
shows the confirmation fields to the user only when a material user choice or
new authority is required. For technical recovery under existing authority,
report the failure/recovery status, omit the confirmation and modification
fields from user-facing output, and supply the Controller decision internally.
A missing technical proof blocks dependent work; a ritual user confirmation
cannot replace that proof.

```text
SOLUTION_DESIGN_ANOMALY
协议：solution-design-subagent-v2
异常 ID：<unique id>
异常类型：<执行失败 | 结果不确定 | 意外情况 | 与原计划不符 | 需要调整计划>
当前阶段：2方案
原计划：<baseline>
实际情况：<observed state>
偏差：<exact mismatch>
影响：<scope, behavior, delivery, risk, or side-effect impact>
建议：<recommended response>
其他选项：<reasonable alternatives>
已完成：<work that will not repeat>
尚未执行：<stopped actions>
现有文件与发布：<paths, URLs, IDs and states>
恢复位置：<checkpoint>
确认事项：按上述建议处理异常并继续
修改方式：说明要选择的其他处理方式
确认方式：明确同意上述单一待执行事项；如需调整可直接说明
```

## Completion

```text
SOLUTION_DESIGN_COMPLETE
协议：solution-design-subagent-v2
需求来源：<draft path, commit and hash>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<repository identity>
规划载体：<exact target>
Spec：<path or URL>
ADR：<paths or none>
Tickets：<paths or URLs | none>
规划提交：<commit>
Flow Worktree：<exact binding>
本地规划路径：
- <repository-relative path>
发布结果：<completed results>
实现依据：<Spec and Tickets | complete Spec>
测试依据：<confirmed Testing Decisions or exact Spec section>
方案就绪检查：<通过；evidence summary>
变更契约预检：<Spec preflight/call-chain section and evidence>
模块与 Interface：<owning modules and boundaries | evidence-based not applicable>
决策覆盖：<precedence and material branches | evidence-based not applicable>
状态覆盖：<states, transitions and recovery | evidence-based not applicable>
测试 seam：<acceptance conditions mapped to observable seams>
Tickets 追踪：<Spec and acceptance mapping | not applicable after native $ask-matt decision>
实现范围：<included scope>
既有行为变化：<confirmed additions, removals, replacements and effects | none>
必要关联改动：<confirmed required collateral changes | none>
明确不纳入：<deferred optional improvements | none>
Matt 原生发布：Spec=<result>; Tickets=<result or none>; 原生标签与阻塞关系=<results or not applicable>
阶段授权：2方案标准原生动作已完成；保留输入流程模式及其授权引用，由主 Agent 按该模式衔接后续阶段
扩展远程操作：<未授权且未执行 | exact separately authorized actions and results>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
工作区状态：Flow Worktree clean; stage-owned planning paths committed
```

## Stepwise success footer

Use after publication and mechanical intake. Reuse the saved combined-review
acceptance; this footer adds no approval. For an explicit design-only request,
render the actual stopped boundary instead of Stage-3 entry below.

```text
阶段结果：方案完成
Spec：<clickable path or URL>
ADR：<clickable paths or none>
Tickets：<clickable paths or URLs | 不需要，完整 Spec 可在一个实现上下文中完成>
目标仓库：<verified absolute path>
规划提交：<commit>
方案合并提交：<planning merge commit>
Flow Worktree：<exact retained binding at planning merge commit>
本地规划路径：
- <repository-relative path>
发布结果：<results>
实现依据：<Spec and Tickets | complete Spec>
测试依据：<confirmed Testing Decisions or exact Spec section>
方案就绪检查：<通过；evidence summary>
变更契约预检：<Spec preflight/call-chain section and evidence>
模块与 Interface：<owning modules and boundaries | evidence-based not applicable>
决策覆盖：<precedence and material branches | evidence-based not applicable>
状态覆盖：<states, transitions and recovery | evidence-based not applicable>
测试 seam：<acceptance conditions mapped to observable seams>
Tickets 追踪：<Spec and acceptance mapping | not applicable after native $ask-matt decision>
实现范围：<included scope>
既有行为变化：<confirmed additions, removals, replacements and effects | none>
必要关联改动：<confirmed required collateral changes | none>
明确不纳入：<deferred optional improvements | none>
规划载体：<exact local convention, GitHub owner/repository, or tracker target>
Matt 原生发布：Spec=<verified path or URL>; Tickets=<verified paths or URLs, or none after native decision>; 原生标签与阻塞关系=<results or not applicable>
阶段授权：沿用完整方案审阅时的发布及 3实现授权；4归档仍按逐阶段模式处理
扩展远程操作：<未授权且未执行 | exact separately authorized actions and results>
工作区状态：Flow Worktree 干净并停留在方案合并提交；主检出区原有改动保持不变
流程模式：逐阶段确认
下一阶段：`$guided-implementation`（3实现）
进入条件：已满足
授权依据：<saved combined review ID and original user decision reference>
执行状态：已消费原有进入实现授权；本条交接后进入 3实现，不再确认
```

## Continuous completion handoff

Emit this complete block and invoke `$guided-implementation` in the same turn.
Render mechanical fields from the accepted Stage Transfer result. Preserve the
trusted child's semantic evidence, and verify native identity, Git publication,
protected source and worktree facts through the adapter before consumption.

```text
阶段结果：方案完成
需求来源：<draft path, commit and hash>
Spec：<clickable path or URL>
ADR：<clickable paths or none>
Tickets：<clickable paths or URLs | 不需要，完整 Spec 可在一个实现上下文中完成>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<absolute repository path>
规划提交：<commit>
方案合并提交：<planning merge commit>
Flow Worktree：<exact retained binding at planning merge commit>
本地规划路径：
- <repository-relative path>
发布结果：<child-reported results>
实现依据：<Spec and Tickets | complete Spec>
测试依据：<child-reported Testing Decisions or exact Spec section>
方案就绪检查：<通过；child-reported evidence summary>
变更契约预检：<child-reported Spec preflight/call-chain section and evidence>
模块与 Interface：<child-reported owning modules and boundaries | evidence-based not applicable>
决策覆盖：<child-reported precedence and material branches | evidence-based not applicable>
状态覆盖：<child-reported states, transitions and recovery | evidence-based not applicable>
测试 seam：<child-reported acceptance conditions mapped to observable seams>
Tickets 追踪：<child-reported Spec and acceptance mapping | not applicable after native $ask-matt decision>
实现范围：<child-reported included scope>
既有行为变化：<child-reported confirmed additions, removals, replacements and effects | none>
必要关联改动：<child-reported confirmed required collateral changes | none>
明确不纳入：<child-reported deferred optional improvements | none>
规划载体：<exact local convention, GitHub owner/repository, or tracker target>
Matt 原生发布：Spec=<child-reported path or URL>; Tickets=<child-reported paths or URLs, or none>; 原生标签与阻塞关系=<child-reported results or not applicable>
阶段授权：沿用当前目标的连续执行授权，覆盖后续 3实现、4归档及标准本地集成与清理
扩展远程操作：<未授权且未执行 | exact separately authorized actions and child-reported results>
工作区状态：Flow Worktree 干净并停留在方案合并提交；主检出区原有改动保持不变
完成依据：可信 solution_designer 的结构完整终态消息及 Stage Transfer 已核验的身份、Git、路径与工作区事实
流程模式：连续执行后续全部流程
下一阶段：`$guided-implementation`（3实现）
进入条件：已满足
执行状态：本条交接后同一轮立即进入 3实现；不等待阶段确认
```

## Pre-launch anomaly

First classify the failure through the protocol's **Pre-launch recovery**.
Use this decision block only when the user must choose changed facts or grant
missing authority. For unresolved technical evidence or adapter restrictions,
report the original error, retained state and required recovery evidence without
asking for a ritual retry phrase. A permitted technical retry needs only a short
progress update before the normal launch disclosure.

A direct conversation entry prepares its snapshot before this gate. Missing
conversation decisions use targeted questions; snapshot write/commit failures
name their local recovery step. Invalid existing handoffs require source-owner
repair and never silently switch to conversation input.

```text
流程异常：需要用户决策
异常类型：2方案子 agent启动前异常
当前阶段：2方案
本次目标：<goal>
需求来源：<source>
目标项目：<project>
目标仓库：<repository>
规划载体：<target>
待解析设置：model=<value or unavailable>; reasoning_effort=<value or unavailable>
实际情况：<missing, ambiguous, unavailable, changed, unsupported, or workflow_runtime_version_mismatch fact>
影响：无法生成完整启动披露或精确启动 solution_designer
建议：<recommended correction or recovery>
确认事项：按上述建议修正启动信息
修改方式：说明要采用的其他值或处理方式
确认方式：明确同意上述单一待执行事项；如需调整可直接说明
```

## Child termination anomaly

```text
流程异常：需要用户决策
异常类型：子 agent异常终止
当前阶段：2方案
可信子 agent：<agent id>
已完成：<known results>
未完成：<remaining work>
现有文件与发布：<paths, URLs, IDs and states | none>
建议：优先恢复同一个子 agent；无法恢复时再确认是否创建替代子 agent
恢复位置：<checkpoint>
确认事项：按上述建议恢复
确认方式：明确同意上述单一待执行事项；如需调整可直接说明
```

Configuration disclosure includes the select-configuration reason and current
adapter receipt. Unsupported user settings do not silently fall back. Unknown
capability/cost remains null; do not invent scores or prices to populate a template.
