// Drawn on load and again when a period switch swaps the growth section in (UX-10).
window.StakeholdChartTheme.mount((root) => {
  const canvas = root.querySelector("#labs-drawdown-chart");
  const dataEl = document.getElementById("labs-drawdown-data");
  if (!canvas || !dataEl) return;

  // labs_risk_view.build_drawdown_payload: {period, dates, values}, the mix's drawdown as a
  // fraction (0 at a high, below 0 under it), one value per growth date. A null is a day an
  // asset had no return (R5), drawn as a break in the line.
  const drawdown = JSON.parse(dataEl.textContent);

  // Theme colors, the percentage format and the redraw on a theme change: static/chart_theme.js.
  const theme = window.StakeholdChartTheme;
  const formatPct = theme.formatPct;
  let colors = theme.readColors();

  // Agreed for RK-9: an ink line with a soft fill down from 0%, not oxblood. A drawdown is a
  // distance below the mix's last high, not a loss figure, and an all-oxblood chart would read
  // as alarm. Gold is out for the reason the growth chart gives (2.2:1 on parchment).
  const FILL_ALPHA = 0.16;

  // As labs_chart.js: Chart.js sizes its date ticks against a width that leaves out the y
  // axis, so the count is capped by the plot's real width.
  const DATE_GAP = 12;
  const dateLimit = (chart, canvasWidth) => {
    if (!chart.chartArea) return undefined;
    const { ctx } = chart;
    ctx.save();
    ctx.font = Chart.helpers.toFont(chart.scales.x.options.ticks.font).string;
    const dateWidth = ctx.measureText(drawdown.dates[0] ?? "").width;
    ctx.restore();
    const plot = canvasWidth - chart.chartArea.left;
    return Math.max(2, Math.floor(plot / (dateWidth + DATE_GAP)));
  };

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: drawdown.dates,
      datasets: [
        { label: "Drawdown", data: drawdown.values, borderWidth: 2, fill: "origin", spanGaps: false },
      ],
    },
    options: {
      // The .chart-frame around the canvas sets the height (web.css).
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          callbacks: { label: (ctx) => `Drawdown: ${formatPct(ctx.parsed.y, 1)}` },
        },
      },
      onResize: (chart, size) => {
        chart.options.scales.x.ticks.maxTicksLimit = dateLimit(chart, size.width);
      },
      scales: {
        x: { grid: {}, ticks: { maxRotation: 0, autoSkipPadding: 24 } },
        y: {
          // A drawdown is never above 0, which is the mix at a high: the top of the plot. A mix
          // that never fell below its high would otherwise get Chart.js's default range down
          // to −100%, which reads as a crash; −1% keeps it a flat line on a small scale, and
          // any deeper drawdown widens the axis as usual.
          max: 0,
          suggestedMin: -0.01,
          grid: { color: (ctx) => (ctx.tick.value === 0 ? colors.muted : colors.grid) },
          // As many decimals as the tick step needs: a shallow drawdown steps by tenths of a
          // percent, and whole percents would print "0%" four times.
          ticks: {
            callback: (value, index, ticks) => {
              const step = ticks.length > 1 ? Math.abs(ticks[1].value - ticks[0].value) : 1;
              const digits = step * 100 >= 1 ? 0 : step * 100 >= 0.1 ? 1 : 2;
              return formatPct(value, digits);
            },
          },
        },
      },
    },
  });

  // Restyles from the theme colors; a canvas doesn't restyle itself. Only the first draw
  // animates.
  let firstRender = true;
  const render = () => {
    const [dataset] = chart.data.datasets;
    dataset.borderColor = colors.text;
    dataset.backgroundColor = theme.translucent(colors.muted, FILL_ALPHA);
    chart.options.scales.x.ticks.maxTicksLimit = dateLimit(chart, chart.width);
    chart.options.scales.x.grid.color = colors.grid;
    for (const axis of [chart.options.scales.x, chart.options.scales.y]) {
      axis.ticks.color = colors.muted;
    }
    if (!firstRender) chart.stop();
    chart.update(firstRender ? undefined : "none");
    firstRender = false;
  };

  render();

  theme.onThemeChange(() => {
    colors = theme.readColors();
    render();
  }, canvas);
});
