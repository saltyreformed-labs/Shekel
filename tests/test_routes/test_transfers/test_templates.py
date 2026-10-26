"""Transfer route tests: the template list, the create form and its prefill, creating, updating,
archiving and unarchiving a transfer template.
"""

import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, StatusEnum
from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.recurrence_rule import RecurrenceRule
from app.services.balance_at import BalanceContext
from app.services import transfer_service
from app.services import account_service
from app.utils.dates import display_today
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    all_periods,
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    last_covered_day,
    make_transfer_template,
    open_books_before_the_first_assertion,
)
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import transfer_amount

from ._helpers import (
    _create_savings_account,
    _create_template,
    _create_transfer,
    _create_other_user_with_template,
)


# ── Template Management ───────────────────────────────────────────


class TestTemplateList:
    """Tests for GET /transfers and GET /transfers/new."""

    def test_list_redirects_to_unified_recurring(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /transfers redirects to the unified Recurring surface.

        The standalone transfers list retired when /transfers folded into
        /templates (Loop B); the URL is kept as a redirect for old
        bookmarks and the create/update routes' post-save redirects.
        Following it lands on /templates showing the transfer.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            _create_template(seed_user, savings, with_rule=False)

            response = auth_client.get("/transfers")
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/templates")

            followed = auth_client.get("/transfers", follow_redirects=True)
            assert followed.status_code == 200
            assert b"Monthly Savings" in followed.data

    def test_new_template_form(self, app, auth_client, seed_user):
        """GET /transfers/new renders the creation form."""
        with app.app_context():
            response = auth_client.get("/transfers/new")

            assert response.status_code == 200
            assert b'name="default_amount"' in response.data
            assert b'name="from_account_id"' in response.data
            assert b"New Recurring Transfer" in response.data

    def test_the_create_form_names_the_destinations_that_derive_the_bounds(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The CREATE form carries which destinations lock both bound rows.

        Plan step R7c-b.  A loan payment's first occurrence is the loan's, so
        the control stops being the user's to state the moment a loan is
        chosen as the destination -- and the server cannot know at render
        which the user will choose, so it ships the SET and
        ``recurrence_form.js`` applies it.  ONE set since plan step R7d-g-2
        (ruling **R-R81**): the payment-less loans, which derive both bounds;
        the wider ``data-loan-account-ids`` that locked "Starts on" for every
        loan went with the derivation of a second transfer's start.

        What this can see is the attribute; whether the script actually
        disables the control is ``tests/manual/verify_recurrence_form.py``'s,
        because rendered HTML cannot tell a control a script re-enabled from
        one that was never locked -- the defect class this whole affordance
        belongs to.

        NEGATIVE CONTROL: the savings account below must NOT appear, or the
        attribute is "every account" and the lock would fire on all of them.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Sav",
                anchor_balance=Decimal("100.00"),
            )
            db.session.commit()

            html = auth_client.get("/transfers/new").data.decode()

            assert f'data-loan-account-ids-without-payment="{loan.id}"' in html
            assert 'data-loan-account-ids="' not in html
            assert str(savings.id) not in (
                html.split('data-loan-account-ids-without-payment="')[1]
                .split('"')[0]
            )

    def test_an_edit_form_names_the_destinations_computed_for_this_edit(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """An EDIT form ships the sets too, computed for ITS definition.

        **REVERSED by ruling at plan step R7d-f-5** (developer 2026-09-12,
        ruling **R-R79**; CLAUDE.md rule 5's exception).  Until then this case
        asserted the edit form ships NO set, on the premise that
        ``recurrence.selected_start.locked`` already answers the question and
        a client-side set would be a second answer.  Plan step R7d-f-4 made
        the premise false: the UPDATE door derives the same bounds for a
        repeating transfer MOVED onto a loan, which the server cannot see at
        render, so the form invited a start the save replaces.  The two do not
        answer one question: the server answers the STORED identity and locks
        the standing payment's rows, the script answers the CHOSEN destination
        against the set the server computed for this edit -- the payment-less
        loans, or nothing for a definition the door pins (one set since plan
        step R7d-g-2, ruling **R-R81**).  The census is
        ``tests/test_routes/test_transfer_edit_form_locks.py``; this is the
        representative case: a repeating savings transfer names the loan a
        move onto which derives both bounds.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Sav",
                anchor_balance=Decimal("100.00"),
            )
            template = make_transfer_template(
                db.session, seed_user, to_account=savings,
            )
            db.session.commit()

            html = auth_client.get(
                f"/transfers/{template.id}/edit",
            ).data.decode()

            assert f'data-loan-account-ids-without-payment="{loan.id}"' in html


class TestTemplatePrefill:
    """Tests for GET /transfers/new with pre-filled account query params."""

    def test_new_transfer_prefills_from_account(self, app, auth_client, seed_user):
        """GET /transfers/new?from_account=<id> pre-selects the source account."""
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(f"/transfers/new?from_account={account_id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'value="{account_id}"' in html

    def test_new_transfer_prefills_to_account(self, app, auth_client, seed_user):
        """GET /transfers/new?to_account=<id> pre-selects the destination account."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            resp = auth_client.get(f"/transfers/new?to_account={savings.id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'value="{savings.id}"' in html


class TestTheStartPeriodSelectorComesFromTheDerivation:
    """The create form's pay-period ``<select>`` is answered by ONE calendar.

    Plan step **C2-f3a**.  It read ``pay_period_service.get_all_periods`` for
    its options and ``get_current_period`` to preselect one -- two reads of
    ``budget.pay_periods``, the second SQL whose ``.first()`` carried no
    ``ORDER BY`` (ledger row **P19**) resolved against the process clock (row
    **P49**).  Both are now the one derivation, and the day is the OWNER's.

    ``tests/test_arch/test_one_read_pass_per_render`` counts the derivations;
    these grade what the form actually renders, which a count cannot see.
    """

    def test_the_option_values_are_pay_period_ids(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every option names a row a foreign key can point at.

        The values come off ``DerivedPeriod.period_id`` now, where they came
        off ``PayPeriod.id`` -- the SAME integer, and the case exists because
        the derived type spells it differently: a template rendering ``p.id``
        against a ``DerivedPeriod`` emits an EMPTY value silently, and the
        POST door would then read "no start period" for a form that showed
        one selected.
        """
        with app.app_context():
            expected = [str(period.id) for period in seed_periods_today]

            html = auth_client.get("/transfers/new").data.decode()

        select = html.split('id="start_period_id"', 1)[1].split("</select>", 1)[0]
        rendered = re.findall(r'<option value="([^"]*)"', select)
        assert rendered == expected, (
            "the start-period options do not name this owner's pay periods "
            "in payday order"
        )
        assert "" not in rendered, (
            "an option rendered an empty value, which is what reading ``.id`` "
            "off a DerivedPeriod produces"
        )

    def test_the_preselected_option_is_the_owners_current_paycheck(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The selected option is the period containing the OWNER's day.

        ``seed_periods_today`` puts ``display_today()`` inside period 4 by
        construction, so the assertion names that period rather than
        recomputing the fixture's own rule.
        """
        with app.app_context():
            today = display_today()
            expected = next(
                period for period in seed_periods_today
                if period.start_date <= today <= last_covered_day(period)
            )

            html = auth_client.get("/transfers/new").data.decode()

        select = html.split('id="start_period_id"', 1)[1].split("</select>", 1)[0]
        selected = re.findall(r'<option value="([^"]*)" selected', select)
        assert selected == [str(expected.id)], (
            "the form preselected a paycheck other than the one the owner is "
            "in today"
        )


class TestTemplateCreate:
    """Tests for POST /transfers."""

    def test_create_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers creates a template with recurrence and generates transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            response = auth_client.post("/transfers", data={
                "name": "Weekly Savings",
                "default_amount": "150.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data

            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Weekly Savings")
                .one()
            )
            assert tmpl.default_amount == Decimal("150.00")
            assert tmpl.recurrence_rule is not None

    def test_create_template_validation_error(self, app, auth_client, seed_user):
        """POST /transfers with missing fields shows a validation error."""
        with app.app_context():
            response = auth_client.post("/transfers", data={
                "name": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_template_same_accounts(self, app, auth_client, seed_user):
        """POST /transfers with from == to account shows a validation error."""
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.post("/transfers", data={
                "name": "Self Transfer",
                "default_amount": "100.00",
                "from_account_id": acct_id,
                "to_account_id": acct_id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_recurring_template_from_loan_flashes_not_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A recurring template with a LOAN source flashes gracefully, not a 500.

        The transfer form offers every active account as a source, so a user can
        pick a Mortgage.  A transfer OUT of an amortizing loan is rejected by
        ``create_transfer`` (review R6); on the RECURRING path the recurrence
        engine fans out through that service, so an unhandled rejection would
        500 (the T-1 defect the guard newly made reachable).  The route must
        instead roll back and flash -- exactly as the one-time path does -- and
        persist no template.
        """
        with app.app_context():
            loan = create_loan_account(
                {"user": seed_user["user"], "scenario": seed_user["scenario"]},
                db.session, name="Mortgage",
                principal=Decimal("250000.00"), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), term=360,
            )
            db.session.commit()
            response = auth_client.post("/transfers", data={
                "name": "Drain The Mortgage",
                "default_amount": "100.00",
                "from_account_id": str(loan.id),        # loan as SOURCE
                "to_account_id": str(seed_user["account"].id),
                **cadence_payload(),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            # Graceful: 200 (not 500), the guard's message flashed, no template.
            assert response.status_code == 200
            assert b"Could not create transfer" in response.data
            assert b"out of a loan" in response.data
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Drain The Mortgage")
                .first()
            ) is None

    def test_create_recurring_template_from_card_flashes_not_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A recurring template with a CREDIT CARD source flashes, not a 500.

        The loan pin's twin for plan step credit_card:CC-10 (design 3.8): a
        transfer OUT of a card is a cash advance or balance transfer, refused
        by ``create_transfer`` through the same composed source refusal as
        the loan's, so it reaches this door by the same fan-out and must land
        as the same flash -- rolled back, no template persisted.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            response = auth_client.post("/transfers", data={
                "name": "Cash Advance",
                "default_amount": "100.00",
                "from_account_id": str(card.id),        # card as SOURCE
                "to_account_id": str(seed_user["account"].id),
                **cadence_payload(),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Could not create transfer" in response.data
            assert b"out of a credit card" in response.data
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Cash Advance")
                .first()
            ) is None

    def test_create_template_double_submit(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers twice with the same name returns a flash warning
        on the second attempt instead of a 500 error, and creates exactly
        one template in the database."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            form_data = {
                "name": "Duplicate Transfer",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                **cadence_payload(),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            # -- First submission: succeeds --
            resp1 = auth_client.post("/transfers", data=form_data)
            assert resp1.status_code == 302, (
                f"First submit returned {resp1.status_code}, expected 302"
            )

            # Verify creation via DB.
            template = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Duplicate Transfer",
            ).one()
            assert template.default_amount == Decimal("100.00")

            # Record how many transfers were generated.
            first_submit_transfer_count = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count()
            assert first_submit_transfer_count > 0, (
                "Recurrence should have generated at least one transfer"
            )

            # -- Second submission: duplicate name, handled gracefully --
            resp2 = auth_client.post("/transfers", data=form_data)
            assert resp2.status_code == 302, (
                f"Second submit returned {resp2.status_code}, expected 302 "
                "(not 500 -- IntegrityError should be caught)"
            )

            # Verify redirect target.
            location = resp2.headers.get("Location", "")
            assert "/transfers" in location, (
                f"Redirect went to {location}, expected /transfers list"
            )

            # Follow the redirect chain and verify the flash warning.  The
            # /transfers list URL now forwards to the unified Recurring
            # surface, so the create route's redirect hops once more before
            # rendering; Flask carries the flash across the chain.
            resp3 = auth_client.get(location, follow_redirects=True)
            assert resp3.status_code == 200
            assert b"already exists" in resp3.data, (
                "Flash warning about duplicate name not found in response"
            )

            # -- Verify database state: exactly 1 template, no orphans --
            template_count = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Duplicate Transfer",
            ).count()
            assert template_count == 1, (
                f"Expected exactly 1 template, found {template_count}"
            )

            # Transfer count unchanged (second submit was rolled back).
            final_transfer_count = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count()
            assert final_transfer_count == first_submit_transfer_count, (
                f"Transfer count changed from {first_submit_transfer_count} "
                f"to {final_transfer_count} after rolled-back duplicate"
            )

            # RecurrenceRule count: exactly 1 for this template.
            rule_count = db.session.query(RecurrenceRule).filter_by(
                id=template.recurrence_rule.id,
            ).count()
            assert rule_count == 1, (
                f"Expected 1 recurrence rule, found {rule_count}"
            )

            # Session health check: a subsequent query must not raise
            # InvalidRequestError (proves rollback was effective).
            total_templates = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert total_templates >= 1


class TestTemplateUpdate:
    """Tests for GET/POST /transfers/<id>/edit and /archive and /unarchive."""

    def test_edit_template_form(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/<id>/edit renders the edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            response = auth_client.get(f"/transfers/{template.id}/edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data

    def test_update_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id> updates the template."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            response = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Updated Savings",
                "default_amount": "300.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"updated" in response.data

            db.session.refresh(template)
            assert template.default_amount == Decimal("300.00")

    def test_a_retained_transfer_is_reported_and_the_edit_still_commits(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A row the pass declined to change is TOLD, not put to the owner.

        Plan step R10-b's route half, and the transfer twin of
        ``test_templates.py``'s transaction case.  A retained row has no
        keep-vs-use question -- the pass already took the only safe outcome --
        so ``regenerate_or_conflict_chooser`` must NOT render the chooser over
        it, must flash the notice, and must let the edit commit.  Rendering the
        chooser here would show an empty table and roll the whole edit back,
        which is how an ordinary amount change came to do nothing silently.
        """
        with app.app_context():
            from app.services import transfer_recurrence

            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)  # rule, amount 200
            periods = all_periods(seed_user["user"].id)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            # A note on a FUTURE row, which the update route's sweep reaches.
            noted = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date.desc())
                .first()
            )
            transfer_service.update_transfer(
                noted.id, seed_user["user"].id, notes="reconcile this one",
            )
            # Moving the DESTINATION is what retains it: the row's records
            # would be re-filed against an account nobody asserted them on.
            elsewhere = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings.account_type_id,
                    name="Second Savings",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            # Its books open before the first paycheck, as ``Savings``'s do
            # (rule-5 confirmation 4, developer 2026-09-23).  Opened today,
            # they would sit after this definition's still-projected past
            # rows, and moving the destination onto them is a save the edit
            # door now refuses (plan step pay_calendar:C18-a, rulings R-PC90
            # and R-PC91) -- a refusal this test is not about.
            open_books_before_the_first_assertion(db.session, elsewhere)
            db.session.commit()
            tid, noted_id = template.id, noted.id
            elsewhere_id = elsewhere.id

            # The amount MOVES (200 -> 275), which is what arms the chooser
            # branch at all: it is gated on ``template.default_amount !=
            # before.amount``.  An adversarial review of R10-b found the first
            # version posting the unchanged 200.00, so the "no chooser"
            # assertion below was dead however the branch behaved.
            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Monthly Savings",
                "default_amount": "275.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(elsewhere_id),
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Some upcoming instances were hand-edited" not in resp.data
            assert b"kept the value it already had" in resp.data

            db.session.expire_all()
            reloaded = db.session.get(TransferTemplate, tid)
            assert reloaded.to_account_id == elsewhere_id, (
                "the edit was rolled back by a conflict that asked nothing"
            )
            assert reloaded.default_amount == Decimal("275.00")
            held = db.session.get(Transfer, noted_id)
            assert held.to_account_id == savings.id, (
                "the retained row must be exactly as the pass found it"
            )
            assert held.notes == "reconcile this one"
            # **A retained row's PLAN follows its definition** -- it reads
            # ``$275.00`` here, and it stored ``$200.00`` until plan step
            # X-au-f.  Being RETAINED protects what the row RECORDED and its
            # place in the ledger; it is what stops the pass re-attributing a
            # row holding an owner's note. A plan is not a record (plan step
            # X-au-c3), and the row never owned its figure: an OVERRIDE would
            # still keep its own.
            assert transfer_amount(held) == Decimal("275.00"), (
                "a retained row's plan still follows its definition"
            )

    def test_transfer_amount_change_with_override_shows_chooser(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A transfer-template amount change colliding with a hand-edited
        transfer shows the conflict chooser (the shared flow, transfer kind)
        and does not commit the pending edit."""
        with app.app_context():
            from app.services import transfer_recurrence, transfer_service
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)  # rule, amount 200
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.flush()
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date.desc())
                .first()
            )
            # Hand-edit the future transfer, shadow-safe via the service.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("350.00")),  is_override=True,
            )
            db.session.commit()
            tid = template.id
            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Monthly Savings",
                "default_amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
            })
            assert resp.status_code == 200
            assert b"hand-edited" in resp.data
            assert b"Keep" in resp.data and b"Use" in resp.data
            # Rolled back: the template keeps its pre-edit amount.
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tid,
            ).default_amount == Decimal("200.00")

    def test_transfer_chooser_apply_use_realigns_the_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply 'use' realigns the overridden transfer to the new amount.

        It also read both shadow transactions' amounts until plan step
        ``balance:X-bi-6-4d-2``, which births none: each side's figure is
        derived from the transfer read here, a projection
        ``test_transfer_legs/test_what_a_side_shows.py::TestBothSidesShowTheTransfer`` grades
        field by field (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            from app.services import transfer_recurrence, transfer_service
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)  # rule, amount 200
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.flush()
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date.desc())
                .first()
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("350.00")),  is_override=True,
            )
            db.session.commit()
            tid, xfer_id = template.id, xfer.id
            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Monthly Savings",
                "default_amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{xfer_id}": "use",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transfer, xfer_id)
            assert transfer_amount(reloaded) == Decimal("250.00")
            assert reloaded.is_override is False

    def test_archive_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id>/archive archives the template and soft-deletes transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            response = auth_client.post(
                f"/transfers/{template.id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data

            db.session.refresh(template)
            assert template.is_active is False

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_unarchive_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id>/unarchive restores the template and its transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            # Deactivate first.
            template.is_active = False
            xfer.is_deleted = True
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"unarchived" in response.data

            db.session.refresh(template)
            assert template.is_active is True

            db.session.refresh(xfer)
            assert xfer.is_deleted is False

    def test_update_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id> for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_archive_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id>/archive for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 404


# ── Unarchive Service Integration Tests (M1) ─────────────────────


class TestUnarchiveUsesService:
    """Verify that unarchive_transfer_template delegates to
    transfer_service.restore_transfer instead of directly manipulating
    ORM objects, ensuring all transfer mutations flow through the
    service layer.
    """

    def test_unarchive_restores_through_the_service(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The unarchive route restores a hidden transfer THROUGH the service.

        The subject is the CALL BOUNDARY: every transfer the route brings back
        is handed to ``transfer_service.restore_transfer``, the one door
        (Transfer Invariant 4), rather than un-hidden by the route itself.  A
        spy wraps that door, records each call and PASSES THROUGH, so the
        state read back below is the real restore's: not hidden, ``$200.00``
        (the definition's price), Projected, in its own pay period.

        **Was ``test_unarchive_restores_via_service_with_invariant_correction``
        until plan step ``balance:X-bi-6-4d-2``.**  Its proof was a twin whose
        period it drifted and the service re-synced; that step births no twin
        (the database refuses one), and the re-sync is an arm leaf 3b
        deletes.  What the door does after 3b that the route could not do
        itself is ``assert_restorable``'s archived-endpoint refusal (F-164),
        graded at the service, in ``test_transfer_service/test_the_restore_door.py``'s
        ``TestRestoreTransfer``, not through this route.  Re-expressed by the
        coordinator's ruling (option A, R-BAL207, ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            # An ad-hoc transfer FIRST, which the unarchive does not touch.
            # While every table counted from 1 it took id 1, so the restored
            # transfer's id was not its owner's (also 1) and a swapped
            # (owner, transfer) call could not pass.  The test database now
            # numbers each table from a range of its own (ruling
            # balance:R-BAL356), so the two ids differ without it, and the
            # check below holds either way.
            _create_transfer(seed_user, seed_periods_today, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            xfer_id, owner_id = xfer.id, seed_user["user"].id
            assert xfer_id != owner_id

            # Hide the transfer through the service, then archive the
            # definition, the state the route's restore exists for.
            transfer_service.delete_transfer(xfer_id, owner_id, soft=True)
            template.is_active = False
            db.session.commit()

            calls = []
            restore = transfer_service.restore_transfer

            def _spy(transfer_id, user_id):
                """Record the call, then restore for real."""
                calls.append((transfer_id, user_id))
                return restore(transfer_id, user_id)

            monkeypatch.setattr(transfer_service, "restore_transfer", _spy)

            assert auth_client.post(
                f"/transfers/{template.id}/unarchive",
                follow_redirects=True,
            ).status_code == 200
            assert calls == [(xfer_id, owner_id)], (
                "the route brought the transfer back without the service door"
            )
            db.session.expire_all()
            restored = db.session.get(Transfer, xfer_id)
            assert restored.is_deleted is False
            assert transfer_amount(restored) == Decimal("200.00")
            assert restored.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )
            # Its own period: ``_create_transfer`` files it in the first paycheck.
            assert restored.pay_period_id == seed_periods_today[0].id
