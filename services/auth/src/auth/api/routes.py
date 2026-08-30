from datetime import UTC, datetime

from auth import service
from auth.api.cookies import COOKIE_NAME, clear_session_cookie, set_session_cookie
from auth.api.deps import get_db, get_settings, get_throttle, require_internal_secret
from auth.api.schemas import LoginRequest, SignupRequest, UserResponse
from auth.service import AuthenticatedUser
from auth.settings import Settings
from fastapi import APIRouter, Depends, Request, Response, status

# /health carries no internal-secret requirement -- it backs the container healthcheck, which
# doesn't have the shared secret to send. Every other route does, via this router-level dependency.
router = APIRouter(dependencies=[Depends(require_internal_secret)])
health_router = APIRouter()


@health_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _user_response(user: AuthenticatedUser) -> UserResponse:
    return UserResponse(user_id=str(user.id), email=user.email, display_name=user.display_name)


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def signup(
    body: SignupRequest,
    response: Response,
    conn=Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    now = datetime.now(UTC)
    session = service.signup(
        conn,
        email=body.email,
        display_name=body.display_name,
        password=body.password,
        now=now,
        ttl_days=settings.session_ttl_days,
        absolute_ttl_days=settings.session_absolute_ttl_days,
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
    throttle=Depends(get_throttle),
) -> UserResponse:
    now = datetime.now(UTC)
    client_ip = request.client.host if request.client else "unknown"
    session = service.login(
        conn,
        email=body.email,
        password=body.password,
        client_ip=client_ip,
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
