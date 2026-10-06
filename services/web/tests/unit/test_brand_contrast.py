import re
from pathlib import Path

import pytest

_TOKENS_PATH = Path(__file__).resolve().parents[4] / "brand" / "tokens.css"
_DECLARATION = re.compile(r"(--[\w-]+):\s*([^;]+);")
_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)")

_TEXT = 4.5  # WCAG AA, body text
_NON_TEXT = 3.0  # WCAG AA, focus rings and form-control borders

# Every text/surface pair the brand charter's chapter II contrast table records. Translucent
# foregrounds (the control borders) are blended onto the surface before measuring. Dividers
# (--hairline, --hairline-on-dark) are decorative and deliberately absent.
_PAIRS = [
    # light
    ("--stone", "--parchment", _TEXT),
    ("--stone", "--parchment-deep", _TEXT),
    ("--stone-600", "--parchment", _TEXT),
    ("--stone-600", "--parchment-deep", _TEXT),
    ("--forest", "--parchment", _TEXT),
    ("--forest", "--parchment-deep", _TEXT),
    ("--oxblood", "--parchment", _TEXT),
    ("--oxblood", "--parchment-deep", _TEXT),
    ("--stone", "--gold", _TEXT),
    ("--gold-deep", "--parchment", _NON_TEXT),
    ("--gold-deep", "--parchment-deep", _NON_TEXT),
    ("--control-border", "--parchment", _NON_TEXT),
    ("--control-border", "--parchment-deep", _NON_TEXT),
    # dark
    ("--parchment", "--stone-deep", _TEXT),
    ("--parchment", "--stone", _TEXT),
    ("--parchment-muted", "--stone-deep", _TEXT),
    ("--parchment-muted", "--stone", _TEXT),
    ("--forest-on-dark", "--stone-deep", _TEXT),
    ("--forest-on-dark", "--stone", _TEXT),
    ("--oxblood-on-dark", "--stone-deep", _TEXT),
    ("--oxblood-on-dark", "--stone", _TEXT),
    ("--gold", "--stone-deep", _NON_TEXT),
    ("--gold", "--stone", _NON_TEXT),
    ("--control-border-on-dark", "--stone-deep", _NON_TEXT),
    ("--control-border-on-dark", "--stone", _NON_TEXT),
]


def _tokens():
    return dict(_DECLARATION.findall(_TOKENS_PATH.read_text()))


def _rgba(value):
    """A token value as (r, g, b, alpha), r/g/b in 0-255."""
    value = value.strip()
    if value.startswith("#"):
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5)) + (1.0,)
    match = _RGBA.fullmatch(value)
    assert match, f"unsupported color value: {value!r}"
    return tuple(int(c) for c in match.groups()[:3]) + (float(match.group(4)),)


def _blend(fg, bg):
    alpha = fg[3]
    return tuple(alpha * f + (1 - alpha) * b for f, b in zip(fg[:3], bg[:3]))


def _luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_contrast_helper_matches_known_wcag_ratios():
    """Pin the formula itself: black on white is 21:1, a color on itself 1:1."""
    assert _contrast((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert _contrast((201, 154, 62), (201, 154, 62)) == pytest.approx(1.0)


@pytest.mark.parametrize("fg, bg, minimum", _PAIRS, ids=[f"{f} on {b}" for f, b, _ in _PAIRS])
def test_brand_token_pair_meets_wcag_aa(fg, bg, minimum):
    """UI-2: the signed-off palette meets WCAG AA in both themes. A token edit that drops a
    pair below AA fails here rather than surfacing in a page-by-page audit."""
    tokens = _tokens()
    assert fg in tokens and bg in tokens, f"brand/tokens.css is missing {fg} or {bg}"

    surface = _rgba(tokens[bg])
    assert surface[3] == 1.0, f"{bg} must be opaque to serve as a surface"
    ratio = _contrast(_blend(_rgba(tokens[fg]), surface), surface[:3])

    assert ratio >= minimum, f"{fg} on {bg} is {ratio:.2f}:1, needs {minimum}:1"
