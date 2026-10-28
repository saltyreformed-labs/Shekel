"""The contribution setup prompt: when it shows and which kind (transfer or deduction), and the
route that creates the contribution transfer: what it writes, and the sources and owners it
refuses.
"""

from datetime import date
from decimal import Decimal

from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import transfer_amount
from tests._test_helpers import create_account_of_type, create_loan_account

from ._helpers import (
    _create_investment_account,
    _create_investment_params,
    _create_other_investment,
    _create_salary_profile,
    _create_deduction,
    _create_transfer_template,
)


class TestContributionPrompt:
    """Tests for the contribution setup prompt on the investment dashboard.

    Verifies prompt visibility rules, prompt type (transfer vs. deduction),
    and the create_contribution_transfer route.
    """

    def test_prompt_shown_ira_no_contribution(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """IRA with params, no transfer or deduction: transfer prompt visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: the alert became the band-foot funding hint with the
        # transfer form behind a collapse (same prompt logic).
        assert "No funding linked to this account" in html
        assert "Set up a recurring transfer" in html
        assert "Create recurring transfer" in html

    def test_prompt_shown_401k_no_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """401(k) with params, no deduction: deduction linkage prompt visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="My 401k", balance="50000.00",
        )
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: deduction-path copy is now the funding hint's
        # "Link a paycheck deduction" line.
        assert "No funding linked to this account" in html
        # With no active profile the deduction path resolves
        # _salary_profile_action="list" -> url_for("salary.cockpit")
        # (/salary), so salary_profile_url is always set and the hint
        # renders the reachable deduction link. This pins the
        # URL-resolution invariant: a broken endpoint name here would 500.
        assert "Link a paycheck deduction" in html
        assert 'href="/salary"' in html

    def test_prompt_hidden_transfer_exists(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """IRA with active recurring transfer: prompt hidden."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        _create_transfer_template(
            db.session, seed_user["user"].id,
            seed_user["account"].id, acct.id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: the prompt is the band-foot funding hint now; its
        # absence pins the hidden state.
        assert "No funding linked to this account" not in html

    def test_prompt_hidden_deduction_linked(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """401(k) with linked deduction: prompt hidden."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="My 401k", balance="50000.00",
        )
        _create_investment_params(db.session, acct.id)
        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: hint copy replaces the deduction alert.
        assert "No funding linked to this account" not in html

    def test_prompt_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Account without InvestmentParams: no prompt shown."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: hint copy replaces both alert variants.
        assert "No funding linked to this account" not in html

    def test_prompt_shown_archived_transfer(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Archived transfer template: prompt still shown."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        _create_transfer_template(
            db.session, seed_user["user"].id,
            seed_user["account"].id, acct.id,
            is_active=False,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: alert copy -> band-foot funding hint.
        assert "No funding linked to this account" in html

    def test_create_transfer_success(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with valid source creates RecurrenceRule + TransferTemplate."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.is_active is True
        assert tpl.from_account_id == checking.id
        assert tpl.default_amount == Decimal("269.23")
        assert tpl.recurrence_rule is not None

        # Reached through the OWNING relationship (plan step R-F6); a
        # second fetch by id would re-assert what the line above says.
        assert tpl.recurrence_rule.transfer_template_id == tpl.id

    def test_create_transfer_generates_a_transfer_in_every_period(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """After creation: a live transfer INTO the account in every pay period.

        The route fans its new definition out across the owner's pay periods
        (``generate_transfers_for_all_periods``), so the money it plans
        arrives on the investment account as transfers whose to-side it is:
        one in each period, each from the savings account the case posts
        from, at the $269.23 it posts.  The source is a second account the
        case creates, not the seeded checking, so the read tells the account
        posted from the owner's checking.  It was chosen when the seed's owner
        and its checking were both id 1, so a from-side written as the owner's
        id read as checking and passed; since ruling R-BAL356 each non-ref
        table's ids start in a range of their own, and the two differ.

        **Re-expressed onto the TRANSFER at plan step balance:X-bi-6-4d-2**
        (ruling **R-BAL167** class 1).  It counted the live income SHADOW
        rows on the investment account -- a ``budget.transactions`` row of
        income type carrying a ``transfer_id`` -- and a transfer is one row
        since that step (ruling **R-BAL258**), so none can be stored: the
        income side is the transfer's to-side, read off ``budget.transfers``.

        **The fan-out's WINDOW is pinned as today's behaviour, not as a
        ruling that it is right.**  The route starts its every-period cadence
        at the opening of the owner's schedule (``PayCalendar.opening_bound``,
        the first payday), so it writes into EVERY period the owner has,
        closed ones included: all ten here, because ``seed_periods_today``
        resets the owner's schedule to exactly those.  Whether it should
        start at the current paycheck instead is open ledger row
        **recurrence:D34**, and a change here MOVES MONEY: started at today
        it wrote six of the ten (measured 2026-10-19), so the four earlier
        periods' 4 x $269.23 = $1,076.92 of planned contributions went.  That
        is the developer's decision, not a test edit (CLAUDE.md rule 5).  The
        read is the sorted LIST of periods, not their number or their set: a
        count says how many periods were written and not which, and a set
        cannot see two transfers in one period.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings",
        )
        # Committed like the helpers' rows above.  Only a GET or HEAD refuses
        # to start on a transaction that has already written
        # (app/db_transaction.py); this POST would see the row merely flushed.
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(savings.id),
                "amount": "269.23",
            },
        )

        template = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .one()
        )
        written = (
            db.session.query(Transfer)
            .filter(Transfer.transfer_template_id == template.id)
            .all()
        )
        assert sorted(xfer.pay_period_id for xfer in written) == sorted(
            period.id for period in seed_periods_today
        ), (
            "the contribution route wrote into pay periods "
            f"{sorted(xfer.pay_period_id for xfer in written)}; it writes one "
            "transfer into every period of the owner's schedule from its first "
            "payday on, closed ones included "
            f"({sorted(period.id for period in seed_periods_today)}).  Where "
            "that window STARTS is open ledger row recurrence:D34: a changed "
            "start MOVES MONEY and is the developer's decision, not a test "
            "edit (CLAUDE.md rule 5).  A period missing or doubled at or after "
            "the start is a cadence or generation regression, not that row"
        )
        assert {
            (
                xfer.from_account_id, xfer.to_account_id,
                transfer_amount(xfer), xfer.is_deleted,
            )
            for xfer in written
        } == {(savings.id, acct.id, Decimal("269.23"), False)}

    def test_create_transfer_redirect_hides_prompt(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """After creation, GET dashboard: prompt no longer visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: hint copy replaces the alert.
        assert "No funding linked to this account" not in html

    def test_create_transfer_validates_source_not_self(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with investment account as source: validation error."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(db.session, acct.id)

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={"source_account_id": str(acct.id), "amount": "100"},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

    def test_create_transfer_rejects_inactive_source(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Inactive source account -> rejected, no transfer wired (B7).

        ``validate_and_resolve_source_account`` refuses to route a
        recurring contribution out of a deactivated account.  The source
        is owned (so ``get_or_404`` passes -- it checks ownership, not
        ``is_active``) but inactive, so the guard redirects with the
        ``Source account is inactive.`` flash.  The load-bearing assertion
        is the money-routing guard: NO ``TransferTemplate`` is created, so
        no transfer is generated into the destination.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]
        checking.is_active = False
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

        # Money-routing guard: an inactive source gets no transfer wired
        # up -- no TransferTemplate against the destination, hence no
        # transfer moves money into it.
        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is None

        # The user-facing reason is surfaced.  The redirect was not
        # followed, so the flash is still unconsumed in the session.
        with auth_client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any(
            "inactive" in message.lower() for _category, message in flashes
        )

    def _post_contribution_from(self, auth_client, seed_user, db, source):
        """POST a contribution transfer funded from *source*; return the response.

        Shared by the two source-kind refusal tests below so the request is
        identical to the success case's and only the source's KIND varies.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(source.id),
                "amount": "269.23",
            },
        )
        return acct, resp

    def _assert_refused_with_flash(
        self, auth_client, seed_user, db, acct, resp, phrase,
    ):
        """The door rolled back, flashed *phrase*, and persisted no template."""
        assert resp.status_code == 302, resp.status_code
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )
        assert (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        ) is None
        with auth_client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any(
            phrase in message for _category, message in flashes
        ), flashes

    def test_create_transfer_from_a_credit_card_flashes_not_500(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A CARD as the funding source is refused with a flash, not a 500.

        The dashboard's "From" picker offers every active account, and plan
        step credit_card:CC-10 refuses a transfer OUT of a card inside
        ``create_transfer`` (design 3.8) -- which the recurrence fan-out this
        door runs reaches.  Found by CC-10's adversarial review: this door
        ran the fan-out bare while ``loan.create_payment_transfer`` translated
        it, so a clean UI action ended in a 500.  Now it rolls back, flashes
        the refusal and persists no template.
        """
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa",
            anchor_balance=Decimal("-500.00"),
        )
        acct, resp = self._post_contribution_from(
            auth_client, seed_user, db, card,
        )
        self._assert_refused_with_flash(
            auth_client, seed_user, db, acct, resp, "out of a credit card",
        )

    def test_create_transfer_from_a_loan_flashes_not_500(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A LOAN as the funding source is refused with a flash, not a 500.

        The same hole the card test above names, for the source kind the
        transfer service has refused since the loan ledger was built: no test
        had ever posted a loan through this door.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Mortgage",
            principal=Decimal("250000.00"), rate=Decimal("0.06000"),
            origination_date=date(2025, 1, 1), term=360,
        )
        acct, resp = self._post_contribution_from(
            auth_client, seed_user, db, loan,
        )
        self._assert_refused_with_flash(
            auth_client, seed_user, db, acct, resp, "out of a loan",
        )

    def test_create_transfer_idor(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """POST to other user's investment account returns 404 (security).

        The source posted is the owner's own checking, which this door
        accepts (``test_create_transfer_success`` posts it and is redirected),
        so the 404 is the destination's ownership check's.  The case posted a
        literal ``"1"``, the seeded checking's id before ruling R-BAL356.
        Since that ruling each non-ref table's ids start in a range of their
        own, so ``1`` names no account, and with the destination's check
        removed the source's own lookup answered the 404.
        """
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(seed_user["account"].id),
                "amount": "100",
            },
        )
        assert resp.status_code == 404

    def test_create_transfer_amount_override(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with custom amount: template uses the override amount."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(db.session, acct.id)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "1000.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.default_amount == Decimal("1000.00")
