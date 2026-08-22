# Bootstrap persistent 0讨论 topics

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Deliver the first usable `0讨论` path: an explicit invocation initializes a durable root topic, project identity, authoritative ledger, active conversation binding and minimal topic document before asking one substantive question. Ordinary design consultation remains stateless.

## Acceptance criteria

- [x] `$design-discussion`, `0讨论` and explicit sustained-design intent initialize the configured persistent discussion workspace before the first substantive question.
- [x] Initialization is all-or-stop: project identity, root topic, ledger, active binding and minimum topic document are reread successfully before the Skill claims that 0讨论 has started.
- [x] Ordinary consultation and incidental wording do not create discussion files, ledger state or bindings.
- [x] The Skill presents one question at a time, gives a recommendation and reason first, and never records an unconfirmed preference as a decision.
- [x] The new Skill uses the approved progressive-loading structure, interface name `0讨论`, declared dependencies and repository validation rules.
- [x] CLI-level tests cover Git and non-Git initialization, duplicate invocation, partial failure, invalid paths and zero-side-effect stateless consultation.

Blocked by: None — can start immediately.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
