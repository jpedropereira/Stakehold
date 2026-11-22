"""GET /glossary: what each figure on the holdings pages and the overview means.

The holdings pages link every column header and tile label to its entry here
(/glossary#<term>), so a definition is written once, not repeated under each table. The page is
static text: it calls no other service, so it still renders while portfolio is down. Behind the
login like every other page, so the header shows the same nav and user. See
docs/copy-ui.md, "Glossary".
"""

from fastapi import APIRouter, Depends, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_settings, get_templates, require_user
from web.api.routes import _render
from web.auth_client.base import AuthUser
from web.settings import Settings

router = APIRouter()


@router.get("/glossary")
async def glossary(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
):
    # The header's logout and theme forms need the token, as on every other page.
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    # No nav_active: the page is reached from the figures it explains, not from the nav.
    return _render(
        request, response, templates, "glossary.html", {"user": user, "csrf_token": csrf_token}
    )
