/**
 * Recurring bill form -- name the account box by the Type picked.
 *
 * Task plan#2931 (rulings plan#2926, plan#3225): the box says which account
 * the bill is "Paid from", or for income "Paid into".  The server renders the
 * label for the type the form opens on, marks the income <option> with
 * ``data-pays-into`` and puts both wordings on the label as data attributes,
 * so this script only chooses between them as the Type changes -- it states
 * no rule of its own.  With scripts off, the label keeps the wording for the
 * type the page opened on.
 */
(function () {
  var label = document.getElementById("account-label");
  var typeSelect = document.getElementById("transaction_type_id");
  if (!label || !typeSelect) return;

  function paysInto() {
    var opt = typeSelect.options[typeSelect.selectedIndex];
    return opt ? opt.hasAttribute("data-pays-into") : false;
  }

  function relabel() {
    label.textContent = paysInto() ? label.dataset.intoLabel : label.dataset.fromLabel;
  }

  typeSelect.addEventListener("change", relabel);
  relabel();
})();
