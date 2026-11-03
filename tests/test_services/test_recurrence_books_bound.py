"""A recurring definition names no occurrence below its accounts' books.

Plan step ``pay_calendar:C18-a``.

**The rule, in one sentence** (rulings **R-PC85** and **R-PC86**, developer
2026-09-22): a recurring definition's occurrences run from the LATER of its
own first occurrence and the day after the books open on every account it
moves money in, and the day compared is the ROW's cash day -- its due day --
with a day ON the opening inside it (ruling **R-HG**).

**Why it needed stating at all** -- measured on a production clone before the
step: nothing said "no books before an account opens".  An occurrence with no
paycheck to land in is skipped, so the owner's first payday (2026-03-26) stood
in for the books, and a paycheck recorded at 2026-03-12 let the rules fill it:
a Van Payment on 03-22 and a birthday on 03-24, both before Checking's
books, moving 112 of 4,745 figures by exactly the two rows' sum.

What each class covers:

* :class:`TestTheWalkStopsAtTheBooks` -- the pure walk.  One composition
  (``recurrence._placement._placements``) serves both public walks, and these
  pin both, at the boundary from both sides.
* :class:`TestWhatTheBooksDrop` -- ``placements_below_the_books``, the walk's
  answer to what the floor removed, which the doors refusing to strand a row
  ask (rulings R-PC88 / R-PC90 / R-PC91).
* :class:`TestTheCashDayIsCompared` -- ruling R-PC86: a bill scheduled before
  the books but due after them is kept; one due ON the opening is not.
* :class:`TestTheRowDateIsOneDerivation` -- the day the walk bounds by and the
  day ``compute_due_date`` stamps on the row are one body
  (``recurrence._row_day.date_row``); graded against the rule-row reading.
* :class:`TestTheFloorIsTheAccounts` -- the read pass composes the floor from
  every account the definition moves money in, and the pure resolver leaves
  it empty.
* :class:`TestGenerationStopsAtTheBooks` -- the regression PC-500 named: a
  pay period below an account's books is filled with nothing for it.
* :class:`TestTheBooksNeverEndTheRule` -- ruling **R-PC94**: an occurrence the
  books drop still spends a count bound, so an "after N times" rule ends
  where it did before the books bound (the round-2 review's H-A).
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import account_service, obligations_aggregator
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    EndsAfterOccurrences,
    RecurrenceSpec,
    RuleReading,
    author_rule,
    compute_due_date,
    has_ended,
    occurrence_placements,
    occurrence_walk,
    placements_below_the_books,
    projected_occurrence_placements,
    resolve,
)
from app.services import recurrence_engine
from app.services.recurring_definition import read_definition
from tests._test_helpers import make_cadence_rule, state_template_price
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY, MONTHLY_FIRST
from tests.test_services.test_recurrence_resolution import build_calendar

_USER_ID = 1

#: A fortnightly calendar opening one cadence BEFORE the production owner's
#: first payday -- the 2026-03-12 prepend ruling R-PC87 makes recordable.
_PREPENDED = date(2026, 3, 12)


def _monthly(
    day: date, *,
    placement: PeriodPlacementEnum = PeriodPlacementEnum.CONTAINING_DATE,
    times: int | None = None,
):
    """Return a monthly rule's resolved value on the prepended calendar.

    *placement* is which paycheck funds each occurrence -- the one containing
    it, or the first starting on or after it, whose row is then due on that
    later payday.  *times* bounds it to that many occurrences; ``None`` never
    ends.
    """
    spec = RecurrenceSpec(
        user_id=_USER_ID,
        unit=RecurrenceUnitEnum.MONTH,
        starts_on=day,
        placement=placement,
    )
    if times is not None:
        spec = replace(spec, end_bound=EndsAfterOccurrences(count=times))
    return resolve(
        spec, build_calendar(first_payday=_PREPENDED, cadence_days=14, count=12),
    )


def _dates(placements) -> list[date]:
    """Return the occurrence dates of *placements*, in walk order."""
    return [placement.occurrence for placement in placements]


class TestTheWalkStopsAtTheBooks:
    """Both walks drop an occurrence whose row would land on or before the books."""

    def test_an_occurrence_BEFORE_the_books_is_not_walked(self):
        """The Van Payment on 03-22 against Checking's 03-26 opening.

        Unbounded, the prepended 03-12 paycheck places it -- which is the
        PC-500 row.  Bounded, it is gone and the next month's stays.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = _monthly(date(2026, 3, 22))
        assert date(2026, 3, 22) in _dates(
            occurrence_placements(resolved, calendar),
        )

        bounded = replace(resolved, books_opened_on=date(2026, 3, 26))

        walked = _dates(occurrence_placements(bounded, calendar))
        assert date(2026, 3, 22) not in walked
        assert walked[0] == date(2026, 4, 22)

    def test_an_occurrence_ON_the_opening_is_not_walked(self):
        """R-HG's strict reading: the opening is the CLOSE of its day.

        Paired with the case below so the comparison cannot be off by one in
        either direction.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22)), books_opened_on=date(2026, 4, 22),
        )

        walked = _dates(occurrence_placements(bounded, calendar))

        assert date(2026, 4, 22) not in walked
        assert walked[0] == date(2026, 5, 22)

    def test_the_day_AFTER_the_opening_is_walked(self):
        """The other side of the boundary: a day after the books is theirs."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22)), books_opened_on=date(2026, 3, 21),
        )

        assert _dates(occurrence_placements(bounded, calendar))[0] == (
            date(2026, 3, 22)
        )

    def test_the_PROJECTED_walk_drops_the_same_occurrences(self):
        """The loan estimate's walk and generation's cannot disagree.

        Both go through one composition; this pins that the projected one --
        which places past the saved horizon -- agrees with the saved one
        inside it and drops the same pre-books occurrence.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22)), books_opened_on=date(2026, 3, 26),
        )
        horizon = calendar.horizon()

        saved = _dates(occurrence_placements(bounded, calendar))
        projected = _dates(
            projected_occurrence_placements(bounded, calendar, through=horizon),
        )

        assert date(2026, 3, 22) not in projected
        assert projected == saved

    def test_an_UNPLACED_occurrence_is_kept_as_it_always_was(self):
        """An occurrence below the first payday has no row day and no row.

        ``period`` is ``None`` there under ``CONTAINING_DATE``; no reader
        writes, estimates or counts it (ruling R-R64), and the walk's answer
        for it is unchanged by the bound -- it is below every books floor
        this case states and still listed.
        """
        calendar = build_calendar(
            first_payday=date(2026, 3, 26), cadence_days=14, count=12,
        )
        resolved = resolve(
            RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 1, 22),
            ),
            calendar,
        )
        unbounded = occurrence_placements(resolved, calendar)
        bounded = occurrence_placements(
            replace(resolved, books_opened_on=date(2026, 4, 25)), calendar,
        )

        unplaced = [p.occurrence for p in unbounded if p.period is None]
        assert unplaced == [date(2026, 1, 22), date(2026, 2, 22), date(2026, 3, 22)]
        assert [p.occurrence for p in bounded if p.period is None] == unplaced
        # The one PLACED occurrence on or before the floor is what went.
        assert date(2026, 4, 22) in _dates(unbounded)
        assert date(2026, 4, 22) not in _dates(bounded)

    def test_no_floor_walks_every_occurrence_through_the_horizon(self):
        """``None`` is the pure resolver's answer and bounds nothing.

        Graded against the dates themselves (review L2: the first cut
        compared the value with itself and could not fail): every monthly
        occurrence on the prepended calendar, 03-22 included -- the one a
        floor at any of the owner's paydays would drop.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = _monthly(date(2026, 3, 22))

        assert resolved.books_opened_on is None
        assert _dates(occurrence_placements(resolved, calendar)) == [
            date(2026, month, 22) for month in range(3, 9)
        ]


class TestWhatTheBooksDrop:
    """``placements_below_the_books``: the walk's own answer to "what did the floor remove"."""

    def test_it_is_exactly_what_the_floor_removes_from_the_unbounded_walk(self):
        """The floored walk and this partition the unbounded walk, in walk order.

        A floor ON 04-22 drops 03-22 and 04-22 (the opening is the close of
        its day, R-HG) and keeps the rest -- and nothing is in both halves or
        in neither.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = _monthly(date(2026, 3, 22))
        bounded = replace(resolved, books_opened_on=date(2026, 4, 22))

        dropped = placements_below_the_books(bounded, calendar)
        kept = occurrence_placements(bounded, calendar)

        assert _dates(dropped) == [date(2026, 3, 22), date(2026, 4, 22)]
        assert sorted(dropped + kept, key=lambda p: p.occurrence) == list(
            occurrence_placements(resolved, calendar),
        )

    def test_no_floor_drops_nothing(self):
        """A value with no floor is unbounded, so nothing is below it."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )

        assert placements_below_the_books(
            _monthly(date(2026, 3, 22)), calendar,
        ) == ()

    def test_an_ENVELOPE_is_dropped_by_its_paychecks_last_day(self):
        """R-PC89 through the same predicate: the straddled paycheck is kept.

        Every-paycheck on the 03-26 calendar, books opening 04-12 -- three
        days into the 04-09..04-22 paycheck.  As a bill that paycheck's row
        is due 04-09, inside the books, so it is dropped; as an envelope it
        compares 04-22, after them, so only the paychecks wholly on or before
        the opening are.
        """
        calendar = build_calendar(
            first_payday=date(2026, 3, 26), cadence_days=14, count=12,
        )
        every_paycheck = resolve(
            RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.PERIOD,
                starts_on=date(2026, 3, 26),
            ),
            calendar,
        )
        bill = replace(every_paycheck, books_opened_on=date(2026, 4, 12))
        envelope = replace(bill, is_envelope=True)

        assert _dates(placements_below_the_books(bill, calendar)) == [
            date(2026, 3, 26), date(2026, 4, 9),
        ]
        assert _dates(placements_below_the_books(envelope, calendar)) == [
            date(2026, 3, 26),
        ]

    def test_an_UNPLACED_occurrence_is_never_reported(self):
        """Below the first payday there is no row day and no row to strand."""
        calendar = build_calendar(
            first_payday=date(2026, 3, 26), cadence_days=14, count=12,
        )
        resolved = resolve(
            RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 1, 22),
            ),
            calendar,
        )

        dropped = placements_below_the_books(
            replace(resolved, books_opened_on=date(2026, 4, 25)), calendar,
        )

        assert _dates(dropped) == [date(2026, 4, 22)]
        assert all(placement.period is not None for placement in dropped)


class TestTheCashDayIsCompared:
    """Ruling R-PC86: the day the money lands, not the day the rule schedules."""

    def test_a_bill_scheduled_BEFORE_the_books_and_due_AFTER_is_kept(self):
        """A bill scheduled the 24th, funded -- and so due -- on the next payday.

        Scheduled 03-24, one day before the 03-25 opening; funded from the
        first paycheck starting on or after it, 03-26, which is the day its
        money lands (ruling R-R95) -- after the opening, so it is a real
        payment the books do not contain.  It was a separate due day on a
        rule until plan step recurrence:R5-a dropped that column (ruling
        R-R96); a later funding paycheck is the shape where a row's cash day
        and its occurrence still part company.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(
                date(2026, 3, 24),
                placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
            books_opened_on=date(2026, 3, 25),
        )

        placements = occurrence_placements(bounded, calendar)

        assert placements[0].occurrence == date(2026, 3, 24)
        assert bounded.row_date(
            placements[0].occurrence, placements[0].period,
        ) == date(2026, 3, 26)

    def test_the_same_bill_funded_by_its_containing_paycheck_is_dropped(self):
        """Funded by the paycheck CONTAINING it, its money lands on 03-25, inside the opening."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 25)), books_opened_on=date(2026, 3, 26),
        )

        assert _dates(occurrence_placements(bounded, calendar))[0] == (
            date(2026, 4, 25)
        )

    def test_a_PAYCHECK_cadence_is_bounded_by_its_payday(self):
        """A pay-period rule's row is dated from its paycheck's start.

        The 03-12 paycheck's row lands 03-12, before a 03-26 opening, and the
        03-26 paycheck's lands ON it -- both dropped; 04-09 is the first.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = resolve(
            RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.PERIOD,
                starts_on=_PREPENDED,
            ),
            calendar,
        )
        bounded = replace(resolved, books_opened_on=date(2026, 3, 26))

        assert _dates(occurrence_placements(bounded, calendar))[:2] == [
            date(2026, 4, 9), date(2026, 4, 23),
        ]


class TestTheRowDateIsOneDerivation:
    """The day the walk bounds by is the day the written row carries."""

    def test_row_date_agrees_with_compute_due_date_on_every_placed_period(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Graded against the rule-row reading, where the row day is not the occurrence.

        ``compute_due_date`` reads a stored rule; ``row_date`` reads the
        resolved value.  Both hand their coordinates to one body, and this is
        what would fail if either re-derived the day on its own.
        """
        with app.app_context():
            scheduled = seed_periods[0].start_date + timedelta(days=3)
            # BOTH branches of the one body: a rule dated from its day (the
            # row day IS the occurrence), and one funded from the FIRST
            # paycheck starting on or after each occurrence (the row day is
            # that payday) -- where the two part company since plan step
            # recurrence:R5-a dropped the separate due day that used to be
            # this case's lever.
            for name, cadence, row_day_is_occurrence in (
                ("Card bill", MONTHLY, True),
                ("Card bill, first paycheck", MONTHLY_FIRST, False),
            ):
                template = _transaction_template(seed_user, name)
                rule = _monthly_rule(template, scheduled, cadence=cadence)
                ctx = BalanceContext.build(seed_user["user"].id)
                resolved = ctx.resolved_recurrence_of(rule)

                placed = [
                    p for p in occurrence_placements(resolved, ctx.calendar())
                    if p.period is not None
                ]
                assert placed, name
                if row_day_is_occurrence:
                    assert all(
                        resolved.row_date(p.occurrence, p.period) == p.occurrence
                        for p in placed
                    ), name
                else:
                    assert any(
                        resolved.row_date(p.occurrence, p.period) != p.occurrence
                        for p in placed
                    ), "precondition: some row day differs from its occurrence"
                for p in placed:
                    assert resolved.row_date(p.occurrence, p.period) == (
                        compute_due_date(rule, p.occurrence, p.period)
                    ), name


class TestTheFloorIsTheAccounts:
    """The read pass composes the floor; the pure resolver leaves it empty."""

    def test_the_pure_resolver_attaches_no_floor(self):
        """A fact about accounts is not the pure resolver's to read."""
        assert _monthly(date(2026, 3, 22)).books_opened_on is None

    def test_a_transaction_template_takes_its_account_s_opening(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The one account a transaction template moves money in."""
        with app.app_context():
            opened = seed_periods[3].start_date
            account = _account_opened_on(seed_user, "Later books", opened)
            template = _transaction_template(
                seed_user, "Later bill", account_id=account.id,
            )
            rule = _monthly_rule(template, seed_periods[0].start_date)
            ctx = BalanceContext.build(seed_user["user"].id)

            assert ctx.resolved_recurrence_of(rule).books_opened_on == opened

    def test_a_transfer_takes_the_LATER_of_its_two_openings(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Money leaves one account and reaches the other; both books bind.

        The later opening is the binding one, whichever end it is on.
        """
        with app.app_context():
            later = seed_periods[4].start_date
            destination = _account_opened_on(seed_user, "Late savings", later)
            ctx = BalanceContext.build(seed_user["user"].id)
            spec = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=seed_periods[0].start_date,
            )

            class _Transfer:  # pylint: disable=too-few-public-methods
                """A definition naming two accounts, as a transfer does."""

                from_account_id = seed_user["account"].id
                to_account_id = destination.id

            assert ctx.resolved_for(spec, _Transfer()).books_opened_on == later
            swapped = _Transfer()
            swapped.from_account_id, swapped.to_account_id = (
                destination.id, seed_user["account"].id,
            )
            assert ctx.resolved_for(spec, swapped).books_opened_on == later

    def test_a_definition_moving_no_money_has_no_floor(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A payroll line's rule: no template, no account, no floor."""
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            spec = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=seed_periods[0].start_date,
            )

            assert ctx.resolved_for(spec, None).books_opened_on is None


class TestGenerationStopsAtTheBooks:
    """PC-500's regression: no row is generated on or before its books."""

    def test_a_template_on_a_LATER_opened_account_generates_only_above_it(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The seeded account's books open before the schedule; this one's do not.

        Two templates, one rule each, every paycheck: the one on the seeded
        account fills every period; the one on the account opened at the
        fourth paycheck's payday fills only the paychecks AFTER it -- its
        own opening paycheck included in neither, because a row dated ON the
        opening is inside it.
        """
        with app.app_context():
            opened = seed_periods[3].start_date
            account = _account_opened_on(seed_user, "Later books", opened)
            early = _every_paycheck_template(
                seed_user, "Early", seed_user["account"].id, seed_periods,
            )
            late = _every_paycheck_template(
                seed_user, "Late", account.id, seed_periods,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(seed_user["user"].id),
                {period.id for period in seed_periods},
            )

            recurrence_engine.generate_for_template(
                early, schedule, seed_user["scenario"].id,
            )
            recurrence_engine.generate_for_template(
                late, schedule, seed_user["scenario"].id,
            )
            _db.session.flush()

            def due_days(template):
                return sorted(
                    row.due_date for row in _db.session.query(Transaction)
                    .filter_by(template_id=template.id, is_deleted=False)
                )

            assert due_days(early) == sorted(p.start_date for p in seed_periods)
            assert due_days(late) == sorted(
                p.start_date for p in seed_periods if p.start_date > opened
            )


class TestAnEnvelopeComparesItsPaychecksLastDay:
    """Ruling R-PC89: an envelope's row is bounded by its paycheck's END.

    The prepended calendar's first paycheck is 03-12..03-25; an every-
    paycheck rule dates its row on the payday, 03-12.  Books opening 03-20
    open INSIDE that paycheck: a bill dated 03-12 is inside the opening, an
    envelope spent across 03-12..03-25 is still owed from 03-21 on.
    """

    @staticmethod
    def _every_paycheck(*, books: date, is_envelope: bool):
        """Return an every-paycheck value bounded at *books*, envelope or not."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = resolve(
            RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.PERIOD,
                starts_on=_PREPENDED,
            ),
            calendar,
        )
        return calendar, replace(
            resolved, books_opened_on=books, is_envelope=is_envelope,
        )

    def test_the_envelope_of_the_paycheck_the_books_open_inside_is_walked(self):
        """Books 03-20: the envelope's paycheck ends 03-25, after them -- kept."""
        calendar, envelope = self._every_paycheck(
            books=date(2026, 3, 20), is_envelope=True,
        )

        assert _dates(occurrence_placements(envelope, calendar))[0] == _PREPENDED

    def test_a_BILL_in_the_same_paycheck_is_not_walked(self):
        """The same rule as a bill: its row's due day 03-12 is inside the books."""
        calendar, bill = self._every_paycheck(
            books=date(2026, 3, 20), is_envelope=False,
        )

        assert _dates(occurrence_placements(bill, calendar))[0] == (
            date(2026, 3, 26)
        )

    def test_an_envelope_whose_paycheck_ENDS_ON_the_opening_is_not_walked(self):
        """R-HG's strict reading on the paycheck's last day: 03-25 is inside."""
        calendar, envelope = self._every_paycheck(
            books=date(2026, 3, 25), is_envelope=True,
        )

        assert _dates(occurrence_placements(envelope, calendar))[0] == (
            date(2026, 3, 26)
        )

    def test_an_envelope_whose_paycheck_ends_the_day_AFTER_is_walked(self):
        """The other side of the boundary, so ``>=`` for ``>`` fails one."""
        calendar, envelope = self._every_paycheck(
            books=date(2026, 3, 24), is_envelope=True,
        )

        assert _dates(occurrence_placements(envelope, calendar))[0] == _PREPENDED

    def test_the_PROJECTED_walk_agrees_for_an_envelope(self):
        """The loan estimate's walk takes the same picker as generation's."""
        calendar, envelope = self._every_paycheck(
            books=date(2026, 3, 20), is_envelope=True,
        )
        horizon = calendar.horizon()

        assert _dates(
            projected_occurrence_placements(envelope, calendar, through=horizon),
        ) == _dates(occurrence_placements(envelope, calendar))


class TestTheEnvelopeFlagIsTheDefinitions:
    """The read pass attaches the definition's envelope flag beside its floor."""

    def test_an_envelope_and_a_bill_stating_one_spec_resolve_APART(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The memo keys on the flag: a bill never walks an envelope's value.

        Two templates on one account, one cadence each, the same spec: they
        share the floor and differ in the flag, so they are two values -- a
        memo keyed by (spec, floor) alone would hand the second the first's.
        """
        with app.app_context():
            opened = seed_periods[3].start_date
            account = _account_opened_on(seed_user, "Later books", opened)
            envelope = _transaction_template(
                seed_user, "Groceries", account_id=account.id, is_envelope=True,
            )
            bill = _transaction_template(
                seed_user, "Phone", account_id=account.id,
            )
            envelope_rule = _monthly_rule(envelope, seed_periods[0].start_date)
            bill_rule = _monthly_rule(bill, seed_periods[0].start_date)
            ctx = BalanceContext.build(seed_user["user"].id)

            as_envelope = ctx.resolved_recurrence_of(envelope_rule)
            as_bill = ctx.resolved_recurrence_of(bill_rule)

            assert as_envelope.is_envelope is True
            assert as_bill.is_envelope is False
            assert as_envelope.books_opened_on == as_bill.books_opened_on == opened
            assert replace(as_envelope, is_envelope=False) == as_bill

    def test_a_transfer_is_never_an_envelope(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A transfer template carries no flag; it compares its due day."""
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            spec = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=seed_periods[0].start_date,
            )

            class _Transfer:  # pylint: disable=too-few-public-methods
                """A definition naming two accounts and no envelope flag."""

                from_account_id = seed_user["account"].id
                to_account_id = seed_user["account"].id

            assert ctx.resolved_for(spec, _Transfer()).is_envelope is False

    def test_generation_writes_the_envelope_of_the_straddled_paycheck(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Books opening INSIDE the fourth paycheck: the envelope's row is written.

        The bill on the same account and cadence starts one paycheck later,
        because its row is dated on the payday, which the books already hold.
        """
        with app.app_context():
            opened = seed_periods[3].start_date + timedelta(days=3)
            account = _account_opened_on(seed_user, "Mid-paycheck books", opened)
            envelope = _every_paycheck_template(
                seed_user, "Groceries", account.id, seed_periods,
                is_envelope=True,
            )
            bill = _every_paycheck_template(
                seed_user, "Phone", account.id, seed_periods,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(seed_user["user"].id),
                {period.id for period in seed_periods},
            )

            for template in (envelope, bill):
                recurrence_engine.generate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            _db.session.flush()

            def first_due(template):
                return min(
                    row.due_date for row in _db.session.query(Transaction)
                    .filter_by(template_id=template.id, is_deleted=False)
                )

            assert first_due(envelope) == seed_periods[3].start_date
            assert first_due(bill) == seed_periods[4].start_date


class TestTheBooksNeverEndTheRule:
    """Ruling R-PC94: the books decide which occurrences become rows, never when the rule ends.

    The round-2 review's H-A, measured before the fix: a monthly rule "2
    times" from 2026-01-02 on an account whose books open 2026-01-16 walked
    only 02-02, and the Recurring screen counted it at ``$120.00`` a month
    for good where dev counted ``$0.00`` -- the walk spent its count on 01-02
    while the closing counted only the rows' half.
    """

    def test_the_split_IS_the_two_public_answers(self):
        """One walk: its halves are what both public walks answer, and together all of it."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22)), books_opened_on=date(2026, 3, 26),
        )

        walk = occurrence_walk(bounded, calendar)

        assert walk.kept == occurrence_placements(bounded, calendar)
        assert walk.below_the_books == placements_below_the_books(
            bounded, calendar,
        )
        assert _dates(walk.below_the_books) == [date(2026, 3, 22)]
        assert sorted(_dates(walk.kept) + _dates(walk.below_the_books)) == (
            _dates(occurrence_placements(
                replace(bounded, books_opened_on=None), calendar,
            ))
        )

    def test_a_count_spent_below_the_books_still_ends_the_rule(self):
        """Twice from 03-22 over books opening 03-26: 03-22 happened, 04-22 is the second.

        The firing control is the same reading with the books' half left
        off -- the shape the closing read before this ruling -- which never
        ends.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22), times=2),
            books_opened_on=date(2026, 3, 26),
        )
        walk = occurrence_walk(bounded, calendar)
        reading = RuleReading(
            resolved=bounded,
            placements=walk.kept,
            horizon=calendar.horizon(),
            below_the_books=walk.below_the_books,
        )
        after_the_second = date(2026, 4, 23)

        assert _dates(reading.placements) == [date(2026, 4, 22)]
        assert reading.bound_reading().occurrences == (
            date(2026, 3, 22), date(2026, 4, 22),
        )
        assert bounded.closing.has_closed(
            on=after_the_second, reading=reading.bound_reading,
        )
        rows_only = replace(reading, below_the_books=())
        assert not bounded.closing.has_closed(
            on=after_the_second, reading=rows_only.bound_reading,
        )

    def test_the_recurring_totals_drop_a_rule_spent_below_the_books(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The review's probe, through the read pass and the Recurring screen's reader.

        Both templates sit on an account whose books open at the second
        paycheck and start on the first paycheck's first day, so the books
        drop each one's first occurrence.  "Twice" has spent its count by
        the pass's today and leaves the monthly totals; "twelve times" has
        not, and still counts its $10.00 -- the control that the same account
        and start are not simply dropped.
        """
        with app.app_context():
            later = _account_opened_on(
                seed_user, "Later books", seed_periods[1].start_date,
            )
            start = seed_periods[0].start_date
            spent = _transaction_template(seed_user, "Twice", account_id=later.id)
            _count_bounded_monthly_rule(spent, start, times=2)
            ongoing = _transaction_template(
                seed_user, "Twelve times", account_id=later.id,
            )
            _count_bounded_monthly_rule(ongoing, start, times=12)
            ctx = BalanceContext.build(seed_user["user"].id)

            reading = read_definition(spent, ctx)

            assert _dates(reading.below_the_books) == [start]
            assert len(reading.placements) == 1
            assert reading.placements[0].occurrence < ctx.as_of
            assert has_ended(spent.recurrence_rule, reading, on=ctx.as_of)
            assert obligations_aggregator.template_monthly_or_none(
                spent, ctx,
            ) is None
            assert obligations_aggregator.template_monthly_or_none(
                ongoing, ctx,
            ) == Decimal("10.00")


# ── helpers ──────────────────────────────────────────────────────────────


def _account_opened_on(seed_user, name, opened_on):
    """Create a checking account whose books open on *opened_on*."""
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name=name,
            anchor_balance=Decimal("0.00"),
            observed_on=opened_on,
        ),
    )
    _db.session.flush()
    return account


def _transaction_template(seed_user, name, *, account_id=None, is_envelope=False):
    """Create a live, priced expense template on *account_id* (default seeded)."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account_id or seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        is_envelope=is_envelope,
    )
    _db.session.add(template)
    _db.session.flush()
    # A generated row stores no figure and is priced by this series on its own
    # due date (plan step balance:X-au-e), so a template must state one.
    state_template_price(template, Decimal("10.00"))
    return template


def _monthly_rule(template, starts_on, *, cadence=MONTHLY):
    """Author a monthly rule onto *template* through the write door.

    *cadence* is ``MONTHLY`` (funded by the paycheck containing each
    occurrence) or ``MONTHLY_FIRST`` (by the first starting on or after it).
    """
    rule = make_cadence_rule(template, cadence, starts_on=starts_on)
    _db.session.refresh(template)
    return rule


def _count_bounded_monthly_rule(template, starts_on, *, times):
    """Author a monthly rule ending after *times* occurrences onto *template*."""
    rule = author_rule(
        RecurrenceSpec(
            user_id=template.user_id,
            unit=RecurrenceUnitEnum.MONTH,
            starts_on=starts_on,
            end_bound=EndsAfterOccurrences(count=times),
        ),
        calendar_for(template.user_id),
        template,
    )
    _db.session.flush()
    _db.session.refresh(template)
    return rule


def _every_paycheck_template(
    seed_user, name, account_id, seed_periods, *, is_envelope=False,
):
    """Create a template on *account_id* recurring every paycheck."""
    template = _transaction_template(
        seed_user, name, account_id=account_id, is_envelope=is_envelope,
    )
    make_cadence_rule(
        template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
    )
    _db.session.refresh(template)
    return template
