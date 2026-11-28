"""Keep the Stakehold Roadmap board's Blocked/Ready statuses in line with Blocked-by links.

`.claude/rules/tasks.md` defines the two statuses: an open issue is `Ready` when every issue
that blocks it is closed as completed, and `Blocked` otherwise. The board can't derive that on
its own (its built-in workflows only react to the item itself), so this script recomputes it
for every open issue currently in `Blocked` or `Ready`, and writes only the ones that are wrong.

It recomputes the whole board rather than following the dependents of whichever issue just
closed. That way one script handles a reopened blocker, a blocker closed as not planned, a
Blocked-by link added after the fact (GitHub Actions has no trigger for those), and any drift
left by an earlier missed run.

Items in `In progress` or `Done`, items with no status, closed issues, drafts and pull requests
are never touched.

Run by .github/workflows/board-status.yml. Locally (needs `gh` signed in with the `project`
scope; `--dry-run` prints what would change without writing):

    python scripts/sync_board_status.py --owner jpedropereira --project 1 --dry-run

Standard library only, so the workflow doesn't have to install the workspace to run it.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

BLOCKED = "Blocked"
READY = "Ready"
MANAGED_STATUSES = (BLOCKED, READY)

# Blockers fetched per issue. An issue with more is skipped rather than guessed at.
_BLOCKERS_PER_ISSUE = 100


class BoardError(Exception):
    """Reading or writing the board failed."""


@dataclass(frozen=True)
class Blocker:
    number: int
    state: str  # OPEN | CLOSED
    state_reason: str | None  # COMPLETED | NOT_PLANNED | DUPLICATE | REOPENED | None


@dataclass(frozen=True)
class Item:
    item_id: str
    number: int
    title: str
    state: str
    status: str | None
    blockers: tuple[Blocker, ...]
    all_blockers_listed: bool


class Board(Protocol):
    def items(self) -> list[Item]: ...

    def set_status(self, item: Item, status: str) -> None: ...


def is_managed(item: Item) -> bool:
    """Whether this script owns the item's status."""
    return item.state == "OPEN" and item.status in MANAGED_STATUSES


def desired_status(item: Item) -> str | None:
    """The status the item should have, or None if this script doesn't manage it."""
    if not is_managed(item):
        return None
    done = all(b.state == "CLOSED" and b.state_reason == "COMPLETED" for b in item.blockers)
    return READY if done else BLOCKED


def _label(item: Item) -> str:
    return f"#{item.number} {item.title}"


def run(board: Board, dry_run: bool) -> int:
    """Recompute and apply statuses. Returns the process exit code."""
    managed = [i for i in board.items() if is_managed(i)]
    if not managed:
        print("Nothing to check: no open issues are in Blocked or Ready.")
        return 0

    mode = " (dry run: nothing will be written)" if dry_run else ""
    print(f"Checking {len(managed)} open issues in Blocked or Ready{mode}.")

    changed = unchanged = skipped = failed = 0
    for item in managed:
        if not item.all_blockers_listed:
            print(
                f"{_label(item)}: skipped, it has more than {_BLOCKERS_PER_ISSUE} blockers "
                "and they couldn't all be read"
            )
            skipped += 1
            continue
        target = desired_status(item)
        if target == item.status:
            unchanged += 1
            continue
        line = f"{_label(item)}: {item.status} -> {target}"
        if dry_run:
            print(f"{line} (not written)")
            changed += 1
            continue
        try:
            board.set_status(item, target)
        except BoardError as error:
            print(f"{line} FAILED: {error}")
            failed += 1
            continue
        print(line)
        changed += 1

    if changed == 0 and skipped == 0 and failed == 0:
        print(f"No changes needed: all {unchanged} statuses are already correct.")
    verb = "Would change" if dry_run else "Changed"
    print(f"{verb} {changed}, unchanged {unchanged}, skipped {skipped}, failed {failed}.")
    return 1 if failed else 0


# --- GitHub -------------------------------------------------------------------------------------

GraphQL = Callable[[str, dict], dict]

_ITEMS_QUERY = """
query($login: String!, $number: Int!, $cursor: String) {
  user(login: $login) {
    projectV2(number: $number) {
      id
      field(name: "Status") {
        ... on ProjectV2SingleSelectField { id options { id name } }
      }
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            __typename
            ... on Issue {
              number
              title
              state
              stateReason
              blockedBy(first: %d) { totalCount nodes { number state stateReason } }
            }
          }
        }
      }
    }
  }
}
""" % (_BLOCKERS_PER_ISSUE,)

_SET_STATUS_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field,
    value: { singleSelectOptionId: $option }
  }) { projectV2Item { id } }
}
"""


def gh_graphql(query: str, variables: dict) -> dict:
    """Run a GraphQL request through the `gh` CLI, which handles auth (GH_TOKEN in CI)."""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        flag = "-F" if isinstance(value, int) else "-f"
        args += [flag, f"{key}={value}"]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise BoardError(result.stderr.strip() or f"gh exited with {result.returncode}")
    return json.loads(result.stdout)


class GitHubBoard:
    """A user-owned Projects v2 board, read and written through GraphQL."""

    def __init__(self, owner: str, number: int, graphql: GraphQL = gh_graphql):
        self._owner = owner
        self._number = number
        self._graphql = graphql
        self._project_id: str | None = None
        self._field_id: str | None = None
        self._options: dict[str, str] = {}

    def items(self) -> list[Item]:
        items: list[Item] = []
        cursor = None
        while True:
            response = self._graphql(
                _ITEMS_QUERY, {"login": self._owner, "number": self._number, "cursor": cursor}
            )
            project = ((response.get("data") or {}).get("user") or {}).get("projectV2")
            if project is None:
                raise BoardError(
                    f"project {self._number} of {self._owner} not found or not readable: "
                    f"{response.get('errors')}"
                )
            self._remember_status_field(project)
            page = project["items"]
            items.extend(_to_items(page["nodes"]))
            if not page["pageInfo"]["hasNextPage"]:
                return items
            cursor = page["pageInfo"]["endCursor"]

    def set_status(self, item: Item, status: str) -> None:
        if self._project_id is None:
            raise BoardError("set_status called before items()")
        if status not in self._options:
            raise BoardError(f"the board has no Status option named {status!r}")
        response = self._graphql(
            _SET_STATUS_MUTATION,
            {
                "project": self._project_id,
                "item": item.item_id,
                "field": self._field_id,
                "option": self._options[status],
            },
        )
        if response.get("errors"):
            raise BoardError(str(response["errors"]))

    def _remember_status_field(self, project: dict) -> None:
        field = project.get("field")
        if not field:
            raise BoardError(f"project {self._number} has no single-select Status field")
        self._project_id = project["id"]
        self._field_id = field["id"]
        self._options = {o["name"]: o["id"] for o in field["options"]}


def _to_items(nodes: Iterable[dict]) -> Iterable[Item]:
    for node in nodes:
        content = node.get("content") or {}
        if content.get("__typename") != "Issue":
            continue
        blocked_by = content["blockedBy"]
        blockers = tuple(
            Blocker(b["number"], b["state"], b["stateReason"]) for b in blocked_by["nodes"]
        )
        yield Item(
            item_id=node["id"],
            number=content["number"],
            title=content["title"],
            state=content["state"],
            status=(node.get("fieldValueByName") or {}).get("name"),
            blockers=blockers,
            all_blockers_listed=blocked_by["totalCount"] <= len(blockers),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner", required=True, help="user that owns the Projects board")
    parser.add_argument("--project", required=True, type=int, help="the board's number")
    parser.add_argument("--dry-run", action="store_true", help="print changes, write nothing")
    args = parser.parse_args(argv)
    try:
        return run(GitHubBoard(args.owner, args.project), dry_run=args.dry_run)
    except BoardError as error:
        print(f"Could not read board {args.project} of {args.owner}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
