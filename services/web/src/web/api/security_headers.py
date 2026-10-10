from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# fonts.googleapis.com/fonts.gstatic.com: brand/tokens.css @imports Google Fonts. No script-src
# CDN is needed since htmx is vendored into static/vendor/ rather than loaded from a CDN.
_CSP = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self'; "
    "script-src 'self'"
)


def apply_security_headers(response: Response) -> Response:
    """Sets the headers every response carries. The middleware below applies it to everything
    that passes through it; app.py's 500 handler applies it itself, because Starlette runs that
    handler in ServerErrorMiddleware, outside every user middleware, so its response never
    passes back through this one."""
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = _CSP
    return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        return apply_security_headers(await call_next(request))
