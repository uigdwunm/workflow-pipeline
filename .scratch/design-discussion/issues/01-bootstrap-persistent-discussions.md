# Bootstrap persistent 0讨论 topics

Status: `ready-for-agent`

## What to build

Deliver the first usable `0讨论` path: an explicit invocation initializes a durable root topic, project identity, authoritative ledger, active conversation binding and minimal topic document before asking one substantive question. Ordinary design consultation remains stateless.

## Acceptance criteria

- [ ] `$design-discussion`, `0讨论` and explicit sustained-design intent initialize the configured persistent discussion workspace before the first substantive question.
- [ ] Initialization is all-or-stop: project identity, root topic, ledger, active binding and minimum topic document are reread successfully before the Skill claims that 0讨论 has started.
- [ ] Ordinary consultation and incidental wording do not create discussion files, ledger state or bindings.
- [ ] The Skill presents one question at a time, gives a recommendation and reason first, and never records an unconfirmed preference as a decision.
- [ ] The new Skill uses the approved progressive-loading structure, interface name `0讨论`, declared dependencies and repository validation rules.
- [ ] CLI-level tests cover Git and non-Git initialization, duplicate invocation, partial failure, invalid paths and zero-side-effect stateless consultation.

Blocked by: None — can start immediately.

## Comments
