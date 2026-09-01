#!/usr/bin/env bash
# PreToolUse guard (Bash only): deterministically blocks Claude from deleting, overwriting,
# moving, or truncating .env or .secrets.baseline via a shell command.
#
# Write/Edit on these files are already blocked natively via permissions.deny in settings.json
# (Write(**/.env), Edit(**/.secrets.baseline), etc.) -- those tools take a structured file-path
# argument the permission engine can glob-match directly, so no script is needed there. Bash
# permission rules can't do the equivalent: they only support exact-match or a trailing-wildcard
# *prefix* match against the whole command string (e.g. "Bash(git *)"), with no way to express
# "this command touches path X" -- so "Bash(rm .env)" would catch a command starting with
# exactly that text and nothing else (not "rm -f .env", not "cp .env.example .env", not a
# compound "cd x && rm .env"). This script exists to cover exactly that gap.
#
# This is why a written rule alone (.claude/rules/security.md, "`.env` and other real-credential
# files") wasn't enough either: Claude can still choose to run `rm .env` or
# `cp .env.example .env` if it judges that safe in the moment, which is exactly what happened
# once (a throwaway .env copy created for local test verification, deleted again as cleanup --
# no real file was lost that time, but the pattern risks destroying a real one, and it was a
# Bash command, which is why the fix has to live here rather than only in permissions.deny).
#
# Heuristic, not a full shell parser: gated on the protected filename appearing in the command
# text at all, then on a mutating verb/redirect also being present. False positives (a read-only
# command that happens to trip a verb match) fail safe -- blocked with an explanation -- which is
# the acceptable direction to be wrong in for files that hold real secrets.
set -euo pipefail

input=$(cat)
tool_name=$(jq -r '.tool_name // ""' <<<"$input")

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

if [[ "$tool_name" == "Bash" ]]; then
  command=$(jq -r '.tool_input.command // ""' <<<"$input")
  # Standalone ".env" token (not ".env.example", ".env.local", etc.) or any mention of the
  # secrets baseline.
  if grep -Eq '(^|[^.[:alnum:]_])\.env([^.[:alnum:]_]|$)' <<<"$command" \
    || grep -Eq '\.secrets\.baseline' <<<"$command"; then
    if grep -Eq '\b(rm|mv|cp|shred|truncate|touch|tee|install|dd)\b' <<<"$command" \
      || grep -Eq '>{1,2}' <<<"$command" \
      || grep -Eq '\bsed\b[^|;&]*(-i|--in-place)' <<<"$command" \
      || grep -Eq '\bdetect-secrets\b[^|;&]*\bscan\b' <<<"$command"; then
      deny "Blocked: this Bash command looks like it would delete, move, overwrite, or truncate .env or .secrets.baseline (command: ${command}). Never touch these files -- if .env is missing, ask the user to create it. To refresh the secrets baseline use 'uv run detect-secrets audit .secrets.baseline' or the pre-commit hook, not a direct 'detect-secrets scan' -- that truncates the baseline to just the files you pass it."
    fi
  fi
fi

exit 0
