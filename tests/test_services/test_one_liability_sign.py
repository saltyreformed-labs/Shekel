"""Tests for plan step credit_card:CC-5-5c -- ONE sign for every balance.

Ruling **R-CC47** ("A: one sign"): every balance is what the account HOLDS,
negative when owed -- a configured loan included, whose seam arms reported the
owed figure until this step -- and what an account owes is
:func:`app.services.liability_sign.owed` of it.  Net worth is the plain sum of
every balance; the liability surfaces read ``owed()`` where they took ``abs()``
(ledger row **CC-354**, ruling **R-CC48** for the group subtotal); every loan
screen and liability tile shows what is owed.  Ruling **R-CC67** (with R-CC53,
ledger row CC-362) puts an archived debt's figure on the same calculation, and
ruling **R-CC68** (ledger row CC-361) captions the footer's figure "with no
payoff date".

New module rather than an append to ``test_savings_dashboard_service.py`` or
``test_balance_at.py``: concurrent lanes add to those files too, and two
end-of-file appends conflict (the coordinator's instruction, 2026-09-23).
"""

from datetime import date
from decimal import Decimal

from app.services import balance_at, savings_dashboard_service
from app.services.balance_at import BalanceContext
from app.services.savings_dashboard_service._types import (
    ArchivedAccount,
    ArchivedDebt,
)
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_cash_transaction,
)


def _car_loan(seed_user, session):
    """Create the configured loan every case here stands beside, and return it."""
    return create_loan_account(
        seed_user, session, name="Car Loan",
        principal=Decimal("12000.00"), rate=Decimal("0.05000"),
        term=24, origination_date=date(2026, 1, 1),
    )


def _owed_today(loan, ctx):
    """What the loan owes at the pass's day, from the loan domain's OWED producer.

    :func:`~app.services.balance_at.positions` is not flipped by this step: it
    states what the loan owes, and the seam's arms report minus it.  Reading it
    here is what makes the held-sign assertions independent of the arm under
    test.
    """
    return balance_at.positions(loan, ctx, [ctx.as_of])[ctx.as_of]


class TestAConfiguredLoanReportsTheHeldSign:
    """The seam's two configured-loan arms report minus what the loan owes."""

    def test_the_scalar_the_dates_and_the_period_map_are_minus_positions(
        self, app, db, seed_user, seed_periods,
    ):
        """``balance_at``, ``balance_map`` and ``build_maps`` negate ``positions``.

        The Car Loan (``$12,000.00`` at 5% over 24 months from 2026-01-01)
        owes a positive figure today; its held balance is minus that, and
        every period of its map is minus the owed map's same period -- the
        date-keyed arm and the period-keyed arm flip identically.
        """
        with app.app_context():
            loan = _car_loan(seed_user, db.session)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            owed_today = _owed_today(loan, ctx)
            assert owed_today > Decimal("0.00")
            assert balance_at.balance_at(loan, ctx, ctx.as_of) == -owed_today

            owed_map = balance_at.positions_period_map(loan, ctx)
            held_map = balance_at.balance_map(loan, ctx)
            batch_map = balance_at.build_maps([loan], ctx)[loan.id]
            assert list(held_map) == list(owed_map)
            assert list(batch_map) == list(owed_map)
            for period_id, owed_in_period in owed_map.items():
                assert held_map[period_id] == -owed_in_period
                assert batch_map[period_id] == -owed_in_period

    def test_the_liability_view_reports_what_the_loan_owes(
        self, app, db, seed_user, seed_periods,
    ):
        """``liability_owed_at_dates`` reads owed: positive for a loan owing.

        Handed the loan's HELD balance as its today figure (what the hero
        sums), it answers what the loan owes at today and at a forward date --
        the positive figures ``positions`` states.
        """
        with app.app_context():
            loan = _car_loan(seed_user, db.session)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            later = date(ctx.as_of.year + 1, ctx.as_of.month, 1)
            owed_by_date = balance_at.positions(loan, ctx, [ctx.as_of, later])

            owed = balance_at.liability_owed_at_dates(
                [loan], ctx, [ctx.as_of, later],
                {loan.id: -owed_by_date[ctx.as_of]},
            )[loan.id]

            assert owed == [owed_by_date[ctx.as_of], owed_by_date[later]]
            assert owed[0] > owed[1] >= Decimal("0.00")


class TestACardHoldingACreditIsACreditOnEveryNetWorthSurface:
    """Ledger row CC-354's oracle: a ``$50.00`` credit reads ``-$50.00`` owed.

    A Credit Card the issuer owes ``$50.00`` holds ``+50.00``.  Beside the
    configured Car Loan, every liability figure on /savings is what the loan
    owes MINUS fifty dollars -- the hero, the trend at today, the Horizon at
    index 0 and the group subtotal the legend prints -- and net worth is fifty
    dollars HIGHER than the loan alone leaves it.  Under ``abs()`` each of them
    read the loan PLUS fifty: net worth low by twice the credit.
    """

    def test_every_liability_figure_counts_the_credit_against_the_debt(
        self, app, db, seed_user, seed_periods,
    ):
        """Hero, trend, Horizon index 0 and subtotal: ``loan owed - 50.00``."""
        with app.app_context():
            loan = _car_loan(seed_user, db.session)
            amex = create_account_of_type(
                seed_user, db.session, "Credit Card", "Amex",
                anchor_balance=Decimal("50.00"),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            loan_owed = _owed_today(loan, ctx)

            data = savings_dashboard_service.compute_dashboard_data(ctx)
            today = data["net_worth"].today
            series = data["net_worth"].series
            horizon = data["net_worth"].horizon

            expected_liabilities = loan_owed - Decimal("50.00")
            assert today.total_liabilities == expected_liabilities
            assert (
                series.composition["liability"][series.current_index]
                == expected_liabilities
            )
            assert horizon["composition"]["liability"][0] == expected_liabilities
            assert data["group_subtotals"]["liability"] == expected_liabilities

            # And FORWARD: the card's fold holds its credit flat (no rows), so
            # at every future Horizon sample the band is what the loan is
            # projected to owe minus fifty -- the splice's projected half,
            # which read the credit as fifty owed under ``abs``.
            future = horizon["dates"][1:]
            assert future, "the Horizon has no future sample to grade"
            loan_forward = balance_at.positions(loan, ctx, list(future))
            assert horizon["composition"]["liability"][1:] == [
                loan_forward[on] - Decimal("50.00") for on in future
            ]

            # Net worth is the plain sum of every held balance: the assets,
            # minus what the loan owes, PLUS the card's fifty-dollar credit.
            assert today.net_worth == (
                today.total_assets - loan_owed + Decimal("50.00")
            )
            assert today.net_worth == sum(
                (ad.current_balance for ad in data["account_data"]),
                Decimal("0.00"),
            )

            by_id = {ad.account.id: ad for ad in data["account_data"]}
            assert by_id[amex.id].current_balance == Decimal("50.00")
            assert by_id[amex.id].shown_balance == Decimal("-50.00")
            assert by_id[loan.id].current_balance == -loan_owed
            assert by_id[loan.id].shown_balance == loan_owed

    def test_a_card_owing_shows_what_it_owes_on_its_tile(
        self, app, db, seed_user, seed_periods,
    ):
        """A Visa holding ``-1,000.00`` shows ``1,000.00`` (R-CC47's own example)."""
        with app.app_context():
            visa = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )

            visa_ad = next(
                ad for ad in data["account_data"] if ad.account.id == visa.id
            )
            assert visa_ad.current_balance == Decimal("-1000.00")
            assert visa_ad.shown_balance == Decimal("1000.00")
            assert visa_ad.owed == Decimal("1000.00")
            assert data["group_subtotals"]["liability"] == Decimal("1000.00")


class TestAnArchivedDebtShowsWhatItOwesToday:
    """Ruling R-CC67 (with R-CC53; ledger row CC-362): an archived debt's figure."""

    def test_an_archived_card_reads_its_balance_today_not_its_last_assertion(
        self, app, db, seed_user, seed_periods,
    ):
        """The ruling's own example: typed as owing ``$1,000.00``, then ``$200.00``.

        The Visa's last assertion is ``-1,000.00``; a ``$200.00`` purchase is
        settled on it after, so it owes ``$1,200.00`` -- which the drawer shows
        as an :class:`ArchivedDebt`, and which the last assertion would have
        read as ``$1,000.00``.
        """
        with app.app_context():
            visa = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
                observed_on=seed_periods[5].start_date,
            )
            db.session.flush()
            create_settled_cash_transaction(
                seed_user, seed_periods[7], Decimal("200.00"),
                account=visa, name="Purchase",
            )
            visa.is_active = False
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            rows = savings_dashboard_service.compute_dashboard_data(ctx)[
                "archived_accounts"
            ]

            row = next(r for r in rows if r.account.id == visa.id)
            assert isinstance(row, ArchivedDebt)
            assert row.is_debt
            assert row.owed == Decimal("1200.00")

    def test_an_archived_loan_reads_what_it_owes_and_an_asset_its_assertion(
        self, app, db, seed_user, seed_periods,
    ):
        """A configured loan reads its schedule's owed (R-CC53); an asset is unchanged."""
        with app.app_context():
            loan = _car_loan(seed_user, db.session)
            savings = create_account_of_type(
                seed_user, db.session, "HYSA", "Old Savings",
                anchor_balance=Decimal("3000.00"),
            )
            loan.is_active = False
            savings.is_active = False
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            loan_owed = _owed_today(loan, ctx)

            rows = {
                row.account.id: row
                for row in savings_dashboard_service.compute_dashboard_data(
                    ctx,
                )["archived_accounts"]
            }

            assert isinstance(rows[loan.id], ArchivedDebt)
            assert rows[loan.id].owed == loan_owed
            assert loan_owed > Decimal("0.00")
            assert isinstance(rows[savings.id], ArchivedAccount)
            assert not rows[savings.id].is_debt
            assert rows[savings.id].last_anchor_balance == Decimal("3000.00")


class TestATileTracesWhatADebtOwes:
    """The tile's sparkline and projected caption speak the tile's own words."""

    def test_a_loans_sparkline_falls_as_what_it_owes_falls(
        self, app, db, seed_user, seed_periods,
    ):
        """The Car Loan's series is what it OWES per period: positive, falling.

        Its map is HELD, so drawn raw the same loan would climb toward zero
        while the figure beside it fell (the sparkline is normalised min to
        max by the route).
        """
        with app.app_context():
            loan = _car_loan(seed_user, db.session)
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )

            series = data["sparklines"][loan.id]
            assert all(point > Decimal("0.00") for point in series)
            assert series[0] > series[-1]

    def test_a_cards_projected_caption_is_what_it_is_projected_to_owe(
        self, app, db, seed_user, seed_periods_52,
    ):
        """A Visa holding ``-1,000.00`` projects ``1,000.00`` owed at each horizon.

        On a year of pay periods (``seed_periods_52``), so the tile's horizons
        fall inside the calendar and the caption has a figure to show.
        """
        with app.app_context():
            visa = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()

            visa_ad = next(
                ad for ad in savings_dashboard_service.compute_dashboard_data(
                    BalanceContext.build(seed_user["user"].id),
                )["account_data"]
                if ad.account.id == visa.id
            )

            assert visa_ad.projected, "the card projects no horizon to caption"
            assert visa_ad.shown_projected == {
                label: -held for label, held in visa_ad.projected.items()
            }
            assert set(visa_ad.shown_projected.values()) == {Decimal("1000.00")}
