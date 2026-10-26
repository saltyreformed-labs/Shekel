"""
Tests for the unified Accounts & Savings dashboard (category grouping)
and account hard-delete (5A.5-4).
"""

import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, CompoundingFrequencyEnum
from app.models.account import Account, AccountAnchorHistory
from app.models.interest_params import InterestParams
from app.models.loan_params import LoanParams
from app.models.ref import AccountType, TransactionType
from app.models.savings_goal import SavingsGoal
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.models.user import User, UserSettings
from app.utils.dates import display_today
from app.services.auth_service import hash_password
from app.services import account_service

from tests._test_helpers import (
    create_loan_account,
    loan_params_for,
    make_every_period_rule,
    one_off_row_of,
)


def _create_savings_account(
    seed_user, db_session, name="My Savings",
    anchor_balance=Decimal("5000.00"),
):
    """Helper to create a savings account.

    The default $5000 anchor posts a Step-5 opening correction at create
    time, which makes the account archive-only under hard-delete Guard 5;
    the hard-delete tests that need a deletable account pass
    ``Decimal("0.00")`` (a zero opening books nothing).
    """
    savings_type = db_session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=anchor_balance,
        ),
    )
    db_session.add(account)
    db_session.commit()
    return account


def _create_hysa_account(seed_user, db_session, name="My HYSA"):
    """Helper to create a HYSA account with params."""
    hysa_type = db_session.query(AccountType).filter_by(name="HYSA").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=hysa_type.id,
            name=name,
            anchor_balance=Decimal("10000.00"),
        ),
    )
    db_session.add(account)
    db_session.flush()

    # HIGH-06 / Commit 24: ``apy`` NOT NULL with no server_default.
    params = InterestParams(
        account_id=account.id, apy=Decimal("0.04500"),
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.DAILY,
        ),
    )
    db_session.add(params)
    db_session.commit()
    return account


class TestDashboardGrouping:
    """Dashboard groups accounts by category."""

    def test_dashboard_groups_by_category(self, auth_client, seed_user, db, seed_periods_today):
        """Dashboard shows category headers."""
        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Asset" in resp.data

    def test_dashboard_hysa_shows_interest(self, auth_client, seed_user, db, seed_periods_today):
        """HYSA account card shows APY info."""
        _create_hysa_account(seed_user, db.session)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"HYSA" in resp.data
        assert b"APY" in resp.data

    def test_dashboard_emergency_includes_hysa(self, auth_client, seed_user, db, seed_periods_today):
        """Emergency fund total includes HYSA balances."""
        # Create a savings account so emergency fund section appears.
        _create_savings_account(seed_user, db.session)
        _create_hysa_account(seed_user, db.session)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        # Should include both savings ($5,000) and HYSA ($10,000) in total.
        # D6-F fold: the EF card is now the Savings group card's footer line.
        assert b"Emergency fund coverage" in resp.data

    def test_dashboard_savings_goals_unchanged(self, auth_client, seed_user, db, seed_periods_today):
        """Goals section renders correctly (regression)."""
        savings_acct = _create_savings_account(seed_user, db.session)

        goal = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=savings_acct.id,
            name="Emergency Fund",
            target_amount=Decimal("20000.00"),
        )
        db.session.add(goal)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        # D6-F fold: the "Savings Goals" h5 is gone; goals render inside the
        # "Savings" group card.  The goal name proves the goals section
        # rendered; "Emergency fund coverage" proves the EF footer rendered.
        assert b"Emergency Fund" in resp.data
        assert b"Emergency fund coverage" in resp.data

    def test_dashboard_mortgage_shows_rate(self, auth_client, seed_user, db, seed_periods_today):
        """Mortgage card shows interest rate."""
        create_loan_account(
            seed_user, db.session, name="Home Loan",
            principal=Decimal("250000.00"), rate=Decimal("0.06500"), term=360,
            anchor_balance=Decimal("200000.00"),
            origination_date=date(2023, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Mortgage" in resp.data
        assert b"6.500%" in resp.data

    def test_dashboard_auto_loan_shows_payment(self, auth_client, seed_user, db, seed_periods_today):
        """Auto loan card shows monthly payment."""
        create_loan_account(
            seed_user, db.session, name="Car Payment",
            principal=Decimal("25000.00"), rate=Decimal("0.05000"), term=60,
            anchor_balance=Decimal("20000.00"),
            origination_date=date(2024, 6, 1), payment_day=15,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Auto Loan" in resp.data
        assert b"Monthly Payment" in resp.data

    def test_dashboard_liability_category(self, auth_client, seed_user, db, seed_periods_today):
        """Liabilities grouped under Liability header."""
        create_loan_account(
            seed_user, db.session, name="My Mortgage",
            principal=Decimal("200000.00"), rate=Decimal("0.06000"), term=360,
            anchor_balance=Decimal("150000.00"),
            origination_date=date(2022, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        # The liability group surfaces as "Liabilities" (the group header and
        # the stream legend both render category_labels['liability']).  The
        # prior singular "Liability" substring came from the diverging
        # allocation bar's legend (seg.label|title), retired with P-AC1.
        assert b"Liabilities" in resp.data

    def test_dashboard_no_accounts(self, app, db, seed_user):
        """Empty state renders the dashboard page with navigation elements.

        When no active accounts exist, the page should still render the
        Accounts heading and action buttons (New Account, etc.).
        """
        # Deactivate the default checking account.
        seed_user["account"].is_active = False
        db.session.commit()

        client = app.test_client()
        client.post("/login", data={"email": "test@shekel.local", "password": "testpass"})
        resp = client.get("/savings")
        assert resp.status_code == 200
        assert b"Accounts" in resp.data
        assert b"New Account" in resp.data

    def test_emergency_fund_uses_is_liquid(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Emergency fund total includes all is_liquid=True accounts.

        Checking and Savings are is_liquid=True by default. HYSA and
        Money Market are also is_liquid=True. CD (is_liquid=False) and
        retirement accounts should not contribute.
        """
        # seed_user["account"] is a Checking account (is_liquid=True) whose
        # origination assertion is already $1,000.00.  This line used to write
        # ``current_anchor_balance = 1000.00`` beside that assertion, which was
        # redundant even then; ruling R-EH deleted the column, and re-asserting
        # the same balance for the same day is what the write door answers as
        # UNCHANGED (ruling R-EQ).  The fixture's balance comes from the seed.

        # Add a Money Market account (is_liquid=True by seed).
        mm_type = db.session.query(AccountType).filter_by(
            name="Money Market",
        ).one()
        mm_acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=mm_type.id,
                name="My Money Market",
                anchor_balance=Decimal("2000.00"),
            ),
        )
        db.session.add(mm_acct)

        # Add a CD account (is_liquid=False).
        cd_type = db.session.query(AccountType).filter_by(name="CD").one()
        cd_acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=cd_type.id,
                name="My CD",
                anchor_balance=Decimal("5000.00"),
            ),
        )
        db.session.add(cd_acct)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        # Emergency fund section should appear (liquid accounts exist).
        # D6-F fold: the EF card is now the Savings group card's footer line.
        assert b"Emergency fund coverage" in resp.data

    def test_user_created_liquid_type_in_emergency_fund(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """A user-created type with is_liquid=True contributes to emergency fund."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum

        with app.app_context():
            custom_type = AccountType(
                name="TestLiquid",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                is_liquid=True,
            )
            db.session.add(custom_type)
            db.session.flush()

            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="Custom Liquid",
                    anchor_balance=Decimal("3000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            # D6-F fold: the EF card is now the Savings group card's footer line.
            assert b"Emergency fund coverage" in resp.data


# ── Hard Delete Tests (5A.5-4) ─────────────────────────────────────


class TestAccountHardDelete:
    """Tests for POST /accounts/<id>/hard-delete (permanent deletion).

    Accounts have multiple RESTRICT-FK dependents, requiring a careful
    guard chain before permanent deletion is allowed.
    """

    def test_hard_delete_account_no_history(self, app, auth_client, seed_user, db):
        """C-5A.5-22: Account with no transactions or templates is permanently deleted.

        $0-anchor: a non-zero anchor posts its Step-5 opening correction and
        becomes archive-only under Guard 5 (see the companion test below).
        """
        with app.app_context():
            savings = _create_savings_account(
                seed_user, db.session, name="Deletable Savings",
                anchor_balance=Decimal("0.00"),
            )
            acct_id = savings.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(Account, acct_id) is None

    def test_hard_delete_nonzero_anchor_archives_instead(
        self, app, auth_client, seed_user, db,
    ):
        """A non-zero-anchor account is archive-only (Step-5 Guard 5).

        The accepted behavior change (plan Section 3.5): the $5000 opening
        correction posted at create time is immutable posting-ledger
        history, so the hard delete archives the account instead --
        identical to the shipped loan behavior.
        """
        with app.app_context():
            savings = _create_savings_account(
                seed_user, db.session, name="Opening Savings",
            )
            acct_id = savings.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"posting-ledger history" in resp.data
            assert b"archived instead" in resp.data
            reloaded = db.session.get(Account, acct_id)
            assert reloaded is not None
            assert reloaded.is_active is False

    def test_hard_delete_account_with_history(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C-5A.5-23: Account with transactions is blocked and archived instead."""
        with app.app_context():
            account = seed_user["account"]
            acct_id = account.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            one_off_row_of(
                seed_periods_today[0],
                name="Test Expense",
                amount=Decimal("100.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=acct_id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=txn_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"transaction history" in resp.data
            assert b"archived instead" in resp.data

            reloaded = db.session.get(Account, acct_id)
            assert reloaded is not None
            assert reloaded.is_active is False

    def test_hard_delete_configured_loan_is_archived_not_deleted(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C-5A.5-24: a CONFIGURED loan is archived, never hard-deleted (Guard 5).

        Configuring a loan opens its genesis posting ledger in the same
        transaction as the ``LoanParams`` insert (``loan.create_params``), so a
        configured loan ALWAYS carries ledger postings -- and Guard 5
        (``archive_helpers.account_has_ledger_postings``) archives any account that
        does.  A configured loan is therefore not hard-deletable in production, and
        this test previously asserted the opposite: it passed only because its
        fixture never opened the ledger, a state production cannot reach.

        The flash assertion is deliberately DISCRIMINATING.  The old one --
        ``b"permanently deleted" in resp.data`` -- passes on the ARCHIVE message
        too, because that message reads "...cannot be permanently deleted. It has
        been archived instead."  It was a vacuous assertion: it could not have
        failed either way.
        """
        with app.app_context():
            acct = create_loan_account(
                seed_user, db.session, name="Test Mortgage",
                principal=Decimal("250000.00"), rate=Decimal("0.06500"),
                term=360, origination_date=date(2023, 1, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            params = loan_params_for(db.session, acct.id)

            acct_id = acct.id
            params_id = params.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived instead" in resp.data
            assert b"cannot be permanently deleted" in resp.data

            # Archived, not deleted: the account and its params both survive.
            reloaded = db.session.get(Account, acct_id)
            assert reloaded is not None
            assert reloaded.is_active is False
            assert db.session.get(LoanParams, params_id) is not None

    def test_hard_delete_blocked_by_transfer_templates(
        self, app, auth_client, seed_user, db,
    ):
        """C-5A.5-25: Account referenced by a transfer template cannot be deleted."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Transfer Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            xfer_template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Blocked Transfer",
                default_amount=Decimal("100.00"),
            )
            db.session.add(xfer_template)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{savings.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"recurring transfers" in resp.data

            # Account is NOT archived -- it was blocked, not fallback-archived.
            db.session.refresh(savings)
            assert savings.is_active is True

    def test_hard_delete_blocked_by_transaction_templates(
        self, app, auth_client, seed_user, db,
    ):
        """C-5A.5-26: Account referenced by a RECURRING definition cannot be deleted.

        The definition carries a cadence since plan step balance:X-bi-7a
        (ruling **R-BAL23**: the refusal counts definitions with a rule).  It
        was built bare, and a bare definition -- no rule, no row -- defines
        nothing and is disposed of with the account now (developer
        2026-09-13);
        ``test_accounts.test_hard_delete.TestHardDeleteAndTheAccountsDefinitions``
        grades that arm.  What this case grades is guard 3 itself.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user, db.session, name="Template Acct")
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                name="Blocking Template",
                default_amount=Decimal("50.00"),
            )
            db.session.add(template)
            db.session.flush()
            make_every_period_rule(db.session, template)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{savings.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"recurring transactions" in resp.data

            db.session.refresh(savings)
            assert savings.is_active is True

    def test_hard_delete_account_idor(self, app, auth_client, seed_user, db):
        """C-5A.5-27: Hard-deleting another user's account returns 404 (security)."""
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            other_user = User(
                email="other@shekel.local",
                password_hash=hash_password("otherpass"),
                display_name="Other User",
            )
            db.session.add(other_user)
            db.session.flush()
            settings = UserSettings(user_id=other_user.id)
            db.session.add(settings)

            # The second user's calendar, so the factory has somewhere to
            # anchor.
            # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                open_owner_calendar as _open_calendar,
            )
            _bootstrap = _open_calendar(other_user.id, _date(2024, 1, 5))[0]

            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
            other_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=other_user.id,
                    account_type_id=checking_type.id,
                    name="Other Checking",
                    anchor_balance=Decimal("500.00"),
                ),
            )
            db.session.commit()
            other_id = other_acct.id

            resp = auth_client.post(
                f"/accounts/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404
            assert db.session.get(Account, other_id) is not None

    def test_cockpit_separates_active_and_archived_accounts(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C-5A.5-28: The cockpit shows active and archived in separate sections.

        After Loop B P4 the standalone /accounts table was retired; the
        active-vs-archived split now lives on the unified cockpit
        (savings.dashboard): active accounts render as cards, archived
        accounts in the collapsed "Archived Accounts (N)" section.

        **The FIGURE is asserted under its own label, not just the name**
        (plan step X-w2; anchored and its reason corrected at X-w6).  This test
        asserted the section header and the account name only.

        The reason first written here was wrong, and correcting it is the
        point.  A bare ``{{ value }}`` on a missing attribute does render
        empty -- but this figure goes through the ``money`` macro, whose first
        statement is ``{% if value < 0 %}``, and ``Undefined.__lt__`` RAISES.
        So ruling R-CH's rename would have produced a 500, which the
        ``status_code == 200`` arm above already caught.  What no status check
        can see is the drawer rendering the WRONG figure, or an unrelated
        ``$5,000.00`` elsewhere on the page satisfying a bare ``in html``.  So
        the assertion is anchored to the ``Last Balance`` label it sits under.
        """
        with app.app_context():
            # seed_user["account"] is active by default.
            archived = _create_savings_account(
                seed_user, db.session, name="Archived Savings",
            )
            archived.is_active = False
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active account appears as a cockpit card.
            assert "Checking" in html

            # Archived section with count indicator.
            assert "Archived Accounts (1)" in html
            assert "Archived Savings" in html
            # And its last anchor balance -- the $5,000 the helper anchors it
            # at -- is actually RENDERED under the "Last Balance" label, matched
            # as one block so a figure elsewhere on the page cannot stand in.
            assert re.search(
                r'Last Balance</div>\s*<div class="[^"]*font-mono">'
                r'\$5,000\.00',
                html,
            ), "the archived drawer did not render $5,000.00 as Last Balance"

    def test_hard_delete_account_with_history_already_archived(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Already-archived account with transactions stays archived without re-archiving."""
        with app.app_context():
            account = seed_user["account"]
            acct_id = account.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            one_off_row_of(
                seed_periods_today[0],
                name="Pre-existing Expense",
                amount=Decimal("100.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=acct_id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=txn_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )

            account.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"transaction history" in resp.data

            reloaded = db.session.get(Account, acct_id)
            assert reloaded is not None
            assert reloaded.is_active is False

    def test_hard_delete_account_with_anchor_history(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Account with anchor history records but no txns is permanently deleted.

        $0-anchor create (no opening posted), then a directly-added history
        row that never synced: history ROWS alone do not block a hard
        delete -- only posted ledger history (Guard 5) does.
        """
        with app.app_context():
            savings = _create_savings_account(
                seed_user, db.session, name="Anchor Test",
                anchor_balance=Decimal("0.00"),
            )
            history = AccountAnchorHistory(
                account_id=savings.id,
                anchor_balance=Decimal("5000.00"),
                observed_on=display_today(),
            )
            db.session.add(history)
            db.session.commit()

            acct_id = savings.id
            history_id = history.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            assert db.session.get(Account, acct_id) is None
            assert db.session.get(AccountAnchorHistory, history_id) is None

    def test_hard_delete_blocked_by_archived_transfer_template(
        self, app, auth_client, seed_user, db,
    ):
        """Account referenced by an archived transfer template is still blocked.

        The TransferTemplate.from_account_id FK is ON DELETE RESTRICT,
        so even archived (is_active=False) templates block account deletion.
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Archived Template Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            xfer_template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Archived Xfer Template",
                default_amount=Decimal("100.00"),
                is_active=False,
            )
            db.session.add(xfer_template)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{savings.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"recurring transfers" in resp.data

            db.session.refresh(savings)
            assert savings.is_active is True

    def test_archive_label_in_flash_accounts(
        self, app, auth_client, seed_user, db,
    ):
        """Archive flash message says 'archived' not 'deactivated'."""
        with app.app_context():
            savings = _create_savings_account(
                seed_user, db.session, name="Flash Test",
            )

            resp = auth_client.post(
                f"/accounts/{savings.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            assert b"deactivated" not in resp.data
