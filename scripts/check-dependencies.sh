#!/usr/bin/env bash
set -euo pipefail

required_skills=(
  design-discussion
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

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_root="$repo_root/skills"
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
duplicates=()
for skill in "${required_skills[@]}"; do
  source_candidate="$source_root/$skill/SKILL.md"
  matches=()
  for root in "${search_roots[@]}"; do
    candidate="$root/$skill/SKILL.md"
    if [[ -f "$candidate" ]]; then
      canonical="$(cd "$(dirname "$candidate")" && pwd -P)/SKILL.md"
      already_seen=false
      for observed in "${matches[@]:-}"; do
        if [[ "$observed" == "$canonical" ]]; then
          already_seen=true
          break
        fi
      done
      if [[ "$already_seen" == false ]]; then
        matches+=("$canonical")
      fi
    fi
  done
  if [[ -f "$source_candidate" ]]; then
    printf 'source  %-32s %s\n' "$skill" "$source_candidate"
    if (( ${#matches[@]} > 1 )); then
      printf 'duplicate %-31s filesystem sources outside this checkout:\n' "$skill"
      printf '          %s\n' "${matches[@]}"
      duplicates+=("$skill")
    fi
  elif (( ${#matches[@]} == 1 )); then
    printf 'ok      %-32s %s\n' "$skill" "${matches[0]}"
  elif (( ${#matches[@]} > 1 )); then
    printf 'duplicate %-31s filesystem sources:\n' "$skill"
    printf '          %s\n' "${matches[@]}"
    duplicates+=("$skill")
  else
    printf 'missing %-32s\n' "$skill"
    missing+=("$skill")
  fi
done

if (( ${#missing[@]} > 0 || ${#duplicates[@]} > 0 )); then
  if (( ${#duplicates[@]} > 0 )); then
    printf '\nRemove duplicate filesystem sources for each reported registered name.\n'
  fi
  if (( ${#missing[@]} > 0 )); then
  printf '\nInstall missing Matt dependencies with:\n'
  printf '  npx skills@latest add mattpocock/skills\n'
  printf '\nInstall or link this repository so all five internal Skills are discoverable.\n'
  fi
  exit 1
fi

printf '\nAll declared Skill dependencies are discoverable with no duplicate files in the searched roots.\n'
printf 'This filesystem check cannot inspect the active Codex registry or prove installed versions.\n'
