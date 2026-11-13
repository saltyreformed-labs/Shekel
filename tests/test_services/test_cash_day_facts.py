"""
Shekel Budget App -- The cash fold read at DAY grain, split into its tiers.

Pins ``balance_at.cash_daily_facts_series`` and the fold behind it (plan step
**bank_import:X-f6e-2**).  A reader comparing this account against a record
kept OUTSIDE the app -- the bank's own statement -- needs a day's MOVEMENT
split three ways, because a balance difference alone cannot tell the three
apart and they mean different things: money the app's rows say moved, a
balance the owner ASSERTED, and a plan that has not happened.

**The load-bearing property is the identity**::

    balance(d) - balance(d - 1) == recorded(d) + asserted(d) + planned(d)

It holds by construction today -- ``_running_steps`` assembles exactly
``dated_deltas`` plus the opening's compensator plus the planned nets -- and
this suite exists because that is a property of TODAY's assembly rather than of
the contract.  A fourth tier joining the running total with no entry here would
otherwise be absorbed silently into whichever component a consumer derived by
subtraction, and labelled as something it is not.

**The OPENING assertion is the case worth naming.**  Ruling R-I moves its
correction into the SEED and books an equal-and-opposite step on its own day,
so its net contribution there is ZERO: the figure it establishes is part of the
level every later day is measured from, not money moving that day.  Deriving
``asserted`` from the walk's corrections without excluding it puts the opening's
whole delta on its day -- ``$798.03`` on the developer's real account -- and the
identity above is what catches it.

Scenario: the ``seed_periods`` biweekly calendar from 2026-01-02, whose account
opens with a $1000.00 assertion.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.account_opening import AccountOpening
from app.models.ref import AccountType
from app.services import account_service, balance_at, cash_ledger
from app.services.balance_at import BalanceContext
from app.services.balance_at._assertions import assertion_corrections
from app.services.scenario_resolver import get_baseline_scenario
from tests._test_helpers import (
    append_balance_assertion,
    default_settle_day,
    one_off_row_of,
    open_books_before_the_first_assertion,
    settle_day_columns,
    cover_bare_settled_row,
)
from tests.test_services.test_cash_fold._helpers import _instant

_ZERO = Decimal("0.00")


def _settled(
    db, seed_user, period, name, amount, day, *, is_income=False, account=None,
):
    """Insert one SETTLED row whose cash moved on *day*.

    Args:
        account: The account to file it against, or ``None`` for the seeded
            Checking.  A case that opens its own account states it.
    """
    status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=(seed_user["account"] if account is None else account).id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
        ),
    )
    txn.status_id = status_id
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(day).items():
        setattr(txn, _column, _value)
    db.session.flush()
    cover_bare_settled_row(db.session, txn, amount, amount)
    return txn


def _projected(db, seed_user, period, name, amount, due_date):
    """Insert one still-PROJECTED row -- the fold's planned tier."""
    status_id = ref_cache.status_id(StatusEnum.PROJECTED)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        due_date=due_date,
    )
    txn.status_id = status_id
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(default_settle_day(period, status_id)).items():
        setattr(txn, _column, _value)
    db.session.flush()
    if default_settle_day(period, status_id) is not None:
        cover_bare_settled_row(db.session, txn, amount)
    return txn


def _account_opened_on(db, seed_user, day, name="Second Checking"):
    """Create a plain Checking account whose ORIGINATION asserts on *day*.

    The production act -- ``account_service.create_account`` writes the opening
    record and the origination assertion from one day the owner supplies -- so
    a case needing an opening on a day it names gets one without editing a
    stored assertion (plan step X-f3c-2c).

    Args:
        db: The SQLAlchemy ``db`` fixture.
        seed_user: The ``seed_user`` fixture dict.
        day: The civil day the opening balance is asserted for.
        name: The account name, unique per owner.

    Returns:
        The created :class:`~app.models.account.Account`, flushed.
    """
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name=name,
            anchor_balance=Decimal("1000.00"),
            observed_on=day,
        ),
    )
    db.session.flush()
    return account


def _series(seed_user, first_day, last_day):
    """Return the seam's day-facts series for the seed user's account."""
    return balance_at.cash_daily_facts_series(
        seed_user["account"],
        BalanceContext.build(seed_user["user"].id),
        first_day,
        last_day,
    )


class TestTheThreeTiersSumToTheDaysMovement:
    """The identity, and what breaks it."""

    def test_a_settled_row_lands_wholly_in_RECORDED(
        self, app, seed_user, seed_periods, db,
    ):
        """Cash the app's own row moved is the only tier that saw money move."""
        with app.app_context():
            _settled(
                db, seed_user, seed_periods[6], "Rent", "500.00",
                date(2026, 4, 6),
            )
            db.session.commit()

            facts = _series(
                seed_user, date(2026, 4, 5), date(2026, 4, 7),
            ).facts

            assert facts[date(2026, 4, 6)].recorded == Decimal("-500.00")
            assert facts[date(2026, 4, 6)].asserted == _ZERO
            assert facts[date(2026, 4, 6)].planned == _ZERO

    def test_a_balance_assertion_lands_wholly_in_ASSERTED(
        self, app, seed_user, seed_periods, db,
    ):
        """A true-up is the owner correcting the app, never money moving.

        Reported apart from ``recorded`` because a report that added the two
        would say the bank should have shown the correction -- and would read
        a day where a true-up exactly cancels a real error as agreement.
        """
        with app.app_context():
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1300.00"), _instant(2026, 4, 6),
            )
            db.session.commit()

            facts = _series(
                seed_user, date(2026, 4, 5), date(2026, 4, 7),
            ).facts

            assert facts[date(2026, 4, 6)].recorded == _ZERO
            assert facts[date(2026, 4, 6)].asserted == Decimal("300.00")

    def test_a_RESTATED_opening_makes_the_first_assertion_a_real_movement(
        self, app, seed_user, seed_periods, db,
    ):
        """The FIRING control that the first assertion is counted at all.

        **Every other case in this file has a zero here, and that is the
        problem this test exists for.**  A fixture built through
        ``create_account`` records the opening equity as the balance its owner
        typed, so the first assertion agrees with the books and its correction
        is ``$0.00`` -- which means ``day_facts`` summing every correction and
        ``day_facts`` skipping the first one give the identical answer, and an
        adversarial review measured exactly that: re-introducing the deleted
        ``corrections[1:]`` slice left 191 tests green.

        So this restates the opening to a DIFFERENT figure and asserts the
        difference lands on the assertion's own day.  Hand-computed: the books
        are restated to open at ``$400.00`` where the account was declared at
        ``$1,000.00``, so the first assertion now corrects the books UP by
        ``$600.00`` and that is what its day's ``asserted`` must read.  Under
        the old slice it would read ``$0.00``.
        """
        with app.app_context():
            account = seed_user["account"]
            governing = (
                db.session.query(AccountOpening)
                .filter_by(account_id=account.id)
                .order_by(
                    AccountOpening.created_at.desc(), AccountOpening.id.desc(),
                )
                .first()
            )
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=governing.opened_on,
                opening_equity=Decimal("400.00"),
                source_id=governing.source_id,
            ))
            db.session.commit()

            walk = cash_ledger.walk_cash_ledger(
                account.id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = assertion_corrections(walk)[0]
            assert walk.opening.opening_equity == Decimal("400.00")
            assert opening.delta == Decimal("600.00")

            facts = _series(
                seed_user, opening.observed_on, opening.observed_on,
            ).facts
            assert facts[opening.observed_on].asserted == Decimal("600.00")

    def test_the_OPENING_EQUITY_is_level_and_the_first_assertion_is_movement(
        self, app, seed_user, seed_periods, db,
    ):
        """What an account opened with moves no day; a CORRECTION to it does.

        The two halves of plan step **X-f3c-2a**, on one fixture:

        * the account's OPENING EQUITY is the fold's SEED, so it appears in no
          day's ``asserted`` at all -- it is the level every day is measured
          FROM, and putting it on a day would ask the bank to explain a
          movement that never happened (on the developer's real account that
          would have been ``$798.03`` of phantom movement);
        * the FIRST ASSERTION is an ordinary correction, so its delta lands on
          its own day like any other.  Here it is ``$0.00`` because the account
          was created through ``create_account``, which records the opening
          equity as the balance the owner typed -- so the books and the
          declaration agree by construction.

        *This test asserted ``opening.delta != 0`` and ``asserted == 0`` until
        X-f3c-2a: the opening's delta WAS the account's opening equity then, and
        the fold had to hold it out of every day to keep it off the bank's
        books.  The exclusion is gone because the conflation is.*
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = assertion_corrections(walk)[0]

            facts = _series(
                seed_user, opening.observed_on, opening.observed_on,
            ).facts

            # The seed is the level, and it is a real figure -- so "asserted is
            # zero" below is a statement about where that figure lives, not an
            # empty account.
            assert walk.opening.opening_equity != _ZERO
            # The declaration agrees with the books it opened, so it corrects
            # nothing and the day carries no assertion movement.
            assert opening.delta == _ZERO
            assert facts[opening.observed_on].asserted == _ZERO

    def test_the_three_tiers_sum_to_the_days_change_in_balance(
        self, app, seed_user, seed_periods, db,
    ):
        """The identity, over a span carrying all three kinds at once.

        Asserted over EVERY day of the range rather than the interesting ones,
        because a tier appearing where none of the three accounts for it is
        exactly what this is here to catch, and it would appear on whichever
        day the new behaviour happened to fall on.

        **The range starts at the account's OPENING assertion**, and that is a
        correction this test needed rather than a flourish: written over April
        alone it ran entirely after the opening, so the mutation that stops
        excluding the opening from ``asserted`` left it green -- the seeded
        calendar opens in 2024 and April 2026 never sees that day.  A test of
        an identity must span the one day the identity has a special case on.
        """
        with app.app_context():
            _settled(
                db, seed_user, seed_periods[6], "Rent", "500.00",
                date(2026, 4, 6),
            )
            _settled(
                db, seed_user, seed_periods[6], "Salary", "2000.00",
                date(2026, 4, 9), is_income=True,
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("2600.00"), _instant(2026, 4, 10),
            )
            _projected(
                db, seed_user, seed_periods[7], "Car", "800.00",
                date(2026, 4, 20),
            )
            db.session.commit()

            opening = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            ).anchor_facts[0]
            first, last = opening.observed_on, date(2026, 4, 30)
            series = _series(seed_user, first - timedelta(days=1), last)
            facts = series.facts

            previous = facts[first - timedelta(days=1)].balance
            for offset in range((last - first).days + 1):
                day = first + timedelta(days=offset)
                fact = facts[day]
                assert fact.balance - previous == (
                    fact.recorded + fact.asserted + fact.planned
                ), f"unattributed movement on {day}"
                previous = fact.balance

    def test_a_still_PROJECTED_row_never_lands_on_a_PAST_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-G: a plan cannot have already happened.

        What lets a reader comparing PAST days against a bank ignore the
        planned tier entirely -- and the reason the books-vs-bank report bounds
        its span at the reader's NOW rather than trusting the statement's dates.
        """
        with app.app_context():
            _projected(
                db, seed_user, seed_periods[0], "Old bill", "125.00",
                date(2026, 1, 5),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            facts = balance_at.cash_daily_facts_series(
                seed_user["account"], ctx, date(2026, 1, 1), ctx.as_of,
            ).facts

            assert all(fact.planned == _ZERO for fact in facts.values())


    def test_a_SECOND_assertion_on_the_opening_day_is_still_a_true_up(
        self, app, seed_user, seed_periods, db,
    ):
        """Only the FIRST correction is the seed; a later one that day is not.

        ``corrections[1:]`` skips exactly one row, and this is the case
        that says whether "one" is the right number: two assertions sharing the
        opening day means the opening is in the seed and the second is an
        ordinary true-up on its own day.  Skipping by DAY instead of by
        position would swallow it, and the identity is what notices.
        """
        with app.app_context():
            # **The account is OPENED here rather than inherited** (plan step
            # X-f3c-2c).  This case reads a day-facts series for the OPENING's
            # own day, and the seeded account's origination assertion is dated
            # on the bootstrap day before the calendar -- a day no pay period
            # covers, so the series answers zeros for it and the case would
            # grade nothing.  Opening an account on the calendar's first day
            # puts the opening where a reader can ask about it, which is what
            # ``account_service.create_account`` does in production.
            account = _account_opened_on(db, seed_user, seed_periods[0].start_date)
            opening_day = seed_periods[0].start_date
            # **Recorded AFTER the origination, and stated rather than
            # implied.**  ``create_account`` stamps its assertion's
            # ``created_at`` from the clock -- the suite's frozen 2026-03-20 --
            # while ``observed_on`` is the day supplied above, so a second
            # assertion for that day whose instant is the day itself would sort
            # BEFORE the origination and this case would grade the pair the
            # wrong way round.  ``recorded_at`` is what separates the two
            # clocks, and a balance typed today for a day in January is an
            # ordinary back-dated assertion.
            append_balance_assertion(
                db.session, account,
                Decimal("1750.00"),
                _instant(opening_day.year, opening_day.month,
                         opening_day.day, 18),
                recorded_at=_instant(2026, 3, 20, 18),
            )
            db.session.commit()

            reread = cash_ledger.walk_cash_ledger(
                account.id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            second = assertion_corrections(reread)[1]
            facts = balance_at.cash_daily_facts_series(
                account,
                BalanceContext.build(seed_user["user"].id),
                opening_day - timedelta(days=1),
                opening_day,
            ).facts
            fact = facts[opening_day]

            assert second.observed_on == opening_day
            assert fact.asserted == second.delta
            assert fact.balance - facts[
                opening_day - timedelta(days=1)
            ].balance == fact.recorded + fact.asserted + fact.planned


class TestItCannotDisagreeWithTheBalanceSeriesBesideIt:
    """Two seam entries reporting one day's balance is the drift shape."""

    def test_both_entries_report_the_same_balance_on_every_day(
        self, app, seed_user, seed_periods, db,
    ):
        """``cash_daily_balance_series`` and this one are one running total.

        The codebase has measured what the alternative costs: before plan step
        X-c2b2 the cash scalar and the cash series were separate producers of
        one quantity and stood ``$15.96`` apart on the real Checking account.
        Both entries here sample ``(folded.seed, folded.steps)`` through the
        same :func:`sample_cumulative`, so this asserts a property rather than
        maintaining an agreement -- and it is the assertion that notices if one
        of them ever grows its own assembly.
        """
        with app.app_context():
            _settled(
                db, seed_user, seed_periods[6], "Rent", "500.00",
                date(2026, 4, 6),
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1300.00"), _instant(2026, 4, 8),
            )
            _projected(
                db, seed_user, seed_periods[7], "Car", "800.00",
                date(2026, 4, 20),
            )
            db.session.commit()

            first, last = date(2026, 4, 1), date(2026, 4, 30)
            ctx = BalanceContext.build(seed_user["user"].id)
            plain = balance_at.cash_daily_balance_series(
                seed_user["account"], ctx, first, last,
            )
            facts = balance_at.cash_daily_facts_series(
                seed_user["account"], ctx, first, last,
            ).facts

            assert list(plain) == list(facts)
            for day, balance in plain.items():
                assert facts[day].balance == balance


class TestTheDayTheAccountsBooksOpen:
    """The fact a reader needs to tell disagreement from "inside the opening".

    The series carries the governing opening's ``opened_on`` (plan step
    balance:X-f3c-2b-2c, ruling **R-BAL285**).  It carried the account's first
    CASH FACT until then (finding **BAL-616**), which is a different day
    wherever the books open before the first row or assertion, or an
    assertion is back-dated below them (finding **N-400**: under books opening
    Jan 1, one dated Dec 20 started the comparison on Dec 20).  Where the
    first assertion falls ON the opening day, as the origination assertion
    does in production, the two are one day, and the comparison's fault there
    was its ``>=``, which counted that day itself.  It mis-drew its boundary
    in all three.
    """

    def test_it_is_the_day_the_books_open_and_NOT_the_first_cash_fact(
        self, app, seed_user, seed_periods, db,
    ):
        """Books restated back past the first row: the two days part.

        The shape finding **BAL-616** turns on.  The account is opened on
        period 1's first day and its books are restated backward past a row
        settled in period 0 on 2026-01-05, so the books open on 2026-01-01 --
        the day before the earliest day a row could be dated, four days
        before that row -- and the row, not the opening, is the earliest cash
        fact.  The series must answer the OPENING's day, since every day after
        it is a day the books claim whether or not a row has been recorded on
        it yet.  Pinned to the date itself, not to the walk's own opening
        read a second time, which would grade one producer against itself.
        """
        with app.app_context():
            opened_on = seed_periods[1].start_date
            account = _account_opened_on(
                db, seed_user, opened_on, name="Early Records",
            )
            earlier = seed_periods[0].start_date + timedelta(days=3)
            # The books have to hold the row before it can be recorded
            # (ruling **R-HG**): an opening equity is the closing balance for
            # its own day, so a movement dated on or before it is refused
            # outright.  Backward-only, and it moves no figure here -- the
            # equity is carried forward unchanged.
            open_books_before_the_first_assertion(
                db.session, account, also_before=earlier,
            )
            _settled(
                db, seed_user, seed_periods[0], "Early", "20.00", earlier,
                account=account,
            )
            db.session.commit()

            governing = cash_ledger.walk_cash_ledger(
                account.id, get_baseline_scenario(seed_user["user"].id).id,
            ).opening
            series = balance_at.cash_daily_facts_series(
                account,
                BalanceContext.build(seed_user["user"].id),
                earlier,
                opened_on,
            )

            # The case is vacuous unless the opening really precedes the
            # first cash fact.  The helper opens the books the day before the
            # earliest day a row could be dated, which is before the row.
            assert earlier == date(2026, 1, 5)
            assert governing.opened_on < earlier
            assert series.opened_on == date(2026, 1, 1)

    def test_an_INVERTED_range_still_reports_the_day_the_books_open(
        self, app, seed_user, seed_periods, db,
    ):
        """The account HAS an opening whether or not a day was asked about.

        A range yielding no days is not a statement that the account has no
        books, and answering anything else there would let a caller conclude
        one.  The seeded account's books open 2024-01-04, the day before its
        origination assertion (``build_seed_user``, ``tests/_seed_owner_fixtures.py``).
        """
        with app.app_context():
            series = _series(seed_user, date(2026, 4, 10), date(2026, 4, 1))

            assert series.facts == {}
            assert series.opened_on == date(2024, 1, 4)
