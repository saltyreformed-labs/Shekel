"""The shapes an anchor takes: pre-anchor absorption, a same-day merge, a negatively anchored
liability, a zero delta, and a back-dated true-up that re-bases the ledger.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.services import anchor_service, cash_ledger
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.dates import display_today
from app.services.ledger_lines import account_counter_key, linked_key
from tests._ledger_reads import (
    account_net,
    ledger_net,
    scenario_lines,
    source_lines,
    source_net,
    trial_balance,
)
from tests._test_helpers import (
    create_account_of_type,
    create_account_via_service,
    observed_day_of,
)

from ._helpers import (
    _latest_assertion,
    _independent_post_assertion_source_effect,
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
        BEFORE the account's origination assertion (server-now, 2026).  The
        origination assertion absorbs it: its corrections total
        500 - (-200) = +700.00, so the linked ledger stays exactly on the 500.00
        anchor and NO source rides on top.

          linked = 700 (opening + true-up) - 200 = 500.00 = latest anchor 500 + 0

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

            assert account_net(
                _db.session, savings.id, scenario_id,
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
            assert source_net(
                db.session, linked_key(savings), scenario_id,
                PostingSourceEnum.ACCOUNT_OPENING,
            ) == Decimal("500.00")
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
            db.session.commit()

            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("550.00")
            # Exactly ONE true-up line survives the same-day merge.
            assert len(source_lines(
                db.session, linked_key(savings), scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )) == 1
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

            assert account_net(
                _db.session, card.id, scenario_id,
            ) == Decimal("-620.00")
            assert ledger_net(
                db.session,
                account_counter_key(LedgerAccountKindEnum.ANCHOR_EQUITY, card.id),
                scenario_id,
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

        The opening delta is $0, so the read books nothing: no correction line
        and no ``anchor_equity`` line.  The absolute invariant is
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

            assert account_net(
                _db.session, zero.id, scenario_id,
            ) == Decimal("0.00")
            # 0.00 asserted - 0.00 booked = a $0.00 delta, and the read holds
            # no $0.00 line, so the equity twin's key holds none at all.
            equity_key = account_counter_key(
                LedgerAccountKindEnum.ANCHOR_EQUITY, zero.id,
            )
            assert [
                line for line in scenario_lines(db.session, scenario_id)
                if line.key == equity_key
            ] == []
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 7b. A BACK-DATED true-up, through the real write door (plan step X-f1c4c)
# ---------------------------------------------------------------------------


class TestBackDatedTrueUpReBasesTheLedger:
    """A user-supplied ``observed_on`` inserts an assertion MID-HISTORY.

    Plan step X-f1c4c made that reachable for the first time: every cash true-up
    stamped ``display_today()`` before it, so an assertion could only ever be
    appended at or after every existing one.  Inserting one BETWEEN two others
    is therefore a new input shape for the account's corrections
    (``ledger_lines``' account anchors): the correction after it re-bases, and
    a correction's source kind can flip.  (For the stored ledger's reconcile,
    ``account_posting_service``, it meant ADJUSTING an already-posted
    correction rather than posting one fresh, until plan task plan#3133.)

    **Three independent reviews of that step all found the posted ledger
    ungraded for it**, and one demonstrated the gap by removing the re-sync
    entirely: all eleven of the step's own new tests still passed, because
    ``cash_balance_at`` does not read ``account_postings``.  These two cases
    closed that hole; since plan#2914 they grade the ledger worked out from
    the records for the same input, using this file's independent oracles
    rather than the service's own helpers.

    Both go through ``anchor_service.apply_anchor_true_up`` -- the production
    door, with its lock and its duplicate rule (and its re-sync, until plan
    task plan#3076) -- not the
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
            # The ledger lands on the LATEST anchor, read off the records
            # rather than the service's own total.
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("900.00")
            # Ledger-wide self-check: nothing was double-booked or stranded.
            assert trial_balance(_db.session) == Decimal("0.00")
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
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("1000.00")
            assert trial_balance(_db.session) == Decimal("0.00")
            _assert_account_anchors_reconcile(scenario_id)

            # The source KIND flipped with the flag, graded on the read's
            # opening line per day.  The store's reconcile reversed the old
            # opening entry, and a reversal keeps the source kind it reverses, so
            # there only a SUM per day could say they cancelled.  The read holds
            # no reversal: which day holds a line is the whole answer.
            opening_by_day = {
                line.entry_date: line.amount
                for line in source_lines(
                    db.session, linked_key(savings), scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            }
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
            assert source_net(
                db.session, linked_key(savings), scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == Decimal("0.00")
