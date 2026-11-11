"""A settled row's worth, its one day, the rows the walk sees, and a source the books refuse."""

from datetime import date, time
from decimal import Decimal

import pytest

from app.services.cash_ledger import settled_cash_facts, walk_cash_ledger
from app.services import transaction_service
from app.services.row_valuation import purchases_total, settled_figure
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.services.ledger_lines import linked_key
from app.utils.dates import display_today
from tests._ledger_reads import family_movement_ids, movement_nets_by_date
from tests._test_helpers import (
    add_txn,
    an_entered_day,
    create_savings_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    figure_source_columns,
    freeze_today,
    one_off_row_of,
    settle_day_columns,
)
from app.services.settle_day import record_settle_day

from ._helpers import _instant, _opened_at, _money_facts, _running_balance


class TestSourceFactValuation:
    """A settled row is worth its ``effective_amount``, signed by direction."""

    def test_income_adds_and_expense_subtracts(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """+$250.00 income and -$75.00 expense: 1000 + 250 - 75 = 1175.00."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, period, Decimal("250.00"),
            is_income=True, settled_on=date(2026, 2, 1), name="pay",
        )
        create_settled_cash_transaction(
            seed_user, period, Decimal("75.00"),
            settled_on=date(2026, 2, 2), name="spend",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("1175.00")

    def test_actual_amount_wins_over_the_estimate(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A $100.00 estimate that settled at $84.20 is worth $84.20.

        ``effective_amount`` prefers ``actual_amount`` when populated, so the
        walk values a settled row at what really moved: 1000 - 84.20 = 915.80.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, period, Decimal("100.00"),
            settled_amount=Decimal("84.20"),
            settled_on=date(2026, 2, 1), name="spend",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("915.80")

    def test_a_still_projected_row_is_not_in_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """PLANNED rows belong to the reader, not the clock-free walk (R-G).

        A projected row's effective date is ``max(its date, as_of + 1d)``, which
        depends on the reader's as-of; putting it in the walk would make this
        leaf's output a function of the wall clock -- the corruption shape plan
        step A3 removed from the loan walk.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        one_off_row_of(
            period,
            name="unpaid bill",
            amount=Decimal("500.00"),
            user_id=period.user_id,
            account_id=account.id,
            scenario_id=scenario.id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_a_credit_card_entry_never_leaves_checking(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An envelope's CREDIT entries are excluded from the cash effect.

        The shape this valuation exists for, and the one an ``effective_amount``
        walk gets wrong.  A $200.00 grocery envelope settled with a $120.00 debit
        purchase and an $80.00 credit-card purchase moved only $120.00 out of
        checking -- the credit portion leaves when its CC Payback sibling
        settles, so counting it here debits the money twice.  Hand-computed:
        1000 - 120 = 880.00.

        Measured on production 2026-07-25 before the shared
        :func:`~app.services.cash_ledger.settled_cash_leg` was adopted: an
        ``effective_amount`` walk diverged from the posted ledger on 10 of the
        real Checking account's 130 settled rows, by up to $181.58.

        **The $120.00 is ONE fact, the purchase's, and ruling R-FM is why**
        (plan step X-f3b).  The debit purchase carries the day the bank took
        it (01-05), so it is a movement of its own on THAT day.  Through plan
        step ``X-bi-3e`` the envelope's close on 02-01 was a second fact
        worth ``200 - 80 credit - 120 already posted = $0.00``; since
        ``balance:X-bi-4a`` a row is no fact at all (ruling **R-BAL80**), so
        the stream holds the purchase and nothing else.  The account is
        $120.00 lighter either way -- what moved is WHEN, from the day the
        owner finished the envelope to the day the money actually left, which
        is the whole point of the step.
        """
        from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_envelope_txn  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_envelope_txn(
            seed_user, db.session, period, "Groceries", Decimal("200.00"),
        )
        for amount, is_credit, day in (
            (Decimal("120.00"), False, date(2026, 1, 5)),
            (Decimal("80.00"), True, date(2026, 1, 6)),
        ):
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=amount,
                description="purchase",
                purchased_on=day,
                is_credit=is_credit,
                **settle_day_columns(day),
            ))
        db.session.flush()
        # Settled through PRODUCTION's own verb rather than hand-set: the
        # ``recorded - credit`` formula collapses to the debit-only outflow
        # ONLY because a settled envelope is worth ALL of its entries, so a
        # test that stipulated that premise would keep passing if production
        # stopped honouring it.  The verb picks the entries branch and records
        # the ``purchases`` basis, which stores no figure at all (plan step
        # X-au-c3) -- the row's own entries state it, and the accessor is what
        # reads it back.
        transaction_service.settle_transaction(txn)
        assert settled_figure(txn) == Decimal("200.00")
        assert purchases_total(list(txn.entries)) == Decimal("200.00")
        record_settle_day(txn, an_entered_day(date(2026, 2, 1)))
        db.session.commit()

        [purchase_fact] = settled_cash_facts(account.id, scenario.id)
        assert (purchase_fact.settled_on, purchase_fact.delta) == (
            date(2026, 1, 5), Decimal("-120.00"),
        )
        assert purchase_fact.entry_id is not None
        assert _running_balance(account, scenario) == Decimal("880.00")
        # The claim the row-leg move to ``cash_ledger`` rested on (plan step
        # X-a), and the movement's fact inherits it: the walk's delta IS the
        # amount the ledger books on the account's own line, in the SAME sign
        # and on the same day.  A flipped sign still balances every entry, so
        # nothing else would catch it.
        assert movement_nets_by_date(
            db.session, family_movement_ids(txn), linked_key(account), scenario.id,
        ) == {purchase_fact.settled_on: purchase_fact.delta}

    def test_a_settled_transfer_out_reduces_the_walk_like_any_other_row(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Transfer Invariant 5: a settled transfer reaches the walk as its side's record.

        A $300.00 transfer out of Checking is one dated fact on Checking: its
        from-side's record, valued at the transfer's figure
        (:func:`~app.services.cash_ledger.settled_cash_facts`), exactly as a
        settled expense's movement is.  Hand-computed: 1000 - 300 = 700.00.
        It said the walk read the transfer's shadow rows and never queried
        ``Transfer``, which leaf ``balance:X-bi-6-4a`` made false.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        create_settled_transfer(
            seed_user, db.session, account, savings, period,
            amount=Decimal("300.00"), settled_on=date(2026, 2, 1),
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("700.00")


class TestAttributionIsOneKey:
    """One STORED day per fact; nothing here derives it (ruling R-EC)."""

    def test_a_settled_row_with_no_day_is_INVISIBLE_to_the_walk_and_named_by_DC11(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The walk reads nothing for a broken row; the integrity check names it.

        **This test asserted a REFUSAL through plan step ``X-bi-3e``**, and
        the opposite of that until plan step X-f1: the reader derived the day
        from ``paid_at`` and fell back to the row's pay-period ``start_date``
        when the instant was NULL (8 of 146 production settled rows took that
        fallback -- a GUESS the reader could not see), then, the guess gone,
        REFUSED an undated settled row loudly, because silently dating it
        would put real money on a fabricated day and silently dropping it
        would take money out of a balance without saying so.

        Since plan step ``balance:X-bi-4a`` the walk reads MOVEMENTS and no
        row (ruling **R-BAL80**), so it has nothing to refuse: a settled row
        with no day, built bare with ``settled_on=None`` (the only way to
        construct the state -- every write door refuses it), carries no
        dated movement and is simply absent from the stream.  That absence
        is the "silently dropping it" the old refusal forbade, and the alarm
        moved with the read: ``scripts/integrity_check.py``'s **DC-11** names
        every settled row the fold cannot see, this one included.
        """
        # pylint: disable=import-outside-toplevel  -- the script is not a
        # package; the integrity suite imports it the same way.
        from scripts.integrity_check import check_data_consistency

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[3]
        _opened_at(account, _instant(2026, 1, 1))
        txn = add_txn(
            db.session, seed_user, period, "legacy settle", "12.34",
            status_enum=StatusEnum.DONE, settled_on=None,
        )
        db.session.commit()

        facts = settled_cash_facts(account.id, scenario.id)
        assert [fact for fact in facts if fact.transaction_id == txn.id] == []
        dc11 = next(
            r for r in check_data_consistency(db.session) if r.check_id == "DC-11"
        )
        assert not dc11.passed
        assert txn.id in {row["transaction_id"] for row in dc11.details}

    def test_an_evening_eastern_settle_counts_from_the_users_day(
        self, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """A settle at 20:00 Eastern attributes to THAT day, not to UTC's next.

        The discriminating clock: 2026-03-03 20:00 Eastern is already
        2026-03-04 in UTC, so a fixture pinned where the two zones agree would
        prove nothing.  ``freeze_today`` is asked for that instant explicitly --
        its default is noon UTC, which is the same civil day in both calendars.

        **It goes through the real write door rather than setting the column**,
        and that is what makes it a walk-grain pin of ruling **R-DH (b)** rather
        than a restatement of its own fixture.  The rule lives in
        ``status_seam`` since plan step X-f1 -- nothing downstream derives a day
        any more -- so this asserts the composition: the seam records the user's
        civil day, and the walk attributes the cash to it unchanged.  Measured
        on production before the column existed: 22 of 139 settled Checking rows
        land on a different day under UTC and 5 of those in a different PAY
        PERIOD.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[2]
        _opened_at(account, _instant(2026, 1, 1))
        # 01:00 UTC on the 4th is 20:00 Eastern on the 3rd.
        freeze_today(monkeypatch, date(2026, 3, 4), at_time=time(1, 0))
        assert display_today() == date(2026, 3, 3)
        assert date.today() == date(2026, 3, 4), (
            "the freeze must separate the two calendars or this test cannot "
            "tell the display rule from the process one"
        )

        txn = create_settled_cash_transaction(
            seed_user, period, Decimal("5.00"),
        )
        db.session.commit()

        assert txn.settled_on == date(2026, 3, 3)
        fact, = _money_facts(settled_cash_facts(account.id, scenario.id))
        assert fact.settled_on == date(2026, 3, 3)


class TestTheWalkSeesOnlyItsOwnRows:
    """Scope: this account, this scenario, contributing rows only."""

    def test_a_non_contributing_row_is_excluded_whatever_it_carries(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A soft-deleted transfer's settled leg must move nothing on the walk.

        The guard that matters most for money: every valuation is TOTAL over
        the contributing gate -- a movement under a non-contributing parent
        moves nothing (``movement_cash_leg``, ruling **R-FM**) and the row is
        worth nothing (``covered_cash_leg``, ruling **R-BAL81**) -- but without
        the SQL exclusion the row would still enter the stream.  Both defences
        were pinned until plan step ``balance:X-bi-6-4d-2`` (the row absent,
        AND every valuation of it zero); the second is pinned since (below).

        **The hidden row is a transfer's leg because no other can hold a
        movement** (ruling **R-CC92**, plan step ``credit_card:CC-5-4a-4``):
        the database refuses a commit that leaves any other row hidden holding
        one.  This was a deleted envelope carrying an $80.00 credit entry, the
        shape that once valued at a fabricated ``+$80.00`` inflow through the
        row's own leg (``settled_cash_leg``, deleted at R-BAL81).  A settled
        transfer's soft delete still hides its legs holding their dated
        payments (finding **balance:BAL-532**), so that is the state staged,
        through the door; plan step ``balance:X-bi-6-4`` closes it, and this
        staging with it.  Re-expressed under rule 5, developer-confirmed
        2026-09-23.

        **Since plan step ``balance:X-bi-6-4d-2`` the state is STAGED inside
        the save and refused at its commit** (ruling **R-BAL167** class 2):
        the delete door takes the payments off (ruling
        **credit_card:R-CC75**) and the deleted-row rule's transfer arm
        refuses a transfer hidden holding one.  The transfer is hidden by
        SQL with the payments left on, the Checking leg -- its transfer and
        its side's record -- is valued inside the save, and the commit is
        then refused.  It also hid the transfer's two twin rows by SQL; from
        plan step ``balance:X-bi-6-4d-2``'s create door on there are none, so
        that statement touched no row (measured at that step's
        vacuous-residue checkpoint), and it is dropped.
        DROPPED: ``settled_cash_facts(...) == []``.  Inside the save the walk
        lists the hidden transfer's record as a fact at ``0.00``: finding
        **BAL-534** deleted the walk's hidden-transfer filter, because the
        state cannot be committed.  Every valuation is still zero and the
        balance is unmoved.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import text

        from app.models.transaction_entry import TransactionEntry
        from app.services.cash_ledger import movement_cash_leg
        from app.services.status_seam import covered_cash_leg
        from app.services.transfer_legs import TransferLeg
        from tests._test_helpers import refused_by_database_rule

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        xfer = create_settled_transfer(
            seed_user, db.session, account, savings, period,
            amount=Decimal("80.00"), settled_on=date(2026, 2, 1),
        )
        db.session.commit()
        assert [fact.delta for fact in settled_cash_facts(
            account.id, scenario.id,
        )] == [Decimal("-80.00")], "the leg must reach the walk while it counts"

        # Hidden the way the soft delete left it until the step, less the
        # twins it also flagged (none exist since plan step
        # ``balance:X-bi-6-4d-2``): the transfer flagged, the payments in
        # place.  STAGED.
        db.session.execute(
            text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
            {"t": xfer.id},
        )
        db.session.expire_all()
        leg = TransferLeg(
            transfer=xfer, account_id=account.id, is_income=False,
        )
        # Read off the transfer's list (ruling plan#1984), checked by its own
        # SQL rather than handed to the leg.
        assert leg.record is db.session.query(TransactionEntry).filter(
            TransactionEntry.expense_transfer_id == xfer.id,
        ).one()

        assert leg.is_deleted is True
        assert covered_cash_leg(leg, leg.account_id) == Decimal("0.00")
        assert leg.covering_movements, (
            "the leg must carry its payment or this grades nothing"
        )
        assert {
            movement_cash_leg(leg, entry) for entry in leg.covering_movements
        } == {Decimal("0.00")}
        assert _running_balance(account, scenario) == Decimal("1000.00")

        with refused_by_database_rule(
            "was deleted while it still holds a recorded payment",
        ) as caught:
            db.session.commit()
        db.session.rollback()
        assert f"transfer {xfer.id} " in str(caught.value)

    def test_another_scenarios_rows_do_not_enter_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Assertions are per-ACCOUNT; settled rows are per-SCENARIO.

        The split ``walk_cash_ledger`` documents as a deliberate contract: the
        same assertions replay in every scenario, each against that scenario's
        own rows.  A what-if scenario's spending must not move the baseline.
        """
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        other = Scenario(
            user_id=seed_user["user"].id, name="What if", is_baseline=False,
        )
        db.session.add(other)
        db.session.flush()
        create_settled_cash_transaction(
            seed_user, period, Decimal("400.00"),
            scenario=other, settled_on=date(2026, 2, 1), name="what-if spend",
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")
        # ...and the SAME assertions replay in the other scenario, against its
        # own row.
        assert _running_balance(account, other) == Decimal("600.00")

    def test_another_accounts_rows_do_not_enter_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settled row on a sibling account of the same user is excluded."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("50.00"),
        )
        create_settled_cash_transaction(
            seed_user, period, Decimal("25.00"),
            account=savings, settled_on=date(2026, 2, 1), name="other acct",
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_a_liability_accounts_negative_anchor_walks_ledger_native(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The walk never branches on account class -- the sign follows TYPE.

        A Credit Card carries an owed-as-NEGATIVE anchor, and a direct charge on
        it is an EXPENSE, so it must make the balance more negative.
        Hand-computed: an asserted -$300.00 with a $75.00 charge settled after
        it walks to -$375.00.  Nothing in the walk consults the account's class
        to get that right, which is what makes the claim structural.
        """
        from tests._test_helpers import create_account_of_type  # pylint: disable=import-outside-toplevel

        _account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        # **The card is OPENED on the calendar's first day** (plan step
        # X-f3c-2c).  ``create_account_of_type`` otherwise asserts the day
        # before today -- the frozen 2026-03-20 -- and the charge below settles
        # 2026-02-01, which would be inside that assertion rather than after
        # it.  Stated at creation because an assertion is append-only: where
        # the origination goes is decided when it is written.
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa", Decimal("-300.00"),
            observed_on=seed_periods[0].start_date,
        )
        create_settled_cash_transaction(
            seed_user, period, Decimal("75.00"),
            account=card, settled_on=date(2026, 2, 1), name="charge",
        )
        db.session.commit()

        assert _running_balance(card, scenario) == Decimal("-375.00")


class TestASourceCannotPredateTheBooks:
    """A settle dated at or before the account's opening is UNSTORABLE.

    **This class replaced ``TestPreOpeningSources``, which pinned the opposite**
    (plan step X-f3c-2b, ruling **R-HG**).  That class graded a walk carrying a
    source dated BEFORE the books opened, and said so deliberately: ruling R-I
    had put the reader's answer in the fold and left the leaf emitting the row
    at its own day, "so the leaf does not unilaterally re-key it".

    What changed is not the leaf's re-keying rule -- it is unchanged -- but
    whether the state it re-keys can exist.  An opening equity is the CLOSING
    balance for its own day, so such a row is already inside the level the fold
    seeds at, and carrying it twice is finding **N-378**.  The fixture below is
    the one the old class used, and the point is that it no longer builds.

    **The walk is therefore free of a filter, and its absence is the property.**
    A leaf that screened its own inputs would be a second statement of a rule
    the database holds, and the two would drift -- which is this arc's own root
    cause 1.
    """

    def test_the_fixture_the_old_contract_rested_on_is_REFUSED(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Books opened 2026-02-01; a $500.00 expense settling 2026-01-15 raises.

        The exact shape ``TestPreOpeningSources`` built, refused at the one
        settle-day writer with a message naming both days.
        """
        account = seed_user["account"]
        period = seed_periods[0]
        # The books' own day is the subject here, so the case names it rather
        # than inheriting the backward-only default (plan step X-f3c-2c).
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        db.session.flush()
        with pytest.raises(ValidationError) as exc:
            create_settled_cash_transaction(
                seed_user, period, Decimal("500.00"),
                settled_on=date(2026, 1, 15), name="pre-opening",
            )
        assert "2026-01-15" in str(exc.value)
        db.session.rollback()

    def test_the_walk_yields_each_source_at_its_OWN_day_and_adds_none(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The whole source list, by day and amount, hand-computed.

        **This replaces an assertion that could not fail.**  Its first version
        checked that every source the walk yields is dated after the opening --
        over rows read from a database the constraint will not let hold a
        violating one, using the same governing-row rule the constraint uses.
        It could only have failed if the SQL and Python lookups disagreed,
        which is graded properly and from both sides by
        ``test_books_boundary.test_the_governing_row``'s
        ``TestTheTwoGoverningLookupsElectTheSameRow``.  As
        a control over what the walk EMITS, which is what its name promised, it
        was a tautology (found by adversarial review, 2026-08-28).

        So this computes instead.  Books open 2026-01-31 (one day before the
        2026-02-01 opening assertion), a ``$500.00`` expense settles 2026-02-05
        and a ``$120.00`` one settles 2026-02-20.  Hand-computed: the walk
        yields exactly two sources, ``-$500.00`` on 02-05 and ``-$120.00`` on
        02-20, each at its OWN day.  A leaf that filtered its inputs, re-keyed
        one onto the opening, folded the two together or emitted a third would
        fail on the exact pair rather than on a predicate that is true of any
        list.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        # The books open 2026-01-31, which this case asserts below and which
        # the sources are then dated after -- so the day is named rather than
        # inherited (plan step X-f3c-2c).
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        create_settled_cash_transaction(
            seed_user, period, Decimal("500.00"),
            settled_on=date(2026, 2, 5), name="after-the-books",
        )
        create_settled_cash_transaction(
            seed_user, period, Decimal("120.00"),
            settled_on=date(2026, 2, 20), name="later-still",
        )
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)
        assert walk.opening.opened_on == date(2026, 1, 31)
        assert sorted(
            (fact.settled_on, fact.delta) for fact in _money_facts(walk.source_facts)
        ) == [
            (date(2026, 2, 5), Decimal("-500.00")),
            (date(2026, 2, 20), Decimal("-120.00")),
        ]
