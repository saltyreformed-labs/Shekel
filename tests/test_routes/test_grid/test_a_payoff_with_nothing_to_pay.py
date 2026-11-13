"""
Shekel Budget App -- a card's payoff row with nothing to pay draws blank, and still opens

Plan task plan#3698, for the card's amendment of 2026-11-05: every card has one
payment, a payoff row each payday made with the card, blank when nothing is
owed.  The grid drew ``$0`` in every paycheck the card owed nothing.  The
example in each test: a Visa added owing $0.00 the day before this paycheck,
and a $75.00 Restaurant row put on it this paycheck by Credit.  This
paycheck's payoff pays the $0.00 owed the day before it began, the next
paycheck's the $75.00, and the paycheck after that $0.00 again.

The grid's cell, its mobile card and its Plan row draw no figure for a $0.00
payoff (``grid_view_service.draws_blank``).  The cell keeps the click, the
width and the label finding N-344 gave a zero
(``test_transaction_crud.py::test_a_zero_row_still_draws_a_click_target``):
its ``$0`` is drawn invisible, so it holds its space.
"""

import re
from datetime import timedelta
from decimal import Decimal

from flask import render_template

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.grid_view_service import draws_blank
from app.services.transfer_legs import cell_key, leg_of
from tests._cards import add_card_by_its_door
from tests._test_helpers import (
    create_transfer,
    current_pay_period,
    derived_span,
    one_off_row_of,
)

_ZERO = Decimal("0.00")

#: What a blank cell's opener holds, whitespace collapsed: the invisible `$0`
#: that keeps its width, and then the opener's own close.  No arrow, no figure.
_BLANK_OPENER = '<span class="font-mono invisible" aria-hidden="true">$0</span> </span>'

#: The label a screen reader reads on the $0.00 payoff, blank or not.
_ZERO_LABEL = 'aria-label="Transfer to Visa: $0.00 -- Projected"'


def _payoffs(auth_client, seed_user, seed_periods_today):
    """Add the Visa and put $75.00 on it this paycheck; return the card and its payoff rows.

    Returns:
        ``(card, now, next_, after)``: the card, and its payoff transfer in
        this paycheck ($0.00), the next ($75.00) and the one after ($0.00).
    """
    current = current_pay_period(seed_user["user"].id)
    card = add_card_by_its_door(
        auth_client, seed_user, owed_on=current.start_date - timedelta(days=1),
    )
    restaurant = one_off_row_of(
        current,
        name="Restaurant",
        amount=Decimal("75.00"),
        user_id=current.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.commit()
    assert auth_client.post(
        f"/transactions/{restaurant.id}/mark-credit",
    ).status_code == 200

    period_at = {derived_span(p).period_index: p.id for p in seed_periods_today}
    index = derived_span(current).period_index
    template = db.session.query(TransferTemplate).filter_by(
        to_account_id=card.id,
    ).one()
    row_in = {
        row.pay_period_id: row
        for row in db.session.query(Transfer).filter_by(
            transfer_template_id=template.id,
        )
    }
    return (card, *(row_in[period_at[index + step]] for step in (0, 1, 2)))


def _collapsed(text):
    """*text* with each run of whitespace one space, trimmed."""
    return re.sub(r"\s+", " ", text).strip()


def _cell(html, xfer, account_id):
    """The leg's desktop cell, from its wrapper to the end of its table cell."""
    start = html.index(f'<div id="xfer-leg-{xfer.id}-{account_id}">')
    return html[start:html.index("</td>", start)]


def _opener(cell):
    """What the cell draws: the ``.txn-open`` span's content, collapsed.

    It runs to the tick a Projected cell draws after it, else to the chip's
    close: no ``<div>`` is drawn inside the opener.
    """
    start = cell.index('<span class="txn-open"')
    end = cell.find("<button", start)
    if end == -1:
        end = cell.index("</div>", start)
    return _collapsed(cell[cell.index(">", start) + 1:end])


def _plan_figure(html, period_id):
    """The Visa payoff's figure in the Plan tab's block for *period_id*, collapsed."""
    start = html.index(f'id="plan-period-{period_id}"')
    row = re.search(
        r'<span class="text-truncate me-2">\s*Visa\s.*?'
        r'<span class="font-mono fw-semibold text-nowrap">(.*?)</li>',
        html[start:], re.DOTALL,
    )
    return _collapsed(row.group(1))


def test_the_grid_draws_a_payoff_with_nothing_to_pay_blank(
    app, auth_client, seed_user, seed_periods_today,
):
    """This paycheck's and the one after's $0.00 payoff draw no figure; the next's draws $75.

    The blank cell holds only its `$0`, drawn invisible, so it keeps the
    width a click needs; its label still says $0.00 -- Projected, and its tick
    is still there.  The arrow goes with the figure.
    """
    with app.app_context():
        _card, now, next_, after = _payoffs(auth_client, seed_user, seed_periods_today)
        checking = seed_user["account"].id

        html = auth_client.get("/grid?periods=3").data.decode()

        blank = _cell(html, now, checking)
        assert _opener(blank) == _BLANK_OPENER
        assert _ZERO_LABEL in blank
        assert f'hx-post="/transfers/instance/{now.id}/mark-done"' in blank
        assert _opener(_cell(html, after, checking)) == _BLANK_OPENER

        owed = _opener(_cell(html, next_, checking))
        assert owed.startswith('<span class="font-mono">$75</span>')
        assert "bi-arrow-left-right" in owed


def test_the_mobile_card_and_the_plan_row_draw_it_blank_too(
    app, auth_client, seed_user, seed_periods_today,
):
    """This Period's card names the payoff with no figure; the Plan rows hide $0 and show $75.

    The card's label still says $0.00 -- Projected, and the Plan row, which
    has no label of its own, keeps its `$0` for a screen reader alone.
    """
    with app.app_context():
        _card, now, next_, after = _payoffs(auth_client, seed_user, seed_periods_today)
        checking = seed_user["account"].id

        html = auth_client.get("/grid?periods=3").data.decode()

        start = html.index(f'data-mobile-leg="{now.id}-{checking}"')
        card = html[start:html.index("</li>", start)]
        assert _ZERO_LABEL in card
        assert _collapsed(
            card.split('<span class="font-mono fw-semibold text-nowrap">')[1],
        ) == "</span>"

        assert _plan_figure(html, next_.pay_period_id) == "$75 </span>"
        assert _plan_figure(html, after.pay_period_id) == (
            '<span class="visually-hidden">$0</span> </span>'
        )


def test_marked_paid_it_draws_its_zero_and_reverted_it_draws_blank_again(
    app, auth_client, seed_user, seed_periods_today,
):
    """Paid at $0.00 is a record of paying nothing: its cell draws $0 and the tick.

    Both cells are the one a click swaps in (the leg's own, by
    ``leg_account_id``): Mark Paid's, and the revert's, which is blank again.
    """
    with app.app_context():
        _card, now, _next, _after = _payoffs(auth_client, seed_user, seed_periods_today)
        checking = str(seed_user["account"].id)

        resp = auth_client.post(
            f"/transfers/instance/{now.id}/mark-done",
            data={"leg_account_id": checking},
        )
        assert resp.status_code == 200
        paid = _opener(resp.data.decode())
        assert paid.startswith('<span class="font-mono">$0</span>')
        assert "badge-done" in paid

        db.session.expire_all()
        resp = auth_client.patch(f"/transfers/instance/{now.id}", data={
            "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
            "version_id": str(db.session.get(Transfer, now.id).version_id),
            "leg_account_id": checking,
        })
        assert resp.status_code == 200
        reverted = resp.data.decode()
        assert _opener(reverted) == _BLANK_OPENER
        assert _ZERO_LABEL in reverted


def test_a_blank_cell_draws_no_due_caption(
    app, auth_client, seed_user, seed_periods_today,
):
    """A due caption published for the $0.00 payoff is not drawn; for the $75.00 one it is.

    A worked-out payment repeating on each statement's due date is captioned
    with its due date (``grid_view_service.due_captions_by_key``), and with
    nothing to pay the caption goes with the figure.  Each cell is rendered
    with the maps the grid hands it, a caption 10 days into its paycheck
    published for both.
    """
    with app.app_context():
        _card, now, next_, _after = _payoffs(auth_client, seed_user, seed_periods_today)

        def drawn(xfer, shown):
            """*xfer*'s cell on checking, showing *shown* and captioned."""
            leg = leg_of(xfer, seed_user["account"].id)
            key = cell_key(leg)
            due = xfer.pay_period.start_date + timedelta(days=10)
            with app.test_request_context():
                html = render_template(
                    "grid/_transaction_cell.html",
                    txn=leg,
                    account=seed_user["account"],
                    budgets={key: shown},
                    settled={key: None},
                    retained={key: None},
                    due_captions={key: due},
                    credit_opens_popover={key: None},
                )
            return f"Due: {due.month}/{due.day}" in html

        assert (drawn(now, _ZERO), drawn(next_, Decimal("75.00"))) == (False, True)


def test_only_a_projected_card_payoff_showing_zero_draws_blank(
    app, auth_client, seed_user, seed_periods_today,
):
    """Each clause of ``draws_blank``, one case apart from the payoff on either side.

    A tick that would book $75.00 has that to show; a $75.00 figure is shown;
    an extra payment into the card, Projected and shown at $0.00 here, is not
    the card's worked-out one; a $0.00 bill prints its zero (finding N-344).
    Paid is graded by the test above.
    """
    with app.app_context():
        card, now, _next, _after = _payoffs(auth_client, seed_user, seed_periods_today)
        checking = seed_user["account"].id
        projected = ref_cache.status_id(StatusEnum.PROJECTED)
        payoff = leg_of(now, checking)
        extra = create_transfer(
            seed_user, db.session, seed_user["account"], card, now.pay_period,
            Decimal("100.00"), name="Extra to the Visa",
        )
        bill = one_off_row_of(
            now.pay_period,
            name="Snacks",
            amount=_ZERO,
            user_id=seed_user["user"].id,
            account_id=checking,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=seed_user["categories"]["Groceries"].id,
        )
        assert bill.status_id == projected

        assert {
            "the payoff on checking": draws_blank(payoff, _ZERO, None),
            "the payoff on the card": draws_blank(leg_of(now, card.id), _ZERO, None),
            "a tick would book $75.00": draws_blank(payoff, _ZERO, Decimal("75.00")),
            "showing $75.00": draws_blank(payoff, Decimal("75.00"), None),
            "an extra payment": draws_blank(leg_of(extra, checking), _ZERO, None),
            "a $0.00 bill": draws_blank(bill, _ZERO, None),
        } == {
            "the payoff on checking": True,
            "the payoff on the card": True,
            "a tick would book $75.00": False,
            "showing $75.00": False,
            "an extra payment": False,
            "a $0.00 bill": False,
        }
