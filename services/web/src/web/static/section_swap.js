// A switch that swaps only its own section (UX-10; partials/_segmented.html's swap_attrs). htmx
// fetches the section, keeps the scroll (show:none) and records the choice in the address bar
// (hx-push-url). This file does the rest:
// - while the request runs, the section is aria-busy (web.css dims it on htmx-request)
// - once the new section is in, focus goes back to the control the reader used, which the swap
//   replaced: the option now selected in the same group, or the link for the same ticker
//   (Exclude X becomes "Include X again"), else the section's heading
// - the polite #swap-status (base.html) says what changed, in the section's own words
//   (data-swap-announce, server-rendered, so no copy lives here)
// A failed request leaves the old section in place, no longer busy; htmx-error-swap.js says why.

// Region id -> the data-swap-focus of the control that asked for it.
const pendingSwaps = new Map();

function swapRegion(event) {
  const target = event.detail.target;
  return target instanceof Element && target.hasAttribute("data-swap-region") ? target : null;
}

document.body.addEventListener("htmx:beforeRequest", function (event) {
  const region = swapRegion(event);
  if (!region) {
    return;
  }
  region.setAttribute("aria-busy", "true");
  pendingSwaps.set(region.id, event.detail.elt.dataset.swapFocus || "");
});

function endSwap(event) {
  const region = swapRegion(event);
  if (region) {
    region.removeAttribute("aria-busy");
    pendingSwaps.delete(region.id);
  }
}

["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(function (name) {
  document.body.addEventListener(name, endSwap);
});

function focusAfterSwap(region, key) {
  const same = [...region.querySelectorAll("[data-swap-focus]")].filter(function (el) {
    return key !== "" && el.dataset.swapFocus === key;
  });
  const target =
    same.find(function (el) {
      return el.getAttribute("aria-current") === "true";
    }) ||
    same[0] ||
    region.querySelector("h2, h3");
  if (!target) {
    return;
  }
  if (target.matches("h2, h3")) {
    target.setAttribute("tabindex", "-1");
  }
  target.focus({ preventScroll: true });
}

function announce(text) {
  const status = document.getElementById("swap-status");
  if (!status || !text) {
    return;
  }
  // Cleared first, so choosing the same thing twice is still announced.
  status.textContent = "";
  setTimeout(function () {
    status.textContent = text;
  }, 50);
}

// For an outerHTML swap htmx fires this on the new section itself, which has the old one's id.
document.body.addEventListener("htmx:afterSettle", function (event) {
  const region = event.target;
  if (!(region instanceof Element) || !pendingSwaps.has(region.id)) {
    return;
  }
  const key = pendingSwaps.get(region.id);
  pendingSwaps.delete(region.id);
  focusAfterSwap(region, key);
  announce(region.dataset.swapAnnounce);
});
