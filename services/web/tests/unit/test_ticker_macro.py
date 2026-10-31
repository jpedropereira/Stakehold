"""The ticker macro (UX-1, partials/_ticker.html): a ticker in mono, and its full name in a tooltip
that is also part of the text a screen reader reads. With no name, a plain mono ticker."""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
_ENV = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
_TAG = re.compile(r"<[^>]+>")
NAMES = {"VWCE.DE": "Vanguard FTSE All-World"}


def _ticker(*args, **kwargs) -> str:
    return str(_ENV.get_template("partials/_ticker.html").module.ticker(*args, **kwargs))


def test_a_named_ticker_is_focusable_with_its_name_in_a_tooltip():
    html = _ticker("VWCE.DE", NAMES)

    assert html == (
        '<span class="ticker" tabindex="0"><span class="mono">VWCE.DE</span>'
        '<span class="ticker-name"><span class="visually-hidden">, </span>'
        "Vanguard FTSE All-World</span></span>"
    )


def test_a_screen_reader_reads_the_ticker_then_its_name():
    assert _TAG.sub("", _ticker("VWCE.DE", NAMES)) == "VWCE.DE, Vanguard FTSE All-World"


def test_a_ticker_without_a_name_is_plain_mono_text():
    html = _ticker("IE00B4L5Y983", NAMES)

    assert html == '<span class="mono">IE00B4L5Y983</span>'


def test_no_names_at_all_means_plain_tickers():
    """A page whose lookup failed passes an empty map; one that never looked passes nothing."""
    assert _ticker("VWCE.DE", {}) == '<span class="mono">VWCE.DE</span>'
    assert _ticker("VWCE.DE", None) == '<span class="mono">VWCE.DE</span>'


def test_inside_a_link_or_button_the_ticker_adds_no_tab_stop():
    html = _ticker("VWCE.DE", NAMES, focusable=False)

    assert "tabindex" not in html
    assert '<span class="ticker-name">' in html


def test_the_name_is_escaped():
    """Names come from the data provider: text, never markup."""
    html = _ticker("EVIL", {"EVIL": "<script>alert(1)</script>"})

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
