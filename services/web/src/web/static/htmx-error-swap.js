// htmx's default response handling only swaps 2xx/3xx responses into the target -- every form
// error path in this app (login, signup, accounts, transactions) deliberately re-renders the
// real error partial with the matching 4xx/5xx status code (routes.py's _render_form_error), so
// without this, htmx silently drops the response and a failed submit looks like nothing happened.
document.body.addEventListener("htmx:beforeSwap", function (event) {
  if (event.detail.xhr.status >= 400) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});
