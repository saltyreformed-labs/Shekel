"""The paycheck an account's correction is filed in: the one containing its own day."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db as _db
from app.models.pay_period import PayPeriod
from app.services import balance_at
from app.services.cash_flow_set import CashFlowSet
from app.services.pay_calendar import PayCalendarError
from app.utils.dates import to_display_date
from tests._ledger_reads import (
    account_net,
    linked_key_of,
    scenario_lines,
    source_lines,
)
from tests._test_helpers import (
    create_loan_account,
    derived_span,
    restate_account_opening,
    settle_instant_on,
)

from ._helpers import (
    _ONE_HOUR,
    _PINNED_OPENING_DAY,
    _add_assertion,
    _correction_net_in_period,
    _make_account,
)


class TestCorrectionPeriodAttribution:
    """An anchor correction books in the period containing its OWN day.

    The production defect these pin (finding N-161, plan step X-ai-r), and it
    had two halves.  The reconcile key was ``(source kind, entry date)`` with
    no period, so two assertions observed on ONE civil day could not be told
    apart and a reversal of one period's postings was filed against another;
    and the period the entry was FILED under was copied from the history row's
    stored ``pay_period_id``, which is a CACHE of the same day-to-period
    derivation and can be split from its own day by a clock.  Measured on a
    production clone: corrections of ``+$386.85`` and ``-$186.85`` had posted
    as ``+$3,054.36`` in one period and ``-$2,854.36`` in the next.

    Production's row was recorded 21:28 Eastern on a period's LAST day while
    its stored ``pay_period_id`` named the NEXT one; ruling R-EO deleted that
    column, so what these pin now is the day rule alone: the ledger files
    every correction in the period containing the day it asserts, which is
    what ruling R-DH states
    (``account_service.resolve_anchor_period_id``: "the period is DERIVED from
    the day, not chosen beside it") and what the grid's "Book vs bank" row
    already does (``balance_at._cash_periods._assertion_sums`` buckets on
    ``observed_on``).  The worked-out read files each correction by its day
    (ruling plan#2519), so the rule is the read's own.
    """

    _ASSERTION_DAY = date(2026, 4, 3)
    _PRE_SCHEDULE_DAY = date(2023, 6, 14)

    def _period_containing_the_day(self, seed_user):
        """Add (and return) a pay period whose range contains the assertion day."""
        period = PayPeriod(
            user_id=seed_user["user"].id,
            start_date=date(2026, 3, 21),
        )
        _db.session.add(period)
        _db.session.flush()
        return period

    def _period_after_the_day(self, seed_user):
        """Add (and return) the period that STARTS the day after the assertion."""
        period = PayPeriod(
            user_id=seed_user["user"].id,
            start_date=date(2026, 4, 4),
        )
        _db.session.add(period)
        _db.session.flush()
        return period

    def test_a_correction_on_a_paychecks_last_day_files_in_that_paycheck(
        self, app, db, seed_user,
    ):
        """Two assertions on a period's LAST day book in that period, not the next.

        Two assertions an hour apart on one civil day that is a period's LAST
        day.  Both corrections must book in the period CONTAINING the day --
        ``+100.00`` and ``-50.00`` merging to one ``+50.00`` line there -- and
        the period starting the next day must hold nothing.  It was
        ``test_the_stored_period_is_ignored_and_the_day_decides``, its second
        row filed against the next period by the row's own ``pay_period_id``,
        until ruling R-EO deleted that column; the fixture's period argument
        went unread from then until plan task plan#3079 deleted it.

        The ledger otherwise disagrees with the grid's "Book vs bank" row,
        which buckets the same corrections on ``observed_on``, by the whole
        correction.  Measured on a production clone: deriving agrees with the
        grid on all 61 periods, reading the stored column on 59 of 61.

        Own line ``500 opening + 100 - 50 = 550.00``, the later value.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Opened ON the pinned day, so the origination IS the assertion
            # this case reasons from: appending a second would leave the
            # factory's own row standing on a LATER day, as a correction the
            # case did not write (plan step X-f3c-2c).
            account = _make_account(
                seed_user, "500.00", observed_on=_PINNED_OPENING_DAY,
            )
            containing = self._period_containing_the_day(seed_user)
            following = self._period_after_the_day(seed_user)
            at = settle_instant_on(self._ASSERTION_DAY)
            _add_assertion(account, "600.00", at)
            _add_assertion(account, "550.00", at + _ONE_HOUR)
            # The preconditions: ONE civil day, and the next period starting
            # the day after it.
            assert to_display_date(at) == to_display_date(at + _ONE_HOUR)
            assert following.start_date > self._ASSERTION_DAY
            _db.session.commit()

            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, containing.id,
            ) == Decimal("50.00")
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, following.id,
            ) == Decimal("0.00")
            assert {
                line.entry_date for line in source_lines(
                    _db.session, linked_key_of(_db.session, account.id), scenario_id,
                    PostingSourceEnum.ACCOUNT_TRUEUP,
                )
            } == {self._ASSERTION_DAY}
            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("550.00")

    def test_a_period_appearing_around_the_day_refiles_the_correction(
        self, app, db, seed_user,
    ):
        """Generating the period that contains the day moves the correction into it.

        The R2 case on the cash side, and it is reachable by an ordinary
        schedule extension.  ``seed_user`` carries only its 2024 bootstrap
        period, so a true-up dated 2026 has no containing period and
        :meth:`app.services.pay_calendar.PayCalendar.filing_period` clamps it
        to that one -- the state a user reaches by asserting a balance past the
        end of their generated schedule.  ``600 - 500 = +100.00``.  Once the
        period containing the day exists, the read files the correction there
        and the fallback holds nothing.

        The total does not move: this is an attribution change, not a
        revaluation.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            fallback = seed_user["bootstrap_period"]
            # Opened ON the pinned day, for the reason the sibling case states
            # (plan step X-f3c-2c).
            account = _make_account(
                seed_user, "500.00", observed_on=_PINNED_OPENING_DAY,
            )
            _add_assertion(
                account, "600.00", settle_instant_on(self._ASSERTION_DAY),
            )
            _db.session.commit()
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, fallback.id,
            ) == Decimal("100.00")

            containing = self._period_containing_the_day(seed_user)
            _db.session.commit()

            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, fallback.id,
            ) == Decimal("0.00")
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, containing.id,
            ) == Decimal("100.00")
            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("600.00")

    def test_the_cash_and_loan_halves_file_one_day_under_one_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A cash opening and a loan opening on ONE day file in ONE period.

        Pay-calendar plan step **C2-d** gave the account and loan corrections
        one way to a calendar (finding **N-169**); the worked-out read hands
        both families the one calendar it was given, and each files a
        correction by
        :meth:`~app.services.pay_calendar.PayCalendar.filing_period`.  The
        day chosen is BEFORE the whole schedule, the clamp branch, because
        inside the schedule every candidate rule agrees and the assertion would
        grade nothing.

        ``seed_periods`` opens 2026-01-02, so a 2023-06-14 event precedes every
        payday and both halves must clamp into ``period_index`` 0.  It sat in
        the class grading the writers' shared filing door
        (``_posting_reconcile.filing_calendar_for``) until plan step plan#2914
        deleted the door with the store.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            earliest = min(seed_periods, key=lambda p: derived_span(p).period_index)
            assert earliest.start_date > self._PRE_SCHEDULE_DAY, (
                "the fixture must place the probe day before every payday"
            )

            account = _make_account(seed_user, "500.00")
            # **The BOOKS are restated onto the probe day, and the assertion is
            # left where it is** (plan step X-f3c-2c).  What files here is the
            # account's BOOKS and not its assertion: the ``account_opening``
            # line is dated on ``budget.account_openings.opened_on`` (plan step
            # X-f3c-2a).  This case used to move the ASSERTION one day after the
            # probe day and let ruling **R-HG**'s day-before rule carry the
            # books there -- two acts spelled as one, and the assertion half is
            # now impossible twice over: the table is append-only, and
            # ``resolve_observation_day`` floors an assertion at the calendar's
            # own first day, which is 2026-01-02 here.  Restating the opening is
            # the act that was always meant, and it is a production door
            # (plan step X-f3c-2b-2a).
            restate_account_opening(
                _db.session, account, self._PRE_SCHEDULE_DAY,
            )
            _db.session.commit()

            create_loan_account(
                seed_user, _db.session, name="Pre-schedule Loan",
                principal=Decimal("200000.00"),
                origination_date=self._PRE_SCHEDULE_DAY,
                rate=Decimal("0.06"),
            )
            _db.session.commit()

            # Filtered on the DAY: what files there is the books the case
            # restated, and the question is the paycheck those lines name.
            lines = scenario_lines(_db.session, scenario_id)
            for source in (
                PostingSourceEnum.ACCOUNT_OPENING,
                PostingSourceEnum.LOAN_OPENING,
            ):
                source_kind_id = ref_cache.posting_source_id(source)
                assert {
                    line.pay_period_id for line in lines
                    if line.source_kind_id == source_kind_id
                    and line.entry_date == self._PRE_SCHEDULE_DAY
                } == {earliest.id}, source

    def test_an_owner_with_no_materialised_period_is_refused_loudly(
        self, app, db, seed_user,
    ):
        """No period to file a correction in is a LOUD failure of the read.

        The read DERIVES each correction's paycheck from its day, so an empty
        calendar is the one state that could silently mis-file every
        correction an account has (finding **N-192**).  What keeps it out of
        reach is code rather than a constraint -- registration opens a period
        before it creates the default account, ``truncate_pay_periods`` always
        keeps the period its ``keep_through_period_id`` names (plan step C3-a),
        and ``reset_pay_periods`` regenerates before returning -- so the
        refusal is deliberate rather than defensive.

        **It is the calendar's ``PayCalendarError``, refused in ONE place**
        (the ruling of 2026-08-10): a second refusal at the loader, naming the
        account, could never fire where ``filing_period`` would not, and its
        predicate had already drifted from the calendar's.  It reached the
        stored ledger's reconcile until plan step plan#2914 deleted the store;
        the read files through the same ``filing_period``.
        """
        with app.app_context():
            owner_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            _make_account(seed_user, "500.00")
            _db.session.query(PayPeriod).filter_by(user_id=owner_id).delete()
            _db.session.commit()

            with pytest.raises(
                PayCalendarError,
                match=rf"user {owner_id} has no materialised pay period",
            ):
                scenario_lines(_db.session, scenario_id)
            _db.session.rollback()


class TestLedgerAgreesWithTheGridOnAssertionPeriods:
    """The worked-out ledger and the grid bucket a true-up into the SAME period.

    The property ruling R-EA was decided on, pinned as a test rather than left
    as a measurement in a docstring (plan step X-ai-r; finding N-169).  Two
    independent implementations answer "which pay period did this balance
    assertion book in": the worked-out read files each correction line through
    ``pay_calendar.PayCalendar.filing_period`` (``ledger_lines._account_anchors``);
    the grid (``balance_at._cash_periods._assertion_sums``) buckets the walk's
    corrections by ``spans.containing(observed_on)`` and renders the result as
    the grid's "Book vs bank" row.  They agreed on 61 of 61 periods of a
    production clone when the ruling was taken; nothing in the tree held them
    together afterwards, which is how the ledger and the grid came to disagree
    by a whole correction in the first place.

    **Scoped to TRUE-UPS, and that is not a convenience.**  ``_assertion_sums``
    folds ``anchor_corrections[1:]`` -- ruling R-I moves the OPENING into the
    fold's seed, where it back-projects rather than stepping the balance on its
    own day -- while the ledger keeps an ``account_opening`` line in a period.
    Comparing the opening would compare two different questions.
    """

    def test_trueup_periods_match_the_grids_book_vs_bank_row(
        self, app, db, seed_user,
    ):
        """Every period's true-up net on the read equals the grid's book_vs_bank.

        Three assertions across two periods on one account, so the comparison
        has something to disagree about: +100.00 and -50.00 in the first
        period and +250.00 in the second.  The ledger's per-period true-up net
        must equal the figure the grid renders for that period, cell for cell,
        with at least one period non-zero (a comparison of all-zeros grades
        nothing).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Opened ON the pinned day, so the origination IS the assertion
            # this case reasons from: appending a second would leave the
            # factory's own row standing on a LATER day, as a correction the
            # case did not write (plan step X-f3c-2c).
            account = _make_account(
                seed_user, "500.00", observed_on=_PINNED_OPENING_DAY,
            )
            first = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2026, 3, 21),
            )
            second = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2026, 4, 4),
            )
            _db.session.add_all([first, second])
            _db.session.flush()
            _add_assertion(
                account, "600.00", settle_instant_on(date(2026, 3, 25)),
            )
            _add_assertion(
                account, "550.00", settle_instant_on(date(2026, 4, 1)),
            )
            _add_assertion(
                account, "800.00", settle_instant_on(date(2026, 4, 10)),
            )
            _db.session.commit()

            ctx = balance_at.BalanceContext.build(
                seed_user["user"].id, as_of=date(2026, 4, 17),
            )
            periods = (
                _db.session.query(PayPeriod)
                .filter_by(user_id=seed_user["user"].id)
                .order_by(PayPeriod.start_date)
                .all()
            )
            grid = balance_at.grid_balance_view(CashFlowSet.single(account), ctx)

            compared = 0
            for period in periods:
                column = grid.columns.get(period.id)
                if column is None:
                    continue
                assert _correction_net_in_period(
                    account.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_TRUEUP, period.id,
                ) == column.book_vs_bank
                compared += 1
            assert compared == len(periods)
            # The comparison has teeth only if something is non-zero: the
            # first period books 600-500 = +100.00 then 550-600 = -50.00
            # (net +50.00) and the second 800-550 = +250.00.
            assert grid.columns[first.id].book_vs_bank == Decimal("50.00")
            assert grid.columns[second.id].book_vs_bank == Decimal("250.00")
