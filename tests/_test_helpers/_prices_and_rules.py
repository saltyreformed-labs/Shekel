"""Prices and recurrence rules: a definition's price through its one write door, a bare template,
and the rules of a stated cadence a definition owns.
"""

from datetime import date as _real_date
from decimal import Decimal


def resolved_amount(txn):
    """Return what the APP says *txn*'s amount is, through the one resolver.

    **The assertion a generated row needs since plan step balance:X-au-e**,
    where it used to be ``txn.estimated_amount``.  That column is NULL on a
    derived row -- the whole point of the cutover -- so a test reading it
    asserts the absence of a cache rather than the presence of money, and
    ``assert None == Decimal("1200.00")`` is what a correct app now produces.

    Reading the resolver instead keeps the assertion about the FIGURE, which is
    what the test was always for: it goes through
    ``cash_ledger.resolve_transaction_amount`` over a basis pinned the way every
    production reader pins one, so a test asserts the number a screen would
    show.  Use it for any row whose amount is DERIVED; a row that owns its
    figure (an ad-hoc one, or one a human re-priced) answers the same through
    this function, so it is safe everywhere and needed only where the column
    went away.

    Args:
        txn: The transaction to price.

    Returns:
        The row's amount as a ``Decimal``.

    Raises:
        AmountUnresolvable: When no rule can price the row -- which is a real
            finding, not a fixture inconvenience.  See
            :func:`state_template_price` for the fixture shape that causes it.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.cash_ledger import derived_amount_basis, resolve_transaction_amount

    return resolve_transaction_amount(
        txn, derived_amount_basis(txn.account.user_id, txn.scenario_id),
    )


def state_template_price(template, amount, *, effective_on=None):
    """State *template*'s price through the app's ONE write door.

    **A fixture that constructs a template and stops has built a definition the
    application cannot build**, and since plan step balance:X-au-e that
    difference is fatal rather than cosmetic -- for a TRANSFER template since
    balance:X-au-f, which is the twin obligation this helper was written
    anticipating.  Both doors that create a
    transaction template -- ``routes/templates/crud.create_template`` and
    ``routes/salary/profiles._salary_template`` -- call
    ``template_amount_service.set_amount`` immediately after the flush, so every
    real definition has a price SERIES.  A generated row stores no figure now
    and is priced by that series on its own due date, and
    ``cash_ledger._amount_source._stated_amount`` REFUSES a row whose series is
    empty rather than falling back to a scalar -- which is X-au-a's
    ruling, not an oversight: the scalar has no time dimension, so reading it
    would price a March row at June's figure.

    So a bare-constructed template generates rows nothing can price, and the
    failure surfaces as ``AmountUnresolvable`` in whatever the test was actually
    about.  Call this straight after the flush wherever a fixture builds a
    template by hand.

    Zero production templates have an empty series (measured on the 2026-09-03
    production clone, all 525 declared rows resolved), so this restores the
    fixture to the shape the data actually has.

    Args:
        template: The flushed transaction or transfer template.
        amount: The price to state.  Required since plan step X-bp dropped
            ``default_amount``, which it defaulted to.
        effective_on: The date it takes effect; the owner's today when omitted,
            as both doors pass.  One version is enough for any due date --
            ``amount_as_of`` holds FLAT before the earliest -- so a fixture
            needs a date only when it is testing the series itself.

    Returns:
        None.  The caller flushes or commits as it already does.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import template_amount_service
    from app.utils.dates import display_today

    template_amount_service.set_amount(
        template,
        amount,
        effective_on=display_today() if effective_on is None else effective_on,
    )


def bare_expense_template(
    db_session, seed_user, name="Cadence Under Test", *, account=None,
    is_envelope=False,
):
    """Create and flush an expense template carrying NO cadence.

    The definition a test authors a rule onto when the rule is the subject and
    the template is only there to own it -- which plan step R-F6 made
    necessary: ``ck_recurrence_rules_one_owner`` refuses a rule belonging to
    nothing, so ``author_rule`` takes an owner and there is no such thing as a
    free-standing rule any more.

    **And the PRICING LINK a bare CHECK builder's row carries** since plan step
    ``balance:X-bi-7d-1``.  The builders in ``tests/test_models`` whose
    subject is a CHECK or key of ``budget.transactions`` construct their rows
    by hand on purpose -- a control that reached the row through a door would
    grade the door -- and the family's cutover (``X-bi-7d-2``) re-cuts
    ``ck_transactions_one_pricing_link`` to ``= 1``, so a bare row must name
    a definition to be storable at all.  A rule-less definition of the
    owner's is the one such a row would have had the grid mint it
    (**R-BAL20**), and this is the shared builder of that shape (the audit
    trigger's workloads keep a local one, ``tests._audit_trigger_workloads
    ._rule_less_definition``); each such row takes its OWN definition,
    because two undated rows of one definition in one paycheck collide on
    ``idx_transactions_template_scenario_undated`` and two dated ones on the
    occurrence index.

    Distinct from :func:`make_expense_template`, which already gives its
    template an every-paycheck rule -- authoring a second onto that one is
    refused by ``uq_recurrence_rules_transaction_template_id``, and refused
    correctly: a definition has one cadence.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict.
        name: Display name; distinct per call when a test needs two.
        account: The account the definition names, or ``None`` for the seed
            user's.  Stated by a case that has DELETED the seeded account
            before it mints (``transaction_templates.account_id`` is
            ``ON DELETE RESTRICT``, so a definition minted on it beforehand
            would refuse the very act under test, and one minted afterwards
            has no account to name).
        is_envelope: Whether the definition's rows take purchase entries --
            the DEFINITION's setting, which ``Transaction.tracks_purchases``
            reads for every row that names one.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        with ``recurrence_rule`` still ``None``.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import TxnTypeEnum
    from app.models.transaction_template import TransactionTemplate

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=(seed_user["account"] if account is None else account).id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        is_envelope=is_envelope,
    )
    db_session.add(template)
    db_session.flush()
    state_template_price(template, Decimal("100.00"))
    return template


def sole_rule_owned_by(user_id):
    """Return the ONE recurrence rule *user_id* owns, through its definition.

    ``budget.recurrence_rules`` carries no ``user_id`` column since plan step
    R-F6 -- the owner is the definition holding the rule, and
    :attr:`~app.models.recurrence_rule.RecurrenceRule.user_id` reads through to
    it -- so a Python property cannot be a SQL filter and
    ``filter_by(user_id=...)`` no longer names a column.  This is the join that
    replaces it, over both arms of the owning arc.

    It is a TEST helper rather than an application one deliberately: no
    production reader asks "which rules does this user own", and adding a query
    nothing calls is the speculative surface coding-standards rule 13 refuses.

    Args:
        user_id: The owner.

    Returns:
        The single :class:`~app.models.recurrence_rule.RecurrenceRule`.

    Raises:
        AssertionError: The owner has no rule, or more than one -- the same
            two failures ``Query.one()`` reports, named.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.models.recurrence_rule import RecurrenceRule
    from app.models.transaction_template import TransactionTemplate
    from app.models.transfer_template import TransferTemplate

    rules = []
    for template, arm in (
        (TransactionTemplate, RecurrenceRule.transaction_template_id),
        (TransferTemplate, RecurrenceRule.transfer_template_id),
    ):
        rules.extend(
            db.session.query(RecurrenceRule)
            .join(template, arm == template.id)
            .filter(template.user_id == user_id)
            .all()
        )
    assert len(rules) == 1, (
        f"expected user {user_id} to own exactly one recurrence rule, "
        f"found {len(rules)}: {[rule.id for rule in rules]}"
    )
    return rules[0]


def make_every_period_rule(db_session, owner):  # pylint: disable=unused-argument
    """Author an every-paycheck recurrence ONTO *owner*, and flush it.

    The shared rule builder for every fixture that needs a template to repeat,
    so no test re-derives it.

    **It takes the OWNING DEFINITION rather than a user id, since plan step
    R-F6**, because that is what a rule now needs to exist at all: the owning
    FK is on ``budget.recurrence_rules`` under
    ``ck_recurrence_rules_one_owner``, so a rule with no template is not a row
    the database accepts.  A fixture therefore builds its template first and
    makes it repeat second, which is the order production runs in.

    **Authored through the WRITE DOOR since plan step R7c-b**, and the change is
    a real improvement rather than plumbing.  It used to construct a
    ``RecurrenceRule`` directly with a ``pattern_id`` and nothing else, which
    built a row ``resolve`` would refuse: no first occurrence, no unit, no
    placement.  That was invisible while the two-axis columns were nullable and
    unread; it is a ``NOT NULL`` violation now, and the door is what a
    production rule goes through anyway.

    ``starts_on`` is the schedule's OPENING payday, which is what an
    unbounded rule resolved to before the column was authored -- so every
    fixture built on this generates into the same periods it always did.

    Args:
        db_session: The test session.  Unused since the door flushes into it
            through ``app.extensions.db``; kept because every caller passes it
            and a signature change would touch two dozen call sites for no
            behaviour.
        owner: The ``TransactionTemplate`` or ``TransferTemplate`` the
            recurrence belongs to.  Mutated: its ``recurrence_rule`` is set.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import RecurrenceUnitEnum
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import RecurrenceSpec, author_rule

    calendar = calendar_for(owner.user_id)
    return author_rule(
        RecurrenceSpec(
            user_id=owner.user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=calendar.opening_bound(),
        ),
        calendar,
        owner,
    )


def make_line_cadence_rule(db_session, deduction, per_year):  # pylint: disable=unused-argument
    """Author the rule migration ``542c61e48ee8`` writes for a 24 / 12 line.

    **The shared cadence builder for every fixture that gave a deduction a
    ``deductions_per_year`` of 24 or 12** before plan step salary:R15-b
    deleted the column (rulings R-SAL3, R-SAL29, R-SAL30): the same two
    shapes the migration writes, authored through the write door onto the
    deduction as its owner, so a fixture's line is taken on exactly the
    paydays the migrated production line is.

    Args:
        db_session: The test session; unused for the reason
            :func:`make_every_period_rule` gives.
        deduction: A flushed ``PaycheckLine`` on a profile whose owner
            has pay periods.  Mutated: its ``recurrence_rule`` is set.
        per_year: ``24`` -- every paycheck, at most 2 a month, from the
            owner's opening payday -- or ``12`` -- monthly, on the first
            paycheck on or after the 1st, from the first of the opening
            payday's month.  A 26 line has NO rule; do not call this for one.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.

    Raises:
        ValueError: *per_year* is neither 24 nor 12.
    """
    # pylint: disable=import-outside-toplevel
    from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import RecurrenceSpec, author_rule

    calendar = calendar_for(deduction.user_id)
    opening = calendar.opening_bound()
    if per_year == 24:
        spec = RecurrenceSpec(
            user_id=deduction.user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=opening,
            max_per_month=2,
        )
    elif per_year == 12:
        spec = RecurrenceSpec(
            user_id=deduction.user_id,
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            starts_on=opening.replace(day=1),
        )
    else:
        raise ValueError(
            f"per_year must be 24 or 12 (a 26 line carries no rule); got {per_year!r}"
        )
    return author_rule(spec, calendar, deduction)


def first_occurrence_on_day(user_id, fires_on_day, fires_in_month=None):
    """Return the first date matching a calendar cadence the schedule reaches.

    **A TEST-AUTHORING translation, and deliberately not a production one.**
    Plan step R7c-b made a rule's first occurrence the AUTHORED value (ruling
    R-R16) and deleted the resolver's month-ordinal search that used to
    reconstruct it from ``(month_of_year, day_of_month)``.  A form now asks the
    user for the date outright, so nothing in ``app/`` derives one.

    What survives is a test that describes its subject as "a monthly bill on
    the 15th" and whose assertions were hand-computed against the dates that
    description produced.  Restating each as a literal would mean recomputing
    two dozen of them against a fixture schedule, so the description is
    translated instead -- ONCE, here, answering exactly what the retired
    derivation answered: the first date in the cadence's own residue class on
    or after the schedule's opening.

    Args:
        user_id: The owner, whose schedule sets the floor.
        fires_on_day: Day of the month the cadence fires on, 1-31.  Clamped by
            :func:`~app.services.recurrence._months.clamped_day` in a month too
            short to hold it, which is where a ``nominal_day`` comes from.
        fires_in_month: Month of the year, for the annual and semi-annual
            cadences.  ``None`` for a monthly one, which fires in every month.

    Returns:
        The :class:`datetime.date` to author as ``starts_on``.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence._months import clamped_day, month_ordinal
    from app.utils.dates import MONTHS_PER_YEAR

    opening = calendar_for(user_id).opening_bound()
    ordinal = month_ordinal(opening)
    if fires_in_month is not None:
        # Step to the named month, then forward a year if it has already
        # passed -- the residue class the old ``_calendar_anchor`` walked.
        ordinal += (fires_in_month - opening.month) % MONTHS_PER_YEAR
    candidate = clamped_day(ordinal, fires_on_day)
    if candidate >= opening:
        return candidate
    step = MONTHS_PER_YEAR if fires_in_month is not None else 1
    return clamped_day(ordinal + step, fires_on_day)


def _default_first_occurrence(user_id, calendar, unit, placement):
    """Return what the retired anchor derivation answered for a day-less rule.

    :func:`make_cadence_rule`'s ``starts_on`` default, split out because it has
    THREE answers and each is a different retired derivation.  A fixture that
    states no date is describing a cadence, not a date, so what it must get is
    the date that description produced before plan step R7c-b -- otherwise
    every hand-computed assertion built on it moves.

    * ``PERIOD`` -- the schedule's opening payday, which is what
      ``_phased_period_anchor`` answered for a rule with no stated bound.
    * ``MONTHLY_FIRST`` -- the FIRST of the opening's own month.
      ``_first_of_month_anchor`` scanned for the earliest month whose own first
      payday was not before the effective start and took ``date_trunc('month')``
      of it; the opening's month always qualifies, because the opening IS the
      schedule's earliest payday.  **It can therefore fall BELOW the opening**,
      and that is not a defect: the occurrence is a marker the placement reads,
      and clamping it up to the opening drops the opening month's own paycheck.
      Measured: the fixture schedule opening 2026-01-02 owes 5 rows and a
      clamped default produced 4.
    * every other calendar cadence -- the first day-1 at or after the opening,
      because ``_calendar_anchor`` defaulted an unstated day to 1 and walked
      forward.

    The MONTHLY_FIRST arm is selected by
    :func:`~app.services.recurrence.fires_on_day_of_month`, the application's
    own projection of which cadences anchor on the calendar, rather than by a
    ``(unit, placement)`` pair written out here -- a second statement of that
    mapping is one that can disagree with it.

    Args:
        user_id: The owner, whose schedule sets the floor.
        calendar: That owner's :class:`~app.services.pay_calendar.PayCalendar`.
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.

    Returns:
        The :class:`datetime.date` to author as ``starts_on``.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import RecurrenceUnitEnum
    from app.services.recurrence import fires_on_day_of_month

    if unit is RecurrenceUnitEnum.PERIOD:
        return calendar.opening_bound()
    if not fires_on_day_of_month(unit, placement):
        return calendar.opening_bound().replace(day=1)
    return first_occurrence_on_day(user_id, 1)


def transient_cadence_rule(user_id, cadence, **kwargs):
    """Author one rule of a stated CADENCE without adding it to the session.

    :func:`make_cadence_rule`'s unsaved twin, for the tests that hand a real
    rule to a route helper and never persist it -- the recurrence-form helpers,
    which re-point a rule in place and are called on a bare
    ``test_request_context``.

    It exists for the same reason its sibling does: plan step R7c-b made the
    two-axis columns ``NOT NULL``, so a hand-built rule stating its cadence in
    one field is a shape production cannot produce, and a helper reading it
    would resolve something the application never stores.

    **It writes through ``build_transient_rule`` DIRECTLY since plan step
    R-F6**, rather than through its sibling under a private ``_flush=False``
    flag.  The two stopped being one function with a switch: an authored rule
    takes the definition that owns it and an unsaved one has none, so the flag
    would have had to mean "and also skip the owner", which is two functions
    wearing one name.  What they still share -- the cadence-to-spec
    translation -- is :func:`_cadence_spec`, which both call.

    Args:
        user_id: Whose pay calendar the cadence resolves against, and whose
            UNSAVED definition the rule is built on.  Stated rather than taken
            as an owner object because the caller does not want a definition;
            it wants a rule it can hand to a pure producer.
        cadence: A :class:`~tests.oracles.recurrence_baseline.ShapeCadence`.
        **kwargs: Every other argument :func:`_cadence_spec` takes.

    Returns:
        The unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import build_transient_rule

    calendar = calendar_for(user_id)
    # The door builds the unsaved owner itself from the spec's ``user_id``
    # (plan step R-F6), so ``rule.user_id`` has an answer and nothing reaches
    # the session.
    return build_transient_rule(
        _cadence_spec(user_id, cadence, calendar, **kwargs), calendar,
    )


def _cadence_spec(
    user_id,
    cadence,
    calendar,
    *,
    starts_on=None,
    fires_on_day=None,
    fires_in_month=None,
    interval_n=1,
    nominal_day=None,
    end_date=None,
    max_per_month=None,
):
    """Translate a stated CADENCE into the spec the write door takes.

    The shared half of :func:`make_cadence_rule` and
    :func:`transient_cadence_rule`, which differ only in whether the resulting
    rule is written onto a definition or left unsaved.  Split out at plan step
    R-F6, when the authored path grew an owner argument the transient one
    cannot have.

    Args:
        user_id: Whose calendar the cadence resolves against.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.
        calendar: That owner's :class:`~app.services.pay_calendar.PayCalendar`,
            passed in because both callers already hold one.
        starts_on: See :func:`make_cadence_rule`.
        fires_on_day: See :func:`make_cadence_rule`.
        fires_in_month: See :func:`make_cadence_rule`.
        interval_n: See :func:`make_cadence_rule`.
        nominal_day: See :func:`make_cadence_rule`.
        end_date: See :func:`make_cadence_rule`.
        max_per_month: See :func:`make_cadence_rule`.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.recurrence import (
        NEVER_ENDS,
        EndsOnDate,
        RecurrenceSpec,
    )
    from tests.oracles.recurrence_baseline import ShapeCadence

    if starts_on is not None and fires_on_day is not None:
        raise ValueError(
            "make_cadence_rule takes starts_on OR fires_on_day, not both: "
            "they are two statements of the same fact and only one can be "
            f"authored (got {starts_on!r} and day {fires_on_day!r})",
        )
    # The TYPE is checked at the door because every other read of *cadence*
    # below is an attribute access, so a caller still passing plan step R9's
    # retired shorthand -- the string "Monthly", or a member of the deleted
    # ``RecurrencePatternEnum`` -- would otherwise surface as an
    # ``AttributeError`` from three frames down naming ``interval_n``.
    if not isinstance(cadence, ShapeCadence):
        raise TypeError(
            f"make_cadence_rule takes a ShapeCadence, not {cadence!r}.  Plan "
            f"step R9 retired the closed pattern set's display names with the "
            f"table they came from; state the two axes instead, as one of "
            f"tests.oracles.recurrence_baseline's cadence constants.",
        )
    resolved_interval = (
        interval_n if cadence.interval_n is None else cadence.interval_n
    )
    if starts_on is None and fires_on_day is not None:
        starts_on = first_occurrence_on_day(
            user_id, fires_on_day, fires_in_month,
        )
    if starts_on is None:
        starts_on = _default_first_occurrence(
            user_id, calendar, cadence.unit, cadence.placement,
        )
    return RecurrenceSpec(
        user_id=user_id,
        unit=cadence.unit,
        placement=cadence.placement,
        interval_n=resolved_interval,
        starts_on=(
            starts_on if starts_on is not None
            else calendar.opening_bound()
        ),
        nominal_day=nominal_day,
        end_bound=(
            NEVER_ENDS if end_date is None else EndsOnDate(end_date)
        ),
        max_per_month=max_per_month,
    )


def make_cadence_rule(owner, cadence, **kwargs):
    """Author one rule of a stated CADENCE ONTO *owner*, through the write door.

    :func:`make_every_period_rule`'s general sibling, for the tests that need a
    cadence other than every-paycheck.  Both exist because a rule may not be
    constructed field by field any more: plan step R7c-b made ``unit_id``,
    ``placement_id``, ``shift_id`` and ``starts_on`` ``NOT NULL``, so a
    hand-built ``RecurrenceRule`` stating a cadence in one field is a
    constraint violation rather than a shortcut.

    **It took a closed-set NAME until plan step R9**, resolved through a
    ``CADENCE_BY_LEGACY_NAME`` table in the baseline oracle.  R9 drops
    ``ref.recurrence_patterns`` and ``RecurrencePatternEnum``, and the table
    of names went with them rather than outliving both: a caller states the
    two axes the row actually holds, as a
    :class:`~tests.oracles.recurrence_baseline.ShapeCadence` constant beside
    the frozen shapes -- so a fixture asking for the quarterly cadence and a
    captured shape labelled ``quarterly`` still cannot come to mean different
    things, and a mistyped one is a ``NameError`` at import.

    **It takes the OWNING DEFINITION rather than a user id, since plan step
    R-F6**: the owning FK is on ``budget.recurrence_rules`` under
    ``ck_recurrence_rules_one_owner``, so a rule with no template is not a row
    the database accepts.  A fixture builds its template first and makes it
    repeat second, which is the order production runs in.

    Args:
        owner: The ``TransactionTemplate`` or ``TransferTemplate`` the
            recurrence belongs to.  Mutated: its ``recurrence_rule`` is set.
            Its ``user_id`` is whose calendar the cadence resolves against.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author -- one of that module's seven constants, or any other
            ``(interval_n, unit, placement)`` a test needs.
        starts_on: The rule's FIRST OCCURRENCE (ruling R-R16).  Defaults to
            what the retired derivation answered for a rule stating no day:
            the schedule's opening payday for a PAYCHECK-space cadence, and
            the first 1st of a month the schedule reaches for a calendar
            one -- so a fixture that states nothing generates where it
            always did, and a MONTHLY_FIRST rule does not read as firing on
            whichever day of the month the schedule happened to open on.
        fires_on_day: An alternative to *starts_on* for a test that describes
            its subject as a cadence rather than as a date -- "a monthly bill
            on the 15th".  Translated by :func:`first_occurrence_on_day`; see
            it for why the translation lives in the tests and not in ``app/``.
            Mutually exclusive with *starts_on*.
        fires_in_month: The month half of that description, for the annual and
            semi-annual cadences.
        interval_n: Read only when *cadence* fixes none of its own -- exactly
            ``EVERY_N_PERIODS``; every other constant carries one and this
            argument is discarded.  (The COLUMN takes any positive interval
            for any unit since plan step R7c-c -- what is narrow here is the
            constant, not the model.)
        nominal_day: The day the rule MEANS when *starts_on*'s month clamped
            it (ruling R-R3).
        end_date: The rule's closing bound.  ``None`` never ends.
        max_per_month: The per-month ceiling (plan step salary:R15-a), or
            ``None`` for none.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import author_rule

    calendar = calendar_for(owner.user_id)
    return author_rule(
        _cadence_spec(owner.user_id, cadence, calendar, **kwargs),
        calendar,
        owner,
    )


def rows_by_paycheck(rows, periods):
    """Group generated rows by their paycheck's payday, as ``(occurs_on, due_date)``.

    Each paycheck's pairs are sorted, so a case can write the exact list a
    paycheck holds.  Keyed on the payday rather than the period id so the
    expected value reads as the schedule does.  Both engines' rows carry the
    three columns it reads, so the weekly cases of the transaction engine and
    the transfer engine state their expectations in one shape (plan step
    R8-b).

    Args:
        rows: The ``Transaction`` or ``Transfer`` rows a generation wrote,
            as it returned them or as re-read from the database.
        periods: The saved pay periods the rows were generated into.

    Returns:
        dict: payday -> sorted list of ``(occurs_on, due_date)``.
    """
    payday_by_id = {period.id: period.start_date for period in periods}
    grouped = {}
    for row in rows:
        grouped.setdefault(payday_by_id[row.pay_period_id], []).append(
            (row.occurs_on, row.due_date),
        )
    return {payday: sorted(pairs) for payday, pairs in grouped.items()}


#: What :func:`rows_by_paycheck` reads for ``WEEKLY_FIRST`` -- every 7 days
#: from Tuesday 2026-01-06, funded from the first paycheck starting on or after
#: each date (ruling R-R95) -- over ``seed_periods``, ten 14-day paychecks from
#: Friday 2026-01-02.  Both of a paycheck's weeks defer to the NEXT payday and
#: are due on it: 01-06 and 01-13 to 01-16, 01-20 and 01-27 to 01-30, and so on.
#: The first paycheck funds nothing, and 05-12 and 05-19 defer to 05-22, past
#: the ten saved paychecks: eighteen rows in nine paychecks.
#:
#: ONE statement for both engines, because both take their placements from the
#: one shared preamble (``recurrence_engine.resolve_generation_plan``) and their
#: due days from ``compute_due_date``: the transaction case and the transfer
#: case grade the same answer through two writes.
WEEKLY_FIRST_ON_SEED_PERIODS = {
    _real_date(2026, 1, 16): [
        (_real_date(2026, 1, 6), _real_date(2026, 1, 16)),
        (_real_date(2026, 1, 13), _real_date(2026, 1, 16)),
    ],
    _real_date(2026, 1, 30): [
        (_real_date(2026, 1, 20), _real_date(2026, 1, 30)),
        (_real_date(2026, 1, 27), _real_date(2026, 1, 30)),
    ],
    _real_date(2026, 2, 13): [
        (_real_date(2026, 2, 3), _real_date(2026, 2, 13)),
        (_real_date(2026, 2, 10), _real_date(2026, 2, 13)),
    ],
    _real_date(2026, 2, 27): [
        (_real_date(2026, 2, 17), _real_date(2026, 2, 27)),
        (_real_date(2026, 2, 24), _real_date(2026, 2, 27)),
    ],
    _real_date(2026, 3, 13): [
        (_real_date(2026, 3, 3), _real_date(2026, 3, 13)),
        (_real_date(2026, 3, 10), _real_date(2026, 3, 13)),
    ],
    _real_date(2026, 3, 27): [
        (_real_date(2026, 3, 17), _real_date(2026, 3, 27)),
        (_real_date(2026, 3, 24), _real_date(2026, 3, 27)),
    ],
    _real_date(2026, 4, 10): [
        (_real_date(2026, 3, 31), _real_date(2026, 4, 10)),
        (_real_date(2026, 4, 7), _real_date(2026, 4, 10)),
    ],
    _real_date(2026, 4, 24): [
        (_real_date(2026, 4, 14), _real_date(2026, 4, 24)),
        (_real_date(2026, 4, 21), _real_date(2026, 4, 24)),
    ],
    _real_date(2026, 5, 8): [
        (_real_date(2026, 4, 28), _real_date(2026, 5, 8)),
        (_real_date(2026, 5, 5), _real_date(2026, 5, 8)),
    ],
}
