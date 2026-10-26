"""Ledger row BAL-530: the transfer popover loads its legs' records ONCE.

Plan step ``balance:X-bi-6-4c-3``.  ``GET /transfers/<id>/full-edit`` reads the
legs' records -- each side's covering movement -- for both day boxes and the
withdrawal caption, and read them for the figures too until plan step
``balance:X-bi-6-4d-2`` made both figures the transfer's own.  Until
``X-bi-6-4c-3`` the figures and the caption each called
``transfer_legs.covering_movements_by_leg``, so one render ran the join twice;
the route now loads the records once and hands the same dict to every reader
(``routes/transfers/forms.get_full_edit``).

The loader is reached through two names -- the package attribute the route
calls and ``_records``' internal calls (the grid's leg builders) -- so the spy
stands in both places, and a render that reached the join by either is
counted.  ``_render_helpers``' own import was a third until plan step
``balance:X-bi-6-4d-2``, when the transfers page's cell render stopped
loading the records for figures that read none.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from app.extensions import db
from app.services import transfer_legs, transfer_service
from app.services.transfer_legs import _records
from app.utils.dates import display_today
from tests._test_helpers import (
    an_observed_day,
    create_savings_account,
    create_transfer,
    open_books_before_the_first_assertion,
)


def test_the_popover_loads_the_legs_records_once(
    app, auth_client, seed_user, seed_periods_today,
):
    """One side bank-shown, the other borrowing: both boxes drawn off ONE load."""
    with app.app_context():
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        open_books_before_the_first_assertion(db.session, savings)
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods_today[3], amount=Decimal("300.00"),
        )
        db.session.commit()
        transfer_service.settle_transfer(
            xfer.id, seed_user["user"].id,
            side_days=(transfer_service.SideDay(
                xfer.from_account_id,
                an_observed_day(display_today() - timedelta(days=3)),
            ),),
        )
        db.session.commit()

        real = _records.covering_movements_by_leg
        loads = []

        def counting(transfer_ids):
            """Record one load, then run the real join."""
            ids = list(transfer_ids)
            loads.append(ids)
            return real(ids)

        with mock.patch.object(
            transfer_legs, "covering_movements_by_leg", counting,
        ), mock.patch.object(
            _records, "covering_movements_by_leg", counting,
        ):
            response = auth_client.get(f"/transfers/{xfer.id}/full-edit")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # The render read the records it was handed: the bank-shown side's
        # box and the borrowing side's caption are both drawn off them.
        assert 'name="settled_on_from"' in body
        assert 'name="settled_on_to"' in body
        assert "(a guess)" in body
        assert loads == [[xfer.id]]
