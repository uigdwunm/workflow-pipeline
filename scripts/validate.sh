#!/usr/bin/env bash
set -euo pipefail

validate_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --jobs|--nice)
        if [[ $# -lt 2 ]]; then
          printf 'validate.sh: %s requires a value.\n' "$1" >&2
          exit 2
        fi
        shift 2
        ;;
      --jobs=*|--nice=*)
        shift
        ;;
      *)
        printf 'validate.sh requires the full test suite and only accepts --jobs and --nice; use scripts/run_tests.py for filtered tests.\n' >&2
        exit 2
        ;;
    esac
  done
}
validate_arguments "$@"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validator="${CODEX_SKILL_VALIDATOR:-$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py}"
repository_validator="$repo_root/scripts/validate_repository.py"

if [[ ! -f "$validator" ]]; then
  printf 'Skill validator not found: %s\n' "$validator" >&2
  printf 'Set CODEX_SKILL_VALIDATOR to quick_validate.py from the Codex skill-creator Skill.\n' >&2
  exit 1
fi

python3 "$repo_root/scripts/build_skills.py" --check

for skill_dir in "$repo_root"/skills/*; do
  python3 "$validator" "$skill_dir"
done

python3 "$repo_root/scripts/run_tests.py" --repository "$repo_root" "$@"
python3 "$repository_validator" --repository "$repo_root"

printf 'Validation complete.\n'
