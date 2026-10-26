> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Recurrence redesign, as built: R7b-4's account (2026-08-14)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_recurrence_redesign.md`; this record exists so that document's
rule 4 cap is met by ARCHIVING a completed span rather than by trimming a live specification
(`conventions.md` rule 5).

R7b-4 shipped at `67f013c8` and was ticked at `5935de0c`. Its entry in the live plan carried
this account under R7c, on the reasoning that every ruling in it binds the cutover. Plan step
**R7c-a** split the cutover into three leaves, and what still BINDS a leaf either stayed in the
live document (the form-control rulings, the anchor-family router, the derived phase, the
downgrade's proof, the browser-drive mandate) or moved into `R7c-b`'s own specification (the
disabled-versus-hidden rule, `owns_validity_window` as the loan-bound predicate, the create
form's defaulted opening bound, and the two inherited findings). What is left HERE is the
history: how R7b-4 came to find those things, and the claims it corrected on the way.

**R7b-3's unrun debt was PAID at R7b-4**, which ran the script FIRST against the unchanged form
(green, so R7b-3's control was sound), extended it for its own two controls (`_drive_opening_bound`,
18 checks), and ran it again.
**That second run found a 500 the whole 9,293-test suite was green across**: the "Starts on" box is
hidden when the form says "does not repeat", a hidden input still SUBMITS, and `start_date=""`
reached `TransactionTemplate(**data)`, which has no such keyword. Every hand-written payload omitted
the key because a person writing one includes the fields they are thinking about; a browser posts
every control the page renders. Fixed on both tiers, with a route test written from the wire and
shown FAILING against the un-fixed helper before it was kept.

**The transfer form's pay-period `<select>` SURVIVES under its other job** -- which period a
one-time transfer lands in -- and it means ONE thing now: the JS relabelling is deleted, the control
shows only while "Does not repeat" is selected, and it is DISABLED otherwise because a hidden
control still submits. Its owner-check moved with it, from the kind-agnostic F-24 builder to
`transfers.create_transfer_template`; the transaction schema no longer declares the field at all.

**One finding R7b-3 left was stated BACKWARDS, and the wrong direction was the harmless-reading
one.** It said `is_loan_payment` (`settings is not None`) is BROADER than the set
`loan_recurrence_sync` writes bounds for, and that every live loan payment satisfies both. Measured
2026-08-14: **neither real loan payment carries a `loan_payment_settings` row**, so it is NARROWER
and R7b-3's "Ends" lock never fired on either loan -- a user could type an end date on their
mortgage and the next payoff-affecting edit would silently overwrite it. Both bound locks and both
crafted-POST refusals now ask `loan_recurrence_sync.owns_validity_window`, which is the sync's own
precondition.

**The THIRD caller was fixed in the same commit** (developer ruling 2026-08-14, reversing one taken
before the measurement existed). `LOAN_PAYMENT_CANNOT_BE_ONE_TIME` had been left on
`is_loan_payment` on the reasoning that it is about the standing `extra_principal` -- true, and not
the whole of it: clearing the recurrence nulls `recurrence_rule_id`, which is how
`active_recurring_transfer_template` FINDS a loan's payment, so both of the developer's real loans
could be set to "Does not repeat" and left amortizing with nothing projecting a payment. It asks the
UNION now, which keeps the set the refusal was written for and adds the set the harm is measured on.
Its firing control uses the PRODUCTION shape -- a loan payment with no settings row -- and was shown
failing against the predicate it replaced.

**The other inherited finding is unchanged, and it belongs to `balance:X-ah`** -- the step that
already rules every other input-door spelling. The 58 `Schema.validate(...)` calls each followed by
a `load()` of the same payload run every validator twice, and `validate` is
`_do_load(postprocess=False)`, so a `@post_load` refusal escapes as an unhandled 500. The four sites
in this arc already moved to `load_form_or_redirect`, and that function is the pattern the sweep
should copy.

**Three adversarial reviews ran against this leaf before it shipped and every one earned its keep.**
What they found is in the commit; three things they left are here because a later step must act on
them, and the ledger is at its 20-line headroom (`conventions.md` rule 4).

- **The create form's DEFAULT was the money finding, and it was mine to introduce.** The control
  this step replaced was a `<select>` with no empty option preselecting the CURRENT period, so every
  definition ever created carried an opening bound of "the paycheck I am in". A date box defaulting
  to empty made that "unbounded", and the create routes generate over `GenerationSchedule.for_user`
  with no lower window bound -- measured, a `$2,000.00` rent template created today wrote 5
  backdated rows, `$10,000.00`, into pay periods that had already closed. Fixed in-commit
  (`create_form_default_start_date`), with a route test that drives the form's OWN rendered default
  rather than a date the test chose, shown FAILING against the empty default. **Nothing is owed** --
  it is recorded because the lesson generalises: replacing a control that always submitted with one
  that may not is a DEFAULT change, and the suite could not see it because no test asserted the
  generated ROWS of a create.
- **A create into a configured LOAN still discards a typed "Starts on" silently.**
  `materialize_initial_transfers` calls `bind_rule_to_loan`, which overwrites `start_date` with the
  loan's first contractual installment -- the same "accepted then silently discarded" outcome the
  EDIT path refuses with a message. Financially safe (the loan's bound wins, so nothing generates
  pre-origination) and PRE-EXISTING for the closing bound, which R7b-3 left in the same shape. It is
  R7c's to rule with the rest of the create-form controls: lock on a loan destination, or say so in
  the help text.
- **`PayCalendar.period_by_id` has no `app/` caller left**, this step having removed the last one.
  It is the pay-calendar arc's value, so deleting it from the recurrence arc would be the
  out-of-scope change `RecurrenceRule.start_period` was not; both docstrings now say so rather than
  naming a consumer that no longer exists.

**One claim about the frozen oracle is weaker than it reads, stated so it is not over-relied on.**
The 36 `every_n_periods` shapes are re-parameterised onto `start_date`, and every bound they state
is exactly a payday -- so the byte-identical blob proves the derivation over the payday case and
says nothing about a bound landing MID-period, which is the input the change actually introduced.
That case is covered, by two hand-computed cases in `test_recurrence_resolution.py` and one in
`test_recurrence_engine.py`; the blob is not what covers it.

## The R7b span's entries, as they stood in the live plan

Condensed to one line each there at plan step R7c-a (`conventions.md` rule 5, the cap).
What each one closed is in `steps.md`'s Shipped table; the code each shipped is the record.

- [x] **R7b-1 -- the authored vocabulary becomes the two axes.** `e7eb3b1a`. A caller states
      `(interval_n, unit, placement)`; the closed set is a STORAGE ENCODING crossed by
      `_frequency.encode_cadence` and `decode_pattern`, the latter INVERTED from
      `PATTERN_DERIVATIONS` at import. The `month_step` column, the `family` column and the second
      statement of which units fire on a day of the month are gone with it. Baseline byte-identical;
      on a production clone all 46 rules read and re-author unmoved.

- [x] **R7b-2 -- the form authors that vocabulary.** `ecc4d01b`. Three linked controls, their offer
      set `authorable_cadences` -- the encoder's table INVERTED -- so an unstorable cadence is
      unofferable rather than fenced. `pattern_choices`, `RecurrencePatternField`, the five `REC_*`
      globals and the `offset_periods` schema field go with it. **D8 closed**; **D31**, **D32**
      opened. Six defects were found CLOSING it, four by two adversarial reviews and two by driving
      the form; the commit enumerates them and `anchor_family` moved to `_frequency` with them.

- [x] **R7b-3 -- one "ends" control, and the CHECKs the door does not mirror.** `c8655584`. The
      closing bound is ONE value with THREE shapes above the columns, so a rule cannot state two,
      and `max_occurrences` has its first writer. **D23 closes** on two remedies, not one:
      `single_end_bound` and `positive_max_occurrences` are properties of the TYPE that no door
      refuses; `due_dom` and `valid_offset` are mirrored beside `dom` / `moy`. Takes the
      count-bounded end off **R8**. Four reviews; the commit enumerates what they found.

- [x] **R7b-4 -- the opening bound becomes a DATE.** `67f013c8`. "First paycheck" (a pay-period FK)
      became "Starts on", folded into `start_date` under a MAXIMUM that writes only the term
      deciding it. **D2 and D30 close.** The `Every N Periods` PHASE became a derivation of that
      bound, deleting `_phased_period_anchor`, `RecurrenceSpec.offset_periods` and the negative-
      offset refusal. 46 rules, 880 placed occurrences, 0 moved; the frozen 430-shape oracle is
      byte-identical. What R7c inherits is under R7c.
