// What every chart script shares: the theme's colors, read at runtime, and a redraw when the
// theme changes. Loaded (deferred) before the chart scripts that use it, so they find
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

  // A canvas doesn't restyle itself, so `redraw` runs when the OS switches scheme (only
  // visible with no manual choice, but harmless otherwise) or on the header toggle
  // (static/theme.js).
  onThemeChange(redraw) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
    document.addEventListener("stakehold:themechange", redraw);
  },
};
