import re
from pathlib import Path

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "web"
_CSS_PATH = _SRC_DIR / "static" / "web.css"
_TEMPLATES_DIR = _SRC_DIR / "templates"
_TOKENS_PATH = _SRC_DIR.parents[3] / "brand" / "tokens.css"

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_ROOT_BLOCK = re.compile(r":root\s*\{[^}]*\}")
_BRAND_COLOR_TOKEN = re.compile(r"(--[\w-]+)\s*:\s*(?:#[0-9a-fA-F]{3,8}\b|rgba?\()")
_SELECTOR_LIST = re.compile(r"([^{}]+)\{")
_CLASS_SELECTOR = re.compile(r"\.([a-zA-Z_][\w-]*)")
_JINJA_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


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


def test_templates_have_no_inline_executable_script():
    """web's CSP (api/security_headers.py) is script-src 'self' with no 'unsafe-inline' and no
    nonce -- an inline <script> with real JS logic would be silently blocked by the browser, not
    just a style violation. A <script type="application/json"> data island is fine: the browser
    never executes it, only same-origin JS reads it (see portfolio.html/portfolio_chart.js)."""
    offenders = {}
    for path in sorted(_TEMPLATES_DIR.rglob("*.html")):
        text = path.read_text()
        bad_tags = []
        for attrs, body in _SCRIPT_TAG.findall(text):
            has_src = "src=" in attrs
            is_data_island = 'type="application/json"' in attrs
            if not has_src and not is_data_island:
                bad_tags.append(attrs.strip() or "<script>")
        if bad_tags:
            offenders[str(path.relative_to(_SRC_DIR))] = bad_tags

    assert offenders == {}, f"template(s) have inline executable <script>: {offenders}"


def _css_without_comments():
    return _CSS_COMMENT.sub("", _CSS_PATH.read_text())


def _semantic_alias_block(css):
    match = _ROOT_BLOCK.search(css)
    assert match, "web.css has no :root semantic alias block"
    return match.group(0)


def _css_class_names(css):
    names = set()
    for selector_list in _SELECTOR_LIST.findall(css):
        if selector_list.strip().startswith("@"):
            continue
        names.update(_CLASS_SELECTOR.findall(selector_list))
    return names


def test_web_css_uses_brand_color_tokens_only_in_semantic_aliases():
    """Every rule outside web.css's :root block styles through semantic tokens (--surface,
    --text, --accent, ...), never a brand color token directly -- so dark mode (UI-3) only has
    to re-point the aliases, not rewrite rules one by one. The brand color list is read from
    tokens.css, so a brand color added there later is covered without touching this test."""
    brand_colors = set(_BRAND_COLOR_TOKEN.findall(_TOKENS_PATH.read_text()))
    assert brand_colors, "found no color tokens in brand/tokens.css"
    css = _css_without_comments()
    rules = css.replace(_semantic_alias_block(css), "", 1)

    offenders = sorted(t for t in brand_colors if re.search(rf"var\(\s*{t}\s*[,)]", rules))

    assert offenders == [], f"web.css uses brand color token(s) outside :root: {offenders}"


def test_web_css_defines_semantic_tokens():
    block = _semantic_alias_block(_css_without_comments())
    expected = [
        "--surface",
        "--surface-raised",
        "--text",
        "--text-muted",
        "--border",
        "--accent",
        "--on-accent",
        "--gain",
        "--loss",
        "--focus-ring",
        "--border-control",
        *(f"--space-{n}" for n in range(1, 9)),
        "--content-max",
    ]

    missing = [t for t in expected if not re.search(rf"{t}\s*:", block)]

    assert missing == [], f"web.css :root is missing semantic token(s): {missing}"


def test_web_css_has_no_unused_class_selectors():
    """A class renamed in the templates but left behind in web.css is dead CSS that the next
    reader has to puzzle over. A plain word search also finds classes chosen inside a Jinja
    expression (e.g. 'gain' if ... else 'loss')."""
    templates = "\n".join(p.read_text() for p in _TEMPLATES_DIR.rglob("*.html"))

    unused = sorted(
        name
        for name in _css_class_names(_css_without_comments())
        if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", templates)
    )

    assert unused == [], f"web.css styles class(es) no template uses: {unused}"


def test_templates_reference_only_defined_classes():
    """The reverse check: a class a template names but web.css doesn't define is usually a typo
    or a missed rename, and it fails silently in the browser. Classes chosen inside a Jinja
    expression are stripped out here; the unused-selector test above covers those."""
    defined = _css_class_names(_css_without_comments())
    offenders = {}
    for path in sorted(_TEMPLATES_DIR.rglob("*.html")):
        text = _JINJA_TAG.sub("", path.read_text())
        used = {name for attr in _CLASS_ATTR.findall(text) for name in attr.split()}
        undefined = sorted(used - defined)
        if undefined:
            offenders[str(path.relative_to(_SRC_DIR))] = undefined

    assert offenders == {}, f"template(s) use class(es) web.css doesn't define: {offenders}"


def test_mono_inputs_use_the_figure_font():
    """A bare `.mono` rule loses to the form-control rule (`.field input`, higher specificity)
    that sets the body font, so a ticker/identifier input marked `class="mono"` would silently
    render in Inter. The brand puts every ticker in mono, so web.css needs an `input.mono` rule
    that sets the figure font explicitly."""
    css = _css_without_comments()
    rules = _RULE.findall(css)

    mono_input_rules = [body for selector, body in rules if "input.mono" in selector]

    assert any(
        re.search(r"font\s*:\s*var\(--text-figure\)", body) for body in mono_input_rules
    ), "web.css has no input.mono rule setting font: var(--text-figure)"


def test_page_stacks_its_cards_vertically():
    """Regression: `.page` was a centered flex *row*, so a page with several cards (accounts,
    portfolio, admin mappings) squeezed them side by side, and once they overflowed, the
    centering pushed the first card off the left edge where it can't be scrolled to. The page
    container must lay its children out as a column."""
    css = _css_without_comments()
    page_rules = [body for selector, body in _RULE.findall(css) if selector.strip() == ".page"]

    assert any(
        re.search(r"flex-direction\s*:\s*column", body) for body in page_rules
    ), ".page does not stack its children in a column"


def test_form_controls_use_the_control_border():
    """UI-2: --border is a decorative hairline (~1.3:1). A form control's edge identifies the
    control, so WCAG wants 3:1 -- that's --border-control's job, not --border's."""
    offenders = []
    for selectors, body in _RULE.findall(_css_without_comments()):
        if not re.search(r"\b(input|select|textarea)\b", selectors):
            continue
        for declaration in re.findall(r"border(?:-[a-z]+)?\s*:[^;]*", body):
            if "var(--border)" in declaration:
                offenders.append(f"{selectors.strip()} {{ {declaration.strip()} }}")

    assert offenders == [], f"form control(s) bordered with the decorative hairline: {offenders}"


def test_decimal_inputs_are_mono():
    """The brand sets every number in mono. A field that takes a number (`inputmode="decimal"`:
    amount, quantity, price, FX rate, fees) must be marked `.mono`, or what the user types renders
    in Inter."""
    offenders = {}
    for path in sorted(_TEMPLATES_DIR.rglob("*.html")):
        for tag in re.findall(r"<input\b[^>]*>", path.read_text()):
            if 'inputmode="decimal"' not in tag:
                continue
            classes = re.search(r'\bclass="([^"]*)"', tag)
            if not classes or "mono" not in classes.group(1).split():
                name = re.search(r'\bname="([^"]*)"', tag)
                offenders.setdefault(str(path.relative_to(_SRC_DIR)), []).append(
                    name.group(1) if name else tag
                )

    assert offenders == {}, f"decimal input(s) not marked .mono: {offenders}"


def test_account_row_wraps():
    """Regression: an account row holds a label, a broker and three links on one flex line,
    which is wider than a 360px phone, so the accounts page scrolled horizontally. The row must
    be allowed to wrap."""
    css = _css_without_comments()
    row_rules = [
        body for selector, body in _RULE.findall(css) if selector.strip() == ".account-row"
    ]

    assert any(
        re.search(r"flex-wrap\s*:\s*wrap", body) for body in row_rules
    ), ".account-row does not wrap"


def test_site_header_wraps_at_phone_width():
    """The header holds the mark, three nav links, the user's name and a log-out button, which
    is wider than a 360px phone. At phone width it must wrap, and the nav must take a row of its
    own, rather than push the page sideways."""
    css = _css_without_comments()
    phone = re.search(r"@media \(max-width: 480px\)\s*\{(.*)\}\s*$", css, re.DOTALL)
    assert phone, "web.css has no phone-width media query"
    rules = dict((sel.strip(), body) for sel, body in _RULE.findall(phone.group(1)))

    assert re.search(r"flex-wrap\s*:\s*wrap", rules.get(".site-header-inner", ""))
    assert re.search(r"flex-basis\s*:\s*100%", rules.get(".site-nav", ""))
