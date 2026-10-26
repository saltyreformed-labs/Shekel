"""The emergency fund's coverage: the committed monthly expense floor it is measured against."""

from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import obligations_aggregator
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    cover_bare_settled_row,
    make_cadence_rule,
    one_off_row_of,
    settle_day_columns,
    transient_cadence_rule,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, EVERY_N_PERIODS, MONTHLY, ANNUAL

from ._helpers import _create_savings_account


def _create_expense_template(seed_user, cadence, amount, name="Test Expense",
                             is_active=True, interval_n=1):
    """Create an expense template on the seed user's checking account.

    **It takes the CADENCE rather than a pre-built rule, since plan step
    R-F6**, and authors it here: a rule carries its owning template's FK, so it
    cannot exist before the template does.  The two calls a caller used to make
    -- build the rule, then pass it in -- collapse into this one, and the
    separate ``_create_recurrence_rule`` helper is deleted.

    Args:
        seed_user: The seed user fixture dict.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence constant, or
            ``None`` for a template that does not repeat (plan step R2e-3's
            shape).
        amount: Decimal default amount.
        name: Template display name.
        is_active: Whether the template is active (default True).
        interval_n: Interval for the every-N-paychecks cadence (default 1).

    Returns:
        TransactionTemplate: the new template, flushed for id assignment.
    """
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=amount,
        is_active=is_active,
    )
    db.session.add(tmpl)
    db.session.flush()
    if cadence is not None:
        make_cadence_rule(tmpl, cadence, interval_n=interval_n)
    return tmpl


def _create_test_transfer_template(seed_user, to_account, cadence, amount,
                                   name="Test Transfer", is_active=True):
    """Create a transfer template from checking to another account.

    Takes the CADENCE rather than a pre-built rule, for the reason
    :func:`_create_expense_template` gives.

    Args:
        seed_user: The seed user fixture dict (checking is the source).
        to_account: Destination Account object.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence constant.
        amount: Decimal default amount.
        name: Template display name.
        is_active: Whether the template is active (default True).

    Returns:
        TransferTemplate: the new template, flushed for id assignment.
    """
    tmpl = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name=name,
        default_amount=amount,
        is_active=is_active,
    )
    db.session.add(tmpl)
    db.session.flush()
    make_cadence_rule(tmpl, cadence)
    return tmpl


# ── Emergency Fund Committed Baseline Tests ──────────────────────────


class TestEmergencyFundCommittedBaseline:
    """Tests for the committed monthly expense floor in emergency fund coverage.

    The emergency fund calculation uses the higher of:
    - Historical actual average expenses (from settled transactions)
    - Committed baseline (from active recurring templates)

    This ensures newly created recurring obligations are immediately
    reflected without waiting for settlement history to accumulate.
    """

    def test_emergency_fund_includes_transfer_templates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Transfer templates debiting checking are included in the
        committed monthly baseline.  A $1,500 every-period transfer
        produces committed = $1,500 * 26/12 = $3,250/month.
        """
        with app.app_context():
            # Savings account so emergency fund section renders.
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Transfer template: checking -> savings, every period.
            _create_test_transfer_template(
                seed_user, savings, EVERY_PERIOD, Decimal("1500.00"),
                name="Mortgage Payment",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # committed = 1500 * 26/12 = 3250
            # Template shows "$3,250/mo avg expenses".
            assert "$3,250/mo" in html, (
                "Expected $3,250/mo from committed transfer baseline, "
                f"but not found in HTML"
            )

    def test_emergency_fund_uses_higher_of_actual_or_committed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """When committed monthly exceeds historical average, the
        committed value is used.  Small settled history ($10/period)
        should be overridden by the $3,250/month committed baseline.
        """
        with app.app_context():
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Create small settled expenses across 6 recent periods.
            settled_id = ref_cache.status_id(StatusEnum.DONE)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            category_id = seed_user["categories"]["Rent"].id

            for period in seed_periods[1:7]:
                txn = one_off_row_of(
                    period,
                    name="Small Expense",
                    amount=Decimal("10.00"),
                    user_id=period.user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type_id,
                    category_id=category_id,
                )
                txn.status_id = settled_id
                # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
                # record is the covering movement written after the flush (X-bi-4b-2).
                for _column, _value in settle_day_columns(period.start_date).items():
                    setattr(txn, _column, _value)
                db.session.flush()
                # The record's home is the covering movement (X-bi-4b-1).
                cover_bare_settled_row(db.session, txn, "10.00")

            # Transfer template with higher committed amount.
            _create_test_transfer_template(
                seed_user, savings, EVERY_PERIOD, Decimal("1500.00"),
                name="Mortgage Payment",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Historical avg ~= $21.67/mo, committed = $3,250/mo.
            # max() picks $3,250.
            assert "$3,250/mo" in html, (
                "Expected committed baseline ($3,250) to override "
                "small historical average"
            )

    def test_emergency_fund_historical_excludes_non_checking_expenses(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The historical operand counts only checking-account expenses.

        DH-#29: ``_recent_settled_expenses_monthly`` is scoped to the
        same checking accounts as the committed floor, so a settled
        expense on a NON-checking account (here a Savings account) no
        longer inflates the emergency-fund denominator.  The floor is $0
        -- each row's definition is rule-less, and a definition with no
        rule commits nothing (``obligations_aggregator._committed_amount``)
        -- so the displayed average is driven entirely by the historical
        operand, which must reflect only the $120/period checking
        expenses, not the $300/period Savings ones.  Today is frozen to
        2026-03-20 (autouse fixture), so the current period is
        seed_periods[5] and the recent-6 window is
        seed_periods[0:6]; seeding every window period the same amount
        makes the monthly average independent of the exact window.
        """
        with app.app_context():
            # Savings account so the emergency fund section renders and
            # supplies a non-checking account to spend from.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            settled_id = ref_cache.status_id(StatusEnum.DONE)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            category_id = seed_user["categories"]["Rent"].id

            # $120 on CHECKING and $300 on the non-checking SAVINGS
            # account in each of the 6 recent (window) periods.  Each
            # row's definition is rule-less and so commits nothing to
            # the floor ($0); the historical operand alone drives the
            # displayed average.
            for period in seed_periods[0:6]:
                row = one_off_row_of(
                    period,
                    name="Checking Expense",
                    amount=Decimal("120.00"),
                    user_id=period.user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type_id,
                    category_id=category_id,
                )
                row.status_id = settled_id
                # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
                # record is the covering movement written after the flush (X-bi-4b-2).
                for _column, _value in settle_day_columns(period.start_date).items():
                    setattr(row, _column, _value)
                db.session.flush()
                # The record's home is the covering movement (X-bi-4b-1).
                cover_bare_settled_row(db.session, row, "120.00")
                row = one_off_row_of(
                    period,
                    name="Savings Expense",
                    amount=Decimal("300.00"),
                    user_id=period.user_id,
                    account_id=savings.id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type_id,
                    category_id=category_id,
                )
                row.status_id = settled_id
                # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
                # record is the covering movement written after the flush (X-bi-4b-2).
                for _column, _value in settle_day_columns(period.start_date).items():
                    setattr(row, _column, _value)
                db.session.flush()
                cover_bare_settled_row(db.session, row, "300.00")
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Checking-only: $120/period * 26/12 = $260/mo.
            assert "$260/mo avg expenses" in html, (
                "Expected the checking-only historical average "
                "($260/mo); non-checking (Savings) expenses must be "
                "excluded from the emergency-fund denominator"
            )
            # Pre-fix (all accounts): ($120 + $300)/period * 26/12 =
            # $910/mo -- must NOT appear now that Savings is excluded.
            assert "$910/mo" not in html, (
                "Non-checking Savings expenses must not inflate the "
                "emergency-fund average (would read $910/mo if counted)"
            )

    def test_emergency_fund_with_no_history_uses_committed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With zero settled transactions, the committed baseline from
        active templates is used instead of the historical $0 average.
        """
        with app.app_context():
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Monthly expense template = $2,000/month.
            _create_expense_template(
                seed_user, MONTHLY, Decimal("2000.00"),
                name="Monthly Bills",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # committed = $2,000 (monthly, no conversion needed).
            assert "$2,000/mo" in html, (
                "Expected $2,000/mo from committed monthly baseline"
            )

    def test_emergency_fund_no_templates_no_history(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With no templates and no settled transactions, avg_monthly_expenses
        stays at $0 and coverage metrics show zero.
        """
        with app.app_context():
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Section renders (savings > 0) but no expense info.
            assert "Emergency fund coverage" in html
            assert "avg expenses" not in html

    def test_emergency_fund_monthly_template_contribution(
        self, app, seed_user,
    ):
        """A monthly template contributes its exact default_amount as the
        monthly equivalent -- NOT multiplied by 26/12.
        """
        with app.app_context():
            tmpl = _create_expense_template(
                seed_user, MONTHLY, Decimal("500.00"),
                name="Monthly Subscription",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], BalanceContext.build(seed_user["user"].id),
            )
            assert result == Decimal("500.00"), (
                f"Monthly template should contribute exactly $500, got {result}"
            )

    def test_emergency_fund_excludes_non_repeating_templates(
        self, app, seed_user,
    ):
        """Non-repeating templates do not contribute to committed monthly.

        Only the recurring every-period template should be counted.  The
        non-repeating one carried a ``Once`` PATTERN until plan step R2e-3;
        it is now rule-less, which is the shape the aggregator filters on.
        """
        with app.app_context():
            once_tmpl = _create_expense_template(
                seed_user, None, Decimal("5000.00"),
                name="One-Time Purchase",
            )

            recurring_tmpl = _create_expense_template(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Recurring Bill",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [once_tmpl, recurring_tmpl],
                BalanceContext.build(seed_user["user"].id),
            )
            # Only recurring: 100 * 26/12 = 216.67
            expected = (Decimal("100") * Decimal("26") / Decimal("12")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            assert result == expected, (
                f"Expected {expected} (once excluded), got {result}"
            )

    def test_emergency_fund_excludes_inactive_templates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Inactive templates are filtered out by the route and do not
        contribute to the committed monthly baseline.
        """
        with app.app_context():
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Inactive template -- excluded by route query.
            _create_expense_template(
                seed_user, EVERY_PERIOD, Decimal("999.00"),
                name="Inactive Bill", is_active=False,
            )

            # Active template -- included.
            _create_expense_template(
                seed_user, EVERY_PERIOD, Decimal("1500.00"),
                name="Active Bill",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Only active: 1500 * 26/12 = 3250.
            # If inactive were included: (1500+999)*26/12 = 5415.
            assert "$3,250/mo" in html, (
                "Expected only active template in committed baseline"
            )
            assert "$5,415/mo" not in html, (
                "Inactive template should not contribute"
            )

    def test_emergency_fund_handles_none_default_amount(
        self, app, seed_user,
    ):
        """Templates with default_amount=None are skipped without error.

        The column is NOT NULL in the schema, but the function handles it
        defensively for robustness, so the row is built UNSAVED rather than
        flushed.

        **Both halves are real model instances from plan step R9**, where the
        rule half was a ``SimpleNamespace`` carrying four hand-listed
        attributes -- one of them ``pattern_id``, a column dropped at R7c-c
        that nothing had read since.  A stand-in that lists what the reader
        happens to touch stops being a stand-in the moment the reader touches
        something else, and it cannot fail loud when it does: the shape it
        mirrors has no constraint to violate.  An unsaved
        ``TransactionTemplate`` holding a rule authored through the real door
        costs no flush and cannot drift from either model.
        """
        with app.app_context():
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                name="No amount",
                default_amount=None,
                recurrence_rule=transient_cadence_rule(
                    seed_user["user"].id, EVERY_PERIOD,
                ),
            )

            result = obligations_aggregator.committed_monthly(
                [template], BalanceContext.build(seed_user["user"].id),
            )
            assert result == Decimal("0.00"), (
                f"Expected 0.00 when template has None amount, got {result}"
            )

    def test_emergency_fund_every_n_periods_template(
        self, app, seed_user,
    ):
        """An every_n_periods template with n=2 and $600 contributes
        $600 * (26/2) / 12 = $650.00 per month.
        """
        with app.app_context():
            tmpl = _create_expense_template(
                seed_user, EVERY_N_PERIODS, Decimal("600.00"),
                name="Biweekly Alternating", interval_n=2,
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], BalanceContext.build(seed_user["user"].id),
            )
            assert result == Decimal("650.00"), (
                f"Expected 650.00 for every-2-periods template, got {result}"
            )

    def test_emergency_fund_annual_template(
        self, app, seed_user,
    ):
        """An annual template with $1,200 contributes $100.00 per month."""
        with app.app_context():
            tmpl = _create_expense_template(
                seed_user, ANNUAL, Decimal("1200.00"),
                name="Annual Insurance",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], BalanceContext.build(seed_user["user"].id),
            )
            assert result == Decimal("100.00"), (
                f"Expected 100.00 for annual template, got {result}"
            )

    def test_committed_monthly_empty_iterable(
        self, app, seed_user,
    ):
        """obligations_aggregator.committed_monthly([], pass) returns zero."""
        with app.app_context():
            result = obligations_aggregator.committed_monthly(
                [], BalanceContext.build(seed_user["user"].id),
            )
            assert result == Decimal("0.00"), (
                f"Expected 0.00 for empty iterable, got {result}"
            )
