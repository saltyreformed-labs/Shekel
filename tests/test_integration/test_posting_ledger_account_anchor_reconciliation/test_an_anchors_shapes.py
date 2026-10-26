"""The shapes an anchor takes: pre-anchor absorption, a same-day merge, a negatively anchored
liability, a zero delta, and a back-dated true-up that re-bases the ledger.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.services import account_posting_service, anchor_service, cash_ledger, posting_service
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.dates import display_today
from tests._test_helpers import (
    create_account_of_type,
    create_account_via_service,
    ledger_account_of_kind,
    ledger_net,
    linked_ledger_account,
    observed_day_of,
)

from ._helpers import (
    _independent_linked_ledger_sum,
    _latest_assertion,
    _independent_post_assertion_source_effect,
    _trial_balance,
    _entries_violating_balance,
    _assert_account_anchors_reconcile,
    _origin_instant,
    _assert_balance_at,
    _settle_expense,
)


# ---------------------------------------------------------------------------
# 4. Pre-anchor absorption (a settle before the opening is inside it)
# ---------------------------------------------------------------------------


class TestPreAnchorAbsorption:
    """A settle attributed before the opening is absorbed into the opening delta."""

    def test_a_spend_dated_before_the_opening_is_absorbed_into_it(
        self, app, db, seed_user,
    ):
        """A 2024-dated spend is inside the 2026 opening.

        A $200.00 expense settled on the 2024 bootstrap period's start day --
        BEFORE the account's origination assertion (server-now, 2026).  The C6
        self-heal at the settle re-derives the opening: its delta becomes
        500 - (-200) = +700.00, so the linked ledger stays exactly on the 500.00
        anchor and NO source rides on top.

          linked = 700 (opening) - 200 = 500.00 = latest anchor 500 + 0

        **The row reached that day through a FALLBACK until plan step X-f1** --
        it carried no ``paid_at`` and every reader substituted its pay period's
        ``start_date`` -- and this test was named for the fallback.  The day is
        a stored fact now and the substitution is gone, so the fixture states
        the day it always meant.  Every figure is unchanged, because the day is.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "PreAnchor Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()

            _settle_expense(
                seed_user, savings, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("500.00")
            # The OPENING books the account's opening equity ($500.00, the
            # stored fact) and the $200.00 the records moved books separately
            # as an ``account_trueup`` against the origination assertion.  The
            # account's TOTAL is unchanged at $500.00, which is the property
            # this test is about.
            #
            # *One $700.00 ``account_opening`` entry until plan step X-f3c-2a,
            # which conflated capital brought on with a correction to it -- and
            # that conflation is why a BACK-DATED assertion could re-elect the
            # whole figure.*
            linked = linked_ledger_account(db.session, savings.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            opening_net = (
                db.session.query(
                    _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .scalar()
            )
            assert opening_net == Decimal("500.00")
            # Nothing rides on top: the spend is pre-assertion.
            latest_asserted_day, _latest = _latest_assertion(savings.id)
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("0.00")
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 5. Same-day true-ups merge to one correction landing on the later value
# ---------------------------------------------------------------------------


class TestSameDayAnchorMerge:
    """Two same-UTC-day true-ups merge to one entry on the later value."""

    @pytest.mark.server_clock
    def test_two_same_day_trueups_reconcile(self, app, db, seed_user):
        """$600 then $550 on one future UTC day merge to a single +50.00 entry.

        Both assertions sit on one future UTC day (06:00 and 07:00, no midnight
        crossing) on a $500.00-anchored Savings, with no settled sources.  Their
        deltas +100.00 and -50.00 share the (true-up, day) reconcile key and
        merge to ONE +50.00 entry; the ledger lands on the LATER value 550.00
        (== the latest anchor, no post-assertion sources).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Merge Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            day = (_origin_instant(savings) + timedelta(days=30)).replace(
                hour=6, minute=0, second=0, microsecond=0,
            )

            _assert_balance_at(savings, "600.00", day)
            _assert_balance_at(savings, "550.00", day + timedelta(hours=1))
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                savings.id,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("550.00")
            # Exactly ONE true-up entry survives the same-day merge.
            linked = linked_ledger_account(db.session, savings.id)
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            trueup_entries = (
                db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == trueup_source,
                )
                .distinct()
                .all()
            )
            assert len(trueup_entries) == 1
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 6. A negatively-anchored (owed-as-negative) non-loan liability
# ---------------------------------------------------------------------------


class TestNegativelyAnchoredLiability:
    """A non-loan liability anchor posts ledger-native, with no sign branch."""

    @pytest.mark.server_clock
    def test_credit_card_negative_anchor_reconciles(self, app, db, seed_user):
        """A Credit Card anchored -500.00, then a $120 charge, reconciles absolutely.

        The owed-as-negative convention: the opening books linked -500.00 /
        equity +500.00 (no ``-abs`` normalization, exactly like the engine).  A
        $120.00 expense charged to the card (post-assertion) rides on top:

          linked = -500 (opening) - 120 = -620.00
                 = latest anchor -500 + post-assertion (-120.00)
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            # The civil day an hour after the origination instant -- verbatim
            # what the deleted derivation computed for the instant this fixture
            # used to pass, so the figures below are unchanged (plan step X-f1).
            _settle_expense(
                seed_user, card, "120.00",
                observed_day_of(_origin_instant(card) + timedelta(hours=1)),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                card.id, scenario_id,
            ) == Decimal("-620.00")
            equity = ledger_account_of_kind(
                db.session, card.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                db.session, equity.id, scenario_id,
            ) == Decimal("500.00")  # ledger-native: +500 against the -500 anchor
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 7. A $0-anchor account books nothing and stays out of the ledger
# ---------------------------------------------------------------------------


class TestZeroDeltaBooksNothing:
    """A $0-anchor account mints no correction and no equity twin, yet reconciles."""

    def test_zero_anchor_books_nothing_and_reconciles(
        self, app, db, seed_user,
    ):
        """A fresh $0.00 Savings posts no entry, no twin, and the sweep still ties.

        The opening delta is $0, so the reconcile books nothing: no correction
        entry and no ``anchor_equity`` ledger row (the account stays
        hard-deletable, Guard 5 never engaging).  The absolute invariant is
        0.00 == latest anchor 0.00 + 0, so the production-wide sweep reconciles
        it alongside the seeded Checking.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            zero = create_account_of_type(
                seed_user, db.session, "Savings", "Zero Savings",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                zero.id, scenario_id,
            ) == Decimal("0.00")
            assert ledger_account_of_kind(
                db.session, zero.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is None
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 7b. A BACK-DATED true-up, through the real write door (plan step X-f1c4c)
# ---------------------------------------------------------------------------


class TestBackDatedTrueUpReBasesTheLedger:
    """A user-supplied ``observed_on`` inserts an assertion MID-HISTORY.

    Plan step X-f1c4c made that reachable for the first time: every cash true-up
    stamped ``display_today()`` before it, so an assertion could only ever be
    appended at or after every existing one.  Inserting one BETWEEN two others
    is therefore a new input shape for
    ``account_posting_service.sync_account_anchor_postings_all_scenarios`` --
    an already-posted correction has to be ADJUSTED rather than posted fresh,
    and the entry's source kind can flip.

    **Three independent reviews of that step all found the posted ledger
    ungraded for it**, and one demonstrated the gap by removing the re-sync
    entirely: all eleven of the step's own new tests still passed, because
    ``cash_balance_at`` does not read ``account_postings``.  These two cases
    close that hole, using this file's independent oracles rather than the
    service's own helpers.

    Both go through ``anchor_service.apply_anchor_true_up`` -- the production
    door, with its lock, its duplicate rule and its re-sync -- not the
    deterministic ``_assert_balance_at`` stand-in the older classes use, because
    the door is what the step changed.
    """

    def test_a_mid_history_back_date_re_bases_and_reconciles(
        self, app, db, seed_user,
    ):
        """An assertion inserted between two others leaves the ledger exact.

        Opening $1,000.00 at O, a settled $200.00 expense at O+5, a true-up to
        $900.00 at O+20, then a BACK-DATED $700.00 asserted for O+10 -- which
        lands after the expense and before the later true-up, so it must
        re-partition which correction absorbs that expense and adjust the later
        correction rather than double-count it.

        Hand-computed: the walk absorbs the -$200.00 into the O+10 assertion
        (running 1000 - 200 = 800, asserted 700, delta -100), and the O+20
        correction re-bases from 700 to 900 (delta +200).  The linked ledger
        must therefore land on the LATEST anchor, $900.00, with no
        post-assertion sources.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            opening_day = display_today() - timedelta(days=40)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Back-dated Savings",
                anchor_balance=Decimal("1000.00"), observed_on=opening_day,
            )
            db.session.commit()

            _settle_expense(
                seed_user, savings, "200.00",
                opening_day + timedelta(days=5),
            )
            db.session.commit()

            anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("900.00"),
                observed_on=opening_day + timedelta(days=20),
            )
            # THE BACK-DATE: strictly between the expense and the later true-up.
            outcome = anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("700.00"),
                observed_on=opening_day + timedelta(days=10),
            ).outcome

            assert outcome is AnchorTrueUpOutcome.COMMITTED
            # The ledger lands on the LATEST anchor, read through the
            # independent join rather than the service's own total.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("900.00")
            # ...and through the seam's own reader, which resolves the ledger
            # row first, so a shared lookup bug cannot satisfy both.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("900.00")
            # Ledger-wide self-checks: nothing was double-posted or stranded.
            assert _trial_balance() == Decimal("0.00")
            assert _entries_violating_balance() == []
            _assert_account_anchors_reconcile(scenario_id)

    def test_back_dating_below_the_opening_re_designates_it_and_reconciles(
        self, app, db, seed_user,
    ):
        """An assertion before the opening BECOMES the opening, and ties exactly.

        **Developer ruling 2026-08-04**, taken at this step's adversarial
        review: a LOAN's origination is a contractual fact and its door refuses
        below it (``routes/loan/params.true_up_balance``), but a CASH account's
        opening is merely its earliest record -- so learning an earlier balance
        legitimately makes that the opening, and the cash door does NOT inherit
        the loan's third bound.  The reviewer who found this reproduced the
        re-designation and asked for it to be blocked; it was ruled ALLOWED and
        graded instead, which is what this case is.

        **What that ruling MEANT changed at plan step X-f3c-2a, and the money
        did not.**  ``cash_anchor_facts`` still marks the earliest row
        ``is_opening``, so the re-designation the 2026-08-04 ruling allows is
        still visible on the FACTS -- and it no longer decides a posting.  Which
        correction books ``account_opening`` is now read from the stored
        ``budget.account_openings`` row (**R-GX**, **R-HE**), so the opening
        entry stays where the books actually opened and the back-dated
        assertion books an ordinary ``account_trueup`` on its own day.

        *Until then the flag chose the posting: back-dating below the
        origination REVERSED the original opening entry and re-posted it as a
        true-up, moving the account's opening equity onto a day the owner had
        merely typed a balance for.  An owner who genuinely means "my books
        opened earlier, at $250" restates the opening record -- X-f3c-2b's
        door -- rather than having a true-up re-designate it implicitly.*

        The money must not move either way: the linked ledger still lands on
        the latest anchor, which is what the totals below grade.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            opening_day = display_today() - timedelta(days=20)
            # Built through the PRIMITIVE, so the books stay exactly where
            # ``create_account`` put them (plan step X-f3c-2b): this case
            # asserts WHICH DAY the ``account_opening`` entry is filed on, and
            # the shared factory opens the books earlier -- right for a
            # fixture that records movements, wrong for one whose subject is
            # the day.
            savings = create_account_via_service(
                seed_user, db.session, "Savings", "Pre-opening Savings",
                anchor_balance=Decimal("1000.00"), observed_on=opening_day,
            )
            db.session.commit()

            facts_before = cash_ledger.cash_anchor_facts(savings.id)
            assert [(f.observed_on, f.is_opening) for f in facts_before] == [
                (opening_day, True),
            ], "precondition: exactly one assertion, and it is the opening"

            earlier = opening_day - timedelta(days=10)
            anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("250.00"),
                observed_on=earlier,
            )

            # The flag MOVED -- this is the behaviour the ruling allows, and it
            # is asserted rather than merely tolerated.
            facts_after = cash_ledger.cash_anchor_facts(savings.id)
            assert [(f.observed_on, f.is_opening) for f in facts_after] == [
                (earlier, True), (opening_day, False),
            ], "the earlier assertion must become the account's opening"

            # The ledger is unmoved by the re-designation: still the latest
            # anchor, still balanced, still reconciling across the scenario.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("1000.00")
            assert _trial_balance() == Decimal("0.00")
            assert _entries_violating_balance() == []
            _assert_account_anchors_reconcile(scenario_id)

            # The source KIND flipped with the flag, and it is graded by the NET
            # opening-sourced amount per day rather than by which days carry an
            # opening entry at all.  That distinction is the finding: the
            # reconcile does not retag a row in place, it REVERSES the old
            # opening entry -- and a reversal keeps the source kind it reverses,
            # so the old day still has opening-kind entries and only their SUM
            # says they cancelled.  A first version of this assertion looked at
            # the days alone and reported both, which grades nothing.
            linked = linked_ledger_account(db.session, savings.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            opening_by_day = dict(
                db.session.query(
                    JournalEntry.entry_date,
                    db.func.coalesce(db.func.sum(Posting.amount), Decimal("0")),
                )
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .group_by(JournalEntry.entry_date)
                .all()
            )
            # The OPENING stays where the books opened, carrying the stored
            # opening equity ($1,000.00); the back-dated day gets no opening
            # entry at all.  Nothing is reversed, because nothing was
            # re-designated.
            assert opening_by_day.get(opening_day) == Decimal("1000.00")
            assert opening_by_day.get(earlier) is None
            # The two assertions book TRUE-UPS that net to zero: the back-dated
            # $250.00 corrects the books DOWN by $750.00 and the original
            # $1,000.00 assertion corrects them back UP by $750.00.  Their sum
            # is what keeps the account on $1,000.00.
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
            ).join(
                JournalEntry, Posting.journal_entry_id == JournalEntry.id,
            ).filter(
                Posting.ledger_account_id == linked.id,
                JournalEntry.scenario_id == scenario_id,
                JournalEntry.source_kind_id == trueup_source,
            ).scalar() == Decimal("0.00")
