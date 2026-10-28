"""An archived definition's hidden rows bound its accounts' books (ruling R-PC93).

Plan step ``pay_calendar:C18-a``, the round-2 adversarial review's H-B.
Archiving a recurring definition hides its still-Projected rows and
unarchiving brings them back, so a books move made while it was archived saw
nothing to strand: measured before the fix, the restatement COMMITTED, the
transaction template's unarchive restored a row below the new books, and the
transfer template's unarchive -- whose maintain pass retires a row the rule no
longer names -- HARD-DELETED the current paycheck's transfer.  Josh ruled
2026-09-23, "Refuse the books move": the rows an unarchive would restore count
as planned rows at every door that moves a definition's books, and the
refusal names such a row as the archived definition's with the remedy that
reaches it.

The archive and the unarchive run through their ROUTES here, so what they
hide and restore is theirs and not a fixture's imitation; the restatement runs
through its service door, whose refusal is what R-PC93 changes.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes.accounts import opening as opening_route
from app.services import planned_rows_books, transfer_recurrence
from app.services.balance_at import BalanceContext, cash_balance_at
from app.services.opening_service import (
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceResolutionError
from app.utils.balance_predicates import is_projected_clause
from app.utils.dates import display_today
from tests._test_helpers import make_cadence_rule, state_template_price
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests.test_routes.test_definition_edit_strands_no_row import (
    _a_save_made_today,
    _transaction_update_payload,
    _transfer_template_with_rows,
    _transfer_update_payload,
)
from tests.test_services.test_opening_restatement_planned_rows import (
    _ONE_DAY,
    _account_opened_early,
    _account_with_projected_rows,
    _opening_count,
    _schedule,
)


class TestATransactionDefinitionsHiddenRows:
    """The archive hides the rows; the books still may not move past them."""

    def test_the_first_hidden_rows_due_day_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The review's probe, refused: the row is named as the archived definition's."""
        with app.app_context():
            account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            before = _opening_count(account)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(account),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            message = str(refused.value)
            assert (
                f'the recurring "Recurring rent" is archived and still holds an '
                f"unpaid item due {first_due.isoformat()} that unarchiving "
                "would bring back"
            ) in message
            assert (
                'Unarchive "Recurring rent", mark that item paid or cancel it, '
                "then restate the books."
            ) in message
            assert "for good" not in message, "M-1: the clause was dropped"
            _db.session.rollback()
            assert _opening_count(account) == before
            assert _hidden_rows(template_id), "the rows are still hidden"

    def test_the_day_BEFORE_it_is_accepted_and_the_unarchive_restores_above_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The other side of the boundary, and the harm it prevents stays prevented.

        The books move to the day before the first hidden row; the unarchive
        then restores every row it hid, and none of them lands on or before
        the books.
        """
        with app.app_context():
            account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            hidden = {row.id for row in _hidden_rows(template_id)}

            outcome = apply_opening_restatement(
                account=_fresh(account),
                opening=BooksOpening(first_due - _ONE_DAY, Decimal("0.00")),
            )
            assert outcome is OpeningRestatementOutcome.COMMITTED
            resp = auth_client.post(f"/templates/{template_id}/unarchive")

            assert resp.status_code == 302
            _db.session.expire_all()
            assert {row.id for row in _live_rows(template_id)} == hidden
            assert planned_rows_books.first_row_an_opening_strands(
                account.id, first_due - _ONE_DAY,
                calendar_for(seed_user["user"].id),
            ) is None

    def test_a_row_deleted_by_hand_ABOVE_the_books_counts_because_it_comes_back(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A hand-deleted row the books have NOT passed is restorable, so it counts.

        A hand delete is a soft delete no row column tells from the
        archive's, so an unarchive still restores a row its owner deleted
        while the definition was active whenever the books have not passed
        it (ledger row REC-536, the recurrence arc's) -- and the refusal,
        counting exactly what the unarchive restores, counts it.  Refused
        while archived, then unarchived: the row IS restored.  The rows the
        two leave OUT together are graded by
        :class:`TestARowTheBooksHavePassedStaysDeleted`.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            first = min(rows, key=lambda row: row.due_date)
            template_id, first_id, first_due = (
                first.template_id, first.id, first.due_date,
            )
            assert auth_client.delete(
                f"/transactions/{first_id}",
            ).status_code == 200
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302

            with pytest.raises(ValidationError, match=r"is archived"):
                apply_opening_restatement(
                    account=_fresh(account),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )
            _db.session.rollback()
            assert auth_client.post(
                f"/templates/{template_id}/unarchive",
            ).status_code == 302

            _db.session.expire_all()
            assert first_id in {row.id for row in _live_rows(template_id)}

    def test_the_edit_door_counts_them_and_an_ACTIVE_definitions_deletions_do_not(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """R-PC93 at the edit doors: an archived definition's account move is refused.

        The control is the same move on an ACTIVE definition whose rows its
        owner deleted by hand: nothing restores those but the conflict
        chooser (ledger row REC-535), so they are not planned rows.
        """
        with app.app_context():
            _account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            later = _account_opened_early(seed_user, name="Later books")
            _restate_directly(later, first_due)
            template = _db.session.get(
                Transaction, _hidden_rows(template_id)[0].id,
            ).template
            ctx = BalanceContext.build(seed_user["user"].id)
            restorable = planned_rows_books.restorable_before_the_edit(
                template, ctx,
            )
            template.account_id = later.id

            refusal = planned_rows_books.definition_edit_refusal(
                template, ctx, restorable, _a_save_made_today(),
            )

            assert refusal is not None
            assert '"Recurring rent" is archived and still holds' in refusal
            template.is_active = True
            _db.session.flush()
            active_ctx = BalanceContext.build(seed_user["user"].id)
            assert planned_rows_books.restorable_before_the_edit(
                template, active_ctx,
            ) is None
            assert planned_rows_books.definition_edit_refusal(
                template, active_ctx, None, _a_save_made_today(),
            ) is None, "an active definition's deleted rows are not planned"

    def test_the_cards_ceiling_stops_before_the_hidden_row(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The books-opening card reads the same producer, hidden rows included."""
        with app.app_context():
            account, _template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )

            ceiling = opening_route.books_opening_context(_fresh(account))["ceiling"]

            assert ceiling.day == first_due - _ONE_DAY
            assert "is archived and still holds" in ceiling.said
            assert 'Unarchive "Recurring rent", mark that item paid' in ceiling.said


class TestATransferDefinitionsHiddenRows:
    """The review's transfer case: the unarchive that hard-deleted a row cannot be reached."""

    def test_a_books_move_past_a_hidden_transfer_is_refused_and_nothing_is_deleted(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Refused naming the archived transfer; the unarchive then restores every row."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Hidden savings")
            template = _monthly_save_into(seed_user, seed_periods, savings)
            template_id = template.id
            every_id = {
                transfer.id for transfer in _transfers(template_id, deleted=False)
            }
            first_due = min(
                transfer.due_date for transfer in _transfers(template_id)
            )
            assert auth_client.post(
                f"/transfers/{template_id}/archive",
            ).status_code == 302

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(savings),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )
            assert '"Monthly save" is archived and still holds' in str(
                refused.value,
            )
            _db.session.rollback()
            assert auth_client.post(
                f"/transfers/{template_id}/unarchive",
            ).status_code == 302

            _db.session.expire_all()
            assert {
                transfer.id for transfer in _transfers(template_id, deleted=False)
            } == every_id


class TestARowTheBooksHavePassedStaysDeleted:
    """Ruling R-PC95 (the round-3 review's H-C): an unarchive never restores a row below the books.

    A hand delete of a recurring row is a SOFT delete, which no row column
    tells from the archive's hide.  Deleted while the definition was
    active, the row is not planned, so the books may move past it; an
    unarchive that restored every soft-deleted row then brought it back
    INSIDE the opening -- measured on dev and on C18 alike, its amount
    counted a second time.  It stays deleted, the unarchive says so, and
    the refusals, counting exactly what the unarchive restores, never name
    it.
    """

    def test_the_unarchive_leaves_it_deleted_and_names_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """H-C's path 1, through the routes: the forecast the move left is the one after."""
        with app.app_context():
            account, first_id, first_due, others = _rent_with_a_row_deleted_below_the_books(
                auth_client, seed_user, seed_periods,
            )
            template_id = _db.session.get(Transaction, first_id).template_id
            forecast = _forecast(seed_user, account)
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert _flashed(
                f"{len(others)} projected transaction(s) restored. 1 item due "
                f"{first_due.isoformat()} stays deleted: it falls inside "
                f"Planned-rows account's books, which open "
                f"{first_due.isoformat()}."
            ) in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transaction, first_id).is_deleted
            assert {row.id for row in _live_rows(template_id)} == others
            assert _forecast(seed_user, account) == forecast

    def test_two_such_rows_are_named_as_one_span(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The notice counts every row it leaves and spans their days."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            first, second = sorted(rows, key=lambda row: row.due_date)[:2]
            template_id, first_due, second_due = (
                first.template_id, first.due_date, second.due_date,
            )
            for row_id in (first.id, second.id):
                assert auth_client.delete(
                    f"/transactions/{row_id}",
                ).status_code == 200
            _restate_directly(account, second_due)
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert _flashed(
                f"2 items due {first_due.isoformat()} to {second_due.isoformat()} "
                "stay deleted: they fall inside Planned-rows account's books, "
                f"which open {second_due.isoformat()}."
            ) in resp.data
            _db.session.expire_all()
            assert all(
                _db.session.get(Transaction, row_id).is_deleted
                for row_id in (first.id, second.id)
            )

    def test_a_transfer_stays_deleted_and_the_notice_names_the_books_that_bind(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The transfer twin: the later of its two books binds, and the notice names it.

        The transfer's own flag is what stays deleted.  It also read both of
        the transfer's shadow rows deleted until plan step balance:X-bi-6-4d-2
        made a transfer one row.
        """
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Hidden savings")
            template_id = _monthly_save_into(seed_user, seed_periods, savings).id
            first = min(_transfers(template_id), key=lambda row: row.due_date)
            first_id, first_due = first.id, first.due_date
            assert auth_client.delete(
                f"/transfers/instance/{first_id}",
            ).status_code == 200
            _restate_directly(savings, first_due)
            forecast = _forecast(seed_user, savings)
            assert auth_client.post(
                f"/transfers/{template_id}/archive",
            ).status_code == 302

            resp = auth_client.post(
                f"/transfers/{template_id}/unarchive", follow_redirects=True,
            )

            assert _flashed(
                f"1 item due {first_due.isoformat()} stays deleted: it falls "
                "inside Hidden savings's books, which open "
                f"{first_due.isoformat()}."
            ) in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transfer, first_id).is_deleted
            assert _forecast(seed_user, savings) == forecast

    def test_a_later_books_move_while_archived_is_not_refused_over_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The opening door leaves it out: the unarchive would not bring it back.

        Counted, the refusal would say the archived rent "still holds an
        unpaid item ... that unarchiving would bring back" -- false, and
        unsatisfiable, since unarchiving leaves it deleted.  The move stops
        the day before the first row the unarchive WOULD restore.
        """
        with app.app_context():
            account, first_id, _first_due, others = _rent_with_a_row_deleted_below_the_books(
                auth_client, seed_user, seed_periods,
            )
            template_id = _db.session.get(Transaction, first_id).template_id
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302
            next_due = min(
                _db.session.get(Transaction, row_id).due_date for row_id in others
            )

            outcome = apply_opening_restatement(
                account=_fresh(account),
                opening=BooksOpening(next_due - _ONE_DAY, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            with pytest.raises(ValidationError, match=r"is archived and still holds"):
                apply_opening_restatement(
                    account=_fresh(account),
                    opening=BooksOpening(next_due, Decimal("0.00")),
                )

    def test_an_edit_while_archived_is_not_refused_over_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The edit door leaves it out too: a rename of the archived rent saves."""
        with app.app_context():
            _account, first_id, _first_due, _others = _rent_with_a_row_deleted_below_the_books(
                auth_client, seed_user, seed_periods,
            )
            template = _db.session.get(Transaction, first_id).template
            assert auth_client.post(
                f"/templates/{template.id}/archive",
            ).status_code == 302
            _db.session.expire_all()

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(template, name="Rent, renamed"),
            )

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/templates")
            _db.session.expire_all()
            assert _db.session.get(
                TransactionTemplate, template.id,
            ).name == "Rent, renamed"


class TestTheEditDoorsAskBeforeTheEdit:
    """R-PC93 at the edit ROUTES: the restorable rows are read before the edit lands.

    Read after it, the scope would already leave out every row the edit
    moves below the books, and an archived definition's edit could never be
    refused -- the controls that fail if a door asks late.
    """

    def test_an_archived_rents_account_move_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The transaction door."""
        with app.app_context():
            _account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            later = _account_opened_early(seed_user, name="Later books")
            _restate_directly(later, first_due)
            template = _db.session.get(TransactionTemplate, template_id)

            resp = auth_client.post(
                f"/templates/{template_id}",
                data=_transaction_update_payload(
                    template, account_id=str(later.id),
                ),
                follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            assert (
                "is archived and still holds an unpaid item due "
                f"{first_due.isoformat()}"
            ).encode() in resp.data
            _db.session.expire_all()
            assert _db.session.get(
                TransactionTemplate, template_id,
            ).account_id != later.id

    def test_an_archived_transfers_destination_move_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The transfer door, whose capture sits before the destination settle."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Hidden savings")
            template = _transfer_template_with_rows(seed_user, savings)
            template_id = template.id
            first_due = min(row.due_date for row in _transfers(template_id))
            assert auth_client.post(
                f"/transfers/{template_id}/archive",
            ).status_code == 302
            later = _account_opened_early(seed_user, name="Late savings")
            _restate_directly(later, first_due)
            _db.session.expire_all()
            template = _db.session.get(TransferTemplate, template_id)

            resp = auth_client.post(
                f"/transfers/{template_id}",
                data=_transfer_update_payload(
                    template, to_account_id=str(later.id),
                ),
                follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            assert (
                "is archived and still holds an unpaid item due "
                f"{first_due.isoformat()}"
            ).encode() in resp.data
            _db.session.expire_all()
            assert _db.session.get(
                TransferTemplate, template_id,
            ).to_account_id == savings.id


class TestAnUnarchiveOfAnActiveDefinitionRestoresNothing:
    """R-PC95's second clause (H-C's path 2): a stale tab's Unarchive on an active definition.

    Its soft-deleted rows are its owner's own deletions; the unarchive
    brought them back, below the books or not, with only "unarchived".
    """

    def test_the_transaction_door(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Nothing restored, nothing written, and the flash says why."""
        with app.app_context():
            account, first_id, _first_due, others = _rent_with_a_row_deleted_below_the_books(
                auth_client, seed_user, seed_periods,
            )
            template_id = _db.session.get(Transaction, first_id).template_id
            template = _db.session.get(TransactionTemplate, template_id)
            version = template.version_id
            forecast = _forecast(seed_user, account)

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert _flashed(
                "Recurring transaction 'Recurring rent' is not archived, so "
                "nothing was restored."
            ) in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transaction, first_id).is_deleted
            assert {row.id for row in _live_rows(template_id)} == others
            assert _forecast(seed_user, account) == forecast
            template = _db.session.get(TransactionTemplate, template_id)
            assert template.is_active
            assert template.version_id == version, "the definition was not written"

    def test_the_transfer_door(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The twin, over a transfer deleted by hand above the books."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Hidden savings")
            template_id = _monthly_save_into(seed_user, seed_periods, savings).id
            first_id = min(
                _transfers(template_id), key=lambda row: row.due_date,
            ).id
            assert auth_client.delete(
                f"/transfers/instance/{first_id}",
            ).status_code == 200
            live = {row.id for row in _transfers(template_id, deleted=False)}
            version = _db.session.get(TransferTemplate, template_id).version_id
            forecast = _forecast(seed_user, savings)

            resp = auth_client.post(
                f"/transfers/{template_id}/unarchive", follow_redirects=True,
            )

            assert _flashed(
                "Recurring transfer 'Monthly save' is not archived, so nothing "
                "was restored."
            ) in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transfer, first_id).is_deleted
            assert {
                row.id for row in _transfers(template_id, deleted=False)
            } == live
            template = _db.session.get(TransferTemplate, template_id)
            assert template.is_active
            assert template.version_id == version, "the definition was not written"
            assert _forecast(seed_user, savings) == forecast


class TestARowNoWalkNamesIsJudgedByItsOwnDay:
    """Ruling R-PC96 (the round-4 review's H1): no occurrence to look up, so its own day.

    A rule-less definition's rows and a recurring definition's UNDATED rows
    (``occurs_on`` ``NULL``, which plan step R19-a retains and dev held 598 of)
    answer no occurrence, so the walk the unarchive asks never names them.
    Measured before the fix: every such hidden row was restored, one due ON
    the books included.  No regeneration re-dates such a row, so its stored
    day is compared -- and the books move itself stays allowed, as for any
    item that does not repeat.
    """

    def test_a_transfer_that_stopped_repeating_while_archived(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The review's probe P9b, through the routes: $50.00 no longer counted twice."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Unrepeat savings")
            template_id = _monthly_save_into(seed_user, seed_periods, savings).id
            first = min(_transfers(template_id), key=lambda row: row.due_date)
            first_id, first_due = first.id, first.due_date
            assert auth_client.post(
                f"/transfers/{template_id}/archive",
            ).status_code == 302
            _db.session.expire_all()
            template = _db.session.get(TransferTemplate, template_id)
            assert auth_client.post(f"/transfers/{template_id}", data={
                "recurrence_unit": "",
                "version_id": str(template.version_id),
                "effective_from": seed_periods[4].start_date.isoformat(),
            }).status_code in (200, 302)
            _db.session.expire_all()
            assert not _db.session.get(TransferTemplate, template_id).recurs
            hidden = {row.id for row in _transfers(template_id, deleted=True)}
            assert first_id in hidden, "precondition: the rows are still hidden"
            _restate_directly(savings, first_due)
            forecast = _forecast(seed_user, savings)

            resp = auth_client.post(
                f"/transfers/{template_id}/unarchive", follow_redirects=True,
            )

            assert _flashed(
                f"{len(hidden) - 1} projected transfer(s) restored. 1 item due "
                f"{first_due.isoformat()} stays deleted: it falls inside "
                f"Unrepeat savings's books, which open {first_due.isoformat()}."
            ) in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transfer, first_id).is_deleted
            assert {
                row.id for row in _transfers(template_id, deleted=False)
            } == hidden - {first_id}
            assert _forecast(seed_user, savings) == (
                forecast + Decimal("50.00") * (len(hidden) - 1)
            )

    def test_an_undated_hidden_row_ABOVE_the_books_is_restored(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The round-4 review's M2: the undated arm was untested, and a mutation survived.

        The books must drop SOME occurrence for the arm to be asked at all, so
        the first row is deleted by hand and the books moved onto its day;
        the undated row is a later one, above them.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first, _second, third = sorted(rows, key=lambda row: row.due_date)[:3]
            third.occurs_on = None
            _db.session.commit()
            template_id, first_id, first_due, undated_id = (
                first.template_id, first.id, first.due_date, third.id,
            )
            assert auth_client.delete(
                f"/transactions/{first_id}",
            ).status_code == 200
            _restate_directly(account, first_due)
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert _flashed(
                f"1 item due {first_due.isoformat()} stays deleted"
            ) in resp.data, "precondition: the books drop an occurrence"
            _db.session.expire_all()
            assert not _db.session.get(Transaction, undated_id).is_deleted
            assert _db.session.get(Transaction, first_id).is_deleted

    def test_an_undated_hidden_row_on_its_books_stays_deleted_and_is_named(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No occurrence to match, so its own due day: on the books, it stays."""
        with app.app_context():
            account, template_id, undated_id, due = _rent_with_an_undated_row(
                seed_user, seed_periods,
            )
            _restate_directly(account, due)
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302
            restorable = len(_hidden_rows(template_id)) - 1

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert _flashed(
                f"{restorable} projected transaction(s) restored. 1 item due "
                f"{due.isoformat()} stays deleted: it falls inside "
                f"Planned-rows account's books, which open {due.isoformat()}."
            ) in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transaction, undated_id).is_deleted


class TestAnArchivedDefinitionsLIVERowCounts:
    """The round-3 review's M-2: the state CC-5-4a-4's archive leaves.

    That archive keeps a row holding a payment or purchase LIVE (ruling
    R-CC63), so an archived definition can hold a live Projected row, and
    the books may not move past it any more than an active one's.
    """

    def test_a_books_move_past_it_is_refused_naming_it_live(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Refused as a still-projected row, not as a hidden one."""
        with app.app_context():
            account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            kept = _hidden_rows(template_id)[0]
            assert kept.due_date == first_due
            kept.is_deleted = False
            _db.session.commit()

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(account),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            message = str(refused.value)
            assert (
                f'"Recurring rent" is still projected and due '
                f"{first_due.isoformat()}"
            ) in message
            assert "is archived" not in message


class TestAnUnwalkableRuleCountsEveryHiddenRow:
    """The round-4 review's L1: the edit door's fallback was reached by no test and logged nothing."""

    def test_the_edit_doors_capture_holds_nothing_back_and_logs_why(
        self, app, auth_client, seed_user, seed_periods, monkeypatch, caplog,
    ):
        """With no walk, every hidden row counts, and the log says which definition."""
        with app.app_context():
            _account, template_id, _first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            hidden = {row.id for row in _hidden_rows(template_id)}

            def refusing(*_args, **_kwargs):
                raise RecurrenceResolutionError("a rule no door writes")

            monkeypatch.setattr(planned_rows_books, "unarchive_scope_on", refusing)
            template = _db.session.get(TransactionTemplate, template_id)

            scope = planned_rows_books.restorable_before_the_edit(
                template, BalanceContext.build(seed_user["user"].id),
            )

            assert {
                row_id for (row_id,) in _db.session.query(Transaction.id)
                .filter(*scope.restores())
            } == hidden
            assert (
                f"Counting every hidden row of archived TransactionTemplate "
                f"{template_id}"
            ) in caplog.text


class TestAnUnwalkableRuleCostsTheCardNotThePage:
    """Round 2's L-b: the account edit page is the only door to rename, archive or delete."""

    def test_the_edit_page_renders_without_the_card(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A rule the recurrence package refuses hides the card; the page still renders."""
        with app.app_context():
            account = _account_opened_early(seed_user)
            _db.session.commit()

            def refusing(*_args, **_kwargs):
                raise RecurrenceResolutionError("a rule no door writes")

            monkeypatch.setattr(
                opening_route, "first_row_an_opening_strands", refusing,
            )

            assert opening_route.books_opening_context(_fresh(account)) is None
            resp = auth_client.get(f"/accounts/{account.id}/edit")
            assert resp.status_code == 200
            assert account.name.encode() in resp.data


# ── helpers ──────────────────────────────────────────────────────────────


def _archived_rent(auth_client, seed_user, seed_periods):
    """An every-paycheck rent on an early-opened account, archived through its route.

    Returns:
        ``(account, template_id, first_due)`` -- the first row's due day is
        the first day a books move would strand it.
    """
    account, rows = _account_with_projected_rows(seed_user, seed_periods)
    _db.session.commit()
    template_id = rows[0].template_id
    first_due = min(row.due_date for row in rows)
    assert auth_client.post(f"/templates/{template_id}/archive").status_code == 302
    _db.session.expire_all()
    assert not _live_rows(template_id), "precondition: the archive hid every row"
    return account, template_id, first_due


def _rent_with_a_row_deleted_below_the_books(auth_client, seed_user, seed_periods):
    """H-C's setup: the first rent row deleted by hand, then the books moved onto its day.

    Both through their own doors while the rent is ACTIVE, which is why the
    move commits: a deleted row is not planned.

    Returns:
        ``(account, first_id, first_due, others)`` -- the account, the deleted
        row and its due day (also the books' new opening day), and the ids of
        the rent's other rows, all still live.
    """
    account, rows = _account_with_projected_rows(seed_user, seed_periods)
    _db.session.commit()
    first = min(rows, key=lambda row: row.due_date)
    first_id, first_due = first.id, first.due_date
    others = {row.id for row in rows} - {first_id}
    assert auth_client.delete(f"/transactions/{first_id}").status_code == 200
    _restate_directly(account, first_due)
    return account, first_id, first_due, others


def _rent_with_an_undated_row(seed_user, seed_periods):
    """A recurring rent whose FIRST row answers no occurrence (``occurs_on`` ``NULL``).

    The state plan step R19-a retains rather than retires (dev held 598 such
    rows on the day it shipped); stated directly, because it is a state and
    not a door under test.

    Returns:
        ``(account, template_id, undated_id, due)`` -- the undated row's id
        and its stored due day.
    """
    account, rows = _account_with_projected_rows(seed_user, seed_periods)
    first = min(rows, key=lambda row: row.due_date)
    first.occurs_on = None
    _db.session.commit()
    return account, first.template_id, first.id, first.due_date


def _forecast(seed_user, account):
    """*account*'s cash forecast thirty days out, on a fresh read pass."""
    _db.session.expire_all()
    return cash_balance_at(
        _fresh(account), BalanceContext.build(seed_user["user"].id),
        display_today() + timedelta(days=30),
    )


def _flashed(text):
    """*text* as the page renders a flash: autoescaped, an apostrophe as ``&#39;``."""
    return text.replace("'", "&#39;").encode()


def _monthly_save_into(seed_user, seed_periods, savings):
    """An every-paycheck transfer from checking into *savings*, its rows generated."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name="Monthly save",
        default_amount=Decimal("50.00"),
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template)
    make_cadence_rule(
        template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
    )
    _db.session.refresh(template)
    created = transfer_recurrence.generate_for_template(
        template, _schedule(seed_user, seed_periods), seed_user["scenario"].id,
    )
    assert created, "the fixture must generate transfers"
    _db.session.commit()
    return template


def _restate_directly(account, day):
    """Open *account*'s books on *day* through the service door, committed."""
    outcome = apply_opening_restatement(
        account=_fresh(account), opening=BooksOpening(day, Decimal("0.00")),
    )
    assert outcome is OpeningRestatementOutcome.COMMITTED


def _fresh(account):
    """Re-read *account* in the current session."""
    return _db.session.get(Account, account.id)


def _live_rows(template_id):
    """The template's live, still-Projected transactions."""
    return _db.session.query(Transaction).filter(
        Transaction.template_id == template_id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(False),
    ).all()


def _hidden_rows(template_id):
    """The template's soft-deleted, still-Projected transactions."""
    return _db.session.query(Transaction).filter(
        Transaction.template_id == template_id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(True),
    ).order_by(Transaction.due_date).all()


def _transfers(template_id, *, deleted=None):
    """The template's still-Projected transfers, optionally by deleted state."""
    query = _db.session.query(Transfer).filter(
        Transfer.transfer_template_id == template_id,
        is_projected_clause(Transfer),
    )
    if deleted is not None:
        query = query.filter(Transfer.is_deleted.is_(deleted))
    return query.all()
