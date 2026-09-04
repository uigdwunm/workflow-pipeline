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
判断依据：<independent goal, outcome, interruption, removability>

话题归属：<current discussion tree | new root topic>
结构来源：<parent topic | none>
新话题入口：0讨论

依赖关系：<none | dependent depends on prerequisite; exact required result; initial gate closed>
当前话题处理：<continue | wait>
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
