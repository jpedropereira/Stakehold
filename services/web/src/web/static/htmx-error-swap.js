// htmx's default response handling only swaps 2xx/3xx responses into the target -- every form
// error path in this app (login, signup, accounts, transactions) deliberately re-renders the
// real error partial with the matching 4xx/5xx status code (routes.py's _render_form_error), so
// without this, htmx silently drops the response and a failed submit looks like nothing happened.
//
// Scoped to responses carrying the X-Form-Error header that only _render_form_error sets, so an
// unmapped 4xx/5xx (an uncaught exception, a bad gateway) still goes through htmx's normal error
// handling instead of having its real error page swapped into the form silently.
document.body.addEventListener("htmx:beforeSwap", function (event) {
  if (event.detail.xhr.getResponseHeader("X-Form-Error") === "true") {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});

// That normal error handling shows nothing on screen, though. So a request that failed in a way
// the app didn't render itself -- a crash, a branded error page, a lost connection -- gets a
// generic banner in its form's error slot (UI-12). The copy is base.html's
// #request-failed-banner template, server-rendered, so no message lives in this file. Requests
// that target anything other than a form's #form-errors (the theme toggle) are left alone.
// A section switch's request (UX-10) gets the banner in its section's error slot instead, under
// its heading: the section keeps what it showed, and the rest of the page is untouched.
function showRequestFailed(event) {
  const target = event.detail.target;
  const template = document.getElementById("request-failed-banner");
  if (!target || !template) {
    return;
  }
  const slot =
    target.id === "form-errors"
      ? target
      : target.hasAttribute("data-swap-region") && target.querySelector("[data-swap-error]");
  if (slot) {
    slot.replaceChildren(template.content.cloneNode(true));
  }
}

document.body.addEventListener("htmx:responseError", showRequestFailed);
document.body.addEventListener("htmx:sendError", showRequestFailed);
