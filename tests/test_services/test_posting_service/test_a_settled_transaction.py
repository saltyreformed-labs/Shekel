"""A settled ordinary transaction's one balanced entry: the envelope's signs, a repeat sync, a
reversal, the counter leg's routing and the entry's day.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import Posting
from app.services import posting_service, status_seam
from app.utils.dates import display_today
from tests._test_helpers import (
    add_txn,
    an_entered_day,
    create_envelope_txn,
    settlement_if_settling,
)
from app.services.settle_day import record_settle_day

from ._helpers import (
    _ledger_id,
    _legs_by_ledger,
    _scenario_id,
    _entries_for_transaction,
    _period_nets,
    _ledger_total,
    _add_txn_entry,
    _resolve_category_ledger,
)


# ===========================================================================
# Build-Order Step 3: ordinary cash transaction postings
# ===========================================================================


class TestTransactionSettlePostsBalancedEntry:
    """A settled ordinary transaction posts one balanced two-leg entry."""

    def test_plain_expense_signs_balance_and_metadata(
        self, app, db, seed_user,
    ):
        """A $50 Paid Groceries expense posts -50 / +50, summing to zero.

        Arithmetic (plan Section 1): a plain expense's money is its ONE
        covering movement (plan step ``balance:X-bi-4a``, ruling **R-BAL80**:
        the row itself posts nothing), worth ``50`` with the expense sign; the
        cash leg is -50.00 (a credit: money leaving Checking) and the category
        leg is +50.00 (a debit: the expense lands in Food: Groceries).  -50.00
        + 50.00 = 0.00.  Also pins the header metadata (the PURCHASE source
        kind, the movement link, owner / scenario / period) and the per-leg
        posting kind (expense).  It pinned a TRANSACTION-sourced entry linked
        by ``transaction_id`` through ``X-bi-3e``.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # Header metadata: the movement's link, the parent's owner / scenario
            # / period.
            [movement] = txn.covering_movements
            assert entry.transaction_entry_id == movement.id
            assert entry.transaction_id is None
            assert entry.transfer_id is None
            assert entry.user_id == seed_user["user"].id
            assert entry.scenario_id == _scenario_id(seed_user)
            assert entry.pay_period_id == period.id
            assert entry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.PURCHASE,
            )
            assert entry.description == "Groceries"
            assert isinstance(entry.entry_date, date)
            # Legs: -50 from Checking, +50 to Groceries, summing to zero.
            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("-50.00")
            assert legs[groceries_ledger] == Decimal("50.00")
            assert sum(legs.values()) == Decimal("0.00")
            # Every leg carries the expense posting kind.
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .all()
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            }
            assert len(_entries_for_transaction(txn.id)) == 1

    def test_income_signs(self, app, db, seed_user):
        """A $2000 Received Salary income posts +2000 / -2000.

        Arithmetic (plan Section 1, second worked example): income has no
        entries, so the effect is ``effective_amount`` (2000) with the income
        sign; the cash leg is +2000.00 (a debit: money entering Checking) and
        the category leg is -2000.00 (a credit: income earned in Income:
        Salary).  The sign follows the transaction TYPE, never the account
        class.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            salary_ledger = _resolve_category_ledger(
                seed_user, "Salary", LedgerAccountClassEnum.INCOME,
            ).id

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("2000.00")
            assert legs[salary_ledger] == Decimal("-2000.00")
            assert sum(legs.values()) == Decimal("0.00")
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .all()
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.INCOME),
            }

    def test_expense_uses_effective_actual_not_estimated(
        self, app, db, seed_user,
    ):
        """A manual ``actual_amount`` (not the estimate) drives the cash leg.

        Arithmetic: estimated $50 but settled actual $45, so
        ``effective_amount`` is 45 (``actual_amount`` overrides), and the
        expense cash leg is -45.00, NOT -50.00.  Locks that the service reads
        the ``effective_amount`` property (the value the balance calculator
        and the oracle use), not the raw estimate.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_amount="45.00",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("-45.00")
            assert legs[groceries_ledger] == Decimal("45.00")

    def test_envelope_posts_debit_only_effect(self, app, db, seed_user):
        """A settled envelope's DEBIT purchases post, each on its own day; the close nothing.

        Arithmetic (plan Section 1 worked example, re-expressed under ruling
        **R-BAL77** at plan step ``balance:X-bi-4a``): a $200 Groceries
        envelope with entries $60 debit / $50 debit / $40 credit, marked Paid.
        The close books NOTHING -- an un-dated purchase is in flight, not the
        row's leg (through ``X-bi-3e`` the close booked the $110.00 remainder
        on the close day).  Dating the two debit purchases posts each: cash
        -60.00 / Groceries +60.00 and cash -50.00 / Groceries +50.00, -110.00
        of debit spending over the family; the $40 credit purchase posts
        nothing (its CC Payback posts when it settles), so there is no
        double-count.
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
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            # The close: nothing to post, the purchases are in flight.
            assert posting_service.sync_transaction_postings(txn) == []

            record_settle_day(sixty, an_entered_day(display_today()))
            record_settle_day(fifty, an_entered_day(display_today()))
            entries = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert len(entries) == 2
            [(_, nets)] = _period_nets(entries).items()
            assert nets[cash_ledger] == Decimal("-110.00")
            assert nets[groceries_ledger] == Decimal("110.00")
            assert sum(nets.values()) == Decimal("0.00")


class TestTransactionAllCreditNoop:
    """An all-credit envelope has zero debit effect and posts nothing."""

    def test_all_credit_envelope_posts_nothing(self, app, db, seed_user):
        """An envelope whose only entry is a credit purchase posts no entry.

        Arithmetic: a single $40 credit entry, ``actual_amount`` = 40, so
        ``effect = effective(40) - credit_sum(40) = 0``.  The target is
        ``{cash: 0, category: 0}``, nothing is posted yet, so every delta is
        zero and the sync is a no-op (returns []) -- the entire $40 flows
        through the separate CC Payback instead.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "All Credit Env",
                Decimal("200.00"),
            )
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()

            result = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert result == []
            assert _entries_for_transaction(txn.id) == []


class TestEnvelopeCreditDominatesKeepsExpenseSign:
    """L10: a credit-dominated expense envelope stays an OUTFLOW -- no sign flip.

    The confirmed cash effect is ``effective - Sigma(credit entries)``.  This
    pins that even when the credit portion dwarfs the debit, the tiny debit
    still books as a proper outflow and never as income.

    **What guarantees it is that the DEBIT total is positive, and that is a
    narrower claim than this test used to make** (ruling **bank_import:R-II**,
    developer 2026-08-31).  It read: *two structural invariants forbid a flip --
    ``TransactionEntry.amount`` is ``CHECK (amount > 0)`` and a settled
    envelope's figure is the sum of ALL entries, so ``effective -
    Sigma(credit) = Sigma(debit) >= 0`` always* -- and named its own dependency,
    that a change which *relaxed the positive-amount CHECK would fail here*.
    That CHECK is now ``amount <> 0``, so ``Sigma(debit) >= 0`` is no longer
    given and the old guarantee is genuinely gone.

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
        """debit $1 / credit $99 -> the dated $1 posts -1.00; the $99 card never.

        Arithmetic (ruling **R-BAL77**, plan step ``balance:X-bi-4a``): the
        close posts nothing; dating the $1 debit purchase posts it, and the
        expense sign makes the cash leg -1.00 (a $1 checking outflow) and the
        Groceries leg +1.00.  The $99 card purchase leaves through its CC
        Payback, so a credit-heavy split never turns the expense into an
        inflow.  (Through ``X-bi-3e`` the close booked ``effective(100) -
        credit_sum(99) = 1`` as the row's own leg.)
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
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            assert posting_service.sync_transaction_postings(txn) == []
            record_settle_day(one, an_entered_day(display_today()))
            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            # The $1 debit books as a proper outflow -- NOT a positive (inflow)
            # leg, which a credits-exceed-effective sign flip would produce.
            assert legs[cash_ledger] == Decimal("-1.00")
            assert legs[cash_ledger] < Decimal("0.00")
            assert legs[groceries_ledger] == Decimal("1.00")
            assert sum(legs.values()) == Decimal("0.00")


class TestEnvelopeRefundDominatesBooksAnInflow:
    """An envelope whose REFUNDS exceed its purchases books a cash INFLOW.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  The twin of
    :class:`TestEnvelopeCreditDominatesKeepsExpenseSign`, and the case that
    class's premise used to make unrepresentable.  A merchant credit files as a
    NEGATIVE purchase against the envelope its merchant rule names, so an
    envelope can genuinely net receive money -- and the ledger must say so.

    **It also pins WHY the figure can be negative at all**, which is the part a
    reader would otherwise have to rediscover: a settle's record
    (``status_seam.Settlement``) refuses a negative figure, and the row's own
    ``settled_amount`` carried ``ck_transactions_settled_amount`` (``>= 0``)
    through plan step ``balance:X-bi-4b-1``, so no record could hold
    ``-49.00``.  An envelope settling from its purchases records NO figure of
    its own (``Settlement(None, None)``, no covering movement) and derives it
    from the entries at read time, which is exactly what lets a refund-dominated
    row be priced at all.  A future change that made such a row record a
    figure of its own would fail here rather than at a constructor with no
    test behind it.
    """

    def test_refund_dominated_envelope_books_a_positive_cash_leg(
        self, app, db, seed_user
    ):
        """debit $1.00 and a -$50.00 refund -> the family nets +49.00 cash, -49.00 category.

        Arithmetic (ruling **R-BAL77**, plan step ``balance:X-bi-4a``): the
        row settles from its purchases and its figure is ``sum(entries) =
        1.00 + (-50.00) = -49.00``; the close posts nothing, and dating both
        purchases posts each as its own movement -- the $1.00 as cash -1.00 /
        Groceries +1.00 and the refund as cash +50.00 / Groceries -50.00 --
        so the family nets ``+49.00`` into checking: money came back.  The
        Groceries net is ``-49.00``, a contra-expense, which is what a refund
        is.  The two still sum to zero.  (Through ``X-bi-3e`` the close booked
        the whole ``-49.00`` as the row's own leg.)
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

            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            assert posting_service.sync_transaction_postings(txn) == []
            record_settle_day(one, an_entered_day(display_today()))
            record_settle_day(refund, an_entered_day(display_today()))
            entries = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert len(entries) == 2
            [(_, legs)] = _period_nets(entries).items()
            assert legs[cash_ledger] == Decimal("49.00")
            assert legs[cash_ledger] > Decimal("0.00")
            assert legs[groceries_ledger] == Decimal("-49.00")
            assert sum(legs.values()) == Decimal("0.00")


class TestTransactionIdempotency:
    """A repeat sync at the same target writes nothing."""

    def test_repeat_settle_is_noop(self, app, db, seed_user):
        """Re-syncing an already-posted settle returns [], no 2nd entry.

        Arithmetic: the first settle posts -50 / +50; the second sync sees
        current == target (zero deltas on every account and period) and writes nothing.  This
        is the double-mark-done guard -- a repeated settle never double-posts.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            first = posting_service.sync_transaction_postings(txn)
            second = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert len(first) == 1
            assert second == []
            assert len(_entries_for_transaction(txn.id)) == 1


class TestTransactionReversal:
    """A reversal reads the posted amount back from the ledger."""

    def test_reverse_nets_to_zero(self, app, db, seed_user):
        """Reverting a settled transaction nets both ledgers back to baseline.

        Arithmetic: settle posts -100 (Checking) / +100 (Groceries), the
        covering movement's leg.  The revert -- the seam un-dates the movement
        (ruling **R-BAL61**), and the reconcile then reverses exactly what is
        posted: +100 (Checking) / -100 (Groceries).  The category account
        nets to zero, the Checking total lands back on its $1000.00 anchor
        (the period-start settle was pre-assertion-absorbed and the self-heal
        re-based the opening at each step), and two family-linked entries
        survive (append-only correction, never an edit).  Through ``X-bi-3e``
        the reconcile took a ``settled=False`` flag for the revert; the row's
        own target is empty on every call now (ruling **R-BAL80**).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            [reversal] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")
            assert len(_entries_for_transaction(txn.id)) == 2

    def test_revert_recategorize_resettle_posts_new_zeroes_old(
        self, app, db, seed_user,
    ):
        """Revert -> recategorize -> re-settle posts to NEW, zeroes OLD (2.8 CRITICAL).

        The exact scenario the per-site approach got wrong.  Arithmetic:

          1. Settle a $100 expense in category A (Groceries): cash -100, A +100.
          2. Revert through the seam (the movement is un-dated), recategorize
             to B (Rent) and reconcile (the single-PATCH revert): the reversal
             reads the LEDGER (category A), not the now-B ``category_id``, so
             it posts +100 cash / -100 A.  A nets to zero.
          3. Re-settle through the seam (the movement is re-dated; category
             now B) and reconcile: cash -100 / +100 B.

        Final books: category A nets to **zero**, category B carries the
        +100.00 expense, and Checking's total sits on its $1000.00 anchor
        (the period-start-dated -100 is pre-assertion-absorbed: the self-heal
        re-based the opening to +1100).  Reading the posted side from the
        ledger -- never from ``txn.category_id`` -- is what makes the
        per-category books correct.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            a_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            # 1. Settle in category A.
            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # 2. Revert, recategorize to B, then reconcile.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            txn.category_id = seed_user["categories"]["Rent"].id
            posting_service.sync_transaction_postings(txn)
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
            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            b_ledger = _resolve_category_ledger(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            ).id
            assert _ledger_total(a_ledger) == Decimal("0.00")
            assert _ledger_total(b_ledger) == Decimal("100.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")
            # settle + revert + re-settle = three append-only entries.
            assert len(_entries_for_transaction(txn.id)) == 3

    def test_recategorize_while_settled_posts_cashless_reclassification(
        self, app, db, seed_user,
    ):
        """Recategorizing a still-settled transaction moves the category leg only.

        Arithmetic: settle a $100 expense in A (Groceries): cash -100, A +100.
        Then recategorize to B (Rent) and re-sync while STILL settled
        (``settled=True``, no revert).  The amount is unchanged, so the cash
        delta is zero and drops out; the entry carries TWO category legs and NO
        cash leg (A -100 / B +100).  This exercises the union reconcile's
        balanced-by-construction property in the cash-leg-absent case: the
        non-zero deltas still sum to zero and still yield >= 2 legs.  Net books:
        A nets to zero, B carries the +100 expense, Checking's total stays on
        its $1000.00 anchor (the pre-assertion -100 absorbed by the opening).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            a_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id
            cash_ledger = _ledger_id(seed_user["account"])

            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # Recategorize WITHOUT reverting; re-sync while still settled.
            txn.category_id = seed_user["categories"]["Rent"].id
            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            b_ledger = _resolve_category_ledger(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            ).id
            # Two category legs, no cash leg, summing to zero.
            legs = _legs_by_ledger(entry.id)
            assert legs == {
                a_ledger: Decimal("-100.00"),
                b_ledger: Decimal("100.00"),
            }
            assert cash_ledger not in legs
            assert sum(legs.values()) == Decimal("0.00")
            # Net books: A zero, B carries the expense, cash on its anchor.
            assert _ledger_total(a_ledger) == Decimal("0.00")
            assert _ledger_total(b_ledger) == Decimal("100.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")


class TestTransactionCounterLegRouting:
    """The counter leg lands in the right category / fallback account."""

    def test_categorized_expense_lands_in_category_ledger(
        self, app, db, seed_user,
    ):
        """A categorized expense books its counter leg into the category row.

        The non-cash leg lands in the (owner, Groceries, Expense) ledger
        account -- a category row (``is_owner_bucket`` False, ``category_id`` set),
        NOT a real-account link.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            groceries = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            cash_ledger = _ledger_id(seed_user["account"])
            legs = _legs_by_ledger(entry.id)
            non_cash = [lid for lid in legs if lid != cash_ledger]
            assert non_cash == [groceries.id]
            assert groceries.is_owner_bucket is False
            assert groceries.account_id is None
            assert groceries.category_id == seed_user["categories"][
                "Groceries"
            ].id

    def test_uncategorized_expense_lands_in_fallback(
        self, app, db, seed_user,
    ):
        """A NULL-category expense books its counter leg into the fallback.

        The non-cash leg lands in the per-(owner, Expense) Uncategorized
        fallback (``is_owner_bucket`` True, ``category_id`` NULL), the catch-all
        for a settled transaction whose category is NULL.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Misc", "50.00",
                status_enum=StatusEnum.DONE, category_key=None,
            )
            _db.session.commit()

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            fallback = _resolve_category_ledger(
                seed_user, None, LedgerAccountClassEnum.EXPENSE,
            )
            cash_ledger = _ledger_id(seed_user["account"])
            legs = _legs_by_ledger(entry.id)
            non_cash = [lid for lid in legs if lid != cash_ledger]
            assert non_cash == [fallback.id]
            assert fallback.is_owner_bucket is True
            assert fallback.category_id is None
            assert fallback.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.EXPENSE,
            )


class TestTransactionEntryDate:
    """``entry_date`` is the movement's recorded settle DAY, the row's mirrored.

    A sibling case, ``test_entry_date_falls_back_to_period_start_when_paid_at_``
    ``null``, was DELETED at plan step X-f1 rather than re-derived: it built a
    row whose settle day equalled its pay period's start, which is the exact
    value the deleted fallback produced, so the two rules coincide on it and
    nothing it asserted could tell them apart.  Measured by a neutral review --
    replacing the row's entry-date reader with the old ``return
    txn.pay_period.start_date`` left it PASSING while the case below failed.
    Since plan step ``balance:X-bi-4a`` the writer reads no row day at all:
    the posted entry is the covering movement's, dated by ITS ``settled_on``.
    """

    def test_entry_date_is_the_rows_recorded_settle_day(
        self, app, db, seed_user,
    ):
        """A settled transaction's postings are dated by its recorded settle day.

        The transaction twin of ``test_a_settled_transfer.py``'s
        ``TestSyncSettleEntryDate::test_entry_date_is_the_rows_recorded_settle_day``;
        see that docstring for how the rule moved from a UTC derivation (pre-R-DH (b)) to a display-zone
        one and then to a stored fact (plan step X-f1).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_on=date(2026, 5, 9),
            )
            _db.session.commit()

            # The movement's day IS the row's (the seam mirrors it), and the
            # movement is what posts (ruling **R-BAL80**).
            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert entry.entry_date == date(2026, 5, 9)
