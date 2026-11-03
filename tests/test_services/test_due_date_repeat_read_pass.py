"""
Shekel Budget App -- the read pass hands a due-date repeat its card's due dates

Plan step **credit_card:CC-6**, its task plan#2787 (ruling plan#2671: a card
payment's dates are its repeat, and a new repeat is each statement's due
date, worked out from the card's dated terms).  plan#2673 taught the walk to
yield a card's due dates and to refuse a value without them; this is where
they come from: :meth:`~app.services.balance_at.BalanceContext.resolved_for`
reads the destination card's terms and attaches its
:class:`~app.services.card_statement.DueDateSchedule`
(:func:`~app.services.balance_at.definition_due_dates`).  Pinned here:

* the pass attaches the schedule to a due-date repeat and to nothing else,
  reads each card's terms once, and keys its memo on the dates, so two cards
  with one spec never share a walk;
* a due-date repeat paying no configured card is refused, naming the
  account, and the refusal is not memoised;
* a terms change moves the dates on the next pass;
* generation writes the rows on the card's due dates, each in the paycheck
  covering it, and the balance seam's planned tier moves both accounts on
  each due date.

**The calendar** (``seed_periods``): ten biweekly paychecks from Fri
2026-01-02, so P0 covers Jan 2-15, P1 Jan 16-29, P2 Jan 30-Feb 12, P3 Feb
13-26, P4 Feb 27-Mar 12, P5 Mar 13-26, P6 Mar 27-Apr 9, P7 Apr 10-23, P8 Apr
24-May 7 and P9 May 8-21, the saved horizon's last day.

**The card**: statements close on the 5th and fall due on the 27th, the
first 27th strictly after the close (ruling R-CC26), so the dates on or after
Jan 2 are Jan 27, Feb 27, Mar 27 and Apr 27 inside the horizon.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence
from app.services.balance_at import BalanceContext, balance_at
from app.services.card_statement import DueDateSchedule
from app.services.card_terms import load_card_term_versions, set_terms
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    RecurrenceResolutionError,
    RecurrenceSpec,
    author_rule,
)
from app.services.recurring_definition import UnsavedDefinition, read_definition
from tests._test_helpers import create_account_of_type, state_template_price

#: The first payday, and the day each repeat below starts covering.
_JAN_2 = date(2026, 1, 2)

#: The terms a version states beside its two days, which no test here reads.
_OTHER_TERMS = {
    "min_payment_percent": Decimal("0.0200"),
    "min_payment_floor": Decimal("25.00"),
    "cashback_rate": Decimal("0.0150"),
    "auto_redeem_threshold": None,
    "credit_limit": None,
}

#: The day the first version governs from, before every close the tests read.
_TERMS_FROM = date(2025, 12, 1)

#: The card's due dates on or after Jan 2 inside the saved horizon.
_DUE_ON_27 = [
    date(2026, 1, 27), date(2026, 2, 27), date(2026, 3, 27), date(2026, 4, 27),
]

#: What a transfer into the card is priced at each due date.
_PAYMENT = Decimal("200.00")


def _terms(close_day, due_day):
    """Every term a version states, closing on *close_day*, due on *due_day*."""
    return {
        "statement_close_day": close_day, "payment_due_day": due_day,
        **_OTHER_TERMS,
    }


def _dormant_card(seed_user, name="Visa"):
    """A Credit Card of *seed_user*'s with no terms version, flushed."""
    card = create_account_of_type(seed_user, db.session, "Credit Card", name)
    db.session.flush()
    return card


def _card(seed_user, name="Visa", *, close_day=5, due_day=27):
    """A Credit Card of *seed_user*'s, its terms in force from Dec 1 2025, flushed."""
    card = _dormant_card(seed_user, name)
    set_terms(card.id, card.user_id, _TERMS_FROM, _terms(close_day, due_day))
    db.session.flush()
    return card


def _due_date_spec(seed_user, starts_on=_JAN_2):
    """A repeat on each statement's due date covering from *starts_on*."""
    return RecurrenceSpec(
        user_id=seed_user["user"].id,
        unit=RecurrenceUnitEnum.DUE_DATE,
        starts_on=starts_on,
    )


def _payment(seed_user, to_account, *, name="Pay the Visa", starts_on=_JAN_2):
    """A $200.00 transfer from checking into *to_account* on each due date.

    Built as the doors build a definition: the template, its price stated,
    then its rule authored through the write door (which resolves the spec
    and never walks it, so no dates are needed to store it).
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, _PAYMENT)
    author_rule(
        _due_date_spec(seed_user, starts_on),
        calendar_for(template.user_id),
        template,
    )
    db.session.flush()
    return template


def _occurrences(template, ctx):
    """Return the occurrence dates the composed door walks for *template* on *ctx*."""
    return [
        placement.occurrence
        for placement in read_definition(template, ctx).placements
    ]


def _terms_reads(monkeypatch):
    """Count the card-terms reads the pass makes; return the running list of ids."""
    reads = []

    def counting(account_id):
        reads.append(account_id)
        return load_card_term_versions(account_id)

    monkeypatch.setattr(
        "app.services.balance_at._definition_due_dates.load_card_term_versions",
        counting,
    )
    return reads


@pytest.mark.usefixtures("seed_periods")
class TestThePassAttachesTheCardsDates:
    """``resolved_for`` lays the destination card's schedule on a due-date repeat."""

    def test_a_due_date_repeat_walks_its_cards_due_dates(self, app, seed_user):
        """Jan 27, Feb 27, Mar 27, Apr 27: the card's dates, not a stored day.

        The schedule attached is the one the card's saved versions decide,
        compared by value.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _payment(seed_user, card)
            ctx = BalanceContext.build(seed_user["user"].id)

            resolved = ctx.resolved_recurrence_of(template.recurrence_rule)

            assert resolved.due_dates == DueDateSchedule.of(
                load_card_term_versions(card.id),
            )
            assert _occurrences(template, ctx) == _DUE_ON_27

    def test_every_other_repeat_carries_no_dates_and_reads_no_terms(
        self, app, seed_user, monkeypatch,
    ):
        """A monthly transfer into the same card: no schedule, no terms read."""
        with app.app_context():
            card = _card(seed_user)
            reads = _terms_reads(monkeypatch)
            ctx = BalanceContext.build(seed_user["user"].id)
            monthly = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 1, 27),
            )

            resolved = ctx.resolved_for(monthly, UnsavedDefinition(
                to_account_id=card.id, from_account_id=seed_user["account"].id,
            ))

            assert resolved.due_dates is None
            assert not reads

    def test_one_cards_terms_are_read_once_per_pass(
        self, app, seed_user, monkeypatch,
    ):
        """Two repeats into one card, starting apart: one terms read, one schedule.

        The two specs differ (a start of Jan 2 and of Feb 1), so the memo
        holds two resolutions; the card's terms are read for the first and
        served from the pass for the second.  The second walks from its own
        start: Feb 27 on.
        """
        with app.app_context():
            card = _card(seed_user)
            first = _payment(seed_user, card)
            second = _payment(
                seed_user, card, name="Second", starts_on=date(2026, 2, 1),
            )
            reads = _terms_reads(monkeypatch)
            ctx = BalanceContext.build(seed_user["user"].id)

            one = ctx.resolved_recurrence_of(first.recurrence_rule)
            two = ctx.resolved_recurrence_of(second.recurrence_rule)

            assert reads == [card.id]
            assert one.due_dates is two.due_dates
            assert _occurrences(second, ctx) == _DUE_ON_27[1:]

    def test_two_cards_with_one_spec_walk_apart(self, app, seed_user):
        """The memo is keyed on the dates: one spec, two cards, two walks.

        Both definitions state the same spec from the same checking account
        into two cards whose books open on the same day, so they agree on
        everything but the dates.  Keyed on the spec and the books alone, the
        second would be served the first card's dates.
        """
        with app.app_context():
            visa = _card(seed_user)
            amex = _card(seed_user, name="Amex", due_day=20)
            to_visa = _payment(seed_user, visa)
            to_amex = _payment(seed_user, amex, name="Pay the Amex")
            ctx = BalanceContext.build(seed_user["user"].id)
            # The plant: the two agree on the books, so only the dates part them.
            assert ctx.resolved_recurrence_of(to_visa.recurrence_rule).books_opened_on == (
                ctx.resolved_recurrence_of(to_amex.recurrence_rule).books_opened_on
            )

            assert _occurrences(to_visa, ctx) == _DUE_ON_27
            assert _occurrences(to_amex, ctx) == [
                date(2026, 1, 20), date(2026, 2, 20), date(2026, 3, 20),
                date(2026, 4, 20), date(2026, 5, 20),
            ]


@pytest.mark.usefixtures("seed_periods")
class TestARepeatPayingNoCardIsRefused:
    """A due-date repeat whose destination is not a configured card is never walked."""

    def test_an_account_that_is_not_a_card_is_refused(self, app, seed_user):
        """Into a savings account: refused, naming the account."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy day",
            )
            db.session.flush()
            ctx = BalanceContext.build(seed_user["user"].id)

            with pytest.raises(
                RecurrenceResolutionError,
                match=f"pays account {savings.id}, which is not a credit card",
            ):
                ctx.resolved_for(_due_date_spec(seed_user), UnsavedDefinition(
                    to_account_id=savings.id,
                    from_account_id=seed_user["account"].id,
                ))

    def test_a_card_with_no_terms_is_refused(self, app, seed_user):
        """A dormant card has no statements, so no due dates to fall on."""
        with app.app_context():
            card = _dormant_card(seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)

            with pytest.raises(
                RecurrenceResolutionError,
                match=f"pays card {card.id}, which has no terms yet",
            ):
                ctx.resolved_for(_due_date_spec(seed_user), UnsavedDefinition(
                    to_account_id=card.id,
                    from_account_id=seed_user["account"].id,
                ))

    @pytest.mark.parametrize("states_an_account", [False, True], ids=[
        "a payroll line's rule", "a transaction template",
    ])
    def test_a_repeat_paying_no_account_is_refused(
        self, app, seed_user, states_an_account,
    ):
        """No destination at all: there is no card whose dates it could take.

        A payroll line's rule has no definition; a transaction template
        moves money in one account and pays into none.
        """
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            definition = (
                UnsavedDefinition(
                    to_account_id=None, account_id=seed_user["account"].id,
                )
                if states_an_account else None
            )

            with pytest.raises(RecurrenceResolutionError, match="pays no account"):
                ctx.resolved_for(_due_date_spec(seed_user), definition)

    def test_the_refusal_is_not_memoised(self, app, seed_user):
        """Terms stated after a refusal on the same pass are read on the next ask."""
        with app.app_context():
            card = _dormant_card(seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)
            definition = UnsavedDefinition(
                to_account_id=card.id, from_account_id=seed_user["account"].id,
            )
            with pytest.raises(RecurrenceResolutionError):
                ctx.resolved_for(_due_date_spec(seed_user), definition)

            set_terms(card.id, card.user_id, _TERMS_FROM, _terms(5, 27))
            db.session.flush()

            resolved = ctx.resolved_for(_due_date_spec(seed_user), definition)
            assert resolved.due_dates == DueDateSchedule.of(
                load_card_term_versions(card.id),
            )


@pytest.mark.usefixtures("seed_periods")
class TestATermsChangeMovesThemOnTheNextPass:
    """The dates are read every pass, so a change of terms moves the walk."""

    def test_a_new_version_moves_the_statements_closing_from_its_day(
        self, app, seed_user,
    ):
        """Due on the 20th from Mar 1: Jan 27, Feb 27, then Mar 20, Apr 20, May 20.

        The statements closing Jan 5 and Feb 5 stand under the old terms
        (due Jan 27, Feb 27); the one closing Mar 5 is the new version's,
        due the first 20th after it, Mar 20, and so on.  The pass built
        before the change still walks the old dates: its memo is that pass's.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _payment(seed_user, card)
            before = BalanceContext.build(seed_user["user"].id)
            assert _occurrences(template, before) == _DUE_ON_27

            set_terms(card.id, card.user_id, date(2026, 3, 1), _terms(5, 20))
            db.session.flush()
            after = BalanceContext.build(seed_user["user"].id)

            assert _occurrences(template, after) == [
                date(2026, 1, 27), date(2026, 2, 27), date(2026, 3, 20),
                date(2026, 4, 20), date(2026, 5, 20),
            ]
            assert _occurrences(template, before) == _DUE_ON_27


def _generate(seed_user, template):
    """Run the transfer generate pass for *template* over every saved paycheck."""
    ctx = BalanceContext.build(template.user_id)
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            ctx, {period.period_id for period in ctx.calendar().periods},
        ),
        seed_user["scenario"].id,
    )
    db.session.flush()


class TestGenerationAndThePlannedTier:
    """The rows land on the due dates, and the balance seam moves on them."""

    def test_generation_writes_a_row_on_each_due_date(
        self, app, seed_user, seed_periods,
    ):
        """Four rows: each dated on its due date, in the paycheck covering it.

        Jan 27 in P1 (Jan 16-29), Feb 27 in P4 (Feb 27-Mar 12), Mar 27 in P6
        (Mar 27-Apr 9), Apr 27 in P8 (Apr 24-May 7); none in the six
        paychecks no due date falls in.
        """
        with app.app_context():
            template = _payment(seed_user, _card(seed_user))

            _generate(seed_user, template)

            rows = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id, is_deleted=False)
                .order_by(Transfer.due_date)
                .all()
            )
            payday = {period.id: period.start_date for period in seed_periods}
            assert [
                (row.occurs_on, row.due_date, payday[row.pay_period_id])
                for row in rows
            ] == [
                (_DUE_ON_27[0], _DUE_ON_27[0], seed_periods[1].start_date),
                (_DUE_ON_27[1], _DUE_ON_27[1], seed_periods[4].start_date),
                (_DUE_ON_27[2], _DUE_ON_27[2], seed_periods[6].start_date),
                (_DUE_ON_27[3], _DUE_ON_27[3], seed_periods[8].start_date),
            ]

    @pytest.mark.usefixtures("seed_periods")
    def test_the_planned_tier_moves_both_accounts_on_each_due_date(
        self, app, seed_user,
    ):
        """Checking falls $200.00 and the card rises $200.00 on Jan 27, not before.

        Read with the resolver's now pinned to Jan 2, before every row, so no
        still-projected row is carried up to a later now.  The day before
        each account holds what it held on Jan 2; on the due date each has
        moved by the one payment: checking 200.00 lower, the card (negative
        while owed) 200.00 higher.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _payment(seed_user, card)
            _generate(seed_user, template)
            checking = seed_user["account"]
            ctx = BalanceContext.build(seed_user["user"].id, as_of=_JAN_2)

            def moved(account, day):
                """The account's balance on *day* less on the day before."""
                return (
                    balance_at(account, ctx, day)
                    - balance_at(account, ctx, day - timedelta(days=1))
                )

            assert moved(checking, _DUE_ON_27[0]) == Decimal("-200.00")
            assert moved(card, _DUE_ON_27[0]) == Decimal("200.00")
            assert balance_at(checking, ctx, date(2026, 1, 26)) == (
                balance_at(checking, ctx, _JAN_2)
            )
            assert moved(checking, _DUE_ON_27[1]) == Decimal("-200.00")
            assert moved(card, _DUE_ON_27[1]) == Decimal("200.00")
