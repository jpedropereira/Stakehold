import logging
from datetime import UTC, datetime

from auth import service
from auth.api.cookies import COOKIE_NAME, clear_session_cookie, set_session_cookie
from auth.api.deps import (
    client_ip,
    get_db,
    get_login_throttle,
    get_settings,
    get_signup_throttle,
    require_internal_secret,
)
from auth.api.schemas import (
    DelegatedTokenResponse,
    LoginRequest,
    SignupRequest,
    UserResponse,
)
from auth.service import AuthenticatedUser
from auth.settings import Settings
from auth.signing import public_jwks
from fastapi import APIRouter, Depends, Request, Response, status

logger = logging.getLogger(__name__)

# /health and the JWKS carry no internal-secret requirement: the container healthcheck doesn't
# have the shared secret to send, and agent, which fetches the JWKS, deliberately holds no auth
# secret (a public key isn't one). Every other route requires it, via this router-level
# dependency.
router = APIRouter(dependencies=[Depends(require_internal_secret)])
public_router = APIRouter()

# Cheap enough to run on every healthcheck tick: to_regclass is a catalogue lookup, not a scan,
# and returns NULL rather than erroring for a table that doesn't exist.
_SCHEMA_PROBE = "SELECT to_regclass('public.users'), to_regclass('public.sessions')"

# Verifiers cache the JWKS and refetch on an unknown kid (PD-8), so this only bounds how long a
# cache that never sees an unknown kid holds on to a key. Short, since the key set is tiny.
_JWKS_MAX_AGE_SECONDS = 300


@public_router.get("/health")
def health(request: Request, response: Response) -> dict[str, str]:
    """Reports unhealthy unless the database is reachable *and* the schema has been applied.

    A liveness-only check would let the container report ready before `auth db init` has run,
    while every real endpoint 500s on `relation "users" does not exist`.
    """
    try:
        with request.app.state.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_SCHEMA_PROBE)
            row = cur.fetchone()
    except Exception:
        logger.exception("health: database unreachable.")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database_unreachable"}

    if row is None or any(table is None for table in row):
        logger.error("health: schema not applied -- run `auth db init`.")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "schema_missing"}

    return {"status": "ok"}


@public_router.get("/.well-known/jwks.json")
def jwks(response: Response, settings: Settings = Depends(get_settings)) -> dict:
    response.headers["Cache-Control"] = f"max-age={_JWKS_MAX_AGE_SECONDS}"
    return public_jwks(settings.signing_key)


def _user_response(user: AuthenticatedUser) -> UserResponse:
    return UserResponse(
        user_id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    conn=Depends(get_db),
    settings: Settings = Depends(get_settings),
    throttle=Depends(get_signup_throttle),
) -> UserResponse:
    now = datetime.now(UTC)
    session = service.signup(
        conn,
        email=body.email,
        display_name=body.display_name,
        password=body.password,
        client_ip=client_ip(request),
        now=now,
        ttl_days=settings.session_ttl_days,
        absolute_ttl_days=settings.session_absolute_ttl_days,
        throttle=throttle,
    )
    set_session_cookie(
        response,
        token=session.token,
        expires_at=session.expires_at,
        now=now,
        secure=settings.cookie_secure,
    )
    return _user_response(session.user)


@router.post("/login", response_model=UserResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    conn=Depends(get_db),
    settings: Settings = Depends(get_settings),
    throttle=Depends(get_login_throttle),
) -> UserResponse:
    now = datetime.now(UTC)
    session = service.login(
        conn,
        email=body.email,
        password=body.password,
        client_ip=client_ip(request),
        now=now,
        ttl_days=settings.session_ttl_days,
        absolute_ttl_days=settings.session_absolute_ttl_days,
        throttle=throttle,
    )
    set_session_cookie(
        response,
        token=session.token,
        expires_at=session.expires_at,
        now=now,
        secure=settings.cookie_secure,
    )
    return _user_response(session.user)


@router.post("/sessions/validate", response_model=UserResponse)
def validate(
    request: Request,
    response: Response,
    conn=Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise service.SessionInvalid()

    now = datetime.now(UTC)
    session = service.validate_session(
        conn, token=token, now=now, ttl_days=settings.session_ttl_days
    )
    set_session_cookie(
        response,
        token=session.token,
        expires_at=session.expires_at,
        now=now,
        secure=settings.cookie_secure,
    )
    return _user_response(session.user)


@router.post("/tokens/delegate", response_model=DelegatedTokenResponse)
def delegate(
    request: Request,
    response: Response,
    conn=Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DelegatedTokenResponse:
    """A short-lived, read-scoped token for `web` to hand to `agent` for one chat request.

    Sets no cookie, unlike /sessions/validate: web forwards auth's Set-Cookie to the browser on
    the session routes, and this response must give it nothing to forward.
    """
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise service.SessionInvalid()

    delegated = service.issue_delegated_token(
        conn,
        session_token=token,
        now=datetime.now(UTC),
        ttl_days=settings.session_ttl_days,
        signing_key=settings.signing_key,
        token_ttl_seconds=settings.delegated_token_ttl_seconds,
    )
    # A bearer credential: no proxy or client cache may keep it.
    response.headers["Cache-Control"] = "no-store"
    return DelegatedTokenResponse(access_token=delegated.token, expires_in=delegated.expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    conn=Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    # Idempotent: no cookie or an unrecognized token both end in a cleared cookie and a 204,
    # never an error -- logging out twice is not a caller mistake.
    token = request.cookies.get(COOKIE_NAME)
    if token is not None:
        service.logout(conn, token=token)
    clear_session_cookie(response, secure=settings.cookie_secure)
