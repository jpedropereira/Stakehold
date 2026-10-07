"""The one-line outcome of the rebuilds portfolio runs after a transaction write (UI-7).

portfolio reports `positions_rebuild` and `returns_rebuild` apart from the write's own status
(portfolio/jobs.py's _rebuild_after_write). The statuses reach the confirmation page through
the redirect's query string, so anything outside the known set is treated as absent, never
echoed. Pure, like formatting.py: the routes add the "Saved." / "Imported N." lead.
"""

_DONE = frozenset({"ok", "nothing_to_do"})
_NOT_RUN = frozenset({"skipped", "failed"})

_RETURNS_LATER = "Returns couldn't be updated now; they'll catch up on the next scheduled refresh."
_RETURNS_WAITING = "Some returns are waiting for prices."


def _returns_clause(returns: str | None) -> str | None:
    if returns in _DONE:
        return "Returns updated."
    if returns == "partial":
        return _RETURNS_WAITING
    if returns in _NOT_RUN:
        return _RETURNS_LATER
    return None


def rebuild_outcome(positions: str | None, returns: str | None) -> str:
    """What the rebuilds did, in plain words, or "" when there is nothing to say: no rebuild
    ran (`skipped`, so nothing was saved), or the positions status is missing or unknown.

    A failed positions rebuild never promises the scheduled refresh: that refresh recomputes
    returns only, so holdings catch up on the account's next write.
    """
    if positions == "failed":
        return "Holdings couldn't be updated now; they'll update after your next change."
    if positions in _DONE:
        if returns in _DONE:
            return "Holdings and returns updated."
        clause = _returns_clause(returns)
        return "Holdings updated." if clause is None else f"Holdings updated. {clause}"
    if positions == "partial":
        lead = "Some holdings are waiting for a ticker mapping."
        clause = _returns_clause(returns)
        return lead if clause is None else f"{lead} {clause}"
    return ""
