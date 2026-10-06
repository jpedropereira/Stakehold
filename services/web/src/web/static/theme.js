// Applies a theme choice made through the header toggle without a reload (UI-3). The server
// stays the source of truth: POST /theme sets the cookie that base.html renders data-theme from
// on every later page load, and answers an htmx post with an HX-Trigger naming the saved
// choice, which this applies to the page already on screen. Without JS the toggle is a plain
// form post that redirects back, so this file only improves it.
//
// "stakehold:themechange" tells anything drawn from the theme tokens at runtime (the returns
// chart) to redraw; CSS-styled elements follow on their own.
document.body.addEventListener("stakehold:theme", function (event) {
  const theme = event.detail.value;
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = theme;
  } else {
    delete document.documentElement.dataset.theme;
  }

  document.querySelectorAll(".theme-toggle button[name=theme]").forEach(function (button) {
    button.setAttribute("aria-pressed", String(button.value === theme));
  });

  document.dispatchEvent(new CustomEvent("stakehold:themechange"));
});
