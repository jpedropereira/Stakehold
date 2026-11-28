"""Runnable units for saved labs (LF-1): list, save, delete. Kept out of jobs.py, which is close
to the 64 KB limit, as risk_jobs.py is; each is the same shape as a jobs.py job (settings, its
own connection, one flow, a payload of builtins), and api/routes.py and cli.py call them the same
way. Domain errors propagate, mapped at each entry point (see jobs.py's module docstring).
"""

import logging
from collections.abc import Iterable
from decimal import Decimal
from uuid import UUID

from portfolio.db.connection import user_connection
from portfolio.run_result import build_run_result
from portfolio.saved_labs import flows
from portfolio.saved_labs.base import LabAsset, SavedLab
from portfolio.settings import Settings, load_settings
from shared.logging_config import configure_logging

logger = logging.getLogger(__name__)


def _weight_text(weight: Decimal) -> str:
    """`60` for 60.000000, `33.3333` for 33.333300: the column's padding dropped, never
    scientific notation, so the form shows the weight about as it was typed."""
    text = format(weight, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _lab_to_dict(lab: SavedLab) -> dict:
    return {
        "name": lab.name,
        "assets": [{"ticker": a.ticker, "weight": _weight_text(a.weight_pct)} for a in lab.assets],
        "created_at": lab.created_at.isoformat(),
        "updated_at": lab.updated_at.isoformat(),
    }


def run_list_saved_labs(*, user_id: UUID, settings: Settings | None = None) -> dict:
    """This user's labs by name. Saves nothing; no labs is `nothing_to_do`."""
    configure_logging()
    settings = settings or load_settings()
    conn = user_connection(settings, user_id)
    try:
        logger.info("saved labs list: listing labs for user %s.", user_id)
        labs = flows.list_labs(conn, user_id=user_id)
    finally:
        conn.close()

    logger.info("saved labs list: done, %d lab(s) found.", len(labs))
    result = build_run_result(units_total=len(labs), rows_saved=0, failed=0, labs_listed=len(labs))
    result["labs"] = [_lab_to_dict(lab) for lab in labs]
    return result


def run_save_lab(
    *,
    user_id: UUID,
    name: str,
    assets: Iterable[tuple[str, Decimal]],
    replace: bool,
    settings: Settings | None = None,
) -> dict:
    """Save `assets` (ticker, weight %) as this user's lab `name`; see flows.save_lab for what
    `replace` does. `rows_saved` counts the asset rows written."""
    configure_logging()
    settings = settings or load_settings()
    lab_assets = [LabAsset(ticker, weight) for ticker, weight in assets]
    conn = user_connection(settings, user_id)
    try:
        logger.info(
            "save lab: saving %r with %d asset(s) for user %s%s.",
            name,
            len(lab_assets),
            user_id,
            ", replacing a lab of that name" if replace else "",
        )
        outcome = flows.save_lab(
            conn, user_id=user_id, name=name, assets=lab_assets, replace=replace
        )
    finally:
        conn.close()

    logger.info(
        "save lab: done, lab %r %s with %d asset(s).",
        outcome.name,
        "created" if outcome.created else "replaced",
        outcome.assets,
    )
    result = build_run_result(
        units_total=1,
        rows_saved=outcome.assets,
        failed=0,
        labs_created=int(outcome.created),
        labs_replaced=int(not outcome.created),
    )
    result["name"] = outcome.name
    return result


def run_delete_saved_lab(*, user_id: UUID, name: str, settings: Settings | None = None) -> dict:
    """Delete this user's lab `name`. SavedLabNotFound propagates."""
    configure_logging()
    settings = settings or load_settings()
    conn = user_connection(settings, user_id)
    try:
        logger.info("delete lab: deleting lab %r for user %s.", name, user_id)
        flows.delete_lab(conn, user_id=user_id, name=name)
    finally:
        conn.close()

    logger.info("delete lab: done, lab %r deleted.", name)
    return build_run_result(units_total=1, rows_saved=0, failed=0, labs_deleted=1)
