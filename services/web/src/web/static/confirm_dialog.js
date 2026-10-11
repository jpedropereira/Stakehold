// Opens a delete's confirmation dialog in place of its confirmation page. Each Delete link names
// its dialog in data-confirm, and stays an ordinary link to the confirmation page, which is the
// whole flow without this file. The link's own query string fills the dialog form's hidden
// fields (the page's form posts the same names), and its data-confirm-summary fills the summary
// line, as text: it comes from broker data. Cancel and Esc close the dialog without posting, and
// focus goes back to the link that opened it.
const openedBy = new Map();

document.querySelectorAll("[data-confirm]").forEach(function (link) {
  const dialog = document.getElementById(link.dataset.confirm);
  if (!dialog || typeof dialog.showModal !== "function") {
    return;
  }
  link.addEventListener("click", function (event) {
    // A modified click opens the page in a new tab, as any link would.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    const form = dialog.querySelector("form");
    new URL(link.href).searchParams.forEach(function (value, name) {
      const field = form.elements.namedItem(name);
      if (field) {
        field.value = value;
      }
    });
    dialog.querySelector("[data-confirm-summary]").textContent =
      link.dataset.confirmSummary || "";
    dialog.querySelector("button:not([formmethod])").disabled = false;
    openedBy.set(dialog, link);
    dialog.showModal();
  });
});

document.querySelectorAll("dialog.confirm-dialog").forEach(function (dialog) {
  dialog.addEventListener("close", function () {
    const link = openedBy.get(dialog);
    if (link) {
      link.focus();
    }
  });
  // Disabled while the delete is in flight, so a second click can't post it twice. Cancel is a
  // dialog-method submit that posts nothing, so it's left alone.
  dialog.querySelector("form").addEventListener("submit", function (event) {
    if (event.submitter && event.submitter.getAttribute("formmethod") !== "dialog") {
      event.submitter.disabled = true;
    }
  });
});

// Back to this page from the next one: the browser may restore it as it was left, dialog open
// and button disabled.
window.addEventListener("pageshow", function (event) {
  if (event.persisted) {
    document.querySelectorAll("dialog.confirm-dialog[open]").forEach(function (dialog) {
      dialog.close();
    });
  }
});
