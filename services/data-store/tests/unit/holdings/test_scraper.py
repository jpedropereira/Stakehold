from unittest.mock import MagicMock

import pytest
from data_store.holdings.scraper import (
    COOKIE_BANNER_SELECTOR,
    DOWNLOAD_HOLDINGS_BUTTON_SELECTOR,
    TNC_MODAL_SELECTOR,
    ScraperError,
    get_holdings_csv_url,
)


def build_mock_page(*, tnc_present=True, cookie_present=True, resolved_url=None, click_fails=False):
    page = MagicMock()

    def wait_for_selector(selector, timeout=None):
        if selector == TNC_MODAL_SELECTOR and not tnc_present:
            raise TimeoutError("T&C modal not shown")
        if selector == COOKIE_BANNER_SELECTOR and not cookie_present:
            raise TimeoutError("cookie banner not shown")
        return MagicMock()

    page.wait_for_selector.side_effect = wait_for_selector

    if click_fails:
        page.expect_request.side_effect = TimeoutError("no matching request seen")
    else:
        request_info = MagicMock()
        request_info.value.url = resolved_url
        expect_request_cm = MagicMock()
        expect_request_cm.__enter__.return_value = request_info
        expect_request_cm.__exit__.return_value = False
        page.expect_request.return_value = expect_request_cm

    return page


def patch_playwright(monkeypatch, page):
    context = MagicMock()
    context.new_page.return_value = page

    browser = MagicMock()
    browser.new_context.return_value = context

    pw = MagicMock()
    pw.chromium.launch.return_value = browser

    playwright_cm = MagicMock()
    playwright_cm.__enter__.return_value = pw
    playwright_cm.__exit__.return_value = False

    monkeypatch.setattr("data_store.holdings.scraper.sync_playwright", lambda: playwright_cm)
    return browser, page


def test_returns_resolved_download_url(monkeypatch):
    page = build_mock_page(
        resolved_url="https://blackrock.com/get-fund-document?component=holdings"
    )
    patch_playwright(monkeypatch, page)

    url = get_holdings_csv_url("https://www.ishares.com/uk/fund")

    assert url == "https://blackrock.com/get-fund-document?component=holdings"
    page.locator.assert_called_with(DOWNLOAD_HOLDINGS_BUTTON_SELECTOR)


def test_proceeds_when_tnc_modal_not_shown(monkeypatch):
    page = build_mock_page(tnc_present=False, resolved_url="https://example.com/holdings.csv")
    patch_playwright(monkeypatch, page)

    url = get_holdings_csv_url("https://www.ishares.com/uk/fund")

    assert url == "https://example.com/holdings.csv"


def test_proceeds_when_cookie_banner_not_shown(monkeypatch):
    page = build_mock_page(cookie_present=False, resolved_url="https://example.com/holdings.csv")
    patch_playwright(monkeypatch, page)

    url = get_holdings_csv_url("https://www.ishares.com/uk/fund")

    assert url == "https://example.com/holdings.csv"


def test_raises_scraper_error_when_download_link_not_resolved(monkeypatch):
    page = build_mock_page(click_fails=True)
    patch_playwright(monkeypatch, page)

    with pytest.raises(ScraperError, match="Could not resolve"):
        get_holdings_csv_url("https://www.ishares.com/uk/fund")


def test_closes_browser_on_success(monkeypatch):
    page = build_mock_page(resolved_url="https://example.com/holdings.csv")
    browser, _ = patch_playwright(monkeypatch, page)

    get_holdings_csv_url("https://www.ishares.com/uk/fund")

    browser.close.assert_called_once()


def test_closes_browser_on_failure(monkeypatch):
    page = build_mock_page(click_fails=True)
    browser, _ = patch_playwright(monkeypatch, page)

    with pytest.raises(ScraperError):
        get_holdings_csv_url("https://www.ishares.com/uk/fund")

    browser.close.assert_called_once()
