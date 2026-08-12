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

python3 -m unittest \
  "$repo_root/skills/problem-framing/scripts/test_read_thread_settings.py" \
  "$repo_root/skills/guided-implementation/scripts/test_supervision_protocol.py"

if rg -n '/Users/[^/]+/' "$repo_root/skills"; then
  printf 'User-specific absolute paths remain under skills/.\n' >&2
  exit 1
fi

printf 'Validation complete.\n'
