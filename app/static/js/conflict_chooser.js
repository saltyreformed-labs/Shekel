/**
 * Shekel Budget App -- Recurrence-conflict chooser bulk shortcuts (Loop B, P3).
 *
 * The chooser (recurrence_conflict_chooser.html) lets the user keep or
 * replace each hand-edited upcoming instance. This adds the two bulk
 * buttons: "Keep all" / "Follow the template for all" set every per-instance
 * radio to the matching value at once. A bill's account radios (ruling
 * plan#3230: "Keep on" / "Move to") carry the same two values, "use" being
 * Move, so each button sets both halves of every row. No money, no network --
 * it only flips radios the user could flip by hand.
 *
 * No-ops on every other page (the [data-conflict-chooser] root is absent).
 */

(function () {
  "use strict";

  const root = document.querySelector("[data-conflict-chooser]");
  if (!root) {
    return;
  }

  const buttons = root.querySelectorAll("[data-conflict-bulk]");
  for (let i = 0; i < buttons.length; i++) {
    buttons[i].addEventListener("click", function (event) {
      const value = event.currentTarget.getAttribute("data-conflict-bulk");
      // Radios carry value="keep"|"use"; select the ones matching the button.
      const radios = root.querySelectorAll(
        'input.btn-check[value="' + value + '"]',
      );
      for (let j = 0; j < radios.length; j++) {
        radios[j].checked = true;
      }
    });
  }
})();
