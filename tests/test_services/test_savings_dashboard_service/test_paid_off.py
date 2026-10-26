"""Savings dashboard service tests: the paid-off flag, read from the ledger, and archived
accounts.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.models.ref import AccountType
from app.services import savings_dashboard_service
from app.services import account_service
from app.services.balance_at import BalanceContext

from app.models.amount_ownership import AmountOwnership

from ._helpers import _create_small_loan


class TestPaidOffFlag:
    """Tests for the is_paid_off flag in account data.

    Commit 5.9-2: the savings dashboard service determines whether a
    loan is paid off by replaying only confirmed (Paid/Settled) payments
    through the amortization engine.  Projected payments are excluded.
    """

    def test_paid_off_true_when_confirmed_covers_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Confirmed payment covering the full balance sets is_paid_off=True.

        A $1,000 loan at 5% for 12 months.  A single confirmed payment
        of $1,100 exceeds principal + first-month interest (~$1,004.17).
        The engine's overpayment guard caps the payment at the remaining
        balance + interest, resulting in remaining_balance = $0.00.
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import TransferSpec, create_transfer, settle_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            payment = create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            # Created Projected, then settled: the create makes no
            # settled transfer (ruling R-BAL379).
            settle_transfer(payment.id, seed_user["user"].id)
            db.session.commit()

            # Under the contractual-schedule model a cash lump sum does
            # not auto-pay-off; the operator records the payoff as a
            # balance true-up to $0 (the explicit-event path the user now
            # follows after an extra/lump-sum payment).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is True

    def test_paid_off_false_no_confirmed_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Loan with no payments at all: is_paid_off=False."""
        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is False

    def test_paid_off_false_partial_confirmed_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Partial confirmed payment leaving balance > 0: is_paid_off=False.

        A $500 payment on a $1,000 loan leaves ~$504 (principal minus
        payment plus interest).
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import TransferSpec, create_transfer, settle_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            payment = create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("500.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            # Created Projected, then settled: the create makes no
            # settled transfer (ruling R-BAL379).
            settle_transfer(payment.id, seed_user["user"].id)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is False

    def test_paid_off_false_projected_only(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected payment that would pay off the loan: is_paid_off=False.

        The critical semantic test -- projections do not equal payoff.
        A projected transfer of $1,100 covers the full balance, but
        since it has Projected status (is_settled=False), the paid-off
        flag must remain False.
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import TransferSpec, create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is False

    def test_paid_off_false_for_non_loan_account(
        self, app, db, seed_user, seed_periods,
    ):
        """Non-loan accounts (checking, savings) have is_paid_off=False."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            # The seed user's checking account is non-amortizing.
            checking_ad = next(
                ad for ad in result["account_data"]
                if ad.account.name == "Checking"
            )
            assert checking_ad.loan is None

    def test_paid_off_false_no_loan_params(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan-TYPE account with no LoanParams carries no figures, no crash.

        Since plan step X-r the projection dict carries the seam's
        ``LoanFigures`` whole rather than five flattened copies of its fields,
        so "not a configured loan" is the bundle's ABSENCE -- which is what
        the tile, the debt summary and the debt-line selection all gate on.
        """
        with app.app_context():
            loan_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan").one()
            )
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=loan_type.id,
                    name="No Params Loan",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            assert loan_ad.loan is None


class TestPaidOffReadsTheLedgerNotTheReplay:
    """``is_paid_off`` is a LEDGER read, not a schedule replay.

    The flag used to be answered by ``resolve_loan(inputs, date.max)`` with no
    ``confirmed_view`` -- a producer that structurally cannot consult the genesis
    ledger (the confirmed view returns ``None`` for any ``as_of`` after
    today) and that is BLIND TO MONEY: the replay advances one SCHEDULED step per
    confirmed payment and discards the cash actually paid.

    Each test here pins the fix by ALSO evaluating that retired producer, so the
    assertion is non-vacuous in both directions: the replay genuinely disagrees
    with the ledger on this data, and the app now follows the ledger.
    """

    def _replay_says_paid_off(self, acct, scenario_id):
        """Return what the RETIRED date.max replay probe would have answered.

        The exact question the old ``_loan_ever_paid_off`` asked: no
        ``confirmed_view`` (so no ledger), no ``extra_principal``,
        ``as_of=date.max``.  Assembled by
        :func:`tests._test_helpers.unseeded_replay_balance`, the suite's ONE
        anchor replay, which is AMOUNT-FREE since plan step
        **balance:X-bl-2b** -- this probe reads three dates per payment and no
        figure, so it never loads the tier that prices one.
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import unseeded_replay_balance

        return unseeded_replay_balance(
            acct.id, scenario_id, date.max,
        ) == Decimal("0.00")

    def test_off_schedule_payoff_needs_no_trueup_band_aid(
        self, app, db, seed_user, seed_periods_today,
    ):
        """One lump-sum settled payment retires the loan -- no true-up required.

        A $1,000 / 24-month loan whose scheduled principal is ~$40/mo, retired by
        a SINGLE settled $1,100 payment.  The genesis ledger books the real
        principal, so it reads $0.00 owed and the tile's balance is $0.

        The retired replay takes ONE ~$40 scheduled step and still owes ~$960, so
        it answered "not paid off" -- which is why the app previously required the
        operator to record a manual balance true-up to $0 after any lump-sum
        payoff (a band-aid for a producer that could not see the cash).  Reading
        the ledger removes that requirement: the payment alone is enough.
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_transfer

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            # The production settle chokepoint, so the payment posts its REAL
            # split to the genesis ledger exactly as a live settle does.  Settled
            # on the current period's start (a past date), so it is visible today
            # under C2's settled-date clock regardless of the UTC/display offset.
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], acct,
                seed_periods_today[4], amount=Decimal("1100.00"),
                settled_on=seed_periods_today[4].start_date,
            )
            db.session.commit()

            # NON-VACUITY: the retired producer disagrees on exactly this data.
            assert self._replay_says_paid_off(
                acct, seed_user["scenario"].id,
            ) is False, (
                "fixture regressed: the replay must still owe here, or this "
                "test no longer pins the ledger-vs-replay divergence"
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            # The ledger booked the real principal: nothing is owed.
            assert loan_ad.current_balance == Decimal("0.00")
            assert loan_ad.loan.figures.is_paid_off is True

    def test_short_paid_loan_never_vanishes_from_total_debt(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan paid SHORT stays in total_debt, even when the replay hits zero.

        The dangerous inverse.  A $1,000 / 2-month loan's scheduled payment is
        ~$502, so TWO confirmed payments exhaust the replay's term and drive its
        balance to $0.00 -- regardless of how little cash was actually paid.  Here
        each payment is only $100, so the ledger (which books the REAL principal)
        still owes several hundred dollars.

        Under the retired probe that made ``is_paid_off`` True, and
        ``_metrics._loan_current_balance`` drops a paid-off loan from
        ``total_debt`` -- so real, still-owed debt silently vanished from the debt
        card and its full original principal counted as paid.  The ledger read
        keeps the loan on the books.
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_transfer

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session, term=2)
            for idx in (3, 4):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], acct,
                    seed_periods_today[idx], amount=Decimal("100.00"),
                )
            db.session.commit()

            # NON-VACUITY: the retired producer really did call this paid off.
            assert self._replay_says_paid_off(
                acct, seed_user["scenario"].id,
            ) is True, (
                "fixture regressed: the replay must reach zero here, or this "
                "test no longer pins the debt-vanishing regression"
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            # The ledger still owes, so the loan is NOT paid off ...  The
            # tile's ``current_balance`` is HELD (negative when owed) since
            # plan step credit_card:CC-5-5c, so what the loan owes is read
            # through ``owed``, on both lines that ask it.
            assert loan_ad.owed > Decimal("0.00")
            assert loan_ad.loan.figures.is_paid_off is False
            # ... and it therefore still counts toward the debt card's total.
            assert result["debt_summary"] is not None
            assert (
                result["debt_summary"].total_debt
                >= loan_ad.owed
            )


class TestArchivedAccounts:
    """Tests for archived account loading in the dashboard service.

    Commit 5.9-3: archived accounts (is_active=False) are loaded
    separately with minimal data and no projections.
    """

    def test_archived_accounts_returned(
        self, app, db, seed_user, seed_periods,
    ):
        """Archived accounts appear in the archived_accounts key."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Old Savings",
                    anchor_balance=Decimal("2000.00"),
                ),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert "archived_accounts" in result
            assert len(result["archived_accounts"]) == 1
            assert result["archived_accounts"][0].account.name == "Old Savings"

    def test_archived_excluded_from_active(
        self, app, db, seed_user, seed_periods,
    ):
        """Archived account does not appear in account_data."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
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

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            active_names = [
                ad.account.name for ad in result["account_data"]
            ]
            assert "Hidden Savings" not in active_names

    def test_no_archived_returns_empty_list(
        self, app, db, seed_user, seed_periods,
    ):
        """No archived accounts yields an empty list."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert result["archived_accounts"] == []

    def test_archived_has_balance_only(
        self, app, db, seed_user, seed_periods,
    ):
        """An archived row carries the last anchor balance and nothing else.

        The field is ``last_anchor_balance`` since plan step X-w2 (ruling
        R-CH): it is the ``current_anchor_balance`` COLUMN, not the
        seam-derived balance the live tiles call ``current_balance``, and an
        archived account gets no seam read at all.  The negative arms pin BOTH
        -- the old name must not come back, and no projection field may appear
        on a shape that is deliberately not an ``AccountProjection``.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Archived Savings",
                    anchor_balance=Decimal("3000.00"),
                ),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            archived_row = result["archived_accounts"][0]
            assert archived_row.last_anchor_balance == Decimal("3000.00")
            assert not hasattr(archived_row, "current_balance"), (
                "the archived drawer's figure is the anchor COLUMN, not the "
                "seam-derived balance the live tiles publish under that name"
            )
            assert not hasattr(archived_row, "projected")
