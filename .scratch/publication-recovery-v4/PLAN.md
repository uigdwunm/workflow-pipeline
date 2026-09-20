# D publication/recovery v4 repair

Status: ready-for-agent
Lifecycle: completed

Implementation and verification complete; independent coordinator review pending.

Base: 508afc302e6338f62eced44ebe3c905bd9cffa56.
User confirmed the unified plan in this task before implementation. Work is limited
to this isolated checkout; no merge, push, installation or migration is authorized.

## Rules

1. Separate invocation provenance from raw content. The trusted host adapter
   captures call_ref/response_ref/action_id outside raw; C verifies immutable
   associations and current generation, not authenticity of arbitrary JSON.
   New calls with identical raw are valid. Replayed responses do not create
   new proof. Missing/invalid provenance cannot grant proof; adverse observations
   revoke current permission before business validation. Preserve last_stop.
2. Both technical_error and no_progress create durable business_block with an
   exact original subject and trigger. Resume, host observations, receive/accept,
   binding and publication bookkeeping cannot silently clear it.
3. recover-business consumes the original Controller's exact reviewed diagnosis,
   instruction and expected progress. Preserve scope/ref/attempt, history and
   decision idempotency. Clear only that block and revoke old proof atomically;
   original carrier must obtain current query proof before one continuation.
4. Preserve B's existing recover-dispatch replacement authority. Journal its
   exact block identity before the B call, and resolve only that block on success.
   This is a distinct existing Controller recovery, not a Stage2 fallback.
5. Pause/cancel precede work. Recovery may be deferred during pausing/paused;
   explicit resume after stopped proof consumes it. Cancel never consumes it.
   Interruption after explicit unpause must resume the saved decision.
6. B transactions retain request/envelope/source/result. Missing result alone
   permits replay. Business consumption never updates current host state.
7. D retains exact publication intent, target/candidate/parents/tree reconciliation,
   candidate separation, cleanup-only and original acceptance/Phase authority.
   An existing publication permits original finalization, never new host execution.
   New publication intake must retain current quiescence; query exact original ref
   when that evidence is uncertain. Never reissue an already published operation.

## Boundaries

Shared workflow_progress is the principal implementation; the foreground runner
adds only explicit decision transport. A/B and supervision scripts are unchanged.
workflow-progress-v4 changes the shared compatibility key, five packages, contracts,
runner prompt and output-schema description. Runner outer v3 and flow-worktree-v2
remain unchanged. C v1/v2/v3 records are rejected untouched by v4, not migrated.

Independent, continuous and stepwise stages share these mechanical rules. Only
existing stage gates differ. No artificial Phase for standalone stages, no new
Stage1-to-2 producer and no changes to external Matt or native governance.

## Acceptance

Keep three baseline RED cases and their assertions. Cover no-raw-nonce invocation
identity, old response relabeling, same-content fresh responses, adverse/invalid
payload ordering, exact Controller recovery, repeated/stale decisions, pause/cancel,
B and recovery save windows, original dispatcher replacement, Stage4 candidate,
publication intake and current stopped queries. Run targeted suites, build/check,
full scripts/validate.sh and diff check. Document actual host/remote/deployment
limits and submit the exact commit to the coordinating task for independent review.
