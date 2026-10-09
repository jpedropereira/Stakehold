// Shows only the manual-entry fields that apply to the chosen transaction type, and the FX rate
// only for a non-EUR currency (UI-9). The mapping isn't kept here: the template marks each field
// with data-types (the types it applies to) and data-required-for, from routes.py's _FIELD_TYPES,
// and the FX field with data-hide-for-currency.
//
// A hidden field's controls are disabled, so they aren't submitted, and a value typed before a
// type switch comes back if the user switches back. `required` is set only on shown fields that
// the type needs. Without JS every field shows and nothing is required by the browser. Either
// way portfolio's own validation decides what's accepted.
(function () {
  const form = document.querySelector(".transaction-form");
  if (!form) {
    return;
  }
  const typeSelect = form.querySelector("#transaction_type");
  const currencyInput = form.querySelector("#native_currency");

  function words(value) {
    return value ? value.split(" ") : [];
  }

  function setShown(element, shown, required) {
    element.hidden = !shown;
    element.querySelectorAll("input, select, textarea").forEach(function (control) {
      control.disabled = !shown;
      control.required = shown && required;
    });
  }

  function update() {
    const type = typeSelect.value;
    const currency = currencyInput.value.trim().toUpperCase();

    form.querySelectorAll("[data-types]").forEach(function (element) {
      setShown(
        element,
        words(element.dataset.types).includes(type),
        words(element.dataset.requiredFor).includes(type),
      );
    });
    form.querySelectorAll("[data-hide-for-currency]").forEach(function (element) {
      const shown = currency !== element.dataset.hideForCurrency;
      setShown(element, shown, shown);
    });

    // A fieldset with nothing left to show (Instrument, for a deposit) goes too.
    form.querySelectorAll("fieldset").forEach(function (fieldset) {
      const fields = fieldset.querySelectorAll(".field");
      fieldset.hidden =
        fields.length > 0 &&
        Array.prototype.every.call(fields, function (field) {
          return field.hidden;
        });
    });
  }

  typeSelect.addEventListener("change", update);
  currencyInput.addEventListener("input", update);
  update();
})();
