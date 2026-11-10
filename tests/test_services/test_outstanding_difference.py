"""What an account's own BOOKS cannot explain -- plan step balance:X-f3c-3.

``balance_at.cash_outstanding_difference``: the owner's latest declared
balance, less what the account's books produce for that same day (its stored
opening equity plus every posting dated on or before it).

**The load-bearing case is** ``TestItIsOneFigureAndNotThePerAssertionPlug``,
and it is ruling **R-FN**'s own argument made a test.  Each true-up's
correction is DEFINED as whatever forces the ledger to the balance just
declared, so consecutive corrections telescope: a producer that answered with
the LAST correction, or with the largest, would be reporting the difference
between two of the owner's successive guesses.  That class was measured on
production Checking 2026-04-15, where three balances recorded in one day with
no transaction between them read ``-$45.86`` against the previous entry and
``-$92.29`` against the records.  The case below builds ``+$1,000.00`` and
``-$1,000.00`` corrections whose net is ``$0.00`` and asserts BOTH -- so the
one-figure derivation is graded against the per-assertion one rather than
merely described as different from it.

**The second control is** ``TestTheBooksSideIsNotTheShippingFOLD``.  An
assertion RESETS the cash walk, so ``cash_balance_at`` on a day that carries
one answers with that assertion and a difference measured against it is zero by
construction.  The books side must therefore be the POST-CUTOVER function --
plan step X-f3c-5's ``opening equity + SUM(postings <= T)`` -- and that test
puts the two side by side on one day where they differ by ``$150.00``.

**Measured against a producer that shares no code with this one.**  On a clone
of the dev database migrated to head, 2026-09-01, the developer's Checking
account answers ``$2,370.02`` -- ``$2,501.31`` asserted for 2026-08-18 against
``$131.29`` of books -- and the persisted double-entry ledger, written by
``account_posting_service``, independently carries ``$2,370.02`` of
``account_trueup`` postings on that account's linked ledger account against
``-$2,370.02`` on ``Checking -- Opening`` equity.  That equity leg is finding
**N-171**.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import StatementBalanceEvidenceEnum
from app.exceptions import ForeignAccountError
from app.models.account import Account
from app.services import (
    account_service,
    balance_at,
    bank_agreement,
    outstanding_difference as service,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at._cash_fold import assembled_fold
from tests._test_helpers import (
    account_never_asserted,
    append_balance_assertion,
    create_account_of_type,
    create_settled_cash_transaction,
)
from tests.test_services.test_cash_fold import _instant
from tests.test_services.test_statement_import.test_anchor import _seed_import

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN

#: The seeded owner's Checking books open the day BEFORE its origination
#: assertion (``tests/conftest.py``: ``open_books_before_the_first_assertion``),
#: and that assertion is dated :data:`~tests.conftest.SEED_USER_BOOTSTRAP_START`
#: at ``$1,000.00``.  Named here because every figure below is that opening plus
#: what the test itself records.
_SEED_OPENING = Decimal("1000.00")
_SEED_ASSERTED_ON = date(2024, 1, 5)
_SEED_BOOKS_OPEN_ON = date(2024, 1, 4)

_ZERO = Decimal("0.00")


def _difference(seed_user, account=None):
    """Return the outstanding difference for an account of the seeded owner."""
    return balance_at.cash_outstanding_difference(
        account or seed_user["account"],
        BalanceContext.build(seed_user["user"].id),
    )


def _settle(
    seed_user, period, amount, day, *, is_income=False, account=None,
):
    """Settle one ordinary cash row on *day* through the production door."""
    return create_settled_cash_transaction(
        seed_user, period, Decimal(str(amount)),
        is_income=is_income, settled_on=day, name=f"row {day}",
        account=account,
    )


def _resolved(seed_user, account=None):
    """Return the whole instrument -- the figure AND the span verdict."""
    return service.outstanding_difference(
        account or seed_user["account"],
        BalanceContext.build(seed_user["user"].id),
    )


def _opened_on(seed_user, day, balance="500.00", name="Fresh"):
    """Create an account whose books open ON *day*, the production shape.

    ``create_account`` writes the opening record and the origination assertion
    for one day from one typed figure, so the returned account's books open on
    *day* and its span therefore starts the day after.  Deliberately NOT
    ``create_account_of_type``, which then restates the books BACKWARD to
    before every day a fixture could date -- useful elsewhere and the opposite
    of what a case controlling the span's first day needs.
    """
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name=name,
            anchor_balance=Decimal(balance),
            observed_on=day,
        ),
    )
    return account


def _assert_balance(seed_user, db, balance, day, *, account=None):
    """Append one balance assertion on *day*, defaulting to the seeded account."""
    append_balance_assertion(
        db.session, account or seed_user["account"],
        Decimal(str(balance)), _instant(day.year, day.month, day.day),
    )


class TestAnAccountWhoseDeclarationMatchesItsBooks:
    """The healthy steady state, and the one every account is created in."""

    def test_the_seeded_account_starts_at_ZERO(
        self, app, seed_user, seed_periods, db,
    ):  # pylint: disable=unused-argument
        """Opening $1,000.00, one assertion of $1,000.00, no postings between.

        The books produce ``1000.00 + 0.00 = 1000.00`` for 2024-01-05 and the
        owner declared ``1000.00`` for that day, so there is nothing the books
        cannot explain.
        """
        with app.app_context():
            difference = _difference(seed_user)

            assert difference is not None
            assert difference.opened_on == _SEED_BOOKS_OPEN_ON
            assert difference.opening_equity == _SEED_OPENING
            assert difference.asserted_on == _SEED_ASSERTED_ON
            assert difference.asserted == _SEED_OPENING
            assert difference.books == _SEED_OPENING
            assert difference.amount == _ZERO

    def test_a_BRAND_NEW_account_has_an_EMPTY_span(
        self, app, seed_user, seed_periods, db,
    ):  # pylint: disable=unused-argument
        """``create_account`` opens the books and asserts on ONE day.

        The owner types one balance and it is both the opening equity and the
        origination assertion, so the books open ON the day the declaration is
        about and no day lies between them.  That is an ordinary state rather
        than a defect -- and it is the state finding **N-400** describes one
        step further, where a back-dated assertion lands strictly BELOW the
        books.
        """
        with app.app_context():
            checking_type = seed_user["account"].account_type_id
            fresh = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type,
                    name="Fresh Checking",
                    anchor_balance=Decimal("250.00"),
                    observed_on=seed_periods[4].start_date,
                ),
            )
            db.session.commit()

            difference = _difference(seed_user, fresh)

            assert difference.opened_on == seed_periods[4].start_date
            assert difference.asserted_on == seed_periods[4].start_date
            assert difference.span.is_empty is True
            # One typed figure became both sides, so they agree by
            # construction.
            assert difference.asserted == Decimal("250.00")
            assert difference.books == Decimal("250.00")
            assert difference.amount == _ZERO


class TestTheFigureIsTheDeclarationLessTheBooks:
    """The subtraction itself, on money the books do not hold."""

    def test_a_settled_row_the_declaration_does_not_know_about(
        self, app, seed_user, seed_periods, db,
    ):
        """$150.00 spent, and the owner still declares $1,000.00.

        Books at 2026-03-03: ``1000.00 (opening) - 150.00 (the expense)
        = 850.00``.  Declared: ``1000.00``.  Outstanding difference:
        ``1000.00 - 850.00 = 150.00`` -- money the account holds that nothing
        recorded put there.
        """
        with app.app_context():
            _settle(seed_user, seed_periods[4], "150.00", date(2026, 3, 3))
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            db.session.commit()

            difference = _difference(seed_user)

            assert difference.asserted_on == date(2026, 3, 3)
            assert difference.asserted == Decimal("1000.00")
            assert difference.books == Decimal("850.00")
            assert difference.amount == Decimal("150.00")

    def test_the_span_runs_from_the_day_after_the_books_open(
        self, app, seed_user, seed_periods, db,
    ):
        """The days the difference accumulated over, and only those.

        An opening equity is the CLOSING balance for ``opened_on`` (ruling
        **R-HG**), so the first day the books can hold a movement is the one
        after it -- and the last is the day of the declaration the difference
        is measured against.
        """
        with app.app_context():
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            db.session.commit()

            span = _difference(seed_user).span

            assert span.first_day == date(2024, 1, 5)
            assert span.last_day == date(2026, 3, 3)
            assert span.is_empty is False

    def test_a_posting_AFTER_the_declaration_does_not_move_it(
        self, app, seed_user, seed_periods, db,
    ):
        """``SUM(postings <= that day)``, and the bound is load-bearing.

        The same $150.00 expense and the same $1,000.00 declaration for
        2026-03-03, plus $300.00 of income settled a week LATER.  That income
        is real and the account's balance today carries it -- but it is not
        part of what the 2026-03-03 declaration was checked against, so the
        difference stays ``$150.00`` rather than becoming ``-$150.00``.
        """
        with app.app_context():
            _settle(seed_user, seed_periods[4], "150.00", date(2026, 3, 3))
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            _settle(
                seed_user, seed_periods[5], "300.00", date(2026, 3, 16),
                is_income=True,
            )
            db.session.commit()

            difference = _difference(seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)

            assert difference.books == Decimal("850.00")
            assert difference.amount == Decimal("150.00")
            # The account really does hold the later money: 1000.00 asserted,
            # then +300.00 on top of it.
            assert balance_at.cash_balance_at(
                seed_user["account"], ctx, date(2026, 3, 16),
            ) == Decimal("1300.00")


class TestItIsOneFigureAndNotThePerAssertionPlug:
    """Ruling R-FN's telescoping argument, graded rather than described."""

    def test_two_corrections_that_CANCEL_leave_nothing_outstanding(
        self, app, seed_user, seed_periods, db,
    ):
        """+$1,000.00 then -$1,000.00, and the books explain everything.

        2026-03-02: $500.00 spent, and the owner declares $1,500.00.  The
        records held ``1000.00 - 500.00 = 500.00`` just before, so that
        assertion's own correction is ``+1000.00``.

        2026-03-04: $200.00 received, and the owner declares $700.00.  The
        running balance held ``1500.00 + 200.00 = 1700.00`` just before, so
        that assertion's correction is ``-1000.00``.

        The books for 2026-03-04 are ``1000.00 - 500.00 + 200.00 = 700.00``,
        which is exactly what the owner declared -- so the OUTSTANDING
        difference is ``$0.00`` while neither per-assertion correction is
        anywhere near it.  A producer answering with the last correction would
        say ``-$1,000.00``; one answering with the largest would say
        ``+$1,000.00``.
        """
        with app.app_context():
            _settle(seed_user, seed_periods[4], "500.00", date(2026, 3, 2))
            _assert_balance(
                seed_user, db, "1500.00", date(2026, 3, 2),
            )
            _settle(
                seed_user, seed_periods[4], "200.00", date(2026, 3, 4),
                is_income=True,
            )
            _assert_balance(
                seed_user, db, "700.00", date(2026, 3, 4),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            corrections = assembled_fold(seed_user["account"], ctx).corrections
            booked = {row.observed_on: row.delta for row in corrections}

            # The per-assertion plugs are real and they are LARGE...
            assert booked[date(2026, 3, 2)] == Decimal("1000.00")
            assert booked[date(2026, 3, 4)] == Decimal("-1000.00")
            # ...and what the books cannot explain is none of them.
            difference = _difference(seed_user)
            assert difference.books == Decimal("700.00")
            assert difference.amount == _ZERO


class TestTheBooksSideIsNotTheShippingFOLD:
    """The reset is what the books side must NOT apply."""

    def test_the_folded_balance_and_the_books_differ_on_an_asserted_day(
        self, app, seed_user, seed_periods, db,
    ):
        """A FIRING control over the producer this figure must not be read off.

        On a day carrying an assertion the shipping cash fold answers with that
        assertion, because the replay RESETS the running total to it
        (``balance_at._assertions``).  Reading the difference off that fold
        would make it zero by construction; the books side is
        ``opening + SUM(postings)``, which on this day is ``$150.00`` lower.
        """
        with app.app_context():
            _settle(seed_user, seed_periods[4], "150.00", date(2026, 3, 3))
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            folded = balance_at.cash_balance_at(
                seed_user["account"], ctx, date(2026, 3, 3),
            )
            difference = _difference(seed_user)

            assert folded == Decimal("1000.00")
            assert difference.books == Decimal("850.00")
            assert folded - difference.books == difference.amount


class TestTheQuestionDoesNotApplyToEveryAccount:
    """Two ``None`` answers, each a statement rather than a gap."""

    def test_a_MODELLED_account_answers_NOTHING(
        self, app, seed_user, seed_periods, db,
    ):  # pylint: disable=unused-argument
        """An HYSA's assertion is mark-to-market, not a check against cash.

        Ruling **R-FO**: an account whose balance carries a modelled tier has
        no record of a price movement to discard, so the same subtraction there
        is its RETURN.  Captioning that as something the books cannot explain
        would name a model-vs-market gap as untracked spend -- on the
        developer's own Roth IRA it would read ``$4,523.33``.  Widening the
        question is finding **N-213**.
        """
        with app.app_context():
            hysa = create_account_of_type(
                seed_user, db.session, "HYSA", "Savings",
                anchor_balance=Decimal("5000.00"),
            )
            db.session.commit()

            assert _difference(seed_user, hysa) is None

    def test_an_account_that_has_ASSERTED_NOTHING_answers_NOTHING(
        self, app, seed_user, seed_periods, db,
    ):  # pylint: disable=unused-argument
        """No declaration exists for the books to disagree with.

        Production-unreachable -- ``create_account`` writes an origination
        assertion and CHECKS that it landed -- and answered rather than raised,
        exactly as the walk answers it
        (:class:`~app.services.cash_ledger.CashLedgerWalk` documents both lists
        empty for this account).

        **The books are given and only the ASSERTION withheld**, which is what
        makes this case about the branch it names: an account with no OPENING
        record raises out of ``cash_ledger.account_opening_fact`` before the
        assertion question is ever reached, so withholding both would grade the
        wrong refusal.
        """
        with app.app_context():
            orphan = account_never_asserted(
                seed_user, db.session, name="Never asserted",
                opening_equity=Decimal("75.00"),
            )
            db.session.commit()

            assert _difference(seed_user, orphan) is None


class TestTheFigureIsScopedToItsOwnAccount:
    """One account's declaration is never measured against another's books."""

    def test_a_SECOND_account_answers_for_itself(
        self, app, seed_user, seed_periods, db,
    ):
        """Two Checking accounts, two different differences.

        The seeded account carries a ``$150.00`` difference; a second one built
        beside it carries none.  The producer takes the fold the read pass
        assembled for the account it was asked about, and that pass refuses a
        foreign account where it memoizes.
        """
        with app.app_context():
            _settle(seed_user, seed_periods[4], "150.00", date(2026, 3, 3))
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            other = create_account_of_type(
                seed_user, db.session, "Checking", "Second Checking",
                anchor_balance=Decimal("42.00"),
            )
            db.session.commit()

            seeded = _difference(seed_user)
            second = _difference(seed_user, other)

            assert seeded.asserted_on == date(2026, 3, 3)
            assert seeded.amount == Decimal("150.00")
            # The second account was created today with one typed figure, so
            # its own books explain its own declaration exactly.
            assert second.asserted_on != seeded.asserted_on
            assert second.amount == _ZERO

    def test_it_refuses_an_account_of_ANOTHER_owner(
        self, app, seed_user, seed_second_user, seed_periods, db,
    ):  # pylint: disable=unused-argument
        """The read pass owns the ownership refusal, and it fires here too."""
        with app.app_context():
            intruder = BalanceContext.build(seed_second_user["user"].id)
            foreign = db.session.get(Account, seed_user["account"].id)

            with pytest.raises(ForeignAccountError):
                balance_at.cash_outstanding_difference(foreign, intruder)


class TestWhetherAStatementReconcilesTheSPAN:
    """The instrument's second half -- plan step X-f3c-3, ruling R-GY.

    A difference the books cannot explain means one thing over a span the bank
    has confirmed line by line and quite another over a span nobody has read,
    so the figure never travels without this beside it.

    **Every case here goes through the PUBLIC door**
    (``outstanding_difference.outstanding_difference``) rather than handing the
    fold a span of its own, which is what makes them grade the composition too:
    the span the verdict is about has to be the DIFFERENCE's own, and a test
    that supplied one could not tell a correct wiring from a wrong one.

    **Two are FIRING CONTROLS over the two zero-counts**, and each exists
    because the other cannot see its case.
    ``test_a_GAP_between_two_imports_is_not_reconciliation`` builds a span
    compared in full, agreeing on every day, whose middle two days no import
    covers.  ``test_imported_days_past_the_TWO_YEAR_drawing_bound_are_not_reconciliation``
    builds the mirror -- every day imported and every drawn day agreeing,
    while the comparison draws only the last two years.  *The mirror was built
    on days before the account's first cash fact until plan step
    balance:X-f3c-2b-2c (finding **BAL-616**, rulings **R-BAL285** and
    **R-BAL291**), which made every drawn day after the books open that an
    import covers a compared one: that fixture's days are all imported and
    drawn, so they are compared now, and its own case below says what it reads
    instead.*
    """

    def test_every_day_imported_compared_and_agreeing_RECONCILES(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-03-01; two lines, two rows that match them.

        The span is 2026-03-02..2026-03-04.  The bank posts ``-50.00`` on the
        2nd and ``-25.00`` on the 4th; the app records ``50.00`` and ``25.00``
        of spend on those days; the 3rd is quiet on both sides.  Every day is
        inside the import's run, inside the drawn range and inside the app's
        records, and every residue is ``$0.00``.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 3, 1))
            _seed_import(
                db, account, stated="1000.00",
                effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
                period=(date(2026, 3, 2), date(2026, 3, 4)),
                lines=[(date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            _settle(
                seed_user, seed_periods[4], "50.00", date(2026, 3, 2),
                account=account,
            )
            _settle(
                seed_user, seed_periods[4], "25.00", date(2026, 3, 4),
                account=account,
            )
            _assert_balance(
                seed_user, db, "425.00", date(2026, 3, 4),
                account=account,
            )
            db.session.commit()

            over = _resolved(seed_user, account).reconciliation

            assert (over.first_day, over.last_day) == (
                date(2026, 3, 2), date(2026, 3, 4),
            )
            assert over.day_count == 3
            assert over.compared == 3
            assert over.unchecked == 0
            assert over.unimported == 0
            assert over.disagreeing == 0
            assert over.reconciles is True

    def test_one_disagreeing_day_is_not_reconciliation(
        self, app, seed_user, seed_periods, db,
    ):
        """The same span, plus ``$10.00`` the app records and the bank does not.

        2026-03-03 carries a row and no line, so its residue is ``-10.00`` --
        while every other count stays clean, so the refusal is attributable to
        the day it happened on.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 3, 1))
            _seed_import(
                db, account, stated="1000.00",
                effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
                period=(date(2026, 3, 2), date(2026, 3, 4)),
                lines=[(date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            _settle(
                seed_user, seed_periods[4], "50.00", date(2026, 3, 2),
                account=account,
            )
            _settle(
                seed_user, seed_periods[4], "10.00", date(2026, 3, 3),
                account=account,
            )
            _settle(
                seed_user, seed_periods[4], "25.00", date(2026, 3, 4),
                account=account,
            )
            _assert_balance(
                seed_user, db, "415.00", date(2026, 3, 4),
                account=account,
            )
            db.session.commit()

            over = _resolved(seed_user, account).reconciliation

            assert over.unchecked == 0
            assert over.unimported == 0
            assert over.disagreeing == 1
            assert over.reconciles is False

    def test_a_GAP_between_two_imports_is_not_reconciliation(
        self, app, seed_user, seed_periods, db,
    ):
        """A day between two imports is NOT COMPARED, and ``unimported`` says why.

        Books open 2026-02-28.  One import's lines run 2026-03-01..2026-03-02
        and the next's 2026-03-05..2026-03-06, so 2026-03-03 and 2026-03-04 sit
        inside neither run.  Every line has a matching row, and the books are
        quiet on those two days.  The bank may have posted lines nobody has
        imported in that gap, so its movement there is UNKNOWN, and a day only
        one record covers is never compared (finding **BAL-650**, ruling
        **R-BAL291**): 4 of the 6 days are compared and 2 are unchecked, and
        the 2 are exactly the ``unimported`` ones, which names the REASON.
        ``unchecked`` refuses the span too, so dropping either term leaves it
        unreconciled.  *Until plan step balance:X-f3c-2b-2c the gap days were
        compared against a bank movement of ``0.00`` nobody imported -- 6
        compared, 0 unchecked -- and the ``unimported`` term was the only thing
        between this span and "reconciled".*

        **Each import's stored period is its own LINE EXTREMES**, which is what
        ``statement_import._record`` writes; an adversarial review caught a
        first version seeding a period wider than its lines, a shape no
        production writer can produce, so the control was graded on a state
        that cannot occur.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 2, 28))
            _seed_import(
                db, account, stated="1000.00",
                effective_on=date(2026, 3, 2), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 1), "-50.00"),
                       (date(2026, 3, 2), "-20.00")],
                file_name="first.csv",
            )
            _seed_import(
                db, account,
                lines=[(date(2026, 3, 5), "-25.00"),
                       (date(2026, 3, 6), "-10.00")],
                file_name="second.csv",
            )
            for day, amount in (
                (date(2026, 3, 1), "50.00"), (date(2026, 3, 2), "20.00"),
                (date(2026, 3, 5), "25.00"), (date(2026, 3, 6), "10.00"),
            ):
                _settle(
                    seed_user, seed_periods[4], amount, day,
                    account=account,
                )
            _assert_balance(
                seed_user, db, "395.00", date(2026, 3, 6),
                account=account,
            )
            db.session.commit()

            over = _resolved(seed_user, account).reconciliation

            # Every day both records cover is clean...
            assert (over.first_day, over.last_day) == (
                date(2026, 3, 1), date(2026, 3, 6),
            )
            assert over.day_count == 6
            assert over.compared == 4
            assert over.disagreeing == 0
            # ...and the two days inside no statement's lines are the two
            # nothing compared: 6 - 4 = 2 unchecked, all of them unimported,
            # so none is an imported day left undrawn.
            assert over.imported == 4
            assert over.unimported == 2
            assert over.unchecked == 2
            assert over.imported_unchecked == 0
            assert over.reconciles is False

    def test_a_books_day_before_the_first_entry_is_COMPARED_not_unchecked(
        self, app, seed_user, seed_periods, db,
    ):
        """The fixture the mirror control used to stand on, and its answer now.

        ``create_account_of_type`` opens an account's books before every day a
        fixture could date -- 2026-01-01 here, the day before the owner's
        earliest pay period -- while its origination assertion sits on
        2026-03-19.  So the span runs 2026-01-02..2026-03-19 and an import
        covers every day of it.  Every one of the 77 is a day the books claim,
        so every one is compared (finding **BAL-616**, ruling **R-BAL285**):
        Jan 2's ``-40.00`` line has no row and DISAGREES, and the ``15.00``
        row matches Mar 19's line.  It still does not reconcile, now for the
        money the bank moved and the books never recorded.  *Before plan step
        balance:X-f3c-2b-2c it read 1 compared, 76 unchecked, 0 disagreeing:
        76 days fell before the first cash fact and were never compared.*
        """
        with app.app_context():
            late = create_account_of_type(
                seed_user, db.session, "Checking", "Late Checking",
                anchor_balance=Decimal("500.00"),
            )
            _seed_import(
                db, late, stated="500.00",
                effective_on=date(2026, 3, 19), evidence=_FILE_CHAIN,
                period=(date(2026, 1, 2), date(2026, 3, 19)),
                lines=[(date(2026, 1, 2), "-40.00"),
                       (date(2026, 3, 19), "-15.00")],
            )
            _settle(
                seed_user, seed_periods[5], "15.00", date(2026, 3, 19),
                account=late,
            )
            db.session.commit()

            resolved = _resolved(seed_user, late)
            over = resolved.reconciliation

            assert resolved.difference.opened_on == date(2026, 1, 1)
            assert resolved.difference.asserted_on == date(2026, 3, 19)
            # 30 days of January from the 2nd, 28 of February, 19 of March.
            assert over.day_count == 77
            assert over.unimported == 0
            assert over.compared == 77
            assert over.unchecked == 0
            assert over.disagreeing == 1
            assert over.reconciles is False

    def test_imported_days_past_the_TWO_YEAR_drawing_bound_are_not_reconciliation(
        self, app, seed_user, seed_periods, db,
    ):
        """A FIRING control over the ``unchecked`` term.

        The seeded account's books open 2024-01-04 and a balance is declared
        for 2026-03-03, so the span is 2024-01-05..2026-03-03, 789 days.  One
        statement's lines run 2024-01-05..2026-03-03, so EVERY day of the span
        is imported (``unimported`` is zero).  The comparison draws at most
        the last 731 days (``bank_agreement._MAX_COMPARED_DAYS``), from
        2024-03-03, so 2024-01-05..2024-03-02 -- 27 + 29 + 2 = 58 days -- are
        imported and never compared.  Every drawn day agrees: the two 2026
        lines have their rows and the rest are quiet on both sides, while the
        2024 line sits among the undrawn days.  So ``unchecked`` is the ONLY
        reason this span does not reconcile; drop the term and it reads as
        reconciled.

        **Built on the drawing bound, not on a window wider than its lines.**
        Since plan step balance:X-f3c-2b-2c every drawn day after the books
        open that an import covers is compared (rulings **R-BAL285**,
        **R-BAL291**), so a books day goes imported and uncompared only where
        the comparison does not DRAW it; and the one
        registered adapter (``statement_import._secu_csv``) declares each
        window as its own first and last line days, so a window reaching past
        its lines is not a state the import door writes -- the GAP case above
        records a review rejecting exactly that fixture.  One mis-dated line
        is enough to stretch a statement past the bound, which is why the
        page draws one.
        """
        with app.app_context():
            _assert_balance(seed_user, db, "925.00", date(2026, 3, 3))
            _seed_import(
                db, seed_user["account"],
                lines=[(date(2024, 1, 5), "-5.00"),
                       (date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 3), "-25.00")],
            )
            _settle(seed_user, seed_periods[4], "50.00", date(2026, 3, 2))
            _settle(seed_user, seed_periods[4], "25.00", date(2026, 3, 3))
            db.session.commit()

            over = _resolved(seed_user).reconciliation
            agreement = bank_agreement.bank_agreement(
                seed_user["account"], BalanceContext.build(seed_user["user"].id),
            )

            # WHY the 58 are unchecked, read off the comparison itself: it
            # drew from 2024-03-03, so those days are not among its days at
            # all -- and every day it did draw is one both records cover.
            assert agreement.span.first_day == date(2024, 3, 3)
            assert all(day.compared for day in agreement.days)
            assert (over.first_day, over.last_day) == (
                date(2024, 1, 5), date(2026, 3, 3),
            )
            # 2024-01-05 through 2026-03-03 inclusive: 362 + 365 + 62.
            assert over.day_count == 789
            assert over.unimported == 0
            assert over.compared == 731
            assert over.unchecked == 58
            # All 58 are imported: 789 imported - 731 compared.
            assert over.imported_unchecked == 58
            # Every drawn day agrees, so nothing but ``unchecked`` is left to
            # refuse this span.
            assert over.disagreeing == 0
            assert over.reconciles is False

    def test_an_EMPTY_span_reconciles_NOTHING(
        self, app, seed_user, seed_periods, db,
    ):
        """A span with no day in it satisfies "nothing disagreed" for free.

        The state every account is CREATED in: the books open and the
        origination assertion lands on ONE day, so no day lies between them.
        Reported as zero days rather than as agreement, which is the difference
        between "the bank checked this and it is fine" and "there was nothing
        to check".
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 3, 1))
            _seed_import(
                db, account, stated="1000.00",
                effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            db.session.commit()

            resolved = _resolved(seed_user, account)

            assert resolved.difference.span.is_empty is True
            assert resolved.reconciliation.day_count == 0
            assert resolved.reconciliation.compared == 0
            assert resolved.reconciliation.disagreeing == 0
            assert resolved.reconciliation.reconciles is False

    def test_an_account_with_NO_statement_has_NO_verdict(
        self, app, seed_user, seed_periods, db,
    ):
        """An absence, never an empty comparison.

        "Nobody has imported a statement" and "the statements say nothing
        disagrees" are different answers, and a surface printing the second for
        the first would tell an owner their books had been checked.
        """
        with app.app_context():
            _settle(seed_user, seed_periods[4], "150.00", date(2026, 3, 3))
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            db.session.commit()

            resolved = _resolved(seed_user)

            assert resolved.difference.amount == Decimal("150.00")
            assert resolved.reconciliation is None


class TestTheStatesADVERSARIALReviewAskedFor:
    """Four shapes the code reaches that its first build left ungraded."""

    def test_a_BACK_DATED_assertion_BELOW_the_books_never_governs(
        self, app, seed_user, seed_periods, db,
    ):
        """Finding N-400's state reaches this figure at all -- and it does not.

        Nothing bounds an assertion at its account's ``opened_on``
        (``anchor_service.resolve_observation_day`` bounds it at the calendar's
        floor and at today), so an owner CAN declare a balance for a day the
        books did not exist on.  Books open 2026-03-10 at ``$500.00``; a
        declaration of ``$600.00`` is then back-dated to 2026-03-05, below
        them.

        **It changes nothing here, and that is the finding.**  The walk orders
        by ``observed_on`` ascending, so the GOVERNING assertion is still the
        origination on 2026-03-10 -- a back-dated row cannot become the latest.
        A first version of this case asserted the opposite (that the span would
        INVERT and the figure would read ``$100.00``) and failed, which is how
        the claim was caught: ``BooksSpan.is_empty``'s docstring had named
        N-400 as one of its two states and no such state exists.

        So N-400 remains a defect about what the app will STORE -- owned by
        ``X-f3c-2b-3`` -- and not one about what this figure answers.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 3, 10))
            _assert_balance(
                seed_user, db, "600.00", date(2026, 3, 5),
                account=account,
            )
            db.session.commit()

            difference = _difference(seed_user, account)

            assert difference.opened_on == date(2026, 3, 10)
            # The ORIGINATION still governs, not the back-dated row.
            assert difference.asserted_on == date(2026, 3, 10)
            assert difference.asserted == Decimal("500.00")
            assert difference.books == Decimal("500.00")
            assert difference.amount == _ZERO
            # Empty because the books open ON the governing declaration's day,
            # which is the only way this state is reachable.
            assert difference.span.is_empty is True

    def test_TWO_assertions_on_ONE_day_take_the_LAST(
        self, app, seed_user, seed_periods, db,
    ):
        """The governing declaration is the last one TYPED, not the first.

        The shape this arc measured on production Checking 2026-04-15 -- three
        balances recorded in one day with no transaction between them.  The
        walk orders ``(observed_on, created_at, id)`` ascending, so
        ``anchor_facts[-1]`` is the row ``cash_ledger.resolve_anchor`` calls
        current; a producer taking the FIRST of the day would answer
        ``$900.00`` here.

        Books ``$1,000.00``, one ``$150.00`` expense, two declarations for
        2026-03-03: ``$900.00`` then ``$1,000.00``.  The books produce
        ``1000 - 150 = 850``, so the outstanding difference is ``$150.00`` and
        not ``$50.00``.
        """
        with app.app_context():
            _settle(seed_user, seed_periods[4], "150.00", date(2026, 3, 3))
            _assert_balance(
                seed_user, db, "900.00", date(2026, 3, 3),
            )
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            db.session.commit()

            difference = _difference(seed_user)

            assert difference.asserted_on == date(2026, 3, 3)
            assert difference.asserted == Decimal("1000.00")
            assert difference.books == Decimal("850.00")
            assert difference.amount == Decimal("150.00")

    def test_a_CREDIT_CARD_is_served_and_its_signs_are_LEDGER_NATIVE(
        self, app, seed_user, seed_periods, db,
    ):
        """A liability the cash detail page serves, and nothing else covered.

        ``classify_account`` answers PLAIN for a Credit Card -- it carries no
        amortization, interest, appreciation or parameters -- and
        ``_cash_page.cash_detail_wrong_type`` serves it, so this card renders
        there.  Every figure is LEDGER-NATIVE, so an owed balance is NEGATIVE
        on both sides.

        The books open at ``-$200.00`` owed; a ``$50.00`` purchase settles,
        taking them to ``-$250.00``; the owner declares ``-$300.00``.  The
        difference is ``-300 - (-250) = -50.00`` -- fifty dollars of spend the
        card has and the books do not, which is the same MEANING the sign
        carries on an asset and the opposite direction of travel.

        **The declaration is dated the FROZEN TODAY**, because
        ``create_account_of_type`` dates its origination assertion the day
        before -- so a declaration on an earlier day would not be the governing
        one and this case would grade the origination instead.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-200.00"),
            )
            _settle(
                seed_user, seed_periods[5], "50.00", date(2026, 3, 20),
                account=card,
            )
            _assert_balance(
                seed_user, db, "-300.00", date(2026, 3, 20),
                account=card,
            )
            db.session.commit()

            difference = _difference(seed_user, card)

            assert difference.opening_equity == Decimal("-200.00")
            assert difference.asserted == Decimal("-300.00")
            assert difference.books == Decimal("-250.00")
            assert difference.amount == Decimal("-50.00")

    def test_a_span_of_TWO_YEARS_costs_no_day_list(
        self, app, seed_user, seed_periods, db,
    ):  # pylint: disable=unused-argument
        """The seeded account's own span is 789 days, and that is the ordinary case.

        Its books open 2024-01-04 and a declaration lands 2026-03-03, so the
        span is far wider than ``bank_agreement``'s two-year drawing bound --
        which is what makes ``unchecked`` the term that carries the answer on a
        production-shaped account rather than an exotic one.

        **``day_count`` is arithmetic and never a materialised list**: the
        span's first day is ``opened_on + 1``, and ``opened_on`` is
        user-supplied through the restatement form with no lower bound, so a
        mis-typed year would otherwise allocate tens of thousands of dates on
        every render of this card.
        """
        with app.app_context():
            _assert_balance(
                seed_user, db, "1000.00", date(2026, 3, 3),
            )
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 3), "-25.00")],
            )
            db.session.commit()

            over = _resolved(seed_user).reconciliation

            # 2024-01-05 through 2026-03-03 inclusive: 362 + 365 + 62.
            assert over.day_count == 789
            assert over.compared == 2
            assert over.unchecked == 787
            assert over.reconciles is False
