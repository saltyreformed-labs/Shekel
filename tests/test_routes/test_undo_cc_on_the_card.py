"""
Shekel Budget App -- Undo CC on a row on the card, through the doors (plan task plan#3055)

The popover's Undo CC (``DELETE /transactions/<id>/unmark-credit``) and its
Save with Status set to Projected take a row put on the card back off it: the
row is Projected and its payment waits on checking, so a later Paid leaves
checking (ruling plan#2887).  The popover says so beside Undo CC on every such
row (ruling plan#3104: "the popover says so before you press"), and where a
match names the payment on the card it names those bank lines too, which the
move withdraws, and posts them back with the press (rulings R-CC80, R-CC127).

**The example**: a $800.00 Groceries row on checking in the paycheck holding
today, dated today, put on the Visa by the Credit press; the Visa's bank line
for it is matched on the card's statement screen.

**Where the Visa's payoff already sent the money, the card keeps it** (plan task
plan#3095, ruling plan#3955), and the caption says by how much: the Visa added
by its door, the $800.00 Groceries in the last paycheck put on it, and this
paycheck's payoff, which works out the $800.00 owed, marked Paid on its payday.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transaction_service, transfer_service
from app.models.statement_match import StatementMatch
from app.services.balance_at import BalanceContext, cash_balance_at
from app.services.settle_day import SettleDay
from app.services.statement_match import accept_match, matched_subjects, release_match
from app.utils.dates import display_today
from app.utils.log_events import EVT_CREDIT_UNMARKED
from tests._cards import add_card, add_card_by_its_door
from tests._test_helpers import add_txn, derived_span, on_both_sides
from tests.test_services.test_service_log_events import _LogCapture
from tests.test_routes.test_a_row_on_the_card_is_drawn_as_credit import (
    _TODAY_PERIOD,
    _fresh,
    _popover,
    _the_save,
)

# Pylint: ``shekel-private-module-import`` -- the one way a test stages a bank
# line, a scope and an accepted act as the app does (``test_statement_reconcile``'s).
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_import,
)

_EIGHT_HUNDRED = Decimal("800.00")


def _status(member):
    """The ``ref.statuses.id`` of *member*."""
    return ref_cache.status_id(member)


def _on_the_card(seed_user, periods, *, account=None):
    """The Visa, and $800.00 Groceries dated today put on it (living there if *account*)."""
    card = add_card(seed_user)
    db.session.commit()
    row = add_txn(
        db.session, seed_user, periods[_TODAY_PERIOD], "Groceries", _EIGHT_HUNDRED,
        due_date=display_today(), account=card if account == "card" else None,
    )
    db.session.commit()
    if account == "card":
        transaction_service.settle_transaction(row)
    else:
        transaction_service.mark_as_credit(row)
    db.session.commit()
    return card, row


def _paid_off_on_the_card(auth_client, seed_user, periods):
    """The Visa by its door, $800.00 Groceries on it, and this paycheck's payoff Paid.

    The Visa's books open on the last paycheck's first day, owing $0.00; the
    Groceries row is in that paycheck, dated the day after, and goes on the
    Visa by Credit.  The door's payoff repeats from this paycheck, whose row
    works out the $800.00 owed the day before its payday and is marked Paid
    on that payday, so the Visa reads -800 + 800 = $0.00 today.

    Returns:
        ``(card, row)``.
    """
    last = derived_span(periods[_TODAY_PERIOD - 1]).start_date
    payday = derived_span(periods[_TODAY_PERIOD]).start_date
    card = add_card_by_its_door(auth_client, seed_user, owed_on=last)
    row = add_txn(
        db.session, seed_user, periods[_TODAY_PERIOD - 1], "Groceries",
        _EIGHT_HUNDRED, due_date=last + timedelta(days=1),
    )
    db.session.commit()
    transaction_service.mark_as_credit(row)
    db.session.commit()
    template = db.session.query(TransferTemplate).filter_by(to_account_id=card.id).one()
    payoff = db.session.query(Transfer).filter_by(
        transfer_template_id=template.id, pay_period_id=periods[_TODAY_PERIOD].id,
    ).one()
    transfer_service.settle_transfer(
        payoff.id, payoff.user_id,
        side_days=on_both_sides(
            payoff.from_account_id, payoff.to_account_id,
            SettleDay(day=payday, basis=SettledDayBasisEnum.ENTERED),
        ),
    )
    db.session.commit()
    assert _card_today(seed_user, card) == Decimal("0.00"), "precondition: paid off"
    return card, row


def _card_today(seed_user, card):
    """What *card* holds at the end of today, on a fresh pass as a page reads it."""
    ctx = BalanceContext.build(seed_user["user"].id)
    return cash_balance_at(card, ctx, ctx.as_of)


def _matched_on_the_card(seed_user, card, row):
    """Match the Visa's $800.00 GROCERIES line to *row*'s payment; return the line."""
    today = display_today()
    statement = an_import(
        seed_user, card, declared=(today - timedelta(days=60), today + timedelta(days=60)),
    )
    line = a_bank_line(
        seed_user, statement, amount="-800.00", posted_on=today, description="GROCERIES",
    )
    db.session.commit()
    scope = a_scope(seed_user, card)
    accept_match(a_submission(scope, lines=[line], transactions=[row]), scope)
    db.session.commit()
    assert matched_subjects(card.id).lines == {line.id}, "precondition: matched on the Visa"
    return line


def _caption(html, row_id):
    """The off-card caption's text, which must be drawn."""
    marker = f'id="off-card-{row_id}"'
    assert marker in html, "the popover draws no off-card caption"
    caption = html[html.index(marker):]
    return " ".join(caption[caption.index(">") + 1:caption.index("</div>")].split())


def _undo_cc_shown_lines(html):
    """The ``shown_lines`` Undo CC posts back, read off its button."""
    undo = html[html.index('aria-label="Undo credit card"') - 400:]
    undo = undo[:undo.index("Undo CC")]
    return undo[undo.index('"shown_lines": "') + len('"shown_lines": "'):].split('"')[0]


class TestThePopoverSaysWhatUndoCcDoes:
    """The caption beside Undo CC on a row on the card."""

    def test_a_matched_card_line_is_named_and_posted_back(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The caption names GROCERIES and where a later Paid goes; Undo CC posts its id."""
        card, row = _on_the_card(seed_user, seed_periods_today)
        line = _matched_on_the_card(seed_user, card, row)

        html = _popover(auth_client, row.id)

        caption = _caption(html, row.id)
        assert caption.startswith(
            "Undo CC, or setting Status back to Projected, takes this off the "
            "card and withdraws 1 accepted match",
        )
        assert "GROCERIES -$800.00" in caption
        assert caption.endswith(
            "After that, Paid takes it from Checking, and Credit puts it back "
            "on the card.",
        )
        assert _undo_cc_shown_lines(html) == str(line.id)

    def test_nothing_matched_still_says_where_a_later_paid_goes(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """No match names the payment: the caption names no line, and Undo CC posts none.

        The revert-to-edit case (the settled row's notice says "Set Status to
        Projected to change the estimate"): told nothing, the owner would
        press Paid next and take a purchase made on the Visa from Checking.
        """
        _card, row = _on_the_card(seed_user, seed_periods_today)

        html = _popover(auth_client, row.id)

        assert _caption(html, row.id) == (
            "Undo CC, or setting Status back to Projected, takes this off the "
            "card. After that, Paid takes it from Checking, and Credit puts it "
            "back on the card."
        )
        assert _undo_cc_shown_lines(html) == ""

    def test_a_bill_that_lives_on_the_card_draws_no_caption(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Its payment is on its own account, the Visa: Undo CC moves nothing, frees nothing."""
        card, row = _on_the_card(seed_user, seed_periods_today, account="card")
        _matched_on_the_card(seed_user, card, row)

        html = _popover(auth_client, row.id)

        assert "Undo CC" in html
        assert f'id="off-card-{row.id}"' not in html


class TestTheCaptionSaysWhatTheCardKeeps:
    """Its last sentence, where the payoff already sent the money."""

    def test_a_payoff_marked_paid_says_the_card_keeps_it(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Without the Groceries' payment the Visa holds 0 + 800 = $800.00, which it says."""
        _card, row = _paid_off_on_the_card(auth_client, seed_user, seed_periods_today)

        html = _popover(auth_client, row.id)

        assert _caption(html, row.id) == (
            "Undo CC, or setting Status back to Projected, takes this off the "
            "card. After that, Paid takes it from Checking, and Credit puts it "
            "back on the card. Visa would then be $800.00 in credit."
        )

    def test_the_press_leaves_the_card_holding_that_figure(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Undo CC pressed: the Visa reads the caption's $800.00, its payoff kept."""
        card, row = _paid_off_on_the_card(auth_client, seed_user, seed_periods_today)

        response = auth_client.delete(f"/transactions/{row.id}/unmark-credit")

        assert response.status_code == 200
        assert _card_today(seed_user, card) == _EIGHT_HUNDRED

    def test_a_matched_line_is_named_and_withdrawing_it_moves_nothing(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The Visa's GROCERIES line matched: both said, and the press leaves $800.00.

        The press withdraws the match as it moves the payment, and the card's
        balance reads no match (``cash_ledger.walk_cash_ledger`` walks
        movements, assertions and the opening), so the card holds what the
        caption said.
        """
        card, row = _paid_off_on_the_card(auth_client, seed_user, seed_periods_today)
        line = _matched_on_the_card(seed_user, card, row)

        html = _popover(auth_client, row.id)
        caption = _caption(html, row.id)
        response = auth_client.delete(
            f"/transactions/{row.id}/unmark-credit?shown_lines={_undo_cc_shown_lines(html)}",
        )

        assert "GROCERIES -$800.00" in caption
        assert caption.endswith("Visa would then be $800.00 in credit.")
        assert _undo_cc_shown_lines(html) == str(line.id)
        assert response.status_code == 200
        assert matched_subjects(card.id).lines == set()
        assert _card_today(seed_user, card) == _EIGHT_HUNDRED


class TestTheUndoCcPress:
    """``DELETE /transactions/<id>/unmark-credit`` on a row on the card."""

    def test_the_press_takes_the_row_off_the_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """200: Projected, the payment on checking, and the cell offers the Paid tick."""
        _card, row = _on_the_card(seed_user, seed_periods_today)

        response = auth_client.delete(f"/transactions/{row.id}/unmark-credit")

        assert response.status_code == 200
        after = _fresh(row.id)
        assert after.status_id == _status(StatusEnum.PROJECTED)
        assert [m.account_id for m in after.entries] == [seed_user["account"].id]
        body = response.get_data(as_text=True)
        assert "st-credit" not in body
        assert "paybtn" in body

    def test_naming_the_line_frees_it(self, auth_client, seed_user, seed_periods_today):
        """The line posted back: 200, the match withdrawn, the payment on checking."""
        card, row = _on_the_card(seed_user, seed_periods_today)
        line = _matched_on_the_card(seed_user, card, row)

        response = auth_client.delete(
            f"/transactions/{row.id}/unmark-credit?shown_lines={line.id}",
        )

        assert response.status_code == 200
        assert matched_subjects(card.id).lines == set()
        after = _fresh(row.id)
        assert after.status_id == _status(StatusEnum.PROJECTED)
        assert [m.account_id for m in after.entries] == [seed_user["account"].id]

    def test_naming_no_line_is_refused_and_moves_nothing(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """No line posted: refused as out of date; still on the card, still matched."""
        card, row = _on_the_card(seed_user, seed_periods_today)
        line = _matched_on_the_card(seed_user, card, row)

        response = auth_client.delete(f"/transactions/{row.id}/unmark-credit")

        assert "this page did not mention" in response.get_data(as_text=True)
        after = _fresh(row.id)
        assert after.status_id == _status(StatusEnum.DONE)
        assert [m.account_id for m in after.entries] == [card.id]
        assert matched_subjects(card.id).lines == {line.id}


    def test_a_page_naming_a_line_released_since_is_refused_and_logs_nothing(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The page named GROCERIES; another tab undid the match; Undo CC is refused, unlogged.

        The move itself frees nothing now, so it runs and owes its
        ``credit_unmarked`` to the press, which then refuses the page for
        naming a line it no longer frees (ruling R-CC127): the row stays Paid
        on the card and no event says it was taken off.
        """
        card, row = _on_the_card(seed_user, seed_periods_today)
        line = _matched_on_the_card(seed_user, card, row)
        (match,) = db.session.query(StatementMatch).all()
        release_match(match.id, seed_user["user"].id, card.id)
        db.session.commit()

        with _LogCapture("app.services.status_seam._covering") as cap:
            response = auth_client.delete(
                f"/transactions/{row.id}/unmark-credit?shown_lines={line.id}",
            )

        assert "Nothing was saved" in response.get_data(as_text=True)
        assert cap.find(EVT_CREDIT_UNMARKED) is None
        after = _fresh(row.id)
        assert after.status_id == _status(StatusEnum.DONE)
        assert [m.account_id for m in after.entries] == [card.id]


class TestStatusSetBackToProjected:
    """The popover's Save, posting what it renders with Status on Projected."""

    def test_the_save_takes_the_row_off_the_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Status = Projected: 200, Projected, the payment on checking, the match withdrawn."""
        card, row = _on_the_card(seed_user, seed_periods_today)
        _matched_on_the_card(seed_user, card, row)
        payload = _the_save(_popover(auth_client, row.id))
        payload["status_id"] = str(_status(StatusEnum.PROJECTED))

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 200
        after = _fresh(row.id)
        assert after.status_id == _status(StatusEnum.PROJECTED)
        assert [m.account_id for m in after.entries] == [seed_user["account"].id]
        assert matched_subjects(card.id).lines == set()
        assert db.session.get(Transaction, row.id).settled_on is None
