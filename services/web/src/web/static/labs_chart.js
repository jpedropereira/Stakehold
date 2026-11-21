// Drawn on load and again when a period switch swaps the growth section in (UX-10).
window.StakeholdChartTheme.mount((root) => {
  const canvas = root.querySelector("#labs-chart");
  const dataEl = document.getElementById("labs-chart-data");
  if (!canvas || !dataEl) return;

  // labs_view.build_growth_payload: {period, dates, mix, assets: [{ticker, name, values}],
  // left_out, benchmark: {ticker, name, values, reason} or null}, each line cumulative growth as
  // a fraction (0 on the first date), or null when it doesn't cover the period. A late asset's
  // Max line has null points before its first day. A name is the instrument's full name, or
  // null when data-store has none.
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
  // The benchmark (RK-14) is a dashed ink line, on by default: the line the mix is read
  // against. The dash, not a color, sets it apart, for the reason above. A line's series is what
  // its checkbox names (an asset's ticker, or "__benchmark"), so a benchmark that is also in the
  // mix stays a line of its own. Derived, not stored on the dataset: a string property of our
  // own on a dataset left a toggled chart frozen mid-animation, where boolean flags don't.
  const BENCHMARK = "__benchmark";
  const seriesOf = (dataset) => (dataset.isBenchmark ? BENCHMARK : dataset.label);
  // Each line's full name (UX-3), for the tooltip, keyed like seriesOf. Kept here, not on the
  // dataset, for the reason above.
  const names = new Map();
  if (growth.benchmark && growth.benchmark.name) names.set(BENCHMARK, growth.benchmark.name);
  for (const asset of growth.assets) {
    if (asset.name) names.set(asset.ticker, asset.name);
  }
  const datasets = [];
  if (growth.mix) {
    datasets.push({ label: "Mix", data: growth.mix, borderWidth: 2.5, isMix: true });
  }
  if (growth.benchmark && growth.benchmark.values) {
    datasets.push({
      label: growth.benchmark.ticker,
      data: growth.benchmark.values,
      borderWidth: 1.5,
      borderDash: [6, 4],
      isBenchmark: true,
    });
  }
  for (const asset of growth.assets) {
    if (asset.values) {
      datasets.push({ label: asset.ticker, data: asset.values, borderWidth: 1.25 });
    }
  }

  const rank = (dataset) => (dataset.isMix ? 0 : dataset.isBenchmark ? 1 : 2);

  const mixSegment = (ctx) =>
    ctx.p0.parsed.y >= 0 && ctx.p1.parsed.y >= 0 ? colors.gain : colors.loss;

  // Asset lines share one muted color (the palette has none to spare), so each shown one is
  // named by its ticker at its last point, in the figures' mono. The ticker alone, not its full
  // name (UX-3): the labels' room comes out of the plot's width, and a name would take most of
  // it at phone width. The tooltip and the checkbox labels carry the names. Labels that would overlap are
  // pushed apart vertically, staying inside the plot's height; they sit in right padding that
  // render() sizes to the widest shown label.
  const LABEL_GAP = 6;
  const LABEL_SIZE = 12;
  const LABEL_LINE = LABEL_SIZE + 2;
  // How far, in px, the pointer can be from an asset line and still raise it.
  const HOVER_REACH = 12;
  // The space, in px, kept between a tooltip and the canvas's edges.
  const TOOLTIP_MARGIN = 8;
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
          // The mix first, then the benchmark, then the assets.
          itemSort: (a, b) => rank(a.dataset) - rank(b.dataset) || a.datasetIndex - b.datasetIndex,
          callbacks: {
            // "TICKER — Name: +12.34%" (UX-3). Chart.js doesn't wrap a tooltip line, so the name
            // is cut to what the canvas has room for, or left out when almost nothing fits.
            label: (ctx) => {
              const { dataset, chart } = ctx;
              const role = dataset.isBenchmark ? " (benchmark)" : "";
              const value = `: ${formatPct(ctx.parsed.y, 2)}`;
              const name = names.get(seriesOf(dataset));
              if (!name) return `${dataset.label}${role}${value}`;
              const tooltip = chart.tooltip.options;
              const padding = Chart.helpers.toPadding(tooltip.padding);
              chart.ctx.save();
              chart.ctx.font = Chart.helpers.toFont(tooltip.bodyFont).string;
              const rest = chart.ctx.measureText(`${dataset.label} — ${role}${value}`).width;
              const room = chart.width - padding.width - TOOLTIP_MARGIN - rest;
              const fitted = theme.fitText(chart.ctx, name, room);
              chart.ctx.restore();
              return fitted
                ? `${dataset.label} — ${fitted}${role}${value}`
                : `${dataset.label}${role}${value}`;
            },
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
            const dataset = chart.data.datasets[datasetIndex];
            return !dataset.isMix && !dataset.isBenchmark && Number.isFinite(element.y);
          })
          .map(({ datasetIndex, element }) => ({
            series: seriesOf(chart.data.datasets[datasetIndex]),
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
      dataset.hidden = !shown.has(seriesOf(dataset));
      if (dataset.isBenchmark) {
        // Below the mix, above every asset line, a raised one included.
        dataset.borderColor = colors.text;
        dataset.order = -1.5;
        continue;
      }
      const isHighlighted = seriesOf(dataset) === highlighted;
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
  }, canvas);
});
