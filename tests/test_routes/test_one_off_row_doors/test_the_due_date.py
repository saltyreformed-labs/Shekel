"""The due date on a rule-less row: the popover offers the input, a move lands, and a clear is
refused with a sentence that names the rule.
"""

import re
from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.utils.dates import display_today
from tests._test_helpers import payback_row_of, resolved_amount, state_template_price

from ._helpers import _one_off, _placed, _card, _submit, _recurring


class TestDueDate:
    """``_gates._reject_generated_due_date_edit`` and the popover's input."""

    def test_the_popover_offers_the_input_on_a_rule_less_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An INPUT where no rule derives the day; TEXT where one does."""
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            recurring = _recurring(seed_user, seed_periods_today)
            html = auth_client.get(
                f"/transactions/{one_off.id}/full-edit",
            ).data.decode()
            assert re.search(r'<input type="date" name="due_date"', html)
            assert "Set by this recurring transaction" not in html

            html = auth_client.get(
                f"/transactions/{recurring.id}/full-edit",
            ).data.decode()
            assert not re.search(r'<input type="date" name="due_date"', html)
            assert "Set by this recurring transaction" in html

    def test_moving_the_date_lands_and_the_price_follows_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The date is the owner's to state (R-BAL22), and rule 3 reads it.

        **Measured rather than claimed** (an adversarial review of this step
        found the gate's first docstring saying the move "prices nothing
        differently", true only of the one-version series R-BAL21 gives a
        one-off at the doors leaf): a CLEARED cadence keeps every version its
        series held, so a row moved across a version boundary re-prices
        through amount rule 3 -- `$162.25` before the boundary, `$170.00`
        after it -- with no figure of its own and no flag, exactly as any
        derived row is priced on its own day.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            # The series: `$162.25` from today (the create door's version),
            # `$170.00` from a week on; the row sits before the boundary.
            today = display_today()
            state_template_price(
                one_off.template, "170.00", effective_on=today + timedelta(days=7),
            )
            db.session.commit()
            assert one_off.due_date < today + timedelta(days=7)
            assert resolved_amount(one_off) == Decimal("162.25")
            moved_to = today + timedelta(days=10)

            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "due_date": moved_to.isoformat(),
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(one_off)
            assert one_off.due_date == moved_to
            assert one_off.estimated_amount is None
            assert one_off.is_override is False
            assert resolved_amount(one_off) == Decimal("170.00")
            assert auth_client.get("/grid").status_code == 200

    def test_a_moved_date_carries_the_occurrence_with_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL25: a placed row answers its own due date, so ``occurs_on`` moves too.

        Leaf 7b-1 wrote both at birth and the popover's date move rewrote
        one: two homes for one fact, and a rule added later would have
        looked for the row at the day it no longer read.  ``one_off.
        state_due_date`` is the one writer now.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            assert row.occurs_on == row.due_date
            moved_to = row.due_date + timedelta(days=5)
            resp = _submit(auth_client, row, due_date=moved_to.isoformat())
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.due_date == moved_to
            assert row.occurs_on == moved_to

    def test_the_input_is_required_on_a_placed_row_and_not_on_a_payback(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The card refuses the clear first; the gate stays the backstop.

        The other half is a row NO definition prices: a CC payback's date
        is its owner's optional note, priced through its parent, so its box
        stays clearable.  (A legacy link-less row was that half until the
        family's cutover, ``balance:X-bi-7d-2``, minted every one a
        definition.)
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            html = _card(auth_client, row)
            box = re.search(r'<input type="date" name="due_date"[^>]*>', html)
            assert box is not None and "required" in box.group(0)

            envelope = _placed(
                seed_user, seed_periods_today[0], name="Card envelope",
                is_envelope=True,
            )
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("5.00"),
                seed_periods_today[0].start_date,
            )
            db.session.commit()
            html = _card(auth_client, payback)
            box = re.search(r'<input type="date" name="due_date"[^>]*>', html)
            assert box is not None and "required" not in box.group(0)

    def test_clearing_the_date_is_refused_with_the_reason(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A CLEAR meets the rule's own sentence, not a constraint's.

        ``ck_transactions_template_row_needs_due_date`` would refuse the
        write anyway, rendered as *Invalid reference*; the gate says why.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            was = one_off.due_date
            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "due_date": "",
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 400
            assert b"can be moved but not cleared" in resp.data
            assert b"Invalid reference" not in resp.data
            db.session.refresh(one_off)
            assert one_off.due_date == was

    def test_a_recurring_definitions_row_still_refuses_the_field(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: a generated row's date is its definition's, move or clear."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            was = recurring.due_date
            for submitted in ("2026-02-20", ""):
                resp = auth_client.patch(f"/transactions/{recurring.id}", data={
                    "due_date": submitted,
                    "version_id": recurring.version_id,
                })
                assert resp.status_code == 400
                assert b"recurring transaction" in resp.data
                db.session.refresh(recurring)
                assert recurring.due_date == was
