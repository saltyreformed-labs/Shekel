"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules of :mod:`app.services.cash_ledger._amounts`,
tested against that module directly.  The cash analog of the loan side's split
tests: given a single row, "how much of this hits the checking balance right
now?"

**These tests MOVED here at plan step X-c2c2a**
(``docs/audits/balance_architecture/README.md``), from
``test_balance_calculator_entries.py`` and ``test_balance_calculator.py``'s
``TestIncomeOverridesSeam``.  They always tested THIS rule; they reached it
through ``balance_at._calculator.calculate_balances``, a PRODUCER that deletes
at plan step X-c2c4, so every assertion read ``anchor - reservation`` and the
anchor arithmetic was scenery.  **Every hand-computed reservation figure is
preserved verbatim** -- the ``5000.00 -`` wrapper is what dropped, and each test
that had one still carries its original arithmetic comment so the two forms can
be diffed against each other.

What did NOT move, and why the split is not "the whole file":

* the STATUS gates (a settled / cancelled / Credit row contributes nothing) and
  the reductions over a SET of rows are :mod:`app.services.cash_ledger._flows`'
  question, and moved to ``test_cash_flows.py``;
* the two ANCHOR-PERIOD tests discriminate a ``_calculator`` branch -- which of
  its two arms calls the shared reduction -- so they stayed with that module and
  died with it at plan step X-g4b.

**Plan step X-g4b then moved C5-8 here too** --
:meth:`TestTheEntriesRelationshipIsNotASeam.test_the_silent_degrade_seam_is_absent_from_source`,
the STATIC guard that the CRIT-01 short-circuit has not been re-introduced.  It
lived in ``test_balance_resolver.py``, which died with ``balances_for``; its
home is the module holding the rule it guards, and its scan WIDENED with the
move (see the method for why that is the guard's own stated logic rather than
opportunism).

The plan's own one-liner said "``test_balance_calculator_entries.py`` (27 tests)
is the three-bucket reservation formula"; tracing measured 18, and the
correction is recorded at the step.

The reservation formula under test::

    posted_debit   = sum(amount where not is_credit and settled_on is not None)
    unposted_debit = sum(amount where not is_credit and settled_on is None)
    sum_credit     = sum(amount where is_credit)

    impact = max(estimated - posted_debit - sum_credit, unposted_debit)

**Which bucket a debit falls in is whether the BANK HAS TAKEN IT** (plan step
X-f3b, ruling **R-FM**), and its history is four answers deep -- each a
narrowing, and the last one a deletion.  It was a stored ``is_cleared``
boolean, carried in every fixture below as a third bool in a triple: an
unconditional claim that a purchase was inside the anchor, which its own
account could not always support.  Ruling R-DH (d) replaced it with a DATE
comparison against the account's latest asserted day, and the triples took the
day the bank was seen to take the money.  The developer's bank exports
falsified that comparison (33 of 110 matched movements carry the day the bank
posted them), so ruling R-FL made it the RECORDED statement, with the date rule
as the fallback.

**R-FM then removed the question from this module entirely**, which is the
simplification rather than a fifth answer.  A purchase carrying a posting day
is now a cash movement of its OWN in the walk, so the reservation asks what it
always should have -- has this money left -- and *which statement cleared it*
is asked once, where the money is replayed (see the `_STATEMENT_DAY` comment
below for the two tests that grade it).  Two consequences are re-ruled test cases rather than
renames, and both are marked as such below: a purchase the bank took AFTER the
balance was read now releases the reservation, and the account's assertion
history is not an input at all.  A NULL posting day is still OUTSTANDING, which
is the arm that never moved.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.exceptions import AmountUnresolvable
from app.services.cash_ledger import _amounts, in_flight_movements
from app.services.cash_ledger._amounts import (
    _entry_aware_amount,
    contribution_of,
)
from app.models.account import Account
from tests._repo_root import REPO_ROOT
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_envelope_txn,
    planted_basis,
    reassert_balance_on,
    settle_instant_on,
)


# The four days these tests turn on, named for the fact each one is.  They are
# DISTINCT on purpose: a purchase made, taken by the bank a day later, and read
# on a statement two days after that is the ordinary debit-card shape, and
# collapsing them onto one date would hide which comparison the rule makes.
_PURCHASED_ON = date(2026, 1, 20)
_POSTED_ON = date(2026, 1, 21)
# The day a statement was read.  It is SCENERY since plan step X-f3b (ruling
# **R-FM**) and kept for exactly that reason: the reservation no longer asks
# which statement showed a purchase, so a day that once decided which bucket a
# debit fell in now decides nothing here, and
# `test_the_accounts_assertion_history_is_not_an_input` is the control that
# says so.  Which statement CLEARED a movement is the walk's question, and a
# PURCHASE's own link is graded there rather than here:
# `test_cash_walk.py`'s
# `TestARecordedClearingFactMayNotMoveALineAcrossAStatement
# ::test_a_PURCHASE_carries_its_OWN_link_not_its_parents` on the read side, and
# `test_account_posting_service.py`'s
# `TestWalkAccountLedger::test_a_purchases_clearing_link_is_read_off_the_PURCHASE`
# on the posted one.  Both were ADDED at this step: the class existed for
# transactions only, and citing it for purchases before that was an invented
# citation an adversarial review caught.
_STATEMENT_DAY = date(2026, 1, 22)


def _basis(*rows, overrides=None):
    """The :class:`AmountBasis` a producer would hand these rows.

    ``overrides`` is planted on the live DERIVATIONS
    (:class:`~tests._test_helpers.PlantedPricing`) rather than into a
    ``{transaction_id: Decimal}`` map the basis carries: plan step X-au-c2b
    made a basis hold the derivations themselves, keyed on an owner and a
    scenario rather than on a row set.  Since plan step **X-au-d** the salary
    derivation is what amount RULE 2 asks directly, rather than what a
    read-time repair indexed, so a planted answer is graded through the
    resolver and not through an override seam laid over it.

    **It was a ``ProjectedBasis`` carrying the account's clearing rule beside
    this until plan step X-f3b** (ruling **R-FM**), because the reservation
    asked which statement had cleared a purchase.  It asks the purchase now, so
    the wrapper and its second field went with the question.
    """
    return planted_basis(*rows, overrides=overrides)
_POSTED_AFTER_THE_STATEMENT = date(2026, 1, 23)


def _envelope(db_session, seed_user, period, estimated, entries=()):
    """Build a Projected envelope expense carrying *entries*, and return it.

    The shared setup for the reservation tests.  Each moved test built ~50
    lines of template + transaction + entry construction inline and then
    re-queried with ``selectinload``; the re-query is not reproduced because
    the rule under test reads ``txn.entries`` through the relationship
    descriptor, which resolves either way -- a property
    :class:`TestTheEntriesRelationshipIsNotASeam` below pins explicitly rather
    than leaving it implied in 18 setups.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        period: The :class:`~app.models.pay_period.PayPeriod` to place it in.
        estimated: The envelope's budgeted amount, as a string.
        entries: An iterable of ``(amount, is_credit, settled_on)`` triples --
            a string amount, a bool, and the day the bank was seen to take the
            money (or ``None`` for a purchase not yet seen on a statement).
            The third element was a BOOL until plan step S1-c, which is the
            whole finding: a flag claimed a purchase was inside the anchor
            without saying which day made it so, while a date can be compared
            against the day the balance was actually read.  Passing the day at
            the call site is what makes each test's bucket readable there
            instead of in this helper.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    txn = create_envelope_txn(
        seed_user, db_session, period, "Groceries", Decimal(estimated),
    )
    for amount, is_credit, settled_on in entries:
        add_entry(
            db_session, seed_user, txn, Decimal(amount), _PURCHASED_ON,
            is_credit=is_credit, settled_on=settled_on,
        )
    db_session.commit()
    return txn


def _in_flight(txn):
    """Return what the IN-FLIGHT tier holds for *txn*'s un-dated purchases.

    Ruling **R-BAL77** (plan step ``balance:X-bi-4a``) split the three-bucket
    reservation in two: :func:`_entry_aware_amount` holds the envelope's
    UNSPENT budget and the plan's in-flight tier holds each un-dated non-card
    purchase at its own figure, ``-delta`` under an expense.  The cases below
    assert both halves and their sum, which is the figure each asserted of
    the one function through ``X-bi-3e``.
    """
    return sum(
        (
            -fact.delta
            for fact in in_flight_movements(txn.account_id, txn.scenario_id)
            if fact.transaction_id == txn.id
        ),
        Decimal("0"),
    )


def _held_back(txn):
    """Return ``(unspent budget, in flight, their sum)`` for *txn*."""
    unspent = _entry_aware_amount(txn, _basis(txn))
    in_flight = _in_flight(txn)
    return unspent, in_flight, unspent + in_flight


class TestTheEntryAwareReservation:
    """The reservation for a still-Projected envelope expense, in its two halves.

    The six scope-doc scenarios (Section 4.2) plus the boundary shapes.  Every
    figure was the three-bucket reservation itself until plan step
    ``balance:X-bi-4a``; since ruling **R-BAL77** each case asserts the
    UNSPENT budget (:func:`_entry_aware_amount`), the IN-FLIGHT purchases
    (the plan tier's own item per un-dated purchase) and their sum, which is
    the old figure: ``max(E - P - C, U) == U + max(E - SUM(entries), 0)``.
    Before X-c2c2a each was asserted as ``5000.00 - reservation`` through the
    balance walk.
    """

    def test_no_entries_holds_the_full_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 1: a tracked expense with no entries holds its estimate.

        est=500, debit=0, credit=0 -> the empty-entries short circuit returns
        ``effective_amount``, which for an unfilled Projected expense is the
        estimate: 500.00.  (Was: 5000 - 500 = 4500.)
        """
        with app.app_context():
            txn = _envelope(db.session, seed_user, seed_periods[1], "500.00")

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_debit_under_budget_holds_the_full_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 2: an uncleared debit under budget does not reduce it.

        est=500, uncleared_debit=200, credit=0.
        max(500 - 0 - 0, 200) = max(500, 200) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("300.00"), Decimal("200.00"), Decimal("500.00"),
            )

    def test_a_credit_entry_reduces_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 3: mixed debit + credit under budget -- credit reduces.

        est=500, uncleared_debit=300, credit=100.
        max(500 - 0 - 100, 300) = max(400, 300) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", False, None), ("100.00", True, None)],
            )

            assert _held_back(txn) == (
                Decimal("100.00"), Decimal("300.00"), Decimal("400.00"),
            )

    def test_all_credit_leaves_only_the_uncovered_portion(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 4: all-credit entries -- only the uncovered part hits cash.

        est=500, debit=0, credit=400.
        max(500 - 0 - 400, 0) = max(100, 0) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("100.00")

    def test_debit_overspend_raises_the_reservation_to_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 5: overspend -- the uncleared debit total is the floor.

        est=500, uncleared_debit=530, credit=0.
        max(500 - 0 - 0, 530) = max(500, 530) = 530.  (Was: 4470.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("530.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("530.00"), Decimal("530.00"),
            )

    def test_mixed_overspend_takes_the_debit_floor_over_the_reduction(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 6: the debit floor beats the credit-reduced reservation.

        est=500, uncleared_debit=400, credit=200.
        max(500 - 0 - 200, 400) = max(300, 400) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", False, None), ("200.00", True, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("400.00"), Decimal("400.00"),
            )

    def test_zero_estimate_with_a_debit_reserves_the_debit(
        self, app, db, seed_user, seed_periods,
    ):
        """A zero-budget envelope still holds back what was actually spent.

        est=0, uncleared_debit=50, credit=0.
        max(0 - 0 - 0, 50) = 50.  (Was: 4950.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "0.00",
                [("50.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("50.00"), Decimal("50.00"),
            )

    def test_credit_exceeding_the_estimate_floors_at_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit beyond the budget cannot drive the reservation negative.

        est=500, uncleared_debit=100, credit=600.
        max(500 - 0 - 600, 100) = max(-100, 100) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("100.00", False, None), ("600.00", True, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("100.00"), Decimal("100.00"),
            )

    def test_one_cent_debit_does_not_disturb_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """The smallest representable entry: a one-cent uncleared debit.

        est=500, uncleared_debit=0.01, credit=0.
        max(500 - 0 - 0, 0.01) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("0.01", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("499.99"), Decimal("0.01"), Decimal("500.00"),
            )

    def test_values_near_the_column_limit_do_not_overflow(
        self, app, db, seed_user, seed_periods,
    ):
        """Values at the ``Numeric(12,2)`` ceiling survive the ``max()``.

        est=9999999999.99, uncleared_debit=9999999999.99, credit=0.
        max(9999999999.99 - 0 - 0, 9999999999.99) = 9999999999.99.
        (Was: 10000000000.00 - 9999999999.99 = 0.01.)
        """
        with app.app_context():
            large = "9999999999.99"
            txn = _envelope(
                db.session, seed_user, seed_periods[1], large,
                [(large, False, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal(large), Decimal(large),
            )

    def test_a_row_with_no_template_is_worth_its_effective_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A plain (non-envelope) expense carries no entries, so no reduction.

        The pre-entries behaviour, and still the common case: no template
        means no entries, so the short circuit returns ``effective_amount``
        -- 1200.00.  (Was: 5000 - 1200 = 3800.)
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[1], "Rent", "1200.00",
                category_key="Rent",
            )
            db.session.commit()

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("1200.00")


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
        ``test_cash_walk.py`` is where it is graded.

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


class TestTheEntriesRelationshipIsNotASeam:
    """The reduction applies whether or not the caller pre-loaded entries.

    The structural fix for CRIT-01 / F-009 / E-25, and the reason
    :func:`_envelope` above does not re-query with ``selectinload``: symptom #1
    ($160 on the grid against $114.29 on ``/savings`` for one row) was exactly
    this seam, where the rule returned ``effective_amount`` whenever the
    consuming query had not issued the eager load.  The rule now reads
    ``txn.entries`` through the relationship descriptor, which lazy-loads on
    demand, so the value is a function of the DATA and not of the caller's
    query plan.
    """

    def test_an_expired_instance_still_reduces(
        self, app, db, seed_user, seed_periods,
    ):
        """Entries NOT resident on the instance -- the descriptor loads them.

        est=500, uncleared_debit=0, credit=300.
        max(500 - 0 - 300, 0) = 200.  (Was: 5000 - 200 = 4800.)

        The instance is expired first so ``entries`` is genuinely absent from
        its ``__dict__``, which is the state a caller that skipped the eager
        load produces.  Pre-E-25 that state returned 500.00.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", True, None)],
            )
            db.session.expire(txn)
            assert "entries" not in txn.__dict__

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("200.00")

    def test_the_silent_degrade_seam_is_absent_from_source(self):
        """C5-8: the ``'entries' not in __dict__`` short-circuit is not in source.

        The static half of the guard above: the test beside it proves the rule
        BEHAVES correctly today, and this one proves the shape that broke it
        cannot be re-introduced anywhere a balance is folded.  A future
        regression re-adding the eager-load presence check fails this loud.

        **It MOVED here at plan step X-g4b, and its scan WIDENED with the
        move.**  It lived in ``test_balance_resolver.py`` and scanned
        ``balance_at/_cash_engine.py``, ``balance_at/_calculator.py`` and the
        whole ``cash_ledger`` package; the first two were deleted at that step
        and the guard would have raised on their paths.  Its own rationale is
        why the replacement is a WIDENING rather than a subtraction: it enumerates
        a PACKAGE rather than naming files precisely because "a scan keyed on a
        file NAME would have gone quiet at exactly the moment the code it guards
        moved" (finding N-28, the shape where creating a module escapes a
        module-keyed gate).  Every balance producer now lives in ``balance_at``,
        so that package is scanned whole beside ``cash_ledger`` -- and
        ``rglob``, not ``glob``, so a future nested subpackage cannot escape
        either.

        Verified free at the move: neither forbidden pattern appears in either
        package on this date, so the widening costs nothing and closes the
        larger surface.
        """
        services = REPO_ROOT / "app" / "services"
        packages = {
            name: sorted((services / name).rglob("*.py"))
            for name in ("cash_ledger", "balance_at")
        }
        # The walk must really have happened AND must have reached the module
        # the forbidden patterns would actually appear in.  Asserting a total
        # instead would be a magic count that breaks when a package legitimately
        # gains or sheds a module.
        for name, sources in packages.items():
            assert sources, f"the {name} package enumerated to nothing"
        assert any(
            source.name == "_amounts.py" for source in packages["cash_ledger"]
        ), (
            "the entry-aware rule's module was not scanned: "
            f"{[s.name for s in packages['cash_ledger']]}"
        )
        assert any(
            source.name == "_cash_fold.py" for source in packages["balance_at"]
        ), (
            "the cash fold's module was not scanned: "
            f"{[s.name for s in packages['balance_at']]}"
        )

        forbidden_patterns = ("not in txn.__dict__", "'entries' not in")
        for sources in packages.values():
            for source_path in sources:
                source = source_path.read_text(encoding="utf-8")
                for pattern in forbidden_patterns:
                    assert pattern not in source, (
                        f"Forbidden seam pattern {pattern!r} found in "
                        f"{source_path}.  E-25 / CRIT-01 / F-009 regression: no "
                        "balance path may consult the instance __dict__ to "
                        "decide whether the entries-aware reduction applies."
                    )


class _FakeActiveProfile:  # pylint: disable=too-few-public-methods
    """The one attribute ``is_salary_linked_template`` reads off a profile."""

    is_active = True


class _FakeSalaryTemplate:  # pylint: disable=too-few-public-methods
    """A definition an ACTIVE salary profile names, for rule 2's refinement."""

    salary_profiles = (_FakeActiveProfile(),)


class _FakeRow:  # pylint: disable=too-few-public-methods
    """A non-ORM stand-in carrying only what a valuation rule may read.

    Deliberately missing ``entries``, and missing ``status_id`` too when
    *statusless* is set: the ordering of :func:`_entry_aware_amount`'s two
    guards is load-bearing, and that shape is what proves it (see
    :meth:`TestTheLiveOverride.test_no_entries_short_circuits_before_the_status_read`).

    **It grew the pricing columns at plan step X-au-c2b and the settlement
    record's at X-au-c3**, and the reason is the restructure itself: a
    read-time repair indexed a ``{transaction_id: Decimal}`` map the basis
    carried, so it read no column at all; the RULES read the row's own columns
    to decide which of them prices it.  Those reads are the same ones the
    producers made when they BUILT that map over a row set -- moved, not added
    -- so a stand-in for a row a valuation may see carries them.

    **A salary-shaped row is DECLARED derived since plan step X-au-d**, which
    is not a convenience: that step is what made "the salary profile prices
    this row" and "this row stores no figure" the same fact, so a stand-in that
    was salary-shaped AND owned a figure would be a state the amount model
    cannot be in.  It carries the ``template`` stub amount rule 2's refinement
    reads for the same reason.
    """

    def __init__(
        self, txn_id=None, effective_amount="77.00", statusless=False,
        *, salary_shaped=False, pay_period_id=None,
    ):
        self.id = txn_id
        # What the row OWNS, which since plan step X-au-c2 is what the
        # valuation reads: ``amount_source_id IS NULL`` says the figure is the
        # row's own, and the four attributes below are every column the OWN arm
        # and the contribution gate touch.  There is no ``effective_amount``
        # property any more -- the resolved figure arrives as an argument.
        #
        # **Plain attributes, and NOT ``amount_ownership``** (plan step
        # X-au-k).  This is a stand-in rather than a mapped row, so what it
        # owes the rules under test is their READ shape; the composite is how
        # the real model keeps those two reads consistent, and a stub that
        # carried it would have to re-implement the projection to answer them
        # at all.
        # A salary-shaped row DECLARES its definition and stores no figure
        # (plan step X-au-d); every other stand-in OWNS what it holds.  The two
        # halves are one attribute on the real model
        # (``ck_transactions_amount_ownership`` pairs them), so a stand-in that
        # set them independently could express a state no row may be in.
        self.estimated_amount = None if salary_shaped else Decimal(
            effective_amount,
        )
        self.amount_source_id = (
            ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE)
            if salary_shaped else None
        )
        # What amount rule 2's refinement reads to tell a salary-linked
        # definition from an ordinary one
        # (``template_amount_service.is_salary_linked_template``).
        self.template = _FakeSalaryTemplate() if salary_shaped else None
        # The settlement RECORD (plan step X-au-c3) is the row's ENTRIES
        # since ``balance:X-bi-4b-1``: ``settled_figure`` asks the STATUS
        # first -- a retained record on a reverted row is not what that row
        # is worth -- and the entries only for a row the status says has
        # settled, so a stand-in a valuation may see carries both.
        self.entries = []
        self.is_deleted = False
        self.status = None
        # What the live-override seam reads before it answers "nothing
        # supersedes this row": the loan half asks for a transfer, the salary
        # half for an income row on a template, and both for the two status
        # facts.  ``None`` / ``False`` throughout, so neither derivation is
        # reached and the fall-through under test is what runs.
        self.transfer_id = None
        # A row the SALARY half can answer for: income, on a template, in a
        # period.  Off by default, so the fall-through under test is what runs.
        # It became the only way to reach the seam at plan step X-au-g-2c-2 --
        # the loan half is deleted, and it was the half a planted figure used
        # to land on.
        self._salary_shaped = salary_shaped
        # The scenario the basis this row is priced against declares.  Zero on
        # both sides, matching ``planted_basis``'s default: a mismatch is what
        # ``resolve_transaction_amount`` refuses (plan step X-au-c2b).
        self.scenario_id = 0
        self.is_override = False
        self.is_income = salary_shaped
        self.template_id = txn_id if salary_shaped else None
        self.pay_period_id = pay_period_id
        # ``status_id`` is always present, and *statusless* now means the
        # ``status`` RELATIONSHIP is absent rather than the column.  Since plan
        # step X-au-c3 every valuation asks the status whether a row is worth
        # what it RECORDED or what it PLANS, so a stand-in without the column is
        # not a row any valuation could see -- and a test double built around a
        # missing attribute grades an impossible input.
        #
        # A salary-shaped row carries the PROJECTED id, because the salary
        # half's own gate is ``is_projected(txn) and not txn.is_override``.
        self.status_id = (
            ref_cache.status_id(StatusEnum.PROJECTED) if salary_shaped else None
        )


class TestTheProjectedValuation:
    """What a still-PROJECTED row contributes, by the rule that prices it.

    **This class was ``TestTheLiveOverride`` until plan step X-au-d**, and the
    rename is the change: there is no override.  A projected salary paycheck
    still reflects the CURRENT salary profile rather than a figure a later
    profile, calibration or code change invalidated -- but it does so because
    the profile is the ONLY producer of that figure, not because a read-time
    repair lays one over a stored copy.  ``income_amount``, which consumed that
    repair, collapsed onto :func:`contribution_of` and was deleted rather than
    kept as a second spelling (``CLAUDE.md`` rule 14).

    The cases below are the same three claims restated against the model that
    replaced the seam, and the THIRD is a behaviour change rather than a
    rewording: an unanswered salary row used to fall back to its stored figure
    and now REFUSES, because there is no figure to fall back to.

    Moved from ``TestIncomeOverridesSeam`` (X-c2c2a) and from
    ``test_balance_resolver.py`` (the expense leg's precedence and the guard
    ordering, which arrived there at plan step X-c2c1).  The fourth
    ``TestIncomeOverridesSeam`` test did NOT move: it pins that the override is
    honoured in the POST-ANCHOR period specifically, which is a ``_calculator``
    branch rather than a valuation rule.
    """

    def test_a_declared_paycheck_contributes_its_PROFILES_net(self, app):
        """A salary row is worth what its profile pays for that period.

        The valuation reaches amount rule 2, which asks the pass's salary
        derivation.  The discriminator is the row's own column: a declared row
        holds none, so a valuation reading it would answer ``None`` rather than
        a figure.
        """
        with app.app_context():
            row = _FakeRow(
                txn_id=101, effective_amount="2000.00",
                salary_shaped=True, pay_period_id=7,
            )
            basis = _basis(row, overrides={(101, 7): Decimal("2473.38")})

            assert row.estimated_amount is None
            assert contribution_of(row, basis) == Decimal("2473.38")

    def test_a_row_that_OWNS_its_figure_contributes_that_figure(self):
        """Rule 1, and the non-vacuity partner for the case above.

        Without it, a valuation that answered the salary derivation for EVERY
        row would pass -- and that valuation prices a haircut at a paycheck.
        """
        row = _FakeRow(txn_id=101, effective_amount="2000.00")
        basis = _basis(row)

        assert contribution_of(row, basis) == Decimal("2000.00")

    def test_a_paycheck_the_projection_does_not_cover_is_REFUSED(self, app):
        """There is no fallback left, and that is plan step X-au-d's point.

        The derivation answers for ``(999, 7)`` and this row is ``(101, 7)``.
        Before the cutover such a row kept its stored figure -- which is
        precisely the stale cache ruling **R-FI** deletes -- and a valuation
        that still substituted one would be publishing it.  A refusal is never
        a fallback, so the rule raises and names the row.
        """
        with app.app_context():
            row = _FakeRow(
                txn_id=101, effective_amount="2000.00",
                salary_shaped=True, pay_period_id=7,
            )
            basis = _basis(row, overrides={(999, 7): Decimal("5.00")})

            with pytest.raises(
                AmountUnresolvable, match="live recompute answered nothing",
            ):
                contribution_of(row, basis)

    # ``test_an_override_wins_over_the_entry_formula`` lived here until plan
    # step X-au-g-2c-2, and the RULE it graded is deleted rather than the test
    # being weakened.  It asserted that on the EXPENSE leg a live-derived
    # amount short-circuits the three-bucket reservation, returning $123.45
    # verbatim instead of the $50.00 the entry formula gives.
    #
    # The seam had two halves and the LOAN one was the only one an expense row
    # could ever take -- a loan payment's cash debit sits on the funding
    # account.  That half went first: a transfer shadow is DERIVED, so there is
    # no stored figure for an override to supersede.  Plan step **X-au-d** then
    # took the salary half and the seam with it, and ``_expense_amount`` -- a
    # one-line forward that existed only for symmetry with ``income_amount`` --
    # went too.  ``sum_projected``'s expense leg calls
    # ``_entry_aware_amount`` directly now, which is exactly what
    # ``TestTheEntryAwareReservation`` below grades, so the successor case this
    # note used to point at has no separate subject left and is deleted with
    # the wrapper.  A test whose only possible outcome is a pass is not a test
    # (finding **N-184**'s rule).

    def test_no_entries_short_circuits_before_the_status_read(
        self, monkeypatch,
    ):
        """The guard ORDER holds: no entries returns before ``is_projected``.

        ``_entry_aware_amount`` checks ``not entries`` FIRST, so an entry-less
        row is valued without the status read the entry-aware branch needs.
        That is load-bearing rather than stylistic: ``is_projected`` resolves a
        ``ref_cache`` id, which is work an entry-less row has no reason to pay.

        **It is graded by a SPY rather than by a missing attribute** (plan step
        X-au-c3).  The proof used to be that ``_FakeRow`` omitted ``status_id``,
        so a swapped order raised ``AttributeError``.  That stopped being a
        legal input: every valuation now asks the status whether a row is worth
        what it RECORDED or what it PLANS, so a row without the column is one no
        valuation could ever see, and a control whose input is impossible grades
        nothing.  Counting the call states the ordering claim directly and still
        fails on a swap -- the entry-aware branch below calls ``is_projected``
        exactly once, so a reordered guard makes this count 1.
        """
        calls = []
        real = _amounts.is_projected
        monkeypatch.setattr(
            _amounts, "is_projected",
            lambda row: (calls.append(row), real(row))[1],
        )

        row = _FakeRow(txn_id=1, statusless=True)
        assert _entry_aware_amount(row, _basis(row)) == Decimal("77.00")
        assert calls == []
