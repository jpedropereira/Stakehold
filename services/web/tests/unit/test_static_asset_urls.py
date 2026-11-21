"""Static and brand asset URLs carry a version taken from the file's content (`?v=`).

The files are served without Cache-Control, so a browser decides on its own how long a cached
copy stays fresh. With a fixed URL it went on using a stale chart_theme.js after the file
changed, and the new drawdown chart threw on the missing `formatPct` and drew nothing. A
content version gives every change a new URL, which no cached copy matches.
"""

import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client
from web.auth_client.errors import SessionInvalid
from web.settings import Settings
from web.static_assets import UnknownAsset, make_asset_url

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
_ASSET_REF = re.compile(r'(?:src|href)="(/(?:static|brand)/[^"]*)"')


class FakeAuthClient:
    async def validate_session(self, *, cookie_header):
        raise SessionInvalid("no session")


def _settings(brand_dir: Path) -> Settings:
    return Settings(
        auth_api_url="http://auth.internal",
        internal_secret="test-secret",  # pragma: allowlist secret
        portfolio_api_url="http://portfolio.internal",
        portfolio_internal_secret="test-portfolio-secret",  # pragma: allowlist secret
        brand_dir=brand_dir,
        cookie_secure=False,
        api_host="127.0.0.1",
        api_port=8000,
        http_timeout_seconds=5.0,
    )


@pytest.fixture
def brand_dir(tmp_path):
    """A copy of the brand directory, so a test can change a file's content."""
    copy = tmp_path / "brand"
    shutil.copytree(settings_module._DEFAULT_BRAND_DIR, copy)
    return copy


@pytest.fixture
def client(brand_dir):
    app = create_app(_settings(brand_dir))
    app.dependency_overrides[get_auth_client] = lambda: FakeAuthClient()
    with TestClient(app) as test_client:
        yield test_client


def _asset_refs(html: str) -> list[str]:
    return _ASSET_REF.findall(html)


def _ref(html: str, path: str) -> str:
    return next(ref for ref in _asset_refs(html) if ref.split("?")[0] == path)


def test_every_asset_on_a_page_carries_a_version(client):
    refs = _asset_refs(client.get("/login").text)

    assert refs, "the login page links no assets"
    for ref in refs:
        assert re.fullmatch(r"/(static|brand)/[\w./-]+\?v=[0-9a-f]{12}", ref), ref


def test_layout_css_is_linked_after_web_css(client):
    """layout.css holds the page grids (UX-11). Its rules override .card's width inside a grid
    column, so it has to come after web.css in the cascade."""
    paths = [ref.split("?")[0] for ref in _asset_refs(client.get("/login").text)]

    assert "/static/layout.css" in paths
    assert paths.index("/static/web.css") < paths.index("/static/layout.css")


def test_a_versioned_url_serves_the_file(client):
    html = client.get("/login").text

    for path, source in [
        ("/static/theme.js", Path(__file__).resolve().parents[2] / "src/web/static/theme.js"),
        ("/brand/tokens.css", settings_module._DEFAULT_BRAND_DIR / "tokens.css"),
    ]:
        response = client.get(_ref(html, path))
        assert response.status_code == 200
        assert response.content == source.read_bytes()


def test_the_version_is_stable_while_the_file_is_unchanged(client):
    first = _ref(client.get("/login").text, "/brand/tokens.css")
    second = _ref(client.get("/login").text, "/brand/tokens.css")

    assert first == second


def test_changing_a_file_changes_its_url(client, brand_dir):
    before = _ref(client.get("/login").text, "/brand/tokens.css")
    (brand_dir / "tokens.css").write_text("/* changed */\n")

    after = _ref(client.get("/login").text, "/brand/tokens.css")

    assert after != before


def test_no_template_links_an_asset_without_asset_url():
    # A bare "/static/..." in a template would be served unversioned, and could go stale.
    offenders = [
        f"{template.relative_to(_TEMPLATES_DIR)}: {ref}"
        for template in _TEMPLATES_DIR.rglob("*.html")
        for ref in _asset_refs(template.read_text())
    ]

    assert offenders == []


def test_an_unknown_asset_fails_loudly(tmp_path):
    asset_url = make_asset_url({"/static": tmp_path})

    with pytest.raises(UnknownAsset):
        asset_url("/static/missing.js")


@pytest.mark.parametrize(
    "path", ["/static/../secret.txt", "/elsewhere/file.js", "static/file.js", "/static/"]
)
def test_a_path_outside_a_mount_is_refused(tmp_path, path):
    mount = tmp_path / "static"
    mount.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    asset_url = make_asset_url({"/static": mount})

    with pytest.raises(UnknownAsset):
        asset_url(path)
