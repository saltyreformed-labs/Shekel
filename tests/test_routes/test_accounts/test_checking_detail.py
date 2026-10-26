"""Account route tests: the checking detail page, its canonical producer, and the dashboard link to
it.
"""

from decimal import Decimal

import pytest

from app.services.cash_flow_set import CashFlowSet
from app.extensions import db
from app.utils.dates import display_today
from tests._test_helpers import (
    APP_PACKAGE_DIR,
    current_pay_period,
    make_projected_envelope_expense,
    one_off_row_of,
    open_books_before_the_first_assertion,
    record_paydays_across_a_hole,
    rhythm_of,
)
from app.models.ref import AccountType, Status, TransactionType
from app.services import account_service, cash_ledger

from ._helpers import _override_account_anchor, _CLEARED_PURCHASE_DAY, _add_cleared_debit_entry


# ── Checking Detail ──────────────────────────────────────────────


class TestCheckingDetail:
    """Tests for a checking account on the merged cash detail page.

    The Fable 5 overhaul merged the checking / interest detail pages into
    ``accounts.cash_detail`` (URL ``/accounts/<id>/details``); a plain
    checking account renders its balance hero, horizon chips, and trend
    chart there.  The per-period table was dropped (developer ruling);
    exact per-period values live in the chart tooltip and, for checking,
    on the grid.
    """

    def _create_checking_account(self, seed_user, periods, balance="5000.00"):
        """Create a new checking account with anchor set to period 0.

        Creates a fresh account (avoiding session identity map caching
        from seed_user's account) with the specified anchor balance.
        """
        checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Detail Checking",
                anchor_balance=Decimal(balance),
            ),
        )
        db.session.add(acct)
        return acct

    def test_checking_detail_page_renders(self, app, auth_client, seed_user):
        """GET /accounts/<id>/checking renders the detail page with account name and balance."""
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")

            assert resp.status_code == 200
            assert b"Detail Checking" in resp.data
            assert b"$5,000.00" in resp.data

    def test_checking_detail_projection_values_are_correct(
        self, app, auth_client, seed_user,
    ):
        """Checking detail projections match expected balance calculations.

        With anchor $5,000 and net +$500 per period, projections are:
        3 months (6 periods) = $8,000, 6 months (13) = $11,500, 1 year (26) = $18,000.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Salary"]

            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=27, rhythm=rhythm_of(14),
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create income and expense in all post-anchor periods.
            for p in periods[1:]:
                one_off_row_of(
                    p,
                    name="Paycheck",
                    amount=Decimal("2000.00"),
                    user_id=p.user_id,
                    account_id=acct.id,
                    scenario_id=scenario.id,
                    transaction_type_id=income_type.id,
                    category_id=category.id,
                )
                one_off_row_of(
                    p,
                    name="Expenses",
                    amount=Decimal("1500.00"),
                    user_id=p.user_id,
                    account_id=acct.id,
                    scenario_id=scenario.id,
                    transaction_type_id=expense_type.id,
                    category_id=category.id,
                )
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # 3 months (6 periods): 5000 + 6*500 = 8000
            assert b"$8,000" in resp.data
            # 6 months (13 periods): 5000 + 13*500 = 11500
            assert b"$11,500" in resp.data
            # 1 year (26 periods): 5000 + 26*500 = 18000
            assert b"$18,000" in resp.data

    def test_checking_detail_matches_grid_balance(
        self, app, auth_client, seed_user,
    ):
        """The detail page's 3-month projection is the HAND-COMPUTED figure.

        It used to derive its expected value by calling the balance producer
        the route also called, which proved only that one function is
        deterministic.  Plan step X-g4b deleted that producer and the
        derivation with it: the figure below is arithmetic, so the page and the
        seam must both be right rather than merely agree.

        Hand-computed: a $5,000.00 opening assertion, then 26 periods each
        carrying +$2,000.00 income and -$1,500.00 expense (net +$500.00).  The
        3-month projection is period 6, six periods forward of the anchor:
        ``5,000.00 + 6 x 500.00 = $8,000.00``.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Salary"]

            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=27, rhythm=rhythm_of(14),
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            for p in periods[1:]:
                one_off_row_of(
                    p,
                    name="Paycheck",
                    amount=Decimal("2000.00"),
                    user_id=p.user_id,
                    account_id=acct.id,
                    scenario_id=scenario.id,
                    transaction_type_id=income_type.id,
                    category_id=category.id,
                )
                one_off_row_of(
                    p,
                    name="Bills",
                    amount=Decimal("1500.00"),
                    user_id=p.user_id,
                    account_id=acct.id,
                    scenario_id=scenario.id,
                    transaction_type_id=expense_type.id,
                    category_id=category.id,
                )
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # The projection summary uses {:,.0f} format.
            assert b"$8,000" in resp.data

    def test_cash_detail_serves_plain_savings(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/details returns 200 for a plain Savings account.

        The merged cash detail page closed the audit's Surface 6 coverage
        gap: plain Savings (and Credit Card / custom cash) types -- which
        had NO detail page before -- are now served with the plain context
        shape (no interest parameters card).
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="My Savings",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.add(savings)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{savings.id}/details")
            assert resp.status_code == 200
            assert b"My Savings" in resp.data
            # Plain cash: no interest parameters card (no APY input).
            assert b'name="apy"' not in resp.data

    def test_cash_detail_rejects_other_users_account(
        self, app, auth_client, seed_user, second_user,
    ):
        """GET /accounts/<id>/details returns 404 for another user's account (IDOR)."""
        with app.app_context():
            resp = auth_client.get(
                f"/accounts/{second_user['account'].id}/details"
            )
            assert resp.status_code == 404

    def test_checking_detail_handles_no_transactions(
        self, app, auth_client, seed_user,
    ):
        """Checking detail with no transactions shows flat balance at anchor amount."""
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=27, rhythm=rhythm_of(14),
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # With no transactions, projections should show the anchor balance.
            assert b"$5,000" in resp.data

    def test_checking_detail_handles_short_horizon(
        self, app, auth_client, seed_user,
    ):
        """Short horizon: 3-month projection available, 12-month projection missing."""
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # 3-month target (period index 6) is within range (10 periods).
            assert b"3 months" in resp.data
            # 6-month (index 13) and 12-month (index 26) are beyond our horizon.
            assert b"6 months" not in resp.data
            assert b"1 year" not in resp.data

    def test_checking_detail_excludes_credit_transactions(
        self, app, auth_client, seed_user,
    ):
        """Credit-status transactions are excluded from the projected balance.

        A credit expense should not reduce the checking balance because
        credit transactions are not paid from checking.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Rent"]

            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            credit_status = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create a credit expense in the first post-anchor period.
            row = one_off_row_of(
                periods[1],
                name="Credit Card Groceries",
                amount=Decimal("1000.00"),
                user_id=periods[1].user_id,
                account_id=acct.id,
                scenario_id=scenario.id,
                transaction_type_id=expense_type.id,
                category_id=category.id,
            )
            row.status_id = credit_status.id
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # The credit expense should NOT reduce the balance.
            # Projections should still show $5,000 (flat from anchor).
            assert b"$5,000" in resp.data
            # Verify the balance was NOT reduced by the credit expense.
            assert b"$4,000" not in resp.data

    @pytest.mark.server_clock
    def test_checking_detail_shows_anchor_date(self, app, auth_client, seed_user):
        """The anchored-as-of date is displayed in the balance hero caption.

        The rebuilt hero caption renders ``anchor_as_of`` -- the anchor
        EVENT instant (the origination ``AccountAnchorHistory`` row), in the
        user's DISPLAY timezone -- not the anchor period's start date (audit
        finding #2) and not the UTC-day ``as_of_date`` (which shows the wrong
        civil day for a late-evening-Eastern event).  The expected string is
        computed from the anchor's ``created_at`` via ``to_display_date`` --
        NOT ``date.today()``, which reads the PROCESS timezone and would
        diverge from the Eastern caption in a UTC CI runner during the
        late-evening-Eastern window.  Deriving both sides from the same
        ``created_at`` also makes the assertion immune to a midnight race.
        The distinct-date (event vs period start) case is pinned in
        ``TestCashDetailContext.test_anchor_as_of_is_event_date_not_period_start``.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.utils.dates import to_display_date  # pylint: disable=import-outside-toplevel
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # The caption shows the anchor event's DISPLAY-timezone civil date,
            # computed here from the same ``created_at`` the caption renders.
            anchor = cash_ledger.resolve_anchor(acct)
            anchor_date_str = to_display_date(anchor.created_at).strftime(
                "%b %-d, %Y",
            )
            assert anchor_date_str.encode() in resp.data


class TestCheckingDetailCanonicalProducer:
    """C7: /accounts checking detail routed through the balance-at seam.

    Pins the symptom #5 fix: the per-account detail page now produces
    the same checking balance as the grid and /savings for the same
    inputs.  Tests track plan IDs C7-1 through C7-3 plus the
    verification gates listed in the Commit 7 spec.
    """

    def test_accounts_checking_equals_grid(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """C7-1: /accounts checking detail current balance == grid balance.

        Reproduction of symptom #5 / F-009 worked example
        (``05_symptoms.md``):

          - Real checking anchor 614.29 on the current pay period.
          - One Projected envelope expense ``estimated_amount = 500.00``
            in the same period (so ``sum_projected`` applies).
          - Three CLEARED debit entries 20.00 + 15.71 + 10.00 = 45.71.
            No credit, no uncleared.

        Hand arithmetic (F-009):

          cleared_debit   = 20.00 + 15.71 + 10.00 = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          anchor_period_balance = 614.29 + 0 - 454.29 = 160.00

        Both the grid and the /accounts checking detail page MUST return
        Decimal("160.00") -- one seam entry, ``balance_at.cash_balance_map``,
        read twice.  Pre-Commit-7 the checking detail page reported the
        silent-degrade value Decimal("114.29") via the unloaded-entries seam.

        The $160.00 survives the basis change at plan step X-c2b2 for a reason
        this fixture makes plain: the account holds ONE asserted balance and the
        only row is still PROJECTED, so the fold has the same assertion to
        replay and the same entries-aware reservation to hold back.  The two
        bases diverge only where money has SETTLED, and nothing here has.
        """
        # Pylint: import-outside-toplevel -- module-scoped imports in this file
        # are the route-test convention; the seam read is local to this case.
        from app.services import balance_at  # pylint: disable=import-outside-toplevel
        from app.services.balance_at import BalanceContext  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None
            account = seed_user["account"]
            _override_account_anchor(
                db.session, account, current_period, Decimal("614.29"),
            )

            txn = make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                account=account,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_cleared_debit_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                )
            db.session.commit()

            # Grid value via the seam entry the grid's balance row reads, so
            # replaying it here is "what does the grid show" without a route
            # round-trip.  It has been repointed TWICE for the same reason -- a
            # guard that had stopped guarding (the N-63 shape): from
            # ``_cash_engine.balances_for`` at plan step X-c2b2, and from
            # ``cash_balance_map`` at X-g3b, where the grid stopped reading the
            # cash view for a modelled account (ruling R-W).  This account is
            # PLAIN, so the two agree by construction -- which is exactly the
            # condition that made the previous spelling look harmless.
            grid_current_balance = balance_at.grid_balance_view(
                CashFlowSet.single(account),
                BalanceContext.build(seed_user["user"].id),
            ).columns[current_period.id].balance

            # F-009 / CRIT-01: 614.29 - max(500 - 45.71 - 0, 0)
            #                = 614.29 - 454.29 = 160.00.
            assert grid_current_balance == Decimal("160.00")

            # The cash-detail balance hero renders ``money(current_balance)``
            # (accounts/cash_detail.html), so the entries-aware $160.00
            # appears verbatim.
            resp = auth_client.get(f"/accounts/{account.id}/details")
            assert resp.status_code == 200
            assert b"$160.00" in resp.data
            # Pre-Commit-7 the page showed the silent-degrade value.
            # Asserting its absence locks the regression.
            assert b"$114.29" not in resp.data

    def test_accounts_anchor_null_fork_removed(self):
        """C7-2: the dead anchor-NULL fallback fork is deleted from accounts.

        Verification gate from the Commit 7 spec.  Post-Commit-3 the
        anchor columns are NOT NULL with a CHECK constraint, so the
        legacy ``account.current_anchor_period_id or current_period``
        substitution is unreachable.  Leaving it in place would be a
        dead branch (CLAUDE.md rule 1: do it right) and a regression
        risk if the CHECK is ever loosened again.  This test greps
        every module in the accounts route package to fail loud if
        the fork is ever reintroduced.

        File-path note: Commit 21 of the follow-up remediation (F-1)
        split the monolithic ``app/routes/accounts.py`` into a per-
        sub-domain package.  The anchor-related routes now live in
        ``app/routes/accounts/crud.py``,
        ``app/routes/accounts/anchor.py``, and
        ``app/routes/accounts/detail.py``; the guard sweeps all of
        them so a regression in any sub-module trips this lock.
        """
        import os  # pylint: disable=import-outside-toplevel
        import re  # pylint: disable=import-outside-toplevel

        # Found from the app package's own location, never from this test's
        # place in the tree, and the read must hold the three modules the
        # docstring names: a "not" search over an empty read passes on nothing.
        package_dir = APP_PACKAGE_DIR / "routes" / "accounts"
        sources = []
        for name in sorted(os.listdir(package_dir)):
            if not name.endswith(".py"):
                continue
            with open(
                os.path.join(package_dir, name), "r", encoding="utf-8",
            ) as fh:
                sources.append((name, fh.read()))
        read = dict(sources)
        assert all(read.get(name) for name in ("crud.py", "anchor.py", "detail.py")), (
            f"did not read a non-empty crud.py, anchor.py and detail.py from "
            f"{package_dir}: {sorted(read)}"
        )

        for name, src in sources:
            # Strip comments before matching: documentation that
            # references the legacy pattern by name is fine; only live
            # code is forbidden.
            code_lines = [
                line for line in src.splitlines()
                if not line.lstrip().startswith("#")
            ]
            code = "\n".join(code_lines)

            # The legacy fork had two shapes; both are dead post-Commit-3.
            assert not re.search(r"current_anchor_period_id\s+or\s+", code), (
                f"anchor-NULL fork (`... or current_period`) found in "
                f"app/routes/accounts/{name}"
            )
            assert not re.search(r"current_anchor_balance\s+or\s+", code), (
                f"anchor-NULL fork (`... or 0`) found in "
                f"app/routes/accounts/{name}"
            )

    def test_accounts_multi_account_each_correct(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """C7-3: two checking accounts with entries each project correctly.

        Pins that the entries-aware reduction applies per-account:
        account A has cleared entries totaling $45.71 against a $500
        estimate, account B has cleared entries totaling $100.00
        against a $300 estimate, both on the current period.

          Account A: 614.29 + 0 - max(500 - 45.71, 0) = 160.00
          Account B: 1000.00 + 0 - max(300 - 100,    0) = 800.00

        Pre-Commit-7 both pages would have silently degraded to the
        non-entries-aware projection ($114.29 and $700.00).
        """
        with app.app_context():
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None

            # Account A: the seed_user account, anchored at 614.29.
            account_a = seed_user["account"]
            _override_account_anchor(
                db.session, account_a, current_period, Decimal("614.29"),
            )

            # Account B: a second checking account anchored at 1000.00.
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account_b = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Second Checking",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.flush()
            # Its books open before the cleared entries below, which are
            # dated earlier than the owner's calendar starts -- so the bound
            # has to be named rather than derived (plan step X-f3c-2b, ruling
            # **R-HG**).
            open_books_before_the_first_assertion(
                db.session, account_b, also_before=_CLEARED_PURCHASE_DAY,
            )

            txn_a = make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                account=account_a,
                estimated=Decimal("500.00"),
                name="Groceries A",
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_cleared_debit_entry(
                    db.session,
                    txn=txn_a,
                    user_id=seed_user["user"].id,
                    amount=amt,
                )

            txn_b = make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                account=account_b,
                estimated=Decimal("300.00"),
                name="Groceries B",
            )
            _add_cleared_debit_entry(
                db.session,
                txn=txn_b,
                user_id=seed_user["user"].id,
                amount=Decimal("100.00"),
            )
            db.session.commit()

            # F-009 / CRIT-01: each account projects from its own
            # anchor minus the entries-aware reservation on its own
            # transactions.  Pre-Commit-7 both pages showed the
            # non-entries-aware value ($114.29 and $700.00).
            resp_a = auth_client.get(f"/accounts/{account_a.id}/details")
            assert resp_a.status_code == 200
            assert b"$160.00" in resp_a.data
            assert b"$114.29" not in resp_a.data

            resp_b = auth_client.get(f"/accounts/{account_b.id}/details")
            assert resp_b.status_code == 200
            # 1000.00 - max(300 - 100, 0) = 1000.00 - 200.00 = 800.00.
            assert b"$800.00" in resp_b.data
            assert b"$700.00" not in resp_b.data

    def test_accounts_checking_zero_anchor_renders_projection(
        self, app, auth_client, db, seed_user,
    ):
        """Zero-anchor account shows a populated zero-anchored projection.

        Commit 7 verification gate: an account with anchor balance
        ``Decimal("0.00")`` (a real zero per E-12, not "missing") must
        render with a populated period projection -- not blank, not
        omitted.  Pre-Commit-3 the anchor-NULL fork would have made the
        page degrade differently from the grid; post-Commit-3 the
        anchor is non-NULL and post-Commit-7 the producer routes the
        zero anchor through ``balances_for`` like any other balance.

        Hand arithmetic: no transactions, anchor 0.00, projection is
        flat 0.00 across every period covered by the anchor period
        forward.  The current-balance display, the balance-projection
        table, and the 3-month horizon ($0) must all appear.

        Periods are generated with ``num_periods=10`` starting today,
        so the anchor sits on period 0 and ``period_index`` 6 (the
        3-month horizon) is reachable.  ``seed_periods_today`` would
        anchor today on period 4 and ``4 + 6 = 10`` exceeds the
        fixture's 10-period window, omitting the horizon -- the
        explicit ``generate_pay_periods`` here mirrors the other
        ``TestCheckingDetail`` tests so the horizon is in range.
        """
        with app.app_context():
            # The CALL is the setup: it creates the ten periods this
            # page projects across.  The return value is unused since
            # the anchor no longer names a period.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )

            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Zero Anchor Checking",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.flush()
            db.session.commit()

            resp = auth_client.get(f"/accounts/{account.id}/details")
            assert resp.status_code == 200
            assert b"Zero Anchor Checking" in resp.data
            # The balance hero renders ``money(current_balance)``; a real
            # zero anchor (E-12: zero is a value, not "missing") must show
            # as "$0.00", not blank or "--".
            assert b"$0.00" in resp.data
            # The 3-month horizon chip appears when there is a period at
            # offset 6 from the current period; the explicit 10-period
            # setup guarantees one (matches the existing
            # ``test_checking_detail_handles_short_horizon`` pattern).  The
            # chip label is "In 3 months".
            assert b"3 months" in resp.data
            # The trend chart is populated (a real zero projection, not an
            # empty state): the serialized series carries the flat-zero
            # balances the chart renders, so the data-chart canvas is
            # present rather than the "No projection to chart yet" state.
            assert b"data-chart=" in resp.data
            assert b"No projection to chart yet" not in resp.data

    def test_cash_detail_balance_routed_through_seam(self):
        """Static guard: cash_detail balances route through the balance-at seam.

        F-6 lock, sibling of
        ``test_grid_balance_computation_routed_through_resolver``.
        The cross-page balance-equality regression test
        (``tests/test_integration/test_cross_page_balance_equality.py``,
        Commit 11 of the main remediation) cannot catch a route-handler
        bypass of the canonical producer because its /accounts reader
        re-runs ``balance_at.cash_balance_map`` itself rather than parsing
        the rendered HTML.  A regression that swaps the detail route for a
        hand-rolled balance loop would drift silently through that lock.  This
        static guard closes the gap.

        The single ``cash_detail`` route reads plain cash balances via the
        cash-flow entry ``balance_at.cash_balance_map`` and interest-bearing
        balances via ``balance_at.interest_projection_for_account``, which
        returns the accrued balances AND the earned-interest map from ONE cash
        fold.  It reaches no producer directly -- structurally since plan step
        D3, W9910 forbidding any import of a seam-private module.

        **Every arm matches the CALL, with its open paren, and that is the
        whole reason this guard was rewritten** (plan finding N-63, promoted to
        a Section 8 lesson: "a static guard that greps for a NAME cannot tell
        code from prose").  Its second arm used to look for the bare name
        ``balance_at.balance_map`` -- and plan step X-c2b2 replaced that call
        with ``interest_projection_for_account``, leaving the string alive in
        three docstrings, so the guard went on reporting the wiring intact while
        what it locked had moved.  Measured on this tree before the fix: zero
        call sites, three prose mentions, green.  The docstrings were corrected
        with it.

        Three assertions:
          1. ``balance_at.cash_balance_map(`` must appear (positive: the
             plain-cash seam wiring is intact).
          2. ``balance_at.interest_projection_for_account(`` must appear
             (positive: the interest wiring is intact).
          3. ``balance_at.balance_map(`` must NOT appear -- the whole-account
             kind-correct map is the shape the page used before X-c2b2, and
             reaching it beside the interest map is what made the page fold the
             SAME account twice per render (finding N-64).  This arm is what
             keeps that fix from silently regressing.

        **A FOURTH arm forbade ``balance_calculator.calculate_balances(`` and
        was deleted at plan step X-g4b, with the producer.**  Section 8's rule:
        when the name a negative arm forbids no longer exists, the arm stops
        being a guard and becomes a sentence that can never fail -- which reads
        as coverage and is not.  It deletes WITH the name.

        File path note: the merged ``cash_detail`` route (and the
        ``checking_detail`` / ``interest_detail`` redirect stubs) live in
        ``app/routes/accounts/detail.py``; the static guard reads that file
        directly.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        accounts_source = Path(
            "app/routes/accounts/detail.py",
        ).read_text(encoding="utf-8")
        assert "balance_at.cash_balance_map(" in accounts_source, (
            "app/routes/accounts/detail.py no longer CALLS "
            "``balance_at.cash_balance_map(`` -- regression on the "
            "balance-at seam contract.  Route the plain-cash balance "
            "computation through the seam's cash-flow entry instead of a "
            "hand-rolled loop or a direct producer call."
        )
        assert (
            "balance_at.interest_projection_for_account(" in accounts_source
        ), (
            "app/routes/accounts/detail.py no longer CALLS "
            "``balance_at.interest_projection_for_account(`` -- regression on "
            "the balance-at seam contract.  An interest account's accrued "
            "balances and its earned-interest map must come from ONE seam "
            "call, so the page cannot fold the account twice (N-64) or show a "
            "chip that explains a balance change the chart does not."
        )
        assert "balance_at.balance_map(" not in accounts_source, (
            "app/routes/accounts/detail.py CALLS the whole-account "
            "kind-correct ``balance_at.balance_map(`` -- that is the "
            "pre-X-c2b2 shape, and calling it beside the interest map folds "
            "the same account twice per render (N-64).  Read both halves from "
            "``interest_projection_for_account`` instead."
        )


class TestCheckingDashboardLink:
    """Tests for the cash-detail links on the savings/accounts dashboard.

    The cockpit's ``detail_endpoint`` macro (shared via _acct_macros.html)
    now routes EVERY cash account -- checking, interest-bearing, and the
    previously page-less plain types -- to the unified
    ``accounts.cash_detail`` page (account_detail_audit.md, rebuild
    decisions 1-2), so the retired type-specific ``/checking`` URL must no
    longer appear anywhere on the dashboard.
    """

    def test_dashboard_has_checking_detail_link(self, app, auth_client, seed_user):
        """GET /savings links the checking card to the unified detail page."""
        with app.app_context():
            # The CALL is the setup: the dashboard can only compute a
            # balance for the seed account across periods that exist.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The checking card links to the unified cash detail page,
            # not the retired type-specific /checking URL.
            expected_url = f"/accounts/{seed_user['account'].id}/details"
            assert expected_url.encode() in resp.data
            legacy_url = f"/accounts/{seed_user['account'].id}/checking"
            assert legacy_url.encode() not in resp.data

    def test_dashboard_links_plain_types_to_cash_detail(
        self, app, auth_client, seed_user,
    ):
        """A plain Savings card links to the unified page (decision 2).

        Pre-rebuild, Savings / Credit Card cards had NO detail link (the
        macro's empty fall-through branch); the coverage ruling gives
        them the same cash detail page as checking.
        """
        with app.app_context():
            # The CALL is the setup: the dashboard can only compute a
            # balance for either card across periods that exist.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )

            # Create a savings account.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="My Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)

            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The savings card now links to the unified cash detail page.
            savings_details_url = f"/accounts/{savings.id}/details"
            assert savings_details_url.encode() in resp.data

            # And the checking card does too.
            checking_url = f"/accounts/{seed_user['account'].id}/details"
            assert checking_url.encode() in resp.data
