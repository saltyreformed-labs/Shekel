"""
Shekel Budget App -- A recurring definition's edit never strands a projected row below its books

Plan step ``pay_calendar:C18-a``, rulings **R-PC90** (refuse an account move
that strands a still-projected row) and **R-PC91** (widened to ANY save:
the edit door checks the state the save would leave, whatever field
changed).  A recurring definition's occurrences start above its accounts'
books (ruling **R-PC85**), so a save that leaves a live, still-Projected row
of the definition on or before them leaves that row named by no occurrence,
and the regeneration in the same save retires it -- the forecast rises by
its amount without a word.  Both template edit doors refuse instead, roll
the edit back and say which row.

Every POST here carries what the edit form renders (a route test that posts
a hand-picked payload can pass against an arm a browser never reaches):
the envelope box is posted only when ticked, as a browser posts a checkbox.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import AccountType, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import account_service, recurrence_engine, transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.planned_rows_books import SaveRegeneration
from app.utils.balance_predicates import is_projected_clause
from app.utils.dates import display_today
from tests._test_helpers import (
    all_periods,
    cadence_payload,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY, MONTHLY_FIRST
from tests._test_helpers import newest_price

_ONE_DAY = timedelta(days=1)


@pytest.mark.usefixtures("seed_periods_today")
class TestATransactionDefinitionsEditIsRefused:
    """The transaction-template edit door, rulings R-PC90 and R-PC91."""

    def test_a_move_onto_books_opening_after_a_projected_row_is_refused(
        self, app, auth_client, seed_user,
    ):
        """R-PC90: the first row is due on the first payday; the new books open a day later.

        The save carries a rename too, so the rollback is graded: nothing of
        the edit survives the refusal.
        """
        with app.app_context():
            template = _transaction_template_with_rows(seed_user, "Rent")
            first_due = _first_due(template)
            later = _account_opened_on(seed_user, "New checking", first_due + _ONE_DAY)
            rows_before = len(_live_rows(template))

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(
                    template, name="Rent, moved", account_id=str(later.id),
                ),
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"This change cannot be saved" in resp.data
            assert f"still projected and due {first_due.isoformat()}".encode() in (
                resp.data
            )
            saved = _reload(TransactionTemplate, template.id)
            assert saved.account_id == seed_user["account"].id
            assert saved.name == "Rent"
            assert len(_live_rows(saved)) == rows_before

    def test_a_move_onto_books_opening_BEFORE_every_row_is_saved(
        self, app, auth_client, seed_user,
    ):
        """The other side: books opening the day before the first row strand nothing.

        The template's own books open on the first payday (an opening cannot
        precede it), so its rows start one paycheck later and a day before
        that is a real opening.
        """
        with app.app_context():
            home = _account_opened_on(
                seed_user, "Home checking", _first_payday(seed_user),
            )
            template = _transaction_template_with_rows(
                seed_user, "Rent", account_id=home.id,
            )
            earlier = _account_opened_on(
                seed_user, "New checking", _first_due(template) - _ONE_DAY,
            )

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(
                    template, account_id=str(earlier.id),
                ),
            )

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/templates")
            assert _reload(TransactionTemplate, template.id).account_id == earlier.id

    def test_unticking_the_envelope_box_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-PC91: no account moves; the box alone strands the straddled row.

        The books open three days into the second paycheck.  As an envelope,
        that paycheck's row is compared on the paycheck's LAST day, after the
        books, so it was generated (R-PC89); unticked, it is a bill compared
        on its due day -- the payday, inside the books.  The refusal names
        the row as the save would leave it: a bill and its due day.
        """
        with app.app_context():
            straddled = seed_periods_today[1].start_date
            account = _account_opened_on(
                seed_user, "Mid-paycheck books", straddled + 3 * _ONE_DAY,
            )
            template = _transaction_template_with_rows(
                seed_user, "Groceries", account_id=account.id, is_envelope=True,
            )
            assert _first_due(template) == straddled

            payload = _transaction_update_payload(template)
            del payload["is_envelope"]
            resp = auth_client.post(
                f"/templates/{template.id}", data=payload, follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            assert f"still projected and due {straddled.isoformat()}".encode() in (
                resp.data
            )
            saved = _reload(TransactionTemplate, template.id)
            assert saved.is_envelope is True
            assert _first_due(saved) == straddled

    def test_keeping_the_envelope_box_ticked_is_saved(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same definition, the box posted ticked: nothing is stranded."""
        with app.app_context():
            straddled = seed_periods_today[1].start_date
            account = _account_opened_on(
                seed_user, "Mid-paycheck books", straddled + 3 * _ONE_DAY,
            )
            template = _transaction_template_with_rows(
                seed_user, "Groceries", account_id=account.id, is_envelope=True,
            )

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(template, name="Food"),
            )

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/templates")
            assert _reload(TransactionTemplate, template.id).name == "Food"

    def test_a_RENAME_alone_is_refused_over_a_row_already_below_the_books(
        self, app, auth_client, seed_user,
    ):
        """R-PC91's 'whatever field changed': no account, no envelope box moves.

        The definition is put on later books directly -- the state a row
        that predates the bound is in -- so the only thing the save changes
        is the name.  A refusal gated on WHICH field moved would save it, and
        the regeneration would retire the stranded row.
        """
        with app.app_context():
            template = _transaction_template_with_rows(seed_user, "Rent")
            first_due = _first_due(template)
            later = _account_opened_on(seed_user, "Later books", first_due + _ONE_DAY)
            template.account_id = later.id
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(template, name="Rent, renamed"),
                follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.name == "Rent"
            assert _first_due(saved) == first_due

    def test_funding_a_CURRENT_row_from_its_own_paycheck_inside_the_books_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """H1: the save re-dates the row by the NEW rule; the refusal reads that walk.

        A monthly bill scheduled the day before the current paycheck, funded
        from the FIRST paycheck starting on or after it -- the current one --
        on books opening on its scheduled day: its cash day is that later
        payday, after the opening, so it was generated (R-PC86, R-R95).
        Funding it from the paycheck CONTAINING its date instead moves its
        cash day back onto its scheduled day -- ON the opening, inside it --
        so the walk stops naming it, and its row is in the CURRENT paycheck,
        which the save's own regeneration reaches (review M1).  The row's
        STORED due day still reads outside the books, which is exactly what
        the first cut compared and passed.  The refusal names the row as the
        save would leave it: due on its scheduled day.  (Until plan step
        recurrence:R5-a the lever was clearing a separate due day; that column
        is gone, ruling R-R96, and a later funding paycheck is the shape where
        a row's cash day and its occurrence still part company.)
        """
        with app.app_context():
            scheduled, account, template = _monthly_bill_due_after_the_books(
                seed_user, seed_periods_today,
            )
            row = _live_row_answering(template, scheduled)
            assert row.due_date > scheduled, "precondition: stored due day outside"
            # VALUES, captured before the post: ``_reload`` hands back the SAME
            # identity-mapped objects, so comparing them after it would compare
            # each to itself and pass whatever the save did (review of R5-a).
            first_paycheck = ref_cache.period_placement_id(
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )
            assert template.recurrence_rule.placement_id == first_paycheck
            row_id, row_due = row.id, row.due_date

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(
                    template, unit=RecurrenceUnitEnum.MONTH,
                    placement=PeriodPlacementEnum.CONTAINING_DATE,
                ),
                follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            assert f"still projected and due {scheduled.isoformat()}".encode() in (
                resp.data
            )
            assert f"open {scheduled.isoformat()}".encode() in resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.account_id == account.id
            assert saved.recurrence_rule.placement_id == first_paycheck
            kept = _live_row_answering(saved, scheduled)
            assert kept.id == row_id and kept.due_date == row_due

    def test_keeping_that_funding_is_saved(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same bill, its funding posted back unchanged: nothing is stranded."""
        with app.app_context():
            scheduled, _account, template = _monthly_bill_due_after_the_books(
                seed_user, seed_periods_today,
            )

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(
                    template, unit=RecurrenceUnitEnum.MONTH, name="Water",
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
            )

            assert resp.status_code == 302
            saved = _reload(TransactionTemplate, template.id)
            assert saved.name == "Water"
            assert _live_row_answering(saved, scheduled).name == "Water"


    def test_a_DAY_change_whose_new_occurrence_falls_inside_the_books_is_saved(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling R-PC92, and the key the refusal pairs a row by (round 2's M-A).

        A monthly bill two days into the current paycheck, on books opening
        that paycheck's first day: its row is outside them.  Moving its day
        ONTO the opening makes a new occurrence (R-R49 / R-R19): the save
        retires the old row, and the books stop the replacement -- "Allow it,
        as today".  The refusal matches a row to the occurrence it ANSWERS
        (``occurs_on``); pairing by paycheck instead, the inference R-PC92
        rejected, would read the old row as the dropped occurrence's and
        refuse the save.
        """
        with app.app_context():
            books = seed_periods_today[4].start_date
            scheduled = books + 2 * _ONE_DAY
            account = _account_opened_on(seed_user, "Rent account", books)
            template = _transaction_template_with_rows(
                seed_user, "Rent", account_id=account.id, cadence=MONTHLY,
                starts_on=scheduled,
            )
            old_row = _live_row_answering(template, scheduled)
            assert old_row.pay_period_id == seed_periods_today[4].id, (
                "precondition: the row sits in the paycheck the books open in"
            )
            payload = _transaction_update_payload(
                template, unit=RecurrenceUnitEnum.MONTH,
            )
            payload.update(cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=books,
            ))

            old_row_id = old_row.id

            resp = auth_client.post(f"/templates/{template.id}", data=payload)

            assert resp.status_code == 302
            saved = _reload(TransactionTemplate, template.id)
            assert saved.recurrence_rule.starts_on == books
            # The money effect the ruling names: the old row is retired and
            # the books stop its replacement, so the paycheck the books open
            # in plans no rent at all.
            live = _live_rows(saved)
            assert old_row_id not in {row.id for row in live}
            assert not [
                row for row in live
                if row.pay_period_id == seed_periods_today[4].id
            ], "the replacement on the opening day is inside the books"


@pytest.mark.usefixtures("seed_periods_today")
class TestATransferDefinitionsEditIsRefused:
    """The transfer-template edit door: the later of its two books binds."""

    def test_a_destination_move_onto_later_books_is_refused(
        self, app, auth_client, seed_user,
    ):
        """R-PC90 at the transfer door; its rows compare their due day."""
        with app.app_context():
            savings = _savings_account(seed_user)
            template = _transfer_template_with_rows(seed_user, savings)
            first_due = _first_transfer_due(template)
            later = _account_opened_on(seed_user, "Late savings", first_due + _ONE_DAY)

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(
                    template, name="Sweep, moved", to_account_id=str(later.id),
                ),
                follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            assert f"still projected and due {first_due.isoformat()}".encode() in (
                resp.data
            )
            saved = _reload(TransferTemplate, template.id)
            assert saved.to_account_id == savings.id
            assert saved.name == "Sweep to savings"

    def test_a_destination_move_onto_EARLIER_books_is_saved(
        self, app, auth_client, seed_user,
    ):
        """The other side, so the transfer door's refusal is not unconditional."""
        with app.app_context():
            savings = _savings_account(seed_user)
            template = _transfer_template_with_rows(seed_user, savings)
            earlier = _account_opened_on(
                seed_user, "Early savings",
                _first_transfer_due(template) - _ONE_DAY,
            )

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(
                    template, to_account_id=str(earlier.id),
                ),
            )

            assert resp.status_code == 302
            assert _reload(TransferTemplate, template.id).to_account_id == earlier.id


# ── helpers ──────────────────────────────────────────────────────────────


def _account_opened_on(seed_user, name, opened_on):
    """Create and commit a checking account whose books open on *opened_on*."""
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name=name,
            anchor_balance=Decimal("0.00"),
            observed_on=opened_on,
        ),
    )
    db.session.commit()
    return account


def _first_payday(seed_user):
    """The owner's first payday -- the earliest day an opening may state."""
    return min(p.start_date for p in all_periods(seed_user["user"].id))


def _monthly_bill_due_after_the_books(seed_user, periods):
    """A monthly bill scheduled the day before the current paycheck, due on it, books opening then.

    The current paycheck is ``seed_periods_today``'s period 4, and its first
    day is the Monday of today's week -- on or before today.  The bill is
    scheduled the day BEFORE it, and the books open on that scheduled day
    (in the past, so an opening may state it).  Funded from the FIRST
    paycheck starting on or after its occurrence, the bill's row lives in the
    current paycheck and is due on its payday (ruling R-R95): strictly after
    the opening, so the row is generated.

    Returns:
        ``(scheduled, account, template)`` -- the scheduled day (also the
        books' opening day), the account, and the committed template.
    """
    assert periods[4].start_date <= display_today() < periods[5].start_date
    scheduled = periods[4].start_date - timedelta(days=1)
    account = _account_opened_on(seed_user, "Water account", scheduled)
    template = _transaction_template_with_rows(
        seed_user, "Water bill", account_id=account.id, cadence=MONTHLY_FIRST,
        starts_on=scheduled,
    )
    return scheduled, account, template


def _live_row_answering(template, occurs_on: date):
    """The template's one live projected transaction answering *occurs_on*."""
    (row,) = [row for row in _live_rows(template) if row.occurs_on == occurs_on]
    return row

def _savings_account(seed_user):
    """Create and commit a Savings account whose books open on the first payday.

    A transfer into it therefore starts one paycheck later: its first-payday
    row would land ON the opening, inside it.
    """
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0.00"),
            observed_on=_first_payday(seed_user),
        ),
    )
    db.session.commit()
    return account


def _a_save_made_today():
    """How the transaction edit door regenerates a save left at its default date.

    What ``routes/templates/crud.update_template`` hands the stranded-row
    refusal when the form states no effective date: the transaction engine's
    preview, maintaining from today (``display_today``).  For the tests that
    ask ``definition_edit_refusal`` directly of an edit staged by hand.
    """
    return SaveRegeneration(
        recurrence_engine.preview_regeneration_for_template, display_today(),
    )


def _schedule(user_id):
    """A generation schedule over every one of the owner's periods."""
    return GenerationSchedule.for_period_ids(
        BalanceContext.build(user_id),
        {p.id for p in all_periods(user_id)},
    )


def _transaction_template_with_rows(
    seed_user, name, *, account_id=None, is_envelope=False,
    cadence=EVERY_PERIOD, **rule_kwargs,
):
    """An expense template, every paycheck unless *cadence* says otherwise, its rows generated.

    *rule_kwargs* reach :func:`~tests._test_helpers.make_cadence_rule` --
    ``starts_on`` for the monthly bill.
    """
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account_id or seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name=name,
        is_envelope=is_envelope,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("100.00"))
    make_cadence_rule(template, cadence, **rule_kwargs)
    recurrence_engine.generate_for_template(
        template, _schedule(template.user_id), seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _transfer_template_with_rows(seed_user, savings):
    """An every-paycheck transfer into *savings*, its rows generated, committed."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Sweep to savings",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("200.00"))
    make_cadence_rule(template, EVERY_PERIOD)
    transfer_recurrence.generate_for_template(
        template, _schedule(template.user_id), seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _live_rows(template):
    """The template's projected, non-deleted transactions."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == template.id,
            is_projected_clause(Transaction),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )


def _first_due(template):
    """The earliest due day among the template's live projected rows."""
    return min(row.due_date for row in _live_rows(template))


def _first_transfer_due(template):
    """The earliest due day among the transfer template's live projected transfers."""
    return min(
        row.due_date for row in db.session.query(Transfer).filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(False),
        )
    )


def _reload(model, template_id):
    """Re-read *model* ``template_id`` from the database."""
    db.session.expire_all()
    return db.session.get(model, template_id)


def _transaction_update_payload(
    template, unit=RecurrenceUnitEnum.PERIOD, placement=None, **overrides,
):
    """The transaction edit form's fields, the cadence restated, as a browser posts them.

    The envelope box is present only when ticked (``"on"``); a test that
    unticks it deletes the key.  *placement* is the funding control's value,
    ``None`` for the cadence's default.
    """
    payload = {
        "name": template.name,
        "amount": str(newest_price(template)),
        "account_id": str(template.account_id),
        "category_id": str(template.category_id),
        "transaction_type_id": str(template.transaction_type_id),
        "version_id": str(template.version_id),
        # The cadence restated WITHOUT a start: the stored one rides through,
        # which is what a browser posts for an untouched rule.
        **cadence_payload(
            unit=unit, placement=placement, states_a_start=False,
        ),
    }
    if template.is_envelope:
        payload["is_envelope"] = "on"
    payload.update(overrides)
    return payload


def _transfer_update_payload(template, **overrides):
    """The transfer edit form's fields, the cadence restated, as a browser posts them."""
    payload = {
        "name": template.name,
        "amount": str(newest_price(template)),
        "from_account_id": str(template.from_account_id),
        "to_account_id": str(template.to_account_id),
        "category_id": str(template.category_id),
        "version_id": str(template.version_id),
        **cadence_payload(unit=RecurrenceUnitEnum.PERIOD, states_a_start=False),
    }
    payload.update(overrides)
    return payload
