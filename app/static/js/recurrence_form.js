/**
 * Recurrence form -- link the three cadence controls, then show/hide the rest.
 * Used by both recurring-transaction and recurring-transfer forms.
 *
 * Plan step R7b-2.  The form authors two axes -- how often (interval + unit)
 * and which paycheck funds an occurrence (placement) -- instead of picking a
 * name from a closed pattern set.
 *
 * The offer set arrives whole in data-cadence-options, derived server-side from
 * the producer that would otherwise refuse the cadence
 * (app.services.recurrence.picker_model).  This file FILTERS it; it never
 * states which cadences exist, so it cannot drift from what the write door
 * accepts.  Filtering is what makes the refusal unreachable: a combination the
 * application cannot resolve is never selectable.
 *
 * Each offer also CARRIES whether the cadence anchors on a DAY of the month,
 * because that is a property of the resolver's own routing rather than of
 * anything visible here.  This file reads it; it does not restate it.
 *
 * Plan step R7c-b deleted the Day of Month and Month controls: a rule's first
 * occurrence carries its cycle's day AND its cycle's month, so ``starts_on``
 * says both (ruling R-R16).  What is left beside that date is the one question
 * the date cannot answer, how it is READ -- see ``syncRepeatsOn``.
 *
 * **Plan step R7c-c freed the INTERVAL and kept the FUNDING row on screen.**
 * The cadence was stored as one of eight pattern names until then, so not every
 * (interval, unit, placement) could be written -- any N paychecks, but only
 * 1 / 3 / 6 months, and only a ONE-month interval funded from the first
 * paycheck -- which is why there were two controls posting interval_n with
 * exactly one enabled, and why the funding row hid itself whenever the chosen
 * pair admitted a single placement.  Every positive interval is authorable on
 * every offered (unit, placement) pair now: one number box, and a funding row
 * that is always rendered and explains itself when there is nothing to choose
 * (plan ledger row D32).
 *
 * **Plan step salary:R15-c gave the controls a THIRD form, and made this file
 * re-runnable for it.**  The payroll-line form on the salary edit page
 * places recurrence_cadence_controls alone -- no #recurrence-fields (a payroll
 * line's first occurrence is derived, ruling R-SAL30), no end bound, no
 * preview -- so every element beyond the four cadence controls is
 * optional here, the container included.  And that form lives INSIDE the
 * #lines-section fragment that htmx swaps wholesale after every add, edit
 * and delete: the elements this closure bound at load are replaced, and a
 * script that ran once would leave the new form's interval box hidden and
 * disabled -- the next add would post no interval and be refused.  So the
 * body is a function, run once at load and again after any swap that carries
 * a fresh unit select; the old closure's listeners die with the old nodes.
 *
 * **Plan step credit_card:CC-6 added a unit only a transfer into a card takes**:
 * a repeat on each statement's due date.  The server states both facts this
 * file acts on -- which offers need a card and fix their interval on the wire
 * (needs_a_card, fixed_interval), and which destinations have due dates on the
 * destination's own option (data-has-due-dates) -- so this file decides neither;
 * the doors refuse whatever it gets wrong.  See syncUnitOffer.
 */
function initRecurrenceForm() {
  var unitSelect = document.getElementById('recurrence_unit');
  if (!unitSelect) return;

  var controls = document.getElementById('cadence-controls');
  var intervalWrap = document.getElementById('field-interval');
  var interval = document.getElementById('interval_n');
  // The per-month CEILING, the cadence's third value (plan step salary:R15-a).
  // Its row shows and its input is enabled only beside a unit whose
  // occurrences can repeat within a month -- the wire's
  // can_repeat_within_month, stated by the SERVER about the chosen offer --
  // and disabled otherwise, because a hidden input still submits and a
  // ceiling beside a monthly unit is the pair the write door refuses.
  var ceilingWrap = document.getElementById('field-max-per-month');
  var ceiling = document.getElementById('max_per_month');
  var placementWrap = document.getElementById('field-placement');
  var placementSelect = document.getElementById('recurrence_placement');
  var placementHelp = document.getElementById('placement-help');

  var container = document.getElementById('recurrence-fields');
  var startPeriod = document.getElementById('field-start-period');
  var startPeriodSelect = document.getElementById('start_period_id');
  var preview = document.getElementById('recurrence-preview');

  // The rule's FIRST OCCURRENCE (plan step R7c-b).  Its ROW shows and hides
  // with the rest of #recurrence-fields -- every cadence has one -- but the
  // input itself is enabled and disabled here, because a hidden input still
  // submits.
  var startsOn = document.getElementById('starts_on');

  // Whether the SERVER rendered it locked -- a loan payment, whose first
  // occurrence the app derives from the loan's first contractual installment.
  // Read ONCE at load, for the reason endBoundLocked is: the toggling below
  // turns the same control off and on and must never hand back one the server
  // locked.
  //
  // DISABLED, and reading the wrong property here DEFEATED the lock.  This
  // asked ``readOnly`` while the template emitted ``disabled``: the two never
  // agreed, so startsOnLocked was false on every locked form, syncStartsOn
  // immediately set ``disabled = false``, and a loan payment's derived date
  // became editable and submittable the instant the page settled.  The stale
  // reading was left behind by the ruling of 2026-08-15 that moved the control
  // from readonly to disabled -- readonly SUBMITS, and the update path reads
  // an absent start as "leave the stored one alone", which is the whole
  // meaning a locked control needs to convey.  Found by reading, not by the
  // suite: rendered HTML cannot tell a control a script re-enabled from one
  // that was never locked, which is what tests/manual/verify_recurrence_form.py
  // exists for.
  var startsOnLocked = startsOn !== null && startsOn.disabled;

  // A set of destination ids the transfer form's container carries, or []
  // when the attribute is absent or empty -- absent on the transaction form,
  // which has no destination; EMPTY on the edit form of a definition the
  // server pins to its loan (plan step R7d-f-5, ruling R-R79: its
  // destination control is disabled, so there is no choice to grade).  An
  // empty set leaves every behaviour below inert.  The edit form's sets are
  // computed for THAT definition by the server, the way its save door decides
  // the same edit (_loan_destination.loan_destination_locks_for_edit); the
  // standing payment's own rows arrive server-locked and are never
  // re-enabled here (startsOnLocked / endBoundLocked).
  //
  // Ids as strings, because that is what a <select>'s value is; comparing a
  // parsed number against an option value is how an off-by-type bug hides.
  function destinationIds(attribute) {
    if (!container) return [];
    var raw = container.getAttribute(attribute);
    if (!raw) return [];
    return raw.split(',').filter(function(id) { return id !== ''; });
  }

  // The destinations whose BOTH bounds the app derives: the loans holding no
  // active payment, so the definition being created or moved IS that loan's
  // standing payment the moment it is saved -- its first occurrence is the
  // loan's first contractual installment (plan step R7c-b) and it runs to
  // the payoff with no stop of its owner's (plan step R7d-f-3).  ONE set
  // since plan step R7d-g-2 (ruling R-R81): it was two -- every configured
  // loan for the start, this subset for the stop -- while the doors derived
  // the start for every loan destination; a SECOND transfer into a loan that
  // already holds a payment carries its owner's start and stop now, so its
  // loan is in no set and no row locks for it.  The set is the server's (one
  // value, _loan_destination.LoanDestinationLocks); this file tests
  // membership and decides nothing about loans.
  var loansWithoutPayment = destinationIds(
    'data-loan-account-ids-without-payment'
  );
  var destinationSelect = document.getElementById('to_account_id');
  // The sentence beside a unit the chosen destination cannot take.  Absent on
  // the two forms that never offer such a unit.
  var unitNeedsACardNote = document.getElementById('unit-needs-a-card');
  // The sentence beside a destination that is a card with a payment of its
  // own: a transfer into it is an extra payment (task plan#4046).  Absent on
  // the transaction form and on a card payment's own form.
  var extraPaymentNote = document.getElementById('to-account-extra-payment');
  // The controls naming where the money COMES FROM -- the transaction form's
  // account, the transfer form's source -- read by name so the preview and
  // its change listeners list them once (plan step pay_calendar:C18-a).
  var SOURCE_ACCOUNT_FIELDS = ['account_id', 'from_account_id'];
  // The transaction form's envelope box (ruling R-PC89): an envelope's row
  // is compared with the books on its paycheck's LAST day, a bill's on its
  // due day, so the box decides which dates saving would generate.  Absent
  // on the transfer form -- a transfer is never an envelope.
  var envelopeBox = document.getElementById('is_envelope');

  // The one question ``starts_on`` cannot answer: how it is READ.  A date
  // that is its own month's LAST day in a month shorter than 31 days could
  // mean that day or any larger one the month could not hold -- 2026-04-30 is
  // "the 30th" or "the last day of the month" -- and since plan step
  // recurrence:R8-c any month date can be read by its weekday too:
  // Tue 2026-11-17 is "the 17th" or "the third Tuesday".  ONE control, so
  // the form posts one reading.
  var repeatsOnWrap = document.getElementById('field-repeats-on');
  var repeatsOn = document.getElementById('repeats_on');

  // Days in a week: what shifts getDay()'s Sunday-0 to the server's Monday-0.
  var DAYS_PER_WEEK = 7;

  // The "Ends" control (plan step R7b-3): a mode select and the value inputs
  // its shapes need.  This file never states which shapes exist -- each
  // <option> names the control ITS shape needs in data-needs, so a shape plan
  // step R8 adds needs no edit here.
  var endBoundWrap = document.getElementById('field-end-bound');
  var endMode = document.getElementById('recurrence_end_mode');

  // Every control any shape can ask for, read off the offer set rather than
  // listed: an id here that the options never name would be a control nothing
  // can enable, and an option naming one that is missing would silently
  // enable nothing.
  var endValueWraps = endMode === null ? [] :
    Array.prototype.map.call(endMode.options, function(opt) {
      return document.getElementById(opt.getAttribute('data-needs') || '');
    }).filter(function(el) { return el !== null; });

  var options = [];
  try {
    options = JSON.parse(controls.getAttribute('data-cadence-options') || '[]');
  } catch (_err) {
    options = [];
  }

  // In-flight preview fetch.  A rapid field change aborts the previous
  // request; otherwise the LAST response to ARRIVE wins and a slow earlier
  // fetch can clobber the preview with stale dates.
  var previewAbortController = null;

  function unitId() {
    return unitSelect.value;
  }

  // What the interval box says, or null when it says nothing.
  //
  // **It defaulted an empty box to 1 until plan step R7c-c, and that default
  // is now a claim the save contradicts.**  Freeing the interval made the box
  // clearable for every unit, and clearing it beside a chosen cadence is
  // REFUSED at the submission (validate_authorable_cadence) rather than
  // silently stored as 1 -- so previewing twelve monthly dates for a box the
  // user has just emptied would show a schedule no save can produce, on the
  // one surface whose whole contract is "what saving would produce".
  //
  // Null rather than a guess, and fetchPreview says so with the SAME sentence
  // it uses for no cadence at all: "choose how often this repeats" is exactly
  // what is missing either way.  The server still bounds the value it is sent
  // (require_positive_interval); this is the preview's own read, not a
  // validation.
  function currentInterval() {
    var n = parseInt(interval.value, 10);
    return Number.isNaN(n) ? null : n;
  }

  // The offer chosen right now: the (unit, placement) pair whose server-stated
  // facts the calendar detail rows are shown from.  Read from the offer set
  // rather than inferred, because both facts belong to the WHOLE pair: "months
  // funded from the first paycheck" anchors on a paycheck and reads no day of
  // the month, while "months funded from the covering paycheck" does.
  //
  // The INTERVAL left this lookup at plan step R7c-c, with the closed pattern
  // set: which intervals an offer takes is its unit's to say (fixed_interval), so
  // it selects no offer and changing it changes no fact here.
  function currentOption(id, placementId) {
    var matches = options.filter(function(o) {
      return String(o.unit_id) === String(id) &&
             String(o.placement_id) === String(placementId);
    });
    return matches.length ? matches[0] : null;
  }

  // The placements authorable for this UNIT.
  //
  // **It was keyed on the (unit, interval) PAIR until plan step R7c-c**, and
  // the pair was the closed pattern set's doing: it stored "every 1 month
  // funded from the first paycheck" and had no quarterly twin, so keying on
  // the unit alone would have offered what the write door refused.  With the
  // interval free the MONTH unit admits both placements at every interval, and
  // the dependency is gone with the fusion that created it -- which is plan
  // ledger row D32's defect ceasing to exist rather than being warned about.
  //
  // DISTINCT placement ids, and the de-duplication is still load-bearing: the
  // help text below swaps on whether a unit admits fewer than two, so a
  // duplicate would read as a choice the user does not have.
  function placementsFor(id) {
    var found = [];
    options.forEach(function(o) {
      if (String(o.unit_id) !== String(id)) return;
      var value = String(o.placement_id);
      if (found.indexOf(value) === -1) found.push(value);
    });
    return found;
  }

  // Whether only a transfer into a configured card may repeat by this unit: the
  // wire's needs_a_card, stated by the SERVER about the offer.
  function unitNeedsACard(id) {
    return options.some(function(o) {
      return String(o.unit_id) === String(id) && o.needs_a_card;
    });
  }

  // Whether the destination the user has CHOSEN carries ``attribute``: a fact
  // the server put on its <option> -- data-has-due-dates, from the predicate
  // the doors refuse with, or data-has-a-payment, from the card's payment.
  function chosenDestinationHas(attribute) {
    if (!destinationSelect) return false;
    var chosenDestination = destinationSelect.options[destinationSelect.selectedIndex];
    return !!chosenDestination && chosenDestination.hasAttribute(attribute);
  }

  // Offer a unit that needs a card exactly while the chosen destination is one.
  //
  // **A unit already CHOSEN stays chosen and on screen when the destination moves
  // off a card, beside a sentence saying why it cannot be saved.**  Moving the
  // selection is the alternative, and every place it could go is worse: the first
  // entry is "Does not repeat", whose save DELETES the rule and sweeps its future
  // rows, and any other names a cadence nobody chose.  A selected option that was
  // hidden and disabled would post nothing, and read as "leave the stored cadence
  // alone" beside a destination it cannot pay.  So nothing here changes what the
  // user chose: the save door refuses the pair in plain words and writes nothing,
  // and choosing another unit retires the option like any other.
  function syncUnitOffer() {
    var chosenId = unitId();
    var hasDueDates = chosenDestinationHas('data-has-due-dates');
    Array.prototype.forEach.call(unitSelect.options, function(opt) {
      if (!unitNeedsACard(opt.value)) return;
      var mine = hasDueDates || opt.value === chosenId;
      opt.hidden = !mine;
      opt.disabled = !mine;
    });
    if (unitNeedsACardNote) {
      unitNeedsACardNote.classList.toggle(
        'd-none', hasDueDates || !unitNeedsACard(chosenId)
      );
    }
  }

  // Say a transfer into a card with a payment of its own is an extra payment,
  // exactly while one is chosen.  The sentence and the set of cards are the
  // server's; this tests the chosen option and nothing else.
  function syncExtraPaymentNote() {
    if (!extraPaymentNote) return;
    extraPaymentNote.classList.toggle(
      'd-none', !chosenDestinationHas('data-has-a-payment')
    );
  }

  // What the box said before a unit fixed it, handed back when that unit is left.
  var intervalBeforeFixed = null;

  // Hold the interval box at the one interval the chosen offer takes (the wire's
  // fixed_interval), or hand it back.  READ-ONLY rather than disabled, because a
  // disabled box posts nothing and the save refuses a cadence with no interval.
  function syncFixedInterval(fixed) {
    if (fixed !== null) {
      if (intervalBeforeFixed === null) intervalBeforeFixed = interval.value;
      interval.value = String(fixed);
    } else if (intervalBeforeFixed !== null) {
      interval.value = intervalBeforeFixed;
      intervalBeforeFixed = null;
    }
    interval.readOnly = fixed !== null;
    // A read-only box looks like any other in this Bootstrap, so it says so.
    interval.classList.toggle('bg-body-secondary', fixed !== null);
  }

  // Enable the placements this unit admits, keep the selection on one of them,
  // and say which of the two states the user is in.
  //
  // **The row is no longer HIDDEN when a unit admits one placement** (plan step
  // R7c-c, developer ruling 2026-08-16 on plan ledger row D32).  Hiding it is
  // how a bill's funding rule came to change with nothing on screen saying so:
  // the select is reassigned below whenever the previous choice is not
  // admissible, which is correct and was invisible.  ONE cadence still admits
  // one -- paychecks, where the placement is inert because a payday resolves to
  // its own paycheck either way -- and for it the row renders with that one
  // option and the help text explains why there is nothing to choose.  It was
  // two until plan step R8-a admitted the year unit's deferring reading.
  //
  // Both sentences come from data-* attributes the SERVER rendered; this file
  // states no copy, the same rule syncStartsOnHelp follows.
  function syncPlacements(id) {
    var allowed = placementsFor(id);
    var stillValid = false;
    Array.prototype.forEach.call(placementSelect.options, function(opt) {
      var mine = allowed.indexOf(opt.value) !== -1;
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (mine && opt.value === placementSelect.value) stillValid = true;
    });
    if (!stillValid && allowed.length) placementSelect.value = allowed[0];
    if (!placementHelp) return;
    var text = placementHelp.getAttribute(
      allowed.length < 2 ? 'data-fixed-text' : 'data-choice-text'
    );
    if (text) placementHelp.textContent = text;
  }

  // Whether the server rendered the "Ends" control locked -- a loan payment,
  // whose closing bound the app DERIVES from the loan's projected payoff.
  // Read ONCE, at load, because the toggling below turns the same controls off
  // and on and must never hand back one the server disabled: a locked form
  // that posted a bound would state a stop the next loan edit silently
  // overwrites.
  var endBoundLocked = endMode !== null && endMode.disabled;

  // The mode the user had chosen before a loan destination locked the row on
  // the CREATE form, so deselecting the loan hands the control back where it
  // was; null while the row is the user's.  A locked row shows a BLANK box
  // under the locked help sentence -- the same render an edit form's locked
  // row has when nothing is derived yet, and nothing is: the payment does
  // not exist.  Leaving "Never" showing beside "set from the loan's projected
  // payoff" would contradict itself (developer, 2026-09-12).
  var endModeBeforeLock = null;

  // Whether the date box has focus, from its own focus and blur events.
  // ``document.activeElement`` cannot say: in Chromium it is not the box during
  // the ``change`` of a digit that moves on to the next segment (measured
  // 2026-11-05), though no blur fires.
  var dateBoxFocused = false;

  // The "repeating on" choice a date refused while the box had focus; see
  // syncRepeatsOn.
  var readingPassedOver = null;

  // Show and enable only the input the chosen "Ends" shape needs, and disable
  // the whole control when the definition does not repeat -- or when the
  // chosen destination is a loan whose payment this would be (plan step
  // R7d-f-3), where the app derives the stop and the door refuses one stated
  // anyway.  Disabled, so a locked row posts NOTHING: the door reads an
  // absent bound as the unbounded rule a loan payment carries.
  //
  // Disabling rather than only hiding, because a hidden control still SUBMITS
  // -- the defect class plan step R7b-2 shipped twice and the browser pass
  // caught (tests/manual/verify_recurrence_form.py).  A stale date left in a
  // box the user has moved off would otherwise reach the door beside a mode
  // that does not name it.
  function syncEndBound(repeating) {
    if (!endMode || endBoundLocked) return;
    var derived = repeating && destinationDerivesTheBounds();
    if (derived && endModeBeforeLock === null) {
      endModeBeforeLock = endMode.selectedIndex;
      endMode.selectedIndex = -1;
    } else if (!derived && endModeBeforeLock !== null) {
      endMode.selectedIndex = endModeBeforeLock;
      endModeBeforeLock = null;
    }
    endMode.disabled = !repeating || derived;
    var chosen = endMode.options[endMode.selectedIndex];
    var needs = repeating && !derived && chosen
      ? chosen.getAttribute('data-needs')
      : '';
    endValueWraps.forEach(function(wrap) {
      var mine = wrap.id === needs;
      wrap.classList.toggle('d-none', !mine);
      Array.prototype.forEach.call(
        wrap.querySelectorAll('input'),
        function(input) { input.disabled = !mine; }
      );
    });
  }

  // The pay-period <select> belongs to the NON-REPEATING case alone since
  // plan step R7b-4: it says which period the single Transfer a one-time
  // transfer stands for lands in.  It used to be shown for BOTH cases with
  // its label swapped -- "First paycheck" when repeating, "Pay period"
  // otherwise -- because one control meant two things; the repeating meaning
  // is the "Starts on" date now, so the control has one label (server-rendered)
  // and one job.
  //
  // DISABLED as well as hidden, and that is the load-bearing half: a hidden
  // control still SUBMITS, so a user who picks a period and then chooses a
  // cadence would post a period the recurrence has no use for -- straight into
  // the route's ownership check and, on a crafted payload, into a column this
  // step is retiring.  It is the defect class plan step R7b-2 shipped twice
  // and only the browser pass caught (tests/manual/verify_recurrence_form.py).
  function syncStartPeriod(repeating) {
    if (!startPeriod) return;
    startPeriod.classList.toggle('d-none', repeating);
    if (startPeriodSelect) startPeriodSelect.disabled = repeating;
  }

  // The "Starts on" box hides with #recurrence-fields rather than on its own
  // -- every cadence has a first occurrence -- but hiding is not enough:
  // a hidden input SUBMITS, and the same browser pass that caught the
  // pay-period select caught this one posting an empty date on a
  // "Does not repeat" save.  The server drops the key either way now, and
  // this is the affordance half: a control the user cannot see does not
  // speak.  Never touched when the server rendered it READONLY (a loan
  // payment, whose date the app derives) -- re-enabling it here would hand
  // back a control the server locked.
  function syncStartsOn(repeating) {
    if (!startsOn || startsOnLocked) return;
    startsOn.disabled = !repeating || destinationDerivesTheBounds();
  }

  // Whether the destination the user has CHOSEN is in ``ids``.  Always false
  // when the list is empty -- a form with no destination control at all (the
  // transaction template's), or the edit form of a definition the server
  // pins to its loan.
  function destinationIsOneOf(ids) {
    if (!destinationSelect || ids.length === 0) return false;
    return ids.indexOf(destinationSelect.value) !== -1;
  }

  // Whether the chosen destination is one whose bounds the app derives: a
  // loan holding no payment yet, so this would be it.  The browser's half of
  // a rule the server states: this form offers every active account, so a
  // recurring loan payment can be created here or an existing transfer moved
  // onto a loan (plan step R7d-f-5) -- and asking the user for a date the
  // route is going to replace is the defect LOAN_PAYMENT_BOUND_IS_DERIVED
  // closes one path over, and the door refuses a stop stated for such a loan
  // whatever this file does (ruling R-R60).  ONE question for both rows
  // since plan step R7d-g-2; the two functions it replaced asked it of two
  // sets that differed only while the doors derived a second transfer's
  // start (ruling R-R81).
  function destinationDerivesTheBounds() {
    return destinationIsOneOf(loansWithoutPayment);
  }

  // Word the "Starts on" help for whichever of the two the row is saying.
  //
  // The COPY is the template's, carried on the row as data attributes, for the
  // reason every other string here is: a second wording in this file is one
  // that can disagree with the server's, and the server renders the same two
  // sentences for a locked and an unlocked edit form.
  function syncStartsOnHelp() {
    var help = document.getElementById('starts-on-help');
    if (!help || startsOnLocked) return;
    var which = 'data-open-text';
    if (destinationDerivesTheBounds()) {
      which = 'data-locked-text';
    } else if (unitNeedsACard(unitId())) {
      // For this unit the date is the first day the repeat covers, not its first
      // occurrence, which the card's terms decide.
      which = 'data-due-date-text';
    }
    var text = help.getAttribute(which);
    if (text) help.textContent = text;
  }

  // Word the "Ends" help the same way, for the same reason: both sentences
  // are the template's, carried on the row, and the server renders the same
  // two for a locked and an unlocked edit form.
  function syncEndBoundHelp() {
    var help = document.getElementById('end-bound-help');
    if (!help || endBoundLocked) return;
    var derived = destinationDerivesTheBounds();
    var text = help.getAttribute(
      derived ? 'data-locked-text' : 'data-open-text'
    );
    if (text) help.textContent = text;
  }

  // Where ``value`` (an ISO date string) sits, or null when it is not a date at
  // all: ``place`` is the key an option's ``data-places`` is written under,
  // "<month's last day>-<day>" (the last day is day 0 of the NEXT month), and
  // ``weekday`` a Python ``date.weekday()`` index (0 = Monday), which keys a
  // weekday option's phrase -- JavaScript's getDay() counts from Sunday.
  // Parsing the date and shifting that index are the only calendar work in this
  // file: which readings a date allows, which week it sits in and the DAY a rule
  // fires on are the server's, and so is any amount.
  function calendarFacts(value) {
    var parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    if (!parts) return null;
    var year = parseInt(parts[1], 10);
    var month = parseInt(parts[2], 10);
    var day = parseInt(parts[3], 10);
    return {
      place: new Date(year, month, 0).getDate() + '-' + day,
      weekday: (new Date(year, month - 1, day).getDay() + DAYS_PER_WEEK - 1) %
        DAYS_PER_WEEK
    };
  }

  // The JSON object an option's attribute holds, as the server wrote it.
  function optionTable(opt, attribute) {
    try {
      return JSON.parse(opt.getAttribute(attribute) || '{}');
    } catch (_err) {
      return {};
    }
  }

  // Show the "repeating on" control wherever the chosen date can be read more
  // than one way, and enable exactly the readings it allows.
  //
  // The rules are the server's, looked up rather than restated: each option's
  // ``data-places`` holds what its producer (offerable_nominal_days,
  // offerable_weekday_readings) answers at every place a date can sit in a
  // month, and an option reads the date exactly when its place is there.  A
  // weekday option's answer is the date's week, which with the weekday keys the
  // phrase the server wrote in ``data-labels`` ("the third Tuesday").  The copy
  // is the template's and the option set is fixed, so this file states no
  // wording and no domain -- and the write door refuses any pair it gets wrong,
  // which is what keeps this an affordance rather than a rule.
  //
  // ``hasDayCoordinate`` is the offer's has_day_of_month_coordinate, which is
  // keyed on the UNIT -- never the (unit, placement) pair's "is a ROW dated
  // from that day" (the wire's schedules_on_day_of_month until plan step
  // recurrence:R5-a removed it with the Due Day row), which answers a
  // different question.  Passing the
  // wrong one MOVED MONEY: they disagree for Monthly First, whose occurrences
  // are days of the month even though its rows are dated from the paycheck, so
  // this cleared and disabled a control the server had rendered enabled.  The
  // update door reads the reading off the same presence key as starts_on, so
  // changing only "Funded from" on a "last day of every month" rent posted the
  // date without the day, wrote nominal_day = NULL, and moved every later
  // occurrence to the 30th for good.
  //
  // DISABLED as well as hidden, for the reason every other control here is: a
  // hidden select still SUBMITS, and a reading left behind beside a date it
  // does not fit is exactly what the CHECKs refuse.  The selection is cleared
  // with an option the new date does not allow, because the control never shows
  // or posts a reading the date cannot carry; a weekday reading the new date
  // DOES allow keeps its place and takes the new date's words ("the third
  // Tuesday" becomes "the second Tuesday").
  //
  // Only a date clears it.  With no date to read -- a unit without a day of a
  // month, a loan destination that derives the bounds, a box holding no complete
  // date -- nothing has answered the question, so the choice waits in the
  // select, disabled and posting nothing, and comes back with the control, as a
  // typed ceiling does and as the "Ends" shape does after a lock.  Clearing it
  // there read as a refusal: one move away and back turned "the third Tuesday"
  // into the 17th on the save.
  //
  // A date in a focused box may only be passing through: Chromium fires
  // ``change`` for each digit that leaves a complete date (measured 2026-11-05),
  // so Nov 24 typed as 12/29/2026 is Jan 24 for a keystroke, outside its month's
  // last seven days, where Dec 29 is inside its own.  So a choice refused while
  // the box has focus is cleared as any other is, and remembered, and a later
  // date that allows it brings it back.  Leaving the box forgets it.
  function syncRepeatsOn(hasDayCoordinate) {
    if (!repeatsOn || !repeatsOnWrap) return;
    var facts = hasDayCoordinate ? calendarFacts(startsOn && startsOn.value) : null;
    var any = false;
    Array.prototype.forEach.call(repeatsOn.options, function(opt) {
      if (opt.value === '') return;
      var answer = facts === null ? undefined : optionTable(opt, 'data-places')[facts.place];
      var mine = answer !== undefined;
      if (mine && opt.hasAttribute('data-labels')) {
        opt.textContent = optionTable(opt, 'data-labels')[answer + '-' + facts.weekday];
      }
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (mine) any = true;
      if (facts === null) return;
      if (mine && opt.value === readingPassedOver) {
        opt.selected = true;
        readingPassedOver = null;
      } else if (!mine && opt.selected) {
        if (dateBoxFocused) readingPassedOver = opt.value;
        repeatsOn.selectedIndex = 0;
      }
    });
    repeatsOnWrap.classList.toggle('d-none', !any);
    if (!startsOnLocked) repeatsOn.disabled = !any;
  }

  // The value is NOT cleared with the row: switching to a monthly unit and
  // back should not lose a typed ceiling, and the update door reads an ABSENT key (a disabled control) as
  // "leave the stored one alone" and drops a stored ceiling itself where the
  // saved unit cannot hold it.
  function syncCeiling(canRepeatWithinMonth) {
    if (!ceilingWrap || !ceiling) return;
    ceilingWrap.classList.toggle('d-none', !canRepeatWithinMonth);
    ceiling.disabled = !canRepeatWithinMonth;
  }

  function toggleFields() {
    var id = unitId();
    syncUnitOffer();
    syncExtraPaymentNote();

    // An EMPTY value is "Does not repeat", the only non-recurring option on
    // either form since plan step R2e-3 retired the ONCE pattern.
    if (!id) {
      intervalWrap.classList.add('d-none');
      placementWrap.classList.add('d-none');
      syncFixedInterval(null);
      interval.disabled = true;
      // DISABLED with the interval, not merely hidden.  This was the one
      // control in the file that broke the file's own rule -- a hidden control
      // still SUBMITS, which is the defect class plan step R7b-2 shipped twice
      // -- and it went unnoticed because the write door pops the placement on
      // the no-cadence branch, so the stray key was absorbed one layer down by
      // a guard written for something else.  Closed at plan step R7c-c.
      placementSelect.disabled = true;
      // Absent on the payroll-line form, which places no calendar detail row
      // for the container to wrap (plan step salary:R15-c).
      if (container) container.classList.add('d-none');
      syncEndBound(false);
      syncStartPeriod(false);
      syncStartsOn(false);
      syncRepeatsOn(false);
      syncCeiling(false);
      fetchPreview();
      return;
    }

    intervalWrap.classList.remove('d-none');
    interval.disabled = false;
    placementWrap.classList.remove('d-none');
    // Re-enabled BEFORE syncPlacements, which turns individual OPTIONS off and
    // on: a disabled <select> posts nothing whatever its options say, so the
    // order here is what makes the chosen placement reach the door.
    placementSelect.disabled = false;
    syncPlacements(id);

    if (container) container.classList.remove('d-none');

    // Whether this cadence lands on a DAY of the month is a fact the SERVER
    // stated about the chosen pair, never inferred here.  Inferring it is what
    // an earlier draft of this file did -- it read "the interval control is a
    // free number box" as "this cadence has no day of the month", which was
    // true of every cadence offered then, is true of NONE now that every unit
    // uses the free box, and was never the same fact either way.
    var chosen = currentOption(id, placementSelect.value);
    syncFixedInterval(chosen !== null ? chosen.fixed_interval : null);
    // The "repeating on" control asks whether occurrences land on a day of the
    // month at all -- keyed on the unit; see syncRepeatsOn.
    var hasDayCoordinate =
      chosen !== null && chosen.has_day_of_month_coordinate;
    syncCeiling(chosen !== null && chosen.can_repeat_within_month);

    syncStartPeriod(true);
    syncStartsOn(true);
    // The "repeating on" control rides with the date: a derived first
    // occurrence brings its own reading (the loan's contract day), so a
    // control the user could still touch would state a reading the route is
    // about to replace.
    syncRepeatsOn(hasDayCoordinate && !destinationDerivesTheBounds());
    syncStartsOnHelp();

    if (endBoundWrap) {
      endBoundWrap.classList.remove('d-none');
    }
    syncEndBound(true);
    syncEndBoundHelp();

    fetchPreview();
  }

  // Whether a control in the "Starts on" row states a value the preview must
  // carry.  A control the FORM turned off states nothing; one the SERVER
  // locked states the value the save will use.  See fetchPreview, which asks
  // one more thing of the "repeating on" control: whether its OPTION is off.
  function statesAStartValue(el) {
    if (!el) return false;
    return (startsOnLocked || !el.disabled) && el.value !== '';
  }

  function fetchPreview() {
    if (!preview) return;

    // A cadence is a unit AND a count, so a missing either is the same state
    // and gets the same sentence.  The copy matches the pre-script render in
    // _recurrence_fields.html verbatim, which is why it is written once here.
    var id = unitId();
    var everyN = currentInterval();
    if (!id || everyN === null) {
      preview.innerHTML = '<small class="text-muted">Choose how often this repeats to see upcoming dates</small>';
      return;
    }

    var previewUrl = preview.getAttribute('data-preview-url');
    if (!previewUrl) return;

    var params = new URLSearchParams();
    params.set('recurrence_unit', id);
    params.set('interval_n', String(everyN));
    if (placementSelect.value) params.set('recurrence_placement', placementSelect.value);

    // The FIRST OCCURRENCE and the day a clamped one MEANT, sent whenever the
    // "Starts on" row states them.
    //
    // ``disabled`` alone is NOT that question, and reading it as if it were
    // killed the preview outright on the one definition whose schedule matters
    // most.  A control the FORM turned off states nothing -- previewing a
    // value the save would not carry is a preview of something else -- but a
    // control the SERVER locked states the DERIVED value, which is exactly
    // what its preview should walk from.  A loan payment's row renders
    // ``disabled`` since the 2026-08-15 readonly->disabled ruling, so skipping
    // every disabled control left its preview reading "No preview for this
    // cadence" forever.  ``startsOnLocked`` is read once at load and tells the
    // two apart.
    //
    // The "repeating on" choice rides on the same test because it is the
    // second half of one row: the server disables it with the date,
    // and the preview must show "Apr 30, May 31, Jun 30" rather than "Apr 30,
    // May 30" for a month-end loan payment as much as for a typed one.  An
    // empty value is the same day of the month, which the endpoint reads from
    // the key's absence.  A reading waiting for the control to come back (see
    // syncRepeatsOn) states nothing, and its option is what says so: on a row
    // the server locked, ``disabled`` is true whichever cadence is chosen.
    if (statesAStartValue(startsOn)) {
      params.set('starts_on', startsOn.value);
    }
    if (statesAStartValue(repeatsOn) &&
        !repeatsOn.options[repeatsOn.selectedIndex].disabled) {
      params.set('repeats_on', repeatsOn.value);
    }

    // The per-month ceiling, sent exactly when the save would carry it: an
    // enabled control with a value.  A disabled one (a monthly unit) states
    // nothing, and an empty one is "no ceiling", which the endpoint reads
    // from the key's absence.
    if (ceiling && !ceiling.disabled && ceiling.value) {
      params.set('max_per_month', ceiling.value);
    }

    // The closing bound, as the SAME three controls the save posts: the mode
    // that names the shape, and the one value input that shape enables.
    //
    // The MODE is what makes this a bound at all.  Sending only the values
    // leaves the endpoint composing "never" -- it dispatches on the mode, and
    // ``NeverEnds`` reads neither input -- so a rule the user has just bounded
    // previews as unbounded, on the one surface whose contract is "what
    // saving would produce".  Two adversarial reviews of plan step R7b-3
    // measured exactly that, on a form whose own comment claimed the
    // opposite.
    if (endMode && !endMode.disabled) {
      params.set('recurrence_end_mode', endMode.value);
    }

    var endDateEl = document.getElementById('end_date');
    if (endDateEl && !endDateEl.disabled && endDateEl.value) {
      params.set('end_date', endDateEl.value);
    }

    var maxOccEl = document.getElementById('max_occurrences');
    if (maxOccEl && !maxOccEl.disabled && maxOccEl.value) {
      params.set('max_occurrences', maxOccEl.value);
    }

    // The DESTINATION (plan step R7d-f-2, ledger row REC-515).  A transfer
    // into a loan stops when the loan does, and that stop is derived, not
    // stored -- so a preview that walked the rule alone listed dates past
    // the payoff whenever fewer than five remained, on the one surface whose
    // contract is "what saving would produce".  The endpoint narrows the
    // walk by the destination's derived stop; the transaction form has no
    // destination control and sends nothing, which the endpoint reads as
    // "narrowed by nothing".  Sent whenever the control states a value,
    // locked or not: a locked row still names the loan the save will use.
    if (destinationSelect && destinationSelect.value) {
      params.set('to_account_id', destinationSelect.value);
    }
    // The SOURCE accounts (plan step pay_calendar:C18-a, ruling R-PC85).  A
    // save bounds a definition's occurrences by the books of EVERY account it
    // moves money in -- no row may land on or before an account's opening day
    // -- so the transaction form's account and the transfer form's source
    // ride beside the destination, and the endpoint drops the dates saving
    // would not generate.  Absent on the other form, and sent only when set.
    SOURCE_ACCOUNT_FIELDS.forEach(function(field) {
      var el = document.getElementById(field);
      if (el && el.value) params.set(field, el.value);
    });
    if (envelopeBox && envelopeBox.checked) params.set('is_envelope', '1');

    // Abort any preview still in flight, then reject non-2xx so a 4xx/5xx or
    // session-expiry login page is never injected as if it were the dates.
    if (previewAbortController) previewAbortController.abort();
    previewAbortController = new AbortController();
    fetch(previewUrl + '?' + params.toString(), { signal: previewAbortController.signal })
      .then(function(r) {
        if (!r.ok) throw new Error('recurrence preview fetch failed: ' + r.status);
        return r.text();
      })
      .then(function(html) { preview.innerHTML = html; })
      .catch(function(err) {
        // A newer field change aborted this preview -- intentional, not an error.
        if (err.name === 'AbortError') return;
        preview.innerHTML = '<small class="text-muted">Could not load preview</small>';
      });
  }

  // Initialize on page load.
  toggleFields();

  // Listen for changes.  The unit re-links everything below it; the interval
  // re-links the placements, because which are authorable depends on the unit.
  // The PLACEMENT re-links too, because the preview it fetches depends on
  // which paycheck funds each occurrence.  (It once also showed or hid a
  // day-of-month row; neither form has one now.)
  // Setting a <select>'s value from script fires no change
  // event, so syncPlacements' own corrections cannot re-enter this.
  unitSelect.addEventListener('change', toggleFields);
  interval.addEventListener('change', toggleFields);
  placementSelect.addEventListener('change', toggleFields);
  // The DESTINATION re-links the "Starts on" row, and the "Ends" row for a
  // loan whose payment this would be: choosing a loan with no payment yet
  // hands both bounds to the route, so neither control is the user's to state.
  // It re-links the unit's offer too, since plan step credit_card:CC-6: which
  // destinations have due dates decides which units are on offer, so an owner
  // with no loans needs the re-link as well, and the shortcut to the preview
  // alone this took for such an owner went with that.  ``toggleFields`` applies
  // all of them, and ends in ``fetchPreview``: the endpoint narrows the walk by
  // the destination's derived stop (plan step R7d-f-2), so moving a transfer
  // onto or off a loan changes the dates it lists.
  if (destinationSelect) {
    destinationSelect.addEventListener('change', toggleFields);
  }
  // The source accounts and the envelope box re-preview too (plan step
  // pay_calendar:C18-a): the books floor the endpoint bounds by is theirs,
  // and the box picks which of a row's days it is compared on.
  ['repeats_on', 'max_per_month', 'end_date',
   'max_occurrences', 'is_envelope'].concat(SOURCE_ACCOUNT_FIELDS).forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', fetchPreview);
  });
  // The first occurrence re-links the "repeating on" control before it
  // previews: which readings that date allows, and in which words, are
  // properties OF the date, so moving it can retire or reword the choice the
  // user made under the old one.
  if (startsOn) {
    startsOn.addEventListener('change', function() {
      var chosen = currentOption(unitId(), placementSelect.value);
      syncRepeatsOn(
        chosen !== null && chosen.has_day_of_month_coordinate &&
        !destinationDerivesTheBounds()
      );
      fetchPreview();
    });
    startsOn.addEventListener('focus', function() {
      dateBoxFocused = true;
    });
    // Leaving the box makes a clear final, whichever date it was on.
    startsOn.addEventListener('blur', function() {
      dateBoxFocused = false;
      readingPassedOver = null;
    });
  }
  // The "Ends" mode re-links which value input is enabled, then previews:
  // switching from a date to a count changes the dates the preview lists.
  if (endMode) {
    endMode.addEventListener('change', function() {
      syncEndBound(true);
      fetchPreview();
    });
  }
  // The pay-period <select> no longer drives the preview: it is shown only
  // when the definition does NOT repeat, and a non-repeating definition has
  // no occurrences to list.
}

initRecurrenceForm();

// Re-link after an htmx swap that brought a fresh set of controls (plan step
// salary:R15-c: the payroll-lines section is re-rendered whole after every
// add / edit / delete, form included).  event.target is the settled NEW node
// -- app.js records why detail.target is the wrong one to read after an
// outerHTML swap -- and a swap carrying no unit select (a grid cell, a
// raises section) re-links nothing.
document.body.addEventListener('htmx:afterSwap', function(event) {
  var settled = event.target;
  if (!settled || typeof settled.querySelector !== 'function') return;
  if (settled.querySelector('#recurrence_unit')) initRecurrenceForm();
});
