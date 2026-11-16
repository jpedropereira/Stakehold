// Closes the header's account menu (UX-4) the way a dropdown is expected to close: on Escape,
// on a click outside it, and when focus leaves it. The menu is a <details>, so without JS it
// still opens and closes from its summary; this only adds the other ways out.
//
// A click inside (a theme button) leaves it open, so the change shows in place.
function openMenus() {
  return document.querySelectorAll(".account-menu[open]");
}

document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape") {
    return;
  }
  openMenus().forEach(function (menu) {
    menu.open = false;
    // Only take focus back if it was in the menu: Escape elsewhere shouldn't move it.
    if (menu.contains(document.activeElement)) {
      menu.querySelector("summary").focus();
    }
  });
});

document.addEventListener("click", function (event) {
  openMenus().forEach(function (menu) {
    if (!menu.contains(event.target)) {
      menu.open = false;
    }
  });
});

// relatedTarget is where focus went; null when it left the page or went nowhere focusable,
// which a click on the page background does too (the click handler covers that case).
document.addEventListener("focusout", function (event) {
  const menu = event.target.closest(".account-menu[open]");
  if (menu !== null && event.relatedTarget !== null && !menu.contains(event.relatedTarget)) {
    menu.open = false;
  }
});
