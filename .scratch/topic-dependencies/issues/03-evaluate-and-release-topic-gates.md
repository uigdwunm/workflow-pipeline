# 03 — Evaluate and atomically release topic gates

Status: `ready-for-agent`

## What to build

When a user returns to a gated Discussion Topic, show the exact current
Phase-0/1 evidence or waiting condition, then release every current closed
dependency together after confirmation so substantive work can begin safely.

## Blocked by

02 — Prepare gated topics from proactive split proposals.

## Spec trace

- Implementation Decisions: `evaluate-topic-gate`; `release-topic-gate`;
  accepted basis; phase and turn guards.
- Acceptance scenarios: 3, 4, 5, 7, 14.

## Acceptance criteria

- [ ] Read-only evaluation returns open, blocked, or releasable with sorted dependency details and current candidate checkpoint, Phase Result, or confirmed-decision authority without interpreting requirement prose.
- [ ] The caller can select the narrow exact authority and decision IDs for every closed dependency and receive one digest-bound release set suitable for user confirmation.
- [ ] Release revalidates actor ownership, topic and dependency revisions, the complete closed set, evidence state, identities, and canonical basis bytes before one all-or-stop mutation.
- [ ] Missing evidence reports the exact prerequisite and waiting condition; partial sets and stale evaluations open nothing.
- [ ] Multiple dependencies use AND semantics, exact successful replay is idempotent, and accepted bases retain stable authority IDs and decision digests.
- [ ] First-turn handoff acceptance remains allowed while closed; later-turn discussion authorization and substantive topic mutation stop while closed and proceed after release.
- [ ] Wrapper contract tests verify there is no polling, monitoring, notification, background wakeup, or ordinary-turn reevaluation after a gate is open.
