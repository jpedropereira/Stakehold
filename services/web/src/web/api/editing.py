"""Editing a transaction: the edit page each row of the transaction log links to, and the POST
that saves the edit.

On its own router, like deletion.py, because routes.py is close to the repo's 64KB file limit.
It renders through routes.py's helpers and shares the add form's fields
(partials/_transaction_fields.html), its parsing and therefore its validation. portfolio
applies the same rules to an edit as to a new entry.

The reference is the transaction's key, so it's shown but can't be changed. An instrument left
as it was is sent back with the kind it was recorded with (an imported ISIN stays an ISIN). A
changed one is a ticker picked from the list, as on the add form.
"""

import asyncio
from datetime import timezone
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from web.api.csrf import ensure_csrf_cookie
from web.api.deletion import _transaction_page_context
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import (
    _FIELD_REQUIRED_FOR,
    _FIELD_TYPES,
    _MANUAL_IDENTIFIER_KIND,
    _SUGGESTED_CURRENCIES,
    _TRANSACTION_ERRORS,
    _TRANSACTION_TYPES,
    _check_csrf,
    _load_tickers,
    _parse_transaction_form,
    _redirect,
    _render,
    _render_account_not_found,
    _render_form_error,
    _submit_portfolio_form,
    _with_rebuild_statuses,
)
from web.auth_client.base import AuthUser
from web.portfolio_client.base import PortfolioClient, Transaction
from web.portfolio_client.errors import TransactionNotFound
from web.settings import Settings

router = APIRouter()

# The types whose amount portfolio derives from price x quantity + fees.
_DERIVED_AMOUNT_TYPES = ("BUY", "SELL")


def _log_url(account_label: str, edited: str) -> str:
    return f"/transactions?account={quote(account_label)}&edited={edited}"


def _plain(value: Decimal | None) -> str:
    """A stored figure as it would be typed: no trailing zeros, never an exponent."""
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def _values_from(txn: Transaction) -> dict:
    """The form's fields, prefilled from the stored row. A BUY/SELL's amount is left blank: kept,
    it would stop matching price x quantity + fees as soon as either changed, and portfolio
    would refuse the edit."""
    return {
        "transaction_type": txn.transaction_type,
        # datetime-local's own format, in UTC, as the field is labelled.
        "trade_datetime": txn.trade_datetime.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        "native_amount": (
            "" if txn.transaction_type in _DERIVED_AMOUNT_TYPES else _plain(txn.native_amount)
        ),
        "native_currency": txn.native_currency,
        "fx_rate": _plain(txn.fx_rate),
        "fees_native": _plain(txn.fees_native),
        "instrument_identifier": txn.instrument_identifier or "",
        "instrument_identifier_kind": txn.instrument_identifier_kind,
        "quantity": _plain(txn.quantity),
        "price": _plain(txn.price),
    }


async def _form_context(portfolio_client: PortfolioClient) -> dict:
    tickers, tickers_notice = await _load_tickers(portfolio_client)
    return {
        "transaction_types": _TRANSACTION_TYPES,
        "currencies": _SUGGESTED_CURRENCIES,
        "field_types": _FIELD_TYPES,
        "field_required_for": _FIELD_REQUIRED_FOR,
        "tickers": tickers,
        "tickers_notice": tickers_notice,
        "reference_editable": False,
    }


@router.get("/transactions/edit")
async def transaction_edit_form(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    ref: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """The edit page each Edit link on the log opens, prefilled from the row."""
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    context, form = await asyncio.gather(
        _transaction_page_context(portfolio_client, user.user_id, account, ref),
        _form_context(portfolio_client),
    )
    if context["account_known"] is False:
        return _render_account_not_found(
            request, response, templates, user=user, csrf_token=csrf_token
        )
    transaction = context["transaction"]
    if transaction is None and context["load_error"] is None:
        # Deleted already, most likely from another tab: the log says so.
        return _redirect(request, response, _log_url(account, "gone"))
    values = _values_from(transaction) if transaction is not None else {}
    return _render(
        request,
        response,
        templates,
        "transaction_edit.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "error": None,
            "reference": ref,
            "values": values,
            "original_instrument_identifier": values.get("instrument_identifier", ""),
            "original_instrument_identifier_kind": values.get("instrument_identifier_kind") or "",
            **context,
            **form,
        },
    )


def _instrument(typed: str, original: str, original_kind: str) -> tuple[str | None, str | None]:
    """The instrument to send, and its kind: the one it was recorded with when it's unchanged,
    otherwise a ticker, as the add form sends."""
    identifier = typed.strip() or None
    if identifier is None:
        return None, None
    if identifier == original.strip() and original_kind:
        return identifier, original_kind
    return identifier, _MANUAL_IDENTIFIER_KIND


@router.post("/transactions/edit")
async def transaction_edit_submit(
    request: Request,
    response: Response,
    # default="", not Form(...) -- see routes.accounts_submit.
    account: str = Form(default=""),
    ref: str = Form(default=""),
    transaction_type: str = Form(default=""),
    trade_datetime: str = Form(default=""),
    native_amount: str = Form(default=""),
    native_currency: str = Form(default=""),
    instrument_identifier: str = Form(default=""),
    original_instrument_identifier: str = Form(default=""),
    original_instrument_identifier_kind: str = Form(default=""),
    quantity: str = Form(default=""),
    price: str = Form(default=""),
    fx_rate: str = Form(default=""),
    fees_native: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """On success it goes back to the log with the outcome in the query string. An error
    re-renders the edit page with what was typed, so nothing has to be entered again."""
    page_context = {
        "user": user,
        "reference": ref,
        # What was typed, not the stored row: an error re-render shows the visitor's own edit.
        "values": {
            "transaction_type": transaction_type,
            "trade_datetime": trade_datetime,
            "native_amount": native_amount,
            "native_currency": native_currency,
            "fx_rate": fx_rate,
            "fees_native": fees_native,
            "instrument_identifier": instrument_identifier,
            "instrument_identifier_kind": original_instrument_identifier_kind or None,
            "quantity": quantity,
            "price": price,
        },
        "original_instrument_identifier": original_instrument_identifier,
        "original_instrument_identifier_kind": original_instrument_identifier_kind,
    }

    async def load_page_context() -> dict:
        # Only a full-page error re-render needs the header, the ticker list and the row.
        context, form = await asyncio.gather(
            _transaction_page_context(portfolio_client, user.user_id, account, ref),
            _form_context(portfolio_client),
        )
        # Keeps the form showing even if the row can't be re-read: what was typed is still
        # worth resubmitting.
        context.pop("transaction")
        context.pop("load_error")
        return {**context, **form}

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "transaction_edit.html",
        "edit_transaction",
        csrf_token,
        extra_context_loader=load_page_context,
        **page_context,
    )
    if csrf_error is not None:
        return csrf_error

    parsed = _parse_transaction_form(
        trade_datetime=trade_datetime,
        native_amount=native_amount,
        quantity=quantity,
        price=price,
        fx_rate=fx_rate,
        fees_native=fees_native,
    )
    if parsed is None:
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            "transaction_edit.html",
            "Enter a valid date/time and numeric fields.",
            status.HTTP_400_BAD_REQUEST,
            extra_context_loader=load_page_context,
            **page_context,
        )

    identifier, identifier_kind = _instrument(
        instrument_identifier, original_instrument_identifier, original_instrument_identifier_kind
    )

    async def update():
        # A row deleted since the page opened isn't worth an error page: the log says so.
        try:
            return await portfolio_client.update_transaction(
                user_id=user.user_id,
                account_label=account,
                broker_transaction_ref=ref,
                transaction_type=transaction_type,
                native_currency=native_currency,
                instrument_identifier=identifier,
                instrument_identifier_kind=identifier_kind,
                **parsed,
            )
        except TransactionNotFound:
            return None

    def on_success(result) -> Response:
        if result is None:
            return _redirect(request, response, _log_url(account, "gone"))
        if not result.transactions_updated:
            return _redirect(request, response, _log_url(account, "0"))
        return _redirect(request, response, _with_rebuild_statuses(_log_url(account, "1"), result))

    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="transaction_edit.html",
        action_name="edit_transaction",
        call=update,
        error_messages=_TRANSACTION_ERRORS,
        on_success=on_success,
        extra_context_loader=load_page_context,
        **page_context,
    )
