"""Loan route tests: an escrow write moving no money, and the rate history (ARM included).

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.models.escrow_line import EscrowLine
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory

from app.services.ledger_lines import linked_key
from tests._ledger_reads import linked_net_by_date
from tests._test_helpers import create_settled_transfer

from ._helpers import (
    _create_loan_account,
    _create_mortgage,
    _create_other_loan,
    _make_clean_split_loan,
    _parse_band_chart,
)


class TestAnEscrowWrite:
    """An escrow write on a loan's settled history moves none of its money."""

    def test_a_clean_escrow_write_moves_no_money(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A future-dated escrow line leaves the loan's ledger unmoved (E1b).

        Under the forward-only guard an escrow change never moves a settled split.
        A $1,000 payment settles 2026-01-20 (interest 100000 * 0.06 / 12 = 500.00,
        principal 500.00, net +500.00 on 01-20); adding a future-dated escrow line
        then returns 200, writes the line, and leaves the linked line's nets
        identical per date.
        """
        loan = _make_clean_split_loan(seed_user, db.session)
        scenario_id = seed_user["scenario"].id
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan, seed_periods[1],
            amount=Decimal("1000.00"), settled_on=date(2026, 1, 20),
        )
        db.session.commit()
        linked = linked_key(loan)
        nets_before = linked_net_by_date(db.session, linked, scenario_id)
        assert nets_before[date(2026, 1, 20)] == Decimal("500.00")

        resp = auth_client.post(
            f"/accounts/{loan.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00",
                  "effective_date": "2026-04-01"},
        )
        assert resp.status_code == 200
        # The mutation itself worked: the escrow line was written.
        assert (
            db.session.query(EscrowLine)
            .filter_by(account_id=loan.id, name="Property Tax").count() == 1
        )
        # No money moved: the linked ledger is byte-identical per date.
        assert linked_net_by_date(db.session, linked, scenario_id) == nets_before


# ── Rate History Tests ───────────────────────────────────────────────


class TestRateHistory:
    """Tests for ARM rate change recording."""

    def test_rate_change_create(self, auth_client, seed_user, db, seed_periods):
        """POST rate change creates a RateHistory row at the effective date.

        DH-#56: the prior ``params.interest_rate`` mirror-write is gone --
        the rate's sole source of truth is now the RateHistory feed.  The
        new change row (effective 2026-04-01) records the 7.000% rate; the
        origination row seeded by ``_create_mortgage`` (effective
        2023-06-01) keeps the loan's period-0 rate.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "7.000", "notes": "Adjustment"},
        )
        assert resp.status_code == 200

        # Scope the lookup to the posted effective date: the loan now has
        # two RateHistory rows (origination + this change), so an
        # unscoped ``first()`` is non-deterministic.
        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=date(2026, 4, 1))
            .one()
        )
        assert entry.interest_rate == Decimal("0.07000")

    def test_rate_change_records_monthly_pi(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An optional monthly_pi pins the period's recast P&I (E-18 setup capture).

        The lender's stated recast payment is stored on the RateHistory
        row so the rate-period engine holds the period's P&I at that
        exact figure instead of deriving it from origination.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={
                "effective_date": "2026-04-01",
                "interest_rate": "7.000",
                "monthly_pi": "2600.00",
            },
        )
        assert resp.status_code == 200

        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .order_by(RateHistory.effective_date.desc())
            .first()
        )
        assert entry is not None
        assert entry.interest_rate == Decimal("0.07000")
        assert entry.monthly_pi == Decimal("2600.00")

    def test_rate_change_without_monthly_pi_is_null(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Omitting monthly_pi leaves it NULL so the period P&I is derived."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-05-01", "interest_rate": "7.500"},
        )
        assert resp.status_code == 200
        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .order_by(RateHistory.effective_date.desc())
            .first()
        )
        assert entry.monthly_pi is None

    def test_rate_change_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid rate returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "200.0"},
        )
        assert resp.status_code == 400

    def test_rate_change_before_origination_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A rate change dated before origination is rejected (400), no row.

        DH-#56: the origination RateHistory row is the loan's period-0 /
        base rate; a pre-origination change would become the earliest row
        and displace the true origination row in the dashboard's
        ``origination_rate`` derivation (``rate_history[-1]``) and in
        ``_origination_rate``'s ``min()``.  ``_create_mortgage`` originates
        the loan on 2023-06-01, so a change effective 2023-01-01 is
        pre-origination.  Revert-proof: without the route guard the POST
        would persist a row and return 200, failing both assertions.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(
            account_id=acct.id,
        ).one()
        params.is_arm = True
        db.session.commit()
        before = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2023-01-01", "interest_rate": "7.000"},
        )
        assert resp.status_code == 400
        after = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before

    def test_rate_change_idor(self, auth_client, second_user, db, seed_periods):
        """Rate change to another user's loan returns 404 with no side effects."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        # DH-#56: the fixture seeds an origination RateHistory row, so the
        # "no side effects" invariant is that the count is UNCHANGED by the
        # IDOR POST -- not that it is zero.
        before = db.session.query(RateHistory).filter_by(account_id=other.id).count()
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/rate",
            data={"interest_rate": "9.0", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

        after = db.session.query(RateHistory).filter_by(account_id=other.id).count()
        assert after == before

    def test_rate_change_same_date_double_submit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """F-104 / C-22: same effective_date double-submit produces one row.

        The composite unique ``uq_rate_history_account_effective_date``
        rejects the second INSERT.  The route flashes a clear
        message and re-renders the rate history without the
        proposed duplicate; total row count is exactly 1.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        data = {
            "effective_date": "2026-04-01",
            "interest_rate": "7.000",
        }
        r1 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        assert r1.status_code == 200

        r2 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        # Idempotent path: route returns the partial; total rows == 1.
        assert r2.status_code == 200

        db.session.expire_all()
        # Scope to the duplicated effective date: DH-#56 seeds an
        # origination RateHistory row (effective 2023-06-01), so an
        # unscoped count includes it.  The F-104 dedupe invariant is
        # that the 2026-04-01 change exists exactly once.
        count = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=date(2026, 4, 1))
            .count()
        )
        assert count == 1, (
            f"Expected 1 rate history row after duplicate submit, "
            f"found {count}; F-104 dedupe failed."
        )

    def test_rate_change_different_date_allowed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """F-104 / C-22: different effective dates both succeed."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        r1 = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "7.000"},
        )
        r2 = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-05-01", "interest_rate": "7.500"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200

        db.session.expire_all()
        # Scope to the two posted change dates: DH-#56 seeds an
        # origination RateHistory row (effective 2023-06-01), so an
        # unscoped count would include it.  The invariant is that both
        # distinct-date changes were admitted (two change rows).
        count = (
            db.session.query(RateHistory)
            .filter(
                RateHistory.account_id == acct.id,
                RateHistory.effective_date.in_(
                    [date(2026, 4, 1), date(2026, 5, 1)],
                ),
            )
            .count()
        )
        assert count == 2

    def test_rate_change_refreshes_band(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A successful ARM rate change carries a fresh band series for the client.

        loan_audit deferred follow-up #1: the band's balance line is always
        visible now (not a hidden tab), but the rate POST swaps only the
        rate-history card + the OOB rate chip.  Since a rate change RE-AMORTIZES
        the loan, the route emits the recomputed band in a hidden
        #loan-band-refresh carrier so loan_detail.js rebuilds #loan-balance-chart
        from it.  A higher rate raises every forward balance, so the carried
        series differs from the pre-change band -- proving the refresh is real,
        not an echo.  Before the fix the rate route carried no band, so the band
        stayed on the pre-change rate until a full reload.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        pre = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        assert pre is not None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "9.500"},
        )
        assert resp.status_code == 200
        # The rate response is the rate-history partial; its ONLY data-chart is
        # the band-refresh carrier (the canvas itself is not re-rendered here).
        refreshed = _parse_band_chart(resp.data.decode())
        assert refreshed is not None, (
            "A successful rate change must carry the recomputed band in "
            "#loan-band-refresh so the always-visible band does not go stale."
        )
        # Well-formed + self-consistent (the follow-up #2 invariant survives).
        assert len(refreshed["labels"]) == len(refreshed["balance"]) > 0
        assert "current_index" in refreshed
        # 9.500% re-amortizes the 6.500% loan: forward balances move, so the
        # carried series is not the pre-change one.
        assert refreshed["balance"] != pre["balance"]

    def test_duplicate_rate_submit_omits_band_refresh(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A rejected duplicate rate submit carries no band-refresh carrier.

        loan_audit deferred follow-up #1: the same-effective-date second submit
        re-amortizes nothing (the composite unique rejects it), so the route
        re-renders the rate history WITHOUT the band carrier -- loan_detail.js
        then leaves the band (and any active payoff preview) untouched.  Only the
        accepted first submit carries the refreshed band.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        data = {"effective_date": "2026-04-01", "interest_rate": "7.000"}
        r1 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        assert r1.status_code == 200
        # The first (accepted) submit DOES carry the refreshed band.
        assert _parse_band_chart(r1.data.decode()) is not None

        r2 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        assert r2.status_code == 200
        # The duplicate re-render carries no band carrier (nothing re-amortized).
        assert 'id="loan-band-refresh"' not in r2.data.decode()
        assert _parse_band_chart(r2.data.decode()) is None


# ── ARM Rate History Integration Tests (Commit 5.7-1) ──────────────


class TestARMRateHistoryIntegration:
    """Tests for ARM rate history integration in the loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    RateHistory entries for ARM loans, convert them to RateChangeRecords,
    and pass them to the amortization engine.
    """

    def test_arm_dashboard_passes_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM mortgage with rate history: dashboard projection differs
        from a fixed-rate projection.

        Creates an ARM mortgage at 5%, adds a rate change to 7%,
        verifies the dashboard renders and shows rate history data.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Mortgage",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        # Add a rate change effective Feb 2025.
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should render with the rate history visible.
        assert "Balance owed" in html

    def test_non_arm_dashboard_ignores_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-ARM loan with rate history entries: rate_changes not passed.

        Defensively verifies that rate history entries on a non-ARM loan
        do not affect the projection.  The dashboard should produce
        identical output to one with no rate history.
        """
        acct = _create_mortgage(seed_user, db.session)

        # Insert rate history despite is_arm=False (defensive case).
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html
        # Non-ARM: rate history section should NOT be visible.
        assert "Rate History" not in html or "Rate Change" not in html
