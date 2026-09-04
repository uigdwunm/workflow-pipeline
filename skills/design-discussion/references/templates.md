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
