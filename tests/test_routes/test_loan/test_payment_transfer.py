"""Loan route tests: the recurring-transfer prompt on the loan page.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.models.transfer import Transfer

from tests._test_helpers import (
    create_account_of_type,
    make_cadence_rule,
    make_loan_payment_template,
    select_option_values,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY

from ._helpers import _create_loan_account, _payment_template, _create_mortgage


# ── Transfer Prompt Tests (Commit 5.1-3) ─────────────────────────


class TestTransferPrompt:
    """Tests for the recurring payment transfer prompt on the loan dashboard
    and the create_payment_transfer route.
    """

    def test_dashboard_shows_prompt_no_transfer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with LoanParams but no recurring transfer: prompt visible."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html
        assert "Create Recurring Transfer" in html

    def test_dashboard_hides_prompt_transfer_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with active recurring transfer template: prompt hidden."""
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        # Create an active recurring transfer template targeting this account.
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            name="Existing Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()
        # Priced, as every real definition is (the loan page's plan sums this
        # definition's occurrences since plan step R16-b-2 and refuses a
        # series nobody stated).
        state_template_price(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        # And the cadence is COMMITTED before the request (plan step X-i3):
        # ``make_cadence_rule``'s contract is that the caller commits, and a
        # request cannot see a row this test never committed.
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_dashboard_shows_prompt_inactive_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Inactive (archived) transfer template: prompt still shown.

        The user may have deactivated a prior transfer. The prompt
        should reappear so they can create a new one.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            name="Old Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=False,  # Deactivated
        )
        db.session.add(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        # Committed before the request, for the reason the sibling above states.
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html

    def test_create_transfer_success(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with valid source account creates RecurrenceRule + TransferTemplate.

        Redirects to the loan dashboard after successful creation.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

        # Verify records were created.
        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.is_active is True
        assert tpl.from_account_id == checking.id
        assert tpl.recurrence_rule is not None
        assert tpl.default_amount > 0
        # No "amount" override was posted, so the route defaults to the
        # full monthly payment and opts into live derivation: the loan-only
        # derive_from_loan flag lives in the 1:1 loan_payment_settings row
        # (decision B), which the loan route creates on the template (a
        # generic transfer / investment contribution gets no settings row).
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is True

        # Reached through the OWNING relationship (plan step R-F6): the
        # rule names this template back, which is the arc the schema holds.
        assert tpl.recurrence_rule is not None
        assert tpl.recurrence_rule.transfer_template_id == tpl.id

    def test_create_transfer_redirect_hides_prompt(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation, GET dashboard: prompt no longer visible."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        # Create the recurring transfer.
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        # Dashboard should no longer show the prompt.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_create_transfer_generates_shadow_transactions(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation: a live payment INTO the loan for every installment.

        The route fans its new monthly definition out across the owner's pay
        periods (``generate_transfers_for_all_periods``), so each installment
        the schedule holds becomes a transfer into the loan from the savings
        account the case posts from, in the pay period holding its due date.
        The source is a second account the case creates, not the seeded
        checking, so the read tells the account posted from the owner's
        checking.  It was chosen when the seed's owner and its checking were
        both id 1, so a from-side written as the owner's id read as checking
        and passed; since ruling R-BAL356 each non-ref table's ids start in a
        range of their own, and the two differ.

        **Re-expressed onto the TRANSFER at plan step balance:X-bi-6-4d-2**
        (ruling **R-BAL167** class 1).  It counted the live income SHADOW
        rows on the loan account -- a ``budget.transactions`` row of income
        type carrying a ``transfer_id`` -- and a transfer is one row since
        that step (ruling **R-BAL258**), so none can be stored: the income
        side is the transfer's to-side, read off ``budget.transfers``.  The
        name is the twin era's, kept so the node id is the baseline's.

        **The window is the loan's contract, a decided rule.**  The route
        seats its cadence at the loan's first contractual installment
        (``loan_recurrence_sync.loan_cadence_start``; this mortgage
        originated 2023-06-01, due the 1st, so 2023-07-01), and writes every
        installment from there that the saved schedule (2026-01-02 ..
        2026-05-21) holds: 02-01, 03-01, 04-01 and 05-01.  01-01 falls before
        the first payday and 06-01 after the last period.  The read is the
        sorted LIST of each payment's due date and period, not their number
        or their set: a count could not see a start moved inside the
        schedule, and a set could not see two payments in one slot.

        The start bound is also graded, in both directions, in
        ``tests/test_services/test_loan_recurrence_start_bound.py``; its
        EARLIER side is not graded here: the first contractual installment
        (2023-07-01) falls long before the schedule opens (2026-01-02), so a
        cadence started earlier than it writes these same four payments
        here.  Within this file, only this case pins each payment's placement
        in the period holding its due date.
        """
        acct = _create_mortgage(seed_user, db.session)
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings",
        )
        # Committed like the helpers' rows above.  Only a GET or HEAD refuses
        # to start on a transaction that has already written
        # (app/db_transaction.py); this POST would see the row merely flushed.
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(savings.id)},
        )

        template = _payment_template(db.session, seed_user, acct)
        written = (
            db.session.query(Transfer)
            .filter(Transfer.transfer_template_id == template.id)
            .all()
        )
        # Each installment, and the seeded period holding its due date.
        installments = [
            (date(2026, 2, 1), seed_periods[2].id),  # 01-30 .. 02-12
            (date(2026, 3, 1), seed_periods[4].id),  # 02-27 .. 03-12
            (date(2026, 4, 1), seed_periods[6].id),  # 03-27 .. 04-09
            (date(2026, 5, 1), seed_periods[8].id),  # 04-24 .. 05-07
        ]
        assert sorted(
            (xfer.due_date, xfer.pay_period_id) for xfer in written
        ) == installments, (
            "the loan payment route wrote (due date, pay period) "
            f"{sorted((xfer.due_date, xfer.pay_period_id) for xfer in written)}; "
            "it writes one payment for every installment from the loan's first "
            "contractual one on (loan_recurrence_sync.loan_cadence_start), each "
            "in the period holding its due date"
        )
        assert {
            (xfer.from_account_id, xfer.to_account_id, xfer.is_deleted)
            for xfer in written
        } == {(savings.id, acct.id, False)}

    def test_create_transfer_validates_source_ownership(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST with other user's account as source returns 404 (security)."""
        acct = _create_mortgage(seed_user, db.session)
        other_account = seed_second_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(other_account.id)},
        )
        # Should not succeed -- security response rule (404 for not-yours).
        assert resp.status_code == 404

    def test_create_transfer_validates_source_not_self(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with debt account as source: validation error."""
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(acct.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_create_transfer_idor_debt_account(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST to other user's debt account returns 404 (security)."""
        other_loan = _create_loan_account(
            seed_second_user, db.session, AcctTypeEnum.MORTGAGE, "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{other_loan.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 404

    def test_create_transfer_amount_override(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with custom amount: template uses the override amount."""
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "2000.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.default_amount == Decimal("2000.00")
        # Manual mode (typed amount) still gets a loan_payment_settings row,
        # but with derive_from_loan False -- the cash stays the typed base
        # (decision B / decision D).
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is False

    def test_create_transfer_with_extra_principal(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Creating a payment with an extra stores it on settings, not the base.

        The standing extra rides the settings row (spec Sec. 6.3); the
        ``default_amount`` stays the derived base (P&I + escrow), NOT base +
        extra -- the extra is applied live, never baked in.
        """
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "100.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is True
        assert tpl.settings.extra_principal == Decimal("100.00")
        # The base is the derived P&I + escrow, NOT base + extra.
        assert tpl.default_amount > Decimal("100.00")

    def test_update_payment_settings_changes_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The per-definition settings door updates that payment's extra in place.

        The door writes the settings row alone (the extra is a live
        parameter): its ``extra_principal`` is set to the new value.  The 302
        is also the proof that the moved door still ROUTES: the ownership 404s
        below are only meaningful beside it.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 302

        db.session.expire(tpl)
        assert tpl.settings.extra_principal == Decimal("150.00")

    def test_update_payment_settings_unknown_template_404s(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A template id that names none of this loan's payments is a 404.

        The door names the definition it writes (plan step R7d-g-3, ruling
        R-R83); with no recurring payment there is no id to name, and an id
        that names nothing reads the same as one that is not the owner's.
        """
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/999999/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404

    def test_update_payment_settings_refuses_another_loans_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The owner's OWN payment into a different loan is a 404 on this loan's door.

        The template must pay INTO the loan the URL names -- the set the card
        renders a strip for -- so a valid id on the wrong loan writes nothing.
        """
        acct = _create_mortgage(seed_user, db.session)
        other = _create_mortgage(seed_user, db.session, name="Other Mortgage")
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{other.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        other_tpl = _payment_template(db.session, seed_user, other)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{other_tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404
        db.session.expire(other_tpl)
        assert other_tpl.settings.extra_principal == Decimal("0.00")

    def test_update_payment_settings_refuses_an_archived_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An ARCHIVED payment is not in the loan's active set, so its door is a 404."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        tpl = _payment_template(db.session, seed_user, acct)
        resp = auth_client.post(f"/transfers/{tpl.id}/archive")
        assert resp.status_code == 302

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404

    def test_update_payment_settings_rejects_negative_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A negative extra is rejected (danger flash) and never mutates settings."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "50.00",
            },
        )

        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "-5.00"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"valid extra principal" in resp.data.lower()
        # The original extra is untouched.
        db.session.expire(tpl)
        assert tpl.settings.extra_principal == Decimal("50.00")

    def test_update_payment_settings_idor(
        self, second_auth_client, seed_user, db, seed_periods,
    ):
        """A non-owner editing a loan's extra gets a 404 (not-yours == not-found).

        With the owner's real template id in the URL, so the 404 is the
        ownership gate's and not the URL map's -- the sibling
        ``test_update_payment_settings_changes_extra`` proves the same URL
        shape routes (302) for the owner.  The owner's payment is built
        through the ORM rather than the owner's client.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl = make_loan_payment_template(
            db.session, seed_user, acct, amount="1500.00",
        )
        db.session.commit()

        resp = second_auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404
        db.session.expire(tpl)
        assert tpl.settings.extra_principal == Decimal("0.00")

    def test_dashboard_shows_extra_control_when_payment_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The payment card renders the definition's extra control once it exists.

        Prefilled from THAT payment's stored extra ($125.00), posting to its
        own per-definition settings door.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "125.00",
            },
        )
        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings" in html
        assert 'value="125.00"' in html

    def test_source_accounts_exclude_debt_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Source accounts dropdown does not include the current debt account.

        Scopes the assertion to the ``source_account_id`` select, the
        control the case is about.  While every table's ids started at
        1, a naive ``f'value="{acct.id}" not in html`` check could match
        the loan account's id in an unrelated attribute (e.g., the
        dashboard's hidden form carrying ``value="N"`` for another model
        whose id equalled the loan account's), and the OR with ``"No
        recurring payment" not in html`` that papered over it failed
        deterministically when the prompt was shown and the ids
        collided.  Since ruling R-BAL356 each non-ref table's ids start
        in a range of their own, so that collision cannot occur in the
        test database; the scoping stays because the assertion is about
        that one control.
        """
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()

        source_options = select_option_values(html, "source_account_id")
        # When the prompt is shown, ``source_options`` carries the
        # eligible source accounts and the loan account itself must
        # not be among them.  When the prompt is hidden (no source
        # accounts available, e.g. the user has no non-loan
        # accounts), ``source_options`` is the empty list and the
        # assertion is vacuously true.
        assert str(acct.id) not in source_options, (
            f"Loan account {acct.id} ({acct.name}) is listed as a "
            f"source-account option on its own loan dashboard; got "
            f"source_account_id options {source_options!r}"
        )
