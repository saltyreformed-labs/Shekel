"""
Shekel Budget App -- Cash ledger: what a SET of rows SUMS TO.

The Projected-only ``(income, expense)`` reduction of
:func:`app.services.cash_ledger.sum_projected`, tested against that function
directly.  Its sibling :mod:`tests.test_services.test_cash_amounts` grades what
ONE row is worth; this grades which rows are counted at all, and on which leg.

**These tests MOVED here at plan step X-c2c2b**
(``docs/audits/balance_architecture/README.md``), from
``test_balance_calculator_entries.py`` (which then had nothing left to hold and
deleted) and ``test_balance_resolver.py``.  They always tested THIS rule; they
reached it through ``balance_at._calculator.calculate_balances``, a PRODUCER
plan step X-g4b deleted (carrying X-c2c4's content), so every assertion read
``anchor +/- the reduction`` and the anchor arithmetic was scenery.  **Every
hand-computed figure is preserved verbatim** -- each test states the reduction
it now asserts and the balance it used to assert, so the two forms can be
diffed against each other.

Two questions this file does NOT answer, and their homes:

* what one row is WORTH -- ``test_cash_amounts.py``;
* which STARTING balance the reduction is added to, and how it rolls forward
  period after period -- the cash fold's (``balance_at._cash_fold``), graded
  in ``test_cash_fold.py`` (``TestTheOpeningEquityIsTheSeed``,
  ``TestEveryAssertionIsReplayed``, ``TestTheDriftOracleWalksFiftyTwoPeriods``).
  It was ``balance_at._calculator``'s roll-forward, graded in
  ``test_balance_calculator.py``, until plan step X-g4b deleted both.

The rule under test: only rows the shared ``is_projected`` predicate admits
contribute; income counts at :func:`~app.services.cash_ledger.contribution_of`
and expense at the entries-aware reservation, and the split follows the
transaction TYPE.  A transfer's planned LEG takes a branch of its own: it is
gated on its PARENT's status, worth the transfer's resolved amount
(:func:`~app.services.cash_ledger.resolve_amount`) and filed by its
SIDE; a movement in flight is an item of its own too
(``TestTheReductionIsAdditiveOverRows``).

**The basis is a REQUIRED argument since plan step S1-c** (ruling R-DH (d)).
``sum_projected`` took one optional ``amount_overrides`` map defaulting to
``None``, and one optional argument is one way for a caller to hand this
reduction half a basis.  Every call below therefore states its
:class:`~app.services.cash_ledger.AmountBasis` explicitly.

**It was a ``ProjectedBasis`` carrying the account's clearing rule until plan
step X-f3b** (ruling **R-FM**), and the day that record stated was what decided
which bucket each purchase fell in.  A purchase's own ``settled_on`` decides it
now, so every fixture below says the bucket it means at the entry rather than
in a basis one screen away.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import status_seam, transaction_service
from app.services.cash_ledger import in_flight_movements, sum_projected
from app.services.transfer_legs import planned_transfer_legs
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_envelope_txn,
    create_savings_account,
    create_transfer,
    planted_basis,
)


_PURCHASED_ON = date(2026, 1, 20)
_ZERO = Decimal("0.00")

# The reduction's basis for every test that does not exercise the override
# seam: no live-recompute candidate, and a statement day EARLIER than every
# purchase below, so each one is OUTSTANDING unless its own test says
# otherwise.  Stating the day rather than leaving it ``None`` keeps this file's
# subject -- which rows are counted, and on which leg -- separate from
# ``test_cash_amounts.py``'s -- what one row is worth.
def _unreconciled(*rows):
    """The reduction's basis over *rows*, with no live producer and no override.

    Both live derivations are planted EMPTY
    (:class:`~tests._test_helpers.PlantedPricing`), so every row here is worth
    what its own rule says and nothing supersedes it.  A basis is keyed on an
    owner and a scenario rather than on a row set since plan step X-au-c2b; the
    rows are still named at each call site because they record what that test
    was valuing.
    """
    return planted_basis(*rows)


def _set_status(txn, status_enum):
    """Move *txn* to *status_enum* through the status seam, as every door does.

    Written against the enum rather than a ``Status`` row queried by NAME,
    which is the project's reference-table rule: ids drive logic, name strings
    are display only.  The suites this moved from queried
    ``Status.filter_by(name=...)`` directly, and this assigned ``status_id``
    past the seam; :func:`~app.services.status_seam.apply_status_change`
    reaches both statuses the cases below need.
    """
    status_seam.apply_status_change(txn, ref_cache.status_id(status_enum))


def _add_outstanding_purchases(db_session, seed_user, txn, entries):
    """Add *entries* to the envelope *txn*, each a purchase no statement has seen.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        txn: The envelope :class:`~app.models.transaction.Transaction`
            (``create_envelope_txn``'s) the purchases are made against.
        entries: An iterable of ``(amount, is_credit)`` pairs.  Every purchase
            is left with a NULL ``settled_on`` -- not yet seen on a statement,
            and so OUTSTANDING -- which is the shape these reduction tests were
            written on: which BUCKET a purchase falls in is
            ``test_cash_amounts.py``'s subject, not this file's.
    """
    for amount, is_credit in entries:
        add_entry(
            db_session, seed_user, txn, Decimal(amount), _PURCHASED_ON,
            is_credit=is_credit,
        )


class TestOnlyProjectedRowsContribute:
    """The status gate, and it is the SHARED predicate, not a local rule.

    ``sum_projected`` filters through
    :func:`app.utils.balance_predicates.is_projected`, the same definition the
    plan LOADER narrows on in SQL
    (:func:`~app.services.cash_ledger.planned_cash_rows`) and the entries-aware
    reduction re-applies -- so the loader and the reduction cannot disagree
    about which rows are in the plan.  A row that has settled, been cancelled,
    or carries the legacy Credit status is money that is no longer a
    RESERVATION: either it already moved (and the walk counts it) or it never
    will.

    **Only the SETTLED case has a firing control, and that is stated rather
    than left to be discovered.**  Measured: deleting the ``is_projected`` gate
    fails the settled test alone.  A Cancelled or Credit row is
    OVER-DETERMINED -- ``Transaction.effective_amount`` independently returns
    ``0`` for a status flagged ``excludes_from_balance``, so the gate and the
    valuation both zero it and no mutation of one can be seen through the
    other.  Those two tests pin a real user-visible property (a cancelled bill
    must not reduce a projected balance) with defence in depth behind it; they
    are NOT evidence that the gate works, and reading them as such is the N-69
    mistake.  They were equally over-determined in the suite they moved from.
    """

    def test_a_settled_row_contributes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled (DONE) row with entries is excluded: it is no longer a plan.

        Its $450.00 actual has already left the account, so counting it here
        would double it against the walk that carries it.  Reduction:
        (0.00, 0.00).  (Was: the balance stayed at the 5000.00 anchor.)
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries", Decimal("500.00"),
            )
            _add_outstanding_purchases(db.session, seed_user, txn, [("450.00", False)])
            # Through the real verb, which picks the entries branch itself and
            # records the ``purchases`` basis -- whose figure IS those entries
            # (plan step X-au-c3), so nothing is hand-written here.
            transaction_service.settle_transaction(txn)
            db.session.commit()

            assert sum_projected([txn], _unreconciled(txn)) == (_ZERO, _ZERO)

    def test_a_cancelled_row_contributes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled row with entries is excluded.

        Reduction: (0.00, 0.00).  (Was: the balance stayed at 5000.00.)
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries", Decimal("500.00"),
            )
            _add_outstanding_purchases(db.session, seed_user, txn, [("200.00", False)])
            _set_status(txn, StatusEnum.CANCELLED)
            db.session.commit()

            assert sum_projected([txn], _unreconciled(txn)) == (_ZERO, _ZERO)

    def test_a_credit_status_row_contributes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The legacy Credit status is excluded from the reduction.

        An entry-capable row should never reach Credit (OQ-10); this pins the
        legacy edge rather than endorsing it.  Reduction: (0.00, 0.00).
        (Was: the balance stayed at 5000.00.)
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries", Decimal("500.00"),
            )
            _add_outstanding_purchases(db.session, seed_user, txn, [("300.00", True)])
            _set_status(txn, StatusEnum.CREDIT)
            db.session.commit()

            assert sum_projected([txn], _unreconciled(txn)) == (_ZERO, _ZERO)


class TestTheTwoLegs:
    """Income counts at its amount; expense counts at its reservation."""

    def test_income_never_takes_the_entry_formula(
        self, app, db, seed_user, seed_periods,
    ):
        """An income row contributes ``effective_amount`` on the INCOME leg.

        The split follows the transaction TYPE, so the entries-aware
        reservation -- an EXPENSE rule -- cannot price it.  Reduction:
        (2000.00, 0.00).  (Was: 5000 + 2000 = 7000.)

        **The $500.00 credit entry is what gives this test teeth, and it is
        the only shape that can.**  The version this moved from asserted the
        same figure on an income row carrying NO entries, where both valuation
        rules return ``effective_amount`` and the assertion therefore held
        whichever one priced it -- finding N-69's shape.  Measured: routing the
        income leg through ``_entry_aware_amount`` failed nothing until the entry
        was added.  With it, the expense rule would answer
        max(2000.00 - 0 - 500.00, 0) = 1500.00 and this fails.

        An income row does not carry entries in production -- no write door
        creates one -- so this is a synthetic discriminator, exactly as plan
        step X-c1's ``is_income`` classification needed one.  It pins which
        RULE prices the row, and the ``0.00`` expense leg beside it pins which
        LEG the row lands on; those are two different mistakes and this
        catches both.
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[1], "Paycheck", "2000.00",
                is_income=True, category_key="Salary",
            )
            add_entry(
                db.session, seed_user, txn, Decimal("500.00"), _PURCHASED_ON,
                is_credit=True,
            )
            db.session.commit()

            assert sum_projected([txn], _unreconciled(txn)) == (Decimal("2000.00"), _ZERO)

    def test_every_loaded_entry_counts(self, app, db, seed_user, seed_periods):
        """The reduction sees every loaded entry, whatever date each carries.

        est=500 with debits of 200.00 (bought and posted Jan 5) and 250.00
        (Jan 20), read against a balance the user declared for Jan 31:
        settled_debit = 450.00, outstanding = 0, credit = 0, so
        max(500.00 - 450.00 - 0, 0) = 50.00.

        The dates span a range on purpose: this test carried an ``as_of``
        bound until plan step X-c2c1 deleted it (ruling R-M refused a future
        ``purchased_on`` at the write door instead), and what it pins now is
        that no bound remains -- BOTH purchases count, fifteen days apart,
        because what a row is worth is a function of the row and its account
        rather than of the reader's clock.

        It built its OWN basis until plan step X-f3b, carrying a
        reconciled-through day that covered both purchases, because the shared
        ``_unreconciled`` would otherwise have left both on the floor and
        answered 500.00.  Ruling **R-FM** moved the bucket onto each purchase's
        own ``settled_on`` -- which these two carry -- so the shared basis says
        the same thing and the special case is gone.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries",
                Decimal("500.00"),
            )
            for amount, day in (("200.00", 5), ("250.00", 20)):
                add_entry(
                    db.session, seed_user, txn, Decimal(amount),
                    date(2026, 1, day), settled_on=date(2026, 1, day),
                )
            db.session.commit()
            assert sum_projected(
                [txn], _unreconciled(txn),
            ) == (_ZERO, Decimal("50.00"))


class TestTheReductionIsAdditiveOverRows:
    """Each item is priced by its own rule, and the legs sum independently.

    The property the fold depends on structurally: ``sum_projected`` is
    additive over disjoint groups, so a period's days sum to the period's net
    exactly -- which is why ``_cash_fold._planned_day_nets`` may reduce per DAY
    and ``_budget_legs`` may reduce the SAME rows per pay period and get
    answers that reconcile to the cent.

    **The plan set holds a third kind since plan step ``balance:X-bi-4a``**
    (ruling **R-BAL77**): an un-dated purchase is a movement IN FLIGHT, an
    item of its own beside the envelope that now reserves only its UNSPENT
    budget.  The cases below hand the reduction the account's in-flight
    items with the rows (:func:`_with_in_flight`), and every figure is the
    one the three-bucket reservation produced through ``X-bi-3e``:
    ``max(E - P - C, U) == U + max(E - SUM(entries), 0)``.
    """

    @staticmethod
    def _with_in_flight(seed_user, *rows):
        """Return *rows* plus the account's movements in flight, as the plan loads them."""
        return [
            *rows,
            *in_flight_movements(seed_user["account"].id, seed_user["scenario"].id),
        ]

    def test_two_envelopes_each_take_their_own_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """Two tracked expenses in one period do not share a reservation.

        Groceries: est=500, uncleared_debit=200, credit=100 ->
        max(500 - 0 - 100, 200) = 400.
        Gas: est=80, uncleared_debit=60, credit=0 ->
        max(80 - 0 - 0, 60) = 80.
        Reduction: (0.00, 480.00).  (Was: 5000 - 480 = 4520.)
        """
        with app.app_context():
            groceries = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries", Decimal("500.00"),
            )
            _add_outstanding_purchases(
                db.session, seed_user, groceries, [("200.00", False), ("100.00", True)],
            )
            gas = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Gas", Decimal("80.00"),
            )
            _add_outstanding_purchases(db.session, seed_user, gas, [("60.00", False)])
            db.session.commit()

            assert sum_projected(
                self._with_in_flight(seed_user, groceries, gas),
                _unreconciled(groceries, gas),
            ) == (_ZERO, Decimal("480.00"))

    def test_a_row_without_entries_sums_beside_one_with_them(
        self, app, db, seed_user, seed_periods,
    ):
        """Tracked expense + plain expense + income, all in one reduction.

        Groceries: est=500, uncleared_debit=300, credit=100 ->
        max(500 - 0 - 100, 300) = 400.
        Rent (no template, no entries): 1200.00 at ``effective_amount``.
        Paycheck: 2000.00 on the income leg.
        Reduction: (2000.00, 1600.00).
        (Was: 5000 + 2000 - 400 - 1200 = 5400.)
        """
        with app.app_context():
            groceries = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries", Decimal("500.00"),
            )
            _add_outstanding_purchases(
                db.session, seed_user, groceries, [("300.00", False), ("100.00", True)],
            )
            rent = add_txn(
                db.session, seed_user, seed_periods[1], "Rent", "1200.00",
                category_key="Rent",
            )
            paycheck = add_txn(
                db.session, seed_user, seed_periods[1], "Paycheck", "2000.00",
                is_income=True, category_key="Salary",
            )
            db.session.commit()

            assert sum_projected(
                self._with_in_flight(seed_user, groceries, rent, paycheck),
                _unreconciled(groceries, rent, paycheck),
            ) == (Decimal("2000.00"), Decimal("1600.00"))

    def test_a_transfers_leaving_leg_is_priced_like_any_other_row(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer's leaving side is worth its amount, not a reservation rule.

        The side is the transfer's from-side LEG, loaded as the plan loads it
        (``planned_transfer_legs``).  The reduction prices a leg in its own
        branch: gated on the parent transfer's status, worth the transfer's
        resolved amount (``resolve_amount``), and filed by its side,
        here the expense leg.  Groceries: est=500, uncleared_debit=300, credit=0
        -> max(500 - 0 - 0, 300) = 500.  Leg: 200.00.
        Reduction: (0.00, 700.00).  (Was: 5000 - 500 - 200 = 4300.)

        It priced the transfer's expense SHADOW row until plan step
        ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 1): a transfer is one
        row and writes no shadow, and the plan holds its leaving side as a
        leg.  Only the leaving account's items are reduced here, so the
        figure is unchanged.
        """
        with app.app_context():
            groceries = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries", Decimal("500.00"),
            )
            _add_outstanding_purchases(db.session, seed_user, groceries, [("300.00", False)])
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session,
                from_account=seed_user["account"], to_account=savings,
                period=seed_periods[1], amount=Decimal("200.00"),
            )
            db.session.commit()
            [leg] = planned_transfer_legs(
                seed_user["account"].id, seed_user["scenario"].id, options=(),
            )
            assert (leg.transfer.id, leg.is_income) == (transfer.id, False)

            assert sum_projected(
                self._with_in_flight(seed_user, groceries, leg),
                _unreconciled(groceries, leg),
            ) == (_ZERO, Decimal("700.00"))
