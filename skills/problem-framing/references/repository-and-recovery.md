# Repository Coordination and Recovery

Resolve the primary checkout and inspect implementation source protection
before any documentation edit. An exact protected source path is read-only.
When the user requests a change, create a successor document or stop all
dependent implementations before changing and re-checkpointing the source.

For an unprotected write, acquire and verify the exact-path document lease,
edit only the named paths, and release before waiting. Before a planning commit,
require the primary checkout and index to contain only the stage-owned paths,
acquire the short repository coordination barrier with purpose
`checkpoint-publish`, compare the expected HEAD, stage exact pathspecs, commit,
verify, and release. Worktree implementation activity does not otherwise block
planning commits.

Unknown user work uses the stable workspace-decision block. Never stash, clean,
reset or overwrite it. Recovery revalidates exact bytes, source protection,
document lease, branch and HEAD without repeating completed questioning.
