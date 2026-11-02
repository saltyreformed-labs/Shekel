"""The worked-out ledger equals the stored one to the cent (plan task plan#2527).

The suite half of X-ew's first leaf's proof (plan step plan#2513;
``docs/plans/verification.md``): the ledger
:func:`app.services.ledger_lines.worked_out_lines` works out from the records
nets, per (chart row, paycheck, entry day), to what the stored journal holds,
each worked-out line mapped onto its stored row by the row's
:class:`~app.services.ledger_lines.ChartKey`
(``tests._test_helpers.stored_chart_key``).  The comparison is
``tests._ledger_parity.assert_ledger_parity``, and it runs in two places:

* **every world of the posting reconciliation suites**, at each of their
  sweeps (the cash, account-anchor, loan and Step-2 transfer oracles); two of
  those worlds write a record past every writer and name the cells that leaves
  the store behind on;
* **here, a world for each source kind** the store posts under, each built
  so that its writers post it -- through production's doors, or, for the
  loan true-up, by the suite's builder that appends the event and runs the
  door's own posting sync -- and each shown to hold an entry of its kind on
  both sides, so no world agrees vacuously.

**The comparison is shown to fire**: a planted stored line the records do not
hold, a record the store has not caught up with, and a difference a caller
states that is not there each fail it, with a message naming every cell's
chart row, paycheck and day -- a failure message is code a green suite never
runs, so each is forced to run once and read back.

What a pass does NOT say: the worked-out read and the store's writers apply
the same target rules (plan task plan#2526 moved them into
``app.services.ledger_lines`` and the writers import them back), so a wrong
rule moves both sides alike.  The rules are pinned by hand-computed literals
elsewhere (``test_the_worked_out_read/``, ``test_the_target_rules.py``, the
reconciliation oracles); this grades whether the store, as its writers left
it, is the ledger the records work out to -- its filing, its chart rows,
whether it has caught up.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.services import anchor_service, entry_service
from app.services.ledger_lines import (
    account_counter_key,
    category_key,
    linked_key,
    worked_out_lines,
)
from tests._ledger_parity import (
    LedgerParityDifference,
    assert_ledger_parity,
    ledger_parity_differences,
)
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    generate_row_of,
    make_expense_template,
    typed,
)

# Inside seed_periods' last paycheck (2026-05-08 - 2026-05-21), after every
# paycheck a world files a row in.
_TODAY = date(2026, 5, 15)


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Freeze the app's today at :data:`_TODAY` for every case."""
    freeze_today(monkeypatch, _TODAY)


def _scenario_id(seed_user):
    """Return the seed owner's baseline scenario id."""
    return seed_user["scenario"].id


def _paycheck_holding(seed_periods, day):
    """Return the id of the seeded paycheck whose window holds *day*.

    The latest of ``seed_periods`` starting on or before *day*: a saved
    paycheck runs to the day before the next one starts, and every day a case
    names falls before the last one's fourteenth day.  Read off the fixture's
    own starts, not through the pay calendar's filing rule the read files
    corrections by, so the expected paycheck states the fixture rather than
    the code under test.
    """
    return [
        period for period in seed_periods if period.start_date <= day
    ][-1].id


def _stored_row_id(seed_user, **columns):
    """Return the id of the owner's one stored chart row with *columns*."""
    return (
        _db.session.query(LedgerAccount)
        .filter_by(user_id=seed_user["user"].id, **columns)
        .one()
        .id
    )


def _kind_id(member):
    """Return the stored id of a ``LedgerAccountKindEnum`` member."""
    return ref_cache.ledger_account_kind_id(member)


def _plant_entry(seed_user, pay_period_id, entry_date, legs):
    """Plant a balanced stored ``purchase`` entry no record books; flush it.

    Written straight to the stored journal, past every writer, in the seed
    owner's baseline: one ``expense`` leg per ``{chart row id: amount}`` in
    *legs*, which must net to ``$0.00`` (the deferred per-entry trigger's
    rule, enforced at commit, which a case never reaches).
    """
    entry = JournalEntry(
        user_id=seed_user["user"].id, scenario_id=_scenario_id(seed_user),
        pay_period_id=pay_period_id, entry_date=entry_date,
        source_kind_id=ref_cache.posting_source_id(PostingSourceEnum.PURCHASE),
        description="Planted: no record books this",
    )
    _db.session.add(entry)
    _db.session.flush()
    expense = ref_cache.posting_kind_id(PostingKindEnum.EXPENSE)
    _db.session.add_all([
        Posting(
            journal_entry_id=entry.id, ledger_account_id=row_id,
            amount=amount, posting_kind_id=expense,
        )
        for row_id, amount in legs.items()
    ])
    _db.session.flush()


# ---------------------------------------------------------------------------
# A world for each source kind, each one its writers post
# ---------------------------------------------------------------------------


def _purchases(seed_user, seed_periods):
    """A Groceries bill paid today and an envelope's dated purchase.

    The $40.00 bill is budgeted in January and settles May 15, so its lines
    file in January on May 15 (ruling pay_calendar:R-PC53); the $25.00
    purchase on a $100.00 envelope is the purchase kind's other shape, a
    movement booked on its own bank day under an open envelope (ruling R-FM).
    """
    create_settled_cash_transaction(
        seed_user, _db.session, seed_periods[0], Decimal("40.00"),
        category=seed_user["categories"]["Groceries"],
    )
    envelope = generate_row_of(
        make_expense_template(
            _db.session, seed_user, amount="100.00", name="Groceries Run",
            category_key="Groceries", is_envelope=True,
        ),
        seed_periods[8],
    )
    entry_service.create_entry(
        transaction_id=envelope.id,
        user_id=seed_user["user"].id,
        details=entry_service.EntryDetails(
            figure=typed(Decimal("25.00")),
            description="Market",
            purchased_on=_TODAY,
            is_credit=False,
            settle_day=an_entered_day(_TODAY),
        ),
    )


def _transfer(seed_user, seed_periods):
    """A $250.00 transfer from Checking to a new Savings, settled."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Parity Savings",
    )
    create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[2], amount=Decimal("250.00"),
    )


def _trued_up_loan(seed_user):
    """Return a $250,000.00 6% loan trued up to $100,000.00 on 2026-01-10.

    The suite's one shared trued-up loan: its true-up is appended by
    ``insert_trueup_event``, which runs the posting sync the loan door
    (``apply_loan_anchor_true_up``) runs, rather than the door itself.
    """
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("100000.00"),
        anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
        origination_date=date(2025, 1, 1), name="Parity Loan",
    )


def _loan_payment(seed_user, seed_periods):
    """A $1,000.00 payment, settled, into the trued-up loan."""
    create_settled_transfer(
        seed_user, _db.session, seed_user["account"],
        _trued_up_loan(seed_user), seed_periods[1], amount=Decimal("1000.00"),
    )


def _loan_opening(seed_user, _seed_periods):
    """A $20,000.00 loan carrying only its origination."""
    create_loan_account(
        seed_user, _db.session, name="Opened Loan",
        principal=Decimal("20000.00"), rate=Decimal("0.05000"), term=60,
        origination_date=date(2025, 6, 1),
    )


def _loan_trueup(seed_user, _seed_periods):
    """The trued-up loan alone: its opening and its true-up, no payment."""
    _trued_up_loan(seed_user)


def _account_opening(seed_user, _seed_periods):
    """A Savings account opened at $500.00 three days ago."""
    create_account_of_type(
        seed_user, _db.session, "Savings", "Opened Savings",
        anchor_balance=Decimal("500.00"),
        observed_on=_TODAY - timedelta(days=3),
    )


def _account_trueup(seed_user, _seed_periods):
    """A $1,000.00 Money Market trued up to $1,150.00 yesterday, at the door."""
    account = create_account_of_type(
        seed_user, _db.session, "Money Market", "Trued Money Market",
        anchor_balance=Decimal("1000.00"),
        observed_on=_TODAY - timedelta(days=3),
    )
    anchor_service.apply_anchor_true_up(
        account=account, new_balance=Decimal("1150.00"),
        observed_on=_TODAY - timedelta(days=1),
    )


def _source_kinds_on_each_side(seed_user):
    """Return the source kinds the store's entries and the read's lines carry.

    Both for the seed owner's baseline: the stored journal entries' kinds,
    then the worked-out lines'.
    """
    owner_id = seed_user["user"].id
    scenario_id = _scenario_id(seed_user)
    stored = {
        kind_id for (kind_id,) in _db.session.query(
            JournalEntry.source_kind_id,
        ).filter(
            JournalEntry.user_id == owner_id,
            JournalEntry.scenario_id == scenario_id,
        )
    }
    worked_out = {
        line.source_kind_id
        for line in worked_out_lines(owner_id, scenario_id)
    }
    return stored, worked_out


# Each source kind the store posts under, and the world that posts one.
_WORLDS = [
    (PostingSourceEnum.PURCHASE, _purchases),
    (PostingSourceEnum.TRANSFER_MOVEMENT, _transfer),
    (PostingSourceEnum.LOAN_PAYMENT, _loan_payment),
    (PostingSourceEnum.LOAN_OPENING, _loan_opening),
    (PostingSourceEnum.LOAN_TRUEUP, _loan_trueup),
    (PostingSourceEnum.ACCOUNT_OPENING, _account_opening),
    (PostingSourceEnum.ACCOUNT_TRUEUP, _account_trueup),
]


class TestEachSourceKindsWorldAgrees:
    """For each kind of entry the store posts, a world in which the two agree."""

    @pytest.mark.parametrize("source, build", [
        pytest.param(source, build, id=source.value)
        for source, build in _WORLDS
    ])
    def test_the_worked_out_nets_are_the_stored_nets(
        self, app, seed_user, seed_periods, source, build,
    ):
        """The world's ledger nets alike per (chart row, paycheck, day).

        Every :class:`~app.enums.PostingSourceEnum` member is one case
        (``test_every_source_kind_has_a_world`` holds the list to the enum).
        The store holds an entry of the case's kind and the read a line of it,
        so the world is not one where both sides are empty of what it names.
        """
        with app.app_context():
            build(seed_user, seed_periods)
            _db.session.commit()
            source_id = ref_cache.posting_source_id(source)

            stored, worked_out = _source_kinds_on_each_side(seed_user)

            assert source_id in stored
            assert source_id in worked_out
            assert_ledger_parity(_db.session, _scenario_id(seed_user))

    def test_every_source_kind_has_a_world(self):
        """The cases above name every source kind the store posts under.

        A kind added to :class:`~app.enums.PostingSourceEnum` without a world
        here would be a kind the parity is never shown over.
        """
        assert {source for source, _ in _WORLDS} == set(PostingSourceEnum)


# ---------------------------------------------------------------------------
# The comparison, shown to fire
# ---------------------------------------------------------------------------


class TestTheComparisonIsShownToFire:
    """Each way the two ledgers can part fails it, naming every cell."""

    def test_a_planted_stored_line_the_records_do_not_hold(
        self, app, seed_user, seed_periods,
    ):
        """A balanced $12.34 entry no record books fails at both of its cells.

        Planted beside the $40.00 Groceries bill, in the 2026-03-13 -
        2026-03-26 paycheck and dated 2026-03-14, a day and paycheck where
        the records book nothing on either row: Checking
        ``+12.34``, Groceries (Expense) ``-12.34``.  Stored ``12.34`` against
        worked out ``0.00`` on Checking, ``-12.34`` against ``0.00`` on
        Groceries -- and nowhere else.
        """
        with app.app_context():
            groceries = seed_user["categories"]["Groceries"]
            checking = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, _db.session, seed_periods[0], Decimal("40.00"),
                category=groceries,
            )
            _db.session.commit()
            scenario_id = _scenario_id(seed_user)
            assert_ledger_parity(_db.session, scenario_id)

            planted_on = date(2026, 3, 14)
            paycheck = _paycheck_holding(seed_periods, planted_on)
            checking_row = _stored_row_id(
                seed_user, account_id=checking.id,
                kind_id=_kind_id(LedgerAccountKindEnum.LINKED),
            )
            groceries_row = _stored_row_id(
                seed_user, category_id=groceries.id,
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
            )
            _plant_entry(seed_user, paycheck, planted_on, {
                checking_row: Decimal("12.34"),
                groceries_row: Decimal("-12.34"),
            })

            checking_key = linked_key(checking)
            groceries_key = category_key(
                groceries.id, LedgerAccountClassEnum.EXPENSE,
            )
            assert ledger_parity_differences(_db.session, scenario_id) == [
                LedgerParityDifference(
                    key=groceries_key, stored_row_ids=(groceries_row,),
                    pay_period_id=paycheck, entry_date=planted_on,
                    stored=Decimal("-12.34"), worked_out=Decimal("0.00"),
                ),
                LedgerParityDifference(
                    key=checking_key, stored_row_ids=(checking_row,),
                    pay_period_id=paycheck, entry_date=planted_on,
                    stored=Decimal("12.34"), worked_out=Decimal("0.00"),
                ),
            ]
            with pytest.raises(AssertionError) as raised:
                assert_ledger_parity(_db.session, scenario_id)

            message = str(raised.value)
            assert (
                f"the worked-out ledger in scenario {scenario_id} parts from "
                f"the stored one at 2 cell(s) no caller expected:"
            ) in message
            assert (
                f"chart row #{groceries_row} [category / Expense / record "
                f"{groceries.id}], paycheck {paycheck}, day 2026-03-14: "
                f"stored -12.34, worked out 0.00"
            ) in message
            assert (
                f"chart row #{checking_row} [linked / Asset / record "
                f"{checking.id}], paycheck {paycheck}, day 2026-03-14: "
                f"stored 12.34, worked out 0.00"
            ) in message

    def test_a_record_the_store_has_not_caught_up_with(
        self, app, seed_user, seed_periods,
    ):
        """A true-up written past its door fails where its lines would book.

        A $1,000.00 Money Market opened May 12 through the door, then a
        $1,150.00 assertion for May 13 added as a bare row, so no writer
        posts it: worked out, it books ``1,150.00 - 1,000.00 = +150.00`` on
        the account and ``-150.00`` on its Interest Income counter (a Money
        Market's true-up is interest, ruling R-FO), both in May 13's
        paycheck; the store holds neither, and has no Interest Income row at
        all.  Naming the two cells as known passes, which is the shape the
        reconciliation suites' two such worlds take.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Money Market", "Behind Money Market",
                anchor_balance=Decimal("1000.00"),
                observed_on=_TODAY - timedelta(days=3),
            )
            _db.session.commit()
            scenario_id = _scenario_id(seed_user)
            assert_ledger_parity(_db.session, scenario_id)

            trued_on = _TODAY - timedelta(days=2)
            _db.session.add(AccountAnchorHistory(
                account_id=account.id, anchor_balance=Decimal("1150.00"),
                observed_on=trued_on, recorded_on=trued_on,
            ))
            _db.session.flush()

            paycheck = _paycheck_holding(seed_periods, trued_on)
            account_row = _stored_row_id(
                seed_user, account_id=account.id,
                kind_id=_kind_id(LedgerAccountKindEnum.LINKED),
            )
            interest = account_counter_key(
                LedgerAccountKindEnum.INTEREST_INCOME, account.id,
            )
            behind = [
                LedgerParityDifference(
                    key=interest, stored_row_ids=(),
                    pay_period_id=paycheck, entry_date=trued_on,
                    stored=Decimal("0.00"), worked_out=Decimal("-150.00"),
                ),
                LedgerParityDifference(
                    key=linked_key(account), stored_row_ids=(account_row,),
                    pay_period_id=paycheck, entry_date=trued_on,
                    stored=Decimal("0.00"), worked_out=Decimal("150.00"),
                ),
            ]
            assert ledger_parity_differences(_db.session, scenario_id) == behind
            with pytest.raises(AssertionError) as raised:
                assert_ledger_parity(_db.session, scenario_id)

            message = str(raised.value)
            assert (
                f"chart row (none stored) [interest_income / Income / record "
                f"{account.id}], paycheck {paycheck}, day 2026-05-13: "
                f"stored 0.00, worked out -150.00"
            ) in message
            assert (
                f"chart row #{account_row} [linked / Asset / record "
                f"{account.id}], paycheck {paycheck}, day 2026-05-13: "
                f"stored 0.00, worked out 150.00"
            ) in message
            assert_ledger_parity(_db.session, scenario_id, tuple(behind))

    def test_a_stated_difference_that_is_not_there(
        self, app, seed_user, seed_periods,
    ):
        """A known cell the two ledgers agree at fails, naming it as stated.

        The $40.00 Groceries bill agrees everywhere, so a caller stating
        that its Checking line differs on May 15 has stated a difference that
        is not there -- the stale claim the known arm exists to refuse.
        """
        with app.app_context():
            checking = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, _db.session, seed_periods[0], Decimal("40.00"),
                category=seed_user["categories"]["Groceries"],
            )
            _db.session.commit()
            scenario_id = _scenario_id(seed_user)
            checking_row = _stored_row_id(
                seed_user, account_id=checking.id,
                kind_id=_kind_id(LedgerAccountKindEnum.LINKED),
            )
            stated = LedgerParityDifference(
                key=linked_key(checking), stored_row_ids=(checking_row,),
                pay_period_id=seed_periods[0].id, entry_date=_TODAY,
                stored=Decimal("0.00"), worked_out=Decimal("-40.00"),
            )

            with pytest.raises(AssertionError) as raised:
                assert_ledger_parity(_db.session, scenario_id, (stated,))

            message = str(raised.value)
            assert (
                f"the worked-out ledger in scenario {scenario_id} does not "
                f"part from it as stated at 1 cell(s) the caller expected:"
            ) in message
            assert (
                f"chart row #{checking_row} [linked / Asset / record "
                f"{checking.id}], paycheck {seed_periods[0].id}, day "
                f"2026-05-15: stored 0.00, worked out -40.00"
            ) in message
            assert "no caller expected" not in message
