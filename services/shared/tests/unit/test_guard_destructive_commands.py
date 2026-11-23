"""The Claude Code hook `.claude/hooks/guard-destructive-commands.sh`, which asks the user to
confirm a Bash command before Claude runs it when the command would destroy data or history.

It lives here for the same reason as the root conftest guard's tests: `pytest services` is the
canonical run, and `shared` is the workspace's cross-service package. The hook itself has to sit
in `.claude/hooks/`, where Claude Code's settings point.

Real portfolio data was lost twice to TRUNCATEs on the dev stack, so these tests pin both
directions: every destructive pattern must reach a prompt, and the everyday near-misses
(`git push`, `rm file`, a SELECT through psql) must not, or the prompt gets approved out of habit.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[4] / ".claude" / "hooks" / "guard-destructive-commands.sh"


@pytest.fixture
def scratch_root(tmp_path):
    """The directory the hook treats as Claude's scratchpad root: `$TMPDIR/claude-<uid>`."""
    return tmp_path / f"claude-{os.getuid()}"


@pytest.fixture
def run_hook(tmp_path):
    def run(command, *, tool_name="Bash", permission_mode="default", cwd=None):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"command": command},
            "permission_mode": permission_mode,
            "cwd": str(cwd or tmp_path),
        }
        result = subprocess.run(
            ["bash", str(_HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={**os.environ, "TMPDIR": str(tmp_path)},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)["hookSpecificOutput"]

    return run


ASKS = [
    # Recursive rm anywhere but the scratchpad.
    pytest.param("rm -rf build", "rm", id="rm-rf"),
    pytest.param("rm -fr build", "rm", id="rm-fr"),
    pytest.param("rm -r build", "rm", id="rm-r-without-f"),
    pytest.param("rm -R build", "rm", id="rm-capital-R"),
    pytest.param("rm -r -f build", "rm", id="rm-separate-flags"),
    pytest.param("rm --recursive --force build", "rm", id="rm-long-flags"),
    pytest.param("rm -rfv /", "rm", id="rm-root"),
    pytest.param("rm -rf {scratch}/x /srv/data", "rm", id="rm-one-operand-outside"),
    pytest.param("rm -rf {scratch}/../../srv", "rm", id="rm-scratch-traversal"),
    pytest.param("rm -rf {scratch}-evil/x", "rm", id="rm-scratch-prefix-lookalike"),
    pytest.param('rm -rf "$DIR"', "rm", id="rm-variable"),
    pytest.param("rm -rf ~/projects", "rm", id="rm-home"),
    pytest.param("cd {scratch} && rm -rf x", "rm", id="rm-relative-path"),
    pytest.param("sudo rm -rf /var/lib/postgresql", "rm", id="rm-sudo"),
    pytest.param("ls && rm -rf build", "rm", id="rm-after-and"),
    pytest.param("echo hi; rm -rf build", "rm", id="rm-after-semicolon"),
    pytest.param("bash -c 'rm -rf build'", "rm", id="rm-in-bash-c"),
    pytest.param("FOO=1 rm -rf build", "rm", id="rm-env-assignment"),
    pytest.param("find . -name cache -exec rm -rf {} +", "rm", id="rm-find-exec"),
    pytest.param("ls | xargs rm -rf", "rm", id="rm-xargs"),
    # Force pushes.
    pytest.param("git push --force", "force push", id="push-force"),
    pytest.param("git push -f origin main", "force push", id="push-f"),
    pytest.param("git push -uf origin main", "force push", id="push-grouped-f"),
    pytest.param("git push --force-with-lease", "force push", id="push-force-with-lease"),
    pytest.param("git push origin +main", "force push", id="push-plus-refspec"),
    pytest.param("git -C services push --force", "force push", id="push-git-C"),
    pytest.param("git push origin main \\\n  --force", "force push", id="push-continuation"),
    pytest.param("cd repo && git push -f", "force push", id="push-compound"),
    # History and working-tree resets.
    pytest.param("git reset --hard", "reset --hard", id="reset-hard"),
    pytest.param("git reset --hard origin/main", "reset --hard", id="reset-hard-ref"),
    pytest.param("git -c core.pager=cat reset --hard HEAD~1", "reset --hard", id="reset-git-c"),
    pytest.param("git clean -fdx", "git clean", id="clean-fdx"),
    pytest.param("git clean -f", "git clean", id="clean-f"),
    pytest.param("git clean -fd", "git clean", id="clean-fd"),
    pytest.param("git clean --force -d", "git clean", id="clean-long"),
    pytest.param("git clean -xdf", "git clean", id="clean-xdf"),
    pytest.param("git branch -D feature", "branch -D", id="branch-D"),
    pytest.param("git branch --delete --force feature", "branch -D", id="branch-long"),
    pytest.param("git branch -df feature", "branch -D", id="branch-df"),
    pytest.param("git branch -d -f feature", "branch -D", id="branch-d-f"),
    # SQL that destroys data, sent through psql or docker exec.
    pytest.param('psql -c "DROP TABLE users"', "drop", id="sql-drop-table"),
    pytest.param(
        'psql "$DATABASE_URL" -c "drop schema portfolio cascade"', "drop", id="sql-drop-lower"
    ),
    pytest.param(
        'docker exec -i stakehold-db psql -U postgres -c "TRUNCATE holdings"',
        "truncate",
        id="sql-truncate-docker-exec",
    ),
    pytest.param(
        'docker compose exec db psql -c "DELETE FROM holdings"', "delete", id="sql-delete-no-where"
    ),
    pytest.param(
        'psql -c "DELETE FROM holdings; DELETE FROM users WHERE id = 1"',
        "delete",
        id="sql-delete-one-of-two-unscoped",
    ),
    pytest.param("psql <<'SQL'\nTRUNCATE prices;\nSQL", "truncate", id="sql-heredoc"),
    pytest.param('echo "truncate prices" | psql', "truncate", id="sql-piped"),
    pytest.param(
        'psql -c "ALTER TABLE users DROP COLUMN email"', "drop", id="sql-alter-drop-column"
    ),
    pytest.param("dropdb portfolio", "dropdb", id="sql-dropdb"),
    # Docker volumes hold the dev databases.
    pytest.param("docker compose down -v", "volume", id="compose-down-v"),
    pytest.param("docker compose down --volumes", "volume", id="compose-down-volumes"),
    pytest.param("docker-compose down -v", "volume", id="compose-legacy-down-v"),
    pytest.param(
        "docker compose -f docker-compose.test.yml down -v", "volume", id="compose-file-down-v"
    ),
    pytest.param("docker volume rm stakehold_pgdata", "volume", id="volume-rm"),
    pytest.param("docker volume prune -f", "volume", id="volume-prune"),
    pytest.param("docker system prune -af", "system prune", id="system-prune"),
    # GitHub objects that can't be restored.
    pytest.param("gh repo delete owner/repo --yes", "repo delete", id="gh-repo-delete"),
    pytest.param("gh issue delete 149 --yes", "issue delete", id="gh-issue-delete"),
    pytest.param("gh release delete v1.0", "release delete", id="gh-release-delete"),
]

ALLOWED = [
    pytest.param("rm file.txt", id="rm-file"),
    pytest.param("rm -f file.txt", id="rm-f-file"),
    pytest.param("rmdir build", id="rmdir"),
    pytest.param("echo rm -rf build", id="rm-as-echo-argument"),
    pytest.param("rm -rf {scratch}/session/scratchpad/out", id="rm-in-scratchpad"),
    pytest.param('rm -rf "{scratch}/a b"', id="rm-in-scratchpad-quoted"),
    pytest.param("rm -rf {scratch}/x/*", id="rm-in-scratchpad-glob"),
    pytest.param("rm -rf {scratch}/x 2>/dev/null", id="rm-in-scratchpad-redirect"),
    pytest.param("git push", id="push"),
    pytest.param("git push -u origin pd-2", id="push-set-upstream"),
    pytest.param("git push --follow-tags", id="push-follow-tags"),
    pytest.param("git push origin main", id="push-explicit"),
    pytest.param("git reset", id="reset"),
    pytest.param("git reset --soft HEAD~1", id="reset-soft"),
    pytest.param("git reset HEAD file.py", id="reset-path"),
    pytest.param("git clean -n", id="clean-dry-run"),
    pytest.param("git clean -nd", id="clean-dry-run-dirs"),
    pytest.param("git branch -d feature", id="branch-d"),
    pytest.param("git branch", id="branch"),
    pytest.param("git branch --list", id="branch-list"),
    pytest.param("git status && git log --oneline", id="git-read-only"),
    pytest.param('git commit -m "Guard git reset --hard and rm -rf"', id="commit-mentions"),
    pytest.param('grep -rn "git push --force" .claude', id="grep-mentions"),
    pytest.param('psql -c "SELECT * FROM holdings"', id="sql-select"),
    pytest.param('psql -c "DELETE FROM holdings WHERE id = 1"', id="sql-delete-with-where"),
    pytest.param('psql -c "DELETE FROM holdings\n  WHERE id = 1"', id="sql-delete-where-next-line"),
    pytest.param("grep -rn TRUNCATE services/", id="grep-truncate"),
    pytest.param('grep -rn "DROP TABLE" services/auth', id="grep-drop"),
    pytest.param("docker compose down", id="compose-down"),
    pytest.param("docker compose up -d --wait", id="compose-up"),
    pytest.param("docker volume ls", id="volume-ls"),
    pytest.param("docker system df", id="system-df"),
    pytest.param("gh issue close 149", id="gh-issue-close"),
    pytest.param("gh issue view 149", id="gh-issue-view"),
    pytest.param("gh repo view", id="gh-repo-view"),
    pytest.param("gh release list", id="gh-release-list"),
]


@pytest.mark.parametrize(("command", "rule"), ASKS)
def test_destructive_command_asks_for_confirmation(run_hook, scratch_root, command, rule):
    decision = run_hook(command.replace("{scratch}", str(scratch_root)))

    assert decision is not None, "expected a confirmation prompt, got none"
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "ask"
    assert rule.lower() in decision["permissionDecisionReason"].lower()


@pytest.mark.parametrize("command", ALLOWED)
def test_near_miss_runs_without_a_prompt(run_hook, scratch_root, command):
    assert run_hook(command.replace("{scratch}", str(scratch_root))) is None


def test_reason_says_what_would_be_destroyed_and_shows_the_command(run_hook):
    decision = run_hook("git reset --hard origin/main")

    reason = decision["permissionDecisionReason"]
    assert "uncommitted changes" in reason
    assert "git reset --hard origin/main" in reason


def test_every_matching_rule_is_named(run_hook):
    decision = run_hook("git reset --hard && git push --force")

    reason = decision["permissionDecisionReason"].lower()
    assert "reset --hard" in reason
    assert "force push" in reason


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "auto", "plan"])
def test_modes_that_can_prompt_get_ask(run_hook, mode):
    assert run_hook("git reset --hard", permission_mode=mode)["permissionDecision"] == "ask"


def test_bypass_permissions_mode_denies_instead_of_asking(run_hook):
    # The Claude Code docs don't say whether a hook's "ask" still prompts in
    # bypassPermissions, so the hook fails closed there.
    decision = run_hook("git reset --hard", permission_mode="bypassPermissions")

    assert decision["permissionDecision"] == "deny"
    assert "bypassPermissions" in decision["permissionDecisionReason"]


def test_bypass_permissions_mode_still_allows_near_misses(run_hook):
    assert run_hook("git push", permission_mode="bypassPermissions") is None


def test_other_tools_are_ignored(run_hook):
    assert run_hook("rm -rf /", tool_name="Read") is None


def test_psql_file_with_destructive_sql_asks(run_hook, tmp_path):
    sql = tmp_path / "reset.sql"
    sql.write_text("BEGIN;\nTRUNCATE prices;\nCOMMIT;\n")

    decision = run_hook(f"psql -f {sql}")

    assert decision["permissionDecision"] == "ask"
    assert "truncate" in decision["permissionDecisionReason"].lower()


def test_psql_file_given_by_relative_path_is_read_from_cwd(run_hook, tmp_path):
    (tmp_path / "reset.sql").write_text("DROP TABLE prices;\n")

    decision = run_hook("psql --file=reset.sql", cwd=tmp_path)

    assert decision["permissionDecision"] == "ask"


def test_psql_stdin_redirect_from_file_with_destructive_sql_asks(run_hook, tmp_path):
    sql = tmp_path / "wipe.sql"
    sql.write_text("DELETE FROM holdings;\n")

    decision = run_hook(f"docker exec -i db psql -U postgres < {sql}")

    assert decision["permissionDecision"] == "ask"


def test_psql_file_with_harmless_sql_is_allowed(run_hook, tmp_path):
    sql = tmp_path / "report.sql"
    sql.write_text("SELECT count(*) FROM holdings;\n")

    assert run_hook(f"psql -f {sql}") is None


def test_psql_file_the_hook_cannot_read_is_allowed(run_hook, tmp_path):
    # A known gap: a path inside a container, or one that doesn't exist, can't be checked.
    assert run_hook(f"psql -f {tmp_path / 'missing.sql'}") is None
