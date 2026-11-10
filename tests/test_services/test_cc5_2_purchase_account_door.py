"""Plan step ``credit_card:CC-5-2``: the purchase door takes an ACCOUNT of its own.

The FIRST door that writes a movement onto another account than its row's
(ruling **R-CC15**: a card swipe in a checking envelope is a movement ON the
card; **R-BAL75**: it folds there; **R-BAL76** dropped the key that forbade
it at ``CC-5-1``).  What is graded here is the door's own rule -- which
account a purchase may name, whose it must be, and what may not be said
beside it -- plus the two facts the design states about the written row: the
envelope's spend grows by the purchase whatever account it moved through, and
the CARD's fold sees the swipe where checking's sees nothing.

Every refusal is graded by the state it leaves: the session holds no new
purchase after a refused call, because the door composes its refusals ahead
of the write (the ``_refusals`` module's contract).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import SettledDayBasisEnum
from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, entry_service
from app.services.row_valuation import purchases_total
from app.services.cash_ledger import account_opening_fact
from app.services.settle_day import SettleDay
from tests._legacy_credit import plant_old_flagged_purchase
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    generate_row_of,
    make_expense_template,
    typed,
)

_ONE_DAY = timedelta(days=1)
_PURCHASED_ON = date(2026, 1, 5)


def _card(seed_user, name="Rewards Card"):
    """Create an active Credit Card account for *seed_user*."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
    )


def _envelope(seed_user, period):
    """Place a Projected Groceries envelope of *seed_user*'s in *period*."""
    template = make_expense_template(
        db.session, seed_user, amount="500.00",
        name="Groceries", category_key="Groceries", is_envelope=True,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _swipe(row, user_id, account_id=None, **overrides):
    """Record a `$60.00` purchase against *row* through the door."""
    details = entry_service.EntryDetails(**{
        "figure": typed(Decimal("60.00")),
        "description": "Kroger",
        "purchased_on": _PURCHASED_ON,
        "account_id": account_id,
        **overrides,
    })
    return entry_service.create_entry(row.id, user_id, details)


def _purchases_of(row_id):
    """Return the purchases stored against *row_id*, straight off the table."""
    return db.session.query(TransactionEntry).filter_by(
        transaction_id=row_id,
    ).all()


class TestWhichAccountAPurchaseNames:
    """The account is the caller's, else the row's; the design's two facts hold."""

    def test_no_account_named_is_the_rows(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every caller but the add-purchase form states nothing: the row's account.

        The byte-identical default the form keeps while the owner has no card
        (ruling **R-CC34**), and what ``statement_match._born_purchase`` and
        every test helper write today.
        """
        with app.app_context():
            row = _envelope(seed_user, seed_periods[0])
            entry = _swipe(row, seed_user["user"].id)
            db.session.flush()
            assert entry.account_id == row.account_id

    def test_a_card_swipe_in_a_checking_envelope_is_a_movement_on_the_card(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Design 3.2's worked case, read by the readers it names.

        Ticked CC, the way a purchase goes on a card since ruling plan#3227
        took the cards out of "Paid from".  The purchase is stored on the
        CARD; the envelope's spend counts it
        (``row_valuation.purchases_total``, what the grid's progress reads);
        and the card's in-flight stream carries it at `-60.00` while
        checking's carries nothing -- the fold attributes a movement to its
        own account (**R-BAL75**, re-pointed at ``balance:X-bi-4``).
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods[0])
            entry = _swipe(row, seed_user["user"].id, on_card=True)
            db.session.flush()

            assert entry.account_id == card.id
            assert entry.owner_id == row.user_id
            assert row.account_id == seed_user["account"].id
            assert purchases_total(row.purchases) == Decimal("60.00")

            scenario_id = seed_user["scenario"].id
            on_the_card = cash_ledger.in_flight_movements(card.id, scenario_id)
            on_checking = cash_ledger.in_flight_movements(
                seed_user["account"].id, scenario_id,
            )
            assert [(m.entry_id, m.delta) for m in on_the_card] == [
                (entry.id, Decimal("-60.00")),
            ]
            assert on_checking == []

    def test_a_companion_files_the_owners_swipe_on_the_owners_card(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):  # pylint: disable=unused-argument
        """The gate is against the ROW's owner, never the caller (ruling **R-CC11**).

        A companion owns no account at all; a gate on ``user_id`` would refuse
        every companion swipe.  The written row is the owner's on the owner's
        card, authored by the companion.
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods[0])
            companion_id = seed_companion["user"].id
            assert companion_id != row.user_id

            entry = _swipe(row, companion_id, on_card=True)
            db.session.flush()

            assert entry.account_id == card.id
            assert entry.owner_id == row.user_id
            assert entry.user_id == companion_id


class TestWhatTheDoorRefuses:
    """Foreign -> 404; archived, a card, holding no cash, or the flag beside the card -> refused."""

    def test_another_owners_account_is_not_found(
        self, app, db, seed_user, seed_periods, second_user,
    ):  # pylint: disable=unused-argument
        """The security response rule: one answer for "not yours" and "no such row"."""
        with app.app_context():
            row = _envelope(seed_user, seed_periods[0])
            with pytest.raises(NotFoundError, match=r"^Account not found\.$"):
                _swipe(
                    row, seed_user["user"].id,
                    account_id=second_user["account"].id,
                )
            assert _purchases_of(row.id) == []

    def test_a_missing_account_is_not_found_the_same_way(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Same message, same exception: nothing distinguishes the two."""
        with app.app_context():
            row = _envelope(seed_user, seed_periods[0])
            with pytest.raises(NotFoundError, match=r"^Account not found\.$"):
                _swipe(row, seed_user["user"].id, account_id=999_999)
            assert _purchases_of(row.id) == []

    def test_an_archived_account_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A new door does not copy the row doors' archived-account admission.

        Ruling **R-CC31**'s review named that admission as pre-existing at the
        row doors; a purchase filed onto an archived account would land on no
        surface that loads it.
        """
        with app.app_context():
            card = _card(seed_user)
            card.is_active = False
            db.session.flush()
            row = _envelope(seed_user, seed_periods[0])
            with pytest.raises(ValidationError, match=r"archived"):
                _swipe(row, seed_user["user"].id, account_id=card.id)
            assert _purchases_of(row.id) == []

    def test_a_card_other_than_the_rows_is_refused_naming_credit_and_cc(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """"Paid from" never names a card other than the row's own (ruling plan#3227).

        Credit puts a bill on a card and the CC tick puts a purchase on one,
        so a hand-built post naming the card under "Paid from" is refused in
        a sentence naming them, rather than filed on the card.
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods[0])
            with pytest.raises(
                ValidationError,
                match=(
                    r"^'Rewards Card' is a credit card, so a purchase cannot be "
                    r"paid from it: Credit puts a bill on a card, and CC puts a "
                    r"purchase on one\.$"
                ),
            ):
                _swipe(row, seed_user["user"].id, account_id=card.id)
            assert _purchases_of(row.id) == []

    def test_an_account_that_holds_no_cash_is_refused_per_kind(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """ONE predicate, the picker's (ruling plan#3227): a loan, a 401(k), a CD, a house.

        Each is the owner's, active, and of a type ``is_liquid`` does not
        mark, so each is refused by the same sentence -- where the first cut
        of this door admitted every non-loan account and a crafted POST could
        file a swipe on a 401(k), whose balance never folds movements.  A CD
        and a house are assets, so a predicate that widened to "not a loan"
        or to "an asset" fails on them.
        """
        with app.app_context():
            outside = [
                create_loan_account(seed_user, db.session, name="Car Loan"),
                create_account_of_type(seed_user, db.session, "401(k)", "Work 401k"),
                create_account_of_type(seed_user, db.session, "CD", "12-month CD"),
                create_account_of_type(seed_user, db.session, "Property", "House"),
            ]
            row = _envelope(seed_user, seed_periods[0])
            for account in outside:
                assert account.is_active and account.user_id == row.user_id
                with pytest.raises(
                    ValidationError, match=r"not an account a purchase can be paid from",
                ):
                    _swipe(row, seed_user["user"].id, account_id=account.id)
            assert _purchases_of(row.id) == []

    def test_every_account_that_holds_cash_is_admitted_per_kind(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Savings, an HYSA, a money market and a second checking: each files the purchase there.

        Ruling plan#3227: "Paid from" lists the row's own account, then every
        active account of the owner's that holds cash (the types
        ``ref.account_types.is_liquid`` marks).  Until then the door refused
        savings and a second checking as outside the cash-flow set (rulings
        R-CC37 and R-CC39).  Each purchase is stored on the account named and
        in flight there, and checking, the row's account, carries none.
        """
        with app.app_context():
            cash = [
                create_account_of_type(seed_user, db.session, "Savings", "Rainy Day"),
                create_account_of_type(seed_user, db.session, "HYSA", "High Yield"),
                create_account_of_type(
                    seed_user, db.session, "Money Market", "Money Market",
                ),
                create_account_of_type(
                    seed_user, db.session, "Checking", "Second Checking",
                ),
            ]
            row = _envelope(seed_user, seed_periods[0])
            scenario_id = seed_user["scenario"].id
            for account in cash:
                entry = _swipe(row, seed_user["user"].id, account_id=account.id)
                db.session.flush()
                assert entry.account_id == account.id
                assert [
                    (m.entry_id, m.delta)
                    for m in cash_ledger.in_flight_movements(account.id, scenario_id)
                ] == [(entry.id, Decimal("-60.00"))]
            assert cash_ledger.in_flight_movements(
                seed_user["account"].id, scenario_id,
            ) == []

    def test_a_row_living_outside_the_set_keeps_its_purchases_on_its_own_account(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The row's OWN account is always admitted, first in the list.

        A Savings envelope's purchase names Savings -- by default, and when
        the form states it explicitly.  The first member of plan#3227's list,
        graded on its own (the case above is the rest of it).
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            template = make_expense_template(
                db.session, seed_user, amount="200.00", name="Vacation",
                category_key="Groceries", is_envelope=True, account=savings,
            )
            row = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert row.account_id == savings.id

            defaulted = _swipe(row, seed_user["user"].id)
            stated = _swipe(row, seed_user["user"].id, account_id=savings.id)
            db.session.flush()
            assert defaulted.account_id == savings.id
            assert stated.account_id == savings.id

    def test_the_tick_beside_another_account_is_refused_and_alone_files_on_the_card(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The tick and "Paid from" may not both say where the money went; the tick alone names the card.

        Plan task plan#2930 (ruling plan#2888): the CC tick says *this went on
        the card*, and so does a card named as the account.  Both at once is
        one fact in two homes (rule 14), refused before anything is written,
        by the test that refused the CC flag beside another account until
        then.  The tick alone files the purchase on the owner's one card, and
        the add door writes no CC flag (the old lines keep theirs, plan#1034).
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods[0])
            with pytest.raises(ValidationError, match=r"not both"):
                _swipe(
                    row, seed_user["user"].id,
                    account_id=card.id, on_card=True,
                )
            assert _purchases_of(row.id) == []

            ticked = _swipe(row, seed_user["user"].id, on_card=True)
            db.session.flush()
            assert ticked.is_credit is False
            assert ticked.account_id == card.id


class TestTheBooksBoundaryIsTheMovementsAccounts:
    """A card purchase is dated against the CARD's opening, by construction."""

    def test_a_card_purchase_cannot_be_dated_on_the_cards_opening_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``record_settle_day`` reads the ENTRY's account, whatever its row names.

        The card opened the day before the frozen today; checking's books
        opened before the seeded calendar.  The same day settles a checking
        purchase and refuses the card's, and the refusal names the card.
        """
        with app.app_context():
            card = _card(seed_user)
            cards_opening = account_opening_fact(card.id).opened_on
            assert cards_opening > account_opening_fact(
                seed_user["account"].id,
            ).opened_on
            row = _envelope(seed_user, seed_periods[0])
            owner_id = seed_user["user"].id
            # Both purchases MADE before the card's books open, so the only
            # rule the settle day below can meet is the books boundary (a
            # posting day before the purchase day is a different refusal).
            made_on = cards_opening - _ONE_DAY
            on_the_card = _swipe(
                row, owner_id, on_card=True, purchased_on=made_on,
            )
            on_checking = _swipe(row, owner_id, purchased_on=made_on)
            db.session.flush()
            that_day = SettleDay(
                day=cards_opening, basis=SettledDayBasisEnum.ENTERED,
            )

            entry_service.update_entry(
                on_checking.id, owner_id, settle_day=that_day,
            )
            db.session.flush()
            assert on_checking.settled_on == cards_opening

            with pytest.raises(ValidationError, match=r"books open on"):
                entry_service.update_entry(
                    on_the_card.id, owner_id, settle_day=that_day,
                )
            # The refusal precedes the write, so the stored pair is untouched:
            # re-read from the database rather than trusted in memory.
            db.session.refresh(on_the_card)
            assert on_the_card.settled_on is None

            entry_service.update_entry(
                on_the_card.id, owner_id,
                settle_day=SettleDay(
                    day=cards_opening + _ONE_DAY,
                    basis=SettledDayBasisEnum.ENTERED,
                ),
            )
            db.session.flush()
            assert on_the_card.settled_on == cards_opening + _ONE_DAY


class TestTheUpdateDoorRefusesTheSameCombination:
    """The update door writes the flag ON nowhere; a stored one beside another account is kept."""

    def test_ticking_cc_on_a_card_purchase_writes_no_flag(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The combination the add door refuses is unwritable at the edit door: its tick moves.

        Until plan task plan#3142 a tick here flipped the flag ON beside the
        card and was refused ("not both"); since then the tick says where the
        money went, the purchase is on the card already, and nothing moves or
        is flagged (``entry_service._details.tick_edit``).
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods[0])
            owner_id = seed_user["user"].id
            entry = _swipe(row, owner_id, on_card=True)
            db.session.flush()

            entry_service.update_entry(entry.id, owner_id, on_card=True)
            db.session.flush()
            # Re-read from the database rather than trusted in memory.
            db.session.refresh(entry)
            assert (entry.account_id, entry.is_credit) == (card.id, False)

    def test_an_edit_that_leaves_a_stored_flag_alone_is_admitted_after_the_row_moved(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A legacy flagged line whose ROW moved on (ruling **R-CC36**) is still editable.

        The edit form posts the tick on every submit.  The stored pair --
        flag set, account differing -- was written by no door: the line sat on
        the account its row named, and the row moved.  Re-describing it
        re-states the tick it already shows and is admitted; a flagged line
        ticked keeps its flag and moves nowhere (plan task plan#3142).
        """
        with app.app_context():
            other = create_account_of_type(
                seed_user, db.session, "Checking", "Second Checking",
            )
            row = _envelope(seed_user, seed_periods[0])
            owner_id = seed_user["user"].id
            legacy = plant_old_flagged_purchase(
                row.id, owner_id, entry_service.EntryDetails(
                    figure=typed(Decimal("60.00")), description="Kroger",
                    purchased_on=_PURCHASED_ON,
                ),
            )
            db.session.flush()
            row.account_id = other.id
            db.session.flush()
            assert legacy.account_id != row.account_id

            entry_service.update_entry(
                legacy.id, owner_id, description="Amazon", on_card=True,
            )
            db.session.flush()
            assert legacy.description == "Amazon"
            assert legacy.is_credit is True
            assert legacy.account_id != row.account_id
