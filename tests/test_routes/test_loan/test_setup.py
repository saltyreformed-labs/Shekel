"""Loan route tests: the loan setup door (creating a loan's parameters).

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from html.parser import HTMLParser
from decimal import Decimal

import pytest

from app import ref_cache
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory
from app.models.ref import AccountType
from app.services import account_service

from tests._ledger_reads import posted_loan_balance_at
from tests._test_helpers import (
    create_settled_transfer,
    make_transfer_template,
)

from ._helpers import _create_auto_loan


# ── Setup / Create Params Tests ──────────────────────────────────────


class TestLoanSetup:
    """Tests for initial loan parameter setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_create_params(self, auth_client, seed_user, db, seed_periods, type_name):
        """POST valid params creates LoanParams record."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name=f"Setup {type_name}",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        # DH-#56: the loan's rate now lives in the origination
        # RateHistory row (effective_date == origination_date), not the
        # dropped ``LoanParams.interest_rate`` column.
        origination_rate = (
            db.session.query(RateHistory)
            .filter_by(
                account_id=account.id,
                effective_date=params.origination_date,
            )
            .one()
        )
        assert origination_rate.interest_rate == Decimal("0.05000")
        assert params.term_months == 60

    def _unconfigured_loan_with_a_recurring_transfer(self, seed_user, db):
        """Return ``(account, template)``: an every-paycheck transfer into a NOT-yet-configured Auto Loan.

        The shape ruling **R-R81** names at the setup door: the transfer's
        start is its owner's while the account is not a loan, and becomes
        the contract's the moment it is one.  The transfer is built through
        the generic fixture (no settings row), the way ``POST /transfers``
        leaves one, and committed so the request can see it.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Set Up Later",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.flush()
        template = make_transfer_template(db.session, seed_user, to_account=account)
        db.session.commit()
        return account, template

    def test_setup_after_the_transfer_exists_makes_its_start_the_contracts(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The loan-setup door derives the standing payment's start (plan step R7d-g, ruling **R-R81**).

        Until R7d-g the next chokepoint of any kind healed a start authored
        before the account was a loan; R7d-g-1's review measured that with
        the nine chokepoints gone nothing did until a params edit, so the
        transfer justified occurrences before the loan existed.  Setup is
        where the transfer BECOMES the loan's payment, and it derives the
        start there.  The every-paycheck rule's stored start is the payday of
        the paycheck hosting the first installment (2026-04-01 on this
        origination): the biweekly schedule from 2026-01-02 puts that in the
        paycheck opening 2026-03-27 (six fortnights on), which is what
        ``bind_rule_to_loan`` writes at create; the fixture's default start
        is the schedule's opening payday.
        """
        account, template = self._unconfigured_loan_with_a_recurring_transfer(
            seed_user, db,
        )
        rule = template.recurrence_rule
        owners_start = rule.starts_on
        assert owners_start == date(2026, 1, 2)

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "12000.00",
                "anchor_balance": "12000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "24",
                "origination_date": "2026-03-15",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        assert db.session.query(LoanParams).filter_by(account_id=account.id).one()

        db.session.refresh(rule)
        assert rule.starts_on != owners_start
        assert rule.starts_on == date(2026, 3, 27), (
            "the standing payment's start must be the payday hosting the "
            f"first installment 2026-04-01, got {rule.starts_on}"
        )
        assert rule.end_date is None

    def test_setup_that_would_move_the_start_past_an_authored_stop_is_refused_whole(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The same refusal the params edit makes, at the door where the account becomes a loan.

        The transfer carries an owner's stop of 2026-01-16 (its second
        payday); setting the loan up with a first installment of 2026-04-01
        would lift the start past it.  The write door refuses, the setup is
        rolled back WHOLE -- no ``LoanParams`` row, the rule untouched -- and
        the flash names the transfer.
        """
        account, template = self._unconfigured_loan_with_a_recurring_transfer(
            seed_user, db,
        )
        rule = template.recurrence_rule
        rule.end_date = date(2026, 1, 16)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "12000.00",
                "anchor_balance": "12000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "24",
                "origination_date": "2026-03-15",
                "payment_day": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert template.name in body
        # The refusal's own remedy clause, worded once for every door since
        # plan step R7d-g-2 (ruling R-R85; it read "Archive that transfer
        # first" while the params edit was the only door that could reach it).
        assert "Set it up again without an end date, or archive it." in body
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).one_or_none() is None, "a refused setup left LoanParams behind"
        # The origination rate row is keyed by the account, not the params
        # row, so it is the one artefact a partial rollback could leave.
        assert db.session.query(RateHistory).filter_by(
            account_id=account.id,
        ).count() == 0, "a refused setup left the origination rate behind"
        # The stated balance's tracking-start is staged in the same
        # transaction (plan step R20) and rolls back with the rest.
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        assert db.session.query(_LAE).filter_by(
            account_id=account.id,
        ).count() == 0, "a refused setup left the stated balance behind"
        db.session.refresh(rule)
        assert rule.starts_on == date(2026, 1, 2)
        assert rule.end_date == date(2026, 1, 16)

    def test_create_params_writes_no_anchor_event_and_posts_the_opening(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Setup writes NO origination LoanAnchorEvent; the ledger opening is the record.

        The read switch's final commit retired the origination event write:
        the origination anchor is synthesized from the immutable LoanParams,
        so setup leaves the event table EMPTY, and the worked-out ledger opens
        the loan from it (-original_principal on the loan's own line, so the
        confirmed-balance reader answers the full $30,000 owed).  The
        dashboard must still resolve and render -- the resolver runs on the
        synthesized facts, never a stored row.

        The balance is stated for the ORIGINATION day, so the setup door's
        own write (plan step R20: a ``tracking_start`` for a balance stated
        after origination) has nothing to record either -- the origination
        IS that assertion.  :meth:`test_setup_records_the_stated_balance_as_a_tracking_start`
        is the other half.
        """
        loan_type = (
            db.session.query(AccountType).filter_by(name="Auto Loan").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No-Event Setup Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "30000.00",
                "anchor_date": "2025-01-01",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        # No anchor event row of ANY kind was written.
        from app.models.loan_anchor_event import (  # pylint: disable=import-outside-toplevel
            LoanAnchorEvent,
        )
        assert (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .count()
        ) == 0

        # The genesis OPENING is worked out instead: the loan's lines sum to
        # the full original principal owed in the baseline scenario.
        assert posted_loan_balance_at(
            account.id, seed_user["scenario"].id, date.today(),
        ) == Decimal("30000.00")

        # And the dashboard resolves on the synthesized facts.
        page = auth_client.get(f"/accounts/{account.id}/loan")
        assert page.status_code == 200
        assert b"30,000.00" in page.data

    def test_post_retirement_loan_trueup_lifecycle(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Setup (zero events) then a true-up: the card reads the asserted value.

        The post-retirement lifecycle end-to-end through the ROUTES: a loan
        created with NO stored anchor rows at all (its balance stated for the
        origination day, so the setup door records nothing -- plan step
        R20), then a $25,000 true-up
        asserted through the dashboard form.  The true-up appends the ONE
        ``user_trueup`` event (the source document), the genesis walk books
        its TRUEUP correction so the confirmed-balance reader answers the
        asserted $25,000 (opening -30,000 + correction +5,000, negated), and
        the dashboard card renders it -- proving a loan that never had an
        origination event supports the whole assert-and-display flow.
        """
        loan_type = (
            db.session.query(AccountType).filter_by(name="Auto Loan").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Lifecycle Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "30000.00",
                "anchor_date": "2025-01-01",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/trueup",
            data={
                "anchor_balance": "25000.00",
                "anchor_date": "2025-06-01",
            },
        )
        assert resp.status_code == 302

        from app.models.loan_anchor_event import (  # pylint: disable=import-outside-toplevel
            LoanAnchorEvent,
        )
        events = (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .all()
        )
        # Exactly ONE stored row: the user's assertion.  No origination row.
        assert len(events) == 1
        assert events[0].anchor_balance == Decimal("25000.00")

        # The ledger reconciled to the asserted value...
        assert posted_loan_balance_at(
            account.id, seed_user["scenario"].id, date.today(),
        ) == Decimal("25000.00")
        # ...and the card shows it.
        page = auth_client.get(f"/accounts/{account.id}/loan")
        assert page.status_code == 200
        assert b"25,000.00" in page.data

    def test_create_params_already_configured(self, auth_client, seed_user, db, seed_periods):
        """POST setup when params exist redirects with info flash."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/setup",
            data={
                "original_principal": "99999.00",
                "anchor_balance": "99999.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "0.99",
                "term_months": "12",
                "origination_date": "2025-01-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Loan parameters already configured." in resp2.data

    def test_create_params_term_exceeds_type_max(self, auth_client, seed_user, db, seed_periods):
        """Auto loan rejects term > 120 (type-specific max_term_months)."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Term Test Auto",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "360",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200  # re-renders setup
        assert b"cannot exceed" in resp.data

        count = db.session.query(LoanParams).filter_by(account_id=account.id).count()
        assert count == 0

    def test_mortgage_allows_long_term(self, auth_client, seed_user, db, seed_periods):
        """Mortgage accepts term=360 (max_term_months=600)."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Long Term Mortgage",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "300000.00",
                "anchor_balance": "250000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "6.500",
                "term_months": "360",
                "origination_date": "2023-06-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert params.term_months == 360

    def test_setup_prefills_the_stated_balance_and_its_day(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The setup form opens on the account's anchor balance, stated for today.

        Plan step R20: the "balance today" field prefills from the account's
        latest cash assertion (the $15,000 typed when the account was made)
        and its "as of" date from today, the setup date, bounded above by
        today -- what the form submits unchanged is the door's default.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": "Prepop Auto",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "15000",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b'name="current_principal"' not in resp.data
        # Read input by input (review 7f): a second input of a name ahead of
        # the real box is what a browser posts first and the server reads.
        values, _ = self._inputs(resp.data)
        assert values.get("anchor_balance") == ["15000.00"]
        assert values.get("anchor_date") == ["2026-03-20"]
        assert b'max="2026-03-20" value="2026-03-20"' in resp.data

    def _unconfigured_auto_loan(self, seed_user, db, name, opened_on=None):
        """Return a committed, not-yet-configured Auto Loan account.

        ``opened_on`` is the day its books open (its opening assertion's
        day, today when omitted): a test that records a payment into the
        account before setup passes an earlier one, since the ledger refuses
        a movement dated before the books open.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name=name,
                anchor_balance=Decimal("0"),
                observed_on=opened_on,
            ),
        )
        db.session.add(account)
        db.session.commit()
        return account

    def _stored_anchors(self, db, account):
        """Return ``[(date, balance, source id)]`` for every stored anchor row."""
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        rows = (
            db.session.query(_LAE)
            .filter(_LAE.account_id == account.id)
            .order_by(_LAE.id)
            .all()
        )
        return [(e.anchor_date, e.anchor_balance, e.source_id) for e in rows]

    def test_setup_records_the_stated_balance_as_a_tracking_start(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan originating before the stated day gets ONE tracking_start at that day.

        Plan step R20 (ruling R-R72 part 3, finding REC-519).  A $30,000
        loan originating 2025-01-01, set up on the frozen 2026-03-20 with
        "balance today" $25,000 as of 2026-02-15: the door appends exactly
        one ``tracking_start`` at (2026-02-15, $25,000) in the same
        transaction as the params, the genesis walk resets to it, and
        the dashboard reads $25,000 with the row labelled a tracking start.
        Until R20 the $25,000 landed in ``LoanParams.current_principal`` and
        the loan read as unpaid since 2025 (the R-R71 calendar).
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Mid-life Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-02-15",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        assert self._stored_anchors(db, account) == [(
            date(2026, 2, 15), Decimal("25000.00"),
            ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.TRACKING_START),
        )]
        assert posted_loan_balance_at(
            account.id, seed_user["scenario"].id, date.today(),
        ) == Decimal("25000.00")
        page = auth_client.get(f"/accounts/{account.id}/loan")
        assert page.status_code == 200
        assert b"25,000.00" in page.data
        # The anchors card's row badge, not the tracking-start FORM's label
        # (which every configured loan's page renders).
        assert (
            b'<span class="badge bg-secondary ms-1">Tracking start</span>'
            in page.data
        )

    def test_setup_asserts_nothing_for_a_loan_originating_after_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan not yet originated is configured, and no assertion is written.

        The form still submits its prefilled pair (today, the account's
        anchor), but a loan originating 2026-06-01 has no balance today: the
        origination IS its assertion (plan step R20), and the stated pair --
        necessarily dated before origination -- is not a refusal either,
        since no date on or before today could satisfy the bound.
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Future Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "0.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2026-06-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        assert db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert self._stored_anchors(db, account) == []

    def test_setup_refuses_a_stated_day_before_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A stated day before an ORIGINATED loan's origination is refused whole.

        The origination half of the ``[origination_date, today]`` bound
        (plan step R20), refused the way the two dashboard doors refuse a
        pre-origination date: the form re-renders with the sentence, and
        neither params, rate row nor anchor row is written.
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Refused Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2024-12-31",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200
        assert (
            b"Balance date cannot be before the loan&#39;s origination date "
            b"(2025-01-01)." in resp.data
        )
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert db.session.query(RateHistory).filter_by(
            account_id=account.id,
        ).count() == 0
        assert self._stored_anchors(db, account) == []

    def test_setup_refuses_a_stated_day_after_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The schema refuses a stated day in the future; nothing is written."""
        account = self._unconfigured_auto_loan(seed_user, db, "Future-dated Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-03-21",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert self._stored_anchors(db, account) == []

    def test_setup_requires_the_stated_pair(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A submission carrying only the retired ``current_principal`` field is refused.

        A stale client's field is dropped by the schema's EXCLUDE policy,
        which leaves the required pair missing -- so the door refuses rather
        than configuring a loan whose stated balance it silently lost.
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Stale Client Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert self._stored_anchors(db, account) == []

    @staticmethod
    def _setup_form(anchor_date, origination_date="2025-01-01"):
        """Return a $30,000 auto-loan setup form stating $25,000 as of *anchor_date*."""
        return {
            "original_principal": "30000.00",
            "anchor_balance": "25000.00",
            "anchor_date": anchor_date,
            "interest_rate": "5.000",
            "term_months": "60",
            "origination_date": origination_date,
            "payment_day": "15",
        }

    @pytest.mark.parametrize("stated_on", ["2026-02-27", "2026-03-01"])
    def test_setup_refuses_a_stated_day_on_or_after_a_payment_and_writes_nothing(
        self, auth_client, seed_user, db, seed_periods, stated_on,
    ):
        """Ruling R-R115 ("Same rule at setup"): the setup door refuses as the dashboard's does.

        A $500 payment into the account moves money on Feb 27, 2026 (seed
        period 4's first day), before its loan is set up.  A balance stated
        for that day or for Mar 1 would start the loan's record after a
        payment it holds, so the setup is refused WHOLE: the form re-renders
        asking for an earlier day, and neither params, rate row nor anchor row
        is written.
        """
        account = self._unconfigured_auto_loan(
            seed_user, db, "Paid Before Setup", opened_on=date(2026, 1, 2),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[4], amount=Decimal("500.00"),
            settled_on=date(2026, 2, 27),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=self._setup_form(stated_on),
        )
        assert resp.status_code == 200
        stated = date.fromisoformat(stated_on).strftime("%b %-d, %Y").encode()
        assert (
            b"A payment into this loan already moved money on Feb 27, 2026, "
            b"on or before " + stated in resp.data
        )
        assert (
            b"Enter the balance as of a date after Jan 1, 2025 and before "
            b"Feb 27, 2026." in resp.data
        )
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert db.session.query(RateHistory).filter_by(
            account_id=account.id,
        ).count() == 0
        assert self._stored_anchors(db, account) == []

    def test_setup_records_a_stated_day_before_the_first_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The day before the Feb 27 payment starts the record: one tracking start, Feb 26."""
        account = self._unconfigured_auto_loan(
            seed_user, db, "Stated Before Paying", opened_on=date(2026, 1, 2),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[4], amount=Decimal("500.00"),
            settled_on=date(2026, 2, 27),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=self._setup_form("2026-02-26"),
        )
        assert resp.status_code == 302
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        assert self._stored_anchors(db, account) == [(
            date(2026, 2, 26), Decimal("25000.00"),
            ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.TRACKING_START),
        )]

    def test_setup_does_not_count_a_zero_close(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Ruling R-BAL155 at setup: a $0.00 close moved no money and refuses nothing.

        The account's payment due Mar 15 (the loan's own day, so also the
        installment it would skip) is closed at $0.00 on Feb 27, before
        setup.  A balance stated for Mar 16 -- after the close's settle day,
        its due date and its installment alike -- is recorded as the tracking
        start it is, so no day of the close is counted.
        """
        account = self._unconfigured_auto_loan(
            seed_user, db, "Closed Empty Before Setup", opened_on=date(2026, 1, 2),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[4], amount=Decimal("500.00"),
            settled_amount=Decimal("0.00"), settled_on=date(2026, 2, 27),
            due_date=date(2026, 3, 15),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=self._setup_form("2026-03-16"),
        )
        assert resp.status_code == 302
        assert [
            (anchor_date, balance)
            for anchor_date, balance, _ in self._stored_anchors(db, account)
        ] == [(date(2026, 3, 16), Decimal("25000.00"))]

    @pytest.mark.parametrize(("paid_on", "period_index"), [
        (date(2026, 2, 19), 3),  # before the Feb 27 origination
        (date(2026, 2, 27), 4),  # ON the origination day
        (date(2026, 2, 28), 4),  # the day after: no day falls between them
    ])
    def test_setup_asks_for_the_origination_day_when_no_later_day_comes_before_the_payment(
        self, auth_client, seed_user, db, seed_periods, paid_on, period_index,
    ):
        """A payment before, on, or the day after the origination leaves the origination day.

        $500 moves into the account before its loan is set up with an
        origination of Feb 27 (the payment guard, ruling R-C, has no loan
        terms to compare against until then): on Feb 19, on Feb 27 itself, or
        on Feb 28.  In each case no day falls strictly between the
        origination and the payment, so every stated day that would record a
        tracking start is on or after the payment, and one before the
        origination is refused as pre-origination: the sentence asks for the
        origination day itself -- which states nothing beyond the original
        amount -- and names Record balance for the correction, rather than
        asking for "a date before" the payment, which would send the owner
        to the origination day without saying the stated balance is then
        dropped.  Following it configures the loan with no tracking start.
        """
        account = self._unconfigured_auto_loan(
            seed_user, db, "Paid Around Its Origination", opened_on=date(2026, 1, 2),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[period_index], amount=Decimal("500.00"),
            settled_on=paid_on,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=self._setup_form("2026-03-01", origination_date="2026-02-27"),
        )
        assert resp.status_code == 200
        assert (
            b"No date after the loan&#39;s origination (Feb 27, 2026) comes "
            b"before that payment: enter Feb 27, 2026 as the date" in resp.data
        )
        assert b"Enter the balance as of a date after" not in resp.data
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=self._setup_form("2026-02-27", origination_date="2026-02-27"),
        )
        assert resp.status_code == 302
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 1
        assert self._stored_anchors(db, account) == []

    def test_setup_asks_for_a_day_between_the_origination_and_the_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two days after the origination, one day between them records, and it is asked for.

        The loan originates Feb 27 and $500 moved into the account on Mar 1,
        before setup; a balance stated for Mar 10 is refused.  Feb 28 is the
        one stated day that both follows the origination and precedes the
        payment, so the sentence names BOTH bounds -- the origination day
        satisfies "before Mar 1" alone and records nothing -- and following
        it records one tracking start on Feb 28.
        """
        account = self._unconfigured_auto_loan(
            seed_user, db, "Paid Two Days In", opened_on=date(2026, 1, 2),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[4], amount=Decimal("500.00"),
            settled_on=date(2026, 3, 1),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=self._setup_form("2026-03-10", origination_date="2026-02-27"),
        )
        assert resp.status_code == 200
        assert (
            b"Enter the balance as of a date after Feb 27, 2026 and before "
            b"Mar 1, 2026." in resp.data
        )
        assert b"No date after the loan" not in resp.data
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert db.session.query(RateHistory).filter_by(
            account_id=account.id,
        ).count() == 0

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=self._setup_form("2026-02-28", origination_date="2026-02-27"),
        )
        assert resp.status_code == 302
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        assert self._stored_anchors(db, account) == [(
            date(2026, 2, 28), Decimal("25000.00"),
            ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.TRACKING_START),
        )]

    @staticmethod
    def _inputs(page):
        """Return ``({name: [values]}, {checked names})`` for every ``<input>`` on *page*.

        Each value is read off its OWN input, so a value that came back in
        the wrong box does not count as coming back; and EVERY input of a
        name is kept, in page order, because a browser posts them all and
        the server reads the FIRST (review 7e: keeping only the last let a
        hidden duplicate ahead of the real box pass).
        """
        class _Inputs(HTMLParser):
            """Collect every input's name, value and checked flag."""

            def __init__(self):
                super().__init__()
                self.values, self.checked = {}, set()

            def handle_starttag(self, tag, attrs):
                """Record one ``<input>``."""
                if tag != "input":
                    return
                named = dict(attrs)
                if "name" in named:
                    self.values.setdefault(named["name"], []).append(
                        named.get("value", ""),
                    )
                    if "checked" in named:
                        self.checked.add(named["name"])

        parser = _Inputs()
        parser.feed(page.decode())
        return parser.values, parser.checked

    @pytest.mark.parametrize(("stated_on", "term", "refusal"), [
        # The schema's: a stated day after today (2026-03-20).
        ("2026-03-21", "60", b"Please correct the highlighted errors"),
        # The term cap's: over the Auto Loan type's 120 months.
        ("2026-03-01", "121", b"Term cannot exceed 120 months"),
        # The pre-origination one: a stated day before the origination.
        ("2024-12-31", "60", b"Balance date cannot be before the loan"),
        # The tracking-start one (ruling R-R115): on or after the Feb 27
        # payment.
        ("2026-03-01", "60",
         b"A payment into this loan already moved money on Feb 27"),
    ])
    def test_a_refused_setup_comes_back_as_typed(
        self, auth_client, seed_user, db, seed_periods, stated_on, term, refusal,
    ):
        """Every refusal the form itself can remedy re-renders every field as typed.

        Review 7c of plan step recurrence:R16-c-2: the refused form came back
        with the first showing's defaults -- the account's own balance under
        "Balance today", today's date, a blank principal, origination, rate
        and term, payment day 1, the ARM box clear -- so following a refusal
        by changing only the date re-submitted a prefilled balance (a
        prefilled 0.00 configured a loan owing $0.00).  Each value is read off
        its own input (review 7d: a value echoed into the wrong box must not
        pass), and each typed value differs from its default, so an echo of
        the defaults fails.  The answers that redirect instead are
        render_loan_setup's docstring's to list, not repeated here.
        """
        account = self._unconfigured_auto_loan(
            seed_user, db, "Refused As Typed", opened_on=date(2026, 1, 2),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[4], amount=Decimal("500.00"),
            settled_on=date(2026, 2, 27),
        )
        db.session.commit()
        typed = {
            **self._setup_form(stated_on), "term_months": term, "is_arm": "true",
        }

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup", data=typed,
        )
        assert resp.status_code == 200
        assert refusal in resp.data
        values, checked = self._inputs(resp.data)
        assert {field: values.get(field) for field in typed} == {
            field: [value] for field, value in typed.items()
        }
        assert "is_arm" in checked
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0

    def test_a_refused_setup_leaves_an_arm_box_posted_false_unchecked(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Only the box's own posted value checks it on the way back (review 7d).

        The form's box posts ``true`` or nothing; a hand-built ``is_arm=false``
        is not ARM, and echoing it by its string's truthiness re-rendered the
        box checked, so a re-submit would have made the loan ARM.
        """
        account = self._unconfigured_auto_loan(
            seed_user, db, "Posted False", opened_on=date(2026, 1, 2),
        )
        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={**self._setup_form("2026-03-21"), "is_arm": "false"},
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data
        values, checked = self._inputs(resp.data)
        # The as-typed render, not the first showing's (which never checks
        # the box either): the refused day came back.
        assert values.get("anchor_date") == ["2026-03-21"]
        assert values.get("is_arm") == ["true"]
        assert "is_arm" not in checked
