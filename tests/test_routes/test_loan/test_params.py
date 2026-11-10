"""Loan route tests: updating a loan's parameters, and recording its tracking start.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory
from app.models.ref import AccountType
from app.services import account_service

from tests._ledger_reads import posted_loan_balance_at
from tests._test_helpers import create_settled_transfer

from ._helpers import _TRACKED_FROM, _create_auto_loan, _create_mortgage, _create_other_loan


# ── Update Params Tests ──────────────────────────────────────────────


class TestLoanParamsUpdate:
    """Tests for updating loan parameters."""

    def test_params_update(self, auth_client, seed_user, db, seed_periods):
        """POST valid data updates editable params; a stray ``current_principal`` is ignored.

        Re-pinned for E-18 / Commit 16 (decision D-C) and again for plan step
        R20.  The balance is not a parameter: ``LoanParamsUpdateSchema``'s
        ``unknown = EXCLUDE`` policy silently strips a stray
        ``current_principal`` submission (the column E-18 demoted and R20
        dropped), and the loan's balance -- the ledger's, asserted only
        through the dated true-up form -- survives the POST untouched.  The
        interest_rate percentage-to-decimal conversion is unchanged.

        Hand-check:
        * The fixture states ``$25,000.00`` as the balance via
          ``_create_auto_loan`` (one ``tracking_start``).
        * POSTing ``current_principal=22000.00`` is the silent no-op
          because the schema does not declare the field and
          ``_PARAM_FIELDS`` in :func:`app.routes.loan.update_params`
          does not reference it: no anchor row is written and the posted
          balance still reads ``$25,000.00``.
        * ``interest_rate=4.500`` -> ``Decimal("0.04500")`` via
          ``LoanParamsUpdateSchema``'s ``@pre_load`` hook, which
          dispatches to
          :func:`app.schemas.validation._normalize_percent_fields`
          (Commit 24 / HIGH-06 convention).
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        scenario_id = seed_user["scenario"].id
        events_before = (
            db.session.query(_LAE).filter_by(account_id=acct.id).count()
        )
        assert events_before == 1
        assert posted_loan_balance_at(
            acct.id, scenario_id, date.today(),
        ) == Decimal("25000.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                # Stray ``current_principal`` -- silently dropped by
                # the schema's EXCLUDE policy (E-18 / Commit 16).
                "current_principal": "22000.00",
                "interest_rate": "4.500",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        # DH-#56: the rate form field now edits the loan's ORIGINATION
        # rate -- the route upserts the RateHistory row effective at
        # origination_date (the resolver's period-0 rate), not the
        # dropped ``LoanParams.interest_rate`` column.
        origination_rate = (
            db.session.query(RateHistory)
            .filter_by(
                account_id=acct.id,
                effective_date=params.origination_date,
            )
            .one()
        )
        assert origination_rate.interest_rate == Decimal("0.04500")
        # E-18 / Commit 16 / R20: the stray ``current_principal`` post
        # asserts nothing.  Users edit the displayed balance via the dated
        # true-up form, not this endpoint.
        assert (
            db.session.query(_LAE).filter_by(account_id=acct.id).count()
        ) == events_before
        assert posted_loan_balance_at(
            acct.id, scenario_id, date.today(),
        ) == Decimal("25000.00")

    def test_params_update_validation(self, auth_client, seed_user, db, seed_periods):
        """POST invalid data leaves DB unchanged."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"payment_day": "32"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_term_update_saves(self, auth_client, seed_user, db, seed_periods):
        """POST with valid term_months persists the new value."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "48",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.term_months == 48

    def test_arm_fields_update(self, auth_client, seed_user, db, seed_periods):
        """ARM fields can be toggled on and adjusted."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "1",
                "is_arm": "true",
                "arm_first_adjustment_months": "60",
                "arm_adjustment_interval_months": "12",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.is_arm is True
        assert params.arm_first_adjustment_months == 60

    def test_params_update_idor(self, auth_client, second_user, db, seed_periods):
        """POST to another user's loan params returns 404 (security) and is unchanged."""
        other = _create_other_loan(second_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        orig_day = orig.payment_day
        assert orig_day != 28

        resp = auth_client.post(
            f"/accounts/{other.id}/loan/params",
            data={
                "interest_rate": "0.99",
                "payment_day": "28",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        assert after.payment_day == orig_day

    def test_params_update_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """POST to nonexistent account returns 404 (security)."""
        resp = auth_client.post(
            "/accounts/999999/loan/params",
            data={"interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 404

    def test_params_update_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """POST loan params to checking account returns 404.

        The route's _load_loan_account helper returns None for both
        ownership-failure and wrong-type cases, which both abort 404.
        """
        checking = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking.id}/loan/params",
            data={"interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 404

    def test_amortization_uses_updated_term(self, auth_client, seed_user, db, seed_periods):
        """Changing term_months recalculates amortization on next dashboard load."""
        acct = _create_auto_loan(seed_user, db.session)
        resp1 = auth_client.get(f"/accounts/{acct.id}/loan")

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "36",
            },
        )
        resp2 = auth_client.get(f"/accounts/{acct.id}/loan")

        pattern = rb"Monthly P.{1,6}I.*?\$([0-9,]+\.\d{2})"
        match1 = re.search(pattern, resp1.data, re.DOTALL)
        match2 = re.search(pattern, resp2.data, re.DOTALL)
        assert match1 is not None
        assert match2 is not None
        assert match1.group(1) != match2.group(1)


# ── Loan Balance True-up (E-18 D-C / Commit 16) ──────────────────────


class TestRecordTrackingStartRoute:
    """POST /loan/tracking-start records a mid-life tracking-start (a tracking_start event).

    Every fixture loan here already carries ONE ``tracking_start`` -- the
    balance stated at setup, which the shared helper records the way the
    setup door does (plan step R20) -- so this door's write is the SECOND.
    """

    def _tracking_start_events(self, db_session, account):
        """Return the account's tracking_start LoanAnchorEvents."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        from app.models.loan_anchor_event import (  # pylint: disable=import-outside-toplevel
            LoanAnchorEvent,
        )
        src = ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.TRACKING_START,
        )
        return (
            db_session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id, source_id=src)
            .all()
        )

    def test_records_tracking_start_and_redirects(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A valid tracking-start POST appends one tracking_start event and redirects.

        The auto-loan fixture originated 2025-01-01 and stated $25,000 as of
        ``_TRACKED_FROM``.  POSTing a $20,000 balance as of 2025-06-01
        (after origination, before any payment, before today) appends exactly
        one more tracking_start event with that balance / date and redirects
        to the dashboard.
        """
        acct = _create_auto_loan(seed_user, db.session)
        before = self._tracking_start_events(db.session, acct)
        assert [(e.anchor_date, e.anchor_balance) for e in before] == [
            (_TRACKED_FROM, Decimal("25000.00")),
        ]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2025-06-01", "anchor_balance": "20000.00"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/accounts/{acct.id}/loan")

        db.session.expire_all()
        events = self._tracking_start_events(db.session, acct)
        assert len(events) == 2
        new = [e for e in events if e.id not in {b.id for b in before}]
        assert len(new) == 1
        assert new[0].anchor_balance == Decimal("20000.00")
        assert new[0].anchor_date == date(2025, 6, 1)

    def test_rejects_date_before_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A tracking-start dated before origination is rejected; no event written."""
        acct = _create_auto_loan(seed_user, db.session)
        before = len(self._tracking_start_events(db.session, acct))
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2024-12-01", "anchor_balance": "20000.00"},
        )
        assert resp.status_code == 302
        db.session.expire_all()
        assert len(self._tracking_start_events(db.session, acct)) == before

    def test_refuses_a_date_after_a_recorded_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A tracking-start dated after a recorded payment is refused; nothing is written.

        **Ruling R-R114 ("Decide at the door") flips this test**, which pinned
        the opposite from plan step R20 (it was
        ``test_accepts_a_date_after_a_recorded_payment``): a tracking start
        says where the app's record of the loan STARTS -- what the loan's
        recorded start reads -- and a payment whose money already moved says
        it started earlier (ruling R-BAL155's day), so the statement is a
        balance correction and the flash offers an earlier date or the Record
        balance control that records one.  A $500 payment settles on Jan 2
        (seed period 0's start); a $20,000 tracking-start asserted for today,
        Mar 20, appends nothing and moves no balance.
        """
        acct = _create_auto_loan(seed_user, db.session)
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("500.00"),
            settled_on=date(2026, 1, 2),
        )
        db.session.commit()
        before = len(self._tracking_start_events(db.session, acct))
        balance_before = posted_loan_balance_at(
            acct.id, seed_user["scenario"].id, date.today(),
        )
        # $25,000.00 stated, then $500.00 paid: a month's interest at 5% is
        # 25000.00 * 0.05 / 12 = 104.17, so 395.83 of principal.
        assert balance_before == Decimal("24604.17")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "20000.00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert (
            b"A payment into this loan already moved money on Jan 2, 2026, "
            b"on or before Mar 20, 2026" in resp.data
        )
        assert (
            b"Choose a date before Jan 2, 2026, or use Record balance to "
            b"correct the loan" in resp.data
        )
        db.session.expire_all()
        assert len(self._tracking_start_events(db.session, acct)) == before
        assert posted_loan_balance_at(
            acct.id, seed_user["scenario"].id, date.today(),
        ) == balance_before

    def test_refuses_the_day_a_payment_is_recorded(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Ruling R-R114's "on or before": a tracking start ON the payment's day is refused."""
        acct = _create_auto_loan(seed_user, db.session)
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("500.00"),
            settled_on=date(2026, 1, 2),
        )
        db.session.commit()
        before = len(self._tracking_start_events(db.session, acct))

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2026-01-02", "anchor_balance": "25000.00"},
            follow_redirects=True,
        )
        assert (
            b"A payment into this loan already moved money on Jan 2, 2026, "
            b"on or before Jan 2, 2026" in resp.data
        )
        db.session.expire_all()
        assert len(self._tracking_start_events(db.session, acct)) == before

    def test_accepts_a_date_before_the_first_recorded_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The day before the Jan 2 payment is a start: the event is appended."""
        acct = _create_auto_loan(seed_user, db.session)
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("500.00"),
            settled_on=date(2026, 1, 2),
        )
        db.session.commit()
        before = len(self._tracking_start_events(db.session, acct))

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2025-12-31", "anchor_balance": "25100.00"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/accounts/{acct.id}/loan")
        db.session.expire_all()
        events = self._tracking_start_events(db.session, acct)
        assert len(events) == before + 1
        assert (date(2025, 12, 31), Decimal("25100.00")) in {
            (e.anchor_date, e.anchor_balance) for e in events
        }

    def test_a_zero_close_counts_from_no_day(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A $0.00 close moved no money, so no tracking start after it is refused.

        **The developer's explicit change of behaviour, not a rule-5
        deletion** (ruling **R-BAL155**, answered in the recurrence lane's
        decisions record, s.18: "The loan's tracking-start button stops
        counting $0.00 payments now").  This test was ``test_a_zero_close_counts_from_the_installment_it_skips``,
        which pinned checkpoint 12's reading: a $0.00 close due Mar 20 on a
        loan due the 15th counted from Mar 15, the installment it skips
        (ruling R-R107), so a Mar 17 tracking start was refused naming a day
        nothing was paid.  The same close now counts from no day: a tracking
        start on Mar 20 -- after the installment it skips and ON its due and
        settled days -- is recorded.
        """
        acct = _create_auto_loan(seed_user, db.session)
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[5], amount=Decimal("500.00"),
            settled_amount=Decimal("0.00"),
            settled_on=date(2026, 3, 20), due_date=date(2026, 3, 20),
        )
        db.session.commit()
        before = len(self._tracking_start_events(db.session, acct))

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2026-03-20", "anchor_balance": "20000.00"},
        )
        assert resp.status_code == 302
        db.session.expire_all()
        events = self._tracking_start_events(db.session, acct)
        assert len(events) == before + 1
        assert (date(2026, 3, 20), Decimal("20000.00")) in {
            (e.anchor_date, e.anchor_balance) for e in events
        }

    @pytest.mark.parametrize(
        ("paid_on", "period_index", "offered", "absent", "accepted_on"), [
            # Before the Feb 27 origination, and ON it: no date this door
            # accepts comes before the payment.
            (date(2026, 2, 19), 3,
             b"No tracking start can come before that payment, since the loan "
             b"originated on Feb 27, 2026; use Record balance",
             b"Choose a date before", None),
            (date(2026, 2, 27), 4,
             b"No tracking start can come before that payment, since the loan "
             b"originated on Feb 27, 2026; use Record balance",
             b"Choose a date before", None),
            # The day after: this door records a tracking start ON the
            # origination day, so Feb 27 is still a date it accepts -- and
            # following the flash there records one (the setup door's gap is
            # its own: there the origination day states nothing).
            (date(2026, 2, 28), 4,
             b"Choose a date before Feb 28, 2026, or use Record balance",
             b"No tracking start can come before that payment",
             date(2026, 2, 27)),
        ],
    )
    def test_offers_an_earlier_date_only_when_this_door_accepts_one(
        self, auth_client, seed_user, db, seed_periods,
        paid_on, period_index, offered, absent, accepted_on,
    ):
        """This door offers an earlier date only where it accepts one; else Record balance alone.

        The door accepts dates from the origination on, so where the
        payment's money moved on or before the origination the flash cannot
        ask for an earlier date: it offers Record balance alone.  $500 moves
        into an account before its loan is set up with an origination of Feb
        27 and no stated balance (the payment guard, ruling R-C, has no loan
        terms to compare against until then); a Mar 1 tracking start is
        refused and appends nothing.  A payment the day after the origination
        still leaves the origination day, so that flash offers it, and a
        tracking start posted there is recorded.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Paid Around Its Origination",
                anchor_balance=Decimal("0"),
                observed_on=date(2026, 1, 2),
            ),
        )
        db.session.add(acct)
        db.session.flush()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[period_index], amount=Decimal("500.00"),
            settled_on=paid_on,
        )
        db.session.commit()
        configured = auth_client.post(
            f"/accounts/{acct.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "30000.00",
                "anchor_date": "2026-02-27",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2026-02-27",
                "payment_day": "15",
            },
        )
        assert configured.status_code == 302
        assert self._tracking_start_events(db.session, acct) == []

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2026-03-01", "anchor_balance": "29000.00"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert offered in resp.data
        assert absent not in resp.data
        db.session.expire_all()
        assert self._tracking_start_events(db.session, acct) == []

        if accepted_on is not None:
            followed = auth_client.post(
                f"/accounts/{acct.id}/loan/tracking-start",
                data={
                    "anchor_date": accepted_on.isoformat(),
                    "anchor_balance": "29000.00",
                },
            )
            assert followed.status_code == 302
            db.session.expire_all()
            assert [
                (e.anchor_date, e.anchor_balance)
                for e in self._tracking_start_events(db.session, acct)
            ] == [(accepted_on, Decimal("29000.00"))]

    def test_a_projected_payment_does_not_refuse(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A payment due Feb 15 that has not settled is not recorded: Mar 1 is a start."""
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_transfer
        acct = _create_auto_loan(seed_user, db.session)
        create_transfer(
            seed_user, seed_user["account"], acct,
            seed_periods[3], amount=Decimal("500.00"),
            due_date=date(2026, 2, 15),
        )
        db.session.commit()
        before = len(self._tracking_start_events(db.session, acct))

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2026-03-01", "anchor_balance": "24000.00"},
        )
        assert resp.status_code == 302
        db.session.expire_all()
        assert len(self._tracking_start_events(db.session, acct)) == before + 1
