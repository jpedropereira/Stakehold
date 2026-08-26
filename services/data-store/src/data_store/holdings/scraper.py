from playwright.sync_api import sync_playwright

# iShares shows a Terms & Conditions modal and a separate OneTrust cookie banner on first
# visit; both can intercept clicks on the actual page if left open. Neither is guaranteed to
# appear (e.g. a persisted consent cookie can skip them), so both are dismissed best-effort.
TNC_MODAL_SELECTOR = ".oneds-tnc-modal"
TNC_ACCEPT_SELECTOR = ".oneds-tnc-modal a.oneds-button-v2-primary"
COOKIE_BANNER_SELECTOR = "#onetrust-accept-btn-handler"

DOWNLOAD_HOLDINGS_BUTTON_SELECTOR = 'button[aria-label="Download Holdings"]'
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ScraperError(Exception):
    """Raised when the holdings CSV download link can't be resolved from the fund page."""


def get_holdings_csv_url(fund_url: str, *, timeout_ms: int = 60000) -> str:
    """Drives a real page load to resolve the current holdings CSV download URL.

    The "Download Holdings" button doesn't expose a static href — clicking it fires an XHR
    to a BlackRock document API carrying the fund's current as-of date, so the URL can't be
    constructed offline. The URL it resolves to is otherwise a plain, unauthenticated GET
    (confirmed fetchable via a plain HTTP client), so only URL resolution needs a browser.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                locale="en-GB",
                timezone_id="Europe/London",
                user_agent=USER_AGENT,
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(fund_url, wait_until="domcontentloaded")

            _dismiss_if_present(page, TNC_MODAL_SELECTOR, TNC_ACCEPT_SELECTOR)
            _dismiss_if_present(page, COOKIE_BANNER_SELECTOR, COOKIE_BANNER_SELECTOR)

            try:
                with page.expect_request(_is_holdings_document_request) as request_info:
                    page.locator(DOWNLOAD_HOLDINGS_BUTTON_SELECTOR).click()
            except Exception as e:
                raise ScraperError(
                    f"Could not resolve the holdings CSV download link on {fund_url}: {e}"
                ) from e

            return request_info.value.url
        finally:
            browser.close()


def _is_holdings_document_request(request) -> bool:
    return "get-fund-document" in request.url and "component=holdings" in request.url


def _dismiss_if_present(page, wait_selector: str, click_selector: str) -> None:
    try:
        page.wait_for_selector(wait_selector, timeout=8000)
        page.click(click_selector)
    except Exception:
        pass  # not shown this run (e.g. consent already recorded) — nothing to dismiss
