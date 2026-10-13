// Show/hide a holding's per-account breakdown on /portfolio from its ticker. Each toggle names
// its breakdown row with aria-controls and ships disabled with the row open, so without this file
// every breakdown is shown and there's no button that does nothing. Here the rows close, the
// buttons come alive, and aria-expanded carries the state for assistive tech.
document.querySelectorAll(".ticker-toggle").forEach(function (button) {
  const row = document.getElementById(button.getAttribute("aria-controls"));
  if (!row) {
    return;
  }
  row.hidden = true;
  button.setAttribute("aria-expanded", "false");
  button.disabled = false;
  button.addEventListener("click", function () {
    const open = row.hidden;
    row.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  });
});
