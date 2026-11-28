"""Saving and deleting a lab (LF-1): POST /labs/saved, and the delete's no-JS confirmation page
and POST. Listing and opening a lab happen on /labs itself (labs.py): opening one is only a link
to its mix in the query string.

A save posts the mix the page shows, as the mix form would send it, plus the view choices to
come back to. A taken name isn't overwritten: portfolio answers 409, and labs_save.html asks
whether to replace that lab's mix, posting the same fields with `replace`. Both a save and a
delete redirect back to /labs with a status flag, never a name (saved_labs_view.py).

`back`, on a delete, is the /labs query to return to. Only ever used as a query string after
"/labs?", re-encoded from its parsed pairs, so it can't point anywhere else.

On its own router, like deletion.py, because routes.py is close to the repo's 64KB file limit;
it renders and redirects through routes.py's helpers, so a cookie set earlier in the request
still reaches the response.
"""

import logging
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import _check_csrf, _redirect, _render
from web.auth_client.base import AuthUser
from web.labs_mix import MixRow, canonical_query, check_mix, parse_mix, parse_weight
from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import (
    InvalidPortfolioRequest,
    PortfolioClientError,
    SavedLabNameTaken,
    SavedLabNotFound,
)
from web.saved_labs_view import back_query, mix_summary
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

_SAVE_LEAD = "Couldn't save the lab."
_DELETE_LEAD = "Couldn't delete."
_SAVE_UNAVAILABLE = "Can't reach the portfolio service right now, so the lab wasn't saved."
_DELETE_UNAVAILABLE = "Can't reach the portfolio service right now, so the lab wasn't deleted."
_VIEW_KEYS = ("period", "risk_window", "benchmark")


def _without_form_keys(context: dict) -> dict:
    """The page's context for _check_csrf, which sets the token, the error and its lead itself."""
    return {k: v for k, v in context.items() if k not in ("csrf_token", "error", "error_lead")}


def _labs_url(query: str, *flag: tuple[str, str]) -> str:
    extra = urlencode(list(flag))
    return "/labs?" + "&".join(part for part in (query, extra) if part)


# --- save -------------------------------------------------------------------------------------


def _save_context(user, csrf_token, name, rows, kept, **extra) -> dict:
    mix_query = canonical_query(rows, ()) if rows else ""
    return {
        "user": user,
        "csrf_token": csrf_token,
        "name": name,
        "rows": rows,
        "kept": kept,
        "summary": " · ".join(f"{row.ticker} {row.weight}%" for row in rows),
        "back_url": _labs_url("&".join(p for p in (mix_query, urlencode(kept)) if p)),
        "conflict": False,
        "error": None,
        "error_lead": _SAVE_LEAD,
        "name_error": False,
        **extra,
    }


@router.post("/labs/saved")
async def save_lab(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """Posted by the save form on /labs and by labs_save.html's Replace. The mix's tickers were
    checked against the tracked list when /labs showed it, so only its shape is checked again
    here; portfolio checks the rest."""
    items = list((await request.form()).multi_items())
    fields = dict(items)
    name = fields.get("name", "")
    replace = fields.get("replace") == "1"
    form = parse_mix([(k, v) for k, v in items if k in ("ticker", "weight")] or [("ticker", "")])
    rows: tuple[MixRow, ...] = form.rows
    kept = [(k, fields[k]) for k in _VIEW_KEYS if fields.get(k)]

    def render(status_code: int, **extra) -> Response:
        token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
        context = _save_context(user, token, name, rows, kept, **extra)
        return _render(
            request, response, templates, "labs_save.html", context, status_code=status_code
        )

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "labs_save.html",
        "save_lab",
        fields.get("csrf_token", ""),
        **_without_form_keys(_save_context(user, None, name, rows, kept)),
    )
    if csrf_error is not None:
        return csrf_error

    checked = check_mix(rows, None)
    if not rows or not checked.valid:
        message = (
            "Add an asset to the mix before saving it."
            if not rows
            else "Fix the marked rows in the mix before saving it."
        )
        logger.info("save_lab: refused, %s", message)
        return render(status.HTTP_422_UNPROCESSABLE_CONTENT, error=message)

    assets = [(row.ticker, parse_weight(row.weight)) for row in rows]
    logger.info(
        "save_lab: saving %d asset(s) as %r%s.", len(assets), name, " (replace)" if replace else ""
    )
    try:
        result = await portfolio_client.save_lab(
            user_id=user.user_id, name=name, assets=assets, replace=replace
        )
    except SavedLabNameTaken:
        logger.info("save_lab: the name %r is taken, asking before replacing.", name)
        # 200, not 409: the page is a question, like a delete's confirmation page, not an error.
        return render(status.HTTP_200_OK, conflict=True)
    except InvalidPortfolioRequest as exc:
        logger.info("save_lab: portfolio refused the lab: %s", exc)
        return render(status.HTTP_422_UNPROCESSABLE_CONTENT, error=str(exc), name_error=True)
    except PortfolioClientError:
        logger.exception("save_lab: portfolio service unavailable.")
        return render(status.HTTP_503_SERVICE_UNAVAILABLE, error=_SAVE_UNAVAILABLE)

    logger.info(
        "save_lab: done, lab %r %s with %d asset(s).",
        result.name,
        "created" if result.created else "replaced",
        result.assets_saved,
    )
    query = "&".join(p for p in (canonical_query(rows, ()), urlencode(kept)) if p)
    return _redirect(request, response, _labs_url(query, ("saved", "1")))


# --- delete -----------------------------------------------------------------------------------


def _clean_back(back: str) -> str:
    return back_query(parse_qsl(back, keep_blank_values=True))


def _delete_context(user, csrf_token, name, back, summary=None, **extra) -> dict:
    return {
        "user": user,
        "csrf_token": csrf_token,
        "name": name,
        "back": back,
        "back_url": _labs_url(back),
        "summary": summary,
        "gone": False,
        "error": None,
        "error_lead": _DELETE_LEAD,
        **extra,
    }


@router.get("/labs/saved/delete")
async def saved_lab_delete_confirm(
    request: Request,
    response: Response,
    name: str = Query(..., min_length=1),
    back: str = Query(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """The confirmation page a saved lab's Delete link opens without JS. It names the lab's mix
    when the list loads; a lab that isn't in it is a 404. An outage still confirms, by name only,
    so it never reads as "already deleted"."""
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    back = _clean_back(back)
    summary, status_code, gone = None, status.HTTP_200_OK, False
    try:
        labs = await portfolio_client.list_saved_labs(user_id=user.user_id)
    except PortfolioClientError:
        logger.exception(
            "saved_lab_delete: portfolio unavailable listing labs, confirming by name."
        )
    else:
        lab = next((lab for lab in labs if lab.name == name), None)
        if lab is None:
            logger.info("saved_lab_delete: no lab called %r to confirm.", name)
            status_code, gone = status.HTTP_404_NOT_FOUND, True
        else:
            summary = f"{lab.name} — {mix_summary(lab)}"
    return _render(
        request,
        response,
        templates,
        "labs_saved_delete.html",
        _delete_context(user, csrf_token, name, back, summary, gone=gone),
        status_code=status_code,
    )


@router.post("/labs/saved/delete")
async def saved_lab_delete_submit(
    request: Request,
    response: Response,
    # default="", not Form(...) -- see routes.accounts_submit.
    name: str = Form(default=""),
    back: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """Posted by the dialog on /labs and by the confirmation page. Back on /labs, the banner
    says whether a lab went."""
    back = _clean_back(back)
    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "labs_saved_delete.html",
        "delete_saved_lab",
        csrf_token,
        **_without_form_keys(_delete_context(user, None, name, back)),
    )
    if csrf_error is not None:
        return csrf_error

    logger.info("delete_saved_lab: deleting lab %r.", name)
    try:
        await portfolio_client.delete_saved_lab(user_id=user.user_id, name=name)
    except SavedLabNotFound:
        logger.info("delete_saved_lab: no lab called %r, nothing deleted.", name)
        return _redirect(request, response, _labs_url(back, ("lab_deleted", "0")))
    except PortfolioClientError:
        logger.exception("delete_saved_lab: portfolio service unavailable.")
        token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
        return _render(
            request,
            response,
            templates,
            "labs_saved_delete.html",
            _delete_context(user, token, name, back, error=_DELETE_UNAVAILABLE),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    logger.info("delete_saved_lab: done, lab %r deleted.", name)
    return _redirect(request, response, _labs_url(back, ("lab_deleted", "1")))
