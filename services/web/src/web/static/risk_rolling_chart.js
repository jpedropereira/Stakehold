// Drawn on load and again when a switch swaps the ex-ante section in (UX-10).
window.StakeholdChartTheme.mount((root) => {
  // Every /risk rolling chart (RK-25): a canvas[data-rolling] names its data island,
  // risk_rolling_view's payload: {kind, label, dates, starts, n_obs, values, lower, upper}, one
  // entry per window, plotted at the window's end. A null value is a window below its minimum
  // (R9), drawn as a break in the line; its reason is in the notes under the chart.
  const canvases = root.querySelectorAll("canvas[data-rolling]");
  if (!canvases.length) return;

  // Theme colors, the percentage format, the soft fill and the redraw on a theme change:
  // static/chart_theme.js.
  const theme = window.StakeholdChartTheme;
  let colors = theme.readColors();

  // Agreed for RK-25: ink lines only. A volatility, a Sharpe or a correlation is not a gain or a
  // loss, so no forest or oxblood, and gold is out for the growth chart's reason (2.2:1 on
  // parchment). The Sharpe's ± 1 s.e. band is the muted text color, as the drawdown's fill is.
  const BAND_ALPHA = 0.16;

  // Mirrors web/formatting.py's `pct1` and `ratio`, as the summary under each chart prints
  // them: "pct1" a percentage to 1 decimal, "ratio" and "rho" 2 decimals, each unsigned unless
  // negative, with a true minus sign. Keep the two in step.
  const formatValue = (kind, value) => {
    const pct = kind === "pct1";
    const fixed = Math.abs(pct ? value * 100 : value).toFixed(pct ? 1 : 2);
    const sign = value < 0 && Number(fixed) !== 0 ? "−" : "";
    return `${sign}${fixed}${pct ? "%" : ""}`;
  };

  // As labs_drawdown_chart.js: Chart.js sizes its date ticks against a width that leaves out
  // the y axis, so the count is capped by the plot's real width.
  const DATE_GAP = 12;
  const dateLimit = (chart, canvasWidth, sample) => {
    if (!chart.chartArea) return undefined;
    const { ctx } = chart;
    ctx.save();
    ctx.font = Chart.helpers.toFont(chart.scales.x.options.ticks.font).string;
    const dateWidth = ctx.measureText(sample ?? "").width;
    ctx.restore();
    const plot = canvasWidth - chart.chartArea.left;
    return Math.max(2, Math.floor(plot / (dateWidth + DATE_GAP)));
  };

  const draw = (canvas, data) => {
    const band = data.lower !== null && data.upper !== null;
    const datasets = [
      { label: data.label, data: data.values, borderWidth: 2, spanGaps: false, order: 0 },
    ];
    if (band) {
      // The upper edge fills down to the lower one (dataset 2), drawn under the line.
      datasets.push(
        { label: "upper", data: data.upper, borderWidth: 0, fill: "+1", spanGaps: false, order: 1 },
        { label: "lower", data: data.lower, borderWidth: 0, fill: false, spanGaps: false, order: 1 },
      );
    }

    const y = {
      grid: {},
      ticks: {
        callback: (value) => formatValue(data.kind, value),
      },
    };
    if (data.kind === "rho") {
      // ρ's whole range, so a move from 0.9 to 0.95 reads as small as it is.
      y.min = -1;
      y.max = 1;
      y.ticks.stepSize = 0.5;
    } else if (data.kind === "pct1") {
      y.beginAtZero = true;
    }

    const chart = new Chart(canvas, {
      type: "line",
      data: { labels: data.dates, datasets },
      options: {
        // The .chart-frame around the canvas sets the height (web.css).
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        elements: { point: { radius: 0 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            filter: (item) => item.datasetIndex === 0,
            callbacks: {
              title: (items) => {
                const i = items[0].dataIndex;
                return `${data.starts[i]} to ${data.dates[i]}`;
              },
              label: (ctx) => {
                const i = ctx.dataIndex;
                let text = `${data.label}: ${formatValue(data.kind, ctx.parsed.y)}`;
                if (band && data.upper[i] !== null) {
                  text += ` ± ${formatValue("ratio", data.upper[i] - ctx.parsed.y)} (1 s.e.)`;
                }
                return [text, `${data.n_obs[i]} weekly returns`];
              },
            },
          },
        },
        onResize: (chart, size) => {
          chart.options.scales.x.ticks.maxTicksLimit = dateLimit(chart, size.width, data.dates[0]);
        },
        scales: { x: { grid: {}, ticks: { maxRotation: 0, autoSkipPadding: 24 } }, y },
      },
    });

    // Restyles from the theme colors; a canvas doesn't restyle itself. Only the first draw
    // animates.
    let firstRender = true;
    const render = () => {
      const [line, upper, lower] = chart.data.datasets;
      line.borderColor = colors.text;
      line.backgroundColor = colors.text;
      if (upper) {
        const fill = theme.translucent(colors.muted, BAND_ALPHA);
        upper.backgroundColor = fill;
        upper.borderColor = fill;
        lower.borderColor = fill;
      }
      chart.options.scales.x.ticks.maxTicksLimit = dateLimit(chart, chart.width, data.dates[0]);
      chart.options.scales.x.grid.color = colors.grid;
      // The zero line is the one reference every kind shares: no volatility, no excess return
      // per unit of risk, no correlation.
      chart.options.scales.y.grid.color = (ctx) =>
        ctx.tick.value === 0 ? colors.muted : colors.grid;
      for (const axis of [chart.options.scales.x, chart.options.scales.y]) {
        axis.ticks.color = colors.muted;
      }
      if (!firstRender) chart.stop();
      chart.update(firstRender ? undefined : "none");
      firstRender = false;
    };
    render();
    return render;
  };

  const renders = [];
  const drawCanvas = (canvas) => {
    // Once per canvas: a fold that opens again keeps its chart.
    if (Chart.getChart(canvas)) return;
    const dataEl = document.getElementById(canvas.dataset.rolling);
    if (!dataEl) return;
    renders.push(draw(canvas, JSON.parse(dataEl.textContent)));
  };

  // A chart in a closed fold (UX-7) has no box to size from, and its first animation would
  // play unseen, so it is drawn when its fold first opens. A fold the server opened (a chosen
  // correlation pair) draws now, like any chart in view.
  const waiting = new Set();
  for (const canvas of canvases) {
    const closed = canvas.closest("details:not([open])");
    if (!closed) {
      drawCanvas(canvas);
    } else if (!waiting.has(closed)) {
      waiting.add(closed);
      const onToggle = () => {
        // Draw only once every enclosing fold is open: a closed outer fold still has no box.
        if (!closed.open || closed.parentElement.closest("details:not([open])")) return;
        closed.removeEventListener("toggle", onToggle);
        for (const inner of closed.querySelectorAll("canvas[data-rolling]")) drawCanvas(inner);
      };
      closed.addEventListener("toggle", onToggle);
    }
  }

  // Every canvas under root goes with the section, so the first stands for them all.
  theme.onThemeChange(() => {
    colors = theme.readColors();
    for (const render of renders) render();
  }, canvases[0]);
});
