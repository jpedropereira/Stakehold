"""The portfolio API's routes: argument parsing plus a call into jobs.py, the same shape
cli.py has for every command. Nothing here touches SQL, pipeline.py, or a repository directly.

account_label is always a query parameter or a request-body field, never a path segment --
labels are user-chosen free text up to 100 chars and may contain '/', which no amount of
path-segment escaping survives cleanly through a proxy. user_id is likewise explicit, matching
cli.py's --user and docs/architecture.md's "user_id is trusted input" stance -- the internal
secret proves a legitimate internal caller, not a specific user.
"""

import logging
import os
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from portfolio import jobs
from portfolio.api.models import (
    AccountCreateRequest,
    AccountResponse,
    AccountWriteResponse,
    PositionResponse,
    RebuildPositionsResponse,
    RebuildRequest,
    RebuildReturnsResponse,
    ReturnResponse,
    TransactionCreateRequest,
    TransactionResponse,
    TransactionsWriteResponse,
    UnmappedInstrumentResponse,
)
from portfolio.api.security import get_settings, require_internal_secret
from portfolio.run_result import STATUS_OK
from portfolio.settings import Settings
from shared.db.connection import get_connection
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# Unauthenticated on purpose -- a liveness/readiness probe must work before any secret is
# provisioned, and before a caller could send one. Every other route depends on
# require_internal_secret via this router.
health_router = APIRouter()
router = APIRouter(dependencies=[Depends(require_internal_secret)])

# Cheap enough to run on every healthcheck tick: to_regclass is a catalogue lookup, not a scan,
# and returns NULL rather than erroring for a table that doesn't exist. Unqualified table names
# rely on the connecting role's own schema being first on its search_path -- see
# jobs._ensure_role_and_schema's docstring.
_SCHEMA_PROBE = (
    "SELECT to_regclass('accounts'), to_regclass('transactions'), "
    "to_regclass('position_snapshots'), to_regclass('return_snapshots')"
)

# Generous for a broker CSV/XLSX export, small enough to bound memory -- the whole file is
# streamed to disk in chunks, never held in memory at once, but the temp file itself is not
# unbounded.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@health_router.get("/health")
def health(request: Request, response: Response) -> dict[str, str]:
    """Reports unhealthy unless the database is reachable *and* the schema has been applied --
    a liveness-only check would let the container report ready before `portfolio db init` has
    run, while every real endpoint 500s on a missing relation. Matches auth's own /health."""
    settings = request.app.state.settings
    try:
        conn = get_connection(settings.database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_PROBE)
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        logger.exception("health: database unreachable.")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database_unreachable"}

    if row is None or any(table is None for table in row):
        logger.error("health: schema not applied -- run `portfolio db init`.")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "schema_missing"}

    return {"status": "ok"}


def _write_status_code(result: dict) -> int:
    """201 for a genuine write, 200 otherwise. Every write route uses this: for
    create/add/import, "otherwise" only ever means `nothing_to_do` -- those jobs can no longer
    return `partial` themselves, since jobs.py lets a domain error propagate instead of catching
    it (see docs/architecture.md's "HTTP API" section), and a domain error reaches this API as an
    exception, mapped to its own status code by app.py's handlers, not through this dict. For the
    two rebuild routes, "otherwise" can also mean a genuine `partial` -- a real half-succeeded
    run, still reported as 200 with the blocked/failed detail in the body, not as an error."""
    return status.HTTP_201_CREATED if result["status"] == STATUS_OK else status.HTTP_200_OK


async def _save_upload_to_tempfile(file: UploadFile) -> Path:
    """Streams an uploaded file to a temp path under _MAX_UPLOAD_BYTES, never holding the whole
    body in memory. The caller is responsible for deleting the returned path -- see the two
    import routes' `finally` blocks. Raises HTTPException(413) and cleans up its own partial
    file if the cap is exceeded; a caller-supplied server-side path is deliberately not offered
    as an alternative -- that would be an arbitrary-file-read primitive behind a shared secret.
    """
    suffix = Path(file.filename).suffix if file.filename else ""
    fd, raw_path = tempfile.mkstemp(suffix=suffix)
    path = Path(raw_path)
    try:
        written = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        "file exceeds the upload size limit",
                    )
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


# --- accounts ---


@router.get("/accounts", response_model=list[AccountResponse])
def list_accounts(
    user_id: UUID = Query(...), settings: Settings = Depends(get_settings)
) -> list[dict]:
    result = jobs.run_list_accounts(user_id=user_id, settings=settings)
    return result["accounts"]


@router.post("/accounts", response_model=AccountWriteResponse)
def create_account(
    body: AccountCreateRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    result = jobs.run_add_account(
        user_id=body.user_id,
        broker=body.broker,
        account_label=body.account_label,
        settings=settings,
    )
    response.status_code = _write_status_code(result)
    return result


@router.get("/brokers", response_model=list[str])
def list_brokers() -> list[str]:
    result = jobs.run_list_available_brokers()
    return result["brokers"]


@router.get("/instruments/unmapped", response_model=list[UnmappedInstrumentResponse])
def list_unmapped_instruments(
    user_id: UUID = Query(...), settings: Settings = Depends(get_settings)
) -> list[dict]:
    result = jobs.run_list_unmapped_instruments(user_id=user_id, settings=settings)
    return result["unmapped_instruments"]


# --- transactions ---


@router.get("/transactions", response_model=list[TransactionResponse])
def list_transactions(
    user_id: UUID = Query(...),
    account_label: str = Query(...),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    return jobs.get_transactions(user_id=user_id, account_label=account_label, settings=settings)


@router.post("/transactions", response_model=TransactionsWriteResponse)
def create_transaction(
    body: TransactionCreateRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    result = jobs.run_add_transaction(
        user_id=body.user_id,
        account_label=body.account_label,
        broker_transaction_ref=body.broker_transaction_ref,
        transaction_type=body.transaction_type,
        trade_datetime=body.trade_datetime,
        native_amount=body.native_amount,
        native_currency=body.native_currency,
        instrument_identifier=body.instrument_identifier,
        instrument_identifier_kind=body.instrument_identifier_kind,
        quantity=body.quantity,
        price=body.price,
        fx_rate=body.fx_rate,
        fees_native=body.fees_native,
        settings=settings,
    )
    response.status_code = _write_status_code(result)
    return result


async def _handle_import(
    job: Callable[..., dict],
    *,
    response: Response,
    user_id: UUID,
    account_label: str,
    file: UploadFile,
    settings: Settings,
) -> dict:
    """Shared body for the two file-upload routes below -- identical except which `jobs.py`
    function does the parsing. `job` runs off the event loop (see `run_in_threadpool`'s use
    below): both `run_import_transactions`/`run_import_cashflow` do blocking DB and, for
    cashflow, blocking HTTP I/O, and every other route in this file is a plain `def`, which
    FastAPI dispatches to a threadpool automatically -- these two need it done explicitly since
    `await _save_upload_to_tempfile` forces them to be `async def`."""
    tmp_path = await _save_upload_to_tempfile(file)
    try:
        result = await run_in_threadpool(
            job, user_id=user_id, account_label=account_label, file_path=tmp_path, settings=settings
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    response.status_code = _write_status_code(result)
    return result


@router.post("/transactions/import", response_model=TransactionsWriteResponse)
async def import_transactions(
    response: Response,
    user_id: UUID = Form(...),
    account_label: str = Form(...),
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict:
    return await _handle_import(
        jobs.run_import_transactions,
        response=response,
        user_id=user_id,
        account_label=account_label,
        file=file,
        settings=settings,
    )


@router.post("/cashflows/import", response_model=TransactionsWriteResponse)
async def import_cashflow(
    response: Response,
    user_id: UUID = Form(...),
    account_label: str = Form(...),
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict:
    return await _handle_import(
        jobs.run_import_cashflow,
        response=response,
        user_id=user_id,
        account_label=account_label,
        file=file,
        settings=settings,
    )


# --- positions ---


@router.get("/positions", response_model=list[PositionResponse])
def list_positions(
    user_id: UUID = Query(...),
    account_label: str = Query(...),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    return jobs.get_positions(user_id=user_id, account_label=account_label, settings=settings)


@router.post("/positions/rebuild", response_model=RebuildPositionsResponse)
def rebuild_positions(
    body: RebuildRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    result = jobs.run_rebuild_positions(
        user_id=body.user_id, account_label=body.account_label, settings=settings
    )
    response.status_code = _write_status_code(result)
    return result


# --- returns ---


@router.get("/returns", response_model=list[ReturnResponse])
def list_returns(
    user_id: UUID = Query(...),
    account_label: str = Query(...),
    ticker: str | None = Query(default=None),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    return jobs.get_returns(
        user_id=user_id,
        account_label=account_label,
        canonical_ticker=ticker,
        start=start,
        end=end,
        settings=settings,
    )


@router.get("/returns/latest", response_model=list[ReturnResponse])
def list_latest_returns(
    user_id: UUID = Query(...),
    account_label: str = Query(...),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    return jobs.get_latest_returns(user_id=user_id, account_label=account_label, settings=settings)


@router.post("/returns/rebuild", response_model=RebuildReturnsResponse)
def rebuild_returns(
    body: RebuildRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    result = jobs.run_rebuild_returns(
        user_id=body.user_id, account_label=body.account_label, settings=settings
    )
    response.status_code = _write_status_code(result)
    return result
