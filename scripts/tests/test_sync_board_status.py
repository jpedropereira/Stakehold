"""Tests for scripts/sync_board_status.py, which keeps the roadmap board's Blocked/Ready
statuses in line with each issue's Blocked-by relationships (RT-1).

The script isn't part of an installed package, so it is loaded from its path. Its GitHub side
is tested through the `graphql` callable it is given, so no test touches the network.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "sync_board_status.py"
_spec = importlib.util.spec_from_file_location("sync_board_status", _SCRIPT)
sync = importlib.util.module_from_spec(_spec)
sys.modules["sync_board_status"] = sync
_spec.loader.exec_module(sync)


def blocker(number, state="OPEN", reason=None):
    return sync.Blocker(number=number, state=state, state_reason=reason)


def item(number=1, status="Blocked", state="OPEN", blockers=(), all_blockers_listed=True):
    return sync.Item(
        item_id=f"PVTI_{number}",
        number=number,
        title=f"XX-{number}: task {number}",
        state=state,
        status=status,
        blockers=tuple(blockers),
        all_blockers_listed=all_blockers_listed,
    )


class FakeBoard:
    def __init__(self, items, fail_on=()):
        self._items = items
        self._fail_on = set(fail_on)
        self.writes = []

    def items(self):
        return self._items

    def set_status(self, board_item, status):
        if board_item.number in self._fail_on:
            raise sync.BoardError(f"write refused for #{board_item.number}")
        self.writes.append((board_item.number, status))


# --- desired_status -------------------------------------------------------------------------


def test_every_blocker_closed_as_completed_means_ready():
    task = item(blockers=[blocker(2, "CLOSED", "COMPLETED"), blocker(3, "CLOSED", "COMPLETED")])
    assert sync.desired_status(task) == "Ready"


def test_one_open_blocker_means_blocked():
    task = item(status="Ready", blockers=[blocker(2, "CLOSED", "COMPLETED"), blocker(3)])
    assert sync.desired_status(task) == "Blocked"


def test_a_reopened_blocker_means_blocked():
    task = item(status="Ready", blockers=[blocker(2, "OPEN", "REOPENED")])
    assert sync.desired_status(task) == "Blocked"


@pytest.mark.parametrize("reason", ["NOT_PLANNED", "DUPLICATE", None])
def test_a_blocker_closed_other_than_completed_keeps_the_task_blocked(reason):
    task = item(blockers=[blocker(2, "CLOSED", reason)])
    assert sync.desired_status(task) == "Blocked"


def test_no_blockers_means_ready():
    assert sync.desired_status(item(status="Blocked", blockers=[])) == "Ready"


@pytest.mark.parametrize("status", ["In progress", "Done", None])
def test_statuses_other_than_blocked_and_ready_are_left_alone(status):
    task = item(status=status, blockers=[blocker(2, "CLOSED", "COMPLETED")])
    assert sync.desired_status(task) is None


def test_closed_issues_are_left_alone():
    task = item(state="CLOSED", status="Blocked", blockers=[])
    assert sync.desired_status(task) is None


# --- run: what it writes ----------------------------------------------------------------------


def test_run_writes_only_items_whose_status_is_wrong():
    board = FakeBoard(
        [
            item(1, "Blocked", blockers=[blocker(9, "CLOSED", "COMPLETED")]),  # -> Ready
            item(2, "Ready", blockers=[blocker(9, "CLOSED", "COMPLETED")]),  # already right
            item(3, "Ready", blockers=[blocker(8)]),  # -> Blocked
            item(4, "In progress", blockers=[blocker(8)]),  # never touched
        ]
    )

    assert sync.run(board, dry_run=False) == 0
    assert board.writes == [(1, "Ready"), (3, "Blocked")]


def test_dry_run_writes_nothing_but_reports_the_changes(capsys):
    board = FakeBoard([item(1, "Blocked", blockers=[])])

    assert sync.run(board, dry_run=True) == 0

    assert board.writes == []
    out = capsys.readouterr().out
    assert "#1 XX-1: task 1: Blocked -> Ready" in out
    assert "dry run" in out.lower()


def test_an_item_with_more_blockers_than_were_listed_is_skipped_not_guessed(capsys):
    board = FakeBoard([item(1, "Blocked", blockers=[], all_blockers_listed=False)])

    assert sync.run(board, dry_run=False) == 0

    assert board.writes == []
    out = capsys.readouterr().out
    assert "#1" in out and "skipped" in out.lower()


def test_a_failed_write_is_reported_the_rest_still_run_and_the_exit_code_is_nonzero(capsys):
    board = FakeBoard(
        [item(1, "Blocked", blockers=[]), item(2, "Blocked", blockers=[])], fail_on={1}
    )

    assert sync.run(board, dry_run=False) == 1

    assert board.writes == [(2, "Ready")]
    out = capsys.readouterr().out
    assert "write refused for #1" in out
    assert "failed 1" in out.lower()


# --- run: what it prints ----------------------------------------------------------------------


def test_run_reports_scope_changes_and_a_summary(capsys):
    board = FakeBoard(
        [
            item(1, "Blocked", blockers=[]),
            item(2, "Ready", blockers=[]),
            item(3, "Done", state="CLOSED"),
        ]
    )

    sync.run(board, dry_run=False)

    out = capsys.readouterr().out
    assert "2 open issues in Blocked or Ready" in out
    assert "#1 XX-1: task 1: Blocked -> Ready" in out
    assert "changed 1, unchanged 1, skipped 0, failed 0" in out.lower()


def test_run_says_clearly_when_every_status_was_already_right(capsys):
    board = FakeBoard([item(1, "Ready", blockers=[]), item(2, "Blocked", blockers=[blocker(5)])])

    sync.run(board, dry_run=False)

    assert "no changes needed" in capsys.readouterr().out.lower()


def test_run_says_clearly_when_there_was_nothing_to_check(capsys):
    board = FakeBoard([item(1, "In progress"), item(2, "Done", state="CLOSED")])

    assert sync.run(board, dry_run=False) == 0

    assert "nothing to check" in capsys.readouterr().out.lower()


# --- GitHubBoard: reading and writing through GraphQL -----------------------------------------


def _page(nodes, has_next=False, cursor=None):
    return {
        "data": {
            "user": {
                "projectV2": {
                    "id": "PVT_project",
                    "field": {
                        "id": "PVTSSF_status",
                        "options": [
                            {"id": "opt-blocked", "name": "Blocked"},
                            {"id": "opt-ready", "name": "Ready"},
                            {"id": "opt-progress", "name": "In progress"},
                            {"id": "opt-done", "name": "Done"},
                        ],
                    },
                    "items": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                        "nodes": nodes,
                    },
                }
            }
        }
    }


def _issue_node(item_id, number, status, blockers=(), total=None):
    return {
        "id": item_id,
        "fieldValueByName": {"name": status} if status else None,
        "content": {
            "__typename": "Issue",
            "number": number,
            "title": f"XX-{number}: task",
            "state": "OPEN",
            "stateReason": None,
            "blockedBy": {
                "totalCount": len(blockers) if total is None else total,
                "nodes": [{"number": n, "state": s, "stateReason": r} for n, s, r in blockers],
            },
        },
    }


class FakeGraphQL:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, query, variables):
        self.calls.append((query, variables))
        return self._responses.pop(0)


def test_github_board_reads_every_page_and_maps_issues_to_items():
    graphql = FakeGraphQL(
        [
            _page(
                [_issue_node("I1", 10, "Blocked", [(4, "CLOSED", "COMPLETED")])],
                has_next=True,
                cursor="c1",
            ),
            _page([_issue_node("I2", 11, "Ready", [(5, "OPEN", None)])]),
        ]
    )
    board = sync.GitHubBoard("someone", 1, graphql)

    items = board.items()

    assert [i.number for i in items] == [10, 11]
    assert items[0] == sync.Item(
        item_id="I1",
        number=10,
        title="XX-10: task",
        state="OPEN",
        status="Blocked",
        blockers=(sync.Blocker(4, "CLOSED", "COMPLETED"),),
        all_blockers_listed=True,
    )
    assert graphql.calls[1][1]["cursor"] == "c1"


def test_github_board_skips_draft_items_and_pull_requests():
    graphql = FakeGraphQL(
        [
            _page(
                [
                    {"id": "D1", "fieldValueByName": None, "content": {"__typename": "DraftIssue"}},
                    {
                        "id": "P1",
                        "fieldValueByName": None,
                        "content": {"__typename": "PullRequest"},
                    },
                    _issue_node("I1", 10, None),
                ]
            )
        ]
    )

    items = sync.GitHubBoard("someone", 1, graphql).items()

    assert [(i.number, i.status) for i in items] == [(10, None)]


def test_github_board_flags_an_issue_whose_blockers_did_not_all_fit_in_the_page():
    graphql = FakeGraphQL(
        [_page([_issue_node("I1", 10, "Blocked", [(4, "OPEN", None)], total=150)])]
    )

    (only,) = sync.GitHubBoard("someone", 1, graphql).items()

    assert only.all_blockers_listed is False


def test_github_board_sets_status_with_the_matching_option_id():
    graphql = FakeGraphQL([_page([_issue_node("I1", 10, "Blocked")]), {"data": {}}])
    board = sync.GitHubBoard("someone", 1, graphql)
    (target,) = board.items()

    board.set_status(target, "Ready")

    _, variables = graphql.calls[-1]
    assert variables == {
        "project": "PVT_project",
        "item": "I1",
        "field": "PVTSSF_status",
        "option": "opt-ready",
    }


def test_github_board_raises_when_the_project_does_not_exist():
    graphql = FakeGraphQL([{"data": {"user": {"projectV2": None}}}])

    with pytest.raises(sync.BoardError, match="project 7"):
        sync.GitHubBoard("someone", 7, graphql).items()
