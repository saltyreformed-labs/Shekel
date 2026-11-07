"""
Shekel Budget App -- a repeating row moved on the card keeps or takes its date

Plan step **credit_card:CC-6**, its task plan#3026 (ruling **plan#2991**, option A,
amending balance:X-au-e and balance:X-au-f for a card).  A repeating bill whose date has
passed keeps it.  One dated ahead moves with its paycheck: the new paycheck's first day,
or today if that paycheck has started.  A standing transfer always follows that way, unless
a new date is typed in the same save.  The row keeps the occurrence it answers, so the
repeat writes no second row, and its date box opens.  Unchanged: off a card, an envelope and
a worked-out payment.

**The failing control.**  Before this task every move left a repeating row's date alone, so
each re-dating assertion below read the date its rule gave it, and the figures were the ones
ruling plan#2991's option B states: Example 1 $1,000.00 on Jan 30, Example 4 $0.00.

**The money unless a case says otherwise.**  The card closes on the 5th and falls due on the
27th.  Paydays are Jan 2, Jan 16, Jan 30, Feb 13 and Feb 27, and the payoff row repeats on
each.  Checking opens at $1,000.00.  **A case's clock is its fixture's**, asked for ahead of
``auth_client``, because a login followed by a travel back answers 302.
"""

from datetime import date
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import PeriodPlacementEnum, TxnTypeEnum
from app.exceptions import RecurrenceConflict
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import recurrence_engine, transfer_recurrence, transfer_service
from app.services.balance_at import BalanceContext
from app.services.card_today import date_left_by_a_move
from app.services.cash_ledger import (
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today
from tests._test_helpers import (
    create_account_of_type,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY
from tests.test_routes._statement_forms import form_fields
from tests.test_routes.test_card_row_with_no_date import _held_at
from tests.test_services.test_card_payment_follows_what_is_owed import (
    _balances,
    _payoff,
    _true_up,
)
from tests.test_services.test_card_payment_price import _card, _price, _row

_ZERO = Decimal("0.00")
_JAN_16 = date(2026, 1, 16)
_JAN_18 = date(2026, 1, 18)
_JAN_20 = date(2026, 1, 20)
_JAN_22 = date(2026, 1, 22)
_JAN_25 = date(2026, 1, 25)
_JAN_30 = date(2026, 1, 30)
_FEB_1 = date(2026, 2, 1)
_FEB_3 = date(2026, 2, 3)
_FEB_5 = date(2026, 2, 5)
_FEB_13 = date(2026, 2, 13)
_FEB_27 = date(2026, 2, 27)


@pytest.fixture(name="on_jan_22")
def _on_jan_22():
    """Today is Jan 22, inside Jan 16's paycheck."""
    with _held_at(_JAN_22):
        yield


@pytest.fixture(name="on_feb_14")
def _on_feb_14():
    """Today is Feb 14, Jan 30's paycheck over."""
    with _held_at(date(2026, 2, 14)):
        yield


@pytest.fixture(name="on_feb_1")
def _on_feb_1():
    """Today is Feb 1, inside Jan 30's paycheck, which is what Carry Forward carries to."""
    with _held_at(_FEB_1):
        yield


def _schedule(user_id):
    """The owner's whole schedule, as a door hands it to a pass."""
    return GenerationSchedule.for_pass(BalanceContext.build(user_id))


def _bill(seed_user, account, starts_on, price="100.00"):
    """A monthly bill on *account* from *starts_on*, its rows generated and committed."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account.id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Streaming",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal(price))
    make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    db.session.flush()
    recurrence_engine.generate_for_template(
        template, _schedule(template.user_id), seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _standing(seed_user, card, starts_on, price="200.00"):
    """A monthly fixed transfer into *card* from *starts_on*, generated and committed."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=card.id,
        name="Extra to the card",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal(price))
    make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    db.session.flush()
    transfer_recurrence.generate_for_template(
        template, _schedule(template.user_id), seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _row_of(template, occurs_on):
    """The live row of *template* answering *occurs_on*."""
    if isinstance(template, TransferTemplate):
        return db.session.query(Transfer).filter_by(
            transfer_template_id=template.id, occurs_on=occurs_on, is_deleted=False,
        ).one()
    return db.session.query(Transaction).filter_by(
        template_id=template.id, occurs_on=occurs_on, is_deleted=False,
    ).one()


def _is_transfer(row):
    """Whether *row* is a transfer."""
    return isinstance(row, Transfer)


def _rendered(client, row):
    """Every control *row*'s popover renders, as a browser would post it."""
    page = client.get(
        f"/transfers/{row.id}/full-edit" if _is_transfer(row)
        else f"/transactions/{row.id}/full-edit",
    )
    assert page.status_code == 200
    door = (
        f"/transfers/instance/{row.id}" if _is_transfer(row)
        else f"/transactions/{row.id}"
    )
    return door, form_fields(page.data.decode(), door, attribute="hx-patch")


def _save(client, row, **typed):
    """PATCH *row* with what its popover renders, *typed* over it."""
    door, rendered = _rendered(client, row)
    payload = [pair for pair in rendered if pair[0] not in typed]
    payload.extend(typed.items())
    return client.patch(door, data=MultiDict(payload))


def _move(client, row, period, **typed):
    """Move *row* to *period* by its popover, and read it back."""
    response = _save(client, row, pay_period_id=str(period.id), **typed)
    assert response.status_code == 200, response.data[:400]
    db.session.refresh(row)
    assert row.pay_period_id == period.id
    return row


def _date_box(client, row):
    """Whether *row*'s popover renders a date box."""
    _, rendered = _rendered(client, row)
    return "due_date" in dict(rendered)


def _carry(client, period):
    """Press Carry Forward on *period*."""
    response = client.post(f"/pay-periods/{period.id}/carry-forward", data={})
    assert response.status_code == 200, response.data[:400]


def _dates(row):
    """The row's due date and the occurrence it answers, read back."""
    db.session.refresh(row)
    return row.due_date, row.occurs_on


def _reads(row, as_of):
    """What a fresh pass held at *as_of* prices *row* at."""
    basis = BalanceContext.build(row.user_id, as_of=as_of).amounts()
    if _is_transfer(row):
        return resolve_transfer_amount(row, basis)
    return resolve_transaction_amount(row, basis)


def _regenerate(template, scenario_id):
    """Run the maintain pass as a template door does, from today, and answer what it held."""
    engine = (
        transfer_recurrence if isinstance(template, TransferTemplate)
        else recurrence_engine
    )
    try:
        engine.regenerate_for_template(
            template, _schedule(template.user_id), scenario_id,
            effective_from=display_today(),
        )
    except RecurrenceConflict as conflict:
        db.session.commit()
        return conflict
    db.session.commit()
    return None


def _use(template, row):
    """Answer "Follow the template" for *row* on the chooser."""
    engine = (
        transfer_recurrence if isinstance(template, TransferTemplate)
        else recurrence_engine
    )
    engine.resolve_conflicts([row.id], "update", BalanceContext.build(template.user_id))
    db.session.commit()


class TestARepeatingBillMoved:
    """The ruling's examples for a bill, through the popover."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_example_1_moved_back_it_takes_today(
        self, auth_client, seed_user, seed_periods,
    ):
        """$0 trued up Jan 20, moved Jan 22 back to Jan 16's paycheck: dated Jan 22.

        The true-up cannot hold it, so Jan 30 pays $100.00 and checking is 1,000 - 100 =
        $900.00.  Left on Jan 30 it paid Feb 13 and checking read $1,000.00 on Jan 30.
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        bill = _bill(seed_user, card, _JAN_30)
        _true_up(card, "0.00", _JAN_20)
        row = _row_of(bill, _JAN_30)

        _move(auth_client, row, seed_periods[1])

        assert _dates(row) == (_JAN_22, _JAN_30)
        assert _price(payoff, _JAN_30, _JAN_22) == Decimal("100.00")
        assert _balances(seed_user, card, _JAN_22, _JAN_30)[0] == Decimal("900.00")

    @pytest.mark.usefixtures("on_jan_22")
    def test_example_2_moved_forward_it_follows_its_paycheck(
        self, auth_client, seed_user, seed_periods,
    ):
        """Moved Jan 22 to Feb 13's paycheck: dated Feb 13, Feb 13 $0.00, Feb 27 $100.00.

        Checking is 1,000 - 100 = $900.00 on Feb 27.  Left on Jan 30, Feb 13 paid it.
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        bill = _bill(seed_user, card, _JAN_30)
        _true_up(card, "0.00", _JAN_20)
        row = _row_of(bill, _JAN_30)

        _move(auth_client, row, seed_periods[3])

        assert _dates(row) == (_FEB_13, _JAN_30)
        assert _price(payoff, _FEB_13, _JAN_22) == _ZERO
        assert _price(payoff, _FEB_27, _JAN_22) == Decimal("100.00")
        assert _balances(seed_user, card, _JAN_22, _FEB_27)[0] == Decimal("900.00")

    @pytest.mark.usefixtures("on_jan_22")
    def test_example_3_across_a_price_change_it_is_re_priced(
        self, auth_client, seed_user, seed_periods,
    ):
        """As example 2, its price $120 from Feb 1: reads $120, Feb 27 pays it.

        Checking is 1,000 - 120 = $880.00 on Feb 27.  Left on Jan 30 it read $100.00.
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        bill = _bill(seed_user, card, _JAN_30)
        state_template_price(bill, Decimal("120.00"), effective_on=_FEB_1)
        db.session.commit()
        _true_up(card, "0.00", _JAN_20)
        row = _row_of(bill, _JAN_30)
        assert _reads(row, _JAN_22) == Decimal("100.00")

        _move(auth_client, row, seed_periods[3])

        assert _reads(row, _JAN_22) == Decimal("120.00")
        assert _price(payoff, _FEB_27, _JAN_22) == Decimal("120.00")
        assert _balances(seed_user, card, _JAN_22, _FEB_27)[0] == Decimal("880.00")

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_typed_over_figure_is_not_re_priced(
        self, auth_client, seed_user, seed_periods,
    ):
        """The same move of a row typed to $150: it owns its figure, and stays $150."""
        card = _card(seed_user)
        _payoff(seed_user, card)
        bill = _bill(seed_user, card, _JAN_30)
        state_template_price(bill, Decimal("120.00"), effective_on=_FEB_1)
        db.session.commit()
        row = _row_of(bill, _JAN_30)
        response = _save(auth_client, row, estimated_amount="150.00")
        assert response.status_code == 200, response.data[:400]

        _move(auth_client, row, seed_periods[3])

        assert _dates(row) == (_FEB_13, _JAN_30)
        assert _reads(row, _JAN_22) == Decimal("150.00")

    @pytest.mark.usefixtures("on_jan_22")
    def test_the_repeat_writes_no_second_row(
        self, auth_client, seed_user, seed_periods,
    ):
        """The row answers its occurrence, so a pass writes no second row for Jan 30."""
        card = _card(seed_user)
        _payoff(seed_user, card)
        bill = _bill(seed_user, card, _JAN_30)
        row = _row_of(bill, _JAN_30)
        before = db.session.query(Transaction).filter_by(template_id=bill.id).count()

        _move(auth_client, row, seed_periods[3])
        conflict = _regenerate(bill, seed_user["scenario"].id)

        # A held row is routed away from the pass's write, so its date stays.
        assert conflict.overridden == [row.id]
        assert db.session.query(Transaction).filter_by(template_id=bill.id).count() == before
        assert _dates(row) == (_FEB_13, _JAN_30)


class TestABillAlreadyBought:
    """A repeating bill whose date has passed keeps it."""

    @pytest.mark.usefixtures("on_feb_1")
    def test_example_5_carried_forward_it_stays(
        self, auth_client, seed_user, seed_periods,
    ):
        """$100 dated Jan 16, $100 trued up Jan 20, Carry Forward Feb 1: stays Jan 16.

        The true-up holds it, so it counts once and checking is $900.00 on Feb 13.
        """
        card = _card(seed_user)
        _payoff(seed_user, card)
        bill = _bill(seed_user, card, _JAN_16)
        _true_up(card, "100.00", _JAN_20)
        row = _row_of(bill, _JAN_16)

        _carry(auth_client, seed_periods[1])

        db.session.refresh(row)
        assert _dates(row) == (_JAN_16, _JAN_16)
        assert row.pay_period_id == seed_periods[2].id
        assert row.is_override is True
        assert _balances(seed_user, card, _FEB_1, _FEB_13)[0] == Decimal("900.00")


class TestCarriedOffItsDate:
    """Carry Forward's batch writes the date its modal says, and no occurrence."""

    @pytest.mark.usefixtures("on_feb_1")
    def test_one_batch_keeps_one_date_and_takes_another(
        self, auth_client, seed_user, seed_periods,
    ):
        """Two bills in Jan 16's paycheck, carried Feb 1 into Jan 30's.

        One dated Jan 16 has passed and stays.  One due Feb 5, moved there and typed to
        Feb 10, is still ahead, and the paycheck it reaches is its own, so it takes its
        rule's day, Feb 5.  Neither answers a new occurrence.
        """
        card = _card(seed_user)
        _payoff(seed_user, card)
        bought = _row_of(_bill(seed_user, card, _JAN_16), _JAN_16)
        ahead = _row_of(_bill(seed_user, card, _FEB_5), _FEB_5)
        _move(auth_client, ahead, seed_periods[1])
        assert _save(auth_client, ahead, due_date="2026-02-10").status_code == 200

        modal = auth_client.get(
            f"/pay-periods/{seed_periods[1].id}/carry-forward-preview",
        ).data.decode()
        _carry(auth_client, seed_periods[1])

        words = " ".join(modal.split())
        assert "It keeps its date, Jan 16, 2026" in words
        assert "It will be dated Feb 05, 2026" in words
        assert _dates(bought) == (_JAN_16, _JAN_16)
        assert _dates(ahead) == (_FEB_5, _FEB_5)
        assert bought.pay_period_id == seed_periods[2].id
        assert ahead.pay_period_id == seed_periods[2].id
        assert bought.is_override is True
        assert ahead.is_override is True


class TestAStandingTransfer:
    """A standing transfer into a card always follows, unless a date is typed."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_example_4_moved_it_follows(self, auth_client, seed_user, seed_periods):
        """$200 dated Jan 16, not sent, $800 trued up Jan 20, moved Jan 22, $800 Jan 25.

        Dated Jan 30, the Jan 25 true-up cannot hold it: Jan 30 pays 800 - 200 = $600.00
        and checking is 1,000 - 200 - 600 = $200.00.  Left on Jan 16 the true-up held it:
        Jan 30 $800.00, checking $0.00, $200.00 too low.
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        standing = _standing(seed_user, card, _JAN_16)
        _true_up(card, "800.00", _JAN_20)
        xfer = _row_of(standing, _JAN_16)

        _move(auth_client, xfer, seed_periods[2])

        assert _dates(xfer) == (_JAN_30, _JAN_16)
        _true_up(card, "800.00", _JAN_25)
        assert _price(payoff, _JAN_30, _JAN_25) == Decimal("600.00")
        assert _balances(seed_user, card, _JAN_25, _JAN_30) == (Decimal("200.00"), _ZERO)

    @pytest.mark.usefixtures("on_feb_1")
    def test_carried_forward_it_takes_today_and_the_modal_says_so(
        self, auth_client, seed_user, seed_periods,
    ):
        """Carry Forward on Feb 1 from Jan 16's paycheck: dated Feb 1, occurrence Jan 16."""
        card = _card(seed_user)
        _payoff(seed_user, card)
        standing = _standing(seed_user, card, _JAN_16)
        xfer = _row_of(standing, _JAN_16)
        modal = auth_client.get(
            f"/pay-periods/{seed_periods[1].id}/carry-forward-preview",
        )
        assert "It will be dated Feb 01, 2026" in " ".join(modal.data.decode().split())

        _carry(auth_client, seed_periods[1])

        assert _dates(xfer) == (_FEB_1, _JAN_16)
        assert xfer.pay_period_id == seed_periods[2].id

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_date_typed_in_the_same_save_stands(
        self, auth_client, seed_user, seed_periods,
    ):
        """Its box open from one move, a second move with Feb 3 typed: Feb 3 stands."""
        card = _card(seed_user)
        _payoff(seed_user, card)
        standing = _standing(seed_user, card, _JAN_16)
        xfer = _row_of(standing, _JAN_16)
        _move(auth_client, xfer, seed_periods[2])

        _move(auth_client, xfer, seed_periods[3], due_date=_FEB_3.isoformat())

        assert _dates(xfer) == (_FEB_3, _JAN_16)


class TestUnchanged:
    """Off a card, an envelope and a worked-out payment keep today's answer."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_off_a_card_the_date_stays(self, auth_client, seed_user, seed_periods):
        """A bill on checking moved to Feb 13's paycheck stays Jan 30, its box closed."""
        bill = _bill(seed_user, seed_user["account"], _JAN_30)
        row = _row_of(bill, _JAN_30)

        _move(auth_client, row, seed_periods[3])

        assert _dates(row) == (_JAN_30, _JAN_30)
        assert _date_box(auth_client, row) is False

    @pytest.mark.usefixtures("on_jan_22")
    def test_an_envelope_on_a_card_stays(self, auth_client, seed_user, seed_periods):
        """No card counts from an envelope's date: moved, it stays Jan 30."""
        card = _card(seed_user)
        bill = _bill(seed_user, card, _JAN_30)
        bill.is_envelope = True
        db.session.commit()
        row = _row_of(bill, _JAN_30)

        _move(auth_client, row, seed_periods[3])

        assert _dates(row) == (_JAN_30, _JAN_30)
        assert _date_box(auth_client, row) is False

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_worked_out_payment_stays(self, auth_client, seed_user, seed_periods):
        """Its date is what prices it (ruling plan#2671): moved, it stays Jan 30.

        $100 is owed so it reads $100.00: its popover cannot save a row reading $0.00
        (finding plan#3028).
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        _true_up(card, "100.00", _JAN_20)
        xfer = _row(payoff, _JAN_30)
        occurrence = xfer.occurs_on

        _move(auth_client, xfer, seed_periods[3])

        assert _dates(xfer) == (_JAN_30, occurrence)
        assert _date_box(auth_client, xfer) is False

    @pytest.mark.usefixtures("on_feb_1")
    def test_a_worked_out_payment_carried_forward_stays(
        self, auth_client, seed_user, seed_periods,
    ):
        """Carry Forward moves it and writes no date; the modal states none."""
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        xfer = _row(payoff, _JAN_16)

        modal = auth_client.get(
            f"/pay-periods/{seed_periods[1].id}/carry-forward-preview",
        ).data.decode()
        _carry(auth_client, seed_periods[1])

        db.session.refresh(xfer)
        assert "It will be dated" not in modal
        assert xfer.due_date == _JAN_16


class TestTheDateBox:
    """A moved row's box opens; a typed-over row's does not."""

    @pytest.mark.usefixtures("on_jan_22", "seed_periods")
    def test_a_row_never_moved_keeps_it_closed_and_refuses_a_date(
        self, auth_client, seed_user,
    ):
        """No box, and a crafted date is the designed 400."""
        card = _card(seed_user)
        bill = _bill(seed_user, card, _JAN_30)
        row = _row_of(bill, _JAN_30)

        assert _date_box(auth_client, row) is False
        response = auth_client.patch(
            f"/transactions/{row.id}", data={"due_date": _FEB_3.isoformat()},
        )

        assert response.status_code == 400
        assert _dates(row) == (_JAN_30, _JAN_30)

    @pytest.mark.usefixtures("on_jan_22", "seed_periods")
    def test_a_row_typed_over_keeps_it_closed(
        self, auth_client, seed_user,
    ):
        """The hold alone is not a move: a figure typed leaves the box closed."""
        card = _card(seed_user)
        bill = _bill(seed_user, card, _JAN_30)
        row = _row_of(bill, _JAN_30)
        assert _save(auth_client, row, estimated_amount="150.00").status_code == 200

        db.session.refresh(row)
        assert row.is_override is True
        assert _date_box(auth_client, row) is False

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_moved_row_opens_it_and_a_typed_date_stands(
        self, auth_client, seed_user, seed_periods,
    ):
        """Moved, the box is rendered, required; Feb 5 typed stands, priced on that day.

        Its occurrence stays Jan 30.  The price is $120 from Feb 1, so it reads $120.00.
        """
        card = _card(seed_user)
        bill = _bill(seed_user, card, _JAN_30)
        state_template_price(bill, Decimal("120.00"), effective_on=_FEB_1)
        db.session.commit()
        row = _row_of(bill, _JAN_30)
        _move(auth_client, row, seed_periods[1])
        assert _dates(row) == (_JAN_22, _JAN_30)
        assert _date_box(auth_client, row) is True

        response = _save(auth_client, row, due_date=_FEB_5.isoformat())

        assert response.status_code == 200, response.data[:400]
        assert _dates(row) == (_FEB_5, _JAN_30)
        assert _reads(row, _JAN_22) == Decimal("120.00")

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_moved_row_may_not_clear_it(self, auth_client, seed_user, seed_periods):
        """Its price is resolved on that day: empty, it is refused and stays."""
        card = _card(seed_user)
        bill = _bill(seed_user, card, _JAN_30)
        row = _row_of(bill, _JAN_30)
        _move(auth_client, row, seed_periods[1])

        response = _save(auth_client, row, due_date="")

        assert response.status_code == 400
        assert b"can be moved but not cleared" in response.data
        assert _dates(row) == (_JAN_22, _JAN_30)

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_moved_transfer_opens_it_too(self, auth_client, seed_user, seed_periods):
        """The transfer popover and its gate read the same answer."""
        card = _card(seed_user)
        standing = _standing(seed_user, card, _JAN_16)
        xfer = _row_of(standing, _JAN_16)
        assert _date_box(auth_client, xfer) is False
        refused = auth_client.patch(
            f"/transfers/instance/{xfer.id}", data={"due_date": _FEB_3.isoformat()},
        )
        assert refused.status_code == 400

        _move(auth_client, xfer, seed_periods[2])

        assert _date_box(auth_client, xfer) is True
        assert _save(auth_client, xfer, due_date="").status_code == 400
        assert _dates(xfer) == (_JAN_30, _JAN_16)
        assert _save(auth_client, xfer, due_date=_FEB_3.isoformat()).status_code == 200
        assert _dates(xfer) == (_FEB_3, _JAN_16)


class TestAMoveBackToItsOwnPaycheck:
    """Ruling plan#3038: a round trip restores the row, its usual date while ahead."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_there_and_back_it_reads_as_it_did(
        self, auth_client, seed_user, seed_periods,
    ):
        """Due on the 5th, $120 from Feb 1, in Jan 30's paycheck: back, Feb 5 and $120.00.

        Its box closes, because it is at home on its rule's date.  The paycheck's first
        day, the one-time row's default, would read Jan 30 and $100.00.
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        bill = _bill(seed_user, card, _FEB_5)
        state_template_price(bill, Decimal("120.00"), effective_on=_FEB_1)
        db.session.commit()
        row = _row_of(bill, _FEB_5)
        assert row.pay_period_id == seed_periods[2].id

        _move(auth_client, row, seed_periods[3])
        assert _dates(row) == (_FEB_13, _FEB_5)

        _move(auth_client, row, seed_periods[2])

        assert _dates(row) == (_FEB_5, _FEB_5)
        assert _reads(row, _JAN_22) == Decimal("120.00")
        assert _date_box(auth_client, row) is False
        # 1,000 - 120, as if it had never moved.
        assert _price(payoff, _FEB_13, _JAN_22) == Decimal("120.00")
        assert _balances(seed_user, card, _JAN_22, _FEB_13)[0] == Decimal("880.00")

    @pytest.mark.usefixtures("on_feb_1")
    def test_a_date_that_has_passed_stays_and_its_box_stays_open(
        self, auth_client, seed_user, seed_periods,
    ):
        """Dated Jan 25 away from home, moved home on Feb 1: Jan 25 stays, the box open.

        At home on another date than its rule's, so only the box can correct it.
        """
        card = _card(seed_user)
        bill = _bill(seed_user, card, _FEB_5)
        row = _row_of(bill, _FEB_5)
        _move(auth_client, row, seed_periods[1])
        response = _save(auth_client, row, due_date=_JAN_25.isoformat())
        assert response.status_code == 200, response.data[:400]

        _move(auth_client, row, seed_periods[2])

        assert _dates(row) == (_JAN_25, _FEB_5)
        assert _date_box(auth_client, row) is True


class TestTheRepeatLeavesAMovedDate:
    """What a regeneration and the chooser's "use" do to a date a move left."""

    @pytest.mark.usefixtures("on_jan_22")
    @pytest.mark.parametrize("kind", ["bill", "transfer"])
    def test_use_hands_back_the_figure_and_keeps_the_move(
        self, auth_client, seed_user, seed_periods, kind,
    ):
        """Moved to Feb 13's, "use", then a pass: still Feb 13, reading $120 from Feb 1.

        The page asks about an amount and says the definition prices a row "on its own
        due date".  Its hold stays, so the pass leaves its date.  Handed back whole, the
        next pass dated it Jan 30 again and it read $100.00: a rename moving $20.00 and a
        paycheck.
        """
        card = _card(seed_user)
        template = (
            _bill(seed_user, card, _JAN_30) if kind == "bill"
            else _standing(seed_user, card, _JAN_30, price="100.00")
        )
        state_template_price(template, Decimal("120.00"), effective_on=_FEB_1)
        db.session.commit()
        row = _row_of(template, _JAN_30)
        _move(auth_client, row, seed_periods[3])

        _use(template, row)

        db.session.refresh(row)
        assert row.is_override is True
        assert row.pay_period_id == seed_periods[3].id
        assert _dates(row) == (_FEB_13, _JAN_30)
        assert _reads(row, _JAN_22) == Decimal("120.00")
        assert _date_box(auth_client, row) is True

        conflict = _regenerate(template, seed_user["scenario"].id)

        assert conflict.overridden == [row.id]
        assert _dates(row) == (_FEB_13, _JAN_30)
        assert _reads(row, _JAN_22) == Decimal("120.00")

    @pytest.mark.usefixtures("on_jan_22")
    def test_use_hands_a_typed_over_figure_back_at_the_moved_date(
        self, auth_client, seed_user, seed_periods,
    ):
        """Typed to $150 and moved to Feb 13's: "use" reads the series on Feb 13, $120."""
        card = _card(seed_user)
        bill = _bill(seed_user, card, _JAN_30)
        state_template_price(bill, Decimal("120.00"), effective_on=_FEB_1)
        db.session.commit()
        row = _row_of(bill, _JAN_30)
        assert _save(auth_client, row, estimated_amount="150.00").status_code == 200
        _move(auth_client, row, seed_periods[3])
        assert _reads(row, _JAN_22) == Decimal("150.00")

        _use(bill, row)

        assert _reads(row, _JAN_22) == Decimal("120.00")
        assert _dates(row) == (_FEB_13, _JAN_30)

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_charge_date_typed_survives_use_at_home(
        self, auth_client, seed_user, seed_periods,
    ):
        """The real charge typed Jan 18, $100 trued up Jan 20, moved home, "use", a pass.

        Jan 18 stays, inside the true-up, so it counts once: Jan 30 pays $100.00, Feb 13
        pays $0.00, checking is $900.00 on Feb 13.  Re-dated Feb 5 by the pass it came on
        top of the true-up: checking $800.00, the bill counted twice.
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        bill = _bill(seed_user, card, _FEB_5)
        row = _row_of(bill, _FEB_5)
        _move(auth_client, row, seed_periods[1])
        assert _save(auth_client, row, due_date=_JAN_18.isoformat()).status_code == 200
        _true_up(card, "100.00", _JAN_20)
        _move(auth_client, row, seed_periods[2])
        assert _dates(row) == (_JAN_18, _FEB_5)

        _use(bill, row)
        _regenerate(bill, seed_user["scenario"].id)

        assert _dates(row) == (_JAN_18, _FEB_5)
        assert _date_box(auth_client, row) is True
        assert _price(payoff, _FEB_13, _JAN_22) == _ZERO
        assert _balances(seed_user, card, _JAN_22, _FEB_13)[0] == Decimal("900.00")

    @pytest.mark.usefixtures("on_jan_22")
    def test_use_at_home_on_its_rules_date_makes_it_the_rules_again(
        self, auth_client, seed_user, seed_periods,
    ):
        """There and back on its rule's date, "use" clears the hold and a pass takes it."""
        card = _card(seed_user)
        bill = _bill(seed_user, card, _FEB_5)
        row = _row_of(bill, _FEB_5)
        _move(auth_client, row, seed_periods[3])
        _move(auth_client, row, seed_periods[2])
        assert row.is_override is True

        _use(bill, row)

        db.session.refresh(row)
        assert row.is_override is False
        assert _regenerate(bill, seed_user["scenario"].id) is None
        assert _dates(row) == (_FEB_5, _FEB_5)

    @pytest.mark.usefixtures("on_feb_14")
    def test_a_pass_from_today_does_not_retire_it_once_its_paycheck_is_over(
        self, auth_client, seed_user, seed_periods,
    ):
        """Moved Feb 14 to Feb 13's paycheck, "use", a pass from today: the row stands.

        A pass from today names no occurrence of a paycheck that has ended.  With its hold
        cleared the row read as dropped by its rule and was deleted, and checking on Feb 27
        went from $900.00 to $1,000.00.  Its hold stays, so it is asked about instead.
        """
        card = _card(seed_user)
        _payoff(seed_user, card)
        bill = _bill(seed_user, card, _JAN_30)
        row = _row_of(bill, _JAN_30)
        _move(auth_client, row, seed_periods[3])
        assert _dates(row) == (_JAN_30, _JAN_30)

        _use(bill, row)
        conflict = _regenerate(bill, seed_user["scenario"].id)

        assert conflict.overridden == [row.id]
        assert db.session.get(Transaction, row.id) is not None
        assert _balances(seed_user, card, date(2026, 2, 14), _FEB_27)[0] == Decimal("900.00")


class TestAMovedRowIsKnownByItsHold:
    """How a moved row is told from what is stored, against every row it must not name."""

    @pytest.mark.usefixtures("on_jan_22")
    @pytest.mark.parametrize(
        "from_placement,period_index,before,after,feb_13,feb_27",
        [
            (
                PeriodPlacementEnum.CONTAINING_DATE, 2, _FEB_5, _FEB_13,
                "0.00", "100.00",
            ),
            (
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER, 3, _FEB_13, _FEB_5,
                "100.00", "0.00",
            ),
        ],
    )
    def test_a_row_never_moved_follows_an_edit_of_its_rules_funding(
        self, auth_client, seed_user, seed_periods,
        from_placement, period_index, before, after, feb_13, feb_27,
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Only the rule's funding is edited: the pass re-dates the row, its box closed.

        Asked by position alone, this row read as moved and its date froze, so Feb 13
        paid the $100.00 a month early, or $100.00 too high on checking the other way.

        Pylint: ``too-many-arguments``, ``too-many-positional-arguments`` -- pytest hands
        the fixtures and each row of the table to the test by name.
        """
        card = _card(seed_user)
        payoff = _payoff(seed_user, card)
        bill = _bill(seed_user, card, _FEB_5)
        rule = bill.recurrence_rule
        other = (
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
            if from_placement is PeriodPlacementEnum.CONTAINING_DATE
            else PeriodPlacementEnum.CONTAINING_DATE
        )
        rule.placement_id = ref_cache.period_placement_id(from_placement)
        db.session.flush()
        db.session.query(Transaction).filter_by(template_id=bill.id).delete()
        recurrence_engine.generate_for_template(
            bill, _schedule(bill.user_id), seed_user["scenario"].id,
        )
        db.session.commit()
        row = _row_of(bill, _FEB_5)
        assert row.pay_period_id == seed_periods[period_index].id
        assert row.due_date == before

        rule.placement_id = ref_cache.period_placement_id(other)
        db.session.commit()
        assert _regenerate(bill, seed_user["scenario"].id) is None

        assert _dates(row) == (after, _FEB_5)
        assert _date_box(auth_client, row) is False
        assert _price(payoff, _FEB_13, _JAN_22) == Decimal(feb_13)
        assert _price(payoff, _FEB_27, _JAN_22) == Decimal(feb_27)

    @pytest.mark.usefixtures("on_jan_22", "seed_periods")
    def test_a_row_answering_no_occurrence_reads_not_moved(self, seed_user):
        """Held, in any paycheck, with no occurrence: nothing to compare, so ``False``."""
        bill = _bill(seed_user, _card(seed_user), _FEB_5)
        row = _row_of(bill, _FEB_5)
        row.is_override = True
        row.occurs_on = None
        db.session.flush()

        assert date_left_by_a_move(row, calendar_for(row.user_id)) is False

    @pytest.mark.usefixtures("on_jan_22", "seed_periods")
    def test_an_occurrence_the_schedule_does_not_reach_reads_moved(
        self, seed_user,
    ):
        """Held, its occurrence past the schedule: it has no paycheck of its own.

        Under a rule dating a row from its paycheck, so nothing can read a date off none.
        """
        bill = _bill(seed_user, _card(seed_user), _FEB_5)
        bill.recurrence_rule.placement_id = ref_cache.period_placement_id(
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        )
        row = _row_of(bill, _FEB_5)
        row.is_override = True
        row.occurs_on = date(2030, 6, 5)
        db.session.flush()

        assert date_left_by_a_move(row, calendar_for(row.user_id)) is True

    @pytest.mark.usefixtures("on_jan_22")
    def test_the_transfer_door_asks_of_the_account_the_save_leaves(
        self, seed_user, seed_periods,
    ):
        """Moved and re-pointed off the card in one update, the date stays: off a card."""
        card = _card(seed_user)
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Rainy Day",
            anchor_balance=_ZERO, observed_on=date(2026, 1, 2),
        )
        standing = _standing(seed_user, card, _JAN_16)
        xfer = _row_of(standing, _JAN_16)

        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            pay_period_id=seed_periods[2].id, is_override=True,
            to_account_id=savings.id,
        )
        db.session.commit()

        assert _dates(xfer) == (_JAN_16, _JAN_16)
