# Project Agent Instructions

## Local deployment on the owner's Mac

The local-only entrypoint is `scripts/deploy_local.py`; its isolated tests are
`scripts/test_deploy_local.py`. Both are intentionally gitignored. They are not
part of the published packages. Run deployment only when the user explicitly
requests installation/update on this Mac, never during build, commit, release,
CI, or ordinary Skill execution. If the script is missing, stop and report it;
do not invent another installer or temporary link-switching command.

Run `python3 scripts/deploy_local.py --help` and its default plan mode first.
The script builds into temporary staging via the existing `build_skills.py`,
validates self-contained resource closures and compares against the repository's package
output. Never deploy `src/`, cross-Skill resource links, or shared source links.
Matt capabilities remain external dependencies under the existing project rules.

Installed bodies must be real directories at the owner's cc-switch Skill source
paths. Project links remain unchanged. External release directories are archival
artifacts, not the active source of truth. Never turn cc-switch Skill sources into
links to a release directory. Migration from an existing release link preserves
its external target and backs up the link itself.

Execution requires the exact plan digest, a codex-manager-prepared approval bundle
bound to that plan/candidate, and explicit confirmation that existing tasks/runners
are quiescent. The script cannot prove that every dormant task is finished: the
operator must inspect the current tasks before confirming. Do not replace packages
used by an old run, migrate runner records, or assume a backup changes their pinned
identity. The approval bundle supplies reviewed current component/security records
and evidence; the deployer never creates a security `pass` or accepts risk itself.
Read the script's help for the bundle format. Missing/outdated evidence blocks writes.

Deployment preserves consumer flags and project scope, synchronizes exact cc-switch
rows and reviewed codex-manager records, checks admission and registry validity, and
backs up under codex-manager's rollback root. Interrupted deployments leave a journal
and block another deployment until explicitly rolled back. Use the script's rollback
mode only after checking its exact receipt; never restore a whole SQLite database
over unrelated later changes. Do not edit runtime configuration, credentials or
project-owned discussion state as an installation side effect.

## Agent skills

### Issue tracker

Issues and planning artifacts are tracked as local Markdown under `.scratch/`; external pull requests are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default Matt Pocock triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Use the single-context layout: `CONTEXT.md` at the repository root and architectural decisions under `docs/adr/`. See `docs/agents/domain.md`.
