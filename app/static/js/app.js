/**
 * Shekel Budget App -- Client-Side JavaScript
 *
 * Minimal JS: HTMX handles most interactivity server-side.
 * This file provides the theme toggle and small UX helpers.
 */

// --- Service Worker Registration ---
// Registers the static-asset-only service worker shipped at
// /sw.js (served by app/routes/static_pass.py at the root scope
// so the worker can intercept fetches across the whole app, not
// just under /static/).  The worker NEVER caches HTML or JSON --
// see app/static/sw.js header for the financial-correctness
// invariant.  Guarded so older / privacy-restricted browsers
// without serviceWorker support degrade silently to plain network
// fetches; registration is deferred to the load event so the SW
// install never competes with the first paint of the page.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}

// --- Theme Toggle ---
(function() {
  var saved = localStorage.getItem('shekel-theme');
  if (saved) {
    document.documentElement.setAttribute('data-bs-theme', saved);
  }

  document.addEventListener('DOMContentLoaded', function() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var icon = btn.querySelector('i');

    function updateIcon() {
      var current = document.documentElement.getAttribute('data-bs-theme');
      if (icon) {
        icon.className = current === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
      }
    }

    updateIcon();

    btn.addEventListener('click', function() {
      var current = document.documentElement.getAttribute('data-bs-theme');
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-bs-theme', next);
      localStorage.setItem('shekel-theme', next);
      updateIcon();
      document.dispatchEvent(new CustomEvent('shekel:theme-changed', { detail: { theme: next } }));
    });
  });
})();

// Listen for HTMX gridRefresh events to reload the full page.
document.body.addEventListener("gridRefresh", function() {
  window.location.reload();
});

// Mark a transaction credit via HTMX, swapping just its cell.  Shared by
// the grid 'c' keyboard shortcut (below) and the command palette's Credit
// action (command_palette.js), so the route path is defined in one place
// (JS-19).  Defined at module scope so command_palette.js -- loaded after
// app.js -- can call it.
function markTxnCredit(txnId) {
  htmx.ajax('POST', '/transactions/' + txnId + '/mark-credit', {
    target: '#txn-cell-' + txnId,
    swap: 'innerHTML',
  });
}

// Reset the Add Transaction form whenever its modal opens.
var addModal = document.getElementById("addTransactionModal");

// Purchase tracking is expense-only, so the "Track individual purchases"
// row is hidden (and unchecked) whenever the modal's Type select is set
// to a non-expense type.  The expense type id is published on the row as
// a data attribute by the template.  Companion visibility applies to all
// types and is never hidden.
function syncAddEnvelopeVisibility() {
  if (!addModal) return;
  var row = addModal.querySelector("[data-adhoc-envelope-row]");
  var typeSel = addModal.querySelector('select[name="transaction_type_id"]');
  if (!row || !typeSel) return;
  var isExpense = typeSel.value === row.getAttribute("data-expense-type-id");
  row.classList.toggle("d-none", !isExpense);
  if (!isExpense) {
    const cb = row.querySelector('input[name="is_envelope"]');
    if (cb) cb.checked = false;
  }
}

if (addModal) {
  addModal.addEventListener("show.bs.modal", function() {
    var form = addModal.querySelector("form");
    if (form) form.reset();
    syncAddEnvelopeVisibility();
  });
  const addTypeSel = addModal.querySelector('select[name="transaction_type_id"]');
  if (addTypeSel) {
    addTypeSel.addEventListener("change", syncAddEnvelopeVisibility);
  }
}

// Inject CSRF token into all HTMX requests.
document.body.addEventListener("htmx:configRequest", function(event) {
  var token = document.querySelector('meta[name="csrf-token"]');
  if (token) {
    event.detail.headers["X-CSRFToken"] = token.getAttribute("content");
  }
});

// Show a loading spinner during HTMX requests (class-based).
document.body.addEventListener("htmx:beforeRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").classList.add("htmx-loading");
  }
});

document.body.addEventListener("htmx:afterRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").classList.remove("htmx-loading");
  }
});

// Dispose Bootstrap component instances on markup a swap is about to
// DESTROY (plan step X-f2-b, finding N-206's class one level up).
//
// Bootstrap keys every component instance in an element-keyed `Map` (its
// dom/data.js -- a Map, verified in the vendored bundle, NOT a WeakMap), so an
// element removed by an htmx swap while carrying an instance is retained by
// that map forever, with the whole subtree hanging off it.
//
// MEASURED, not theorised, via CDP `Performance.getMetrics` with a forced GC
// between samples: expanding the cash detail page's Balance history disclosure
// and refreshing the card five times retained 4,592 DOM nodes -- about 918 per
// cycle, the detached <tbody> of 46 rows.  Five refreshes WITHOUT expanding
// moved the counter by -2, so the retention is exactly the Collapse instance.
//
// The three types here are the ones this file ATTACHES to swappable content:
// the afterSwap handler below creates a Popover and a Tooltip per swap and has
// never disposed the previous ones, which is the same leak already live.
// Modal and Toast are deliberately absent: the modal mount leaves its markup
// in place on purpose, and a toast now disposes itself on `hidden.bs.toast`.
//
// Disposal is safe because none of the three holds state that must survive the
// swap: a Collapse's state IS the `show` class, which the replacement markup
// re-declares.
document.body.addEventListener("htmx:beforeSwap", function(event) {
  const target = event.detail && event.detail.target;
  if (!target || !target.querySelectorAll) {
    return;
  }
  // ``.collapse`` is the COLLAPSIBLE element (Bootstrap's data-api puts the
  // instance there, never on the button that toggles it); the two
  // ``data-bs-toggle`` selectors are the popover / tooltip anchors, which DO
  // carry their own.
  target.querySelectorAll(
    '.collapse, [data-bs-toggle="popover"], [data-bs-toggle="tooltip"]',
  ).forEach(function(el) {
    [bootstrap.Collapse, bootstrap.Popover, bootstrap.Tooltip].forEach(
      function(Component) {
        const instance = Component.getInstance(el);
        if (instance) {
          instance.dispose();
        }
      },
    );
  });
});

// Consolidated htmx:afterSwap handler -- save flash, popover close, focus restore.
document.body.addEventListener("htmx:afterSwap", function(event) {
  const el = event.detail.elt;

  // Save flash animation -- only for transaction cell saves.
  if (el && el.closest && el.closest('td.cell')) {
    el.classList.add("save-flash");
    el.addEventListener("animationend", function() {
      el.classList.remove("save-flash");
    }, { once: true });
  }

  // Close the full-edit popover when a CARD-INITIATED action swaps
  // content outside the card: the card's status buttons and Save target
  // the grid cell (#txn-cell-<id>), and landing that swap is the
  // designed save-and-close moment.  The predicate needs BOTH legs:
  //   - issuer inside the card: the balanceChanged chrome refreshes
  //     (tfoot balance row, subtotal tbodies) fire after EVERY entry
  //     mutation from elements outside the card, and the old
  //     target-only check made them slam the card shut while the user
  //     was still adding purchases.
  //   - the settled element outside the card: entry CRUD swaps the
  //     list inside the card and must keep it open.  event.target (the
  //     element this per-settled-element dispatch fired on) is the
  //     attached NEW node; detail.target is unreliable here because an
  //     outerHTML swap leaves it pointing at the DETACHED old node,
  //     where a containment test reads as "outside" (verified in a
  //     live-browser harness).
  // An entry-CRUD form is detached by its own outerHTML swap before
  // this handler runs, so contains(issuer) is false there too -- also
  // a keep-open, which is correct.  When the issuer is unknown
  // (requestConfig absent), keep the card open rather than guessing.
  if (typeof activePopover !== 'undefined' && activePopover) {
    const requestConfig = event.detail.requestConfig;
    const issuer = requestConfig && requestConfig.elt;
    if (issuer && activePopover.contains(issuer)
        && !activePopover.contains(event.target)) {
      closeFullEdit();
    }
  }

  // Re-initialize Bootstrap popovers/tooltips in swapped content.
  // event.target is the element each per-settled-element dispatch fired
  // on (htmx fires one afterSwap per settled element, OOB fragments
  // included); detail.target always names the request's PRIMARY swap
  // target, which misses OOB fragments like the entries-CRUD cell
  // re-render and would leave its tooltip badges uninitialized.
  var target = event.target || event.detail.target || event.detail.elt;
  if (target) {
    target.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(popEl) {
      if (!bootstrap.Popover.getInstance(popEl)) {
        new bootstrap.Popover(popEl);
      }
    });
    target.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(tipEl) {
      if (!bootstrap.Tooltip.getInstance(tipEl)) {
        new bootstrap.Tooltip(tipEl);
      }
    });

    // Auto-show modal partials swapped into the DOM.  Used by
    // server-rendered confirmation/preview modals (e.g. the
    // carry-forward preview at
    // app/templates/grid/_carry_forward_preview_modal.html).  Marked
    // by ``data-modal-auto-show`` on the .modal element.  After
    // hide, leave the markup in place so a subsequent hx-get swap
    // replaces it cleanly without orphaning Bootstrap state.
    target.querySelectorAll('[data-modal-auto-show]').forEach(function(modalEl) {
      bootstrap.Modal.getOrCreateInstance(modalEl).show();
    });

    // Auto-show toast partials swapped into the DOM -- the toast twin of
    // the modal handler above (plan step X-f1e3).  Used by the anchor
    // true-up's back-dated acknowledgement, which arrives out-of-band
    // into #anchor-ack-mount in base.html.  The DOMContentLoaded block
    // near the bottom of this file shows FLASH toasts, which exist only
    // on a full page render; a toast raised by an HTMX response has no
    // page load to be shown by, and without this it would swap in and
    // stay invisible (.toast is display:none until .show).
    //
    // Marked by ``data-toast-auto-show`` rather than matching .toast, so a
    // swap that happens to carry toast markup cannot re-show a toast the user
    // already dismissed.  Delay and autohide come from the element's own
    // data-bs-* attributes.
    //
    // **It searches the DOCUMENT, not the settled element, and that is plan
    // step X-f2-b.**  Scoping it to ``target`` worked only while the
    // acknowledgement's swap was ``hx-swap-oob="true"``: that is an outerHTML
    // swap, so the settled element WAS the toast's container and htmx fired an
    // ``afterSwap`` naming it.  Finding N-206 changed the swap to
    // ``beforeend:``, and htmx dispatches no per-element ``afterSwap`` for a
    // non-outerHTML out-of-band swap (``He``/``a`` in htmx.min.js insert the
    // nodes and fire ``htmx:oobAfterSwap`` on the REQUESTING element instead).
    // A container-scoped query therefore missed the appended toast entirely,
    // leaving it in the DOM and permanently invisible -- N-199's exact
    // symptom, reproduced in a live browser before it shipped.
    //
    // The un-instantiated guard is what keeps a document-wide query safe, and
    // it is the same shape the popover / tooltip initializers above use: a
    // toast that has already been shown carries a Bootstrap instance, and one
    // the user dismissed is GONE, because each toast now removes itself.
    //
    // Each toast DISPOSES AND REMOVES ITSELF once hidden (finding N-206).  The
    // acknowledgement appends rather than replacing its mount, so without this
    // the mount would accumulate one dead node per save; and Bootstrap keys
    // its instances off the element in a Data map that a plain remove() leaves
    // populated, with the autohide setTimeout still armed to fire hide() on a
    // detached node.  Disposing first and removing after clears both.
    document.querySelectorAll('[data-toast-auto-show]').forEach(function(toastEl) {
      if (bootstrap.Toast.getInstance(toastEl)) {
        return;
      }
      var toast = bootstrap.Toast.getOrCreateInstance(toastEl);
      toastEl.addEventListener('hidden.bs.toast', function() {
        toast.dispose();
        toastEl.remove();
      }, { once: true });
      toast.show();
    });
  }
});

// Designed error fragments (the marker-header convention, ruled
// 2026-07-11).  The htmx responseHandling config in base.html leaves
// 4xx/5xx bodies non-swapping because most are raw strings or JSON; a
// route that deliberately renders a DESIGNED error state -- the same
// partial the request targeted, re-rendered with field errors or a
// danger banner -- stamps the Shekel-Designed-Fragment header (helper:
// app/utils/error_fragments.py, where the two names are kept in sync)
// and this ONE listener swaps it.  An unhandled error document never
// carries the header, so crash pages stay non-swapping.  Replaces the
// per-surface target-id shims (tax_checkpoint.js, the swap block that
// lived in retirement_controls.js).
document.body.addEventListener("htmx:beforeSwap", function (event) {
  var detail = event.detail;
  if (detail && detail.xhr &&
      detail.xhr.getResponseHeader("Shekel-Designed-Fragment") === "1") {
    detail.shouldSwap = true;
  }
});

// Carry-forward modal: surface confirm-time validation failures.
// The Confirm button posts to /pay-periods/<id>/carry-forward; on a
// race-condition validation failure the route returns 4xx with a
// human-readable body.  HTMX defaults to NOT swapping on 4xx, so we
// project the response text into the modal's alert box ourselves.
// On success the route returns 200 + HX-Trigger=gridRefresh which
// reloads the page (handled above), tearing down the modal in the
// process.  A Confirm refused as OUT OF DATE is not projected here: its
// response is a designed fragment, the modal's content drawn again over
// the open modal (plan step credit_card:CC-5-4a-5, leaf 5c-2c-2, ruling
// R-CC128), which the designed-fragment listener above swaps -- its HTML
// is the redraw, never a message for the alert box.
document.body.addEventListener("htmx:responseError", function(event) {
  var elt = event.detail.elt;
  if (!elt || !elt.matches || !elt.matches("[data-carry-forward-confirm]")) {
    return;
  }
  if (event.detail.xhr &&
      event.detail.xhr.getResponseHeader("Shekel-Designed-Fragment") === "1") {
    return;
  }
  var modal = elt.closest(".modal");
  if (!modal) return;
  var alertBox = modal.querySelector("[data-carry-forward-error]");
  if (!alertBox) return;
  alertBox.textContent =
    (event.detail.xhr && event.detail.xhr.responseText)
      ? event.detail.xhr.responseText
      : "An error occurred while carrying forward.";
  alertBox.classList.remove("d-none");
});

// Close Add Transaction modal and reload after successful creation.
document.body.addEventListener("htmx:afterRequest", function(event) {
    if (!event.detail.successful) return;
    var form = event.detail.elt;
    if (form && form.hasAttribute('data-modal-auto-close')) {
        const modal = bootstrap.Modal.getInstance(
            document.getElementById('addTransactionModal')
        );
        if (modal) modal.hide();
        location.reload();
    }
});

// Delegated handlers for salary pages (CSP-compliant, replaces inline onclick/onchange).
document.addEventListener('click', function(e) {
    // Toggle target element (e.g. payroll-line form collapse)
    var toggleBtn = e.target.closest('[data-toggle-target]');
    if (toggleBtn) {
        const target = document.getElementById(toggleBtn.dataset.toggleTarget);
        if (target) target.classList.toggle('show');
        // If the button also has a reset attribute, reset the corresponding form.
        if (toggleBtn.hasAttribute('data-raise-reset')) {
            _resetRaiseForm();
        }
        if (toggleBtn.hasAttribute('data-line-reset')) {
            _resetLineForm();
        }
        return;
    }

    // Edit raise: populate the add form with existing data and switch to edit mode.
    var editBtn = e.target.closest('[data-raise-edit]');
    if (editBtn) {
        _populateRaiseForm(editBtn);
        return;
    }

    // Edit payroll line: populate the add form with existing data and switch to edit mode.
    var lineEditBtn = e.target.closest('[data-line-edit]');
    if (lineEditBtn) {
        _populateLineForm(lineEditBtn);
        return;
    }

    // Period select navigation (breakdown page)
    var navBtn = e.target.closest('[data-action="period-navigate"]');
    if (navBtn) {
        const sel = document.getElementById(navBtn.dataset.selectId);
        if (sel) window.location.href = sel.value;
        return;
    }
});

// Populate the raise form fields from the edit button's data attributes
// and switch the form action/hx-post to the update endpoint.
function _populateRaiseForm(editBtn) {
    var form = document.getElementById('raise-form');
    if (!form) return;

    var editUrl = editBtn.dataset.raiseEditUrl;
    form.action = editUrl;
    form.setAttribute('hx-post', editUrl);
    // Re-process hx-post after changing it dynamically.
    if (window.htmx) htmx.process(form);

    // Populate fields.
    var sel = form.querySelector('[name=raise_type_id]');
    if (sel) sel.value = editBtn.dataset.raiseTypeId;

    var month = form.querySelector('[name=effective_month]');
    if (month) month.value = editBtn.dataset.raiseMonth;

    var year = form.querySelector('[name=effective_year]');
    if (year) year.value = editBtn.dataset.raiseYear;

    var pct = form.querySelector('[name=percentage]');
    var flat = form.querySelector('[name=flat_amount]');
    if (pct) pct.value = editBtn.dataset.raisePercentage || '';
    if (flat) flat.value = editBtn.dataset.raiseFlat || '';

    var recur = form.querySelector('[name=is_recurring]');
    var isRecurring = editBtn.dataset.raiseRecurring === 'true';
    if (recur) recur.checked = isRecurring;

    // The end-year choice, restored from what is STORED: a recurring raise
    // with a terminal year selects "ends after" and fills it, one without
    // selects "no end year".  Editing therefore states the belief already
    // recorded, while a NEW raise opens with neither picked -- the
    // developer's 2026-09-05 ruling that a recurring raise is ASKED how
    // long it is believed rather than given a default.
    var storedEnd = editBtn.dataset.raiseTerminalYear || '';
    var endYear = form.querySelector('[name=terminal_year]');
    var modeYear = document.getElementById('raise-end-mode-year');
    var modeNone = document.getElementById('raise-end-mode-none');
    if (endYear) endYear.value = storedEnd;
    if (modeYear) modeYear.checked = isRecurring && storedEnd !== '';
    if (modeNone) modeNone.checked = isRecurring && storedEnd === '';
    _syncRaiseEndYearVisibility();

    // Optimistic-locking pin (commit C-18 / F-010): submit the
    // raise's version_id so the route handler can detect a stale
    // form before applying the update.  Cleared by _resetRaiseForm
    // when the form returns to add mode.
    var versionInput = form.querySelector('[name=version_id]');
    if (versionInput) versionInput.value = editBtn.dataset.raiseVersionId || '';

    // Change submit button icon to a check mark.
    var btn = document.getElementById('raise-submit-btn');
    if (btn) btn.innerHTML = '<i class="bi bi-check-lg"></i>';

    // Expand the form if collapsed.
    var formDiv = document.getElementById('add-raise-form');
    if (formDiv && !formDiv.classList.contains('show')) {
        formDiv.classList.add('show');
    }
}

// Reset a collapsible add/edit form back to add mode: restore the add
// action + hx-post, clear the fields and the optimistic-lock version pin,
// and reset the submit button label.  Shared by the raise and payroll-line
// forms, which differ only in their element ids and button markup (JS-10).
function _resetForm(formId, buttonId, buttonHtml) {
    var form = document.getElementById(formId);
    if (!form) return;

    var addUrl = form.dataset.addAction;
    if (addUrl) {
        form.action = addUrl;
        form.setAttribute('hx-post', addUrl);
        if (window.htmx) htmx.process(form);
    }

    form.reset();

    // Clear the optimistic-lock pin so a subsequent add submission
    // does not carry a stale version from a prior edit cycle.
    var versionInput = form.querySelector('[name=version_id]');
    if (versionInput) versionInput.value = '';

    var btn = document.getElementById(buttonId);
    if (btn) btn.innerHTML = buttonHtml;
}

// Show the end-year choice only for a recurring raise.  A one-time raise
// cannot carry one at all -- ck_salary_raises_terminal_year_only_on_a_
// recurring_raise -- so offering the control there would be offering a
// value the database refuses.
function _syncRaiseEndYearVisibility() {
    var recur = document.getElementById('raise-recurring');
    var group = document.getElementById('raise-end-year-group');
    var endYear;
    if (!recur || !group) return;
    group.hidden = !recur.checked;
    if (group.hidden) {
        // Clear what the group holds as it goes away.  A hidden input still
        // SUBMITS, so leaving "ends after 2031" behind after unchecking
        // Recur posts an end year on a one-time raise -- which the schema
        // refuses, over a control the owner can no longer see.
        group.querySelectorAll('input[name=raise_end_mode]').forEach(
            function (radio) { radio.checked = false; }
        );
        endYear = group.querySelector('[name=terminal_year]');
        if (endYear) endYear.value = '';
    }
}

// Reset the raise form to add mode (clear fields, restore action).  The
// reset returns both end-year radios to unchecked, because neither carries
// a `checked` attribute for form.reset() to restore -- which is what keeps
// "unanswered" distinguishable from "no end year" on the next add.
function _resetRaiseForm() {
    _resetForm('raise-form', 'raise-submit-btn', '<i class="bi bi-plus"></i>');
    _syncRaiseEndYearVisibility();
}

// The Recur checkbox reveals the end-year question.  A `change` listener
// rather than the click delegation above: clicking the LABEL forwards a
// second click to the input, and change fires once with the final state.
document.addEventListener('change', function(e) {
    if (!e.target.matches('#raise-recurring')) return;
    _syncRaiseEndYearVisibility();
});

// Populate the payroll-line form fields from the edit button's data attributes
// and switch the form action/hx-post to the update endpoint.
function _populateLineForm(editBtn) {
    var form = document.getElementById('line-form');
    if (!form) return;

    var editUrl = editBtn.dataset.lineEditUrl;
    form.action = editUrl;
    form.setAttribute('hx-post', editUrl);
    if (window.htmx) htmx.process(form);

    // Populate fields.
    var name = form.querySelector('[name=name]');
    if (name) name.value = editBtn.dataset.lineName;

    var kind = form.querySelector('[name=paycheck_line_kind_id]');
    if (kind) kind.value = editBtn.dataset.lineKindId;

    var method = form.querySelector('[name=calc_method_id]');
    if (method) {
        method.value = editBtn.dataset.lineMethodId;
        // Trigger change event so the label updater runs.
        method.dispatchEvent(new Event('change', { bubbles: true }));
    }

    var amount = form.querySelector('[name=amount]');
    if (amount) amount.value = editBtn.dataset.lineAmount || '';

    // The line's CADENCE, on the shared recurrence controls (plan step
    // salary:R15-c): the unit, the interval, the funding placement and the
    // per-month ceiling the route resolved for this row, or empty
    // attributes for a line with no rule ("Does not repeat" = every
    // paycheck).  The three dependent values are set BEFORE the unit's
    // change event fires, because recurrence_form.js's toggleFields reads
    // them as it re-links: it keeps a placement that is admissible for the
    // chosen unit and resets one that is not, and shows or disables the
    // ceiling box off the chosen offer -- so setting them afterwards would
    // either be overwritten or land on a control the script just disabled.
    // Setting a <select>'s value from script fires no change event, so the
    // one dispatch below is what re-links everything, exactly as the
    // calc-method prefill above does for its label.
    // The SPAN (plan step salary:R18-c) rides the same dispatch: the rule's
    // own start -- blank for a line with no rule, blank meaning the opening
    // payday -- and the bound's MODE are set BEFORE the unit's change event,
    // with the interval, placement and ceiling, because toggleFields reads
    // all of them as it re-links: it shows the span rows for a cadence and
    // hides and DISABLES them for "Does not repeat" (a hidden control still
    // posts; a disabled one does not), reveals and enables the one input the
    // chosen bound shape needs, and offers the nominal day's options off the
    // (unit, date) pair.  A change dispatched on the mode select itself
    // would re-enable it under "Does not repeat" and post a bound nothing
    // asked for (an adversarial review of this leaf), so there is exactly
    // one dispatch here, the unit's.  The bound's value and the nominal day
    // are filled AFTER it, into controls it has just enabled.
    var unit = form.querySelector('[name=recurrence_unit]');
    var interval = form.querySelector('[name=interval_n]');
    var placement = form.querySelector('[name=recurrence_placement]');
    var ceiling = form.querySelector('[name=max_per_month]');
    var startsOn = form.querySelector('[name=starts_on]');
    var endMode = form.querySelector('[name=recurrence_end_mode]');
    if (unit) {
        if (interval) interval.value = editBtn.dataset.lineInterval || '1';
        if (placement) placement.value = editBtn.dataset.linePlacementId || '';
        if (ceiling) ceiling.value = editBtn.dataset.lineMaxPerMonth || '';
        if (startsOn) startsOn.value = editBtn.dataset.lineStartsOn || '';
        if (endMode) endMode.value = editBtn.dataset.lineEndMode || 'never';
        unit.value = editBtn.dataset.lineUnitId || '';
        unit.dispatchEvent(new Event('change', { bubbles: true }));
    }
    var nominalDay = form.querySelector('[name=nominal_day]');
    if (nominalDay) nominalDay.value = editBtn.dataset.lineNominalDay || '';
    var endDate = form.querySelector('[name=end_date]');
    if (endDate) endDate.value = editBtn.dataset.lineEndDate || '';
    var maxOccurrences = form.querySelector('[name=max_occurrences]');
    if (maxOccurrences) maxOccurrences.value = editBtn.dataset.lineMaxOccurrences || '';

    var cap = form.querySelector('[name=annual_cap]');
    if (cap) cap.value = editBtn.dataset.lineCap || '';

    var target = form.querySelector('[name=target_account_id]');
    if (target) target.value = editBtn.dataset.lineTargetAccount || '';

    var inflEnabled = form.querySelector('[name=inflation_enabled]');
    if (inflEnabled) inflEnabled.checked = editBtn.dataset.lineInflationEnabled === 'true';

    var inflRate = form.querySelector('[name=inflation_rate]');
    if (inflRate) inflRate.value = editBtn.dataset.lineInflationRate || '';

    var inflMonth = form.querySelector('[name=inflation_effective_month]');
    if (inflMonth) inflMonth.value = editBtn.dataset.lineInflationMonth || '';

    // Optimistic-locking pin (commit C-18 / F-010): submit the
    // line's version_id so the route handler can detect a
    // stale form before applying the update.  Cleared by
    // _resetLineForm when the form returns to add mode.
    var versionInput = form.querySelector('[name=version_id]');
    if (versionInput) versionInput.value = editBtn.dataset.lineVersionId || '';

    // Change submit button text.
    var btn = document.getElementById('line-submit-btn');
    if (btn) btn.innerHTML = '<i class="bi bi-check-lg"></i> Update';

    // Expand the form if collapsed.
    var formDiv = document.getElementById('add-line-form');
    if (formDiv && !formDiv.classList.contains('show')) {
        formDiv.classList.add('show');
    }
}

// Reset the payroll-line form to add mode.
//
// form.reset() puts the cadence controls back on their rendered state --
// "Does not repeat", an interval of 1, an empty ceiling -- but the rows
// recurrence_form.js showed for an edited line stay shown and its inputs
// stay enabled until that script re-links off a change event, so one is
// dispatched here: a reset form must post what a fresh one posts (plan step
// salary:R15-c).
function _resetLineForm() {
    _resetForm('line-form', 'line-submit-btn', '<i class="bi bi-plus"></i> Add');
    var form = document.getElementById('line-form');
    var unit = form && form.querySelector('[name=recurrence_unit]');
    // form.reset() has put the span controls back on their rendered state
    // (a blank start, the bound's "never") by the time this fires, and
    // toggleFields hides and disables them with the rest for "Does not
    // repeat" (plan step salary:R18-c) -- one dispatch, the unit's.
    if (unit) unit.dispatchEvent(new Event('change', { bubbles: true }));
}

// Update payroll-line form labels when calc method changes.
document.addEventListener('change', function(e) {
    if (!e.target.matches('[data-action="update-line-labels"]')) return;
    var form = e.target.closest('form');
    if (!form) return;
    var amountInput = form.querySelector('[name=amount]');
    var label = form.querySelector('.amount-label');
    // Key off the option's semantic data-name ("percentage"/"flat"), not its
    // |title display text -- renaming the label or filter must not flip the
    // amount field's meaning (mirrors investment_form.js).
    var selectedOpt = e.target.selectedOptions[0];
    if (selectedOpt && selectedOpt.dataset.name === 'percentage') {
        if (amountInput) { amountInput.placeholder = 'e.g. 6 for 6%'; amountInput.step = '0.01'; }
        if (label) label.textContent = 'Amount (%)';
    } else {
        if (amountInput) { amountInput.placeholder = 'e.g. 500'; amountInput.step = '0.01'; }
        if (label) label.textContent = 'Amount ($)';
    }
});

// --- Keyboard Help Modal (? key) ---
document.addEventListener('keydown', function(e) {
  if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (document.querySelector('.modal.show')) return;
    const modal = document.getElementById('keyboardHelpModal');
    if (modal) {
      e.preventDefault();
      new bootstrap.Modal(modal).show();
    }
  }
});

// --- Keyboard activation for click-to-edit affordances ---
// Elements wired for click only (role="button" tabindex="0") -- the anchor
// balance cells and the calendar day cells -- must also activate on Enter /
// Space for keyboard users, or the role="button" is a lie.  Opt-in via
// data-keyboard-activate so this never hijacks Enter/Space on unrelated
// controls.  (The dashboard's #balance-display has its own handler in
// dashboard_pulse.js.)
document.addEventListener('keydown', function(e) {
  if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
  var el = e.target;
  if (!el || !el.matches
      || !el.matches('[data-keyboard-activate][role="button"][tabindex="0"]')) {
    return;
  }
  // preventDefault stops Space from scrolling; stopImmediatePropagation stops
  // the grid-navigation keydown handler (registered later on document) from
  // ALSO acting on this Enter -- otherwise activating the grid anchor cell
  // would also move the cell cursor or open a focused cell's editor.
  e.preventDefault();
  e.stopImmediatePropagation();
  el.click();
});

// --- Keyboard Navigation ---
// Tracks the focused cell by [row, col] in the grid table.
// Supports arrow keys, Tab, Enter, Escape, Space, Ctrl+Left/Right, Home, End.
(function() {
  var focusedRow = -1;
  var focusedCol = -1;
  var gridTable = null;

  function getGridTable() {
    if (!gridTable) gridTable = document.querySelector('.grid-table');
    return gridTable;
  }

  /** Return all data rows (tbody tr that are not banners/spacers/headers). */
  function getDataRows() {
    var table = getGridTable();
    if (!table) return [];
    return Array.from(table.querySelectorAll('tbody tr')).filter(function(tr) {
      return !tr.classList.contains('section-banner-income') &&
             !tr.classList.contains('section-banner-expense') &&
             !tr.classList.contains('spacer-row') &&
             !tr.classList.contains('group-header-row') &&
             !tr.classList.contains('item-header-row') &&
             !tr.classList.contains('subtotal-row') &&
             !tr.classList.contains('net-cash-flow-row');
    });
  }

  /** Return the number of data columns (excluding the sticky label column).
      The period header is the LAST thead row -- the C3 month band sits
      above it with one th per month, so counting the first row would
      undercount the columns. */
  function getColCount() {
    var table = getGridTable();
    if (!table) return 0;
    var headerRow = table.querySelector('thead tr:last-child');
    if (!headerRow) return 0;
    // Subtract 1 for the sticky label column
    return headerRow.children.length - 1;
  }

  /** Column index of the current pay period (0 = first data column),
      so keyboard navigation starts where the daily loop happens. */
  function currentPeriodCol() {
    var table = getGridTable();
    if (!table) return 0;
    var headerRow = table.querySelector('thead tr:last-child');
    if (!headerRow) return 0;
    var cur = headerRow.querySelector('th.current-period');
    if (!cur) return 0;
    var idx = Array.prototype.indexOf.call(headerRow.children, cur) - 1;
    return idx >= 0 ? idx : 0;
  }

  function clearFocus() {
    var prev = document.querySelector('.grid-table td.cell-focused');
    if (prev) prev.classList.remove('cell-focused');
  }

  /** Declare the opaque sticky chrome as scroll padding right before a
      scrollIntoView.  The grid scrolls inside .grid-wrapper, whose
      scrollport edges are painted over by the two-row sticky thead, the
      sticky tfoot balance row, and the sticky label column; without
      scroll-padding, `block:'nearest'` treats a cell hidden UNDER that
      chrome as already visible (no scroll at all) and aligns edge cells
      flush beneath it.  Measured per call because the chrome is not a
      constant: the thead grows when Carry Fwd buttons render.  The
      window gets the
      sticky navbar's height for the rare page-scroll leg.  CSSOM
      property setters are the CSP-sanctioned dynamic-styling pattern
      (style-src 'self' allows them; inline style attributes are
      blocked). */
  function syncScrollPadding() {
    var table = getGridTable();
    if (!table) return;
    var wrapper = table.closest('.grid-scroll-wrapper');
    if (wrapper) {
      const thead = table.querySelector('thead');
      const tfoot = table.querySelector('tfoot');
      // Measure the label column via its thead header: the first
      // `tbody .sticky-col` is the INCOME section banner, whose
      // colspan covers the whole table, and measuring it would set a
      // table-wide scroll-padding-left (verified in a live-browser
      // harness: 796px vs the header's 112px on the same table).
      const label = table.querySelector('thead .row-label-col');
      wrapper.style.scrollPaddingTop = (thead ? thead.offsetHeight : 0) + 'px';
      wrapper.style.scrollPaddingBottom = (tfoot ? tfoot.offsetHeight : 0) + 'px';
      wrapper.style.scrollPaddingLeft = (label ? label.offsetWidth : 0) + 'px';
    }
    var navbar = document.querySelector('.navbar.sticky-top');
    document.documentElement.style.scrollPaddingTop =
      (navbar ? navbar.offsetHeight : 0) + 'px';
  }

  /** Move the cell cursor.  skipReveal=true re-applies the highlight
      without scrolling -- used by the afterSwap focus-restore while the
      action card is open, where a wrapper scroll would close the card
      (grid_edit.js arms a wrapper 'scroll' -> closeFullEdit listener
      whenever the card is shown). */
  function setFocus(row, col, skipReveal) {
    var rows = getDataRows();
    var colCount = getColCount();
    if (rows.length === 0 || colCount === 0) return;

    // Clamp
    row = Math.max(0, Math.min(row, rows.length - 1));
    col = Math.max(0, Math.min(col, colCount - 1));

    clearFocus();
    focusedRow = row;
    focusedCol = col;

    // col+1 because first td is the sticky label
    var td = rows[row].children[col + 1];
    if (td) {
      td.classList.add('cell-focused');
      if (!skipReveal) {
        syncScrollPadding();
        td.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    }
  }

  function getFocusedCell() {
    var rows = getDataRows();
    if (focusedRow < 0 || focusedRow >= rows.length) return null;
    return rows[focusedRow].children[focusedCol + 1] || null;
  }

  document.addEventListener('keydown', function(e) {
    var table = getGridTable();
    if (!table) return;

    // Don't intercept when typing in form fields.
    // Quick edit and full edit keyboard handling is in grid_edit.js.
    var tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
      return;
    }

    // Don't interfere with modal or popover interactions
    if (document.querySelector('.modal.show')) return;
    if (typeof activePopover !== 'undefined' && activePopover) return;

    var rows = getDataRows();
    var colCount = getColCount();
    if (rows.length === 0 || colCount === 0) return;

    // Initialize focus if not set -- start in the current period's
    // column, where the daily loop happens.
    if (focusedRow < 0) {
      if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Tab', 'Enter', 'Home', 'End'].indexOf(e.key) !== -1) {
        e.preventDefault();
        setFocus(0, currentPeriodCol());
        return;
      }
      return;
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setFocus(focusedRow + 1, focusedCol);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setFocus(focusedRow - 1, focusedCol);
        break;
      case 'ArrowRight':
        if (e.ctrlKey || e.metaKey) {
          // Ctrl+Right: shift period window forward
          e.preventDefault();
          const rightArrow = document.querySelector('a[title="Later"]');
          if (rightArrow) rightArrow.click();
        } else {
          e.preventDefault();
          setFocus(focusedRow, focusedCol + 1);
        }
        break;
      case 'ArrowLeft':
        if (e.ctrlKey || e.metaKey) {
          // Ctrl+Left: shift period window backward
          e.preventDefault();
          const leftArrow = document.querySelector('a[title="Earlier"]');
          if (leftArrow) leftArrow.click();
        } else {
          e.preventDefault();
          setFocus(focusedRow, focusedCol - 1);
        }
        break;
      case 'Tab':
        e.preventDefault();
        if (e.shiftKey) {
          // Move backward
          if (focusedCol > 0) {
            setFocus(focusedRow, focusedCol - 1);
          } else if (focusedRow > 0) {
            setFocus(focusedRow - 1, colCount - 1);
          }
        } else {
          // Move forward
          if (focusedCol < colCount - 1) {
            setFocus(focusedRow, focusedCol + 1);
          } else if (focusedRow < rows.length - 1) {
            setFocus(focusedRow + 1, 0);
          }
        }
        break;
      case 'Enter': {
        // Enter opens the cell's edit surface: the anchored action
        // card for a transaction (.txn-open), or quick-create on an
        // empty cell (rebuild decision 3, docs/design/grid_audit.md).
        e.preventDefault();
        const cell = getFocusedCell();
        if (cell) {
          const clickable = cell.querySelector(
            '.txn-open[data-cell], .txn-empty-cell');
          if (clickable) clickable.click();
        }
        break;
      }
      case 'Escape':
        e.preventDefault();
        clearFocus();
        focusedRow = -1;
        focusedCol = -1;
        break;
      case ' ': {
        // Space marks the focused cell paid via its one-click check
        // button -- the button only renders on projected cells, the
        // exact precondition of the projected -> done transition, so
        // Space on any other cell is a deliberate no-op.
        e.preventDefault();
        const spaceCell = getFocusedCell();
        if (spaceCell) {
          const payBtn = spaceCell.querySelector('.paybtn');
          if (payBtn) {
            // Re-run setFocus on the cursor cell before mutating: the
            // user may have scrolled the cursor out of view since
            // selecting it, and a mark-paid must never happen with
            // zero visible feedback.
            setFocus(focusedRow, focusedCol);
            payBtn.click();
          }
        }
        break;
      }
      case 'c':
      case 'C': {
        // C marks the focused cell credit.  The template stamps
        // data-can-credit with the same predicate as the card's
        // Credit button (expense, projected, a row rather than a
        // transfer leg, not an envelope), so this cannot fire where
        // the button would not render.
        //
        // Ctrl+C / Cmd+C is the OS copy shortcut: bail before any
        // preventDefault so the browser still copies and no
        // mark-credit POST fires (mirrors the ctrlKey/metaKey branch
        // on the Arrow cases above).
        if (e.ctrlKey || e.metaKey) break;
        const creditCell = getFocusedCell();
        const creditable = creditCell
          && creditCell.querySelector('.txn-open[data-can-credit]');
        if (creditable) {
          e.preventDefault();
          // Same reveal-before-mutate rule as Space above.
          setFocus(focusedRow, focusedCol);
          markTxnCredit(creditable.dataset.txnId);
        }
        break;
      }
      case 'Home':
        e.preventDefault();
        setFocus(focusedRow, 0);
        break;
      case 'End':
        e.preventDefault();
        setFocus(focusedRow, colCount - 1);
        break;
    }
  });

  // Restore focus after HTMX swaps
  document.body.addEventListener('htmx:afterSwap', function() {
    if (focusedRow >= 0 && focusedCol >= 0) {
      // Re-apply focus after a short delay to allow DOM to settle.
      // Highlight-only while the action card is open: swaps fire
      // INSIDE the open card (lazy entries load, entry CRUD), and a
      // reveal scroll here would trip grid_edit.js's wrapper-scroll
      // listener and close the card mid-interaction.  Mirrors the
      // activePopover guard on the keydown handler above.
      setTimeout(function() {
        var popoverOpen = typeof activePopover !== 'undefined'
          && Boolean(activePopover);
        setFocus(focusedRow, focusedCol, popoverOpen);
      }, 50);
    }
  });

  // Allow clicking a cell to set focus
  document.addEventListener('click', function(e) {
    var table = getGridTable();
    if (!table) return;
    var td = e.target.closest('td.cell');
    if (td) {
      const tr = td.parentElement;
      const rows = getDataRows();
      const rowIdx = rows.indexOf(tr);
      if (rowIdx >= 0) {
        // Find column index (subtract 1 for sticky col)
        const colIdx = Array.from(tr.children).indexOf(td) - 1;
        if (colIdx >= 0) {
          focusedRow = rowIdx;
          focusedCol = colIdx;
          clearFocus();
          td.classList.add('cell-focused');
        }
      }
    }
  });
})();

// --- Toast Notifications ---
// Initialize Bootstrap toasts on page load.
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.toast').forEach(function(el) {
    new bootstrap.Toast(el).show();
  });

  // Initialize Bootstrap popovers (retirement info icons)
  document.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(el) {
    new bootstrap.Popover(el);
  });

  // Initialize Bootstrap tooltips (transaction status badges)
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(el) {
    new bootstrap.Tooltip(el);
  });
});
