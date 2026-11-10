"""Balance-at seam tests: records balance-at, and the cash anchor history.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import datetime
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.services import anchor_service, balance_at, cash_ledger
from app.services.balance_at._assertions import assertion_corrections
from tests.conftest import SEED_USER_BOOTSTRAP_START
from tests._test_helpers import (
    account_never_asserted,
    all_periods,
    append_balance_assertion,
    create_account_of_type,
    create_account_via_service,
    create_settled_cash_transaction,
    current_pay_period,
    last_covered_day,
    make_investment_account,
    settle_instant_on,
)

from ._helpers import _no_baseline, _make_hysa


class TestRecordsBalanceAt:
    """``records_balance_at`` answers a day WITHOUT its own assertion applied.

    Ruling **R-EU** (plan step X-f2-a).  The true-up form asks "what do my
    records produce for this day?", and the answer must not be the balance the
    user already declared for it -- an assertion RESETS the cash walk, so
    ``cash_balance_at`` answers with the declaration and any difference measured
    against it is zero by construction.

    **The reset case is the whole point, and the FIRST version of this step
    shipped without a single test on it** -- every case asked about a day
    carrying no assertion, which is the one axis where the two producers agree.
    """

    def test_a_day_with_no_assertion_is_the_plain_cash_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """With nothing to see past, it IS ``cash_balance_at``.

        The branch that must NOT diverge: inventing a second answer for the
        ordinary day would be the duplicate producer this seam exists to delete.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            ctx = balance_at.BalanceContext.build(user_id)
            periods = all_periods(user_id)
            day = last_covered_day(periods[1])

            assert balance_at.records_balance_at(account, ctx, day) == (
                balance_at.cash_balance_at(account, ctx, day)
            )

    def test_it_sees_past_the_assertion_dated_that_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """On a day the user has declared, it answers the RECORDS, not the declaration.

        The defect this function exists for, pinned with hand-computed figures:
        seed one settled $125.00 expense, note what the records produce, then
        declare a balance far away from it. ``cash_balance_at`` must move to the
        declaration and this must not.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            periods = all_periods(user_id)
            day = last_covered_day(periods[1])

            create_settled_cash_transaction(
                seed_user, periods[1], Decimal("125.00"),
                account=account, name="Recorded spend",
                settled_on=periods[1].start_date,
            )
            db.session.commit()

            before = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )

            anchor_service.apply_anchor_true_up(
                account=account, new_balance=before + Decimal("625.00"),
                observed_on=day,
            )
            db.session.commit()

            ctx = balance_at.BalanceContext.build(user_id)
            # The declaration governs the account's balance ...
            assert balance_at.cash_balance_at(account, ctx, day) == (
                before + Decimal("625.00")
            )
            # ... and the records are unmoved by it, which is the whole rule.
            assert balance_at.records_balance_at(account, ctx, day) == before

    def test_the_first_assertion_of_a_day_is_the_one_that_answers(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Three declarations in one day still report the RECORDS once.

        Production Checking did exactly this on 2026-04-15 -- three balances
        recorded, no transaction between them -- and it is the case where a
        "last assertion" reading would silently return the user's SECOND guess.
        Each successive declaration must leave this figure untouched.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            periods = all_periods(user_id)
            day = last_covered_day(periods[1])

            records = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )

            for balance in (Decimal("900.00"), Decimal("800.00"),
                            Decimal("700.00")):
                anchor_service.apply_anchor_true_up(
                    account=account, new_balance=balance, observed_on=day,
                )
                db.session.commit()
                assert balance_at.records_balance_at(
                    account, balance_at.BalanceContext.build(user_id), day,
                ) == records

            # Non-vacuity: the declarations really did land and really did move
            # the account's balance, so the constant above is a property of this
            # function rather than of an unchanged database.
            assert balance_at.cash_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            ) == Decimal("700.00")
            assert records != Decimal("700.00")

    def test_a_modelled_account_is_out_of_scope(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An account carrying a modelled tier answers ``None``.

        A scope statement, not a failure: an HYSA accruing interest or a
        brokerage compounding is not being reconciled against a bank statement,
        and its recorded cash is a fraction of what its screens show -- so a
        difference drawn against it would caption a model-vs-market gap as
        untracked spend (finding **N-213**).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            ctx = balance_at.BalanceContext.build(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            assert balance_at.records_balance_at(
                inv, ctx, last_covered_day(periods[-1]),
            ) is None
            # Non-vacuity: the plain account beside it DOES answer, so this is a
            # property of the KIND rather than of the fixture.
            assert balance_at.records_balance_at(
                seed_user["account"], ctx, last_covered_day(periods[-1]),
            ) is not None

    def test_it_refuses_an_instant_where_a_civil_day_is_meant(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A ``datetime`` raises rather than silently valuing some other moment.

        Asserted here because this entry is reached from a route that parses a
        date box -- the one place a ``datetime`` could arrive from a caller that
        "helpfully" widened a type.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            ctx = balance_at.BalanceContext.build(user_id)
            day = last_covered_day(current_pay_period(user_id))

            with pytest.raises(TypeError):
                balance_at.records_balance_at(
                    seed_user["account"], ctx,
                    datetime(day.year, day.month, day.day),
                )


class TestCashAnchorHistory:
    """``cash_anchor_history`` is the account's assertion LOG (ruling R-EV).

    Plan step **X-f2-b**.  Every column is a field the cash walk already
    publishes, so what these tests pin is not arithmetic the walk owns but the
    three rules this entry adds on top of it: the ORDER the card reads in,
    WHERE the ledger / difference pair is withheld, and that the withholding
    flag is read from the account's KIND rather than inferred from the rows.

    The seeded Checking opens at ``$1,000.00``, re-homed by
    ``seed_periods_today`` onto period 0's start day, so every figure below is
    that opening plus what these tests put after it.
    """

    def test_rows_are_newest_first_and_each_carries_the_walk_s_figures(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two declarations read back newest-first with hand-computed cells.

        The figures are computed here rather than read back from a producer.
        The account opens at $1,000.00; one settled $125.00 expense lands in
        period 1; then $500.00 is declared for period 1's last day and $400.00
        for period 2's.  So the $500.00 row's ledger is ``1000.00 - 125.00 =
        875.00`` and its difference ``500.00 - 875.00 = -375.00``; the $400.00
        row sees the $500.00 reset and nothing since, so its ledger is
        ``500.00`` and its difference ``-100.00``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            periods = all_periods(user_id)

            create_settled_cash_transaction(
                seed_user, periods[1], Decimal("125.00"),
                account=account, name="Recorded spend",
                settled_on=periods[1].start_date,
            )
            db.session.commit()

            for balance, day in (
                (Decimal("500.00"), last_covered_day(periods[1])),
                (Decimal("400.00"), last_covered_day(periods[2])),
            ):
                anchor_service.apply_anchor_true_up(
                    account=account, new_balance=balance, observed_on=day,
                )
                db.session.commit()

            history = balance_at.cash_anchor_history(
                account, balance_at.BalanceContext.build(user_id),
            )

            assert history.reconcilable is True
            assert [row.observed_on for row in history.rows] == [
                last_covered_day(periods[2]),
                last_covered_day(periods[1]),
                # The ORIGINATION assertion, dated on the seeded owner's
                # bootstrap day (plan step X-f3c-2c).  ``seed_periods_today``
                # drops that pay PERIOD and used to re-home the assertion with
                # it; the table is append-only, so the row stays where
                # ``account_service.create_account`` wrote it and the card
                # shows the account's real first declaration.
                SEED_USER_BOOTSTRAP_START,
            ]
            assert [row.recorded for row in history.rows] == [
                Decimal("400.00"), Decimal("500.00"), Decimal("1000.00"),
            ]
            # The OPENING row carries its pair like every other row since plan
            # step X-f3c-2a (it read ``None`` before, because the fold had
            # swallowed the opening's whole delta into its seed).  The account
            # was created asserting $1,000.00, so its books opened at
            # $1,000.00 and the declaration corrects nothing.
            assert [row.ledger for row in history.rows] == [
                Decimal("500.00"), Decimal("875.00"), Decimal("1000.00"),
            ]
            assert [row.correction for row in history.rows] == [
                Decimal("-100.00"), Decimal("-375.00"), Decimal("0.00"),
            ]
            # **The opening is a RECORD of its own, not a flag on a row**
            # (plan step X-f3c-2b).  ``CashAnchorRow.is_opening`` is gone: it
            # badged whichever assertion fell on the books' own day, which
            # ruling R-HG makes a day no assertion of the records' era can
            # share on an account whose books were moved back.  The account
            # here was created asserting $1,000.00, so its books opened the
            # same day at the same figure and the badge would still have
            # landed -- which is why the flag's absence is asserted on the
            # ROWS and its replacement on the record beside them.
            assert not hasattr(history.rows[0], "is_opening")
            assert history.opening.equity == Decimal("1000.00")
            assert history.opening.declared is True

    def test_the_opening_is_a_RECORD_and_the_assertions_publish_the_pair(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The books are their own record; the ASSERTIONS carry the pair.

        **Two changes, one after the other, and the name of this case follows
        the second.**  The opening's ``balance_before`` used to be the rows
        dated before the account's first assertion replayed from a ZERO seed --
        on production Checking ``$2,057.42`` against a ``$2,746.58`` opening --
        so the ``$689.16`` between them was the account's opening EQUITY, not a
        correction, and publishing it would have told the owner "your records
        were off by $689.16 the day this account opened", which is false.  Plan
        step X-f3c-2a made opening equity a stored
        ``budget.account_openings`` fact and removed that reason.

        Ruling **R-HG** (plan step X-f3c-2b) then separated the two records
        outright, and this case was called
        ``test_the_opening_row_PUBLISHES_its_pair_like_every_other_row`` until
        it did.  An opening is no longer a row of the assertion series at all:
        it is :class:`CashOpeningRow`, and its ledger / correction cells are
        empty BY CONSTRUCTION, because nothing precedes an opening for a
        correction to be measured against.  What this case grades now is the
        pair either side of that split -- the opening's own day, equity and
        provenance, and the TRUE-UP publishing a non-zero pair.

        **The refusal is asserted rather than the state seeded**, because the
        state this case used to build is the one R-HG makes unstorable: a
        settled row dated before the books.  Hand-computed on what replaces it:
        the books open at ``$2,746.58`` (what ``create_account`` recorded), a
        ``+$300.00`` income lands after them, and the true-up declares
        ``$2,746.58`` again -- giving ``ledger = 2746.58 + 300.00 = $3,046.58``
        and a correction of ``2746.58 - 3046.58 = -$300.00``.

        That ``-$300.00`` is the pre-opening double count plan step X-f3c-2b
        closes -- and the card naming it is the point, where the old behaviour
        rendered ``--`` and said nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            # Built through the PRIMITIVE, so the books stay exactly where
            # ``create_account`` put them (plan step X-f3c-2b): this case
            # asserts on ``history.opening.opened_on``, and the shared factory
            # would open the books earlier -- which is right for a fixture that
            # records movements and wrong for one whose subject is the day.
            later = create_account_via_service(
                seed_user, db.session, "Checking", "Statement Checking",
                anchor_balance=Decimal("2746.58"),
                observed_on=periods[2].start_date,
            )
            # **The fixture this test was built on is now UNSTORABLE** (plan
            # step X-f3c-2b, ruling R-HG).  It recorded $300.00 of income
            # BEFORE the books opened so the opening's pair would be non-zero
            # and the publication visibly meant something.  That state is the
            # double count of finding N-378, so the refusal is asserted here
            # instead -- and the row's pair is then graded on a record the
            # books can hold.
            with pytest.raises(ValidationError):
                create_settled_cash_transaction(
                    seed_user, periods[0], Decimal("300.00"),
                    account=later, is_income=True, name="Pre-opening income",
                    settled_on=periods[0].start_date,
                )
            db.session.rollback()

            later = create_account_via_service(
                seed_user, db.session, "Checking", "Statement Checking",
                anchor_balance=Decimal("2746.58"),
                observed_on=periods[2].start_date,
            )
            create_settled_cash_transaction(
                seed_user, periods[3], Decimal("300.00"),
                account=later, is_income=True, name="Income after the books",
                settled_on=periods[3].start_date,
            )
            anchor_service.apply_anchor_true_up(
                account=later, new_balance=Decimal("2746.58"),
                observed_on=last_covered_day(periods[3]),
            )
            db.session.commit()

            ctx = balance_at.BalanceContext.build(user_id)
            history = balance_at.cash_anchor_history(later, ctx)

            # The OPENING is its own record and its cells are empty by
            # CONSTRUCTION: nothing precedes an opening for a correction to be
            # measured against.
            assert history.opening.opened_on == periods[2].start_date
            assert history.opening.equity == Decimal("2746.58")
            assert history.opening.declared is True

            # The TRUE-UP publishes the pair, and it is non-zero because a
            # real record sits between the books and it -- $2,746.58 opened,
            # +$300.00 recorded, $2,746.58 declared again, so the declaration
            # takes $300.00 back out.
            trued_up = history.rows[0]
            assert trued_up.observed_on == last_covered_day(periods[3])
            assert trued_up.recorded == Decimal("2746.58")
            assert trued_up.ledger == Decimal("3046.58")
            assert trued_up.correction == Decimal("-300.00")
            # Non-vacuity: the row's two cells ARE the walk's own figures,
            # read back rather than recomputed here.
            walk = cash_ledger.walk_cash_ledger(later.id, ctx.scenario_id)
            booked = assertion_corrections(walk)[-1]
            assert booked.balance_before == Decimal("3046.58")
            assert booked.delta == Decimal("-300.00")
            assert walk.opening.opening_equity == Decimal("2746.58")

    def test_a_modelled_account_withholds_the_pair_on_every_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An HYSA logs what was declared and claims no reconciliation.

        The walk sees recorded CASH only, so an accruing account's difference
        would name interest as untracked spend -- measured on production, the
        HYSA's one row reads ``$4,863.56`` against a ``$5,363.56`` balance, and
        the Money Market's two true-ups are ``$15.01`` / ``$15.24``, which is
        interest.  The rows still exist, because "what did I declare, and when"
        is a fact for every kind and is the retrieval path finding **N-205** is
        about.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            anchor_service.apply_anchor_true_up(
                account=hysa, new_balance=Decimal("5100.00"),
                observed_on=last_covered_day(periods[2]),
            )
            db.session.commit()

            history = balance_at.cash_anchor_history(
                hysa, balance_at.BalanceContext.build(user_id),
            )

            assert history.reconcilable is False
            assert [row.recorded for row in history.rows] == [
                Decimal("5100.00"), Decimal("5000.00"),
            ]
            # EVERY row, and the opening record beside them: the true-up
            # above is a non-opening row and its pair is withheld too.
            assert history.opening.equity == Decimal("5000.00")
            assert all(row.ledger is None for row in history.rows)
            assert all(row.correction is None for row in history.rows)
            # Non-vacuity: the PLAIN account beside it does publish a pair for
            # its own non-opening row, so the withholding above is a property
            # of the KIND rather than of the fixture.
            anchor_service.apply_anchor_true_up(
                account=seed_user["account"], new_balance=Decimal("900.00"),
                observed_on=last_covered_day(periods[2]),
            )
            db.session.commit()
            plain = balance_at.cash_anchor_history(
                seed_user["account"], balance_at.BalanceContext.build(user_id),
            )
            assert plain.reconcilable is True
            assert plain.rows[0].correction == Decimal("-100.00")

    def test_reconcilable_reads_the_kind_not_the_rows(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A PLAIN account with ONE assertion still reconciles.

        The trap this test exists for: an inferred definition of
        ``reconcilable`` read off the ROWS is wrong exactly here.  A freshly
        created account's one row corrects ``$0.00``, because its books opened
        at the balance its owner typed -- so ``any(row.correction)`` is falsey
        and would hide the reconciliation columns on the healthiest account
        there is.

        *The trap was sharper still until plan step X-f3c-2a: that single row
        was the OPENING, whose pair was withheld outright, so even
        ``any(row.correction is not None)`` -- the careful spelling -- failed
        here.  The suppression is gone and the trap survives it, which is why
        the flag is read from
        :func:`~app.services.account_projection.classify_account` rather than
        inferred at all.*
        """
        with app.app_context():
            user_id = seed_user["user"].id
            fresh = create_account_of_type(
                seed_user, db.session, "Checking", "Second Checking",
                anchor_balance=Decimal("250.00"),
            )
            db.session.commit()

            history = balance_at.cash_anchor_history(
                fresh, balance_at.BalanceContext.build(user_id),
            )

            # One assertion, and the opening record beside it -- the account
            # was created asserting $250.00, so its books opened there.
            assert len(history.rows) == 1
            assert history.opening.equity == Decimal("250.00")
            # A freshly created account's books open at what its owner typed,
            # so its one row corrects $0.00 -- NOT ``None``.  That is what makes
            # the trap in the docstring above live: ``any(row.correction)`` is
            # falsey here, and would hide the columns on the healthiest account
            # there is.
            assert history.rows[0].correction == Decimal("0.00")
            assert history.reconcilable is True

    def test_a_back_dated_row_carries_the_day_it_was_entered(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``recorded_on`` separates when a balance was TRUE from when it was typed.

        Sorted by ``observed_on`` a back-dated row sits at its past day's
        position -- 40 rows down on the real Checking account -- so without
        this field the card cannot show that the write the user just made
        landed, which is the whole of finding **N-205**.  An ordinary same-day
        true-up must leave the two EQUAL, or the caption would fire on every
        row.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            periods = all_periods(user_id)
            back_day = last_covered_day(periods[1])
            typed_day = periods[3].start_date

            append_balance_assertion(
                db.session, account, Decimal("640.00"),
                settle_instant_on(back_day),
                recorded_at=settle_instant_on(typed_day),
            )
            append_balance_assertion(
                db.session, account, Decimal("710.00"),
                settle_instant_on(last_covered_day(periods[2])),
            )
            db.session.commit()

            history = balance_at.cash_anchor_history(
                account, balance_at.BalanceContext.build(user_id),
            )
            by_day = {row.observed_on: row for row in history.rows}

            assert by_day[back_day].recorded_on == typed_day
            assert by_day[back_day].recorded_on != back_day
            same_day = by_day[last_covered_day(periods[2])]
            assert same_day.recorded_on == same_day.observed_on

    def test_three_declarations_on_one_day_all_appear_and_chain(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A day is not a key, and the card must show every row it carries.

        Production Checking recorded THREE balances on 2026-04-15 with no
        transaction between them ($1,172.44 / $1,133.47 / $1,087.61), and each
        row's ledger is the row before it -- so a card keyed on the day, or one
        that de-duplicated, would drop two real corrections.  Reproduced with
        the same shape: against the $1,000.00 opening and no settled row, the
        first declaration's ledger is $1,000.00 and each later one's is its
        predecessor's declared balance.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            day = last_covered_day(all_periods(user_id)[1])

            for balance in (Decimal("1172.44"), Decimal("1133.47"),
                            Decimal("1087.61")):
                anchor_service.apply_anchor_true_up(
                    account=account, new_balance=balance, observed_on=day,
                )
                db.session.commit()

            history = balance_at.cash_anchor_history(
                account, balance_at.BalanceContext.build(user_id),
            )
            same_day = [row for row in history.rows if row.observed_on == day]

            assert [row.recorded for row in same_day] == [
                Decimal("1087.61"), Decimal("1133.47"), Decimal("1172.44"),
            ]
            # Newest first, so each row's ledger is the NEXT row's recorded
            # balance: a later assertion on one day carries the earlier one's
            # reset inside it.
            assert [row.ledger for row in same_day] == [
                Decimal("1133.47"), Decimal("1172.44"), Decimal("1000.00"),
            ]
            assert [row.correction for row in same_day] == [
                Decimal("-45.86"), Decimal("-38.97"), Decimal("172.44"),
            ]

    def test_an_account_with_no_assertion_logs_nothing_rather_than_raising(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An empty log is answered, not raised.

        Production-unreachable (``account_service.create_account`` and
        migration ``cfb15e782f86`` guarantee an opening row), and asserted
        anyway because the alternative -- a card that 500s on a state the
        database can hold -- is the failure mode this arc keeps closing on the
        read side.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            # **Built rather than emptied** (plan step X-f3c-2c): an assertion
            # is append-only at the database tier, so an account that has
            # asserted nothing is one the assertion factory never touched.
            account = account_never_asserted(
                seed_user, db.session, name="Silent",
                opening_equity=Decimal("0.00"),
            )
            db.session.commit()

            history = balance_at.cash_anchor_history(
                account, balance_at.BalanceContext.build(user_id),
            )

            assert history.rows == []
            assert history.reconcilable is True

    def test_it_refuses_a_context_with_no_scenario(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No baseline is a loud refusal, like every other seam entry."""
        with app.app_context():
            with pytest.raises(ValueError):
                balance_at.cash_anchor_history(
                    seed_user["account"], _no_baseline(seed_user["user"].id),
                )
