"""Every account's anchor ledger reconciles absolutely: per account, with a transfer's source on
top, through each assertion day, and after a revert that follows a true-up.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.services import account_posting_service, posting_service, status_seam
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    ledger_account_of_kind,
    ledger_net,
    linked_ledger_account,
    observed_day_of,
)

from ._helpers import (
    _independent_linked_ledger_sum,
    _latest_assertion,
    _independent_post_assertion_source_effect,
    _assert_account_anchors_reconcile,
    _origin_instant,
    _assert_balance_at,
    _true_up_at,
    _settle_expense,
)


def _linked_ledger_sum_as_of(
    account_id: int, scenario_id: int, civil_date,
) -> Decimal:
    """Sum a LINKED ledger's legs on entries dated at or before *civil_date*.

    The "ledger through an assertion day" reader: every linked-ledger entry --
    a source OR an anchor correction -- carries an ``entry_date`` equal to the
    civil day its money moved (a source entry is dated by its movement's own
    STORED ``settled_on``, through ``_posting_purchases.emit_purchase_deltas``
    since plan steps ``balance:X-bi-4a`` and ``balance:X-bi-6-3``;
    corrections take the anchor's observed day), so
    summing legs with ``entry_date <= civil_date`` reconstructs the ledger as of
    the END of that civil day -- the ``TestLedgerThroughEachAssertionDay``
    invariant.  The fixture places every event on a DISTINCT, increasing civil
    day so each as-of names exactly one state.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == account_id,
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.entry_date <= civil_date,
        )
        .scalar()
    )


# ---------------------------------------------------------------------------
# 1. The absolute invariant: civil-day partition (CRITICAL-1)
# ---------------------------------------------------------------------------


class TestAbsoluteInvariantPerAccount:
    """A multi-anchor account reconciles to latest anchor + post-assertion sources."""

    @pytest.mark.server_clock
    def test_critical1_absorb_and_ride_reconciles_three_ways(
        self, app, db, seed_user,
    ):
        """Anchor 500, spend 200 pre-true-up, true-up 350, spend 100 post: 250.00.

        The CRITICAL-1 fixture on a Savings anchored $500.00 (origination
        instant T):

          - $200.00 expense paid T+1h  -- BEFORE the true-up (absorbed)
          - true-up asserting $350.00 at T+2h
          - $100.00 expense paid T+3h  -- AFTER the true-up (rides on top)

        The engine's pre-true-up answer was 500 - 200 = 300, so the true-up
        delta is 350 - 300 = +50.00; the post-true-up spend is the only source
        attributed after the latest assertion.  So:

          linked ledger = 500 (opening) + 50 (true-up) - 200 - 100 = 250.00
                        = latest anchor 350 + post-assertion effect (-100.00)

        A period-granular reading would have absorbed BOTH settles (all three
        events share one period) and mis-stated the true-up.  All three
        independent computations agree, and the production-wide sweep ties.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Critical1 Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            origin = _origin_instant(savings)

            _settle_expense(
                seed_user, savings, "200.00",
                observed_day_of(origin + timedelta(days=1)),
            )
            _true_up_at(savings, "350.00", origin + timedelta(days=2))
            _settle_expense(
                seed_user, savings, "100.00",
                observed_day_of(origin + timedelta(days=3)),
            )
            db.session.commit()

            latest_asserted_day, latest_anchor = _latest_assertion(savings.id)
            assert latest_anchor == Decimal("350.00")

            # (a) hand-computed literal == independent ledger-table query.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            # (b) independent source query == the hand-computed post-assertion
            #     effect (only the T+3h spend rides on top).
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("-100.00")
            # (c) the production service helpers agree too.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            assert posting_service.posted_purchase_effect(
                savings.id, scenario_id,
            ) == Decimal("-300.00")  # both spends' movements, the ledger-native cash effect

            # The equity twin nets -(opening 500 + true-up 50) = -550.00.
            equity = ledger_account_of_kind(
                db.session, savings.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                db.session, equity.id, scenario_id,
            ) == Decimal("-550.00")

            _assert_account_anchors_reconcile(scenario_id)

    @pytest.mark.server_clock
    def test_source_at_exact_assertion_instant_is_absorbed(
        self, app, db, seed_user,
    ):
        """A source dated the assertion's OWN civil day is absorbed (<= tie).

        The exact boundary the walk's inclusive ``<=`` partition and the
        oracle's strict ``>`` post-assertion filter meet: Savings anchored
        $500.00; a $75.00 expense settled on the true-up's own business day
        (T2's); the true-up asserts $425.00.  The spend is absorbed
        into the anchor (its day is not strictly after T2's): ledger_before =
        500 - 75 = 425, so the true-up delta is 0 and books NOTHING, and the
        spend does not ride on top.

          linked = 500 (opening) - 75 = 425.00 = latest anchor 425 + post (0)

        A strict-``<`` walk would leave the spend outside the anchor, book a
        spurious -75.00 true-up, and mis-state the ledger at 350 -- so this pins
        the exact absorb/ride boundary the module advertises, which no other
        fixture (all sources strictly before or after their assertions) touches.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Tie Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            instant = _origin_instant(savings) + timedelta(hours=2)

            _settle_expense(
                seed_user, savings, "75.00", observed_day_of(instant),
            )
            _true_up_at(savings, "425.00", instant)
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("425.00")
            # The tie source is NOT strictly after the assertion, so nothing
            # rides on top (a strict-< walk would report -75.00 here).
            latest_asserted_day, latest_anchor = _latest_assertion(savings.id)
            assert latest_anchor == Decimal("425.00")
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("0.00")
            # The true-up delta was zero (the tie was absorbed): no true-up entry
            # -- a strict-< walk would have booked one.
            linked = linked_ledger_account(db.session, savings.id)
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert (
                db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == trueup_source,
                )
                .distinct()
                .count()
            ) == 0
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 1b. A transfer source rides on top and reconciles on both endpoints
# ---------------------------------------------------------------------------


class TestTransferSourceRidesOnTop:
    """A settled transfer reconciles on both linked ledgers by its sides' effect."""

    @pytest.mark.server_clock
    def test_transfer_into_savings_reconciles_both_accounts(
        self, app, db, seed_user,
    ):
        """A $150 Checking -> Savings transfer rides on both post-assertion anchors.

        The seeded Checking ($1000.00 opening) transfers $150.00 into a Savings
        anchored $200.00, settled at server-now (after both origination
        assertions, so it rides on top of each).  The transfer's settled
        figure, 150.00, lands +150.00 on Savings by its to-side record and
        -150.00 on Checking by its from-side record (the records hang off the
        transfer, not its shadows, since plan step balance:X-bi-6-4d-2, and
        the figure is stored once on the transfer since its 4d-4 half; ruling
        R-BAL167 class 4):

          Savings  = 200 (opening) + 150 = 350.00 = anchor 200 + post (+150.00)
          Checking = 1000 (opening) - 150 = 850.00 = anchor 1000 + post (-150.00)

        This exercises the transfer branch of the oracle's independent
        source-effect helper on BOTH side polarities, so the sweep's
        second-opinion computation is validated for transfers, not only cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Transfer Savings",
                anchor_balance=Decimal("200.00"),
            )
            db.session.commit()

            create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("850.00")

            # The independent transfer-branch effect signs each side correctly.
            savings_asserted_at, _sa = _latest_assertion(savings.id)
            checking_asserted_at, _ca = _latest_assertion(checking.id)
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, savings_asserted_at,
            ) == Decimal("150.00")
            assert _independent_post_assertion_source_effect(
                checking.id, scenario_id, checking_asserted_at,
            ) == Decimal("-150.00")
            assert posting_service.settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-150.00")

            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 2. Ledger through each assertion instant == the asserted balance
# ---------------------------------------------------------------------------


class TestLedgerThroughEachAssertionDay:
    """At every historical assertion instant the ledger equalled that anchor."""

    @pytest.mark.server_clock
    def test_as_of_each_assertion_lands_on_the_asserted_balance(
        self, app, db, seed_user,
    ):
        """Distinct-day fixture: the as-of ledger lands on each anchor exactly.

        Every event on its OWN increasing civil day (whole-day offsets from the
        origination instant T), so "ledger as of the end of day D" equals
        "ledger as of the assertion instant on day D".  Savings anchored
        $500.00 (opening on day D0):

          - $200.00 expense paid T+5d   (day D0+5)
          - true-up asserting $350.00   at T+10d (day D0+10)
          - $100.00 expense paid T+15d  (day D0+15)

        Walking the as-of ledger forward:

          as of D0    : 500.00                      == opening anchor
          as of D0+5  : 500 - 200 = 300.00          (spend rode the engine value)
          as of D0+10 : 500 - 200 + 50 = 350.00     == true-up anchor
          as of D0+15 : 350 - 100 = 250.00          == latest anchor + post

        The two anchor days (D0, D0+10) land exactly on the asserted balances --
        the ledger-through-each-assertion invariant.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "AsOf Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            origin = _origin_instant(savings)
            day0 = observed_day_of(origin)

            _settle_expense(
                seed_user, savings, "200.00",
                observed_day_of(origin + timedelta(days=5)),
            )
            _assert_balance_at(savings, "350.00", origin + timedelta(days=10))
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                savings.id,
            )
            _settle_expense(
                seed_user, savings, "100.00",
                observed_day_of(origin + timedelta(days=15)),
            )
            db.session.commit()

            # Each as-of civil day reconstructs the ledger through that instant.
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0,
            ) == Decimal("500.00")                      # opening anchor
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0 + timedelta(days=5),
            ) == Decimal("300.00")                      # pre-true-up engine value
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0 + timedelta(days=10),
            ) == Decimal("350.00")                      # true-up anchor
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0 + timedelta(days=15),
            ) == Decimal("250.00")                      # latest anchor + post

            # The final ABSOLUTE total matches, and the sweep ties.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 3. Revert after a true-up self-heals to the engine's answer
# ---------------------------------------------------------------------------


class TestRevertAfterTrueupSelfHeals:
    """Reverting a pre-true-up settle re-bases the true-up and stays reconciled."""

    @pytest.mark.server_clock
    def test_revert_pre_trueup_settle_reconciles(self, app, db, seed_user):
        """The CRITICAL-1 fixture, then revert the pre-true-up spend; sweep ties.

        Continuing from anchor 500 / spend 200 (T+1h) / true-up 350 (T+2h) /
        spend 100 (T+3h) -- linked ledger 250.00 -- the $200.00 pre-true-up
        expense is reverted through the real ``sync_transaction_postings``
        (``settled=False``) path.  The true-up's walked ``ledger_before`` moves
        300 -> 500, so its delta moves +50 -> -150; the effect-time self-heal
        appends the balancing -200.00 delta on the same key.  The reverted
        source drops from the transaction truth too:

          linked = 500 (opening) - 150 (healed true-up) - 100 = 250.00
                 = latest anchor 350 + post-assertion (-100.00)

        The account total never left the anchor + post-assertion balance.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Revert Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            origin = _origin_instant(savings)

            spend = _settle_expense(
                seed_user, savings, "200.00",
                observed_day_of(origin + timedelta(days=1)),
            )
            _true_up_at(savings, "350.00", origin + timedelta(days=2))
            _settle_expense(
                seed_user, savings, "100.00",
                observed_day_of(origin + timedelta(days=3)),
            )
            db.session.commit()
            _assert_account_anchors_reconcile(scenario_id)

            # Revert the pre-true-up spend through the ONE status door, then
            # reconcile via the real posting primitive; the tail self-heal
            # re-bases the true-up in the same transaction.
            #
            # **The revert goes through the seam since plan step X-bi-3a.**
            # This case drove the posting primitive alone with
            # ``settled=False`` on a row the database still held as Paid, and
            # the flag was enough while the money sat on the row's own leg.
            # A settle now writes a covering MOVEMENT that carries the money
            # (ruling R-BAL39), and a posted purchase is cash that left the
            # bank whatever its parent's status says (ruling R-FM) -- so only
            # the act that releases the movement, the seam's revert, takes the
            # $200.00 back out.  Every figure below is unchanged.
            status_seam.apply_status_change(
                spend, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            posting_service.sync_transaction_postings(spend)
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            # The self-heal re-based the true-up: its walked ledger_before moved
            # 300 -> 500 (the $200 no longer absorbed), so the true-up key's
            # linked legs now net -150.00 -- the original +50.00 plus the
            # appended -200.00 balancing delta.  A stale correction left at +50
            # would read the account at 450.00; this pins the re-derivation
            # itself, not merely the total it happens to reach.
            linked = linked_ledger_account(db.session, savings.id)
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            trueup_linked_net = (
                db.session.query(
                    _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == trueup_source,
                )
                .scalar()
            )
            assert trueup_linked_net == Decimal("-150.00")
            _assert_account_anchors_reconcile(scenario_id)
