"""The segmented control (UX-5, partials/_segmented.html): one look for every period, window,
series and sort switch. Links for the server-side switches, so they work without JS; buttons for
the returns chart's client-side ones. Exactly one option reads as selected."""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
_ENV = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
_MODULE = _ENV.get_template("partials/_segmented.html").module
LINKS = {"1Y": "/labs?period=1Y", "5Y": "/labs?period=5Y&a=b"}
BUTTONS = [("total_return_eur", "Total return"), ("market_value_eur", "Market value")]


def _links(*args, **kwargs) -> str:
    return str(_MODULE.segmented_links(*args, **kwargs))


def _buttons(*args, **kwargs) -> str:
    return str(_MODULE.segmented_buttons(*args, **kwargs))


def test_links_are_a_labelled_group_of_links():
    html = _links("Growth period", LINKS, "1Y")

    assert html.startswith('<div class="segmented" role="group" aria-label="Growth period">')
    assert re.findall(r'<a class="segmented-option" href="([^"]*)"', html) == [
        "/labs?period=1Y",
        "/labs?period=5Y&amp;a=b",
    ]


def test_links_mark_only_the_current_option():
    html = _links("Growth period", LINKS, "5Y")

    assert html.count('aria-current="true"') == 1
    assert re.search(r'href="/labs\?period=5Y[^"]*" aria-current="true">5Y</a>', html)


def test_a_current_value_matching_no_option_marks_nothing():
    assert "aria-current" not in _links("Growth period", LINKS, "10Y")


def test_labels_name_options_whose_text_differs_from_their_value():
    links = {"sigma_p": "/risk?rank_by=sigma_p", "diversification_ratio": "/risk?rank_by=dr"}

    html = _links("Order by", links, "sigma_p", labels={"sigma_p": "Volatility"})

    assert re.findall(r">([^<]+)</a>", html) == ["Volatility", "diversification_ratio"]


def test_an_extra_class_names_the_group():
    html = _links("Growth period", LINKS, "1Y", class_="labs-periods")

    assert html.startswith('<div class="segmented labs-periods" role="group"')


def test_mono_labels_are_in_mono():
    html = _links("Growth period", LINKS, "1Y", mono=True)

    assert '<span class="mono">1Y</span></a>' in html
    assert '<span class="mono">5Y</span></a>' in html


def test_buttons_are_a_labelled_group_with_one_pressed():
    html = _buttons("Chart view", BUTTONS, "total_return_eur", "chart-view")

    assert html.startswith('<div class="segmented" role="group" aria-label="Chart view">')
    assert re.findall(r'data-chart-view="(\w+)" aria-pressed="(\w+)"', html) == [
        ("total_return_eur", "true"),
        ("market_value_eur", "false"),
    ]
    assert html.count('<button type="button" class="segmented-option"') == 2


def test_button_values_are_compared_as_text():
    """The returns chart's ranges are months as numbers; 0 means All."""
    html = _buttons("Date range", [(1, "1M"), (0, "All")], 0, "chart-range", mono=True)

    assert re.findall(r'data-chart-range="(\d+)" aria-pressed="(\w+)"', html) == [
        ("1", "false"),
        ("0", "true"),
    ]
    assert '<span class="mono">All</span></button>' in html
