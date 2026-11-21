// Adds rows to /labs's mix form in place (LB-7). Without JS the form still works: its empty row
// adds one asset per submit, and each Remove button submits the form. Here "Add another asset"
// copies labs.html's <template> row, numbers its ids so each label still names its input, and
// gives it a Remove that only takes it off the page, since it was never submitted.
(function () {
  const form = document.querySelector("#labs-form");
  const template = document.querySelector("#labs-row-template");
  if (!form || !template) {
    return;
  }
  const rows = form.querySelector("[data-labs-rows]");
  const addButton = form.querySelector("[data-labs-add]");
  let added = 0;

  function renumber(row, suffix) {
    row.querySelectorAll("[id]").forEach(function (element) {
      element.id = element.id.replace("-new", "-" + suffix);
    });
    row.querySelectorAll("label[for]").forEach(function (label) {
      label.htmlFor = label.htmlFor.replace("-new", "-" + suffix);
    });
  }

  function removeButton(row) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-ghost labs-remove";
    button.textContent = "Remove";
    button.setAttribute("aria-label", "Remove this row");
    button.addEventListener("click", function () {
      row.remove();
      addButton.focus();
    });
    return button;
  }

  // A long mix scrolls in its own box (UX-8). The server makes a box that starts long a
  // labelled tab stop; one that becomes long here gets the same, so a keyboard can scroll it.
  function markScrolling() {
    if (rows.hasAttribute("tabindex") || rows.scrollHeight <= rows.clientHeight) {
      return;
    }
    rows.tabIndex = 0;
    rows.setAttribute("role", "region");
    rows.setAttribute("aria-label", "Mix rows");
  }

  addButton.addEventListener("click", function () {
    const row = template.content.firstElementChild.cloneNode(true);
    added += 1;
    renumber(row, "added-" + added);
    row.appendChild(removeButton(row));
    rows.appendChild(row);
    markScrolling();
    row.scrollIntoView({ block: "nearest" });
    row.querySelector("input[name='ticker']").focus();
  });
  addButton.hidden = false;
})();
