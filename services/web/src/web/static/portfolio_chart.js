document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("returns-chart");
  const dataEl = document.getElementById("returns-chart-data");
  if (!canvas || !dataEl) return;

  const points = JSON.parse(dataEl.textContent);
  const style = getComputedStyle(document.documentElement);
  const forest = style.getPropertyValue("--forest").trim();
  const oxblood = style.getPropertyValue("--oxblood").trim();
  const hairline = style.getPropertyValue("--hairline").trim();

  const segmentColor = (ctx) =>
    ctx.p0.parsed.y >= 0 && ctx.p1.parsed.y >= 0 ? forest : oxblood;

  new Chart(canvas, {
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
        x: { grid: { color: hairline } },
        y: { grid: { color: hairline } },
      },
    },
  });
});
