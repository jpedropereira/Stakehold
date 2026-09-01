import re
from pathlib import Path

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "web"
_CSS_PATH = _SRC_DIR / "static" / "web.css"
_TEMPLATES_DIR = _SRC_DIR / "templates"


def test_web_css_has_no_hardcoded_hex_colors():
    """CLAUDE.md's brand rule: import brand/tokens.css and use its custom properties, never
    hardcode hex values. Enforced mechanically here rather than only by review."""
    css = _CSS_PATH.read_text()

    matches = _HEX_COLOR.findall(css)

    assert matches == [], f"web.css hardcodes hex color(s): {matches}"


def test_templates_have_no_hardcoded_hex_colors():
    """Same brand rule as above, but for an inline `style="color:#…"` in a Jinja template --
    web.css isn't the only place a hex value could land."""
    offenders = {}
    for path in sorted(_TEMPLATES_DIR.rglob("*.html")):
        matches = _HEX_COLOR.findall(path.read_text())
        if matches:
            offenders[str(path.relative_to(_SRC_DIR))] = matches

    assert offenders == {}, f"template(s) hardcode hex color(s): {offenders}"
