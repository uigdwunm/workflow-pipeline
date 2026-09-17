#!/usr/bin/env bash
set -euo pipefail

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

test_files=()
while IFS= read -r test_file; do
  test_files+=("$test_file")
done < <(python3 "$repository_validator" --repository "$repo_root" --list-tests)

python3 -m unittest "${test_files[@]}"
python3 "$repository_validator" --repository "$repo_root"

printf 'Validation complete.\n'
