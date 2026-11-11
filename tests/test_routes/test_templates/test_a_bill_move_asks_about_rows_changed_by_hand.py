"""Template route tests: moving a bill to another account asks before moving a row changed by hand.

Ruling plan#3230, task plan#3237.  Josh's words: "I don't think the hand edited
bills should move without alerting the end user first. A hand edit indicates
something different about that particular bill."  A save that moves a bill
stops on the conflict chooser, listing each unpaid row changed by hand with
"Move to <new account>" or "Keep on <its account>"; nothing saves until he
answers, and Apply re-runs the edit with his answers.  A kept row stays on its
account with its hand change, a moved one takes the new account and keeps it,
and paid rows never move.  An edit that changes the price and the account asks
both on one page (plan#2931's review, L1: "use" used to take the new price on
the old account and drop the row's hold).
"""

import html
from decimal import Decimal
from typing import NamedTuple

from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import recurrence_engine, transaction_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    create_account_of_type,
    create_savings_account,
    moved_by_the_owner,
    repriced_by_the_owner,
    resolved_amount,
    restate_account_opening,
    settle_cash_row,
)
from app.utils.dates import display_today
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests.test_routes._statement_forms import form_fields

from ._helpers import _TODAYS_PERIOD_INDEX, _create_template

TODAY = _TODAYS_PERIOD_INDEX


class _Bill(NamedTuple):
    """The ids one case reads: the bill, its accounts and its rows by paycheck index."""

    tid: int
    checking: int
    savings: int
    rows: dict


def _a_bill(seed_user, periods, *, from_index=0, savings_opened_on=None, account_id=None):
    """A $1,200 Rent bill with a row in each of *periods* from *from_index*.

    On *account_id* (default: checking).  Also a Savings account to move it
    to, whose books open on *savings_opened_on* (default: the first
    paycheck's start, before every row, so no move lands inside them).
    Committed.

    Returns:
        A :class:`_Bill`, its ``rows`` keyed by the index into *periods* of
        the paycheck each row was generated in.
    """
    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("5000.00"),
        observed_on=periods[0].start_date,
    )
    if savings_opened_on is not None:
        restate_account_opening(db.session, savings, savings_opened_on)
    savings = savings.id
    template = _create_template(seed_user, cadence=EVERY_PERIOD)
    template.account_id = account_id or seed_user["account"].id
    recurrence_engine.generate_for_template(
        template, GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id),
            {p.id for p in periods[from_index:]},
        ), seed_user["scenario"].id,
    )
    db.session.flush()
    index_of = {period.id: index for index, period in enumerate(periods)}
    rows = {
        index_of[row.pay_period_id]: row.id
        for row in db.session.query(Transaction).filter_by(template_id=template.id)
    }
    db.session.commit()
    return _Bill(template.id, seed_user["account"].id, savings, rows)


def _moved_by_its_popover(client, row, period):
    """Move *row* into *period* through its popover, posting what the popover renders.

    The browser's save (``tests/test_routes/test_card_row_moved``'s shape),
    so the move door's own re-date applies.

    Returns:
        *row*, refreshed.
    """
    page = client.get(f"/transactions/{row.id}/full-edit")
    assert page.status_code == 200
    payload = [
        pair for pair in form_fields(
            page.data.decode(), f"/transactions/{row.id}", attribute="hx-patch",
        )
        if pair[0] != "pay_period_id"
    ]
    payload.append(("pay_period_id", str(period.id)))
    response = client.patch(f"/transactions/{row.id}", data=MultiDict(payload))
    assert response.status_code == 200, response.data[:400]
    db.session.refresh(row)
    return row


def _row(row_id):
    """Return the stored row, read fresh."""
    db.session.expire_all()
    return db.session.get(Transaction, row_id)


def _page(resp):
    """Return a response's HTML as a reader sees its text: entities decoded, whitespace collapsed."""
    return " ".join(html.unescape(resp.get_data(as_text=True)).split())


def _moved(bill, **fields):
    """The edit form's POST moving *bill* to Savings, with any other *fields*."""
    return {"name": "Rent", "account_id": str(bill.savings), **fields}


def _apply(bill, decisions, **fields):
    """The chooser's Apply for :func:`_moved`: the same edit, with the answers."""
    return {**_moved(bill, **fields), "conflict_apply": "1", **decisions}


class TestTheSaveAsksBeforeMovingARowChangedByHand:
    """POST /templates/<id> with a new account over rows changed by hand."""

    def test_an_account_move_over_a_repriced_row_stops_and_saves_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The save renders the chooser naming the row; the bill and every row stay put.

        The page asks only about the account: the edit stated no price, so
        no row carries the price question.
        """
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 2]
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_moved(bill))

            assert resp.status_code == 200
            text = _page(resp)
            assert "You moved <strong>Rent</strong> to <strong>Savings</strong>" in text
            assert f'name="conflict_account_{hand}"' in text
            assert "Keep on Checking" in text and "Move to Savings" in text
            assert "conflict_decision_" not in text, "an account move asked the price"
            assert db.session.get(TransactionTemplate, bill.tid).account_id == bill.checking
            assert {_row(row_id).account_id for row_id in bill.rows.values()} == {
                bill.checking,
            }, "a row moved before the owner answered"

    def test_move_puts_the_row_on_the_new_account_with_its_figure_and_hold(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Move to Savings": the row is on Savings, still $1,500.00 and still his."""
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 2]
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {f"conflict_account_{hand}": "use"},
            ))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            row = _row(hand)
            assert row.account_id == bill.savings
            assert resolved_amount(row) == Decimal("1500.00")
            assert row.is_override is True
            assert db.session.get(TransactionTemplate, bill.tid).account_id == bill.savings
            assert {
                _row(bill.rows[index]).account_id
                for index in range(TODAY, len(seed_periods_today))
            } == {bill.savings}

    def test_keep_leaves_the_row_and_a_later_save_does_not_move_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Keep on Checking": the row stays there, and the next save leaves it alone.

        The next save does not move the account, so it asks nothing and
        commits; the kept row is still held, so its regeneration skips it.
        """
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 2]
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {f"conflict_account_{hand}": "keep"},
            ))
            assert resp.status_code == 302, resp.get_data(as_text=True)
            later = auth_client.post(f"/templates/{bill.tid}", data=_moved(
                bill, name="Apartment Rent",
            ))
            assert later.status_code == 302, later.get_data(as_text=True)

            row = _row(hand)
            assert row.account_id == bill.checking
            assert resolved_amount(row) == Decimal("1500.00")
            assert row.is_override is True
            assert row.name == "Apartment Rent"
            assert _row(bill.rows[TODAY + 3]).account_id == bill.savings

    def test_a_row_moved_to_another_paycheck_is_asked_and_moves_in_that_paycheck(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A moved paycheck is a hand change too: asked, and moved it keeps the paycheck."""
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 3]
            into = seed_periods_today[TODAY + 4]
            moved_by_the_owner(db.session.get(Transaction, hand), into=into)
            db.session.commit()

            asked = auth_client.post(f"/templates/{bill.tid}", data=_moved(bill))
            assert asked.status_code == 200
            assert f'name="conflict_account_{hand}"' in asked.get_data(as_text=True)
            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {f"conflict_account_{hand}": "use"},
            ))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            row = _row(hand)
            assert row.account_id == bill.savings
            assert row.pay_period_id == into.id
            assert row.is_override is True
            assert resolved_amount(row) == Decimal("1200.00")

    def test_a_crafted_answer_moves_only_a_row_the_page_asked_about(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Move" on a paid row, or on a re-priced row before the save's window, moves neither.

        The earlier row is this bill's own, changed by hand and still unpaid,
        so the engine's move would take it (it is held, live, and off the
        bill's new account); only the page's allow-list -- the rows the save
        asked about -- keeps it.  The same Apply moves the row it asked about,
        so one save shows the list admitting that row and refusing the others.
        """
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand, paid, earlier = (
                bill.rows[TODAY + 2], bill.rows[TODAY + 1], bill.rows[TODAY - 1],
            )
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            repriced_by_the_owner(db.session.get(Transaction, earlier), "1300.00")
            settle_cash_row(db.session.get(Transaction, paid))
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(bill, {
                f"conflict_account_{hand}": "use",
                f"conflict_account_{paid}": "use",
                f"conflict_account_{earlier}": "use",
            }))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            assert _row(hand).account_id == bill.savings
            paid_row = _row(paid)
            assert paid_row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert paid_row.account_id == bill.checking, "a paid row moved"
            assert _row(earlier).account_id == bill.checking, (
                "a row the save did not ask about was moved"
            )

    def test_an_account_move_over_only_a_deleted_row_saves_without_asking(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A deleted row is on no balance: nothing to ask, so the save goes through.

        It stays deleted where it was; "use" on a later page brings it back
        on the bill's account (the price-and-account class below).
        """
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            gone = bill.rows[TODAY + 2]
            transaction_service.delete_transaction(
                db.session.get(Transaction, gone), seed_user["user"].id,
            )
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_moved(bill))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            row = _row(gone)
            assert row.is_deleted is True
            assert row.account_id == bill.checking
            assert _row(bill.rows[TODAY + 3]).account_id == bill.savings


class TestAPriceAndAnAccountAskBothOnOnePage:
    """One save restating the price and moving the account, over rows changed by hand."""

    def test_both_questions_on_one_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The re-priced row carries the price radios and the account radios."""
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 2]
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{bill.tid}", data=_moved(bill, amount="1400.00"),
            )

            assert resp.status_code == 200
            text = _page(resp)
            assert (
                'You changed <strong>Rent</strong> to <span class="font-mono">'
                "$1,400.00</span> and moved it to <strong>Savings</strong>"
            ) in text
            assert f'name="conflict_decision_{hand}"' in text
            assert f'name="conflict_account_{hand}"' in text

    def test_the_new_price_kept_on_the_old_account_holds_the_row_there(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Follow the price, Keep on Checking: $1,400.00 on Checking, still held.

        plan#2931's review (L1): "use" cleared the row's hold, so it read the
        new price on the old account and the next save moved it unasked.
        Here the next save (a rename, the account not moved) leaves it.
        """
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 2]
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {
                    f"conflict_decision_{hand}": "use",
                    f"conflict_account_{hand}": "keep",
                }, amount="1400.00",
            ))
            assert resp.status_code == 302, resp.get_data(as_text=True)
            later = auth_client.post(f"/templates/{bill.tid}", data=_moved(
                bill, name="Apartment Rent",
            ))
            assert later.status_code == 302, later.get_data(as_text=True)

            row = _row(hand)
            assert row.account_id == bill.checking, "the kept row moved unasked"
            assert resolved_amount(row) == Decimal("1400.00")
            assert row.is_override is True

    def test_the_new_price_moved_hands_the_row_back_whole(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Follow the price, Move to Savings: $1,400.00 on Savings, the bill's own row again."""
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 2]
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {
                    f"conflict_decision_{hand}": "use",
                    f"conflict_account_{hand}": "use",
                }, amount="1400.00",
            ))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            row = _row(hand)
            assert row.account_id == bill.savings
            assert resolved_amount(row) == Decimal("1400.00")
            assert row.is_override is False

    def test_a_deleted_row_brought_back_returns_on_the_new_account(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Follow the template" on a deleted row restores it on Savings, at $1,400.00.

        Before ruling plan#3230 it came back on Checking, where the next save
        then moved it.
        """
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            gone = bill.rows[TODAY + 2]
            transaction_service.delete_transaction(
                db.session.get(Transaction, gone), seed_user["user"].id,
            )
            db.session.commit()

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {f"conflict_decision_{gone}": "use"}, amount="1400.00",
            ))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            row = _row(gone)
            assert row.is_deleted is False
            assert row.account_id == bill.savings
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("1400.00")


    def test_a_row_repriced_then_deleted_comes_back_on_the_new_account(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-priced to $1,600.00, then deleted: "use" restores it on Savings, the bill's own.

        A delete keeps the row's hold, so the save files it among the rows
        changed by hand, and the account question asks nothing of a deleted
        row.  It came back on Checking, held there for good (the review of
        task plan#3237, H1).
        """
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            gone = bill.rows[TODAY + 3]
            repriced_by_the_owner(db.session.get(Transaction, gone), "1600.00")
            transaction_service.delete_transaction(
                db.session.get(Transaction, gone), seed_user["user"].id,
            )
            db.session.commit()
            assert (_row(gone).is_deleted, _row(gone).is_override) == (True, True)

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {f"conflict_decision_{gone}": "use"}, amount="1400.00",
            ))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            row = _row(gone)
            assert row.is_deleted is False
            assert row.account_id == bill.savings
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("1400.00")

    def test_a_deleted_row_inside_the_new_accounts_books_stays_deleted_and_the_page_names_the_move(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Only a deleted row to ask about: the page still says where the bill moved.

        Savings' books open today and the deleted row is due on today's
        payday, so "use" leaves it deleted (ruling R-BAL253), named inside
        Savings' books -- the account it now sits on, deleted, where no
        balance counts it.
        """
        today = display_today()
        with app.app_context():
            bill = _a_bill(
                seed_user, seed_periods_today, from_index=TODAY,
                savings_opened_on=today,
            )
            gone = bill.rows[TODAY]
            transaction_service.delete_transaction(
                db.session.get(Transaction, gone), seed_user["user"].id,
            )
            db.session.commit()

            asked = auth_client.post(
                f"/templates/{bill.tid}", data=_moved(bill, amount="1400.00"),
            )
            assert asked.status_code == 200, asked.get_data(as_text=True)
            text = _page(asked)
            assert "and moved it to <strong>Savings</strong>" in text
            assert "conflict_account_" not in text

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {f"conflict_decision_{gone}": "use"}, amount="1400.00",
            ), follow_redirects=True)
            assert resp.status_code == 200
            assert "inside Savings's books" in _page(resp)
            row = _row(gone)
            assert (row.is_deleted, row.account_id) == (True, bill.savings)


class TestARowLeavingACard:
    """A card's row moved off the card by the account question is read where it lands."""

    def test_a_cards_row_moved_and_handed_back_is_its_bills_own(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Moved to Savings and handed back for its price, a row held by a card move is unheld.

        The card's move door holds a bill it moved back a paycheck (ruling
        plan#2991); off the card that reason is gone.  The row's account was
        written as a column alone, so the card-move hold read the card it had
        left and kept the hold (the review of task plan#3237, M1).
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("0.00"),
                observed_on=seed_periods_today[0].start_date,
            )
            bill = _a_bill(
                seed_user, seed_periods_today, from_index=TODAY, account_id=card.id,
            )
            hand = bill.rows[TODAY + 1]
            moved = _moved_by_its_popover(
                auth_client, db.session.get(Transaction, hand), seed_periods_today[TODAY],
            )
            assert moved.is_override is True

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {
                    f"conflict_decision_{hand}": "use",
                    f"conflict_account_{hand}": "use",
                }, amount="1400.00",
            ))

            assert resp.status_code == 302, resp.get_data(as_text=True)
            row = _row(hand)
            assert row.account_id == bill.savings
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("1400.00")


class TestTheNewAccountsBooks:
    """A row the new account's books would count twice is not offered the move."""

    def test_a_row_inside_the_new_accounts_books_stays_and_says_why(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A card's bill moved back into today's paycheck is dated today; Savings opens today.

        The card's move door dates a bill moved back into a paycheck that has
        started today (ruling plan#2991), so the row's own day is today while
        it still answers next paycheck's occurrence.  Savings' books open
        today, so moved there it would sit inside its opening and be counted
        twice (ruling R-PC99).  Nothing else the save leaves is inside them --
        the bill's rows start at today's paycheck, whose own row is deleted --
        so the save reaches the page, which says the row stays; a crafted
        "Move" leaves it on the card.
        """
        today = display_today()
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("0.00"),
                observed_on=seed_periods_today[0].start_date,
            )
            bill = _a_bill(
                seed_user, seed_periods_today, from_index=TODAY,
                savings_opened_on=today, account_id=card.id,
            )
            transaction_service.delete_transaction(
                db.session.get(Transaction, bill.rows[TODAY]), seed_user["user"].id,
            )
            db.session.commit()
            hand = bill.rows[TODAY + 1]
            moved = _moved_by_its_popover(
                auth_client, db.session.get(Transaction, hand), seed_periods_today[TODAY],
            )
            assert (moved.due_date, moved.is_override) == (today, True), (
                "the premise: the card's move door dates the row today and holds it"
            )

            asked = auth_client.post(f"/templates/{bill.tid}", data=_moved(bill))
            assert asked.status_code == 200, asked.get_data(as_text=True)
            text = _page(asked)
            assert (
                f"Stays on Visa: Savings's books open {today.strftime('%b %d, %Y')}, "
                "so moving it there would count it twice."
            ) in text
            assert f'name="conflict_account_{hand}"' not in text

            resp = auth_client.post(f"/templates/{bill.tid}", data=_apply(
                bill, {f"conflict_account_{hand}": "use"},
            ))
            assert resp.status_code == 302, resp.get_data(as_text=True)
            assert _row(hand).account_id == card.id
            assert _row(bill.rows[TODAY + 2]).account_id == bill.savings


class TestTheMoveActRefusesWhatItMayNotMove:
    """``recurrence_engine.resolve_conflicts(..., "move", ...)`` called directly."""

    def test_a_paid_row_and_a_deleted_row_stay_and_a_held_row_moves(
        self, app, seed_user, seed_periods_today,
    ):
        """The engine moves only a live, unpaid row changed by hand, whatever ids it is given."""
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand, paid, gone = (bill.rows[TODAY + i] for i in (1, 2, 3))
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            settle_cash_row(db.session.get(Transaction, paid))
            transaction_service.delete_transaction(
                db.session.get(Transaction, gone), seed_user["user"].id,
            )
            db.session.get(TransactionTemplate, bill.tid).account_id = bill.savings
            db.session.flush()

            notice = recurrence_engine.resolve_conflicts(
                [hand, paid, gone], "move", BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert notice is None
            assert _row(hand).account_id == bill.savings
            assert _row(paid).account_id == bill.checking
            assert _row(gone).account_id == bill.checking


    def test_another_owners_pass_moves_nothing(
        self, app, seed_user, seed_periods_today, second_user,
    ):
        """The row's own owner moves it; another owner's pass, handed its id, does not."""
        with app.app_context():
            bill = _a_bill(seed_user, seed_periods_today)
            hand = bill.rows[TODAY + 1]
            repriced_by_the_owner(db.session.get(Transaction, hand), "1500.00")
            db.session.get(TransactionTemplate, bill.tid).account_id = bill.savings
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [hand], "move", BalanceContext.build(second_user["user"].id),
            )
            db.session.flush()
            assert _row(hand).account_id == bill.checking, "another owner moved the row"

            recurrence_engine.resolve_conflicts(
                [hand], "move", BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert _row(hand).account_id == bill.savings
