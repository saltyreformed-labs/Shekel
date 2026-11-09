"""A settled ordinary transaction's lines: the signs, the figure, an envelope's purchases, and a
revert or a re-categorise read back.
"""

from __future__ import annotations

from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, PostingKindEnum, StatusEnum
from app.extensions import db as _db
from app.services import status_seam
from app.services.ledger_lines import category_key, linked_key
from app.utils.dates import display_today
from tests._ledger_reads import account_net, ledger_net, scenario_lines
from tests._test_helpers import (
    add_txn,
    an_entered_day,
    create_envelope_txn,
    settlement_if_settling,
)
from app.services.settle_day import record_settle_day

from ._helpers import (
    _scenario_id,
    _period_nets_for_transaction,
    _add_txn_entry,
)


def _groceries(seed_user):
    """The Groceries expense category's key."""
    return category_key(
        seed_user["categories"]["Groceries"].id, LedgerAccountClassEnum.EXPENSE,
    )


class TestTransactionSettleBooksTwoLines:
    """A settled ordinary transaction books its account against its category."""

    def test_income_signs(self, app, db, seed_user):
        """A $2000 Received Salary income books +2000 cash / -2000 Salary.

        Arithmetic (plan Section 1, second worked example): income has no
        entries, so its money is its covering movement (2000) with the income
        sign; the cash line is +2000.00 (a debit: money entering Checking) and
        the category line is -2000.00 (a credit: income earned in Income:
        Salary).  The sign follows the transaction TYPE, never the account
        class, and both lines carry the income posting kind.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary",
            )
            _db.session.commit()

            assert _period_nets_for_transaction(txn.id, _scenario_id(seed_user)) == {
                period.id: {
                    linked_key(seed_user["account"]): Decimal("2000.00"),
                    category_key(
                        seed_user["categories"]["Salary"].id,
                        LedgerAccountClassEnum.INCOME,
                    ): Decimal("-2000.00"),
                },
            }
            [movement] = txn.covering_movements
            kinds = {
                line.posting_kind_id
                for line in scenario_lines(_db.session, _scenario_id(seed_user))
                if line.movement_id == movement.id
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.INCOME),
            }

    def test_expense_uses_effective_actual_not_estimated(
        self, app, db, seed_user,
    ):
        """A recorded ``actual_amount`` (not the estimate) is the cash line.

        Arithmetic: estimated $50 but settled actual $45, so the row's figure
        is 45 and the expense cash line is -45.00, NOT -50.00.  Locks that the
        read books the recorded figure (the value the balance seam and the
        oracle use), not the raw estimate.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_amount="45.00",
            )
            _db.session.commit()

            assert _period_nets_for_transaction(txn.id, _scenario_id(seed_user)) == {
                period.id: {
                    linked_key(seed_user["account"]): Decimal("-45.00"),
                    _groceries(seed_user): Decimal("45.00"),
                },
            }

    def test_envelope_posts_debit_only_effect(self, app, db, seed_user):
        """A settled envelope's DEBIT purchases book, each once dated; the close nothing.

        Arithmetic (plan Section 1 worked example, re-expressed under ruling
        **R-BAL77** at plan step ``balance:X-bi-4a``): a $200 Groceries
        envelope with entries $60 debit / $50 debit / $40 credit, marked Paid.
        The close books NOTHING -- an un-dated purchase is in flight, not the
        row's line.  Dating the two debit purchases books each: cash -60.00 /
        Groceries +60.00 and cash -50.00 / Groceries +50.00, -110.00 of debit
        spending over the family; the $40 credit purchase books nothing (its
        CC Payback books when it settles), so there is no double-count.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Groceries Env",
                Decimal("200.00"),
            )
            sixty = _add_txn_entry(seed_user, txn, "60.00", is_credit=False)
            fifty = _add_txn_entry(seed_user, txn, "50.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()

            # The close: nothing books, the purchases are in flight.
            assert _period_nets_for_transaction(
                txn.id, _scenario_id(seed_user),
            ) == {}

            record_settle_day(sixty, an_entered_day(display_today()))
            record_settle_day(fifty, an_entered_day(display_today()))
            _db.session.commit()
            assert _period_nets_for_transaction(txn.id, _scenario_id(seed_user)) == {
                period.id: {
                    linked_key(seed_user["account"]): Decimal("-110.00"),
                    _groceries(seed_user): Decimal("110.00"),
                },
            }


class TestEnvelopeCreditDominatesKeepsExpenseSign:
    """L10: a credit-dominated expense envelope stays an OUTFLOW -- no sign flip.

    **What guarantees it is that the DEBIT total is positive, and that is a
    narrower claim than this test used to make** (ruling **bank_import:R-II**,
    developer 2026-08-31).  It read: *two structural invariants forbid a flip --
    ``TransactionEntry.amount`` is ``CHECK (amount > 0)`` and a settled
    envelope's figure is the sum of ALL entries, so ``effective -
    Sigma(credit) = Sigma(debit) >= 0`` always*.  That CHECK is now
    ``amount <> 0``, so ``Sigma(debit) >= 0`` is no longer given and the old
    guarantee is genuinely gone.

    **It was not merely weakened, it stopped being desirable.**  An envelope
    whose refunds exceed its purchases really did net RECEIVE money, and
    booking that as a cash inflow is correct rather than a regression --
    :class:`TestEnvelopeRefundDominatesBooksAnInflow` is that case, asserted
    beside this one so the pair states the whole rule.  What survives here is
    the case this class was always about: a positive debit remainder, however
    small, books as an outflow, because the sign follows the transaction TYPE
    and never the relative size of the credit portion.
    """

    def test_credit_dominated_expense_still_posts_a_debit_outflow(
        self, app, db, seed_user
    ):
        """debit $1 / credit $99 -> the dated $1 books -1.00; the $99 card never.

        Arithmetic (ruling **R-BAL77**, plan step ``balance:X-bi-4a``): the
        close books nothing; dating the $1 debit purchase books it, and the
        expense sign makes the cash line -1.00 (a $1 checking outflow) and the
        Groceries line +1.00.  The $99 card purchase leaves through its CC
        Payback, so a credit-heavy split never turns the expense into an
        inflow.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Credit-Heavy Env",
                Decimal("200.00"),
            )
            one = _add_txn_entry(seed_user, txn, "1.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "99.00", is_credit=True)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()

            assert _period_nets_for_transaction(
                txn.id, _scenario_id(seed_user),
            ) == {}
            record_settle_day(one, an_entered_day(display_today()))
            _db.session.commit()

            # The $1 debit books as a proper outflow -- NOT a positive (inflow)
            # line, which a credits-exceed-effective sign flip would produce.
            assert _period_nets_for_transaction(txn.id, _scenario_id(seed_user)) == {
                period.id: {
                    linked_key(seed_user["account"]): Decimal("-1.00"),
                    _groceries(seed_user): Decimal("1.00"),
                },
            }


class TestEnvelopeRefundDominatesBooksAnInflow:
    """An envelope whose REFUNDS exceed its purchases books a cash INFLOW.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  The twin of
    :class:`TestEnvelopeCreditDominatesKeepsExpenseSign`, and the case that
    class's premise used to make unrepresentable.  A merchant credit files as a
    NEGATIVE purchase against the envelope its merchant rule names, so an
    envelope can genuinely net receive money -- and the ledger must say so.

    **It also pins WHY the figure can be negative at all**: a settle's record
    (``status_seam.Settlement``) refuses a negative figure, so no record could
    hold ``-49.00``.  An envelope settling from its purchases records NO figure
    of its own (``Settlement(None, None)``, no covering movement) and derives
    it from the entries at read time, which is exactly what lets a
    refund-dominated row be priced at all.  A future change that made such a
    row record a figure of its own would fail here rather than at a
    constructor with no test behind it.
    """

    def test_refund_dominated_envelope_books_a_positive_cash_leg(
        self, app, db, seed_user
    ):
        """debit $1.00 and a -$50.00 refund -> the family nets +49.00 cash, -49.00 category.

        Arithmetic (ruling **R-BAL77**): the row settles from its purchases
        and its figure is ``sum(entries) = 1.00 + (-50.00) = -49.00``; the
        close books nothing, and dating both purchases books each as its own
        movement -- the $1.00 as cash -1.00 / Groceries +1.00 and the refund
        as cash +50.00 / Groceries -50.00 -- so the family nets ``+49.00``
        into checking: money came back.  The Groceries net is ``-49.00``, a
        contra-expense, which is what a refund is.  The two still sum to zero.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Refund-Heavy Env",
                Decimal("200.00"),
            )
            one = _add_txn_entry(seed_user, txn, "1.00", is_credit=False)
            refund = _add_txn_entry(seed_user, txn, "-50.00", is_credit=False)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                ),
            )
            _db.session.commit()

            # The figure is DERIVED, never recorded -- see the class docstring.
            assert txn.covering_movements == []

            assert _period_nets_for_transaction(
                txn.id, _scenario_id(seed_user),
            ) == {}
            record_settle_day(one, an_entered_day(display_today()))
            record_settle_day(refund, an_entered_day(display_today()))
            _db.session.commit()

            assert _period_nets_for_transaction(txn.id, _scenario_id(seed_user)) == {
                period.id: {
                    linked_key(seed_user["account"]): Decimal("49.00"),
                    _groceries(seed_user): Decimal("-49.00"),
                },
            }


class TestTransactionReversal:
    """A revert takes the row's lines off; a re-categorise moves the category line."""

    def test_reverse_nets_to_zero(self, app, db, seed_user):
        """Reverting a settled transaction leaves both accounts where they began.

        Arithmetic: the settle books -100 (Checking) / +100 (Groceries), the
        covering movement's lines.  The revert un-dates the movement (ruling
        **R-BAL61**), so it books nothing: the category account nets to zero
        and the Checking net lands back on its $1000.00 opening.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            # The settle books its expense, so the act below has a line to move.
            assert ledger_net(
                _db.session, _groceries(seed_user), _scenario_id(seed_user),
            ) == Decimal("100.00")

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()

            assert ledger_net(
                _db.session, _groceries(seed_user), _scenario_id(seed_user),
            ) == Decimal("0.00")
            assert account_net(
                _db.session, seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")

    def test_revert_recategorize_resettle_posts_new_zeroes_old(
        self, app, db, seed_user,
    ):
        """Revert -> recategorize -> re-settle books NEW, leaves OLD at zero (2.8 CRITICAL).

        The scenario the per-site approach to the stored ledger got wrong.
        Arithmetic:

          1. Settle a $100 expense in category A (Groceries): cash -100, A +100.
          2. Revert through the seam (the movement is un-dated) and
             recategorize to B (Rent): nothing books.
          3. Re-settle through the seam on the day it first settled (the
             movement is re-dated; category now B): cash -100 / B +100.

        Final books: category A nets to **zero**, category B carries the
        +100.00 expense, and Checking's net sits on its $1000.00 opening (the
        period-start-dated -100 is before the origination assertion, which
        absorbs it).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            # The settle books its expense, so the act below has a line to move.
            assert ledger_net(
                _db.session, _groceries(seed_user), _scenario_id(seed_user),
            ) == Decimal("100.00")

            # 2. Revert, then recategorize to B.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            txn.category_id = seed_user["categories"]["Rent"].id
            _db.session.commit()

            # 3. Re-settle with the new category, on the day it first settled
            #    (period start, pre-assertion) so the absorption arithmetic
            #    above still holds; the seam re-dates the kept movement.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_entered_day(period.start_date),
                settlement=settlement_if_settling(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                ),
            )
            _db.session.commit()

            assert ledger_net(
                _db.session, _groceries(seed_user), _scenario_id(seed_user),
            ) == Decimal("0.00")
            assert ledger_net(
                _db.session,
                category_key(
                    seed_user["categories"]["Rent"].id, LedgerAccountClassEnum.EXPENSE,
                ),
                _scenario_id(seed_user),
            ) == Decimal("100.00")
            assert account_net(
                _db.session, seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")

    def test_recategorize_while_settled_posts_cashless_reclassification(
        self, app, db, seed_user,
    ):
        """Recategorizing a still-settled transaction moves the category line only.

        Arithmetic: settle a $100 expense in A (Groceries): cash -100, A +100.
        Then recategorize to B (Rent) while STILL settled (no revert).  The
        amount is unchanged, so the cash line is unchanged; the category line
        moves: A nets to zero, B carries the +100 expense, and Checking's net
        stays on its $1000.00 opening (the pre-assertion -100 absorbed by the
        opening).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            # The settle books its expense, so the act below has a line to move.
            assert ledger_net(
                _db.session, _groceries(seed_user), _scenario_id(seed_user),
            ) == Decimal("100.00")

            # Recategorize WITHOUT reverting.
            txn.category_id = seed_user["categories"]["Rent"].id
            _db.session.commit()

            assert ledger_net(
                _db.session, _groceries(seed_user), _scenario_id(seed_user),
            ) == Decimal("0.00")
            assert ledger_net(
                _db.session,
                category_key(
                    seed_user["categories"]["Rent"].id, LedgerAccountClassEnum.EXPENSE,
                ),
                _scenario_id(seed_user),
            ) == Decimal("100.00")
            assert account_net(
                _db.session, seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")
