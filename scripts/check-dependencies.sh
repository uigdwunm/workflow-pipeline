#!/usr/bin/env bash
set -euo pipefail

required_skills=(
  problem-framing
  solution-design
  guided-implementation
  change-closure
  setup-matt-pocock-skills
  ask-matt
  grill-with-docs
  grilling
  domain-modeling
  to-spec
  to-tickets
  implement
  tdd
  code-review
)

search_roots=()
if [[ -n "${CODEX_HOME:-}" ]]; then
  search_roots+=("$CODEX_HOME/skills")
fi
search_roots+=(
  "$HOME/.codex/skills"
  "$HOME/.agents/skills"
  "$HOME/.cc-switch/skills"
)

missing=()
for skill in "${required_skills[@]}"; do
  found=""
  for root in "${search_roots[@]}"; do
    candidate="$root/$skill/SKILL.md"
    if [[ -f "$candidate" ]]; then
      found="$candidate"
      break
    fi
  done
  if [[ -n "$found" ]]; then
    printf 'ok      %-32s %s\n' "$skill" "$found"
  else
    printf 'missing %-32s\n' "$skill"
    missing+=("$skill")
  fi
done

if (( ${#missing[@]} > 0 )); then
  printf '\nInstall missing Matt dependencies with:\n'
  printf '  npx skills@latest add mattpocock/skills\n'
  printf '\nInstall or link this repository so all four internal Skills are discoverable.\n'
  exit 1
fi

printf '\nAll declared Skill dependencies are discoverable.\n'
