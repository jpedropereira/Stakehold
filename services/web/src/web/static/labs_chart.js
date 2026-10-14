document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("labs-chart");
  const dataEl = document.getElementById("labs-chart-data");
  if (!canvas || !dataEl) return;

  // labs_view.build_growth_payload: {period, dates, mix, assets: [{ticker, values}]}, each line
  // cumulative growth as a fraction (0 on the first date), or null when it doesn't cover the
  // period.
  const growth = JSON.parse(dataEl.textContent);

  // Mirrors web/formatting.py's pct: a signed percentage, a true minus sign, and no sign on a
  // value that displays as zero. Keep the two in step (as portfolio_chart.js's formatEur).
  const MINUS = "−";
  const formatPct = (fraction, digits) => {
    const magnitude = Math.abs(fraction * 100).toFixed(digits);
    const isZero = Number(magnitude) === 0;
    const sign = isZero ? "" : fraction < 0 ? MINUS : "+";
    return `${sign}${magnitude}%`;
  };

  // Theme colors and the redraw on a theme change: static/chart_theme.js.
  const theme = window.StakeholdChartTheme;
  let colors = theme.readColors();

  const toggles = document.querySelector(".labs-series");
  const boxes = toggles ? [...toggles.querySelectorAll("input[data-series]")] : [];
  // The asset whose box is hovered or focused, drawn in full ink above the others.
  let highlighted = null;

  // The mix first: Chart.js draws dataset 0 last, so it stays on top of the asset lines. Its
  // segments are gain/loss colored, as /portfolio's total return is (a growth is a gain or a
  // loss); the assets are thin and muted. Gold isn't used: it is the one accent, for controls,
  // and as a line on parchment it falls under the 3:1 a chart's marks need.
  const datasets = [];
  if (growth.mix) {
    datasets.push({ label: "Mix", data: growth.mix, borderWidth: 2.5, isMix: true });
  }
  for (const asset of growth.assets) {
    if (asset.values) datasets.push({ label: asset.ticker, data: asset.values, borderWidth: 1.25 });
  }

  const mixSegment = (ctx) =>
    ctx.p0.parsed.y >= 0 && ctx.p1.parsed.y >= 0 ? colors.gain : colors.loss;

  const chart = new Chart(canvas, {
    type: "line",
    data: { labels: growth.dates, datasets },
    options: {
      // The .chart-frame around the canvas sets the height (web.css), not the canvas's width.
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatPct(ctx.parsed.y, 2)}`,
          },
        },
      },
      scales: {
        x: { grid: {}, ticks: { maxRotation: 0, autoSkipPadding: 24 } },
        y: {
          // The 0% line is the start of every line, so it is drawn stronger than the grid.
          grid: { color: (ctx) => (ctx.tick.value === 0 ? colors.muted : colors.grid) },
          ticks: { callback: (value) => formatPct(value, 0) },
        },
      },
    },
  });

  // Restyles every line from the boxes, the highlight and the theme colors. A canvas doesn't
  // restyle itself, so this also runs when the theme changes. Only the first draw animates.
  let firstRender = true;
  const render = () => {
    const shown = new Set(boxes.filter((box) => box.checked).map((box) => box.dataset.series));
    for (const dataset of chart.data.datasets) {
      if (dataset.isMix) {
        dataset.borderColor = colors.text;
        dataset.segment = { borderColor: mixSegment };
        continue;
      }
      const isHighlighted = dataset.label === highlighted;
      dataset.hidden = !shown.has(dataset.label);
      dataset.borderColor = isHighlighted ? colors.text : colors.muted;
      dataset.borderWidth = isHighlighted ? 2 : 1.25;
      // A raised asset line draws above the other assets, still below the mix.
      dataset.order = isHighlighted ? -1 : 0;
    }
    if (chart.data.datasets[0]?.isMix) chart.data.datasets[0].order = -2;
    chart.options.scales.x.grid.color = colors.grid;
    for (const axis of [chart.options.scales.x, chart.options.scales.y]) {
      axis.ticks.color = colors.muted;
    }
    if (!firstRender) chart.stop();
    chart.update(firstRender ? undefined : "none");
    firstRender = false;
  };
  render();

  if (toggles) {
    toggles.hidden = false;
    toggles.addEventListener("change", render);
    const highlight = (box) => {
      const next = box && !box.disabled ? box.dataset.series : null;
      if (next === highlighted) return;
      highlighted = next;
      render();
    };
    for (const box of boxes) {
      const label = box.closest("label");
      label.addEventListener("mouseenter", () => highlight(box));
      label.addEventListener("mouseleave", () => highlight(null));
      box.addEventListener("focus", () => highlight(box));
      box.addEventListener("blur", () => highlight(null));
    }
  }

  theme.onThemeChange(() => {
    colors = theme.readColors();
    render();
  });
});
