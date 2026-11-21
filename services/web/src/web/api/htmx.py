from fastapi import Request, Response, status
from fastapi.responses import RedirectResponse


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") is not None


def htmx_or_redirect(request: Request, url: str) -> Response:
    """A 303 for a plain form post/navigation, or an HX-Redirect for an htmx one.

    htmx's own XHR follows a 303 transparently and swaps the target's *content* in place --
    which would drop a full HTML page into a form's error slot or a partial swap target.
    HX-Redirect instead tells htmx to perform a real browser-level navigation, matching what a
    plain request's redirect achieves. Shared by routes.py's own redirects and app.py's
    AuthRequired handler so every redirect in the app makes this choice the same way.
    """
    if is_htmx(request):
        return Response(status_code=status.HTTP_200_OK, headers={"HX-Redirect": url})
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


# Varied on by every response that can be either a section or a page (UX-10): one URL answers
# both, so a cache must keep them apart.
SWAP_VARY = ("HX-Request", "HX-Target")


def swap_target(request: Request, regions: tuple[str, ...]) -> str | None:
    """The region an htmx switch asked for, if it is one of this page's `regions` (UX-10): the
    route then renders that region alone. None for anything else, which gets the full page: a
    plain request, another target, and a history restore, which htmx sends with HX-Request but
    swaps into the whole body."""
    if not is_htmx(request) or request.headers.get("HX-History-Restore-Request") is not None:
        return None
    target = request.headers.get("HX-Target")
    return target if target in regions else None


def vary_on_swap(response: Response) -> Response:
    for header in SWAP_VARY:
        response.headers.add_vary_header(header)
    return response
