// Lets Escape close a ticker's name tooltip (UX-1) without moving the pointer or focus, as WCAG
// 1.4.13 asks of content shown on hover or focus. CSS opens and closes the tooltip; this only
// sets data-dismissed on the open one, which web.css keeps closed until neither the pointer nor
// focus is on that ticker any more. Without JS the tooltip still works, it just can't be
// dismissed in place.
//
// A ticker inside a link, button or label has no tab stop of its own: its host opens it.
const HOSTS = "a, button, label";

function isOpen(ticker) {
  const host = ticker.closest(HOSTS);
  // :focus-within, since a label's focus is on the box inside it.
  return (
    ticker.matches(":hover, :focus") || (host !== null && host.matches(":hover, :focus-within"))
  );
}

document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape") {
    return;
  }
  document.querySelectorAll(".ticker").forEach(function (ticker) {
    if (isOpen(ticker)) {
      ticker.dataset.dismissed = "";
    }
  });
});

// Checked on the next task: during focusout the element still has focus.
function reopenLeft() {
  setTimeout(function () {
    document.querySelectorAll(".ticker[data-dismissed]").forEach(function (ticker) {
      if (!isOpen(ticker)) {
        delete ticker.dataset.dismissed;
      }
    });
  });
}

// Neither event bubbles; capture lets one listener on the document see every element's.
document.addEventListener("pointerleave", reopenLeft, true);
document.addEventListener("focusout", reopenLeft, true);
