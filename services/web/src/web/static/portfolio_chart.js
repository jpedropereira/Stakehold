document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("returns-chart");
  const dataEl = document.getElementById("returns-chart-data");
  if (!canvas || !dataEl) return;

  // [{date: "YYYY-MM-DD", market_value_eur, total_return_eur, growth_pct}], ascending by date.
  // growth_pct is a fraction (0.1234 is +12.34%), or null on a date portfolio's growth didn't
  // cover; Chart.js draws a null as a gap.
  const points = JSON.parse(dataEl.textContent);

  // Mirrors web/formatting.py's money/signed_money (UI-5): en grouping, a € prefix, a true
  // minus sign, and no sign on a value that displays as zero. Keep the two in step.
  const MINUS = "−";
  const formatEur = (value, { signed = false, digits = 2 } = {}) => {
    const magnitude = Math.abs(value).toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    const isZero = Number(magnitude.replace(/,/g, "")) === 0;
    const sign = isZero ? "" : value < 0 ? MINUS : signed ? "+" : "";
    return `${sign}€${magnitude}`;
  };

  // Mirrors web/formatting.py's pct: a fraction as a signed percentage, `0.0184` → `+1.84%`.
  // Axis ticks pass minDigits 0 so a step like 2.5% isn't padded to 2.50%.
  const formatPct = (value, { minDigits = 2 } = {}) => {
    const magnitude = Math.abs(value * 100).toLocaleString("en-US", {
      minimumFractionDigits: minDigits,
      maximumFractionDigits: 2,
    });
    const isZero = Number(magnitude.replace(/,/g, "")) === 0;
    const sign = isZero ? "" : value < 0 ? MINUS : "+";
    return `${sign}${magnitude}%`;
  };

  // The views the toggle switches between. Total return and growth are deltas, so they're
  // signed and their line is gain/loss colored; market value is a level, so it's neither.
  // Growth is time-weighted since inception: each point is the growth from the first priced day
  // to that date, so a shorter range crops the line rather than restarting it at zero.
  const VIEWS = {
    total_return_eur: {
      signed: true,
      label: "Portfolio total return over time",
      format: (value, digits) => formatEur(value, { signed: true, digits }),
    },
    market_value_eur: {
      signed: false,
      label: "Portfolio market value over time",
      format: (value, digits) => formatEur(value, { digits }),
    },
    growth_pct: {
      signed: true,
      label: "Portfolio growth since inception over time",
      format: (value, digits) => formatPct(value, { minDigits: digits }),
    },
  };
  const state = { view: "total_return_eur", months: 0 };

  // Semantic tokens, not brand tokens, so the chart follows the theme like the rest of the
  // page. Custom properties resolve their var() references, so these are real colors.
  const readColors = () => {
    const style = getComputedStyle(document.documentElement);
    const read = (name) => style.getPropertyValue(name).trim();
    return {
      gain: read("--gain"),
      loss: read("--loss"),
      line: read("--text"),
      grid: read("--border"),
      ticks: read("--text-muted"),
    };
  };
  let colors = readColors();

  // Undefined falls back to the dataset's own (neutral) borderColor.
  const segmentColor = (ctx) => {
    if (!VIEWS[state.view].signed) return undefined;
    return ctx.p0.parsed.y >= 0 && ctx.p1.parsed.y >= 0 ? colors.gain : colors.loss;
  };

  // The range counts back from the last data point, not from today, so a chart whose prices
  // have stopped updating still shows its final stretch. Day of month is clamped (31 Mar - 1M is
  // 28/29 Feb), and ISO strings compare correctly as plain strings.
  const rangeStart = (lastDate, months) => {
    const [year, month, day] = lastDate.split("-").map(Number);
    const target = new Date(Date.UTC(year, month - 1 - months, 1));
    const lastDay = new Date(
      Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)
    ).getUTCDate();
    target.setUTCDate(Math.min(day, lastDay));
    return target.toISOString().slice(0, 10);
  };

  const visiblePoints = () => {
    if (state.months === 0 || points.length === 0) return points;
    const start = rangeStart(points[points.length - 1].date, state.months);
    return points.filter((p) => p.date >= start);
  };

  const chart = new Chart(canvas, {
    type: "line",
    data: { labels: [], datasets: [{ data: [], borderWidth: 2 }] },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) =>
              ctx.parsed.y === null ? "—" : VIEWS[state.view].format(ctx.parsed.y, 2),
          },
        },
      },
      scales: {
        x: { grid: {}, ticks: { maxRotation: 0, autoSkipPadding: 24 } },
        y: {
          grid: {},
          ticks: {
            callback: (value) => VIEWS[state.view].format(value, 0),
          },
        },
      },
    },
  });

  // Rebuilds the chart from `state` and the current theme colors. A canvas doesn't restyle
  // itself, so this also runs when the theme changes. Only the first draw animates: morphing
  // one range or view into another, with a different number of points, draws stray lines.
  let firstRender = true;
  const render = () => {
    const shown = visiblePoints();
    const dataset = chart.data.datasets[0];
    chart.data.labels = shown.map((p) => p.date);
    dataset.data = shown.map((p) => p[state.view]);
    dataset.borderColor = colors.line;
    dataset.segment = { borderColor: segmentColor };
    // A lone point has no line to draw, so show it as a dot instead.
    dataset.pointRadius = shown.length === 1 ? 3 : 0;
    dataset.pointBackgroundColor = colors.line;
    for (const axis of [chart.options.scales.x, chart.options.scales.y]) {
      axis.grid.color = colors.grid;
      axis.ticks.color = colors.ticks;
    }
    canvas.setAttribute("aria-label", VIEWS[state.view].label);
    // update("none") doesn't cancel the first draw's animation if it is still running, and that
    // animation then finishes at the previous view's positions: a click in the first second left
    // the line drawn as the old series against the new axis. Stopping it first prevents that.
    if (!firstRender) chart.stop();
    chart.update(firstRender ? undefined : "none");
    firstRender = false;
  };
  render();

  // The view and range buttons ship hidden (they'd do nothing without this script).
  const controls = document.querySelector(".chart-controls");
  if (controls) {
    controls.hidden = false;
    controls.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      const group = button.closest("[role=group]");
      if (button.dataset.chartView) state.view = button.dataset.chartView;
      else if (button.dataset.chartRange) state.months = Number(button.dataset.chartRange);
      else return;
      for (const other of group.querySelectorAll("button")) {
        other.setAttribute("aria-pressed", String(other === button));
      }
      render();
    });
  }

  // The OS switching scheme (only visible with no manual choice, but harmless otherwise) or the
  // header toggle (static/theme.js).
  const redraw = () => {
    colors = readColors();
    render();
  };
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
  document.addEventListener("stakehold:themechange", redraw);
});
