"""Plan step ``credit_card:CC-5-4a-4``: a row holding a movement is history every door keeps.

Rulings **R-CC54** part (2), **R-CC63**, **R-CC65** and **R-CC66**
(developer 2026-09-22).  Finding **CC-363** is what each class here refuses:
the template, account and pay-period doors destroyed a payment or purchase as
a SIDE EFFECT of deleting something else, judging "history" by a row's STATUS
rather than by what it held -- and the archive hid it, which took its money
off the books just the same (measured 2026-09-22 on a copy of production:
archiving one definition moved its account's projection for that period,
exactly as its permanent delete did).

Every class grades the door the owner presses, through its route or its
service, and asserts the MONEY as well as the sentence: the purchase still
exists, still counts toward the account's settled cash, and its bank match
still stands.  The sentences are ruling **R-CC66**'s, verbatim where it gave
one, in its shape ("'<name>' holds a recorded purchase and cannot be
permanently deleted.  It has been archived instead; the row holding it stays
on your budget." -- the lock badge "Holds a recorded payment or purchase";
the ruling's own example named a real row), read off the session's flashes so
no HTML escaping stands between the test and the text.  **The truncate
refusal's is ruling pay_calendar:R-PC116's since plan step
``pay_calendar:C22``** (developer 2026-10-08), which replaced R-CC66's "Pay
period <m/d> holds a recorded payment or purchase; delete it from its row
first." with one naming each payment and purchase, its amount and its row;
``test_c22_tail_doors_keep_purchases`` grades its shapes.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import PayPeriodLocked, PayPeriodResetBlocked
from app.extensions import db
from app.models.account import Account
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    match_press,
    pay_period_admin,
    pay_period_gates,
    transaction_service,
    transfer_legs,
    transfer_service,
)
from app.services.cash_ledger import settled_cash_facts
from app.services.pay_calendar import calendar_for
from app.services.pay_period_locks import (
    PeriodLockReason,
    classify_schedule_locks,
)
from app.services.statement_match import accept_match, matched_subjects
from app.utils.archive_helpers import HeldMovements
from tests._test_helpers import (
    add_entry,
    create_account_of_type,
    create_settled_transfer,
    generate_row_of,
    generate_transfer_of,
    make_expense_template,
    make_transfer_template,
    one_off_row_of,
    rhythm_of,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does (the convention ``test_cc5_4a3_captions`` keeps).
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_one_off_envelope,
    a_scope,
    a_submission,
    an_import,
    filed_by,
)


def _day(seed_user):
    """A bank day inside the bootstrap period, after the books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _flashes(auth_client):
    """The session's pending flash messages, raw."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _settled_cash(seed_user, account=None):
    """The account's settled cash: what the bank has been seen to move."""
    account = account or seed_user["account"]
    return sum(
        (fact.delta for fact in settled_cash_facts(
            account.id, seed_user["scenario"].id,
        )),
        Decimal("0"),
    )


def _home_improvement(seed_user):
    """Finding CC-363's P5: a one-off envelope holding a bank-recorded $25.00.

    Returns:
        ``(row, created)`` -- the envelope and the create door's
        ``CreatedPurchase`` (the purchase, and the act matching it to the
        bank's line).
    """
    row = a_one_off_envelope(seed_user)
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-25.00",
        posted_on=_day(seed_user), description="HOME DEPOT",
    )
    db.session.commit()
    created = filed_by(seed_user, line, row, by_rule=False)
    db.session.commit()
    return row, created


class TestTheTemplateDoors:
    """The definition's permanent delete archives, and the archive keeps the row."""

    def test_home_improvement_is_archived_and_the_25_stays_spent(
        self, app, db, auth_client, seed_user,
    ):
        """R-CC54's own example: "archived, the $25 stays spent, the match stands".

        Before this step the delete was permitted (no Paid row, no merchant
        rule) and erased the purchase: Checking's settled cash rose $25.00
        above the bank's and the act kept its line alone.
        """
        with app.app_context():
            row, created = _home_improvement(seed_user)
            cash_before = _settled_cash(seed_user)
            template_id = row.template_id

            response = auth_client.post(f"/templates/{template_id}/hard-delete")

            assert response.status_code == 302
            assert _flashes(auth_client) == [
                "'Home Improvement' holds a recorded purchase and cannot be "
                "permanently deleted. It has been archived instead; the row "
                "holding it stays on your budget."
            ]
            db.session.expire_all()
            assert db.session.get(TransactionTemplate, template_id).is_active is False
            assert db.session.get(Transaction, row.id).is_deleted is False
            assert db.session.get(TransactionEntry, created.entry_id) is not None
            assert db.session.get(StatementMatch, created.match_id) is not None
            assert _settled_cash(seed_user) == cash_before

    def test_a_definition_whose_rows_hold_nothing_still_deletes(
        self, app, db, auth_client, seed_user,
    ):
        """The control: the new reason refuses only what it names."""
        with app.app_context():
            row = a_one_off_envelope(seed_user, name="Garage Sale")
            template_id = row.template_id

            auth_client.post(f"/templates/{template_id}/hard-delete")

            assert _flashes(auth_client) == [
                "Recurring transaction 'Garage Sale' permanently deleted.",
            ]
            db.session.expire_all()
            assert db.session.get(TransactionTemplate, template_id) is None

    def test_a_kept_payment_is_named_a_payment(
        self, app, db, auth_client, seed_user,
    ):
        """A reverted bill keeps its payment (R-BAL61); the sentence says so.

        "Name what it holds" (ruling R-CC66): calling a kept payment a
        purchase would be the screen stating what is false.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="120.00", name="Hotel",
            )
            txn = generate_row_of(template, seed_user["bootstrap_period"])
            db.session.commit()
            row_id = txn.id
            _settle_and_revert(seed_user, txn)

            auth_client.post(f"/templates/{template.id}/hard-delete")

            assert _flashes(auth_client) == [
                "'Hotel' holds a recorded payment and cannot be permanently "
                "deleted. It has been archived instead; the row holding it "
                "stays on your budget."
            ]
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).entries

    def test_the_archive_hides_the_empty_row_and_keeps_the_holding_one(
        self, app, db, auth_client, seed_user,
    ):
        """Ruling R-CC63: "Archive hides only rows that hold nothing".

        The row holding the $40.00 purchase stays live, so the envelope keeps
        counting it; the empty later row is hidden as before.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="300.00", name="Groceries",
                category_key="Groceries", is_envelope=True,
            )
            holding = generate_row_of(template, seed_user["bootstrap_period"])
            empty = generate_row_of(template, a_later_period(seed_user))
            add_entry(
                db.session, seed_user, holding, Decimal("40.00"), _day(seed_user),
                settled_on=_day(seed_user),
            )
            db.session.commit()
            cash_before = _settled_cash(seed_user)

            auth_client.post(f"/templates/{template.id}/archive")

            assert _flashes(auth_client) == [
                "Recurring transaction 'Groceries' archived. 1 projected "
                "transaction(s) removed; the row holding a recorded purchase "
                "stays on your budget."
            ]
            db.session.expire_all()
            assert db.session.get(Transaction, holding.id).is_deleted is False
            assert db.session.get(Transaction, empty.id).is_deleted is True
            assert _settled_cash(seed_user) == cash_before


def _settle_and_revert(seed_user, txn):
    """Mark *txn* Paid on :func:`_day`, then Projected: the payment is KEPT."""
    # Pylint: ``import-outside-toplevel`` -- only the kept-payment staging
    # needs the seam's doors; the module's other classes never touch them.
    # pylint: disable=import-outside-toplevel
    from app.enums import SettledDayBasisEnum
    from app.services import transaction_service
    from app.services.settle_day import SettleDay
    from app.services.transaction_service import settle_transaction

    settle_transaction(
        txn, settle_day=SettleDay(
            day=_day(seed_user), basis=SettledDayBasisEnum.ENTERED,
        ),
    )
    db.session.commit()
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


class TestTheAccountDoor:
    """An account a hidden transfer named archives on its posting-ledger history.

    The transfer's soft delete took both sides' payments off, so the hidden
    transfer holds none; the settle's journal entries stay.
    """

    def test_a_hidden_transfer_whose_payments_went_archives_the_account_on_its_ledger(
        self, app, db, auth_client, seed_user,
    ):
        """Ruling R-CC65: the cleanup deleted the hidden row AND its money.

        No live row, no Paid row -- so the history arms say nothing.  **The
        hidden row was a transfer's leg because no other could hold money**
        (ruling **R-CC92**): this was a one-off envelope on Savings,
        soft-deleted with its purchase inside, the state an archive left
        before this step.  Re-expressed under rule 5, developer-confirmed
        2026-09-23.  **Re-expressed again under rule 5, developer-confirmed
        2026-10-09** ("Change it"): since plan step ``balance:X-bi-6-4d-2``
        a transfer's soft delete takes both sides' payments off the books
        too (ruling **credit_card:R-CC75**, "Same as a one-off"; finding
        **balance:BAL-532** closed), so the hidden transfer holds 0 payments
        where it held 2, and Savings still archives rather than deletes --
        now on the history its ledger keeps, not on a hidden payment.  An
        ad-hoc transfer, because a recurring transfer's definition refuses
        the account first (guard 2).
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("60.00"),
                settled_on=_day(seed_user),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            auth_client.post(f"/accounts/{savings.id}/hard-delete")

            assert _flashes(auth_client) == [
                "'Savings' has posting-ledger history and cannot be "
                "permanently deleted. It has been archived instead."
            ]
            db.session.expire_all()
            assert db.session.get(Account, savings.id) is not None
            assert db.session.get(Transfer, xfer.id).is_deleted is True
            assert db.session.query(TransactionEntry).filter(
                (TransactionEntry.expense_transfer_id == xfer.id)
                | (TransactionEntry.income_transfer_id == xfer.id),
            ).count() == 0


def _kept_transfer_payment(seed_user):
    """A $500 recurring transfer paid, its Checking leg matched, then reverted.

    The example ruling R-CC65 was asked with.  Returns the template, the
    transfer and the bank line its kept payment is matched to.

    The line is posted on the Checking side's record's day and ticked as the
    screen offers a paid side, its record; it read and ticked the side's
    SHADOW row until leaf ``balance:X-bi-6-4d-2``, which hangs a side's
    record off the transfer (ruling **R-BAL167**'s class 4, a shared helper
    re-expressed).
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("2000.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )
    template = make_transfer_template(
        db.session, seed_user, savings, amount="500.00",
    )
    db.session.commit()
    xfer = generate_transfer_of(template, seed_user["bootstrap_period"])
    db.session.commit()
    transfer_service.update_transfer(
        xfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.DONE),
    )
    db.session.commit()
    record = transfer_legs.transfer_side_leg(xfer, is_income=False).record
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-500.00",
        posted_on=record.settled_on, description="TRANSFER OUT",
    )
    db.session.commit()
    scope = a_scope(seed_user)
    accept_match(a_submission(scope, lines=[line], transfers=[xfer]), scope)
    db.session.commit()
    transfer_service.update_transfer(
        xfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    return template, xfer, line


class TestTheTransferDoors:
    """The recurring transfer's delete archives; its archive keeps the transfer."""

    def test_the_permanent_delete_archives_and_the_match_stands(
        self, app, db, auth_client, seed_user,
    ):
        """Before this step the delete took the kept payment off the books.

        Through the transfer service's removal act -- the match withdrawn,
        the -$500.00 line unexplained again -- as a side effect of deleting
        the definition (refused option "Reset only").
        """
        with app.app_context():
            template, xfer, line = _kept_transfer_payment(seed_user)

            auth_client.post(f"/transfers/{template.id}/hard-delete")

            assert _flashes(auth_client) == [
                f"'{template.name}' holds a recorded payment and cannot be "
                "permanently deleted. It has been archived instead; the "
                "transfer holding it stays on your budget."
            ]
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template.id).is_active is False
            assert db.session.get(Transfer, xfer.id).is_deleted is False
            assert line.id in matched_subjects(seed_user["account"].id).lines

    def test_the_archive_keeps_the_transfer_holding_a_payment(
        self, app, db, auth_client, seed_user,
    ):
        """One transfer, both legs holding its payment, counted ONCE."""
        with app.app_context():
            template, xfer, _line = _kept_transfer_payment(seed_user)

            auth_client.post(f"/transfers/{template.id}/archive")

            assert _flashes(auth_client) == [
                f"Recurring transfer '{template.name}' archived. 0 projected "
                "transfer(s) removed; the transfer holding a recorded payment "
                "stays on your budget."
            ]
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).is_deleted is False


def _purchase_in(seed_user, period):
    """A one-off envelope 'Tires' in *period*, for an UN-DATED purchase.

    The caller adds the purchase, made on the current paycheck's first day
    (on or before today, ruling R-M) and filed against this LATER paycheck's
    envelope: money in flight, recorded but not yet seen at the bank -- the
    case the handoff asked whether it locks (R-CC65: "dated or not").
    """
    return one_off_row_of(
        period, name="Tires", amount="40.00",
        user_id=seed_user["user"].id, account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        is_envelope=True,
    )


class TestThePayPeriodDoors:
    """Truncate / regenerate lock a period holding one; reset refuses."""

    def test_the_lock_names_the_period_and_deletes_nothing(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The lock's sentence, and ``HOLDS_MOVEMENT`` in the classifier.

        R-CC66's sentence until plan step ``pay_calendar:C22``; ruling
        ``pay_calendar:R-PC116``'s since, naming the $40.00 purchase (this
        fixture's description, "purchase") and the Tires row holding it.
        """
        with app.app_context():
            periods = seed_periods_today
            row = _purchase_in(seed_user, periods[7])
            add_entry(
                db.session, seed_user, row, Decimal("40.00"),
                periods[3].start_date,
            )
            db.session.commit()
            user_id = seed_user["user"].id
            locks = classify_schedule_locks(
                calendar_for(user_id), as_of=periods[3].start_date,
            )
            assert locks[periods[7].id] is PeriodLockReason.HOLDS_MOVEMENT

            with pytest.raises(PayPeriodLocked) as caught:
                pay_period_admin.truncate_pay_periods(
                    user_id, periods[5].id, confirm_discard=True,
                )
            db.session.rollback()

            start = periods[7].start_date
            assert str(caught.value) == (
                f"The {start.isoformat()} paycheck holds 1 purchase you "
                "entered (purchase, $40.00, in Tires). Remove or move it first."
            )
            assert db.session.get(Transaction, row.id) is not None

    def test_the_badge_reads_the_ruled_label(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The settings page's lock chip for the period."""
        with app.app_context():
            row = _purchase_in(seed_user, seed_periods_today[7])
            add_entry(
                db.session, seed_user, row, Decimal("40.00"),
                seed_periods_today[3].start_date,
            )
            db.session.commit()

            response = auth_client.get("/settings?section=pay-periods")

            assert b"Holds a recorded payment or purchase" in response.data

    def test_reset_refuses_while_a_row_holds_one(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Ruling R-CC65: no Paid row anywhere, yet the wipe would take the $40."""
        with app.app_context():
            row = _purchase_in(seed_user, seed_periods_today[7])
            add_entry(
                db.session, seed_user, row, Decimal("40.00"),
                seed_periods_today[3].start_date,
            )
            db.session.commit()
            user_id = seed_user["user"].id
            assert pay_period_gates.can_reset_pay_periods(user_id) is False

            with pytest.raises(PayPeriodResetBlocked) as caught:
                pay_period_admin.reset_pay_periods(
                    user_id, new_start_date=seed_periods_today[0].start_date,
                    num_periods=4, rhythm=rhythm_of(14),
                )
            db.session.rollback()

            assert (caught.value.settled_count, caught.value.holding_count) == (0, 1)
            assert str(caught.value).startswith(
                "Cannot reset the schedule: 1 row(s) hold a recorded payment "
                "or purchase; delete it from its row first."
            )
            assert db.session.get(Transaction, row.id) is not None


def _recurring_groceries_holding_kroger(seed_user):
    """R-CC75's own example: a recurring envelope holding a bank-filed $40.00.

    Returns:
        ``(template, row, created, line)`` -- the recurring definition, its
        occurrence, the create door's ``CreatedPurchase`` and the bank line.
    """
    template = make_expense_template(
        db.session, seed_user, amount="300.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-40.00",
        posted_on=_day(seed_user), description="KROGER",
    )
    db.session.commit()
    created = filed_by(seed_user, line, row, by_rule=False)
    db.session.commit()
    return template, row, created, line


class TestTheRowDoorsSoftArm:
    """Ruling **R-CC75**: deleting a recurring occurrence empties it as a one-off's delete does.

    "Deleting the occurrence takes its payments and purchases off the books
    through the one removal act, exactly as deleting a one-off does, and its
    delete dialog first names the bank lines that frees" (developer
    2026-09-23).  Until then the soft arm kept them: measured, the $40.00
    stayed under the hidden row, the KROGER line read explained, Checking's
    settled cash read $40.00 above the bank, and this step's doors then
    refused the row's period and definition with a sentence naming a row the
    grid does not show.
    """

    def test_the_occurrence_is_emptied_and_its_line_freed(
        self, app, db, seed_user,
    ):
        """The press withdraws what the dialog said, and the tombstone holds nothing."""
        with app.app_context():
            _template, row, created, line = _recurring_groceries_holding_kroger(
                seed_user,
            )
            assert row.recurs
            cash_with_purchase = _settled_cash(seed_user)
            preview = transaction_service.preview_deletion(row)

            with match_press.Press(match_press.Shown(preview.withdrawn.line_ids)) as press:
                outcome = transaction_service.delete_transaction(
                    row, seed_user["user"].id,
                    # What the dialog names, as the card posts it back (plan step
                    # credit_card:CC-5-4a-5, rulings R-CC127 / R-CC131).
                    press=press,
                    purchases_named=preview.purchase_ids,
                )
            db.session.commit()
            db.session.expire_all()

            assert outcome.soft is True
            assert outcome.withdrawn == preview.withdrawn, (
                "the dialog and the press are one derivation"
            )
            assert [freed.line_id for freed in outcome.withdrawn.lines] == [line.id]
            assert db.session.get(Transaction, row.id).is_deleted is True
            assert db.session.get(TransactionEntry, created.entry_id) is None
            assert db.session.get(StatementMatch, created.match_id) is None
            assert line.id not in matched_subjects(seed_user["account"].id).lines
            assert _settled_cash(seed_user) == cash_with_purchase + Decimal("40.00")

    def test_the_rendered_dialog_names_the_line_it_frees(
        self, app, db, auth_client, seed_user,
    ):
        """R-CC75's dialog half, graded on the card the owner reads (review 1, L5).

        The delete button's ``hx-confirm`` on the recurring occurrence's card
        names the KROGER line the press frees, beside R-CC83's sentence.
        """
        with app.app_context():
            _template, row, _created, line = _recurring_groceries_holding_kroger(
                seed_user,
            )
            html = auth_client.get(f"/transactions/{row.id}/full-edit").data.decode()
            confirm = re.search(
                r'hx-delete="/transactions/' + str(row.id)
                + r'"[^>]*?hx-confirm="([^"]*)"',
                html, flags=re.S,
            )
            assert confirm is not None
            question = confirm.group(1)
            freed = (
                f"{line.posted_on.month}/{line.posted_on.day} KROGER -$40.00"
            )
            assert "This occurrence stays deleted" in question
            assert (
                "withdraws 1 accepted match, so 1 bank line is unexplained "
                f"again on your statement screen: {freed}"
            ) in question

    def test_its_definition_then_deletes_and_its_period_does_not_lock(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A row the occurrence delete hides holds no money, so nothing refuses over it.

        What THIS door leaves (ruling R-CC75).  A row hidden before this
        release still holding one refuses the migration (R-CC82); a
        transfer's soft delete takes both sides' payments off the same way
        since plan step ``balance:X-bi-6-4d-2`` (finding BAL-532 closed).
        """
        with app.app_context():
            periods = seed_periods_today
            template = make_expense_template(
                db.session, seed_user, amount="300.00", name="Gas",
                is_envelope=True,
            )
            row = generate_row_of(template, periods[7])
            db.session.commit()
            add_entry(
                db.session, seed_user, row, Decimal("40.00"),
                periods[3].start_date,
            )
            db.session.commit()
            transaction_service.delete_transaction(
                row, seed_user["user"].id,
                # The purchases the dialog names, as the card posts them back
                # (plan step credit_card:CC-5-4a-5, ruling R-CC131).
                purchases_named=(
                    transaction_service.preview_deletion(row).purchase_ids
                ),
            )
            db.session.commit()
            user_id = seed_user["user"].id

            locks = classify_schedule_locks(
                calendar_for(user_id), as_of=periods[3].start_date,
            )
            assert locks[periods[7].id] is None

            auth_client.post(f"/templates/{template.id}/hard-delete")
            assert _flashes(auth_client) == [
                "Recurring transaction 'Gas' permanently deleted.",
            ]


class TestTheSentencesCountWhatTheyName:
    """Several holding rows get a plural sentence (review 1, finding L6).

    Ruling R-CC66 ruled the singular -- "the row holding it stays on your
    budget", "delete it from its row first" -- and the plural branches below
    are this leaf's own: "them" had no plural antecedent after "holds a
    recorded purchase", and "delete it" named one row among several.
    """

    @pytest.mark.parametrize(("payment", "purchase", "named"), [
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, True, False),
    ])
    def test_several_kept_rows_name_what_they_hold_in_the_plural(
        self, payment, purchase, named,
    ):
        """Two live rows: the clause names the kind in the plural either way."""
        kinds = {
            (False, True): "recorded purchases",
            (True, False): "recorded payments",
            (True, True): "recorded payments and purchases",
        }[(payment, purchase)]
        held = HeldMovements(payment=payment, purchase=purchase, live_rows=2)
        assert held.stays("row", named=named) == (
            f"the 2 rows holding {kinds} stay on your budget"
        )

    def test_one_kept_row_refers_back_or_names_it(self):
        """One live row keeps R-CC66's own shape, named or referred back to."""
        held = HeldMovements(payment=False, purchase=True, live_rows=1)
        assert held.stays("transfer", named=False) == (
            "the transfer holding it stays on your budget"
        )
        assert held.stays("row", named=True) == (
            "the row holding a recorded purchase stays on your budget"
        )

    def test_a_reset_blocked_by_several_rows_says_delete_each(self):
        """Three holding rows: 'delete each from its row first'."""
        assert str(PayPeriodResetBlocked(holding_count=3)).startswith(
            "Cannot reset the schedule: 3 row(s) hold a recorded payment or "
            "purchase; delete each from its row first."
        )
