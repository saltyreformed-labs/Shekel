"""A row its books already drop may not become unpaid again (ruling R-PC97).

Plan step ``pay_calendar:C18-a``, the round-4 review's H2.  Setting a paid,
received, credited or cancelled row back to Projected never asked the books:
the database's boundary watches a row only while it carries a settle day, and
a revert clears it.  Measured before the fix through the real routes: a rent
row cancelled, the books restated onto its day (allowed -- a cancelled row is
not planned), then PATCHed back to Projected -- accepted, the row live and
unpaid ON the books, $10.00 off the forecast.  Josh ruled 2026-09-23, "Build
the stopgap": each row type's one status door refuses a revert whose
occurrence the books drop, until plan step ``recurrence:R22`` stores no unpaid
copy to revert.

Every door runs through its ROUTE, and every refusal is graded by re-reading
the rows after the request ended, so a write the route staged and a rollback
did not undo cannot pass for a refusal.
"""
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import planned_rows_books, transfer_service
from app.services.recurrence import RecurrenceResolutionError
from tests._test_helpers import transfer_side_record
from tests.test_routes.test_archived_rows_bound_the_books import (
    _forecast,
    _monthly_save_into,
    _restate_directly,
    _transfers,
)
from tests.test_services.test_opening_restatement_planned_rows import (
    _ONE_DAY,
    _account_opened_early,
    _account_with_projected_rows,
)


class TestATransactionsRevert:
    """The status seam refuses it, at every transaction door that reverts."""

    def test_a_cancelled_row_on_its_books_stays_cancelled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The review's measured path (probe P7), refused: $0.00 moves."""
        with app.app_context():
            account, first_id, first_due = _first_rent_row(seed_user, seed_periods)
            assert auth_client.post(
                f"/transactions/{first_id}/cancel",
            ).status_code == 200
            _restate_directly(account, first_due)
            forecast = _forecast(seed_user, account)

            resp = auth_client.patch(
                f"/transactions/{first_id}", data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400
            assert _rendered(
                f'"Recurring rent" is due {first_due.isoformat()}, on or before '
                "Planned-rows account's books, which open "
                f"{first_due.isoformat()}, so it cannot be planned as unpaid."
            ) in resp.data
            _db.session.expire_all()
            row = _db.session.get(Transaction, first_id)
            assert row.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert not row.is_deleted
            assert _forecast(seed_user, account) == forecast

    def test_a_paid_row_on_its_books_stays_paid(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Paid, settled after the books, due on them: the revert is refused too.

        The ruling's stated cost -- reverting is the only way to correct a
        paid row, so this row cannot be corrected while its books sit on or
        after its due day.
        """
        with app.app_context():
            account, first_id, first_due = _first_rent_row(seed_user, seed_periods)
            assert auth_client.post(
                f"/transactions/{first_id}/mark-done",
            ).status_code == 200
            _restate_directly(account, first_due)
            _db.session.expire_all()
            settled_on = _db.session.get(Transaction, first_id).settled_on
            assert settled_on > first_due, "precondition: a movement after the books"
            forecast = _forecast(seed_user, account)

            resp = auth_client.patch(
                f"/transactions/{first_id}", data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400
            assert b"so it cannot be planned as unpaid." in resp.data
            _db.session.expire_all()
            row = _db.session.get(Transaction, first_id)
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert row.settled_on == settled_on
            assert _forecast(seed_user, account) == forecast

    def test_an_unmark_credit_on_its_books_keeps_the_credit_and_its_payback(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The third door back to unpaid, through the same seam."""
        with app.app_context():
            account, first_id, first_due = _first_rent_row(seed_user, seed_periods)
            assert auth_client.post(
                f"/transactions/{first_id}/mark-credit",
            ).status_code == 200
            _db.session.expire_all()
            paybacks = _paybacks_of(first_id)
            assert len(paybacks) == 1, "precondition: the credit made its payback"
            _restate_directly(account, first_due)

            resp = auth_client.delete(f"/transactions/{first_id}/unmark-credit")

            assert resp.status_code == 400
            assert b"so it cannot be planned as unpaid." in resp.data
            _db.session.expire_all()
            row = _db.session.get(Transaction, first_id)
            assert row.status_id == ref_cache.status_id(StatusEnum.CREDIT)
            assert [p.id for p in _paybacks_of(first_id)] == [paybacks[0].id]

    def test_the_day_before_its_books_the_revert_goes_through(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The other side of the boundary: books opening the day before hold nothing."""
        with app.app_context():
            account, first_id, first_due = _first_rent_row(seed_user, seed_periods)
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

    def test_a_schedule_that_cannot_be_walked_refuses(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """No walk, no telling whether the books hold the row: refused, not a 500."""
        with app.app_context():
            _account, first_id, _first_due = _first_rent_row(seed_user, seed_periods)
            assert auth_client.post(
                f"/transactions/{first_id}/cancel",
            ).status_code == 200

            def refusing(*_args, **_kwargs):
                raise RecurrenceResolutionError("a rule no door writes")

            monkeypatch.setattr(planned_rows_books, "resolved_with_books", refusing)

            resp = auth_client.patch(
                f"/transactions/{first_id}", data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400
            assert b"cannot be read: repair its schedule first." in resp.data
            _db.session.expire_all()
            assert _db.session.get(Transaction, first_id).status_id == (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )


class TestATransfersRevert:
    """The transfer's own status door refuses it before either side is written."""

    def test_a_paid_transfer_on_its_books_stays_paid_with_both_shadows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Atomic: the transfer keeps its status and both sides their settle day.

        The transfer's status is the one status and each side's day is its
        payment RECORD's since plan step ``balance:X-bi-6-4d-2``; it read both
        shadows' status and day until then (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).  The name is kept as history.
        """
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Revert savings")
            template_id = _monthly_save_into(seed_user, seed_periods, savings).id
            first = min(_transfers(template_id), key=lambda row: row.due_date)
            first_id, first_due = first.id, first.due_date
            assert auth_client.post(
                f"/transfers/instance/{first_id}/mark-done",
            ).status_code == 200
            _restate_directly(savings, first_due)
            _db.session.expire_all()
            before = _transfer_and_sides(first_id)
            assert all(day is not None for _status, day in before[:2]), (
                "precondition: both sides dated by the Mark Paid"
            )
            forecast = _forecast(seed_user, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{first_id}",
                data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400
            assert _rendered(
                f'"Monthly save" is due {first_due.isoformat()}, on or before '
                "Revert savings's books"
            ) in resp.data
            _db.session.expire_all()
            assert _transfer_and_sides(first_id) == before
            done = ref_cache.status_id(StatusEnum.DONE)
            assert {status for status, _day in before} == {done}
            assert _forecast(seed_user, savings) == forecast


class TestADeletedTransferIsNotAPlan:
    """A soft delete sets a Paid transfer back to Projected even on its books (R-BAL248).

    Developer 2026-10-09, "Plan, kept deleted", narrowing R-PC97: the delete
    that takes a Paid transfer's payments off returns it to a plan (ruling
    R-BAL246), and the books' revert refusal does not reach it, because a
    hidden row sits in no balance.  Measured before the ruling: the set-back
    through the transfer's one status door was REFUSED with the sentence
    :meth:`TestATransfersRevert.test_a_paid_transfer_on_its_books_stays_paid_with_both_shadows`
    pins, which would have refused a delete that went through.  Where the
    hidden plan could come back, the unarchive asks the books again and
    leaves it deleted, naming it.
    """

    def test_a_paid_transfer_on_its_books_deletes_as_a_hidden_plan(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The delete answers 200: hidden, Projected, both $50.00 records off."""
        with app.app_context():
            _savings, _template_id, first_id, _first_due = _paid_then_held(
                auth_client, seed_user, seed_periods,
            )
            checking = _forecast(seed_user, seed_user["account"])

            resp = auth_client.delete(f"/transfers/instance/{first_id}")

            assert resp.status_code == 200
            _db.session.expire_all()
            assert _transfer_and_sides(first_id) == [
                (_projected(), None), (_projected(), None), (_projected(), None),
            ]
            assert _db.session.get(Transfer, first_id).is_deleted is True
            assert _forecast(seed_user, seed_user["account"]) == (
                checking + Decimal("50.00")
            )

    def test_the_unarchive_leaves_it_deleted_and_names_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived and unarchived, the hidden plan stays deleted and the flash says why."""
        with app.app_context():
            _savings, template_id, first_id, first_due = _paid_then_held(
                auth_client, seed_user, seed_periods,
            )
            assert auth_client.delete(
                f"/transfers/instance/{first_id}",
            ).status_code == 200
            assert auth_client.post(
                f"/transfers/{template_id}/archive",
            ).status_code == 302

            resp = auth_client.post(
                f"/transfers/{template_id}/unarchive", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert _rendered(
                f"1 item due {first_due.isoformat()} stays deleted: it falls "
                "inside Revert savings's books, which open "
                f"{first_due.isoformat()}."
            ) in resp.data
            _db.session.expire_all()
            first = _db.session.get(Transfer, first_id)
            assert first.is_deleted is True
            assert first.status_id == _projected()

    def test_above_its_books_the_unarchive_brings_it_back_as_a_plan(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No books hold it: archived and unarchived, it returns live and empty."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Revert savings")
            template_id = _monthly_save_into(seed_user, seed_periods, savings).id
            first_id = min(
                _transfers(template_id), key=lambda row: row.due_date,
            ).id
            assert auth_client.post(
                f"/transfers/instance/{first_id}/mark-done",
            ).status_code == 200
            assert auth_client.delete(
                f"/transfers/instance/{first_id}",
            ).status_code == 200
            assert auth_client.post(
                f"/transfers/{template_id}/archive",
            ).status_code == 302

            assert auth_client.post(
                f"/transfers/{template_id}/unarchive",
            ).status_code == 302

            _db.session.expire_all()
            assert _db.session.get(Transfer, first_id).is_deleted is False
            assert _transfer_and_sides(first_id) == [
                (_projected(), None), (_projected(), None), (_projected(), None),
            ]

    def test_a_hard_delete_on_its_books_sets_no_status_and_is_not_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The hard arm removes the row: no set-back is asked of a row still live."""
        with app.app_context():
            _savings, _template_id, first_id, _first_due = _paid_then_held(
                auth_client, seed_user, seed_periods,
            )

            transfer_service.delete_transfer(
                first_id, seed_user["user"].id, soft=False,
            )
            _db.session.commit()

            assert _db.session.get(Transfer, first_id) is None


# ── helpers ──────────────────────────────────────────────────────────────


def _rendered(text):
    """*text* as the page renders it: autoescaped quotes and apostrophes."""
    return text.replace('"', "&#34;").replace("'", "&#39;").encode()


def _projected():
    """The Projected status id."""
    return ref_cache.status_id(StatusEnum.PROJECTED)


def _first_rent_row(seed_user, seed_periods):
    """An every-paycheck $10.00 rent on an early-opened account, and its first row.

    Returns:
        ``(account, first_id, first_due)``.
    """
    account, rows = _account_with_projected_rows(seed_user, seed_periods)
    _db.session.commit()
    first = min(rows, key=lambda row: row.due_date)
    return account, first.id, first.due_date


def _paid_then_held(auth_client, seed_user, seed_periods):
    """A $50.00 monthly save's first transfer, Paid, then due ON its books.

    Marked Paid through its route (both sides dated today), then the
    Savings books restated onto its due day: settled after its books, due on
    them -- the state R-PC97 refuses a revert of.

    Returns:
        ``(savings, template_id, first_id, first_due)``.
    """
    savings = _account_opened_early(seed_user, name="Revert savings")
    template_id = _monthly_save_into(seed_user, seed_periods, savings).id
    first = min(_transfers(template_id), key=lambda row: row.due_date)
    first_id, first_due = first.id, first.due_date
    assert auth_client.post(
        f"/transfers/instance/{first_id}/mark-done",
    ).status_code == 200
    _restate_directly(savings, first_due)
    _db.session.expire_all()
    assert _transfer_and_sides(first_id)[0][1] is not None, (
        "precondition: the Mark Paid dated the from-side"
    )
    return savings, template_id, first_id, first_due


def _paybacks_of(source_id):
    """The live card paybacks a credit on *source_id* created."""
    return _db.session.query(Transaction).filter(
        Transaction.credit_payback_for_id == source_id,
        Transaction.is_deleted.is_(False),
    ).all()


def _transfer_and_sides(transfer_id):
    """``(status_id, settled_on)`` of a transfer's two sides, then the parent's status.

    Each side's status is its TRANSFER's -- the one status -- and its day is
    its payment record's, read by independent SQL over the side links; the
    two shadows' own status and day until plan step ``balance:X-bi-6-4d-2``
    (ruling R-BAL167 class 4, plan step balance:X-bi-6-4d-2).  ``None`` for a
    side holding no record.
    """
    transfer = _db.session.get(Transfer, transfer_id)
    sides = []
    for account_id in (transfer.from_account_id, transfer.to_account_id):
        record = transfer_side_record(_db.session, transfer_id, account_id)
        sides.append(
            (transfer.status_id, None if record is None else record.settled_on),
        )
    return sides + [(transfer.status_id, None)]
