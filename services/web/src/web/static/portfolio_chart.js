document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("returns-chart");
  const dataEl = document.getElementById("returns-chart-data");
  if (!canvas || !dataEl) return;

  const points = JSON.parse(dataEl.textContent);

  // Semantic tokens, not brand tokens, so the chart follows the theme like the rest of the
  // page. Custom properties resolve their var() references, so these are real colors.
  const readColors = () => {
    const style = getComputedStyle(document.documentElement);
    const read = (name) => style.getPropertyValue(name).trim();
    return {
      gain: read("--gain"),
      loss: read("--loss"),
      grid: read("--border"),
      ticks: read("--text-muted"),
    };
  };
  let colors = readColors();

  const segmentColor = (ctx) =>
    ctx.p0.parsed.y >= 0 && ctx.p1.parsed.y >= 0 ? colors.gain : colors.loss;

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: points.map((p) => p.date),
      datasets: [
        {
          data: points.map((p) => p.total_return_eur),
          borderWidth: 2,
          pointRadius: 0,
          segment: { borderColor: segmentColor },
        },
      ],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: colors.grid }, ticks: { color: colors.ticks } },
        y: { grid: { color: colors.grid }, ticks: { color: colors.ticks } },
      },
    },
  });

  // A canvas doesn't restyle itself, so re-read the tokens and redraw when the theme changes:
  // the OS switching scheme (only visible with no manual choice, but harmless otherwise) or the
  // header toggle (static/theme.js).
  const redraw = () => {
    colors = readColors();
    for (const axis of [chart.options.scales.x, chart.options.scales.y]) {
      axis.grid.color = colors.grid;
      axis.ticks.color = colors.ticks;
    }
    chart.update();
  };
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
  document.addEventListener("stakehold:themechange", redraw);
});
