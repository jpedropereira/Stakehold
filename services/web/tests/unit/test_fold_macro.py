"""The fold (UX-7, partials/_fold.html): one `<details>` look for every block of secondary
detail. Its summary is one line naming what is inside, with the count where there is one; the
block's heading stays outside it, so heading navigation still finds the block."""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
_ENV = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
HOLDINGS = ("holding", "holdings")


def _fold(*args, body="<p>inside</p>", **kwargs) -> str:
    template = _ENV.from_string(
        '{% from "partials/_fold.html" import fold %}'
        "{% call fold(*args, **kwargs) %}" + body + "{% endcall %}"
    )
    return template.render(args=args, kwargs=kwargs)


def _summary(html: str) -> str:
    return re.search(r"<summary>(.*?)</summary>", html, re.S).group(1)


def test_a_fold_is_a_closed_details_holding_its_body():
    html = _fold("Rolling volatility and Sharpe")

    assert html.startswith('<details class="fold">')
    assert html.rstrip().endswith("</details>")
    assert "<p>inside</p>" in html
    assert _summary(html) == "Rolling volatility and Sharpe"


def test_a_count_follows_the_summary_in_mono():
    html = _fold("What the markers mean", count=2, unit=("note", "notes"))

    assert _summary(html) == 'What the markers mean · <span class="mono">2</span> notes'


def test_one_takes_the_singular():
    assert _summary(_fold("", count=1, unit=HOLDINGS)) == '<span class="mono">1</span> holding'


def test_a_count_alone_has_no_separator():
    assert _summary(_fold("", count=3, unit=HOLDINGS)) == '<span class="mono">3</span> holdings'


def test_open_starts_it_open():
    assert _fold("Pair", open=True).startswith('<details class="fold" open>')


def test_an_extra_class_names_the_fold():
    assert _fold("Risk", class_="labs-risk-fold").startswith(
        '<details class="fold labs-risk-fold">'
    )


def test_the_summary_is_escaped_unless_it_is_markup():
    assert _summary(_fold("<b>")) == "&lt;b&gt;"
    assert _summary(_fold(Markup('<span class="mono">AAPL</span>'))) == (
        '<span class="mono">AAPL</span>'
    )
