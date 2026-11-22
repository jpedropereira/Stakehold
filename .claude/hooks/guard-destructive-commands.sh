#!/usr/bin/env bash
# PreToolUse guard (Bash only): asks the user to confirm a command before Claude runs it, when
# the command would destroy data or history that can't be rebuilt from the repo. Real portfolio
# data was lost twice to TRUNCATEs on the dev stack; a written rule didn't stop it, because Claude
# can always decide in the moment that a command is safe. A hook decides the same way every time.
#
# It returns "ask", not "deny": the user does sometimes want Claude to run these, and a hard
# block would only push them to the terminal. The prompt says what the command would destroy, so
# approving it is a decision rather than a reflex. The one exception is bypassPermissions mode:
# Claude Code's docs don't say whether a hook's "ask" still prompts there, so the hook denies
# instead and leaves the command to the user. (A -p run with nobody to answer denies by itself.)
#
# Why a hook and not `permissions.ask` in settings.json: Bash permission rules only match a prefix
# of the whole command string, so "Bash(git reset --hard*)" misses `cd x && git reset --hard`,
# `git -C x reset --hard`, and SQL inside `psql -c "..."`. The same gap is why
# guard-credential-files.sh exists.
#
# Blacklist, by necessity. guard-credential-files.sh can whitelist because it is gated on a
# filename; "every command that could destroy something" has no such gate, so this lists known
# destructive patterns. It is a heuristic, not a shell parser. A pattern counts only at a command
# position: line start, after ; && || | & ( { $( or a backtick, inside `-c '...'` / `eval`, or
# after `find -exec`, optionally behind sudo, env, VAR=value, xargs, command, exec, nohup, time or
# nice. `git` global options (-C, -c, --git-dir, ...) are skipped before the subcommand. Text that
# merely mentions a command (a commit message, a grep pattern) doesn't count, except on a line
# of its own in a heredoc; pass such text with `-F <file>` / `--body-file` to avoid the prompt.
#
# What it asks about:
#   - recursive rm (-r/-R/--recursive, with or without -f) unless every operand is a literal
#     path under the scratchpad root ${TMPDIR:-/tmp}/claude-<uid>/ with no "..", "$" or "`";
#   - git push --force / -f / --force-with-lease / +refspec, git reset --hard, git clean -f,
#     git branch -D (or --delete --force);
#   - SQL DROP, TRUNCATE, or DELETE FROM with no WHERE, when the command runs psql or docker exec,
#     including SQL in a `psql -f FILE` / `< FILE` the hook can read on the host; dropdb;
#   - docker compose down -v, docker volume rm/prune, docker system prune;
#   - gh repo delete, gh issue delete, gh release delete.
#
# Known gaps (allowed without a prompt): SQL in a file the hook can't read (e.g. a path inside a
# container), SQL sent through Python/psycopg, `find -delete`, `git checkout -- .` and
# `git restore .`, `git push --delete`, `gh api -X DELETE`, `ssh host '...'`, and anything reached
# through a variable, alias, function or script. If jq is missing the hook errors, and Claude Code
# treats a hook error as non-blocking, so it fails open.
#
# Tests: services/shared/tests/unit/test_guard_destructive_commands.py.
set -euo pipefail

input=$(cat)
tool_name=$(jq -r '.tool_name // ""' <<<"$input")
[[ "$tool_name" == "Bash" ]] || exit 0

command=$(jq -r '.tool_input.command // ""' <<<"$input")
permission_mode=$(jq -r '.permission_mode // ""' <<<"$input")
cwd=$(jq -r '.cwd // ""' <<<"$input")

# A backslash-newline continues the same command, so join those lines first.
continuation=$'\\\n'
text=${command//"$continuation"/ }

scratch_root="${TMPDIR:-/tmp}"
scratch_root="${scratch_root%/}/claude-$(id -u)"

boundary="(^|[;&|({\`]|\\\$\\(|-c[[:space:]]+['\"]|eval[[:space:]]+['\"]?|-exec(dir)?[[:space:]]+)[[:space:]]*"
option_with_value='[[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?'
assignment='[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*'
prefix="((sudo|doas|xargs)(${option_with_value})*[[:space:]]+|(command|exec|nohup|time|nice)[[:space:]]+|env([[:space:]]+-[^[:space:]]+)*([[:space:]]+${assignment})*[[:space:]]+|${assignment}[[:space:]]+)*"
git_command='git([[:space:]]+(-C|-c|--git-dir|--work-tree|--namespace)[[:space:]]+[^[:space:]]+|[[:space:]]+--[a-z-]+(=[^[:space:]]*)?|[[:space:]]+-[pP])*[[:space:]]+'

# Prints, one per line, each command-position use of the command matching $1, from the command
# word up to the next separator, with the boundary and any prefixes stripped off.
occurrences() {
  { grep -oE "${boundary}${prefix}($1)([[:space:]][^;&|)\`]*|$)" <<<"$text" || true; } \
    | sed -E "s#^${boundary}${prefix}##"
}

# Prints the arguments after each `git ... <subcommand>` in the command.
git_args() {
  occurrences "${git_command}$1" | sed -E "s#^${git_command}$1##"
}

# True if the whitespace-separated words in $1 include one matching the regex $2 in full.
has_token() {
  grep -qE "(^|[[:space:]])($2)([[:space:]]|$)" <<<"$1"
}

any_git_args_have() {
  local args
  while IFS= read -r args; do
    has_token "$args" "$2" && return 0
  done < <(git_args "$1")
  return 1
}

in_scratchpad() {
  [[ "$1" == "$scratch_root"/* && "$1" != *..* && "$1" != *'$'* && "$1" != *'`'* ]]
}

# True if some recursive rm in the command could reach outside the scratchpad: an operand
# outside it, an operand that can't be resolved here, or no operands at all (xargs supplies them).
recursive_rm_outside_scratchpad() {
  local occurrence tokens token recursive operands outside skip_next end_of_options
  local redirect_alone='^[0-9&]*[<>]+$' redirect_attached='^[0-9&]*[<>]'
  while IFS= read -r occurrence; do
    # xargs does the shell's quote removal without expanding anything; unbalanced quotes
    # (e.g. the rest of a `bash -c '...'`) make it fail, and an unparseable rm asks.
    if ! tokens=$(printf '%s' "${occurrence#rm}" | xargs -n1 printf '%s\n' 2>/dev/null); then
      return 0
    fi
    recursive=false operands=0 outside=false skip_next=false end_of_options=false
    while IFS= read -r token; do
      [[ -z "$token" ]] && continue
      if $skip_next; then
        skip_next=false
        continue
      fi
      if [[ "$token" =~ $redirect_alone ]]; then
        skip_next=true
        continue
      fi
      [[ "$token" =~ $redirect_attached ]] && continue
      if ! $end_of_options && [[ "$token" == -- ]]; then
        end_of_options=true
        continue
      fi
      if ! $end_of_options && [[ "$token" == -* ]]; then
        [[ "$token" =~ ^-[a-zA-Z]*[rR] || "$token" == --recursive ]] && recursive=true
        continue
      fi
      operands=$((operands + 1))
      in_scratchpad "$token" || outside=true
    done <<<"$tokens"
    if $recursive && { $outside || ((operands == 0)); }; then
      return 0
    fi
  done < <(occurrences 'rm')
  return 1
}

runs_sql_client() {
  grep -qE '(^|[^[:alnum:]_-])psql([^[:alnum:]_-]|$)' <<<"$text" && return 0
  grep -qE '(^|[^[:alnum:]_-])docker(-compose)?([^[:alnum:]_-]|$)' <<<"$text" \
    && grep -qE '(^|[[:space:]])exec([[:space:]]|$)' <<<"$text"
}

# The SQL to check: the command text, plus any `-f FILE`, `--file=FILE` or `< FILE` it names that
# can be read here, all on one line so a DELETE's WHERE on the next line still counts.
sql_text() {
  local path
  printf '%s ' "$text"
  while IFS= read -r path; do
    path=${path//[\"\']/}
    [[ -z "$path" ]] && continue
    [[ "$path" == /* ]] || path="${cwd%/}/$path"
    [[ -f "$path" && -r "$path" ]] && head -c 1000000 "$path"
    printf ' '
  done < <(
    {
      grep -oE '(^|[[:space:]])(-f|--file)(=|[[:space:]]*)[^[:space:];&|]+' <<<"$text" || true
      grep -oE '(^|[^<])<[[:space:]]*[^[:space:]<;&|]+' <<<"$text" || true
    } | sed -E 's#^.*(-f|--file|<)(=|[[:space:]]*)##'
  )
}

unscoped_delete() {
  local statement
  while IFS= read -r statement; do
    grep -qiE '(^|[^[:alnum:]_])where([^[:alnum:]_]|$)' <<<"$statement" || return 0
  done < <(grep -oiE '(^|[^[:alnum:]_])delete[[:space:]]+from[^;]*' <<<"$1" || true)
  return 1
}

reasons=()

if recursive_rm_outside_scratchpad; then
  reasons+=("recursive rm outside the scratchpad permanently deletes those files and directories, with no trash to restore them from")
fi

if any_git_args_have push '--force|--force-with-lease(=[^[:space:]]*)?|-[a-zA-Z]*f[a-zA-Z]*|\+[^[:space:]]+'; then
  reasons+=("force push overwrites the remote branch's history, so commits that exist only on the remote are lost")
fi
if any_git_args_have reset '--hard'; then
  reasons+=("git reset --hard discards uncommitted changes in tracked files")
fi
if any_git_args_have clean '--force|-[a-zA-Z]*f[a-zA-Z]*'; then
  reasons+=("git clean -f permanently deletes untracked files (with -x, ignored ones too, such as .env)")
fi
while IFS= read -r args; do
  if has_token "$args" '-[a-zA-Z]*D[a-zA-Z]*' \
    || { has_token "$args" '--delete|-[a-zA-Z]*d[a-zA-Z]*' && has_token "$args" '--force|-[a-zA-Z]*f[a-zA-Z]*'; }; then
    reasons+=("git branch -D deletes the branch even if its commits aren't merged anywhere")
    break
  fi
done < <(git_args branch)

if runs_sql_client; then
  sql=$(sql_text | tr '\n' ' ')
  if grep -qiE '(^|[^[:alnum:]_])drop[[:space:]]+[[:alpha:]]' <<<"$sql"; then
    reasons+=("SQL DROP removes the database object and all the data in it")
  fi
  if grep -qiE '(^|[^[:alnum:]_])truncate([^[:alnum:]_]|$)' <<<"$sql"; then
    reasons+=("SQL TRUNCATE deletes every row in the table")
  fi
  if unscoped_delete "$sql"; then
    reasons+=("SQL DELETE with no WHERE deletes every row in the table")
  fi
fi
if [[ -n "$(occurrences 'dropdb')" ]]; then
  reasons+=("dropdb deletes the whole database")
fi

while IFS= read -r args; do
  if has_token "$args" 'down' && has_token "$args" '--volumes|-[a-zA-Z]*v[a-zA-Z]*'; then
    reasons+=("docker compose down -v deletes the stack's volumes, including its Postgres data")
    break
  fi
done < <(occurrences 'docker[[:space:]]+compose|docker-compose')
while IFS= read -r args; do
  if grep -qE '(^|[[:space:]])volume[[:space:]]+(rm|remove|prune)([[:space:]]|$)' <<<"$args"; then
    reasons+=("docker volume rm/prune deletes Docker volumes and the data in them, including Postgres data")
    break
  fi
done < <(occurrences 'docker')
while IFS= read -r args; do
  if grep -qE '(^|[[:space:]])system[[:space:]]+prune([[:space:]]|$)' <<<"$args"; then
    reasons+=("docker system prune removes stopped containers, unused images and networks, and with --volumes the data in volumes")
    break
  fi
done < <(occurrences 'docker')

while IFS= read -r args; do
  if grep -qE '(^|[[:space:]])repo[[:space:]]+delete([[:space:]]|$)' <<<"$args"; then
    reasons+=("gh repo delete deletes the GitHub repository with its issues, PRs and history")
  fi
  if grep -qE '(^|[[:space:]])issue[[:space:]]+delete([[:space:]]|$)' <<<"$args"; then
    reasons+=("gh issue delete permanently deletes the issue and its comments (closing it keeps them)")
  fi
  if grep -qE '(^|[[:space:]])release[[:space:]]+delete([[:space:]]|$)' <<<"$args"; then
    reasons+=("gh release delete deletes the release and its notes")
  fi
done < <(occurrences 'gh')

((${#reasons[@]})) || exit 0

summary=$(printf '%s; ' "${reasons[@]}")
summary=${summary%; }

if [[ "$permission_mode" == "bypassPermissions" ]]; then
  decision=deny
  reason="Blocked: ${summary}. This session is in bypassPermissions mode, where Claude Code may skip a hook's confirmation prompt, so this hook refuses instead. If you want this run, run it yourself. Command: ${command}"
else
  decision=ask
  reason="Destructive command: ${summary}. Approve only if you mean it. Command: ${command}"
fi

jq -n --arg decision "$decision" --arg reason "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: $decision,
    permissionDecisionReason: $reason
  }
}'
exit 0
