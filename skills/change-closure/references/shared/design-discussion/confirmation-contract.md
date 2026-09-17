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

Bind the reply only to the immediately preceding unresolved confirmation block
in the same task. A reply cannot approve an older block, another task's block,
an undisclosed action, or a later stage. When there is no such block, a bare
affirmation is ordinary conversation and starts no action.

## Interpret changes and ambiguity

A reply that changes the target, scope, files, identities, settings, authority,
risk, or expected effect is a modification request, even if it begins with an
affirmative phrase. Incorporate the requested change, refresh every affected
field and evidence value, and present the resulting pending action again.

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
- `continuous`: perform the disclosed remaining standard stages automatically
  while no blocker, material decision, authority expansion, or anomaly occurs;
- `modify`: do not act; update the pending action;
- `ambiguous`: do not act; ask one concise question.

When a deterministic protocol records this choice, pass the normalized value
as `confirmation_intent`; preserve the user's natural reply only as audit data.

A natural-language request to continue through all remaining stages maps to
`continuous`; no fixed phrase is required. A simple affirmation maps to the
mode displayed by the pending action, normally `stepwise`.

External writes, paid actions, destructive operations, and other authority
expansions still require a complete impact disclosure and an unambiguous reply.
The same natural-language rules apply; higher risk strengthens what must be
disclosed and verified, not the spelling of the user's answer.
