// Show/hide for a password field (UI-12). Each toggle names its input with aria-controls and is
// rendered hidden, so without this file there's no button that does nothing; the field is an
// ordinary password input either way. aria-pressed carries the state for assistive tech, and the
// label says what a press will do.
document.querySelectorAll(".password-toggle").forEach(function (button) {
  const input = document.getElementById(button.getAttribute("aria-controls"));
  if (!input) {
    return;
  }
  button.hidden = false;
  button.addEventListener("click", function () {
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.setAttribute("aria-pressed", String(reveal));
    button.textContent = reveal ? "Hide" : "Show";
  });
});
