#!/usr/bin/env bash
# PreToolUse guard (Bash only): deterministically blocks Claude from deleting, overwriting,
# moving, or truncating .env (and its real, non-template variants) or .secrets.baseline via a
# shell command.
#
# Write/Edit on these files are already blocked natively via permissions.deny in settings.json
# (Write(**/.env), Write(**/.env.production), Edit(**/.secrets.baseline), etc.) -- those tools
# take a structured file-path argument the permission engine can glob-match directly, so no
# script is needed there. Bash permission rules can't do the equivalent: they only support
# exact-match or a trailing-wildcard *prefix* match against the whole command string (e.g.
# "Bash(git *)"), with no way to express "this command touches path X" -- so "Bash(rm .env)"
# would catch a command starting with exactly that text and nothing else (not "rm -f .env", not
# "cp .env.example .env", not a compound "cd x && rm .env"). This script exists to cover exactly
# that gap, for every filename permissions.deny protects -- not just the bare ".env" case.
#
# This is why a written rule alone (.claude/rules/security.md, "`.env` and other real-credential
# files") wasn't enough either: Claude can still choose to run `rm .env` or
# `cp .env.example .env` if it judges that safe in the moment, which is exactly what happened
# once (a throwaway .env copy created for local test verification, deleted again as cleanup --
# no real file was lost that time, but the pattern risks destroying a real one, and it was a
# Bash command, which is why the fix has to live here rather than only in permissions.deny).
#
# Heuristic, not a full shell parser: gated on a protected filename appearing in the command text
# at all, then on every "segment" of the command (split on ;, &&, ||, |) being recognizably
# read-only. This is a whitelist, not a blacklist (security.md, "Whitelist, don't blacklist") --
# an earlier version of this script enumerated mutating verbs/redirects instead, which meant any
# verb not yet thought of (find -delete, rsync, ln -sf, git clean/checkout, a Python one-liner)
# bypassed it silently. Denying-by-default and naming the *safe* commands closes that off: an
# unrecognized command fails closed. False positives (a read-only command that happens not to
# match the whitelist) fail safe -- blocked with an explanation -- which is the acceptable
# direction to be wrong for files that hold real secrets.
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

# Matches the bare ".env" token, or ".env.<suffix>" for exactly the suffixes permissions.deny
# also protects (local, *.local, development, staging, production, test, ci, dev, prod) -- kept
# in sync with settings.json's list by design, so a new protected suffix needs adding in both
# places. Deliberately does NOT match ".env.example": that file is a template, not a credential.
env_pattern='(^|[^.[:alnum:]_])\.env(\.[[:alnum:]_-]+\.local|\.(local|development|staging|production|test|ci|dev|prod))?([^.[:alnum:]_]|$)'
# The filename half of env_pattern, reused below to catch a redirect that writes directly *into*
# one of these files -- deliberately without env_pattern's boundary anchors, since here it's
# matched right after a '>' rather than at a token boundary.
protected_filename='\.env(\.[[:alnum:]_-]+\.local|\.(local|development|staging|production|test|ci|dev|prod))?\b|\.secrets\.baseline'

# Read-only inspection commands, safe no matter what path they're pointed at.
safe_leading_command='^(cat|less|more|head|tail|wc|file|stat|ls|grep|egrep|fgrep|rg|ag|jq|md5sum|sha256sum|realpath|dirname|basename|diff)([[:space:]]|$)'
# git subcommands that only read, plus `git add` (stages the index, never touches the working
# tree file itself) and `git commit` (records a message + whatever's staged; never deletes,
# overwrites, or truncates a working-tree file, so a commit message that merely *mentions* .env
# -- e.g. describing a fix to this very guard -- isn't a reason to deny it).
safe_git_command='^git[[:space:]]+(diff|log|show|status|blame|ls-files|add|commit)([[:space:]]|$)'
# The two sanctioned ways to touch .secrets.baseline: detect-secrets' own audit flow (marks
# findings reviewed, never truncates) and pre-commit (runs detect-secrets the same way CI does).
# A raw `detect-secrets scan` is deliberately NOT whitelisted -- passed explicit file arguments,
# it silently truncates the baseline to just those files.
safe_baseline_command='^(uv[[:space:]]+run[[:space:]]+)?(detect-secrets[[:space:]]+audit|pre-commit)([[:space:]]|$)'

is_segment_safe() {
  local segment="$1"
  # An output redirect whose *target* is one of the protected files can create, truncate, or
  # overwrite it regardless of the leading command, so that disqualifies a segment outright.
  # Matched narrowly -- '>'/'>>' directly followed by the filename -- rather than "segment
  # contains '>' anywhere": a multi-line argument (a commit message, a heredoc body) can contain
  # a literal '>' with no shell meaning at all (e.g. a "Name <email>" trailer), and that must not
  # itself disqualify the segment. Input redirection ('<') is not checked at all -- it only reads
  # a file, and banning it would also ban '<<'/'<<<' heredocs, the common, safe way to pass a
  # multi-line argument like this hook's own commit message.
  if grep -Eq ">{1,2}[[:space:]]*(${protected_filename})" <<<"$segment"; then
    return 1
  fi
  # Only the segment's own first line is the actual command -- a multi-line argument (a commit
  # message, a heredoc body) can span many more lines after it, and grep's ^/$ anchors match
  # per-line on multi-line input, so checking the whole segment here could let a later line that
  # merely *looks* like a safe command (e.g. a "cat ..." bullet point in a commit message) vouch
  # for a genuinely unsafe leading command.
  local first_line="${segment%%$'\n'*}"
  grep -Eq "$safe_leading_command" <<<"$first_line" \
    || grep -Eq "$safe_git_command" <<<"$first_line" \
    || grep -Eq "$safe_baseline_command" <<<"$first_line"
}

if [[ "$tool_name" == "Bash" ]]; then
  command=$(jq -r '.tool_input.command // ""' <<<"$input")

  if grep -Eq "$env_pattern" <<<"$command" || grep -Eq '\.secrets\.baseline' <<<"$command"; then
    all_segments_safe=true
    # Split only on actual shell separators (;, &&, ||, |, &), not on newlines -- a multi-line
    # argument (a commit message body, a heredoc) is one segment, not one per line, so an
    # injected marker character is used instead of tr's newline substitution, which would have
    # conflated "next segment" with "next line of this same argument".
    delimiter=$'\x01'
    blob="$(printf '%s' "$command" | sed -E "s/(&&|\|\||;|\||&)/${delimiter}/g")"
    segments=()
    while IFS= read -r -d "$delimiter" segment; do
      segments+=("$segment")
    done < <(printf '%s%s' "$blob" "$delimiter")

    for segment in "${segments[@]}"; do
      segment="$(sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' <<<"$segment")"
      [[ -z "$segment" ]] && continue
      if ! is_segment_safe "$segment"; then
        all_segments_safe=false
        break
      fi
    done

    if [[ "$all_segments_safe" != true ]]; then
      deny "Blocked: this Bash command touches .env or .secrets.baseline and isn't recognizable as a read-only command (command: ${command}). Never touch these files -- if .env is missing, ask the user to create it. To refresh the secrets baseline use 'uv run detect-secrets audit .secrets.baseline' or the pre-commit hook, not a direct 'detect-secrets scan' -- that truncates the baseline to just the files you pass it."
    fi
  fi
fi

exit 0
