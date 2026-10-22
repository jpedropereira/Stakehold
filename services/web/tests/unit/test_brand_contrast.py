import re
from pathlib import Path

import pytest

_TOKENS_PATH = Path(__file__).resolve().parents[4] / "brand" / "tokens.css"
_WEB_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "web.css"
_ROOT_BLOCK = re.compile(r":root\s*\{[^}]*\}")
_ALIAS = re.compile(r"(--[\w-]+):\s*var\(\s*(--[\w-]+)\s*\)\s*;")
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


# web.css's semantic aliases, measured against both surfaces they're drawn on, in each theme. The
# token-level pairs above prove a good combination exists; this proves web.css actually picked it,
# e.g. that inputs are bordered with a control border rather than the decorative hairline. The
# system-dark block isn't listed: test_templates.py asserts it's identical to the manual one.
_THEMES = {
    "light": _ROOT_BLOCK,
    "dark": re.compile(r':root\[data-theme="dark"\]\s*\{[^}]*\}'),
}
_ALIAS_PAIRS = [
    (theme, fg, bg, minimum)
    for theme in _THEMES
    for fg, minimum in (
        ("--text", _TEXT),
        ("--text-muted", _TEXT),
        ("--gain", _TEXT),
        ("--loss", _TEXT),
        ("--focus-ring", _NON_TEXT),
        ("--border-control", _NON_TEXT),
    )
    for bg in ("--surface", "--surface-raised")
]


def _web_css_aliases(theme):
    css = re.sub(r"/\*.*?\*/", "", _WEB_CSS_PATH.read_text(), flags=re.S)
    block = _THEMES[theme].search(css)
    assert block, f"web.css has no {theme} semantic alias block"
    return dict(_ALIAS.findall(block.group(0)))


@pytest.mark.parametrize(
    "theme, fg, bg, minimum",
    _ALIAS_PAIRS,
    ids=[f"{t}: {f} on {b}" for t, f, b, _ in _ALIAS_PAIRS],
)
def test_web_css_semantic_alias_pair_meets_wcag_aa(theme, fg, bg, minimum):
    aliases, tokens = _web_css_aliases(theme), _tokens()
    assert fg in aliases and bg in aliases, f"web.css {theme} block doesn't alias {fg} or {bg}"

    surface = _rgba(tokens[aliases[bg]])
    ratio = _contrast(_blend(_rgba(tokens[aliases[fg]]), surface), surface[:3])

    assert ratio >= minimum, f"{fg} ({aliases[fg]}) on {bg} is {ratio:.2f}:1, needs {minimum}:1"


@pytest.mark.parametrize("theme", _THEMES)
def test_web_css_aliases_are_not_self_referencing(theme):
    """`--x: var(--x)` is a CSS dependency cycle: the browser makes --x invalid, but resolving it
    through tokens.css (as the contrast test above does) would still find a value and pass. A
    semantic alias must have a different name from the brand token it points at."""
    cycles = sorted(name for name, target in _web_css_aliases(theme).items() if name == target)

    assert cycles == [], f"web.css {theme} alias(es) reference themselves: {cycles}"


# The correlation heatmap's squares (RK-18): an ink ramp of --text over the card's surface,
# with --text on the light steps and the surface colour on the darkest. Read from web.css, so
# deepening a step past what its text allows fails here.
_HEAT_STEP = re.compile(
    r"\.corr-(\d)\s*\{\s*background:\s*color-mix\(in srgb, var\(--text\) (\d+)%, transparent\);"
    r"(\s*color:\s*var\(--surface-raised\);)?"
)


def _heat_steps():
    css = re.sub(r"/\*.*?\*/", "", _WEB_CSS_PATH.read_text(), flags=re.S)
    return [(int(n), int(pct) / 100, bool(flipped)) for n, pct, flipped in _HEAT_STEP.findall(css)]


def test_the_heatmap_has_four_tinted_steps_the_darkest_flipped():
    steps = _heat_steps()

    assert [n for n, _, _ in steps] == [1, 2, 3, 4]
    assert [flipped for _, _, flipped in steps] == [False, False, False, True]
    assert [a for _, a, _ in steps] == sorted(a for _, a, _ in steps)


@pytest.mark.parametrize("theme", list(_THEMES))
@pytest.mark.parametrize("surface", ["--surface", "--surface-raised"])
def test_heatmap_text_meets_wcag_aa_on_every_step(theme, surface):
    aliases, tokens = _web_css_aliases(theme), _tokens()
    text = _rgba(tokens[aliases["--text"]])[:3]
    under = _rgba(tokens[aliases[surface]])[:3]
    for n, alpha, flipped in _heat_steps():
        fill = _blend(text + (alpha,), under)
        ratio = _contrast(under if flipped else text, fill)
        assert ratio >= _TEXT, f"{theme}: step {n} on {surface} is {ratio:.2f}:1"
