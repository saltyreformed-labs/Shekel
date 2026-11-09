"""The cash ledger reconciles per linked account, per counter account and in the trial balance;
every settled transaction posts; scenarios and owners stay apart.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    TxnTypeEnum,
)
from app.extensions import db as _db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service, transaction_service
from app.services.ledger_lines import category_key
from app.utils.balance_predicates import settled_status_ids
from tests._ledger_reads import (
    account_net,
    family_movement_ids,
    ledger_net,
    period_nets_of_movements,
    trial_balance,
)
from tests._test_helpers import (
    create_account_of_type,
    create_envelope_txn,
    create_settled_cash_transaction,
    create_settled_transfer,
    figure_source_columns,
    one_off_row_of,
    settle_cash_row,
)

from ._helpers import (
    _independent_combined_source_effect,
    _signed_cash_effect,
    _assert_full_reconciliation,
)


def _assert_every_settled_transaction_posts(user_id: int) -> None:
    """Assert every settled, nonzero-effect cash transaction books >= 1 line.

    The per-transaction completeness backstop: a settled, non-deleted,
    non-transfer transaction with a NONZERO cash effect must book at least
    one worked-out line (no silent unposted row).  A zero-effect row (an
    envelope closed from card purchases alone, so its family holds no dated
    non-card movement) books nothing and is correctly NOT required to have a
    line.
    """
    settled = (
        _db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    for txn in settled:
        if _signed_cash_effect(txn) == 0:
            # A zero-effect settled row (all-credit envelope) books nothing.
            continue
        # The row's FAMILY (plan step X-bi-3a): a covered bill's money is
        # booked under its covering movement, not under the row.
        assert period_nets_of_movements(family_movement_ids(txn), txn.scenario_id), (
            f"settled transaction {txn.id} with a nonzero effect books no line"
        )


# ---------------------------------------------------------------------------
# 1. Per linked account: transfer + transaction legs combine and reconcile
# ---------------------------------------------------------------------------


class TestPerLinkedAccountReconciliation:
    """A linked account's ledger sums its transfer AND transaction legs."""

    @pytest.mark.server_clock
    def test_combined_transfer_and_cash_legs_reconcile_three_ways(
        self, app, db, seed_user,
    ):
        """Checking reconciles over $350 of transfers plus $1,950 of cash.

        Arithmetic (baseline scenario), all on Checking:
          - transfer Checking -> Savings   $100  -> Checking -100, Savings +100
          - transfer Checking -> Mortgage  $250  -> Checking -250, Mortgage +250
          - cash EXPENSE $50 Groceries           -> Checking  -50, Groceries +50
          - cash INCOME  $2000 Salary            -> Checking +2000, Salary  -2000

        Checking's combined effect = -100 -250 -50 +2000 = +1600.00, riding
        its $1000.00 Step-5 opening: ledger 2600.00.  Its transfer effect is
        -350 and its transaction effect is +1950, and -350 + 1950 = +1600.
        Savings 100 (opening) + 100 = 200.00; the Mortgage is amortizing (no
        account-walk opening, and it carries no LoanParams so no loan genesis
        either), leaving its ledger at the bare +250.00.  All three
        independent computations -- the hand-computed literal, the
        independent cross-table query, and the production service helpers --
        must agree on every account.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Combined Savings",
            )
            mortgage = create_account_of_type(
                seed_user, db.session, "Mortgage", "Combined Mortgage",
            )
            db.session.commit()

            create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("100.00"),
            )
            create_settled_transfer(
                seed_user, db.session, checking, mortgage, period,
                amount=Decimal("250.00"),
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("2000.00"),
                is_income=True, category=seed_user["categories"]["Salary"],
            )
            db.session.commit()

            expected = {
                checking.id: (Decimal("2600.00"), Decimal("1600.00")),
                savings.id: (Decimal("200.00"), Decimal("100.00")),
                mortgage.id: (Decimal("250.00"), Decimal("250.00")),
            }
            for account_id, (want_ledger, want_effect) in expected.items():
                # (a) hand-computed literal == the worked-out read.
                assert account_net(
                    _db.session, account_id, scenario_id,
                ) == want_ledger
                # (b) independent source query == the hand-computed effect.
                assert _independent_combined_source_effect(
                    account_id, scenario_id,
                ) == want_effect
                # (c) the production service helpers agree too.
                assert (
                    posting_service.settled_transfer_effect(
                        account_id, scenario_id,
                    )
                    + posting_service.posted_purchase_effect(
                        account_id, scenario_id,
                    )
                ) == want_effect

            # Checking's split is exactly transfers -350 + transactions +1950.
            assert posting_service.settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-350.00")
            assert posting_service.posted_purchase_effect(
                checking.id, scenario_id,
            ) == Decimal("1950.00")

            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# 2. Per counter account: category / fallback / orphan, by transaction_id link
# ---------------------------------------------------------------------------


class TestPerCounterAccountReconciliation:
    """Category, fallback, and orphan counter accounts reconcile by linkage."""

    def test_category_and_fallback_counter_accounts_reconcile(
        self, app, db, seed_user,
    ):
        """A categorized expense, a NULL-category expense, and income reconcile.

        Arithmetic (all on Checking):
          - cash EXPENSE $50 Groceries  -> Groceries-Expense counter +50.00
          - cash EXPENSE $30 (no cat)   -> Uncategorized-Expense fallback +30.00
          - cash INCOME  $2000 Salary   -> Salary-Income counter -2000.00

        The categorized expense books the Groceries-Expense category key; the
        uncategorized one books the per-(owner, class) Expense fallback bucket;
        the income books the Salary-Income key.  Each counter total is
        hand-checked, and the counter-account sweep reconciles every one by the
        ``transaction_id`` linkage.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            period = seed_user["bootstrap_period"]

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("30.00"),
                category=None,
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("2000.00"),
                is_income=True, category=seed_user["categories"]["Salary"],
            )
            db.session.commit()

            groceries_counter = category_key(
                seed_user["categories"]["Groceries"].id, LedgerAccountClassEnum.EXPENSE,
            )
            fallback_counter = category_key(
                None, LedgerAccountClassEnum.EXPENSE,
            )
            salary_counter = category_key(
                seed_user["categories"]["Salary"].id, LedgerAccountClassEnum.INCOME,
            )

            # Hand-computed counter totals (the negation of each cash leg).
            assert ledger_net(
                _db.session, groceries_counter, scenario_id,
            ) == Decimal("50.00")
            assert ledger_net(
                _db.session, fallback_counter, scenario_id,
            ) == Decimal("30.00")
            assert ledger_net(
                _db.session, salary_counter, scenario_id,
            ) == Decimal("-2000.00")

            _assert_full_reconciliation(scenario_id)

    def test_orphan_counter_account_reconciles_after_category_delete(
        self, app, db, seed_user,
    ):
        """A deleted category's former counter account reconciles by linkage.

        A $50 expense in a fresh "Leisure: Hobbies" category books its counter
        line on that category's key.  Deleting the budget category (the
        schema's ``ON DELETE SET NULL`` clears the transaction's
        ``category_id``) leaves the transaction reading ``category_id IS
        NULL``.  The read files the leg by the row's category, so it books on
        the Uncategorized bucket, which is the sweep's answer.  (The
        category-delete *route* archives a category still in use by a
        transaction; this raw-SQL delete reproduces the DB-level SET NULL
        directly.)

        **The one-off's DEFINITION is un-categorised first**, by the same
        raw SQL: every plan row names a definition since the family's cutover
        (plan step ``balance:X-bi-7d-2``), the definition references the
        category and ``transaction_templates.category_id`` is RESTRICT, so
        the delete below is refused while it does.  A category-less
        definition is a state the schema admits (nullable since
        ``9c1e4b7a2d3f``, ruling R-BAL24), and clearing it is what lets the
        DB-level SET NULL this case locks fire on the two tables it names.
        (A LEGACY link-less row needed no such step until that cutover.)
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            period = seed_user["bootstrap_period"]

            hobbies = Category(
                user_id=user_id, group_name="Leisure", item_name="Hobbies",
            )
            db.session.add(hobbies)
            db.session.flush()
            hobbies_id = hobbies.id

            txn = settle_cash_row(
                one_off_row_of(
                    period, name="Cash Txn", amount="50.00",
                    user_id=user_id, account_id=seed_user["account"].id,
                    scenario_id=scenario_id,
                    transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                    category_id=hobbies_id,
                ),
            )
            db.session.commit()
            txn_id = txn.id
            definition_id = txn.template_id
            assert ledger_net(
                db.session,
                category_key(hobbies_id, LedgerAccountClassEnum.EXPENSE),
                scenario_id,
            ) == Decimal("50.00")

            # Delete the budget category: the FK SET NULL clears the
            # transaction's category_id.
            # The definition's RESTRICT key would refuse the delete, so its
            # own category is cleared first (the docstring says why).
            db.session.execute(
                _db.text(
                    "UPDATE budget.transaction_templates SET category_id = NULL "
                    "WHERE id = :d"
                ),
                {"d": definition_id},
            )
            db.session.execute(
                _db.text("DELETE FROM budget.categories WHERE id = :c"),
                {"c": hobbies_id},
            )
            db.session.commit()
            db.session.expire_all()

            assert db.session.get(Transaction, txn_id).category_id is None

            # Worked out from the records no key names an orphan, so the row's
            # category reading NULL books its +50 on the Uncategorized bucket.
            assert ledger_net(
                db.session,
                category_key(None, LedgerAccountClassEnum.EXPENSE),
                scenario_id,
            ) == Decimal("50.00")
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# 3 + 4. Global trial balance
# ---------------------------------------------------------------------------


class TestPerEntryAndTrialBalance:
    """The whole worked-out ledger sums to zero."""

    def test_every_entry_balances_and_trial_balance_is_zero(
        self, app, db, seed_user,
    ):
        """A transfer plus a cash expense and income book balanced lines.

        Arithmetic: one $100 transfer (Checking -> Savings) books two balanced
        pairs via transit, one $50 Groceries expense and one $2000 Salary
        income each book one balanced pair -- and the Step-5 openings
        (Checking +1000/-1000, Savings +100/-100 against their equity twins)
        are balanced pairs too -- so the whole-ledger total stays 0.00.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Entry Savings",
            )
            db.session.commit()
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings, period,
                amount=Decimal("100.00"),
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("2000.00"),
                is_income=True, category=seed_user["categories"]["Salary"],
            )
            db.session.commit()

            assert trial_balance(db.session) == Decimal("0.00")


# ---------------------------------------------------------------------------
# 5. Per-transaction completeness (no settled cash row is silently unposted)
# ---------------------------------------------------------------------------


class TestEverySettledTransactionPosts:
    """Every settled, nonzero-effect cash transaction books at least one line."""

    def test_no_settled_transaction_is_silently_unposted(
        self, app, db, seed_user,
    ):
        """Two posted expenses post entries; a zero-effect envelope posts none.

        Two settled expenses ($50 Groceries, $40 Rent) each book a line; an
        all-credit envelope (closed from its one $75 card purchase, so its
        record is ``purchases`` and its family holds no dated non-card
        movement -- effect 0) posts nothing and is correctly NOT flagged as
        unposted.  The completeness sweep requires a line for every settled
        nonzero-effect row and excludes the zero-effect one.  Through
        ``X-bi-3e`` the zero-effect row was a typed $75 figure beside a $75
        card purchase, a shape ruling **R-BAL78** refuses at the door.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            period = seed_user["bootstrap_period"]

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("40.00"),
                category=seed_user["categories"]["Rent"],
            )
            # An all-credit envelope: settled from a card purchase, zero effect.
            all_credit = create_envelope_txn(
                seed_user, db.session, period, "All Credit", Decimal("75.00"),
            )
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=all_credit.id, account_id=all_credit.account_id, owner_id=all_credit.user_id, user_id=user_id,
                amount=Decimal("75.00"), description="cc purchase",
                purchased_on=period.start_date, is_credit=True,
            ))
            db.session.flush()
            transaction_service.settle_transaction(all_credit)
            db.session.commit()
            all_credit_id = all_credit.id

            _assert_every_settled_transaction_posts(user_id)
            # The zero-effect FAMILY books nothing (no silent spurious line).
            assert period_nets_of_movements(
                family_movement_ids(all_credit_id), scenario_id,
            ) == {}
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# 6. Multi-scenario and owner isolation
# ---------------------------------------------------------------------------


class TestMultiScenarioIsolation:
    """Postings in one scenario never reconcile against another scenario."""

    def test_cash_postings_are_isolated_per_scenario(
        self, app, db, seed_user,
    ):
        """A $100 baseline and a $70 what-if expense never bleed together.

        Arithmetic: a $100 Groceries expense in the baseline scenario and a $70
        Groceries expense in a separate what-if scenario, both on Checking --
        each scenario also carrying Checking's $1000.00 opening.  Scoped to
        baseline the Checking ledger is 1000 - 100 = 900.00 (NOT 830) and the
        Groceries-Expense counter +100.00; scoped to the what-if they are
        1000 - 70 = 930.00 and +70.00.  Each row's scenario keeps the two
        apart, and each scenario reconciles independently.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            baseline = seed_user["scenario"]
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            whatif = Scenario(
                user_id=user_id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("100.00"),
                category=seed_user["categories"]["Groceries"], scenario=baseline,
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("70.00"),
                category=seed_user["categories"]["Groceries"], scenario=whatif,
            )
            db.session.commit()

            # Checking: opening - 100 in baseline, opening - 70 in the
            # what-if -- never the cross-scenario 830.
            assert account_net(
                _db.session, checking.id, baseline.id,
            ) == Decimal("900.00")
            assert account_net(
                _db.session, checking.id, whatif.id,
            ) == Decimal("930.00")

            # The shared Groceries-Expense counter splits per scenario: +100 / +70.
            groceries_counter = category_key(
                seed_user["categories"]["Groceries"].id, LedgerAccountClassEnum.EXPENSE,
            )
            assert ledger_net(
                _db.session, groceries_counter, baseline.id,
            ) == Decimal("100.00")
            assert ledger_net(
                _db.session, groceries_counter, whatif.id,
            ) == Decimal("70.00")

            _assert_full_reconciliation(baseline.id)
            _assert_full_reconciliation(whatif.id)


class TestOwnerIsolationViaJournalEntry:
    """Owners never cross-contaminate."""

    def test_two_owners_reconcile_independently_and_posting_has_no_user_id(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two independent owners settle cash; neither sees the other's.

        Arithmetic: owner 1 settles a $100 Groceries expense on their Checking
        ($1000.00 opening); owner 2 settles a $200 Groceries expense on theirs
        ($2000.00 opening).  Owner 1's Checking ledger is 1000 - 100 = 900.00
        and owner 2's is 2000 - 200 = 1800.00 with no leakage, and each owner's
        books reconcile in their own baseline scenario.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            create_settled_cash_transaction(
                seed_second_user, db.session,
                seed_second_user["bootstrap_period"], Decimal("200.00"),
                category=seed_second_user["categories"]["Groceries"],
            )
            db.session.commit()

            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            checking1 = seed_user["account"].id
            checking2 = seed_second_user["account"].id
            # No leakage: each owner's Checking ledger holds only their own.
            assert account_net(
                _db.session, checking1, scenario1,
            ) == Decimal("900.00")
            assert account_net(
                _db.session, checking2, scenario2,
            ) == Decimal("1800.00")

            _assert_full_reconciliation(scenario1)
            _assert_full_reconciliation(scenario2)
