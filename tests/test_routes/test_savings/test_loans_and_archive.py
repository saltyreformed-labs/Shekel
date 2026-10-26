"""Loans and archived accounts on the dashboard: the Paid Off badge, archive and unarchive, and the
debt summary card.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.amount_ownership import AmountOwnership
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import account_service, balance_at
from app.services.balance_at import BalanceContext

from tests._test_helpers import create_account_of_type, create_loan_account, start_test_pay_list

from ._helpers import _create_small_loan


def _make_confirmed_transfer(seed_user, to_account, period, amount):
    """Create a confirmed (Paid) transfer to a loan account.

    Created Projected, then settled through the settle verb (a Paid press on
    the owner's today): the create makes no settled transfer (ruling
    **R-BAL379**).
    """
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    transfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=to_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=seed_user["categories"]["Rent"].id,
        ),
    )
    transfer_service.settle_transfer(transfer.id, seed_user["user"].id)
    return transfer


class TestPaidOffBadge:
    """Tests for the Paid Off badge on the accounts dashboard.

    Commit 5.9-2: a green "Paid Off" badge appears on debt account
    cards when confirmed payments bring the remaining balance to zero.
    """

    def test_paid_off_badge_shown(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Loan fully paid by a confirmed payment: badge appears.

        A $1,000 loan at 5% for 12 months.  A single confirmed
        payment of $1,100 covers the full balance + interest.
        """
        with app.app_context():
            acct = _create_small_loan(seed_user)
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("1100.00"),
            )
            # Payoff recorded as a balance true-up to $0 (cash lump sums
            # no longer auto-pay-off under the contractual schedule).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" in resp.data

    def test_no_badge_when_balance_remaining(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Partial confirmed payment: no badge."""
        with app.app_context():
            acct = _create_small_loan(seed_user)
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data

    def test_no_badge_when_no_payments(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Loan with no payments at all: no badge."""
        with app.app_context():
            _create_small_loan(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data

    def test_no_badge_projected_only(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Projected payment covering full balance: no badge.

        Projections do not equal payoff.  Only confirmed (Paid/Settled)
        payments count toward the paid-off determination.
        """
        with app.app_context():
            from app.services import transfer_service  # pylint: disable=import-outside-toplevel

            acct = _create_small_loan(seed_user)
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data

    def test_paid_off_lump_sum(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Single lump-sum payment on a small loan: badge appears.

        A $1,000 loan paid off with a single $1,100 confirmed
        payment triggers the 5.8 overpayment guard, capping the
        payment at remaining balance + interest.
        """
        with app.app_context():
            acct = _create_small_loan(
                seed_user, name="Lump Sum Loan",
                principal=Decimal("500.00"), rate=Decimal("0.06000"), term=6,
            )
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("600.00"),
            )
            # Payoff recorded as a balance true-up to $0.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Paid Off" in html

    def test_paid_off_multiple_accounts_mixed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two loans: one paid off, one not.  Badge on the right one only."""
        with app.app_context():
            paid_off = _create_small_loan(
                seed_user, name="Paid Loan",
                principal=Decimal("1000.00"),
            )
            _make_confirmed_transfer(
                seed_user, paid_off, seed_periods[7], Decimal("1100.00"),
            )

            _unpaid = _create_small_loan(
                seed_user, name="Unpaid Loan",
                principal=Decimal("5000.00"),
            )
            # Payoff recorded as a balance true-up to $0; the $5,000 loan
            # stays active (no true-up), so only one Paid Off badge.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(paid_off.loan_params, Decimal("0.00"))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The paid-off loan's card should have the badge.
            assert "Paid Off" in html
            # Only one badge should appear (for the paid-off loan).
            assert html.count("Paid Off") == 1

    def test_sub_penny_not_paid_off(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Payment leaving $0.01 remaining: not paid off.

        Only exact zero qualifies.  This tests that the comparison
        uses == Decimal("0.00"), not a threshold.
        """
        with app.app_context():
            # A $100 loan at 5% for 12 months, originating Jan 2026.
            # The schedule starts from origination, so two contractual
            # payments (Feb, Mar) reduce the balance before the
            # confirmed payment in April (seed_periods[7]).
            #
            # After month 1 (Feb): balance = $91.86
            # After month 2 (Mar): balance = $83.68
            # Month 3 (Apr) interest: $83.68 * 0.05/12 = $0.35
            # Payment of $84.02 -> principal = $84.02 - $0.35 = $83.67
            # Remaining: $83.68 - $83.67 = $0.01
            acct = _create_small_loan(
                seed_user, name="Sub Penny Loan",
                principal=Decimal("100.00"), rate=Decimal("0.05000"), term=12,
            )
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("84.02"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data


# -- Account Archival on Savings Dashboard Tests (Commit 5.9-3) -----------


class TestAccountArchivalDashboard:
    """Tests for archive/unarchive behavior on the accounts dashboard.

    Commit 5.9-3: archived accounts move to a collapsed section,
    active accounts get an archive button, and paid-off loans get
    a prominent archive prompt.
    """

    def test_archived_account_hidden_from_active_section(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived account does not appear in the active account cards."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Hidden Savings",
                    anchor_balance=Decimal("500.00"),
                ),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The active section (before "Archived Accounts") should
            # not contain the archived account name as a card title.
            active_section = html.split("Archived Accounts")[0]
            assert "Hidden Savings" not in active_section

    def test_archived_section_shown_when_archived_exist(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """When at least one account is archived, the collapsed section appears."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Old Account",
                    anchor_balance=Decimal("0"),
                ),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Archived Accounts" in html
            assert "archivedAccounts" in html

    def test_archived_section_hidden_when_none_archived(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """When no accounts are archived, the section does not render."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Archived Accounts" not in resp.data

    def test_archived_account_shows_in_archived_section(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived account card appears in the collapsed section with
        its name and an unarchive button.
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Closed Savings",
                    anchor_balance=Decimal("0.00"),
                ),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The archived collapse div starts at id="archivedAccounts".
            # Split on the id attribute to get content after it.
            archived_section = html.split('id="archivedAccounts"')[1]
            assert "Closed Savings" in archived_section
            assert "unarchive" in archived_section

    def test_each_archived_card_links_to_its_OWN_edit_page(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Finding **N-430**, closed by plan step ``balance:X-f3c-2b-2d``.

        The archived card emitted no ``href`` naming its account at all, so the
        account edit page -- the surface carrying rename, re-type, hard-delete
        and the books-opening restatement form, but NOT archive, which lives
        only on the live cell's kebab -- was reachable for an archived account
        only by typing its URL.  It carries the live cell's kebab *Edit*
        item now, promoted to the card face by the same rule that put
        hard-delete there.

        **TWO accounts, because one cannot grade a per-card link.**  The drawer
        is a loop, and every other case in this class archives a single account
        -- so a template emitting the FIRST row's id and name on every card
        would pass all of them.  That is also the whole content of the
        accessible name: N cards otherwise give a screen reader N identical
        "Edit" entries with nothing to tell them apart, and the visible word
        leads the label (WCAG 2.5.3 Label in Name) so voice control still
        matches it.

        The ``href`` is matched WHOLE, closing quote included: a
        ``#books-opening`` fragment on this link would name an anchor a loan's
        edit page does not render, and a substring match on the path would pass
        with it there.
        """
        with app.app_context():
            first = create_account_of_type(
                seed_user, db.session, "Savings", "Closed Alpha",
            )
            second = create_account_of_type(
                seed_user, db.session, "Savings", "Closed Beta",
            )
            first.is_active = False
            second.is_active = False
            db.session.commit()
            first_id, second_id = first.id, second.id
            # The ids must DIFFER, or the two assertions below are one.
            assert first_id != second_id

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            archived_section = resp.data.decode().split(
                'id="archivedAccounts"',
            )[1]

            assert f'href="/accounts/{first_id}/edit"' in archived_section
            assert f'href="/accounts/{second_id}/edit"' in archived_section
            assert 'aria-label="Edit Closed Alpha"' in archived_section
            assert 'aria-label="Edit Closed Beta"' in archived_section
            # The VISIBLE word, which is the half of WCAG 2.5.3 the two
            # assertions above cannot see: they pin the accessible name, and an
            # icon-only link with no text node at all satisfies both while
            # voiding the claim that the visible label LEADS that name.  The
            # docstring above and the template's own comment each state it; this
            # is what grades it.
            assert archived_section.count("</i> Edit") == 2

    def test_an_archived_LOANS_card_links_to_its_edit_page_TOO(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The link is UNGATED, and a loan is the kind that proves it.

        A version of this link that carried the ``#books-opening`` fragment
        would have to ask whether the account has a books-opening card and be
        withheld from an amortizing one, whose edit page renders none -- and
        that gate would take an archived loan's ONLY reach to the page that can
        rename or re-type it, on a cockpit that badges a paid-off loan and
        offers Archive on every live cell's kebab.  The plain *Edit* link needs
        no such question, so this case asserts the loan is offered it.

        Its destination is graded where it belongs: that a loan's edit page
        carries no books-opening card is
        ``test_books_opening.py::TestTheCardIsRendered::
        test_a_LOAN_edit_page_carries_no_card``, and that the page itself
        answers is asserted here.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, db.session, "Mortgage", "Closed Mortgage",
                anchor_balance=Decimal("-1000.00"),
            )
            loan.is_active = False
            db.session.commit()
            loan_id = loan.id

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            archived_section = resp.data.decode().split(
                'id="archivedAccounts"',
            )[1]

            assert "Closed Mortgage" in archived_section
            assert f'href="/accounts/{loan_id}/edit"' in archived_section
            assert 'aria-label="Edit Closed Mortgage"' in archived_section

            landed = auth_client.get(f"/accounts/{loan_id}/edit")
            assert landed.status_code == 200

    def test_unarchive_from_dashboard(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST unarchive returns the account to active state."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Restore Me",
                    anchor_balance=Decimal("0"),
                ),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()
            acct_id = archived.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/unarchive",
                follow_redirects=False,
            )
            assert resp.status_code == 302

            refreshed = db.session.get(Account, acct_id)
            assert refreshed.is_active is True

    def test_active_cards_have_archive_button(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Each active account card includes an archive action button."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "bi-archive" in html
            assert f"/accounts/{seed_user['account'].id}/archive" in html

    def test_paid_off_shows_archive_prompt(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Paid-off loan card shows the 'Paid Off' badge and an archive action.

        The Net Worth Cockpit rebuild moved archive off the card face into
        the per-card kebab (audit Surface 2 / decision 9); a paid-off loan is
        now signalled by the 'Paid Off' badge, and the archive affordance is
        the kebab's archive form (its action URL asserted below).
        """
        with app.app_context():
            acct = _create_small_loan(seed_user, name="Paid Off Archival")
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("1100.00"),
            )
            # Payoff recorded as a balance true-up to $0.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Paid Off" in html
            assert f"/accounts/{acct.id}/archive" in html

    def test_archived_account_no_projections(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived accounts show last balance, not projected balances."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Old Savings",
                    anchor_balance=Decimal("5000.00"),
                ),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            archived_section = html.split('id="archivedAccounts"')[1]
            assert "Old Savings" in archived_section
            assert "$5,000.00" in archived_section
            assert "Projected" not in archived_section

    def test_mixed_active_and_archived(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two active accounts + one archived: correct separation."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            active_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Active Savings",
                    anchor_balance=Decimal("3000.00"),
                ),
                is_active=True,
            )
            archived_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Archived Savings",
                    anchor_balance=Decimal("1000.00"),
                ),
                is_active=False,
            )
            db.session.add_all([active_acct, archived_acct])
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Active Savings" in html
            assert "Checking" in html
            assert "Archived Accounts (1)" in html
            assert "Archived Savings" in html


# -- Debt Summary Display Tests (Commit 5.12-1) ─────────────────────


class TestDebtSummaryDisplay:
    """Route-level tests for the debt summary card on the dashboard.

    Commit 5.12-1: the dashboard shows aggregate debt metrics and
    DTI ratio when loan accounts exist.
    """

    def test_dashboard_debt_summary_card_rendered(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.12-17: Dashboard shows debt summary when loans exist.

        D6-F fold: the standalone Debt Summary card is retired; its metrics
        now render as the Liabilities group card's footer line, so assert
        against that folded copy.
        """
        with app.app_context():
            _create_small_loan(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Avg rate" in html
            # X-q3 / N-99: the caption says what the date measures -- the
            # payoff of the debts that HAVE a payoff model.
            assert "Loans paid off" in html
            assert "Payoff Strategies" in html

    def test_a_revolving_balance_is_captioned_beside_the_payoff_date(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The rendered footer names the debt the payoff date cannot cover.

        Plan step X-q3, finding N-99.  The date is derived over amortizing
        loans -- the only debts with a payoff model -- so a Credit Card, whose
        forward balance is its cash fold (plan step credit_card:CC-1) with no
        schedule to pay it off, is invisible to it.  The page says both: when the loans
        are paid off, and what that does not speak for.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import (
            create_account_of_type, create_loan_account,
        )

        with app.app_context():
            create_loan_account(seed_user, db.session, name="Auto Loan")
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Loans paid off" in html
            assert "excludes" in html
            assert "$500.00" in html
            # Captioned by WHY it is left out since ruling R-CC68 (plan step
            # credit_card:CC-5-5c); it read "revolving" until then, which a
            # loan with no terms and a custom liability are not.
            assert "with no payoff date" in html

    def test_a_borrower_whose_loans_are_all_retired_is_told_so(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The footer's THIRD state renders: "All loans paid off".

        Plan step X-s3, ruling R-BE, finding N-104.  The seam's
        :class:`~app.services.savings_dashboard_service.LoanPayoffOutlook` has
        three states and the footer rendered two: a date, or "no payoff date at
        current payments".  The third -- every loan the borrower holds is
        retired -- fell through the chain in SILENCE, because the debt summary
        copied the outlook's two stored fields and dropped its derived
        ``is_loan_free``, so the template had nothing to branch on.  The
        summary now carries the outlook whole and this is the state's first
        reader anywhere in ``app/``.

        The loan is retired the way the app's own true-up UI retires one -- a
        recorded balance true-up to ``$0.00`` with no payment rows -- which is
        the shape that reads ``is_retired`` without ``is_paid_off`` (finding
        B-16's fixture, reused deliberately).  The two states this must NOT be
        confused with are asserted absent: a payoff DATE, and the unclearing
        warning.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import insert_trueup_event, loan_params_for

        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Only Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            db.session.commit()

            # The precondition that makes the fixture discriminating: the loan
            # owes nothing and is NOT badged, so it is retired without being
            # "paid off" in the confirmed-payment sense.
            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.is_retired is True
            assert figures.is_paid_off is False

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The footer is present at all (this user HAS a loan account), and
            # it renders the congratulation rather than nothing.
            assert "Avg rate" in html
            assert "All loans paid off" in html
            # And not either of the other two states.
            assert "Loans paid off " not in html
            assert "No loan payoff date at current payments" not in html

    def test_the_loan_cell_renders_its_rate_payment_and_payoff(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The three loan-cell figures reach the page off the seam's bundle.

        Plan step X-r moved the projection dict from six flattened copies of
        ``LoanFigures`` to the value object itself, and four cockpit sites now
        read through it.  This pins three of them; the fourth, the "Paid Off"
        badge, is already covered by ``TestPaidOffBadge``.

        **What the Jinja risk actually is, measured rather than assumed.**  A
        mis-pointed attribute on the two MONEY sites and the payoff raises
        (``money()`` compares the value, ``to_percent`` constructs a
        ``Decimal``, ``strftime`` is an attribute access on ``Undefined``), so
        those fail loud on their own.  The badge guard is the one site that
        degrades SILENTLY -- ``Undefined`` is falsy, so the badge just stops
        rendering -- and it is the one this test does not need to cover.
        Both halves were confirmed by mutating the template.

        A $1,000.00 24-month auto loan at 5.000%: the rate renders to three
        decimals (the debt footer's own rate uses two, so the assertion cannot
        be satisfied by that one), and the level payment is
        1000 * r(1+r)^24 / ((1+r)^24 - 1) at r = 0.05/12 = $43.87 -- asserted
        glued to the cell's own markup, because with one loan and no escrow
        the footer's monthly total is the SAME number and a bare substring
        would be satisfied by it.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Auto Loan",
                principal=Decimal("1000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(loan, ctx)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "5.000%" in html
            assert (
                'Monthly Payment <span class="font-mono">$43.87</span>' in html
            )
            # The payoff month is read back off the seam deliberately: this
            # module's clock is NOT frozen, and the fold's zero crossing moves
            # with it (an installment already due and unpaid pushes it out).
            # The DERIVATION is oracle-tested at
            # ``test_loan_payoff_date_oracle.py``; what is asserted here is
            # that the cell renders it, and the string "payoff <Mon YYYY>"
            # appears nowhere else on the page.
            assert figures.payoff_date is not None
            assert (
                "payoff " + figures.payoff_date.strftime("%b %Y")
            ) in html

    def test_dashboard_no_debt_summary_when_no_loans(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.12-19: No debt summary when no loan accounts exist.

        D6-F fold: the negative case now checks the same folded-footer
        marker ("Avg rate") the positive test asserts, since the standalone
        "Debt Summary" heading no longer exists.
        """
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Avg rate" not in html

    def test_dashboard_dti_badge_rendered(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.12-18: DTI badge appears when loans and salary exist."""
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("3000.00"))  # $78,000.00 a year / 26
            _create_small_loan(seed_user)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "DTI" in html
            # Small loan relative to $78K salary -> "Healthy" badge
            assert "Healthy" in html

    def test_dashboard_dti_no_salary_shows_na(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """DTI shows N/A when no salary profile configured."""
        with app.app_context():
            _create_small_loan(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "N/A" in html or "no salary profile" in html
