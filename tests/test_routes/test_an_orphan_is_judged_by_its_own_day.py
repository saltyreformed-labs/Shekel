"""A row its rule no longer names is judged by its own day at every books door (ruling R-PC98).

Plan step ``pay_calendar:C18-a``, the round-5 review's H2 (developer
2026-09-23, "Own-day check, all 4").  A rule EDIT -- its start moved later,
say -- leaves the still-Projected rows it no longer produces behind: ORPHANS,
answering no occurrence the walk names, so no dropped occurrence catches them.
The review measured each door letting one through inside the books, the
opening then counting the orphan a second time (``-$20.00``).  Each door now
compares an orphan on its own day (``definition_unarchive.own_books_day``),
as ruling R-PC96 compares a rule-less or undated row at the unarchive.

The orphans here are made the way an edit leaves them: the rule's
``starts_on`` moved onto the THIRD row's occurrence, after which the first two
rows -- in paychecks the maintain pass no longer reaches -- answer nothing.

Also here, the round-5 review's other gaps on the same doors: M2, the two
envelope arms no test graded (the revert's paycheck wording and the
unarchive's own-day envelope day), and H1's placement axis -- a monthly rule
placed into the paycheck starting ON OR AFTER its occurrence, the shape of
production row 788, which the revert refusal holds.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AccountOpeningSourceEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import recurrence_engine, status_seam
from app.services.balance_at import BalanceContext
from app.services.definition_unarchive import unarchive_scope
from app.services.opening_service import (
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.services.pay_calendar import calendar_for
from app.services.planned_rows_books import definition_edit_refusal
from tests._test_helpers import make_cadence_rule, state_template_price
from tests.oracles.recurrence_baseline import MONTHLY_FIRST
from tests.test_routes.test_a_revert_below_the_books import _projected, _rendered
from tests.test_routes.test_archived_rows_bound_the_books import (
    _flashed,
    _fresh,
    _restate_directly,
)
from tests.test_routes.test_definition_edit_strands_no_row import (
    _a_save_made_today,
    _account_opened_on,
)
from tests.test_services.test_opening_restatement_planned_rows import (
    _ONE_DAY,
    _account_opened_early,
    _account_with_projected_rows,
    _schedule,
)


class TestTheOpeningDoor:
    """R-PC88's restatement, over a live orphan and an archived definition's hidden one."""

    def test_a_restatement_onto_a_live_orphans_due_day_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The review's P5: the start moved later, the books restated onto the first old row."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            (first, _second), _template = _orphaned(rows)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(first.due_date, Decimal("0.00")),
                )

            assert (
                f'"{first.name}" is still projected and due '
                f"{first.due_date.isoformat()}"
            ) in str(refused.value)

    def test_the_day_BEFORE_the_first_orphan_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side, so a strict comparison for the inclusive one fails a test."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            (first, _second), _template = _orphaned(rows)

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first.due_date - _ONE_DAY, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_an_archived_definitions_hidden_orphan_is_refused_over(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The review's P4: archived, the start moved later, then the books restated.

        The unarchive would bring the hidden orphan back inside the books, so
        the restatement is refused with R-PC93's archived-row words.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            first = min(rows, key=lambda row: row.occurs_on)
            first_due, template_id = first.due_date, first.template_id
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302
            _db.session.expire_all()
            template = _db.session.get(TransactionTemplate, template_id)
            _orphaned(_rows_of(template_id))

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(account),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            assert (
                f'"{template.name}" is archived and still holds an unpaid item '
                f"due {first_due.isoformat()}"
            ) in str(refused.value)


class TestTheUnarchive:
    """R-PC95/96's scope: a hidden orphan inside the books stays deleted, and is named."""

    def test_the_unarchive_keeps_both_orphans_deleted_and_says_so(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The scope judges each hidden row as it STANDS.

        The books are moved onto the second orphan's day by a direct record,
        because the restatement door now refuses that move (the class above).
        The doors can still leave the state (the round-6 review's L4): the
        owner deletes the two old rows by hand while the definition is active
        (a deleted row is not planned), restates the books onto their days
        (allowed), then archives the definition -- and the unarchive may not
        restore into it.  Every other hidden row comes back.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            template_id = rows[0].template_id
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302
            _db.session.expire_all()
            (first, second), _template = _orphaned(_rows_of(template_id))
            orphan_ids = {first.id, second.id}
            first_due, second_due = first.due_date, second.due_date
            _open_books_directly(account, second_due)
            _db.session.commit()

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert _flashed(
                f"2 items due {first_due.isoformat()} to "
                f"{second_due.isoformat()} stay deleted: they fall inside "
                f"{account.name}'s books, which open {second_due.isoformat()}."
            ) in resp.data
            _db.session.expire_all()
            for row in _rows_of(template_id):
                assert row.is_deleted is (row.id in orphan_ids), row.occurs_on


class TestTheEditDoor:
    """R-PC90/91's edit refusal over an orphan, judged where it SITS (rulings R-PC98, R-PC99).

    The first row is cancelled first, so the one PLANNED orphan is the second
    row and both sides of its day fall inside the seeded history (an account
    cannot be created with books before the first payday).
    """

    def test_moving_the_account_onto_an_orphans_due_day_is_saved(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Re-expressed under R-PC99 (developer-ruled): it asserted a REFUSAL here.

        One save moves the start later AND the item onto books opening on
        the planned orphan's day.  The orphan stays on the account it sits
        on, whose books open long before it, so it stays planned there.  The
        ruled outcome, verbatim from the chosen option: "Moving Rent onto
        books opening 01-16 saves: the orphan stays planned on Checking at
        -$10, as it should."  The refusal it used to give told the owner the
        item would sit inside books it does not sit on.
        """
        with app.app_context():
            ordered = _first_row_cancelled(seed_user, seed_periods)
            later = _account_opened_on(
                seed_user, "Later books", ordered[1].due_date,
            )
            template = _edited_onto(ordered, later)

            assert definition_edit_refusal(
                template, BalanceContext.build(seed_user["user"].id), None,
                _a_save_made_today(),
            ) is None
            assert ordered[1].account_id != later.id

    def test_unticking_the_envelope_box_puts_the_orphan_inside_its_own_books(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The save changes the orphan's own day: its paycheck's last day becomes its payday.

        The books of the account it sits on open ON its payday, which holds
        an envelope's row no longer than its paycheck (ruling R-PC89) -- but
        once the box is unticked it is a bill due that day, inside them.
        """
        with app.app_context():
            account, ordered = _envelope_orphan_on_books_opening_on_its_payday(
                seed_user, seed_periods, day_before=False,
            )
            ordered[0].template.is_envelope = False
            _db.session.flush()

            refusal = definition_edit_refusal(
                ordered[0].template,
                BalanceContext.build(seed_user["user"].id), None,
                _a_save_made_today(),
            )

            assert refusal is not None
            assert (
                f'"{ordered[1].name}" is still projected and due '
                f"{ordered[1].due_date.isoformat()}, and {account.name}'s "
                f"books open {ordered[1].due_date.isoformat()}.  An opening is "
                "the balance at the END of its day, so that unpaid item would "
                "sit inside it."
            ) in refusal

    def test_the_same_untick_over_books_opening_the_day_before_is_saved(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Books opening the day BEFORE the payday hold nothing: the save is not refused."""
        with app.app_context():
            _account, ordered = _envelope_orphan_on_books_opening_on_its_payday(
                seed_user, seed_periods, day_before=True,
            )
            ordered[0].template.is_envelope = False
            _db.session.flush()

            assert definition_edit_refusal(
                ordered[0].template,
                BalanceContext.build(seed_user["user"].id), None,
                _a_save_made_today(),
            ) is None


class TestTheRevert:
    """R-PC97's revert refusal, over an orphan, an envelope and a placed-after occurrence."""

    def test_a_cancelled_orphan_on_its_books_stays_cancelled(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Setting a cancelled orphan back to projected inside its books is refused (400)."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            (first, _second), _template = _orphaned(rows)
            _db.session.commit()
            first_id, first_due, name = first.id, first.due_date, first.name
            assert auth_client.post(
                f"/transactions/{first_id}/cancel",
            ).status_code == 200
            _restate_directly(account, first_due)

            resp = auth_client.patch(
                f"/transactions/{first_id}", data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400
            assert _rendered(
                f'"{name}" is due {first_due.isoformat()}, on or before '
            ) in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transaction, first_id).status_id == (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )

    def test_the_same_revert_with_books_opening_the_day_before_is_allowed(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side of the orphan's own day."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            (first, _second), _template = _orphaned(rows)
            _db.session.commit()
            first_id, first_due = first.id, first.due_date
            assert auth_client.post(
                f"/transactions/{first_id}/cancel",
            ).status_code == 200
            _restate_directly(account, first_due - _ONE_DAY)

            resp = auth_client.patch(
                f"/transactions/{first_id}", data={"status_id": str(_projected())},
            )

            assert resp.status_code == 200
            _db.session.expire_all()
            assert _db.session.get(Transaction, first_id).status_id == _projected()

    def test_an_envelope_is_named_by_its_paychecks_last_day(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Review M2: the revert's envelope wording, which no test graded (R-PC89)."""
        with app.app_context():
            account, rows = _account_with_projected_rows(
                seed_user, seed_periods, is_envelope=True,
            )
            _db.session.commit()
            first = min(rows, key=lambda row: row.due_date)
            first_id, name = first.id, first.name
            last_day = seed_periods[1].start_date - _ONE_DAY
            assert auth_client.post(
                f"/transactions/{first_id}/cancel",
            ).status_code == 200
            _restate_directly(account, last_day)

            resp = auth_client.patch(
                f"/transactions/{first_id}", data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400
            assert _rendered(
                f'"{name}" is in the paycheck ending {last_day.isoformat()}, '
                "on or before "
            ) in resp.data

    def test_an_occurrence_placed_into_a_LATER_paycheck_is_held(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Review H1: production row 788's shape, which the lane once said was not held.

        A monthly rule placed into the paycheck starting ON OR AFTER its
        occurrence, first occurring the day before the first payday: that
        occurrence is PLACED, into the first paycheck, so the books opening on
        its compared day drop it and a cancelled row answering it may not be
        set back to projected.
        """
        with app.app_context():
            account = _account_opened_early(seed_user)
            first_payday = seed_periods[0].start_date
            row = _first_row_of_a_monthly_first_bill(
                seed_user, seed_periods, account, first_payday - _ONE_DAY,
            )
            assert row.occurs_on < first_payday, "precondition: before the first payday"
            row_id, due = row.id, row.due_date
            assert auth_client.post(f"/transactions/{row_id}/cancel").status_code == 200
            _restate_directly(account, due)

            resp = auth_client.patch(
                f"/transactions/{row_id}", data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400
            assert _rendered(f"is due {due.isoformat()}, on or before ") in resp.data


class TestARuleLessEnvelopeAtTheUnarchive:
    """Review M2: R-PC96's own day for a rule-less ENVELOPE is its paycheck's last day."""

    def test_books_opening_on_its_payday_restore_it(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Its money is spent across the paycheck, so books opening on the payday hold none of it."""
        with app.app_context():
            account, template, first = _rule_less_hidden_envelope(
                seed_user, seed_periods,
            )
            _open_books_directly(account, first.due_date)

            scope = unarchive_scope(
                template, None, calendar_for(seed_user["user"].id),
            )

            assert first.id not in scope.own_day_inside

    def test_books_opening_on_its_paychecks_last_day_keep_it_deleted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side: the paycheck's last day is inside books opening on it."""
        with app.app_context():
            account, template, first = _rule_less_hidden_envelope(
                seed_user, seed_periods,
            )
            last_day = seed_periods[1].start_date - _ONE_DAY
            _open_books_directly(account, last_day)

            scope = unarchive_scope(
                template, None, calendar_for(seed_user["user"].id),
            )

            assert scope.own_day_inside.get(first.id) == last_day


# ── helpers ──────────────────────────────────────────────────────────────


def _orphaned(rows):
    """Move the rule's start onto the THIRD row's occurrence, orphaning the first two.

    What a rule edit leaves in paychecks the maintain pass no longer reaches:
    the two old rows stay, answering occurrences the rule no longer names.

    Returns:
        ``((first, second), template)``, the orphans in date order.
    """
    ordered = sorted(rows, key=lambda row: row.occurs_on)
    template = ordered[0].template
    template.recurrence_rule.starts_on = ordered[2].occurs_on
    _db.session.flush()
    return (ordered[0], ordered[1]), template


def _first_row_cancelled(seed_user, seed_periods):
    """An every-paycheck rent's rows in date order, the first one cancelled."""
    _account, rows = _account_with_projected_rows(seed_user, seed_periods)
    ordered = sorted(rows, key=lambda row: row.occurs_on)
    status_seam.apply_status_change(
        ordered[0], ref_cache.status_id(StatusEnum.CANCELLED),
    )
    _db.session.flush()
    return ordered


def _edited_onto(ordered, account):
    """Apply one edit to the rows' definition: onto *account*, its start on the third row."""
    template = ordered[0].template
    template.account_id = account.id
    template.recurrence_rule.starts_on = ordered[2].occurs_on
    _db.session.flush()
    return template


def _rows_of(template_id):
    """Every transaction of the definition, deleted or not."""
    return _db.session.query(Transaction).filter(
        Transaction.template_id == template_id,
    ).all()


def _open_books_directly(account, day):
    """Record *account*'s books opening on *day* with no door asked.

    A later record governs, so this is the state a restatement leaves --
    written without the refusal, for the scope tests that judge a state as it
    stands.
    """
    _db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=day,
        opening_equity=Decimal("0.00"),
        source_id=ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
    ))
    _db.session.flush()


def _rule_less_hidden_envelope(seed_user, seed_periods):
    """An envelope whose rows are all hidden and whose rule is gone: set not to repeat, archived.

    Returns:
        ``(account, template, first)`` -- *first* its earliest row.
    """
    account, rows = _account_with_projected_rows(
        seed_user, seed_periods, is_envelope=True,
    )
    template = rows[0].template
    for row in rows:
        row.is_deleted = True
    template.is_active = False
    _db.session.delete(template.recurrence_rule)
    _db.session.flush()
    _db.session.refresh(template)
    assert not template.recurs, "precondition: the envelope no longer repeats"
    return account, template, min(rows, key=lambda row: row.due_date)


def _first_row_of_a_monthly_first_bill(seed_user, seed_periods, account, starts_on):
    """A $10.00 monthly bill placed into the paycheck starting on or after each occurrence."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account.id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Monthly-first bill",
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template, Decimal("10.00"))
    make_cadence_rule(template, MONTHLY_FIRST, starts_on=starts_on)
    _db.session.refresh(template)
    rows = recurrence_engine.generate_for_template(
        template, _schedule(seed_user, seed_periods), seed_user["scenario"].id,
    )
    _db.session.commit()
    assert rows, "the fixture must generate rows"
    return min(rows, key=lambda row: row.occurs_on)


def _envelope_orphan_on_books_opening_on_its_payday(
    seed_user, seed_periods, *, day_before,
):
    """An envelope's rows, the first cancelled and the second orphaned, its books on its payday.

    The start moves onto the third row, and the books of the account the
    rows sit on are recorded opening on the second row's payday (or the day
    before, *day_before*) -- a state the restatement door accepts for an
    envelope, whose row is compared on its paycheck's LAST day.

    Returns:
        ``(account, ordered)``, the rows in date order.
    """
    account, rows = _account_with_projected_rows(
        seed_user, seed_periods, is_envelope=True,
    )
    ordered = sorted(rows, key=lambda row: row.occurs_on)
    status_seam.apply_status_change(
        ordered[0], ref_cache.status_id(StatusEnum.CANCELLED),
    )
    ordered[0].template.recurrence_rule.starts_on = ordered[2].occurs_on
    payday = ordered[1].due_date
    _open_books_directly(account, payday - _ONE_DAY if day_before else payday)
    return account, ordered
