"""An account's corrections on the worked-out read: the lines they book, and what moves them."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.scenario import Scenario
from app.services import ledger_lines, status_seam, transaction_service
from app.services.ledger_lines import account_counter_key
from app.services.settle_day import record_settle_day
from app.utils.dates import display_today, to_display_date
from tests._ledger_reads import (
    account_net,
    ledger_net,
    linked_key_of,
    scenario_lines,
    source_lines,
    source_net,
)
from tests._test_helpers import (
    an_entered_day,
    create_loan_account,
    create_settled_cash_transaction,
    settle_instant_on,
)

from ._helpers import (
    _ONE_HOUR,
    _add_assertion,
    _correction_net_in_period,
    _linked_source_net,
    _make_account,
    _make_account_as_created,
    _origin_day,
    _period_containing,
    _settle_expense,
)


def _equity_key(account):
    """Return the key of *account*'s anchor-equity counter."""
    return account_counter_key(LedgerAccountKindEnum.ANCHOR_EQUITY, account.id)


class TestTheCorrectionsBookTheAssertedBalance:
    """The corrections land the account's own line on each asserted balance."""

    def test_the_opening_books_the_anchor_against_equity(self, app, db, seed_user):
        """The opening books own +anchor / equity -anchor, on its day, in its paycheck.

        Savings anchored $500.00: ONE ``account_opening`` line on each side --
        ``+500.00`` on the account's own line and ``-500.00`` on its
        anchor-equity counter, both of kind ``opening`` -- dated the day the
        assertion is the CLOSING BALANCE for (its stored ``observed_on``, never
        re-derived from ``created_at``: ruling R-DH, plan step 2) and filed in
        the paycheck that day files in.  The two days are independent since the
        column shipped: this account's opening is observed YESTERDAY while its
        row was recorded today.  The agreeing assertion books no true-up.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account_as_created(seed_user, "500.00")
            history_row = (
                _db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .one()
            )
            assert history_row.observed_on != to_display_date(
                history_row.created_at,
            )
            opening_kind = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
            filed_in = _period_containing(account.user_id, history_row.observed_on)

            own = linked_key_of(_db.session, account.id)
            for key, amount in (
                (own, Decimal("500.00")),
                (_equity_key(account), Decimal("-500.00")),
            ):
                assert [
                    (line.amount, line.posting_kind_id, line.entry_date,
                     line.pay_period_id)
                    for line in source_lines(
                        _db.session, key, scenario_id,
                        PostingSourceEnum.ACCOUNT_OPENING,
                    )
                ] == [(amount, opening_kind, history_row.observed_on, filed_in)]
            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("500.00")
            assert source_lines(
                _db.session, own, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == []

    def test_zero_anchor_books_nothing(self, app, db, seed_user):
        """A $0.00 opening books no line, on the account's own line or on its equity."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "0.00")

            assert source_lines(
                _db.session, linked_key_of(_db.session, account.id), scenario_id,
                PostingSourceEnum.ACCOUNT_OPENING,
            ) == []
            assert [
                line for line in scenario_lines(_db.session, scenario_id)
                if line.key == _equity_key(account)
            ] == []

    def test_trueup_books_its_delta_onto_the_absolute_balance(
        self, app, db, seed_user,
    ):
        """The CRITICAL-1 fixture lands on the absolute balance.

        From the walk fixture (anchor 500, spend 200 on T+1, true-up 350 on
        T+2, spend 100 on T+3 -- DAY offsets since ruling R-DH, see
        ``test_trueup_day_partition`` for why hours no longer separate them):

          opening +500.00, true-up +50.00, sources -200.00 - 100.00
          => own line 250.00 == latest anchor 350 + post-assertion -100
          equity nets -(500 + 50) = -550.00, its true-up line -50.00
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            _add_assertion(account, "350.00", settle_instant_on(origin + timedelta(days=2)))
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(days=3),
            )
            _db.session.commit()

            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("250.00")
            assert ledger_net(
                _db.session, _equity_key(account), scenario_id,
            ) == Decimal("-550.00")
            assert _linked_source_net(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == Decimal("50.00")
            assert source_net(
                _db.session, _equity_key(account), scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == Decimal("-50.00")

    def test_a_pre_trueup_revert_re_bases_the_trueup(
        self, app, db, seed_user,
    ):
        """Reverting a pre-true-up settle re-bases the true-up.

        Continuing the CRITICAL-1 fixture: the $200.00 pre-true-up expense
        is reverted, so the walk's true-up ledger_before moves 300 -> 500
        and its delta +50 -> -150:

          own line: 500 (opening) - 150 (true-up) - 100 (post settle)
                    = 250.00 == anchor 350 + post-assertion -100

        It was ``test_trueup_self_heals_when_pre_assertion_source_changes``,
        the stored ledger's re-sync appending the balancing delta, until plan
        step plan#2914 deleted the store; the read has nothing to heal.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            _add_assertion(account, "350.00", settle_instant_on(origin + timedelta(days=2)))
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(days=3),
            )
            _db.session.commit()
            assert _linked_source_net(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == Decimal("50.00"), "CONTROL: the true-up before the revert"

            # The revert goes through the ONE status door (plan step X-bi-3a):
            # the settle wrote a covering movement that carries the money, and
            # only the seam's revert releases it.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()

            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("250.00")
            assert _linked_source_net(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == Decimal("-150.00")

    def test_a_trueup_the_records_come_to_match_books_nothing(
        self, app, db, seed_user,
    ):
        """A true-up whose walked delta becomes zero books no line.

        Anchor 500; a $200.00 expense dated T+1d; true-up 350.00 on T+2d
        books +50.00 (ledger_before 300).  The user then reverts the $200
        and instead settles $150.00, also dated T+1d (still strictly before
        the true-up's day, which is what "pre-true-up" means now that the
        partition reads civil days rather than instants):

          ledger_before 500 - 150 = 350 -- delta 0, so no true-up line
          own line: 500 (opening) + 0 (true-up) - 150 = 350.00 == anchor.

        It was ``test_trueup_retired_by_matching_balance_reverses_to_zero``,
        the stored ledger reversing its posted true-up through the key's empty
        target, until plan step plan#2914 deleted the store.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            _add_assertion(
                account, "350.00", settle_instant_on(origin + timedelta(days=2)),
            )
            _db.session.commit()
            assert _linked_source_net(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == Decimal("50.00"), "CONTROL: the true-up before the revert"

            # The revert goes through the ONE status door (plan step X-bi-3a).
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _settle_expense(
                seed_user, account, "150.00",
                origin + timedelta(days=1),
            )
            _db.session.commit()

            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("350.00")
            assert source_lines(
                _db.session, linked_key_of(_db.session, account.id), scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == []

    def test_same_day_trueups_book_one_line_landing_later(
        self, app, db, seed_user,
    ):
        """Two same-day true-ups book ONE line, landing on the LATER value.

        Both assertions sit on ONE future civil day, an hour apart: $600.00
        then $550.00 on a $500.00-anchored account.  Their deltas +100.00 and
        -50.00 share the (trueup, paycheck, day) key and merge to ONE +50.00
        line; the account lands on 550.00.

        Noon UTC plus an hour is provably one ``America/New_York`` day (08:00
        and 09:00 EDT), which is the property the merge turns on -- the pair
        must not straddle midnight in the zone the partition reads.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            day = settle_instant_on(_origin_day(account) + timedelta(days=30))
            _add_assertion(account, "600.00", day)
            _add_assertion(account, "550.00", day + _ONE_HOUR)
            # The precondition the merge rests on: ONE civil day for both.
            assert to_display_date(day) == to_display_date(day + _ONE_HOUR)
            _db.session.commit()

            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("550.00")
            assert [
                line.amount for line in source_lines(
                    _db.session, linked_key_of(_db.session, account.id),
                    scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
                )
            ] == [Decimal("50.00")]

    def test_liability_anchor_keeps_ledger_native_sign(
        self, app, db, seed_user,
    ):
        """An owed-as-negative liability anchor books with no sign branch.

        A Credit Card anchored -500.00 (the owed-as-negative convention):
        the opening books own -500.00 / equity +500.00 -- the ledger
        presents the anchor faithfully, exactly like the engine (no class
        branch, no ``-abs`` normalization).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(
                seed_user, "-500.00", type_name="Credit Card", name="CC",
            )

            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("-500.00")
            assert ledger_net(
                _db.session, _equity_key(account), scenario_id,
            ) == Decimal("500.00")


class TestASettleDayMoveAcrossAnAssertion:
    """Finding **N-310** (plan#1310): what a settled row's day move owes the corrections.

    The review that raised it could not say whether moving a settled row's day
    re-derives the anchor corrections.  Since plan task plan#2525 the walk
    reads the RECORDS, so what the corrections owe a moved day is what the
    records now say -- by construction, which the walk's half of the case
    shows; the read's half is what the worked-out ledger files in the
    true-up's paycheck and the account's net beside it.
    """

    def test_a_moved_day_moves_the_crossed_correction_only_both_ways(
        self, app, db, seed_user,
    ):
        """A $150.00 settle moved past a true-up, then back, through the door.

        Savings opened at $500.00 on day T-10 (its origination assertion), a
        TRUE-UP asserting $350.00 on T-5, and a $150.00 expense settled on T-7,
        between them.  Hand-computed, with T the owner's today:

            settled T-7   origination ledger_before 500.00, delta   0.00
                          true-up     ledger_before 500.00 - 150.00 = 350.00,
                                      delta 0.00
                          own line 500.00 - 150.00 = 350.00
            moved to T-3  origination ledger_before 500.00, delta   0.00
                          true-up     ledger_before 500.00, delta -150.00
                          (the expense now rides on top of it)
                          own line 500.00 - 150.00 - 150.00 = 200.00
            back to T-7   as the first state

        The origination's figure is the control: a move that crosses only the
        true-up must leave every other correction where it was.  The return
        trip is the second time the read is asked, after it has booked a
        correction it must now drop.
        """
        with app.app_context():
            today = display_today()
            scenario_id = seed_user["scenario"].id
            account = _make_account(
                seed_user, "500.00", observed_on=today - timedelta(days=10),
            )
            txn = _settle_expense(
                seed_user, account, "150.00", today - timedelta(days=7),
            )
            trueup_day = today - timedelta(days=5)
            _add_assertion(account, "350.00", settle_instant_on(trueup_day))
            _db.session.commit()
            trueup_period = _period_containing(seed_user["user"].id, trueup_day)

            def read_both():
                """Return each assertion's ledger_before from the walk, and the read's state."""
                walked = ledger_lines.walk_account_ledger(
                    account.id, scenario_id,
                )
                return (
                    [correction.ledger_before for correction in walked[1:]],
                    _correction_net_in_period(
                        account.id, scenario_id,
                        PostingSourceEnum.ACCOUNT_TRUEUP, trueup_period,
                    ),
                    account_net(
                        _db.session, account.id, scenario_id,
                    ),
                )

            assert read_both() == (
                [Decimal("500.00"), Decimal("350.00")],
                Decimal("0.00"),
                Decimal("350.00"),
            ), "CONTROL: settled between the two assertions"

            for moved_to, expected in (
                (today - timedelta(days=3), (
                    [Decimal("500.00"), Decimal("500.00")],
                    Decimal("-150.00"),
                    Decimal("200.00"),
                )),
                (today - timedelta(days=7), (
                    [Decimal("500.00"), Decimal("350.00")],
                    Decimal("0.00"),
                    Decimal("350.00"),
                )),
            ):
                transaction_service.apply_requested_status(
                    txn, txn.status_id, settle_day=an_entered_day(moved_to),
                )
                _db.session.commit()
                movement, = txn.covering_movements
                assert movement.settled_on == moved_to, (
                    "PRECONDITION: the door moved the movement's day"
                )
                assert read_both() == expected, f"moved to {moved_to}"


class TestEachScenarioAndALoan:
    """The corrections book per scenario, and never on a loan."""

    def test_each_scenario_reads_its_own_sources(
        self, app, db, seed_user,
    ):
        """The opening books in every scenario; each scenario's net is its own sources.

        A $40.00 expense settled (post-assertion) in a NON-baseline scenario:
        the opening books in both (anchors are per-account), and each
        scenario's own line reflects its OWN sources -- baseline 500.00,
        what-if 500 - 40 = 460.00.
        """
        with app.app_context():
            baseline_id = seed_user["scenario"].id
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            _db.session.add(what_if)
            _db.session.flush()
            account = _make_account_as_created(seed_user, "500.00")
            origin = _origin_day(account)
            txn = create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), account=account, scenario=what_if,
            )
            record_settle_day(txn, an_entered_day(origin + timedelta(days=1)))
            _db.session.commit()

            assert account_net(
                _db.session, account.id, baseline_id,
            ) == Decimal("500.00")
            assert account_net(
                _db.session, account.id, what_if.id,
            ) == Decimal("460.00")
            for scenario_id in (baseline_id, what_if.id):
                assert _linked_source_net(
                    account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
                ) == Decimal("500.00")

    def test_a_loan_books_no_account_correction(self, app, db, seed_user):
        """A loan's anchors are the loan family's, never an account opening or true-up.

        The loan carries an ``AccountAnchorHistory`` row like every account,
        but its corrections belong to the loan family (``loan_opening`` /
        ``loan_trueup``); the walk refuses a loan
        (``TestWalkAccountLedger.test_walk_refuses_amortizing_loan``) and the
        read books none of the account kinds on it.  The loan's own opening is
        the control that the read did book the loan.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(seed_user, _db.session)
            _db.session.commit()
            own = linked_key_of(_db.session, loan.id)

            assert len(source_lines(
                _db.session, own, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) == 1, "CONTROL: the loan's own opening books on its line"
            for source in (
                PostingSourceEnum.ACCOUNT_OPENING,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            ):
                assert source_lines(
                    _db.session, own, scenario_id, source,
                ) == []

    def test_a_pre_assertion_settle_is_absorbed_by_the_origination(
        self, app, db, seed_user,
    ):
        """A settle before the origination assertion leaves the account on its anchor.

        A settle dated in the 2024 bootstrap period -- BEFORE the account's
        origination assertion -- is inside it, so the account's own line stays
        exactly on the 500.00 anchor.

        **The $700.00 is TWO lines, and the split is the improvement** (plan
        step X-f3c-2a).  The ``account_opening`` line books the account's
        opening EQUITY -- $500.00, the stored fact -- and the $200.00 the
        records moved books as an ``account_trueup`` against the origination
        assertion.  It was one $700.00 opening before, which conflated capital
        brought on with a correction to it and is why a BACK-DATED assertion
        could re-elect the whole figure.

        It was ``test_wired_self_heal_absorbs_pre_assertion_settle``, the
        settle's sync re-deriving the stored opening, until plan step plan#2914
        deleted the store.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            _settle_expense(
                seed_user, account, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            _db.session.commit()

            assert account_net(
                _db.session, account.id, scenario_id,
            ) == Decimal("500.00")
            assert _linked_source_net(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            ) == Decimal("500.00")
            assert _linked_source_net(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == Decimal("200.00")
