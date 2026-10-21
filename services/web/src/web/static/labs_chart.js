document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("labs-chart");
  const dataEl = document.getElementById("labs-chart-data");
  if (!canvas || !dataEl) return;

  // labs_view.build_growth_payload: {period, dates, mix, assets: [{ticker, values}], left_out},
  // each line cumulative growth as a fraction (0 on the first date), or null when it doesn't
  // cover the period. A late asset's Max line has null points before its first day.
  const growth = JSON.parse(dataEl.textContent);

  // Theme colors, the percentage format and the redraw on a theme change: static/chart_theme.js.
  const theme = window.StakeholdChartTheme;
  const formatPct = theme.formatPct;
  let colors = theme.readColors();

  const toggles = document.querySelector(".labs-series");
  const boxes = toggles ? [...toggles.querySelectorAll("input[data-series]")] : [];
  // The asset whose box or line is hovered (or box focused), drawn in full ink above the others.
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

  // Asset lines share one muted color (the palette has none to spare), so each shown one is
  // named by its ticker at its last point, in the figures' mono. Labels that would overlap are
  // pushed apart vertically, staying inside the plot's height; they sit in right padding that
  // render() sizes to the widest shown label.
  const LABEL_GAP = 6;
  const LABEL_SIZE = 12;
  const LABEL_LINE = LABEL_SIZE + 2;
  // How far, in px, the pointer can be from an asset line and still raise it.
  const HOVER_REACH = 12;
  const monoFamily = getComputedStyle(document.documentElement)
    .getPropertyValue("--font-mono")
    .trim();
  const labelFont = `${LABEL_SIZE}px ${monoFamily || "monospace"}`;

  const lastPoint = (meta, data) => {
    for (let i = data.length - 1; i >= 0; i--) {
      if (data[i] !== null) return meta.data[i];
    }
    return null;
  };

  const shownAssets = (chart) =>
    chart.data.datasets
      .map((dataset, index) => ({ dataset, index }))
      .filter(({ dataset, index }) => !dataset.isMix && chart.isDatasetVisible(index));

  const lineLabels = {
    id: "labsLineLabels",
    afterDatasetsDraw(chart) {
      const { top, bottom } = chart.chartArea;
      const labels = [];
      for (const { dataset, index } of shownAssets(chart)) {
        const point = lastPoint(chart.getDatasetMeta(index), dataset.data);
        if (!point) continue;
        labels.push({ text: dataset.label, x: point.x, y: point.y, color: dataset.borderColor });
      }
      if (!labels.length) return;
      labels.sort((a, b) => a.y - b.y);
      // Down from the top, then back up from the bottom, so a crowd at either edge stays
      // inside the plot. More labels than fit the height overflow at the bottom.
      for (let k = 1; k < labels.length; k++) {
        labels[k].y = Math.max(labels[k].y, labels[k - 1].y + LABEL_LINE);
      }
      labels[labels.length - 1].y = Math.min(labels[labels.length - 1].y, bottom);
      for (let k = labels.length - 2; k >= 0; k--) {
        labels[k].y = Math.min(labels[k].y, labels[k + 1].y - LABEL_LINE);
      }
      labels[0].y = Math.max(labels[0].y, top);
      for (let k = 1; k < labels.length; k++) {
        labels[k].y = Math.max(labels[k].y, labels[k - 1].y + LABEL_LINE);
      }
      const { ctx } = chart;
      ctx.save();
      ctx.font = labelFont;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      for (const label of labels) {
        ctx.fillStyle = label.color;
        ctx.fillText(label.text, label.x + LABEL_GAP, label.y);
      }
      ctx.restore();
    },
  };

  // How many dates fit under the plot. Chart.js's own skipping sizes them against a width that
  // leaves out the y axis, so at phone width the labels' padding made adjacent dates touch.
  // Every date is ISO, so one measures them all; the y axis is taken from the last layout, so
  // before the first one there is no cap.
  const DATE_GAP = 12;
  const dateLimit = (chart, canvasWidth) => {
    if (!chart.chartArea) return undefined;
    const { ctx } = chart;
    ctx.save();
    ctx.font = Chart.helpers.toFont(chart.scales.x.options.ticks.font).string;
    const dateWidth = ctx.measureText(growth.dates[0] ?? "").width;
    ctx.restore();
    const plot = canvasWidth - chart.chartArea.left - chart.options.layout.padding.right;
    return Math.max(2, Math.floor(plot / (dateWidth + DATE_GAP)));
  };

  const chart = new Chart(canvas, {
    type: "line",
    data: { labels: growth.dates, datasets },
    plugins: [lineLabels],
    options: {
      // The .chart-frame around the canvas sets the height (web.css), not the canvas's width.
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0 } },
      layout: { padding: { right: 0 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          // Chart.js's color squares match no line: every asset is the same muted color, and
          // the mix's changes along its length. Names carry it, the mix first.
          displayColors: false,
          // Only shown lines are listed (index mode skips hidden datasets and null points).
          itemSort: (a, b) =>
            Number(Boolean(b.dataset.isMix)) - Number(Boolean(a.dataset.isMix)) ||
            a.datasetIndex - b.datasetIndex,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatPct(ctx.parsed.y, 2)}`,
          },
        },
      },
      // A resize changes the plot's width, so the date count is capped again before the redraw.
      onResize: (chart, size) => {
        chart.options.scales.x.ticks.maxTicksLimit = dateLimit(chart, size.width);
      },
      // Hovering near a shown asset line raises it, as hovering its box does.
      onHover: (event, _active, chart) => {
        const near = chart
          .getElementsAtEventForMode(event, "index", { intersect: false }, false)
          .filter(({ datasetIndex, element }) => {
            return !chart.data.datasets[datasetIndex].isMix && Number.isFinite(element.y);
          })
          .map(({ datasetIndex, element }) => ({
            series: chart.data.datasets[datasetIndex].label,
            distance: Math.abs(element.y - event.y),
          }))
          .sort((a, b) => a.distance - b.distance)[0];
        setHighlight(near && near.distance <= HOVER_REACH ? near.series : null);
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

  // The room the end-of-line labels need: the widest shown ticker, or none.
  const labelRoom = () => {
    const shown = shownAssets(chart).map(({ dataset }) => dataset.label);
    if (!shown.length) return 0;
    chart.ctx.save();
    chart.ctx.font = labelFont;
    const widest = Math.max(...shown.map((text) => chart.ctx.measureText(text).width));
    chart.ctx.restore();
    return Math.ceil(widest) + LABEL_GAP * 2;
  };

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
    chart.options.layout.padding.right = labelRoom();
    chart.options.scales.x.ticks.maxTicksLimit = dateLimit(chart, chart.width);
    chart.options.scales.x.grid.color = colors.grid;
    for (const axis of [chart.options.scales.x, chart.options.scales.y]) {
      axis.ticks.color = colors.muted;
    }
    if (!firstRender) chart.stop();
    chart.update(firstRender ? undefined : "none");
    firstRender = false;
  };

  function setHighlight(series) {
    if (series === highlighted) return;
    highlighted = series;
    render();
  }

  render();
  // The labels are measured and drawn in the mono web font, which may load after the first
  // draw.
  document.fonts?.load(labelFont).then(render, () => {});

  canvas.addEventListener("mouseleave", () => setHighlight(null));

  if (toggles) {
    toggles.hidden = false;
    toggles.addEventListener("change", render);
    for (const box of boxes) {
      const label = box.closest("label");
      const raise = () => setHighlight(box.disabled ? null : box.dataset.series);
      label.addEventListener("mouseenter", raise);
      label.addEventListener("mouseleave", () => setHighlight(null));
      box.addEventListener("focus", raise);
      box.addEventListener("blur", () => setHighlight(null));
    }
  }

  theme.onThemeChange(() => {
    colors = theme.readColors();
    render();
  });
});
