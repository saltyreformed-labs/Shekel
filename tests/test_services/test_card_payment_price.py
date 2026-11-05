"""
Shekel Budget App -- a card works out its payment inside its own balance

Plan step **credit_card:CC-6**, its task plan#2677 (ruling **R-CC22**; design
3.5): ``price = max(0, A - X)``, one case per the ruling's sentences, each figure
computed by hand in the test that asserts it.  The ceiling above that figure is
:mod:`tests.test_services.test_card_payment_follows_what_is_owed`.

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16, Jan 30, Feb 13 and
Feb 27 2026 onward.  **The card closes on the 5th and falls due on the 27th**
unless a test says otherwise, so the statement closing Jan 5 is valued at the end
of Jan 4 and falls due Jan 27 (P1).  **Each pass is held at Jan 1**, ahead of
every row, unless a test holds it later to make a row overdue.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    CardPaymentModeEnum,
    SettledDayBasisEnum,
    StatusEnum,
)
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.card_payment_settings import CardPaymentSettings
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service, transfer_recurrence, transfer_service
from app.services.balance_at import BalanceContext, balance_at, cash_balance_at
from app.services.balance_at._cash_fold import assembled_fold
from app.services.balance_at._cash_periods import period_view_of
from app.services.card_terms import set_terms
from app.services.cash_ledger import (
    AmountRule,
    amounts_by_key,
    contributions_by_key,
    derived_amount_basis,
    resolve_transfer_amount,
    transfer_amount_rule,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.obligations_aggregator import todays_price
from app.services.pay_calendar import calendar_for
from app.services.recurrence import author_rule
from app.services.settle_day import SettleDay
from app.services.transfer_legs import side_legs
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
    make_every_period_rule,
    on_both_sides,
)
from tests.test_services.test_due_date_repeat_read_pass import (
    _TERMS_FROM,
    _due_date_spec,
    _terms,
)

_JAN_1 = date(2026, 1, 1)
_ZERO = Decimal("0.00")


def _dormant_card(seed_user):
    """A Credit Card owing nothing, its books open before every row here, flushed."""
    card = create_account_of_type(
        seed_user, db.session, "Credit Card", "Visa",
        anchor_balance=_ZERO, observed_on=date(2026, 1, 2),
    )
    db.session.flush()
    return card


def _card(seed_user, *, close_day=5, due_day=27):
    """:func:`_dormant_card`, closing and falling due as stated, flushed."""
    card = _dormant_card(seed_user)
    set_terms(card.id, card.user_id, _TERMS_FROM, _terms(close_day, due_day))
    db.session.flush()
    return card


def _works_out(seed_user, card, mode, *, on_due_dates=False):
    """A worked-out payment from checking into *card*, its rows generated.

    Built as the doors build a definition: the template and its settings, no
    price stated (ruling plan#2667), then its rule.

    Returns:
        The committed template.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=card.id,
        name="Pay the Visa",
    )
    db.session.add(template)
    db.session.flush()
    db.session.add(CardPaymentSettings(
        template=template, user_id=template.user_id,
        mode_id=ref_cache.card_payment_mode_id(mode),
    ))
    db.session.flush()
    if on_due_dates:
        author_rule(
            _due_date_spec(seed_user),
            calendar_for(template.user_id),
            template,
        )
    else:
        make_every_period_rule(db.session, template)
    db.session.flush()
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_pass(BalanceContext.build(template.user_id)),
        seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _row(template, due):
    """The payment row of *template* dated *due*."""
    return db.session.query(Transfer).filter_by(
        transfer_template_id=template.id, due_date=due,
    ).one()


def _price(template, due, as_of=_JAN_1):
    """What a fresh pass held at *as_of* prices the row dated *due* at."""
    ctx = BalanceContext.build(template.user_id, as_of=as_of)
    return resolve_transfer_amount(_row(template, due), ctx.amounts())


def _spend(seed_user, card, period, amount, on):
    """A projected purchase of *amount* on *card*, dated *on*, committed."""
    add_txn(
        db.session, seed_user, period, "Groceries", Decimal(amount),
        due_date=on, account=card,
    )
    db.session.commit()


def _spent(seed_user, card, period, amount, on):
    """A purchase of *amount* on *card*, recorded as moved on *on*, committed.

    What a pass held after *on* reads as money gone, the bank having been seen to
    take it, where :func:`_spend` is the row counted from its date (ruling plan#2926).
    """
    add_txn(
        db.session, seed_user, period, "Groceries", Decimal(amount),
        status_enum=StatusEnum.DONE, settled_amount=Decimal(amount),
        due_date=on, account=card, settled_on=on,
    )
    db.session.commit()


def _one_off(seed_user, card, period, amount, on):
    """A projected one-off of the owner's own figure into *card*, committed."""
    transfer = transfer_service.create_transfer(transfer_service.TransferSpec(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=card.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount_ownership=AmountOwnership.own(Decimal(amount)),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        category_id=seed_user["categories"]["Rent"].id,
        name="Extra to the Visa",
        due_date=on,
    ))
    db.session.commit()
    return transfer


def _type_over(row, amount):
    """Type the owner's *amount* over *row*, through the update door."""
    transfer_service.update_transfer(
        row.id, row.user_id,
        amount_ownership=AmountOwnership.own(Decimal(amount)),
        is_override=True,
    )
    db.session.commit()


def _settle(row, on):
    """Settle *row* as paid on *on*, through the settle door."""
    transfer_service.settle_transfer(
        row.id, row.user_id,
        side_days=on_both_sides(
            row.from_account_id, row.to_account_id,
            SettleDay(day=on, basis=SettledDayBasisEnum.ENTERED),
        ),
    )
    db.session.commit()


class TestEverythingOnTheCard:
    """The balance at the end of the day before the row, less what is not inside."""

    def test_five_hundred_owed_pays_five_hundred(self, seed_user, seed_periods):
        """$500 charged Jan 5: Jan 2 owes $0.00, Jan 16 $500, and Jan 30 is clear."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

        assert _price(template, date(2026, 1, 2)) == _ZERO
        assert _price(template, date(2026, 1, 16)) == Decimal("500.00")
        assert _price(template, date(2026, 1, 30)) == _ZERO

    def test_the_prices_are_inside_the_cards_balance(self, seed_user, seed_periods):
        """The card owes $500 on Jan 15, and nothing once the payment lands Jan 16."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        ctx = BalanceContext.build(seed_user["user"].id, as_of=_JAN_1)

        assert cash_balance_at(card, ctx, date(2026, 1, 15)) == Decimal("-500.00")
        assert cash_balance_at(card, ctx, date(2026, 1, 16)) == _ZERO
        assert cash_balance_at(card, ctx, date(2026, 3, 1)) == _ZERO

    def test_three_hundred_typed_over_leaves_two_hundred(
        self, seed_user, seed_periods,
    ):
        """$300 typed over $500: that row is $300, and Jan 30 picks up the $200."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        row = _row(template, date(2026, 1, 16))

        _type_over(row, "300.00")

        assert transfer_amount_rule(row) is AmountRule.OWN
        assert _price(template, date(2026, 1, 16)) == Decimal("300.00")
        assert _price(template, date(2026, 1, 30)) == Decimal("200.00")
        assert _price(template, date(2026, 2, 13)) == _ZERO

    def test_a_one_off_in_the_same_paycheck_shrinks_the_row(
        self, seed_user, seed_periods,
    ):
        """$100 placed Jan 16 beside $500 owed: the row is $400, and P1 pays $500."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        _one_off(seed_user, card, seed_periods[1], "100.00", date(2026, 1, 16))

        assert _price(template, date(2026, 1, 16)) == Decimal("400.00")
        assert _price(template, date(2026, 1, 30)) == _ZERO

        ctx = BalanceContext.build(seed_user["user"].id, as_of=_JAN_1)
        view = period_view_of(
            assembled_fold(seed_user["account"], ctx), ctx.reported_periods(),
        )
        assert view.columns[seed_periods[1].id].expense == Decimal("500.00")

    def test_an_overdue_row_is_not_paid_twice(self, seed_user, seed_periods):
        """Read Feb 5 with Jan 16's $500 unpaid: Jan 30 owes $500 less that $500."""
        card = _card(seed_user)
        _spent(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

        assert _price(template, date(2026, 1, 16), date(2026, 2, 5)) == (
            Decimal("500.00")
        )
        assert _price(template, date(2026, 1, 30), date(2026, 2, 5)) == _ZERO

    def test_a_row_paid_late_is_not_paid_twice(self, seed_user, seed_periods):
        """Jan 16's $500 paid Feb 3: not inside Jan 29's $500, and still subtracted."""
        card = _card(seed_user)
        _spent(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        row = _row(template, date(2026, 1, 16))
        _type_over(row, "500.00")
        _settle(row, date(2026, 2, 3))

        assert _price(template, date(2026, 1, 30), date(2026, 2, 5)) == _ZERO
        assert _price(template, date(2026, 2, 13), date(2026, 2, 5)) == _ZERO

    def test_a_purchase_on_payday_rolls_to_the_next_row(
        self, seed_user, seed_periods,
    ):
        """$50 charged on Jan 16 itself is not in that row, and is in Jan 30's."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        _spend(seed_user, card, seed_periods[1], "50.00", date(2026, 1, 16))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

        assert _price(template, date(2026, 1, 16)) == Decimal("500.00")
        assert _price(template, date(2026, 1, 30)) == Decimal("50.00")

    def test_a_card_owing_nothing_prices_zero(self, seed_user, seed_periods):
        """No charge at all: every row is $0.00."""
        template = _works_out(
            seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
        )

        assert _price(template, date(2026, 1, 16)) == _ZERO
        assert str(_price(template, date(2026, 1, 16))) == "0.00"

    def test_a_card_in_credit_floors_at_zero(self, seed_user, seed_periods):
        """$25 held in credit on Jan 15 reads -$25.00 owed, and the floor holds."""
        card = _card(seed_user)
        add_txn(
            db.session, seed_user, seed_periods[0], "Reward", Decimal("25.00"),
            is_income=True, due_date=date(2026, 1, 5), account=card,
        )
        db.session.commit()
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

        assert _price(template, date(2026, 1, 16)) == _ZERO

    def test_two_rows_on_one_day_pay_once(self, seed_user, seed_periods):
        """A row moved onto Jan 16 sees the earlier id as a credit: $500, then $0."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        first = _row(template, date(2026, 1, 16))
        second = _row(template, date(2026, 1, 30))
        second.pay_period_id = seed_periods[1].id
        second.due_date = date(2026, 1, 16)
        db.session.commit()
        ctx = BalanceContext.build(template.user_id, as_of=_JAN_1)

        assert first.id < second.id
        assert resolve_transfer_amount(first, ctx.amounts()) == Decimal("500.00")
        assert resolve_transfer_amount(second, ctx.amounts()) == _ZERO


class TestTheStatementBalance:
    """The statement closing before the row, less the credits after that close."""

    def test_eight_hundred_less_the_hundred_before_the_due_date(
        self, seed_user, seed_periods,
    ):
        """$800 at the Jan 5 close, $100 Jan 16, $100 Jan 30: Jan 27 pays $700.

        The Jan 30 payment falls after the due date, so it is not subtracted.
        The next row reads the Feb 5 statement: -800 + 100 + 700 + 100 = +100
        held, so nothing is owed.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        _one_off(seed_user, card, seed_periods[1], "100.00", date(2026, 1, 16))
        _one_off(seed_user, card, seed_periods[2], "100.00", date(2026, 1, 30))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE,
            on_due_dates=True,
        )

        assert _price(template, date(2026, 1, 27)) == Decimal("700.00")
        assert _price(template, date(2026, 2, 27)) == _ZERO

    def test_a_payment_on_the_close_day_counts(self, seed_user, seed_periods):
        """$100 dated Jan 5 is outside the Jan 4 figure, so it is subtracted."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        _one_off(seed_user, card, seed_periods[0], "100.00", date(2026, 1, 5))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE,
            on_due_dates=True,
        )

        assert _price(template, date(2026, 1, 27)) == Decimal("700.00")

    def test_a_payment_made_early_counts_from_the_day_it_moved(
        self, seed_user, seed_periods,
    ):
        """$100 planned for Jan 30 and paid Jan 20: Jan 27 pays $700, not $800.

        Dated by its row alone it would fall after the due date and the card
        would be paid that $100 twice.
        """
        card = _card(seed_user)
        _spent(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        early = _one_off(
            seed_user, card, seed_periods[2], "100.00", date(2026, 1, 30),
        )
        _settle(early, date(2026, 1, 20))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE,
            on_due_dates=True,
        )

        assert _price(template, date(2026, 1, 27), date(2026, 1, 21)) == (
            Decimal("700.00")
        )

    def test_a_due_day_equal_to_the_close_day_pays_the_statement_before_it(
        self, seed_user, seed_periods,
    ):
        """Closes and falls due on the 5th: Feb 5 pays Jan 5's $800, not Feb 5's.

        Mar 5 pays Feb 5's: $800 + $200 charged Jan 20 = $1,000 owed on Feb 4,
        less the Feb 5 row's $800, which lands after that day: $200.
        """
        card = _card(seed_user, close_day=5, due_day=5)
        _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        _spend(seed_user, card, seed_periods[1], "200.00", date(2026, 1, 20))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE,
            on_due_dates=True,
        )

        assert _price(template, date(2026, 2, 5)) == Decimal("800.00")
        assert _price(template, date(2026, 3, 5)) == Decimal("200.00")

    def test_a_dormant_card_refuses(self, seed_user, seed_periods):
        """A statement's rule on a card with no terms refuses rather than guess."""
        card = _dormant_card(seed_user)
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=card.id,
            name="Pay the Visa",
        )
        db.session.add(template)
        db.session.flush()
        db.session.add(CardPaymentSettings(
            template=template, user_id=template.user_id,
            mode_id=ref_cache.card_payment_mode_id(
                CardPaymentModeEnum.STATEMENT_BALANCE,
            ),
        ))
        make_every_period_rule(db.session, template)
        transfer_recurrence.generate_for_template(
            template,
            GenerationSchedule.for_pass(BalanceContext.build(template.user_id)),
            seed_user["scenario"].id,
        )
        db.session.commit()

        with pytest.raises(AmountUnresolvable, match="no terms"):
            _price(template, date(2026, 1, 16))


class TestTheMinimum:
    """That statement's minimum (2.00%, floor $25.00), less the same credits."""

    def test_the_floor_binds_on_eight_hundred(self, seed_user, seed_periods):
        """2% of $800 is $16.00, so the floor's $25.00 is due."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT,
            on_due_dates=True,
        )

        assert _price(template, date(2026, 1, 27)) == Decimal("25.00")

    def test_the_percent_binds_on_two_thousand(self, seed_user, seed_periods):
        """2% of $2,000 is $40.00, above the floor."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "2000.00", date(2026, 1, 3))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT,
            on_due_dates=True,
        )

        assert _price(template, date(2026, 1, 27)) == Decimal("40.00")

    def test_a_credit_shrinks_it_and_the_floor_is_zero(
        self, seed_user, seed_periods,
    ):
        """$25.00 less $10 sent Jan 16 is $15.00; less a further $50 is $0.00."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        _one_off(seed_user, card, seed_periods[1], "10.00", date(2026, 1, 16))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT,
            on_due_dates=True,
        )
        assert _price(template, date(2026, 1, 27)) == Decimal("15.00")

        _one_off(seed_user, card, seed_periods[1], "50.00", date(2026, 1, 20))
        assert _price(template, date(2026, 1, 27)) == _ZERO


class TestEveryReaderReadsOneAnswer:
    """Checking's leg, the card's leg, and the paycheck the payment falls in."""

    def test_both_legs_read_one_figure(self, seed_user, seed_periods):
        """Each leg's amount and contribution is the row's $700.00, to the cent."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "700.00", date(2026, 1, 3))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE,
            on_due_dates=True,
        )
        ctx = BalanceContext.build(template.user_id, as_of=_JAN_1)
        legs = side_legs(_row(template, date(2026, 1, 27)))

        amounts = amounts_by_key(legs, ctx.amounts())
        contributions = contributions_by_key(legs, ctx.amounts())

        assert list(amounts.values()) == [Decimal("700.00"), Decimal("700.00")]
        assert list(contributions.values()) == [
            Decimal("700.00"), Decimal("700.00"),
        ]

    def test_the_payable_lands_in_the_due_dates_paycheck_on_checking(
        self, seed_user, seed_periods,
    ):
        """Due Jan 27 is P1's: checking pays $700 there and nothing in P0 or P2."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "700.00", date(2026, 1, 3))
        _works_out(
            seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE,
            on_due_dates=True,
        )
        ctx = BalanceContext.build(seed_user["user"].id, as_of=_JAN_1)
        checking = seed_user["account"]
        view = period_view_of(
            assembled_fold(checking, ctx), ctx.reported_periods(),
        )

        assert view.columns[seed_periods[0].id].expense == _ZERO
        assert view.columns[seed_periods[1].id].expense == Decimal("700.00")
        assert view.columns[seed_periods[2].id].expense == _ZERO
        before = balance_at(checking, ctx, date(2026, 1, 26))
        after = balance_at(checking, ctx, date(2026, 1, 27))
        assert before - after == Decimal("700.00")


class TestARowOutsideThePlanStillAnswers:
    """A budget figure is asked of every status, so the rule answers for all."""

    def test_a_cancelled_row_is_priced_and_counts_as_no_credit(
        self, seed_user, seed_periods,
    ):
        """Cancelled Jan 16 answers $500; Jan 30 still owes $500, nothing paid it."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        row = _row(template, date(2026, 1, 16))
        row.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
        db.session.commit()

        assert _price(template, date(2026, 1, 16)) == Decimal("500.00")
        assert _price(template, date(2026, 1, 30)) == Decimal("500.00")

    def test_a_settled_rows_own_money_comes_out_of_its_price(
        self, seed_user, seed_periods,
    ):
        """Paid Jan 10 for $500: inside the Jan 15 balance, so it comes back out.

        Read without that, the row that cleared the card would price itself at
        $0.00.  Settled through the door while still worked out (plan#2859).
        """
        card = _card(seed_user)
        _spent(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        row = _row(template, date(2026, 1, 16))
        _settle(row, date(2026, 1, 10))
        assert transfer_amount_rule(row) is AmountRule.CARD_PAYMENT
        assert row.settled_figure == Decimal("500.00")

        assert _price(template, date(2026, 1, 16), date(2026, 1, 12)) == (
            Decimal("500.00")
        )


class TestTheRefusals:
    """A basis that cannot answer says so; no figure stands in."""

    def test_a_basis_with_no_read_pass_refuses(self, seed_user, seed_periods):
        """Nothing can work the price out without the card's fold."""
        template = _works_out(
            seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
        )
        row = _row(template, date(2026, 1, 16))

        with pytest.raises(AmountUnresolvable, match="no read pass"):
            resolve_transfer_amount(
                row, derived_amount_basis(row.user_id, row.scenario_id),
            )

    def test_a_basis_of_another_scenario_refuses(self, seed_user, seed_periods):
        """The fold is one scenario's, so another's basis cannot price the row."""
        template = _works_out(
            seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
        )
        row = _row(template, date(2026, 1, 16))
        ctx = BalanceContext.build(row.user_id, as_of=_JAN_1)
        foreign = replace(ctx.amounts(), scenario_id=row.scenario_id + 1)

        with pytest.raises(AmountUnresolvable, match="another"):
            resolve_transfer_amount(row, foreign)


class TestTheDefinitionStatesNoPrice:
    """A worked-out payment has no series, and its pages read its next row."""

    def test_it_does_not_own_its_amount(self, seed_user, seed_periods):
        """The one predicate says no, and the write door refuses a series."""
        template = _works_out(
            seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
        )

        assert template_amount_service.owns_its_amount(template) is False
        with pytest.raises(ValueError):
            template_amount_service.set_amount(
                template, Decimal("100.00"), effective_on=_JAN_1,
            )

    def test_a_plain_transfer_into_a_card_still_does(self, seed_user, seed_periods):
        """The control: with no settings, the same definition keeps rule 3."""
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=_card(seed_user).id,
            name="Fixed to the Visa",
        )

        assert template_amount_service.owns_its_amount(template) is True

    def test_todays_price_is_the_next_rows(self, seed_user, seed_periods):
        """Read Jan 20 in statement mode: Jan 27's $700, not $0.00 for a row today."""
        card = _card(seed_user)
        _spent(seed_user, card, seed_periods[0], "700.00", date(2026, 1, 3))
        template = _works_out(
            seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE,
            on_due_dates=True,
        )
        ctx = BalanceContext.build(template.user_id, as_of=date(2026, 1, 20))

        assert todays_price(template, ctx).amount == Decimal("700.00")

    def test_todays_price_is_zero_with_no_row_left(self, seed_user, seed_periods):
        """Every row gone: $0.00."""
        template = _works_out(
            seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
        )
        for row in template.transfers:
            row.is_deleted = True
        db.session.commit()
        ctx = BalanceContext.build(template.user_id, as_of=_JAN_1)

        assert todays_price(template, ctx).amount == _ZERO
