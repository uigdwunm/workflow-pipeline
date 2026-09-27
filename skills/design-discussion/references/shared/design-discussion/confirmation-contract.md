# Contextual Confirmation Contract

Use this contract whenever a workflow action requires user confirmation.
Confirmation protects the identity, scope, authority, and effects of an action;
it is not a password or an exact-string challenge.

## Bind one pending action

Present one clearly named pending action together with its target, material
scope, relevant evidence, expected result, and modification route. A list of
steps may belong to that one action. Keep alternatives outside the confirmation
block so the user is never asked to approve mutually exclusive actions at once.

A reply confirms the pending action when, in ordinary conversational meaning,
it clearly and unconditionally tells the workflow to perform that action.
Natural replies such as `确认`, `可以`, `继续`, `开始吧`, or `按这个做` are valid
examples when their referent is unambiguous. Punctuation, politeness, and other
meaning-preserving wording do not invalidate confirmation.

A direct instruction such as `开始方案` authorizes that stage's standard
in-scope work; `连续执行` authorizes the remaining standard stages for the
current goal. Neither requires a preceding confirmation block. Record the
instruction as authority and explain the route while proceeding.
A bare affirmation binds to the immediately preceding unresolved action in the
same task. Without a clear referent it starts no action. Preserve existing
explicit authorization across turns and stage handoffs.

## Interpret changes and ambiguity

A reply that changes the target, scope, files, identities, settings, authority,
risk, or expected effect is a modification request, even if it begins with an
affirmative phrase. A clear instruction to revise in-scope planning artifacts
is authority for that revision: update the artifacts and affected evidence, then
return the revised combined review in stepwise mode or continue in continuous
mode. It does not accept the old plan or authorize implementation in stepwise
mode. For ambiguous changes or new authority, present only the unresolved choice
or additional action for decision.

An explicit request for continuous execution selects the remaining standard
route for the current goal.
Record `confirmation_intent=continuous` and proceed after mechanical entry
checks. If the route or its standard effects were not explained earlier, explain
them now without another approval. Missing prior disclosure alone is not missing
authority. This covers in-scope planning, implementation, validation, local
integration and standard flow cleanup; it does not grant new external writes,
paid actions, destructive actions outside that cleanup, or scope expansion.

A request to switch back to stepwise mode retains that intent; it never maps to
`continuous`. Stop at the next unresolved decision before entering another stage.

Meaning-preserving wording does not require a refreshed block. If the reply
could refer to more than one pending action, mixes approval with an unclear
condition, or leaves the requested action uncertain, ask one concise question
instead of acting.

Material repository, document, task, model, permission, or external-state drift
invalidates the affected confirmation. Refresh the block from actual state.
Unrelated conversation or formatting changes do not invalidate it.

## Normalize flow intent

After interpreting the user's reply, carry a structured intent through the
workflow:

- `stepwise`: perform only the pending action and stop at the next decision;
- `continuous`: perform the remaining standard stages for the authorized goal automatically
  while no blocker, material decision, authority expansion, or anomaly occurs;
- `modify`: perform an explicitly requested in-scope revision; otherwise resolve
  the changed pending action before dependent work;
- `ambiguous`: do not act; ask one concise question.

When a deterministic protocol records this choice, pass the normalized value
as `confirmation_intent`; preserve the user's natural reply only as audit data.

A natural-language request to continue through all remaining stages maps to
`continuous`; no fixed phrase is required. A simple affirmation maps to the
mode displayed by the pending action, normally `stepwise`.

New external writes, paid actions, destructive operations outside authorized
standard flow cleanup, and other authority expansions still require a complete
impact disclosure and an unambiguous reply. Reuse authorization already given
for the exact action.
The same natural-language rules apply; higher risk strengthens what must be
disclosed and verified, not the spelling of the user's answer.
