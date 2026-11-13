"""The period move on a rule-less row: is_override is not flipped, the move re-places the row
(ruling R-BAL33), and a move onto a sibling's paycheck or day is refused.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.one_off import place_row_of
from app.services.template_amount_service import amount_versions
from tests._test_helpers import derived_span, resolved_amount
from tests._definition_price import newest_price

from ._helpers import _one_off, _placed, _submit, _recurring, _rendered_companion


class TestTheOverrideFlip:
    """``mutations._apply_field_updates``: the MOVE half keys on ``recurs``."""

    def test_a_period_move_does_not_flip_a_rule_less_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A moved one-off keeps hearing its definition.

        The flag is what keeps the maintain and generate passes off a row the
        owner placed elsewhere; a rule-less definition runs no pass, and the
        flag would only hide the row from ``propagate_to_unruled_definition``
        (the twin's defect **BAL-493** on this table).
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            target = seed_periods_today[1]
            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "pay_period_id": str(target.id),
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(one_off)
            assert one_off.pay_period_id == target.id
            assert one_off.is_override is False

    def test_a_period_move_still_flips_a_recurring_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the flip the regeneration relies on is untouched."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            target = seed_periods_today[1]
            resp = auth_client.patch(f"/transactions/{recurring.id}", data={
                "pay_period_id": str(target.id),
                "version_id": recurring.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(recurring)
            assert recurring.pay_period_id == target.id
            assert recurring.is_override is True

    def test_a_typed_figure_restates_the_definition_and_does_not_flip(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The interim's flip ENDS here (R-BAL28's stated end; R-BAL29).

        This case asserted the opposite until leaf 7b-2 -- OWN on the row
        with the flag beside it, the figure kept safe from the definition's
        next edit by that flag.  Kayla's Kindle, `$162.25` on the
        definition, `$170.00` typed: the definition's ONE version now says
        `$170.00`, the row still reads it, and there is nothing for a flag
        to protect.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            companion = _rendered_companion(auth_client, row)
            assert companion == "162.25"
            resp = _submit(
                auth_client, row, estimated_amount="170.00",
            )
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("170.00")
            versions = amount_versions(row.template)
            assert [(v.effective_date, v.amount) for v in versions] == [
                (row.due_date, Decimal("170.00")),
            ]
            assert newest_price(row.template) == Decimal("170.00")


class TestAPeriodMoveRePlaces:
    """Ruling **R-BAL33** at the popover's period move (leaf 7b-2)."""

    def test_a_default_dated_one_off_takes_the_target_paychecks_start(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Born on its paycheck's start, moved: due on the new paycheck's start.

        ``occurs_on`` follows (R-BAL25), the flag stays off (R-BAL28's move
        half), and the price is unchanged (a flat series).
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            target = seed_periods_today[1]
            target_start = derived_span(target).start_date
            assert row.due_date == derived_span(seed_periods_today[0]).start_date
            resp = _submit(auth_client, row, pay_period_id=str(target.id))
            assert resp.status_code == 200, resp.data
            assert resp.headers["HX-Trigger"] == "gridRefresh"
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == target_start
            assert row.occurs_on == target_start
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("162.25")

    def test_an_owner_dated_one_off_keeps_its_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: a date other than the source's start is the owner's."""
        with app.app_context():
            source_start = derived_span(seed_periods_today[0]).start_date
            stated = source_start + timedelta(days=6)
            row = _placed(seed_user, seed_periods_today[0], due_date=stated)
            target = seed_periods_today[1]
            resp = _submit(auth_client, row, pay_period_id=str(target.id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == stated
            assert row.occurs_on == stated

    def test_a_date_typed_beside_the_move_is_the_owners(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One save, both controls touched: the typed day wins over the default."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            target = seed_periods_today[1]
            typed = derived_span(target).start_date + timedelta(days=4)
            resp = _submit(
                auth_client, row,
                pay_period_id=str(target.id), due_date=typed.isoformat(),
            )
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == typed
            assert row.occurs_on == typed


class TestAMoveOntoASiblingsPaycheckOrDayIsRefused:
    """Leaf 7b-3's two guards at the popover (found by its adversarial review).

    A bank-born envelope holds one row per paycheck (R-BAL24), and every
    row of a definition answers its own day (R-BAL25) under the occurrence
    index.  A move onto the sibling's paycheck, or a date onto the
    sibling's day, met that index as a bare *Invalid reference*; each is
    a designed 400 now, and the row is left where it was.
    """

    def test_a_period_move_onto_the_paycheck_holding_a_sibling_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The target already holds this item's row: record into that one instead."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            sibling = place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            resp = _submit(
                auth_client, row, pay_period_id=str(seed_periods_today[1].id),
            )
            assert resp.status_code == 400
            assert b"already holds this item" in resp.data
            assert b"Invalid reference" not in resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            sibling = db.session.get(Transaction, sibling.id)
            assert row.pay_period_id == seed_periods_today[0].id
            assert sibling.pay_period_id == seed_periods_today[1].id
            assert row.due_date == derived_span(seed_periods_today[0]).start_date

    def test_a_date_onto_the_day_a_sibling_answers_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An owner-stated day equal to the sibling's: pick a different day."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            sibling = place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            was = row.due_date
            resp = _submit(
                auth_client, row, due_date=sibling.due_date.isoformat(),
            )
            assert resp.status_code == 400
            assert b"already due that day" in resp.data
            assert b"Invalid reference" not in resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.due_date == was
            assert row.occurs_on == was

    def test_a_move_onto_an_empty_paycheck_beside_a_sibling_still_lands(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the guards refuse the collision, not the sibling."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            target = seed_periods_today[2]
            resp = _submit(auth_client, row, pay_period_id=str(target.id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == derived_span(target).start_date
