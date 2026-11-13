"""One PATCH that reverts a settled one-off and moves its paycheck lands both.

The payload is the smallest any HTTP client can send: the two version
controls, the status and the paycheck, no item fields, so no propagation
re-saves the row and only the handler's own writes can land the two acts.
**It does not grade the unlock order** (ruling **R-BAL58**: *unlock, edit,
lock*), and that is said because it was checked: with the order forced to
edit first, the move still lands, the paycheck being no field the lock holds.
A handler that drops the paycheck when a status rides beside it fails it
(both measured 2026-11-10).

**Restored by plan task plan#3394** from the card's review (plan task
plan#3059).  ``tests/test_routes/test_one_off_row_doors.py``'s
``test_a_minimal_revert_and_move_carries_the_purchase_legs_to_the_new_paycheck``
graded this through the stored purchase legs until plan task plan#3072 took
the store's grading off the suite and deleted it whole, and the row's own move
went with it.  ``TestUnlockEditLock``, now in
``test_one_off_row_doors/test_the_figure_and_the_act_order.py``, still grades
the order, on the category, a field the lock does hold.  This case grades the
move on the row and on the worked-out read: a purchase's line is filed under
its PARENT's paycheck (ruling **R-FM**), so it follows the move.  It was
written beside that file rather than in it because the file was then over
1,000 lines.
"""

from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.one_off import OneOffToPlace, place_one_off
from tests._ledger_reads import family_movement_ids, period_nets_of_movements
from tests._test_helpers import add_entry, derived_span
from tests.test_routes._statement_forms import form_fields

#: The two controls a client must send for the PATCH to be accepted at all:
#: the row's and its definition's optimistic-lock versions.
_VERSIONS = frozenset({"version_id", "template_version_id"})


def _paid_envelope_holding_a_purchase(auth_client, seed_user, period):
    """Return a Paid one-off envelope in *period* holding one $40.00 purchase.

    Placed through the one producer, the purchase settled on the period's
    first day, then marked Paid through its route.
    """
    row = place_one_off(
        OneOffToPlace(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name="Kayla's Kindle",
            amount=Decimal("162.25"),
            category_id=seed_user["categories"]["Groceries"].id,
            is_envelope=True,
        ),
        derived_span(period),
        scenario_id=seed_user["scenario"].id,
    )
    db.session.commit()
    add_entry(
        db.session, seed_user, row, Decimal("40.00"),
        period.start_date, settled_on=period.start_date,
    )
    db.session.commit()
    resp = auth_client.post(f"/transactions/{row.id}/mark-done")
    assert resp.status_code == 200, resp.data
    db.session.expire_all()
    row = db.session.get(Transaction, row.id)
    assert row.status.is_settled is True
    return row


def _submit_only(auth_client, row, keep, **changes):
    """PATCH *row* with only the *keep* controls off its card plus *changes*.

    Any HTTP client's payload, where a browser's would carry every control
    the card renders.
    """
    card = auth_client.get(f"/transactions/{row.id}/full-edit")
    assert card.status_code == 200
    rendered = form_fields(
        card.data.decode(), f"/transactions/{row.id}", attribute="hx-patch",
    )
    payload = [
        pair for pair in rendered if pair[0] in keep and pair[0] not in changes
    ]
    payload.extend(changes.items())
    return auth_client.patch(f"/transactions/{row.id}", data=MultiDict(payload))


class TestARevertAndAMoveInOneRequest:
    """A revert and a paycheck move in one request, sent as the smallest payload."""

    def test_a_minimal_revert_and_move_lands_the_row_and_its_purchase_in_the_new_paycheck(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The row reverts and moves, and its $40.00 purchase's line follows it.

        Before: the Paid envelope sits in the first paycheck, and its one
        purchase's lines are filed there and nowhere else.  After the PATCH
        (Projected, the second paycheck): the row is in the second paycheck and
        settled no longer, and the purchase's lines are filed there and
        nowhere else.
        """
        with app.app_context():
            first, second = seed_periods_today[0], seed_periods_today[1]
            scenario_id = seed_user["scenario"].id
            row = _paid_envelope_holding_a_purchase(auth_client, seed_user, first)
            movements = family_movement_ids(row)
            assert set(period_nets_of_movements(movements, scenario_id)) == {
                first.id,
            }, "CONTROL: the purchase's lines are filed in the first paycheck"

            resp = _submit_only(
                auth_client, row, _VERSIONS,
                status_id=str(ref_cache.status_id(StatusEnum.PROJECTED)),
                pay_period_id=str(second.id),
            )

            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.pay_period_id == second.id
            assert row.status.is_settled is False
            after = period_nets_of_movements(movements, scenario_id)
            assert set(after) == {second.id}, (
                f"the purchase's lines stayed in the OLD paycheck: {after}"
            )
