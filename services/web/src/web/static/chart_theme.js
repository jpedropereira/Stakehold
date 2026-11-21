// What every chart script shares: the theme's colors, read at runtime, a redraw when the
// theme changes, and a draw after a section swap. Loaded (deferred) before the chart scripts that use it, so they find
// `StakeholdChartTheme` once DOMContentLoaded fires.
window.StakeholdChartTheme = {
  // Semantic tokens, not brand tokens, so a chart follows the theme like the rest of the page.
  // Custom properties resolve their var() references, so these are real colors.
  readColors() {
    const style = getComputedStyle(document.documentElement);
    const read = (name) => style.getPropertyValue(name).trim();
    return {
      gain: read("--gain"),
      loss: read("--loss"),
      text: read("--text"),
      muted: read("--text-muted"),
      grid: read("--border"),
    };
  },

  // Mirrors web/formatting.py's pct: a signed percentage, a true minus sign, and no sign on a
  // value that displays as zero. Keep the two in step (as portfolio_chart.js's formatEur). Here
  // so the Labs growth and drawdown charts share one copy.
  formatPct(fraction, digits) {
    const magnitude = Math.abs(fraction * 100).toFixed(digits);
    const isZero = Number(magnitude) === 0;
    const sign = isZero ? "" : fraction < 0 ? "−" : "+";
    return `${sign}${magnitude}%`;
  },

  // `color` at `alpha`, for a soft fill (the drawdown's area, a rolling Sharpe's ± 1 s.e.
  // band). A token can be written as hex or rgb(), so a canvas normalizes it first: its
  // fillStyle reads back as #rrggbb for any opaque color.
  translucent(color, alpha) {
    const probe = document.createElement("canvas").getContext("2d");
    probe.fillStyle = color;
    const hex = probe.fillStyle;
    if (!/^#[0-9a-f]{6}$/i.test(hex)) return color;
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  },

  // `text` cut to fit `maxWidth` in the font set on `ctx`, ending in "…" when cut. "" when
  // fewer than MIN characters would be left: a stub says nothing. For a tooltip line, which
  // Chart.js doesn't wrap, so a long instrument name would run past the canvas edge and be
  // clipped (UX-3).
  fitText(ctx, text, maxWidth) {
    const MIN = 4;
    if (ctx.measureText(text).width <= maxWidth) return text;
    for (let length = text.length - 1; length >= MIN; length--) {
      const cut = `${text.slice(0, length).trimEnd()}…`;
      if (ctx.measureText(cut).width <= maxWidth) return cut;
    }
    return "";
  },

  // A canvas doesn't restyle itself, so `redraw` runs when the OS switches scheme (only
  // visible with no manual choice, but harmless otherwise) or on the header toggle
  // (static/theme.js). Given its `canvas`, the listener drops itself once a section swap
  // (UX-10) has taken the canvas off the page, so redraws don't pile up with each swap.
  onThemeChange(redraw, canvas) {
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = () => {
      if (canvas && !canvas.isConnected) {
        scheme.removeEventListener("change", listener);
        document.removeEventListener("stakehold:themechange", listener);
        return;
      }
      redraw();
    };
    scheme.addEventListener("change", listener);
    document.addEventListener("stakehold:themechange", listener);
  },

  // Runs `draw(root)` once the page has loaded, with the document as root, and again for each
  // section a switch swaps in (UX-10, static/section_swap.js), with that section as root, so a
  // chart script draws the canvases under root. A swap replaces the section's markup, so its
  // canvases are new, with no chart on them; the charts on the old ones, now off the page, are
  // destroyed first. Not those under root: each chart script mounts, so another script's new
  // chart may already be there.
  mount(draw) {
    const run = (root) => {
      for (const chart of Object.values(Chart.instances)) {
        if (!chart.canvas.isConnected) chart.destroy();
      }
      draw(root);
    };
    document.addEventListener("DOMContentLoaded", () => run(document));
    document.body.addEventListener("htmx:afterSettle", (event) => {
      const root = event.target;
      if (root instanceof Element && root.hasAttribute("data-swap-region")) run(root);
    });
  },
};
