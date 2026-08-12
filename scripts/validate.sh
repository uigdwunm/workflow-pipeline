#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validator="${CODEX_SKILL_VALIDATOR:-$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py}"

if [[ ! -f "$validator" ]]; then
  printf 'Skill validator not found: %s\n' "$validator" >&2
  printf 'Set CODEX_SKILL_VALIDATOR to quick_validate.py from the Codex skill-creator Skill.\n' >&2
  exit 1
fi

for skill_dir in "$repo_root"/skills/*; do
  python3 "$validator" "$skill_dir"
done

test_files=()
while IFS= read -r -d '' test_file; do
  test_files+=("$test_file")
done < <(find "$repo_root/skills" -type f -path '*/scripts/test_*.py' -print0 | sort -z)

python3 -m unittest "${test_files[@]}"

if rg -n '/Users/[^/]+/' "$repo_root/skills"; then
  printf 'User-specific absolute paths remain under skills/.\n' >&2
  exit 1
fi

printf 'Validation complete.\n'
