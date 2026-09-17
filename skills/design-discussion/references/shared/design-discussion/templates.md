# Stable Templates

Read this reference when presenting the first persistent question or checking
the minimum topic layout.

## First verified turn

```text
0讨论：已初始化并回读验证
话题：<human-readable topic>
推荐：<one recommendation>
理由：<why this is the best current default>
问题：<one substantive question>
```

Do not show this block unless bootstrap returned `ok: true`, `state: started`
and `reread_verified: true`.

## Proactive split proposal

```text
新话题建议

当前话题：<current topic>
建议拆出：<new topic goal>
判断依据：<why this is an independent discussion target>

话题归属：<current discussion tree | new root topic>
结构来源：<parent topic | none>
新话题入口：0讨论

依赖关系：
- <no dependency; both topics may continue>
or
- <dependent topic> depends on <prerequisite topic>
- 所需结果：<specific Phase-0/1 result>
- 初始门禁：关闭
- 放行触发：用户在依赖方话题中要求继续时检查

当前话题处理：<continue | wait for the new topic result>
结果回流：<absorb | impact review | no return required>

确认事项：接受以上拆分方向，并准备新话题身份、初始依赖和 handoff
确认后结果：更新讨论状态并展示独立的 Codex 任务创建确认；本次尚不创建任务
修改方式：说明需要调整的目标、归属、依赖或当前话题处理方式
确认方式：回复 `确认`
```

Use this complete block only after all four split criteria are true. A dedicated
carrier returns a bounded proposal to its source task; it does not present an
authoritative preparation or creation confirmation.

## Ordinary consultation

Answer normally. Do not mention initialization, create files, call the
protocol, or imply that the consultation will persist.

## Minimum topic document

```markdown
## Goal
## Background and Current State
## Scope
## Non-goals
## Users and Key Scenarios
## Confirmed Decisions
## Candidate Solution
## Tentative Assumptions
## Facts
## Constraints
## Acceptance Conditions
## Pending Questions
## Decision Evolution
```

The headings are stable; content changes only through the authorized protocol
operations. `Decision Evolution` stays empty unless one concise change note is
needed to prevent a likely misunderstanding; it is not an audit log.

The created child is one independent Workflow Controller, with fresh preferences and no inherited continuous scope. A closed gate blocks substantive work. For 0/1 dedicated work disclose the prepared plan: stage, controller_ref, project, target, title, document path/hash/version, missing_context, configuration reason/receipt, task_count=1, next_step, archive_ref and plan_id. Confirm once; stage-current/topic-current keeps the document.
