"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_recurrence_engine.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.scenario import Scenario
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import BusinessDayShiftEnum
from app.services import entry_service
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import fires_on_day_of_month
from app.services.recurrence import placed_periods, rule_occurrences
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._test_helpers import (
    typed,
    an_entered_day,
    eras_of,
    make_cadence_rule,
    state_template_price,
)


# --- Rule / period objects for the pure pattern-matching tests ---------------


#: The owner every rule and calendar the PURE matcher tests build names.  The
#: ``FakePeriod`` schedule carries no ``user_id``, and
#: ``app.services.recurrence.resolve`` REFUSES a rule paired with another
#: user's schedule -- so one constant is what keeps the two halves agreeing.
#: Until plan step R4b-1 no constant was needed: period selection built the
#: calendar from ``rule.user_id`` itself, so the owner check compared a value
#: against itself and could not fail.
_MATCH_USER_ID = 1


#: The cadence the hand-built schedules below are generated at.  Named because
#: :func:`_calendar` has to STATE it: since plan step C2-b2 a calendar derives
#: every period's end from the NEXT payday, and the last one's from the owner's
#: cadence, so the cadence is an input rather than a column to copy.
_CADENCE_DAYS = 14


#: The scheduling day ``TestTheGenerationSeam``'s bounded bill fires on.
_MONTHLY_DAY = 15


#: The day every hand-built schedule in this file opens on, and therefore the
#: default first occurrence a rule states.
#:
#: **A rule STATES its first occurrence since plan step R7c-b** (ruling R-R16),
#: so each case below names the date its cadence fires on rather than a day of
#: the month the resolver then had to find the first instance of.  The dates
#: are the ones the old derivation answered for those same cases -- a monthly
#: day-15 rule against a schedule opening 2026-01-02 anchored 2026-01-15 -- so
#: every hand-computed assertion in this file still measures what it measured.
_SCHEDULE_OPENS = date(2026, 1, 2)


_CADENCES_OWN_INTERVAL = object()


def build_rule(cadence=EVERY_PERIOD,
               interval_n=_CADENCES_OWN_INTERVAL,
               starts_on=_SCHEDULE_OPENS, nominal_day=None,
               end_date=None):
    """Build a REAL, unsaved ``RecurrenceRule`` for the pure matcher tests.

    ``rule_occurrences`` and ``compute_due_date`` are pure functions of a rule's
    columns, so they need no database -- but they DO need the real column
    surface.  This used to be a hand-rolled ``FakeRule`` stub, which is the
    anti-pattern finding B-17 names: a test double that mirrors the model by
    hand silently drifts from it.  It drifted the moment ``start_date`` was
    added (plan step C9a) -- 17 tests died on ``AttributeError`` rather than on
    a behaviour change, and had the matcher instead read a column the stub
    lacked while the stub still satisfied every assertion, they would have gone
    green on a rule shape that cannot exist.

    An unsaved model instance costs nothing extra (no session, no flush) and
    guarantees the real COLUMN SURFACE: a column the model does not have raises
    ``TypeError`` here, and a column it does have cannot differ in name or type
    from the one production reads.

    It does NOT guarantee real DEFAULTS -- SQLAlchemy applies ``default=`` at
    INSERT, so an unflushed ``RecurrenceRule()`` carries ``None`` for
    ``interval_n`` despite its ``default=1``.  That is why it is passed
    explicitly below, and any future column with a Python-side default the
    resolver branches on must be passed here too, or these tests will exercise
    a rule shape production never sees.

    **``offset_periods`` and ``start_period_id`` LEFT this signature at plan
    step R7b-4**, and not because they stopped having defaults: nothing reads
    either column now.  A rule has ONE opening bound, ``starts_on`` -- the
    paragraph said ``start_date`` until plan step R9, four steps after R7c-c
    dropped that column -- and the every-N-paychecks phase is the ordinal of
    the paycheck that bound falls in.  Keeping them here would let a case
    state a phase the resolver ignores, which is a test that agrees with
    itself.

    Args:
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to build.  Resolved to ``unit_id`` and ``placement_id`` through
            ``ref_cache``, so this needs an app context as the stub's own
            constructor did.
        interval_n: The cadence interval to store.  Defaults to the one
            *cadence* itself names -- 3 for the quarterly constant, 6 for
            the semi-annual one, 1 elsewhere -- because plan step R7c-c
            re-pointed the column onto the two-axis interval, so a
            builder writing a bare ``1`` would state MONTHLY for a case
            that asked for quarterly.  A caller states one to vary the
            rhythm, including the non-positive values the walk's
            refusals are handed.
        starts_on: The rule's first occurrence, and the whole of what it says
            about when it begins -- including which paycheck an every-N-
            paychecks rule phases on.
        nominal_day: The day the rule MEANS when *starts_on*'s month clamped
            it.
        end_date: The rule's closing validity bound.

    Returns:
        An unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # **The cadence is STATED, never decoded.**  An authored rule gets its
    # cadence from the write door; a TRANSIENT one built for a pure test never
    # passes through that door, and forcing a database and a calendar into a
    # pure test would be a worse test rather than a stricter one.
    #
    # It took a closed-set NAME through a lookup table until plan step R9,
    # with a ``cadence is None`` fallback for a name the table did not hold.
    # No caller ever passed one -- the unreadable-cadence cases plant an
    # unmodelled ``unit_id`` on the row after it is built, which is the only
    # way to reach that state now that the write door and the picker both
    # refuse the cadence -- so the fallback went with the table.
    #
    # **The caller's interval lands on the column verbatim**, including the
    # non-positive values ``test_every_n_periods_interval_zero_raises`` and its
    # ``None`` twin hand the WALK.  That is the rule shape those tests need,
    # and the refusal they grade belongs to the walk rather than to this
    # fixture.
    #
    # ``None`` for the one constant that fixes no interval of its own, which
    # is the shape a caller varies.
    #
    # **The unsaved OWNER is plan step R-F6's**, and it is what makes
    # ``rule.user_id`` answerable: a recurrence rule belongs to exactly one
    # definition, so its owner is where the owner is read from -- there is no
    # ``user_id`` column any more.  The template is transient like the rule;
    # neither is added to a session, so these stay pure tests.
    own_interval = 1 if cadence.interval_n is None else cadence.interval_n
    rule = RecurrenceRule(
        interval_n=(
            own_interval if interval_n is _CADENCES_OWN_INTERVAL
            else interval_n
        ),
        unit_id=ref_cache.recurrence_unit_id(cadence.unit),
        placement_id=ref_cache.period_placement_id(cadence.placement),
        shift_id=ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        starts_on=starts_on,
        nominal_day=nominal_day,
        end_date=end_date,
    )
    TransactionTemplate(user_id=_MATCH_USER_ID).recurrence_rule = rule
    return rule


class FakePeriod:
    def __init__(self, id, start_date, end_date, period_index):
        self.id = id
        self.start_date = start_date
        self.end_date = end_date
        self.period_index = period_index


# --- Pure Pattern Matching Tests ---------------------------------------------
#
# These exercise the PUBLIC producer (``rule_occurrences``, and before plan
# step R4b-2 the ``match_periods`` adapter) rather than the five ``_match_*``
# helpers plan step R4a deleted with the reverse matcher.  The behaviour each
# class asserts is unchanged and is what production reads; what changed is that
# the assertion now runs against the function every caller actually calls,
# instead of against a private helper that no longer exists.


def _calendar(periods, cadence_days=_CADENCE_DAYS):
    """Return the owner's :class:`~app.services.pay_calendar.PayCalendar`.

    Period selection takes the OWNER's schedule as one calendar VALUE since
    plan step R4b-1, rather than building one from whatever candidate list it
    was handed.  Named once here so each pattern test states the rule and
    nothing else.

    **It reads the PAYDAY and the id, and nothing else** (plan step C2-b2).
    A period's ordinal and its last covered day are DERIVED from the payday
    set, so passing a row's stored ``end_date`` in would be passing a second
    answer -- the denormalization the pay-calendar arc exists to remove.  Every
    schedule these tests build is contiguous and generated at one cadence, so
    the derived ends reproduce the stated ones exactly.

    Args:
        periods: The schedule as rows carrying ``id`` and ``start_date``, in
            any order -- the derivation sorts by payday.  **The owner's WHOLE
            payday set**: a slice re-indexes from zero, which is plan ledger
            row P26.
        cadence_days: Days between paydays.  Read only for the LAST period's
            end; every other end is dictated by the next payday.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar` for
        :data:`_MATCH_USER_ID`.
    """
    paydays = [(period.id, period.start_date) for period in periods]
    return PayCalendar.from_paydays(
        paydays=paydays,
        eras=eras_of(paydays, cadence_days),
        user_id=_MATCH_USER_ID,
        history_opens_on=None,
    )


def _matched_periods(rule, calendar, effective_from):
    """Return the pay periods *rule* fires in, at or after *effective_from*.

    **The contract the retired ``recurrence_engine.match_periods`` adapter
    had**, expressed over the two PRODUCTION functions that replaced it at plan
    step R4b-2 -- :func:`~app.services.recurrence.rule_occurrences` and
    :func:`~app.services.recurrence.placed_periods`.  It composes them rather
    than reimplementing the filter, deliberately: a helper that re-stated the
    predicate would let the 26 assertions below keep passing while production
    drifted, which is the shape a neutral review flagged in this step's first
    draft.

    Every assertion below is about which PERIODS a rule fires in, which is the
    question the adapter answered, so porting them onto this projection keeps
    each test asserting what it was written to assert.  Occurrence DATES are
    pinned separately, by ``tests/test_services/test_recurrence_occurrence.py``,
    and the projection's own bound semantics by
    :class:`TestThePlacedPeriodsBound`.

    Args:
        rule: The unsaved rule from :func:`build_rule`.
        calendar: The OWNER's whole schedule, from :func:`_calendar`.
        effective_from: Drop periods ENDING before this date; ``None`` applies
            no bound.

    Returns:
        The matched :class:`~app.services.pay_calendar.DerivedPeriod` values,
        ascending by occurrence date, one entry per occurrence.
    """
    return placed_periods(
        rule_occurrences(rule, calendar), ending_on_or_after=effective_from,
    )


def _matches(rule, periods):
    """Return the periods *rule* fires in, over the whole schedule.

    ``effective_from`` is ``None`` -- no lower window bound.  It used to be
    ``periods[0].start_date``, which plan step R4b-1 proved redundant: the
    anchor's own floor is ``PayCalendar.opening_bound()``, so no walk can emit
    an occurrence placed before it.

    Args:
        rule: The unsaved rule from :func:`build_rule`.
        periods: The schedule to match against.

    Returns:
        The matched :class:`~app.services.pay_calendar.DerivedPeriod` values,
        ascending by occurrence date.
    """
    return _matched_periods(rule, _calendar(periods), None)


def _paycheck_covering(seed_user, day):
    """Return the ``budget.pay_periods.id`` of the paycheck covering *day*.

    ``txn.pay_period.start_date <= day <= txn.pay_period.end_date`` until plan
    step ``pay_calendar:C4-c`` dropped ``end_date``.  Containment is the
    calendar's question -- :meth:`DerivedPeriod.covers` is the one rule
    (**R-PC31**) -- and reaching it through ``period_containing`` is what keeps
    a test from open-coding a fourth copy of that comparison.

    Args:
        seed_user: The seeded owner fixture.
        day: The civil day to place.

    Returns:
        The covering period's id.
    """
    covering = calendar_for(seed_user["user"].id).period_containing(day)
    assert covering is not None, f"no paycheck covers {day}"
    return covering.period_id


class _TestRegenerateForTemplateMembers:
    """DB integration tests for regenerate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def _make_envelope_template(self, seed_user):
        """A template whose rows TRACK PURCHASES, as production requires.

        ``_make_template_with_rule`` leaves ``is_envelope`` False, so
        ``Transaction.tracks_purchases`` is False and
        ``entry_service.create_entry`` REFUSES a purchase against its rows.  An
        adversarial review of plan step R10-a caught the retention tests
        building a parent production cannot produce; every test that records a
        purchase starts here instead.
        """
        template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
        template.is_envelope = True
        db.session.flush()
        return template

    def _record_purchase(
        self, txn, seed_user, amount="12.79", settled_on=None,
    ):
        """Record one purchase against *txn* through the REAL entry doors.

        Through ``entry_service`` rather than onto the model, because the door
        is where the behaviour under test actually comes from: it runs the
        entry-capability guard, and its closing ``_resync_after_entry_change``
        is what stamps the parent's ``actual_amount`` and books the purchase's
        ledger legs.  A hand-built ``TransactionEntry`` skips all three, so a
        suite written that way can assert nothing about the ledger -- which is
        exactly what an adversarial review of plan step R10-a found.

        Args:
            txn: The parent transaction.
            seed_user: The seeded user fixture.
            amount: The purchase amount.
            settled_on: The day the bank took the money.  Passed through the
                UPDATE door, which is the only one that accepts it (a purchase
                is recorded before it is observed to have posted).  A purchase
                carrying one is a cash movement of its own -- ruling R-FM -- so
                this is what gives the row ledger legs.
        """
        entry = entry_service.create_entry(
            txn.id,
            seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal(amount)),
                description="Kroger",
                purchased_on=txn.pay_period.start_date,
            ),
        )
        if settled_on is not None:
            entry_service.update_entry(
                entry.id, seed_user["user"].id, settle_day=None if settled_on is None else an_entered_day(settled_on),
            )
        db.session.flush()
        return entry
