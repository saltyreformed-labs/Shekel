"""A recorded posting day moves a debit from the floor into the reduction."""

from datetime import date
from decimal import Decimal

from app.services.cash_ledger._amounts import _entry_aware_amount
from app.models.account import Account
from tests._test_helpers import (
    add_entry,
    create_envelope_txn,
    reassert_balance_on,
    settle_instant_on,
)

from ._helpers import (
    _PURCHASED_ON,
    _POSTED_ON,
    _STATEMENT_DAY,
    _basis,
    _POSTED_AFTER_THE_STATEMENT,
    _envelope,
    _held_back,
)


class TestTheRecordedPostingDay:
    """``settled_on`` moves a debit from the FLOOR into the reduction.

    A purchase the account's latest asserted balance already contains is
    subtracted from the reservation; every other purchase has hit checking
    without the anchor knowing, so it acts as the floor instead.

    **This class tested a stored ``is_cleared`` boolean until plan step S1-c**
    (ruling R-DH (d)).  The flag was written by a bulk UPDATE at every anchor
    true-up over "every entry dated on or before the SERVER's today", so which
    bucket a purchase fell in was decided by the order two buttons were
    pressed.  The bucket is now ``coverage.is_cleared(entry)``, evaluated at
    read time -- the same rule, in the same units, the read replay and the
    posting walk apply to a settled transaction.

    **No purchase here names a statement**, so every case exercises that rule's
    DATE fallback and answers exactly what it answered under R-DH (d).  That is
    the point of these cases surviving X-f3a-1 unchanged: the recording step
    moves no figure.  ``TestALinkOutranksTheDate`` grades the arm that is new.

    Three of the cases below could not be WRITTEN against a flag, and they are
    the ones that matter: an unobserved purchase (NULL), a purchase the bank
    took after the statement was read, and an account that has never declared a
    balance.  Each is OUTSTANDING, which is the conservative arm -- the engine
    never guesses a posting day on the user's behalf.
    """

    def test_the_grocery_bug_after_a_true_up(
        self, app, db, seed_user, seed_periods,
    ):
        """The user-reported defect: three posted purchases against $500.

        est=500, settled_debit=106.86+249.71+105.77=462.34, outstanding=0,
        credit=0.  max(500 - 462.34 - 0, 0) = 37.66 -- only the unreconciled
        remainder is still held.  (Was: 5000 - 37.66 = 4962.34.)

        All three were taken by the bank on 01-21 and the statement the user
        read was true through 01-22, so all three are inside it.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("106.86", False, _POSTED_ON),
                 ("249.71", False, _POSTED_ON),
                 ("105.77", False, _POSTED_ON)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("37.66")

    def test_partial_settled_and_outstanding(
        self, app, db, seed_user, seed_periods,
    ):
        """A posted purchase reduces, an unobserved one floors, in one envelope.

        est=500, settled_debit=100, outstanding_debit=50, credit=0.
        max(500 - 100 - 0, 50) = max(400, 50) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("100.00", False, _POSTED_ON), ("50.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("350.00"), Decimal("50.00"), Decimal("400.00"),
            )

    def test_settled_overspend_floors_at_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Posted debits beyond the budget hold back nothing further.

        est=500, settled_debit=600, outstanding_debit=0, credit=0.
        max(500 - 600 - 0, 0) = max(-100, 0) = 0 -- the money already left and
        the anchor already knows.  (Was: 5000 - 0 = 5000.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("600.00", False, _POSTED_ON)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("0.00")

    def test_all_outstanding_reduces_to_the_legacy_formula(
        self, app, db, seed_user, seed_periods,
    ):
        """With nothing posted the three buckets collapse to the old two.

        est=500, outstanding_debit=200, credit=0.
        max(500 - 0 - 0, 200) = 500, which is what the pre-cleared-flag
        formula gave.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("300.00"), Decimal("200.00"), Decimal("500.00"),
            )

    def test_settled_debit_plus_credit_both_reduce(
        self, app, db, seed_user, seed_periods,
    ):
        """A posted debit and a credit reduce the same reservation.

        est=500, settled_debit=200, outstanding_debit=0, credit=100.
        max(500 - 200 - 100, 0) = 200.  (Was: 4800.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_ON), ("100.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("200.00")

    def test_a_new_purchase_has_no_posting_day_and_is_outstanding(
        self, app, db, seed_user, seed_periods,
    ):
        """A purchase written without a posting day is OUTSTANDING, and safe.

        The default matters to money: a purchase defaulting to SETTLED would
        subtract it from the reservation before the anchor reflected it,
        double-counting it out of the projection.  est=500,
        outstanding_debit=200 -> max(500 - 0 - 0, 200) = 500.  (Was: 4500.)

        ``settled_on`` being NULL is a FACT, not a gap (ruling R-DH (d)): it
        means the user has not seen this purchase on a statement, so the
        envelope keeps holding its whole budget back until they confirm the
        money has left.  The column is asserted directly beside the figure so a
        regression that started defaulting it fails as itself rather than as a
        balance drift.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries",
                Decimal("500.00"),
            )
            add_entry(
                db.session, seed_user, txn, Decimal("200.00"), _PURCHASED_ON,
            )
            db.session.commit()

            assert txn.entries[0].settled_on is None
            assert _held_back(txn) == (
                Decimal("300.00"), Decimal("200.00"), Decimal("500.00"),
            )

    def test_a_purchase_posted_after_the_statement_still_releases(
        self, app, db, seed_user, seed_periods,
    ):
        """The case ruling **R-FM** RE-RULED: posted after the balance was read.

        A ``$200.00`` purchase made 01-20 whose bank took it on 01-23, against a
        balance the user read for 01-22.  max(500 - 200 - 0, 0) = **300.00**.

        **It answered ``$500.00`` until plan step X-f3b**, and the reason it
        moved is that the reservation stopped being the only way this money
        could reach the book.  While a purchase was not a cash movement, a
        purchase no assertion covered had to stay reserved or it would vanish
        from the projection entirely; now it is a movement of its own on 01-23
        (``cash_ledger._events._posted_purchase_facts``), so holding it back
        HERE as well would count the same ``$200.00`` twice.  WHICH assertion
        absorbs it -- none, until the owner declares a balance dated on or after
        01-23 -- is the walk's question about that movement, and
        ``test_cash_walk/`` is where it is graded.

        The mirror of this is the test below it, one day earlier, and the pair
        now answers the SAME on both sides of a boundary the reservation no
        longer has.  Keeping both is the point: they are the control that the
        rule really is a fact about the purchase.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_AFTER_THE_STATEMENT)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("300.00")

    def test_a_purchase_posted_ON_the_statement_day_releases_too(
        self, app, db, seed_user, seed_periods,
    ):
        """The other side of the retired boundary, answering identically.

        A ``$200.00`` purchase the bank took on 01-22, against a balance read
        for 01-22.  max(500 - 200 - 0, 0) = 300.00 -- the same figure the test
        above it now gives for 01-23.

        Under the DATE rule this side answered ``$300.00`` and that one
        answered ``$500.00``, and the off-by-one between them was worth a
        cent-exact pin: an exclusive boundary held the full ``$500.00`` and made
        the projection ``$200.00`` too low every time a user entered their
        balance on a day they shopped, which on the developer's real data is 53
        of 53 same-day entries.  The boundary is gone rather than moved, so what
        this pair pins now is that no boundary is left to get wrong.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _STATEMENT_DAY)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("300.00")

    def test_the_accounts_assertion_history_is_not_an_input(
        self, app, db, seed_user, seed_periods,
    ):
        """ONE envelope, priced with its account's statement on either side.

        A ``$200.00`` purchase the bank took on 01-21, against the account's
        only assertion moved first to 01-22 (after the money moved) and then to
        01-01 (before it).  Both answer ``$300.00``.

        **Under the DATE rule those two states gave $300.00 and $500.00.**  That
        is the whole of what plan step X-f3b changed here, stated as one
        experiment rather than inferred from two tests that share no envelope:
        the reservation is a function of the ROW, and moving the day the owner
        happened to read their bank moves nothing about what this purchase cost.

        **It replaces a test that could not survive the step.**  That one priced
        a purchase against an EMPTY ``StatementCoverage`` -- an account that had
        never asserted a balance -- and expected ``$500.00``.  Ruling **R-FM**
        removed the coverage argument, so its mechanism is unwritable, and the
        property worth keeping is the stronger one it was a special case of.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_ON)],
            )

            # **Each day is ASSERTED, not written over the last one** (plan
            # step X-f3c-2c).  The table is append-only, and the sweep means
            # "whatever the account's statement day is": a later assertion
            # governs, which is exactly how an owner moves that day.  The two
            # days are given latest-last so each iteration's row is the
            # governing one.
            account = db.session.get(Account, txn.account_id)
            for observed_on in (date(2026, 1, 1), _STATEMENT_DAY):
                reassert_balance_on(
                    db.session, account, settle_instant_on(observed_on),
                )
                db.session.flush()

                assert _entry_aware_amount(
                    txn, _basis(txn),
                ) == Decimal("300.00"), (
                    f"the account's statement day ({observed_on}) is not an "
                    f"input to what one purchase costs its envelope"
                )
