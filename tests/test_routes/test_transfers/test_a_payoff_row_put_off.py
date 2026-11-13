"""
Shekel Budget App -- a card's payoff row is put off by Cancel, and a $0.00 one's popover saves

Plan task plan#3699, finding plan#3028, under rulings plan#3854 ("Money that moves
is never $0 ... to skip one, cancel it") and plan#3851 (a card payment not paid
stays due the next pay period).  The example in each test is
:func:`tests._cards.payoffs_around_a_charge`'s: a Visa added owing $0.00 the day
before this paycheck, and a $75.00 Restaurant row put on it this paycheck by
Credit.  So this paycheck's payoff row reads $0.00, the next paycheck's $75.00,
and the one after that $0.00.

**A $0.00 payoff row's popover saves.**  Both popovers render the row's figure,
$0.00, in the Amount box and post it back beside the figure they showed
(ruling R-JR).  An untouched figure is no statement about the amount
(``routes._authored_figure.figure_was_authored``), so a Save of the row's notes,
its status or nothing at all goes through, and only a figure a person TYPED must
be above $0.00.  Every Save here posts what the popover's form renders, as a
browser posts it (``_statement_forms.form_fields``).  The quick edit is reached
by its own URL: no page draws the cell that opens it (task plan#3810), so the
browser drive of this task reached the full edit alone.

**Cancel puts the payment off.**  Only Projected rows are in the plan
(``cash_ledger/_facts.planned_cash_rows``), so a cancelled payoff row pays
nothing and the next paycheck's row pays what the card still owes.  Cancel and
Paid post what the popover's buttons post bar ``shown_lines``, which Paid sends
through ``hx-include``: they post what the grid's one-click Mark Paid posts, the
leg alone.
"""

from decimal import Decimal
from html.parser import HTMLParser

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from tests._cards import payoffs_around_a_charge
from tests.test_routes._statement_forms import form_fields

_ZERO = Decimal("0.00")
_OWED = Decimal("75.00")

#: The refusal a typed $0.00 meets today, the one the door's schema spoke
#: before the bound moved to the figure a person types.  Ruling plan#3854's
#: own words for it are plan#3858's to give every single item at once.
_TYPED_NOTHING_REFUSED = "amount: Must be greater than 0."


def _popover(auth_client, xfer, popover, checking):
    """GET *popover* for *xfer*, opened from its leg on *checking*; return what its form posts.

    Args:
        auth_client: The owner's logged-in client.
        xfer: The payoff row.
        popover: ``"quick"`` for the cell's inline edit, ``"full"`` for the
            full-edit popover.
        checking: The owner's checking account id, the leg the grid opens.

    Returns:
        ``(html, controls)``: the popover, and ``{name: [value, ...]}`` for
        its form.
    """
    url = (
        f"/transfers/quick-edit/{xfer.id}" if popover == "quick"
        else f"/transfers/{xfer.id}/full-edit?leg_account_id={checking}"
    )
    resp = auth_client.get(url)
    assert resp.status_code == 200
    html = resp.data.decode()
    controls: "dict[str, list[str]]" = {}
    for name, value in form_fields(
        html, f"/transfers/instance/{xfer.id}", attribute="hx-patch",
    ):
        controls.setdefault(name, []).append(value)
    return html, controls


def _shown(auth_client, xfer, checking):
    """What *xfer*'s full-edit popover shows in its Amount box: the row's price now."""
    _html, controls = _popover(auth_client, xfer, "full", checking)
    assert controls["amount"] == controls["amount_as_rendered"]
    return Decimal(controls["amount"][0])


class _AmountBoxes(HTMLParser):
    """Every input named ``amount`` on a page, by its attributes."""

    def __init__(self):
        super().__init__()
        self.boxes: "list[dict[str, str | None]]" = []

    def handle_starttag(self, tag, attrs):
        """Keep an Amount box's attributes."""
        if tag == "input" and dict(attrs).get("name") == "amount":
            self.boxes.append(dict(attrs))


def _amount_box(html):
    """The popover's one Amount box's attributes."""
    reader = _AmountBoxes()
    reader.feed(html)
    assert len(reader.boxes) == 1, reader.boxes
    return reader.boxes[0]


def _still_worked_out(xfer_id):
    """Assert the payoff row is Projected and still priced by its card: no figure of its own."""
    db.session.expire_all()
    row = db.session.get(Transfer, xfer_id)
    assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
    assert row.amount is None
    assert row.is_override is False
    return row


@pytest.mark.parametrize("popover", ["quick", "full"])
def test_a_payoff_with_nothing_to_pay_renders_a_box_its_own_figure_satisfies(
    app, auth_client, seed_user, seed_periods_today, popover,
):
    """The Amount box shows $0.00 and its floor admits it: a browser sends an untouched Save.

    A number box whose value is under its ``min`` fails the browser's own check
    and its form is never sent; the box read ``min="0.01"`` over its $0.00
    (finding plan#3028).
    """
    with app.app_context():
        _card, (now, *_) = payoffs_around_a_charge(
            auth_client, seed_user, seed_periods_today,
        )
        html, controls = _popover(
            auth_client, now, popover, seed_user["account"].id,
        )

        box = _amount_box(html)
        assert Decimal(box["value"]) == _ZERO
        assert Decimal(box["min"]) <= Decimal(box["value"])
        assert controls["amount_as_rendered"] == [box["value"]]


@pytest.mark.parametrize("popover", ["quick", "full"])
def test_an_untouched_save_of_a_payoff_with_nothing_to_pay_goes_through(
    app, auth_client, seed_user, seed_periods_today, popover,
):
    """Saved as it was opened, the $0.00 row answers 200 and is still the card's to price.

    The quick edit's Enter is how it is dismissed; the full edit's Save with
    nothing changed is the same echo.  Each was refused 422 (finding plan#3028).
    """
    with app.app_context():
        _card, (now, *_) = payoffs_around_a_charge(
            auth_client, seed_user, seed_periods_today,
        )
        checking = seed_user["account"].id
        _html, controls = _popover(auth_client, now, popover, checking)

        resp = auth_client.patch(f"/transfers/instance/{now.id}", data=controls)

        assert resp.status_code == 200, resp.data.decode()
        _still_worked_out(now.id)
        assert _shown(auth_client, now, checking) == _ZERO


def test_a_payoff_with_nothing_to_pay_saves_its_notes(
    app, auth_client, seed_user, seed_periods_today,
):
    """A note typed in the full edit is kept, and the row's figure stays the card's."""
    with app.app_context():
        _card, (now, *_) = payoffs_around_a_charge(
            auth_client, seed_user, seed_periods_today,
        )
        checking = seed_user["account"].id
        _html, controls = _popover(auth_client, now, "full", checking)
        controls["notes"] = ["Visa paid off at the counter"]

        resp = auth_client.patch(f"/transfers/instance/{now.id}", data=controls)

        assert resp.status_code == 200, resp.data.decode()
        row = _still_worked_out(now.id)
        assert row.notes == "Visa paid off at the counter"
        assert _shown(auth_client, now, checking) == _ZERO


def test_a_payoff_with_nothing_to_pay_is_cancelled_from_its_status_box(
    app, auth_client, seed_user, seed_periods_today,
):
    """Status set to Cancelled and saved in the full edit cancels the $0.00 row."""
    with app.app_context():
        _card, (now, *_) = payoffs_around_a_charge(
            auth_client, seed_user, seed_periods_today,
        )
        cancelled = str(ref_cache.status_id(StatusEnum.CANCELLED))
        _html, controls = _popover(
            auth_client, now, "full", seed_user["account"].id,
        )
        controls["status_id"] = [cancelled]

        resp = auth_client.patch(f"/transfers/instance/{now.id}", data=controls)

        assert resp.status_code == 200, resp.data.decode()
        db.session.expire_all()
        assert str(db.session.get(Transfer, now.id).status_id) == cancelled


def test_a_payoff_with_nothing_to_pay_is_cancelled_by_its_cancel_button(
    app, auth_client, seed_user, seed_periods_today,
):
    """The full edit's Cancel button, which posts its leg alone, cancels the $0.00 row."""
    with app.app_context():
        _card, (now, *_) = payoffs_around_a_charge(
            auth_client, seed_user, seed_periods_today,
        )
        checking = str(seed_user["account"].id)
        html, _controls = _popover(auth_client, now, "full", checking)
        assert f'hx-post="/transfers/instance/{now.id}/cancel"' in html

        resp = auth_client.post(
            f"/transfers/instance/{now.id}/cancel",
            data={"leg_account_id": checking},
        )

        assert resp.status_code == 200, resp.data.decode()
        db.session.expire_all()
        assert db.session.get(Transfer, now.id).status_id == ref_cache.status_id(
            StatusEnum.CANCELLED,
        )


@pytest.mark.parametrize("popover", ["quick", "full"])
def test_a_typed_zero_over_a_payoff_is_still_refused(
    app, auth_client, seed_user, seed_periods_today, popover,
):
    """$0.00 typed over the $75.00 row is refused, and the row still pays $75.00.

    Money that moves is never $0 (ruling plan#3854): to pay nothing this
    paycheck, the row is cancelled.  The box lets the figure through to the
    door, which answers it with the cell and its refusal.
    """
    with app.app_context():
        _card, (_now, next_, *_) = payoffs_around_a_charge(
            auth_client, seed_user, seed_periods_today,
        )
        checking = seed_user["account"].id
        html, controls = _popover(auth_client, next_, popover, checking)
        assert controls["amount_as_rendered"] == ["75.00"]
        assert Decimal(_amount_box(html)["min"]) <= _ZERO
        controls["amount"] = ["0.00"]

        resp = auth_client.patch(f"/transfers/instance/{next_.id}", data=controls)

        assert resp.status_code == 422
        assert _TYPED_NOTHING_REFUSED in resp.data.decode()
        _still_worked_out(next_.id)
        assert _shown(auth_client, next_, checking) == _OWED


def test_cancelled_the_payoff_is_paid_the_next_paycheck(
    app, auth_client, seed_user, seed_periods_today,
):
    """Cancelled, the $75.00 is paid by the next paycheck's row; Paid there, nothing is owed.

    Ruling plan#3854's example, at this card's figures: checking $1,000, $800
    owed, cancel Jan 16's row and Jan 30's row reads $800.  Here the next
    paycheck's row is cancelled and the one after it reads the $75.00 it read
    $0.00 for before; marked Paid it records $75.00, and the paycheck after
    that pays $0.00.  Right after the cancel, the cancelled row is still the one
    row in its paycheck.
    """
    with app.app_context():
        _card, (_now, next_, after, later, *_) = payoffs_around_a_charge(
            auth_client, seed_user, seed_periods_today,
        )
        checking = str(seed_user["account"].id)
        assert (_shown(auth_client, next_, checking), _shown(auth_client, after, checking)) == (
            _OWED, _ZERO,
        )

        assert auth_client.post(
            f"/transfers/instance/{next_.id}/cancel",
            data={"leg_account_id": checking},
        ).status_code == 200
        assert _shown(auth_client, after, checking) == _OWED
        db.session.expire_all()
        in_its_paycheck = db.session.query(Transfer).filter_by(
            transfer_template_id=next_.transfer_template_id,
            pay_period_id=next_.pay_period_id,
        ).all()
        assert [row.status_id for row in in_its_paycheck] == [
            ref_cache.status_id(StatusEnum.CANCELLED),
        ]

        assert auth_client.post(
            f"/transfers/instance/{after.id}/mark-done",
            data={"leg_account_id": checking},
        ).status_code == 200
        db.session.expire_all()
        paid = db.session.get(Transfer, after.id)
        assert paid.status_id == ref_cache.status_id(StatusEnum.DONE)
        assert paid.settled_figure == _OWED
        assert _shown(auth_client, later, checking) == _ZERO
