"""The settle day the bank evidences: a re-dated settle leaves no movement behind, and a
confirmation of a day the panel only bounded writes the basis, not the day.
"""

from datetime import timedelta
from decimal import Decimal

from app.enums import SettledDayBasisEnum
from app.services import balance_at
from app.services.balance_at import BalanceContext

from tests._test_helpers import create_settled_cash_transaction

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_transaction,
    an_assertion,
    an_import,
)
from app.services.settle_day import SettleDay, recorded_settle_day

from ._helpers import _posted_cash_by_day, _submit


def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*.

    Args:
        seed_user: The seeded user bundle.
        day: The day to value the account on.

    Returns:
        The balance.
    """
    return balance_at.balance_at(
        seed_user["account"],
        BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"], as_of=day,
        ),
        day,
    )


class TestARedatedSettleLeavesNoMovementBehind:
    """Finding **N-324**: every day assertion here had nothing on the OLD day.

    ``_builders.a_transaction`` writes ``status_id`` and ``settled_on``
    straight through the ORM and the covering movement through the seam's
    own writer, and that route is DELIBERATE -- a broken settle verb must not
    also break the fixture that would have caught it (the builders' own module
    docstring).  **What nobody stated was the consequence.**  Those rows held
    no ledger postings while the stored ledger lived, so a defect that left
    the OLD day's posting behind on a re-dated settle, the classic
    double-count, was invisible to every case in this file.  X-f6a-3c-2's
    batch-versus-per-act control compares the two paths' ledgers, so it cannot
    see an error common to both.

    **The remedy is a fixture built through the REAL settle door**
    (``tests._test_helpers.create_settled_cash_transaction``, which drives
    ``status_seam.apply_status_change`` as the mark-done route does), and an
    assertion about the OLD day rather than only the new one.  It sits beside
    the ORM-built cases rather than replacing them, because the two grade
    different things.

    **BOTH CASES BELOW CATCH the defect ruling R-FA exists to prevent**: a
    matcher that stamps the row's ``settled_on`` itself instead of going
    through the status door, so the row's covering movement keeps the old day
    (``status_seam._covering._mirror_assertion`` is what moves it).  The
    balance walk and the ledger read both read that MOVEMENT's day
    (:class:`app.services.cash_ledger.CashSourceFact`), never the row's.
    Planted 2026-11-10, it failed each: the old day's balance read
    ``Decimal('820.00')`` where ``820.00 + 180.00`` was due, and the ledger
    still netted ``-180.00`` on the old day and nothing on the bank's.  Until
    plan task plan#3396 this docstring said only the ledger case could see it;
    the plant measured that false.
    """

    def test_the_OLD_days_balance_stops_carrying_the_row(
        self, app, db, seed_user,
    ):
        """MONEY: the figure the app PUBLISHES moves to the bank's day.

        The row settles on the day the app guessed.  The bank says it moved
        four days later, so the earlier day must stop carrying it and the later
        day must carry it exactly once -- the account would otherwise be short
        by this amount on every day between the two.

        **This is the PRODUCER's half**: the figure a screen shows.  The
        sibling case below reads the same movement per day, where a leftover
        on one day and a missing one on another cannot cancel inside a sum
        (see the class docstring for the defect both catch).
        """
        period = seed_user["bootstrap_period"]
        guessed = period.start_date + timedelta(days=1)
        bank_day = guessed + timedelta(days=4)
        txn = create_settled_cash_transaction(
            seed_user, period, Decimal("180.00"),
            settled_on=guessed, name="Electricity",
        )
        db.session.flush()
        before_at_guessed = _balance_on(seed_user, guessed)
        before_at_bank_day = _balance_on(seed_user, bank_day)

        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[line], transactions=[txn])
        db.session.flush()

        db.session.refresh(txn)
        assert txn.settled_on == bank_day
        # The old day RELEASES it...
        assert _balance_on(seed_user, guessed) == (
            before_at_guessed + Decimal("180.00")
        )
        # ...and the bank's day still carries it, exactly once.
        assert _balance_on(seed_user, bank_day) == before_at_bank_day

    def test_the_rows_cash_NETS_to_the_banks_day_alone(
        self, app, db, seed_user,
    ):
        """The same fact read from the LEDGER rather than from a balance.

        A balance is one sum over everything, so a leftover line here and a
        missing one there can cancel inside it; the ledger, read per day and per
        account, cannot hide either.  What says the money moved is that the old
        day holds nothing.
        """
        period = seed_user["bootstrap_period"]
        guessed = period.start_date + timedelta(days=1)
        bank_day = guessed + timedelta(days=4)
        txn = create_settled_cash_transaction(
            seed_user, period, Decimal("180.00"),
            settled_on=guessed, name="Electricity",
        )
        db.session.flush()
        account = seed_user["account"]
        assert _posted_cash_by_day(db, txn, account) == {
            guessed: Decimal("-180.00"),
        }

        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[line], transactions=[txn])
        db.session.flush()

        assert _posted_cash_by_day(db, txn, account) == {
            bank_day: Decimal("-180.00"),
        }


class TestTheBankCanCONFIRMADayThePanelOnlyBOUNDED:
    """Plan step **X-az**: a confirmation writes the basis, not the day.

    **The reverse-direction half of finding N-332.**  The reconcile panel stamps
    the day the owner asserted a BALANCE for -- the money moved on or BEFORE it.
    When a bank line then posts on exactly that day the app has learned
    something: the bound is the true posting day.  Nothing could record it,
    because ``_apply_day``'s ``"unchanged"`` arm returned early and no settle
    door fires when the day does not move, so such a row went on reporting
    itself a bound forever.

    **The DAY is unchanged, so the receipt is unchanged**: neither
    ``settled_count`` nor ``corrected_count`` moves, because nothing settled and
    nothing was corrected.  What moves is the stored answer to *how is this day
    known*, and with it the window the matcher would bound a future line by.
    """

    @staticmethod
    def _reconciled_purchase(seed_user, made_on, asserted_for):
        """Return a purchase ticked on the panel: an ASSERTED day and a link."""
        assertion = an_assertion(seed_user, observed_on=asserted_for)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        return a_purchase(
            seed_user, envelope, amount="18.64", purchased_on=made_on,
            settled_on=asserted_for, reconciled_by=assertion,
            settle_day_basis=SettledDayBasisEnum.ASSERTED,
        )

    def test_a_line_on_the_bound_itself_raises_the_basis_to_observed(
        self, app, db, seed_user,
    ):
        """The confirmation: same day, stronger evidence, recorded."""
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            asserted_for = made_on + timedelta(days=20)
            purchase = self._reconciled_purchase(
                seed_user, made_on, asserted_for,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-18.64", posted_on=asserted_for,
            )
            db.session.flush()

            accepted = _submit(seed_user, lines=[line], entries=[purchase])
            db.session.flush()

            assert recorded_settle_day(purchase) == SettleDay(
                day=asserted_for, basis=SettledDayBasisEnum.OBSERVED,
            )
            # The DAY did not move, so neither tally counts it.
            assert accepted.settled_count == 0
            assert accepted.corrected_count == 0

    def test_the_confirmation_KEEPS_the_clearing_link(
        self, app, db, seed_user,
    ):
        """A statement that AGREES does not withdraw the one already seen.

        Every settle door releases ``reconciled_by_id`` when the day MOVES, and
        the predicate is about the day rather than the pair for exactly this
        case: an observation the bank confirms strengthens the record the link
        holds instead of contradicting it.
        """
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            asserted_for = made_on + timedelta(days=20)
            purchase = self._reconciled_purchase(
                seed_user, made_on, asserted_for,
            )
            linked_to = purchase.reconciled_by_id
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-18.64", posted_on=asserted_for,
            )
            db.session.flush()

            _submit(seed_user, lines=[line], entries=[purchase])
            db.session.flush()

            assert purchase.reconciled_by_id == linked_to

    def test_a_row_already_OBSERVED_is_left_entirely_alone(
        self, app, db, seed_user,
    ):
        """The early return survives: only a WEAKER basis is worth writing.

        Without this arm the confirmation runs a full settle door on every
        already-correct row of every accept -- a payback sync and an
        optimistic-lock bump apiece (and a posting reconcile until plan task
        plan#3097), for a column that is already right.
        """
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            bank_day = made_on + timedelta(days=2)
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="18.64", purchased_on=made_on,
                settled_on=bank_day,
                settle_day_basis=SettledDayBasisEnum.OBSERVED,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-18.64", posted_on=bank_day,
            )
            db.session.flush()
            before = purchase.version_id

            _submit(seed_user, lines=[line], entries=[purchase])
            db.session.flush()

            assert purchase.version_id == before, (
                "an already-observed row was written for nothing"
            )
            assert recorded_settle_day(purchase).basis is (
                SettledDayBasisEnum.OBSERVED
            )
