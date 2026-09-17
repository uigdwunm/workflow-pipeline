# Contributing

Issues and pull requests are welcome.

Before submitting a change:

1. keep each Skill concise and preserve its current stage boundary;
2. do not add user-specific absolute paths or assume a particular Skill install root;
3. update `docs/dependencies.md` when a new external Skill is invoked;
4. run `./scripts/validate.sh`;
5. describe any protocol or compatibility change in the pull request.

Security-sensitive issues involving repository leases, document leases, path validation or handoff authentication should be reported privately through GitHub's security advisory feature when available.

## Source and generated packages

Edit stage templates under `src/stages/`, shared resources under `src/shared/`, and explicit resource mappings in `build/skill-packages.json`. Source entries use `SKILL.md.in`; only the five generated `skills/*/SKILL.md` files are installable. Never hand-edit generated package files or add tests to packages.

```bash
python3 scripts/build_skills.py
python3 scripts/build_skills.py --check
./scripts/validate.sh
```

Commit source and generated changes together. `--check` rebuilds outside the checkout and detects drift without writing packages. Runtime and package tests live under `tests/runtime` and `tests/packages`; generated copies are not test discovery sources. Update the protocol compatibility key when exchanging semantics change; a bundle digest is integrity/identity evidence, not a signature.

For release installation evidence, use `python3 scripts/verify_installer.py --cli <existing-skills-1.6.0-cli.mjs>`. It verifies pinned installer discovery and five isolated package copies using temporary project/state directories; it does not install the CLI or modify user Skill roots. `python3 scripts/field_acceptance.py` produces the T13/T14 real-host worksheet and separate installation-sync steps. Record unexecuted checks honestly; automated registries/executors cannot substitute for actual host registration, Matt calls and Agent handoffs.
