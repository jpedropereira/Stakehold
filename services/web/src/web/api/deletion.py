"""Deleting a transaction or an account: the no-JS confirmation pages and the POSTs that
delete.

On its own router, like overview.py, because routes.py is close to the repo's 64KB file limit.
It renders and redirects through routes.py's helpers, so a cookie set earlier in the request
(the CSRF cookie, a renewed session) still reaches the response.

Each row of the transaction log, and each account on the accounts list, links to its
confirmation page. With JS, static/confirm_dialog.js opens a dialog on the list instead, and
the dialog's form posts the same fields as the page's form, so both reach the same POST. A
delete can't be undone, so the confirmation always says what is about to go: which
transaction, or which account and how many transactions with it.
"""

import asyncio
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import (
    _account_header_context,
    _account_page_context,
    _check_csrf,
    _redirect,
    _render,
    _render_account_not_found,
    _submit_portfolio_form,
    _with_rebuild_statuses,
)
from web.auth_client.base import AuthUser
from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import (
    AccountNotFound,
    PortfolioClientError,
    TransactionNotFound,
)
from web.settings import Settings
from web.transaction_counts import load_transaction_counts

logger = logging.getLogger(__name__)

router = APIRouter()

_TRANSACTION_UNAVAILABLE = (
    "Can't reach the portfolio service right now — the transaction can't be shown."
)


def _log_url(account_label: str, deleted: int) -> str:
    return f"/transactions?account={quote(account_label)}&deleted={deleted}"


async def _transaction_delete_context(
    portfolio_client: PortfolioClient, user_id: str, account_label: str, ref: str
) -> dict:
    """What transaction_delete.html shows: the account header and the transaction itself.

    portfolio has no single-transaction read, so the account's log is fetched and searched.
    `transaction` is None both when the row is gone and when the log couldn't be loaded;
    `load_error` tells the two apart, so an outage never reads as "already deleted".
    """
    header, transactions = await asyncio.gather(
        _account_header_context(portfolio_client, user_id, account_label),
        portfolio_client.list_transactions(user_id=user_id, account_label=account_label),
        return_exceptions=True,
    )
    if isinstance(header, BaseException):
        raise header
    load_error = None
    if isinstance(transactions, AccountNotFound):
        header["account_known"] = False
        transactions = []
    elif isinstance(transactions, PortfolioClientError):
        logger.exception(
            "portfolio service unavailable listing transactions.", exc_info=transactions
        )
        transactions, load_error = [], _TRANSACTION_UNAVAILABLE
    elif isinstance(transactions, BaseException):
        raise transactions
    transaction = next((t for t in transactions if t.broker_transaction_ref == ref), None)
    return {
        "account_label": account_label,
        "ref": ref,
        "transaction": transaction,
        "load_error": load_error,
        # The switcher submits a plain GET back to this page, which has no ref to carry over.
        "hide_account_switcher": True,
        **_account_page_context("transactions"),
        **header,
    }


@router.get("/transactions/delete")
async def transaction_delete_confirm(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    ref: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """The confirmation page a Delete link opens without JS."""
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    context = await _transaction_delete_context(portfolio_client, user.user_id, account, ref)
    if context["account_known"] is False:
        return _render_account_not_found(
            request, response, templates, user=user, csrf_token=csrf_token
        )
    if context["transaction"] is None and context["load_error"] is None:
        # Deleted already, most likely from another tab: the log says so.
        return _redirect(request, response, _log_url(account, 0))
    return _render(
        request,
        response,
        templates,
        "transaction_delete.html",
        {"user": user, "csrf_token": csrf_token, "error": None, **context},
    )


@router.post("/transactions/delete")
async def transaction_delete_submit(
    request: Request,
    response: Response,
    # default="", not Form(...) -- see routes.accounts_submit.
    account: str = Form(default=""),
    ref: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """Posted by both the dialog and the confirmation page. On success it goes back to the log
    with the outcome in the query string; an error re-renders the confirmation page, so the
    visitor can retry from it whichever of the two they started on."""

    async def load_page_context() -> dict:
        return await _transaction_delete_context(portfolio_client, user.user_id, account, ref)

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "transaction_delete.html",
        "delete_transaction",
        csrf_token,
        user=user,
        extra_context_loader=load_page_context,
    )
    if csrf_error is not None:
        return csrf_error

    async def delete():
        # A row that's already gone isn't an error worth a page of its own: the visitor wanted
        # it gone, and the log says nothing was deleted.
        try:
            return await portfolio_client.delete_transaction(
                user_id=user.user_id, account_label=account, broker_transaction_ref=ref
            )
        except TransactionNotFound:
            return None

    def on_success(result) -> Response:
        if result is None:
            return _redirect(request, response, _log_url(account, 0))
        return _redirect(request, response, _with_rebuild_statuses(_log_url(account, 1), result))

    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="transaction_delete.html",
        action_name="delete_transaction",
        call=delete,
        error_messages={},
        on_success=on_success,
        user=user,
        extra_context_loader=load_page_context,
    )


# --- accounts ---------------------------------------------------------------------------


async def _account_delete_context(
    portfolio_client: PortfolioClient, user_id: str, account_label: str
) -> dict:
    """What account_delete.html shows: the account header and how many transactions go with the
    account (None when they couldn't be counted, which the warning words vaguely rather than
    as zero). The delete is still offered without a count: the account itself is known."""
    header, counts = await asyncio.gather(
        _account_header_context(portfolio_client, user_id, account_label),
        load_transaction_counts(portfolio_client, user_id, [account_label]),
    )
    return {
        "account_label": account_label,
        "transaction_count": counts[account_label],
        "hide_account_switcher": True,
        **_account_page_context(None),
        **header,
    }


@router.get("/accounts/delete")
async def account_delete_confirm(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """The confirmation page an account's Delete link opens without JS."""
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    context = await _account_delete_context(portfolio_client, user.user_id, account)
    if context["account_known"] is False:
        return _render_account_not_found(
            request, response, templates, user=user, csrf_token=csrf_token
        )
    return _render(
        request,
        response,
        templates,
        "account_delete.html",
        {"user": user, "csrf_token": csrf_token, "error": None, **context},
    )


@router.post("/accounts/delete")
async def account_delete_submit(
    request: Request,
    response: Response,
    # default="", not Form(...) -- see routes.accounts_submit.
    account: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """Posted by both the dialog and the confirmation page; the same shape as the transaction
    delete above. Back on the accounts list, the banner says how many transactions went with
    the account. The label isn't echoed back through the query string, so a crafted link can't
    put words in the banner."""

    async def load_page_context() -> dict:
        return await _account_delete_context(portfolio_client, user.user_id, account)

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "account_delete.html",
        "delete_account",
        csrf_token,
        user=user,
        extra_context_loader=load_page_context,
    )
    if csrf_error is not None:
        return csrf_error

    async def delete():
        try:
            return await portfolio_client.delete_account(
                user_id=user.user_id, account_label=account
            )
        except AccountNotFound:
            return None

    def on_success(result) -> Response:
        if result is None:
            return _redirect(request, response, "/accounts?deleted=0")
        return _redirect(
            request,
            response,
            f"/accounts?deleted=1&transactions={result.transactions_deleted}",
        )

    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="account_delete.html",
        action_name="delete_account",
        call=delete,
        error_messages={},
        on_success=on_success,
        user=user,
        extra_context_loader=load_page_context,
    )
