"""GET /portfolio: the old address of one account's holdings page, kept working.

PF-2 moved that page to /accounts/holdings (api/holdings.py) so /portfolio can become the
combined page across accounts (PF-4). Until then this answers only the old ?account=X links; a
/portfolio with no account stays the 422 it always was, since nothing links to it.

A permanent redirect (308) because /portfolio?account=X keeps redirecting after PF-4 too, so a
browser may cache it; it caches per URL, query included, so the bare /portfolio is unaffected.
It asks nobody who is logged in: the redirect reveals nothing, and the page it points at
requires the login. The query string is passed on as it arrived, so an encoded label stays
encoded, and the target path is fixed, so it can't send the visitor off the site.
"""

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/portfolio")
async def portfolio(request: Request, account: str = Query(..., min_length=1)):
    return RedirectResponse(
        url=f"/accounts/holdings?{request.url.query}",
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    )
