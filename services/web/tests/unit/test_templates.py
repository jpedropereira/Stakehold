import re
from pathlib import Path

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "web.css"


def test_web_css_has_no_hardcoded_hex_colors():
    """CLAUDE.md's brand rule: import brand/tokens.css and use its custom properties, never
    hardcode hex values. Enforced mechanically here rather than only by review."""
    css = _CSS_PATH.read_text()

    matches = _HEX_COLOR.findall(css)

    assert matches == [], f"web.css hardcodes hex color(s): {matches}"
