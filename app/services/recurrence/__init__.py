"""
Shekel Budget App -- Recurrence authoring and resolution (plan steps R2c-1, R2d)

The single door every recurrence rule in the application is written through,
and the single producer of what a recurrence MEANS, and the public surface of
a package whose private modules are fenced by ``shekel-private-module-import``
(W9910).

What a rule AUTHORS, and nothing else
--------------------------------------

``budget.recurrence_rules`` states its recurrence in SIX columns: ``interval_n``,
``unit_id``, ``placement_id``, ``shift_id``, ``starts_on`` and the 0-or-1
``nominal_day``.  A caller states the same thing (:class:`RecurrenceSpec`) and
every reader takes it from there.  That is the END of plan step R7c's expand /
migrate / contract: **R7c-a** added the columns and had the write door keep them
in step while nothing read them, **R7c-b** moved every reader across and gave the
form one date to collect, **R7c-c** dropped the closed set.

**There is no encoding left.**  ``pattern_id``, ``day_of_month``,
``month_of_year``, ``start_date``, ``start_period_id`` and ``offset_periods``
are DROPPED (migration ``d9f5c1a48b73``), and with them ``encode_cadence``,
``decode_pattern``, ``PATTERN_DERIVATIONS``, ``stored_interval`` and
``pattern_member``.  ``interval_n`` was the last column read through the
encoding -- the write door stored ``1`` for every pattern whose interval was in
its NAME, so a Quarterly rule read as monthly at face value -- and that same
migration re-points it, so it now means "how many ``unit_id``\\ s pass between
occurrences" for every unit and every reader takes it off the column.

``recurrence.compute_due_date`` dated every row from the dropped
``day_of_month`` and read :func:`scheduling_day_of_month` in its place
(developer ruling 2026-08-16, plan ledger row **D37**): for the day itself
until plan step **R5-a** dated a row from its occurrence, and for whether it
was ``None`` until plan step **R8-b** dated a row from the cadence's unit and
placement.  Nothing that dates a row reads it now; it still answers the day of
the month a rule schedules on.

**A stored DERIVATION would have been a cache; a stored AUTHORED value is a
fact**, and that distinction is why the R2d ruling of 2026-08-07 refused these
columns and R7c-b makes them correct.  The mechanisms proposed to keep a cache
honest -- read-only column accessors, a lint checker, a periodic integrity scan
-- are all apparatus, and none can be complete: measured on SQLAlchemy 2.0.49,
read-only accessors block attribute assignment and keyword construction but not
ORM bulk ``update()``, Core ``update()`` on ``__table__``, or assignment to the
private name.  Nothing is being kept honest here, because nothing is derived
from an input that can move: the form collects ``starts_on``, the loan sync
writes it from a contract.  (A day-less LOAN payment is the one shape still
measured against the schedule -- its stored first occurrence is a PAYDAY rather
than the contractual installment it was derived from; plan ledger row **D39**
tracks it, and owns it at plan step R5.)

What this package offers
------------------------

* :func:`resolve` -- ``(spec, calendar) -> ResolvedRecurrence``, the two-axis
  meaning.  Pure: no Flask, no ORM, no clock, no database.
  Its ``starts_on`` is the rule's FIRST OCCURRENCE, with ONE meaning for every
  unit (ruling **R-R16**, plan ledger row **D28**), and it is what
  ``budget.recurrence_rules.starts_on`` holds.  ``anchor_date`` and the
  standalone ``first_occurrence`` went at plan step R7c-b: that field was the
  first occurrence for a calendar cadence and the opening BOUND for a
  pay-period one (row **D6**), so one name meant two things and a second
  function existed to reconcile them.
* :func:`author_rule` / :func:`reauthor_rule` / :func:`build_transient_rule`
  -- the ORM-facing door.  A caller states what it AUTHORS
  (:class:`RecurrenceSpec`), never a column.
* :func:`occurrences` / :func:`place` / :func:`occurrence_placements` -- the
  forward occurrence engine (plan step R3), AUTHORITATIVE since plan step R4a,
  so every pay period the application generates a row into is selected here.
  An occurrence with no pay period means the SAVED schedule does not reach it,
  which since plan step C2-b2 is the only way to get one.
* :func:`recurrence_spec` / :func:`read_rule` / :func:`resolved_recurrence` /
  :func:`resolved_spec` / :func:`rule_occurrences` / :func:`placed_periods` --
  the READ door, symmetric with the write door: a rule's authored state back
  out, the one resolve-then-place composition, each of its halves alone (the
  resolving half from a rule or from a spec already read), and the projection
  three surfaces take of the second.  Since plan step R4b-2 the generation
  seam, the Recurring surface, the form preview and the frozen baseline all
  answer from one call.
* :func:`cadence_of` -- ``(pattern_id, interval_n) -> Cadence``, how often a
  stored pattern fires, with no schedule involved (plan step R7a-2b).  With
  :meth:`Cadence.units_per_year` it is what makes a monthly equivalent one
  expression instead of a branch per pattern.
* :func:`describe` -- what a recurrence's cadence is CALLED, one function over
  ``(interval_n, unit)`` (plan step R7a).  It replaced eight hand-written
  template branches keyed on the closed pattern set, so a cadence nothing
  authors yet already reads correctly.

What lives where
----------------

* ``_frequency`` -- what a cadence means with NO schedule: :class:`Cadence`
  (its interval, unit and per-month ceiling), the yearly counts and the
  monthly equivalent every money conversion rests on, the unit facts
  (:func:`has_day_of_month_coordinate`, :func:`can_repeat_within_month`),
  the ``ref``-id-to-member readers and the canonical spelling.  Split out at
  plan step R7a-2b because ``obligations_aggregator`` and the calendar's
  infrequent badge ask "how often" and hold no calendar, so they could not
  use the two-axis vocabulary at all while it was fused to the anchor
  derivation.  It held the offer set too, from R8-a until salary:R15-a.
  ``_resolution`` reads this module's tables rather than holding its own, so
  the two cannot disagree.
* ``_bounds`` -- WHEN a recurrence stops: :class:`EndBound` and its three
  shapes.  Its own module rather than a pair of fields on ``_resolution``'s two
  values because "at most one closing bound" is then a property of the TYPE
  rather than a CHECK three layers have to restate -- and because the shape set
  is what the form offers, the schema accepts and the walk asks, so one closed
  table serves all three (plan step R7b-3).
* ``_offer`` -- the OFFER SET and the two predicates it is derived from,
  split out of ``_frequency`` at plan step salary:R15-a as a pure move when
  the cadence's third value took that module past pylint's line ceiling; the
  argument for deriving the offer from what a rule can DO stays in
  ``_frequency``'s docstring.
* ``_nominal_day`` -- the 0-or-1 day a short month clamped: its domain, the
  join into the day a rule fires on, which days a date leaves open, and the
  pair refusal both value types hold.  Split out of ``_resolution`` at the
  same step, for the same reason, the same way.
* ``_resolution`` -- :class:`RecurrenceSpec`, :class:`ResolvedRecurrence` and
  :func:`resolve`, the pure derivation of what a recurrence means AGAINST a
  schedule.  Since plan step R7c-b that derivation is TWO things -- the
  pay-period normalisation and the cycle phase -- because the first occurrence
  is authored rather than reconstructed; the three anchor derivations that
  used to live here are deleted with the columns they read.
* ``_authoring`` -- the WRITE door: refuse the unresolvable, write the
  authored spec.  The only module here that holds a session.  The SCHEDULE it
  resolves against is :class:`~app.services.pay_calendar.PayCalendar`, loaded
  through that package's one door -- this package held a second calendar type
  and a second loader until plan step **C2-b2** deleted both.
* ``_occurrence`` -- the forward occurrence engine: walk the cadence, place
  each occurrence on a pay period.  Consumes :class:`ResolvedRecurrence`.  It
  held ``first_occurrence`` until plan step R7c-b -- the walk's first element
  stated as a direct search, so the value seeding the stored column could not
  differ from the value generating the rows.  ``resolve`` normalises the date
  itself now, so ``starts_on`` IS that first element by construction and there
  are no longer two functions to keep in step.
* ``_placement`` -- the placement half of that model: :class:`OccurrencePlacement`,
  :func:`place` and the two compositions every reader takes, plus
  :func:`placements_below_the_books` (plan step ``pay_calendar:C18-a``), what
  the books bound drops from the saved one, and :func:`occurrence_walk`, the
  saved walk as its two halves (:class:`BooksWalk`, ruling **R-PC94**: the
  closing counts both).  Split out of
  ``_occurrence`` at plan step salary:R15-a as a pure move when the ceiling
  took that module past pylint's line ceiling; the model's argument stays in
  ``_occurrence``'s docstring.
* ``_row_date`` -- the DATE a generated row carries, derived from its rule
  and the placed occurrence it answers (:func:`compute_due_date`, plan step
  R5-a; it read the period alone until then).  Moved here from
  ``recurrence_engine._plan`` at plan step R16-b-2 (ruling **R-R69**) so the
  balance seam can date an occurrence no row answers yet exactly as the row
  would be dated; plan step R5-b re-examines it when the stored date splits.
* ``_reading`` -- the READ door: a stored rule's authored state, its
  occurrences, and the projection onto periods.  Its own module rather than a
  line in ``_authoring`` because reading is not writing -- and because
  ``_authoring`` carries the session while nothing here needs one -- and
  rather than a line in ``_occurrence`` because that module is pure by
  contract and this one takes an ORM row.
* ``_vocabulary`` -- which patterns the application MODELS, so a ``ref`` row
  the enum does not name can be neither read nor accepted (plan step R2e-2).
  It held the PICKER too until plan step R7b-2 moved that out.
* ``_picker`` -- what the recurrence form OFFERS and what each option is
  called.  Its own module rather than a line in ``_vocabulary`` because the two
  answer different questions from different tables: membership is per closed-set
  pattern and is asked of STORED rows, while the offer set is derived from the
  ENCODER's table and asked of a blank form.  Serving the options from the
  encoder is what makes a cadence the closed set cannot store unofferable
  rather than merely unoffered (plan step R7b-2).
* ``_describe`` -- what a RESOLVED recurrence is called on a display surface.
  Its own module rather than a line in ``_vocabulary`` because the two are
  keyed on different things: the picker's labels are per closed-set pattern
  and die with the form at plan step R7b, while this one is a function of the
  two-axis meaning and is what survives.

Plan step R3 built the forward occurrence engine here, step R4a pointed the
old ``match_periods`` adapter at it (gated by
``tests/oracles/recurrence_baseline.txt``), and step R4b-2 deleted that adapter
and moved generation itself onto the ``(occurrence, period)`` pairs.
:class:`RecurrenceSpec`'s fields changed at step R7b-1 and :func:`resolve`
shrank with them.  Step R7c drops the encode / decode pair and the columns
under it; what changes above the door then is the two surfaces that still read
``rule.pattern_id`` for a cadence (``calendar_infrequency``,
``obligations_aggregator``, both through :func:`cadence_of`).
"""
from app.services.recurrence._authoring import (
    EmptyAuthoredWindowError,
    RecurrenceOwner,
    author_rule,
    build_transient_rule,
    reauthor_rule,
)
from app.services.recurrence._bounds import (
    END_BOUND_KINDS,
    NEVER_ENDS,
    BoundReading,
    EndBound,
    EndBoundColumns,
    EndBoundInputError,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
    end_bound_from_columns,
    end_bound_from_token,
)
from app.services.recurrence._frequency import (
    Cadence,
    CadenceReading,
    RecurrenceFrequencyError,
    RecurrenceResolutionError,
    can_repeat_within_month,
    canonical_cadence,
    has_day_of_month_coordinate,
)
from app.services.recurrence._offer import (
    AuthorableCadence,
    authorable_cadences,
    cadences_without_a_card,
    emits_period_starts,
    fires_on_day_of_month,
    fixed_interval,
    follows_card_due_dates,
    is_authorable,
)
from app.services.recurrence._nominal_day import (
    cadence_day_of_month,
    is_offerable_nominal_day,
    offerable_nominal_days,
)
from app.services.recurrence._closing import (
    DERIVED_STOP_KINDS,
    EMPTY,
    INDEFINITE,
    ClosesOn,
    Closing,
    DerivedStop,
    Empty,
    Indefinite,
)
from app.services.recurrence._describe import (
    RecurrenceDescription,
    RecurrenceDescriptionError,
    describe,
)
from app.services.recurrence._occurrence import (
    RecurrenceGenerationError,
    occurrences,
)
from app.services.recurrence._placement import (
    BooksWalk,
    OccurrencePlacement,
    occurrence_placements,
    occurrence_walk,
    place,
    placements_below_the_books,
    projected_occurrence_placements,
)
from app.services.recurrence._reading import (
    RuleReading,
    cadence_of,
    has_ended,
    placed_occurrences,
    placed_periods,
    read_rule,
    recurrence_spec,
    recurrence_spec_with_cadence,
    resolved_recurrence,
    resolved_spec,
    rule_occurrences,
    scheduling_day_of_month,
    stored_cadence,
)
from app.services.recurrence._resolution import (
    RecurrenceSpec,
    ResolvedRecurrence,
    resolve,
)
from app.services.recurrence._row_date import compute_due_date, period_of_occurrence
from app.services.recurrence._picker import (
    CadenceOption,
    EndBoundOption,
    PickerModel,
    SelectedCadence,
    cadence_options,
    end_bound_options,
    picker_model,
    selected_cadence,
)
from app.services.recurrence._vocabulary import (
    UNREADABLE_CADENCE_MESSAGE,
    modelled_placement,
    modelled_unit,
)

__all__ = [
    "DERIVED_STOP_KINDS",
    "EMPTY",
    "END_BOUND_KINDS",
    "INDEFINITE",
    "NEVER_ENDS",
    "UNREADABLE_CADENCE_MESSAGE",
    "AuthorableCadence",
    "BooksWalk",
    "BoundReading",
    "Cadence",
    "CadenceOption",
    "CadenceReading",
    "ClosesOn",
    "Closing",
    "DerivedStop",
    "EndBound",
    "EndBoundColumns",
    "EndBoundInputError",
    "EndBoundOption",
    "Empty",
    "EndsAfterOccurrences",
    "EndsOnDate",
    "Indefinite",
    "NeverEnds",
    "OccurrencePlacement",
    "PickerModel",
    "RecurrenceDescription",
    "RecurrenceDescriptionError",
    "RecurrenceFrequencyError",
    "RecurrenceGenerationError",
    "RecurrenceOwner",
    "RecurrenceResolutionError",
    "EmptyAuthoredWindowError",
    "RecurrenceSpec",
    "ResolvedRecurrence",
    "RuleReading",
    "SelectedCadence",
    "author_rule",
    "authorable_cadences",
    "build_transient_rule",
    "can_repeat_within_month",
    "cadence_day_of_month",
    "cadence_of",
    "cadence_options",
    "cadences_without_a_card",
    "canonical_cadence",
    "compute_due_date",
    "period_of_occurrence",
    "describe",
    "emits_period_starts",
    "end_bound_from_columns",
    "end_bound_from_token",
    "end_bound_options",
    "fires_on_day_of_month",
    "fixed_interval",
    "follows_card_due_dates",
    "has_ended",
    "is_authorable",
    "has_day_of_month_coordinate",
    "is_offerable_nominal_day",
    "modelled_placement",
    "modelled_unit",
    "occurrence_placements",
    "occurrence_walk",
    "offerable_nominal_days",
    "occurrences",
    "picker_model",
    "place",
    "placed_occurrences",
    "placed_periods",
    "placements_below_the_books",
    "projected_occurrence_placements",
    "read_rule",
    "reauthor_rule",
    "recurrence_spec",
    "recurrence_spec_with_cadence",
    "resolve",
    "resolved_recurrence",
    "resolved_spec",
    "rule_occurrences",
    "scheduling_day_of_month",
    "selected_cadence",
    "stored_cadence",
]
