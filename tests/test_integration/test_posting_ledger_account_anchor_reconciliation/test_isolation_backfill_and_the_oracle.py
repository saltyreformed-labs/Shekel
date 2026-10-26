"""Scenario and owner isolation, backfill against go-forward, and the oracle and the transfer
attribution each shown not to be vacuous.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.services import account_posting_service, posting_service, transfer_service
from tests._test_helpers import (
    append_only_guard_lifted,
    an_entered_day,
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    ledger_account_of_kind,
    linked_ledger_account,
    load_migration_module,
    observed_day_of,
    on_both_sides,
    reassert_balance_on,
    restate_account_opening,
)

from ._helpers import (
    _independent_linked_ledger_sum,
    _latest_assertion,
    _independent_post_assertion_source_effect,
    _trial_balance,
    _assert_account_anchors_reconcile,
    _origin_instant,
    _true_up_at,
    _settle_expense,
)


# The Step-5 data-boundary migration, loaded once so its idempotent raw-SQL
# teardown ``_remove_account_anchor_postings`` can reproduce the pre-C6
# historical state (an account whose anchor was asserted before the go-forward
# wiring, carrying no correction) for the backfill == go-forward test -- the
# same pattern the C7 backfill suite uses.
_BOUNDARY_MIGRATION = load_migration_module(
    "c9f2e6a4b1d8_account_anchor_postings_data_boundary.py"
)


# ---------------------------------------------------------------------------
# 8. Scenario and owner isolation
# ---------------------------------------------------------------------------


class TestScenarioAndOwnerIsolation:
    """Corrections and sources never bleed across scenarios or owners."""

    def test_a_settle_in_a_fresh_scenario_mints_that_scenarios_opening(
        self, app, db, seed_user,
    ):
        """A scenario's FIRST activity on an account opens that ledger.

        A scenario becomes live for an account the moment an entry first lands
        on its linked ledger there.  The account-global sync only visits
        scenarios that are ALREADY live, so the emission that makes a scenario
        live is the one that has to mint its corrections -- and the
        effect-time self-heal skipped it, because it tested only whether a
        posted correction had gone STALE and a brand-new scenario has none to
        stale.

        Hand-computed, in the shape production would reach it: Checking opened
        2026-01-02 at $1,000.00, a fresh scenario, one $70.00 expense settled
        2026-03-03 -- two months AFTER the opening, so the change rides on top
        of every assertion and the staleness arm correctly declines.  The
        scenario's linked ledger must read 1000 - 70 = $930.00.  Before the
        fix it read ``-$70.00``: the activity alone, with the opening and its
        equity twin both missing, which is why the trial balance still closed
        while the account's balance was wrong by its entire opening.

        Latent rather than live today -- production creates baseline scenarios
        only, and a baseline carries its corrections from account-create time
        -- so this is the guard for the scenario-clone feature that would
        otherwise ship on top of it.
        """
        with app.app_context():
            checking = seed_user["account"]
            opened = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
            reassert_balance_on(db.session, checking, opened)
            # **The BOOKS go back further than the restamp puts them** (plan
            # step X-f3c-2b, ruling **R-HG**).  The second settle below is
            # dated 2025-12-01 on purpose -- BEFORE the assertion, which is
            # what makes the staleness arm fire -- and ``restamp_opening_
            # assertion`` would open the books one day before the assertion,
            # which is after it.  Before the ASSERTION and after the BOOKS is
            # exactly the span this case needs, and is the production shape.
            restate_account_opening(db.session, checking, date(2025, 11, 30))
            db.session.commit()

            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=checking, scenario=whatif,
                settled_on=date(2026, 3, 3),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, whatif.id,
            ) == Decimal("930.00")
            # The baseline is untouched by the fresh scenario's activity.
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("1000.00")
            _assert_account_anchors_reconcile(whatif.id)
            _assert_account_anchors_reconcile(seed_user["scenario"].id)

    def test_a_settle_that_rides_on_top_does_not_rewalk_an_opened_ledger(
        self, app, db, seed_user, monkeypatch,
    ):
        """The skip survives: an ordinary settle re-derives nothing.

        The non-vacuity twin of the test above.  The fix widened the
        self-heal's fire condition, so this pins that it did not widen it to
        "always": a settle in a scenario that already carries its corrections,
        dated after every assertion, must not walk the account at all.

        **It counts the CALL, not the effect, and it has to.**  The reconcile
        is idempotent -- running it here writes nothing either way -- so an
        assertion about entries cannot tell a skipped walk from a performed
        one, and a test that claimed to pin the skip while asserting entry
        counts would pass with the skip deleted.  The spy delegates, so the
        ledger still reconciles and the sibling assertions below are real.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_posting_service import _sync

        with app.app_context():
            checking = seed_user["account"]
            opened = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
            reassert_balance_on(db.session, checking, opened)
            # **The BOOKS go back further than the restamp puts them** (plan
            # step X-f3c-2b, ruling **R-HG**).  The second settle below is
            # dated 2025-12-01 on purpose -- BEFORE the assertion, which is
            # what makes the staleness arm fire -- and ``restamp_opening_
            # assertion`` would open the books one day before the assertion,
            # which is after it.  Before the ASSERTION and after the BOOKS is
            # exactly the span this case needs, and is the production shape.
            restate_account_opening(db.session, checking, date(2025, 11, 30))
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            # First settle: the scenario is already open (the baseline carries
            # its corrections from account-create time), so this must skip.
            real = _sync.sync_account_anchor_postings
            calls: list[tuple[int, int]] = []

            def _spy(account_id, sync_scenario_id):
                calls.append((account_id, sync_scenario_id))
                real(account_id, sync_scenario_id)

            monkeypatch.setattr(
                _sync, "sync_account_anchor_postings", _spy,
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), account=checking,
                settled_on=date(2026, 3, 3),
            )
            db.session.commit()

            assert calls == [], (
                "an on-top settle in an already-opened scenario walked the "
                f"account anyway: {calls}"
            )
            # ...and a settle dated BEFORE the assertion still fires, so the
            # staleness arm is not what was disabled.
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("25.00"), account=checking,
                settled_on=date(2025, 12, 1),
            )
            db.session.commit()
            assert (checking.id, scenario_id) in calls

            # The opening ABSORBS the pre-assertion settle (nothing rides on
            # top of it) while the on-top one reduces the balance:
            # 1000 - 40 = $960.00.
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("960.00")
            _assert_account_anchors_reconcile(scenario_id)

    def test_scenarios_reconcile_independently(self, app, db, seed_user):
        """Checking's opening + sources reconcile per scenario, never bleeding.

        The seeded Checking's $1000.00 opening lives in the baseline.  A $40.00
        baseline expense and a $70.00 what-if expense (both settled at
        server-now, the same UTC day as the account's origination, so the
        effect-time self-heal posts Checking's opening into the what-if too) land
        on it.  Scoped to the baseline the Checking ledger is 1000 - 40 = 960.00;
        scoped to the what-if it is 1000 - 70 = 930.00 -- never the
        cross-scenario 890.  Each scenario carries its OWN copy of the opening
        and its OWN sources, and reconciles independently.

        Same accepted same-UTC-day caveat as the sibling cash oracle: the
        what-if opening posts only because the settle's civil day is at or before
        Checking's origination assertion, so the effect-time self-heal fires --
        not a general multi-scenario guarantee (R8 owns that policy).
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            checking = seed_user["account"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), account=checking, scenario=baseline,
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=checking, scenario=whatif,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, baseline.id,
            ) == Decimal("960.00")
            assert posting_service.account_posting_total(
                checking.id, whatif.id,
            ) == Decimal("930.00")
            assert _independent_linked_ledger_sum(
                checking.id, baseline.id,
            ) == Decimal("960.00")
            assert _independent_linked_ledger_sum(
                checking.id, whatif.id,
            ) == Decimal("930.00")

            _assert_account_anchors_reconcile(baseline.id)
            _assert_account_anchors_reconcile(whatif.id)

    @pytest.mark.server_clock
    def test_owners_reconcile_independently(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two owners settle on their own savings; neither sweep sees the other.

        Owner 1 settles a $60.00 expense on a $400.00 Savings (ledger 340.00);
        owner 2 settles an $80.00 expense on a $900.00 Savings (ledger 820.00).
        Each owner's sweep reconciles only their own accounts, and a ``Posting``
        carries no ``user_id`` (ownership is reachable only through its journal
        entry).
        """
        with app.app_context():
            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            savings1 = create_account_of_type(
                seed_user, db.session, "Savings", "Owner1 Savings",
                anchor_balance=Decimal("400.00"),
            )
            savings2 = create_account_of_type(
                seed_second_user, db.session, "Savings", "Owner2 Savings",
                anchor_balance=Decimal("900.00"),
            )
            db.session.commit()
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("60.00"), account=savings1,
            )
            create_settled_cash_transaction(
                seed_second_user, db.session,
                seed_second_user["bootstrap_period"],
                Decimal("80.00"), account=savings2,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings1.id, scenario1,
            ) == Decimal("340.00")
            assert posting_service.account_posting_total(
                savings2.id, scenario2,
            ) == Decimal("820.00")
            # A Posting has no user_id; ownership is normalized onto the entry.
            assert not hasattr(Posting, "user_id")
            owner1_id = seed_user["user"].id
            owner2_id = seed_second_user["user"].id
            for posting in _db.session.query(Posting).all():
                entry_owner = posting.journal_entry.user_id
                assert entry_owner in (owner1_id, owner2_id)
                assert posting.ledger_account.user_id == entry_owner

            _assert_account_anchors_reconcile(scenario1)
            _assert_account_anchors_reconcile(scenario2)


# ---------------------------------------------------------------------------
# 9. Backfill == go-forward (the historical sweep reproduces the wiring)
# ---------------------------------------------------------------------------


class TestBackfillEqualsGoForward:
    """Clearing then backfilling reproduces the go-forward ledger exactly."""

    @pytest.mark.server_clock
    def test_backfill_restores_the_absolute_ledger(self, app, db, seed_user):
        """A multi-anchor account's ledger is identical after clear + backfill.

        A Savings anchored $500.00 with a pre-true-up spend ($200 at T+1h), a
        true-up to $350.00 (T+2h), and a post-true-up spend ($100 at T+3h) posts
        its opening + true-up go-forward, reaching linked ledger 250.00.  The
        boundary migration's raw-SQL teardown clears every account correction +
        equity twin (the pre-C6 historical state), leaving the ledger short by
        the corrections; the deploy backfill re-derives them through the SAME
        go-forward sync, restoring the exact 250.00 and re-minting the equity
        twin.  The whole-DB sweep ties before AND after -- backfill ==
        go-forward by construction.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Backfill Savings",
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
            forward_total = posting_service.account_posting_total(
                savings.id, scenario_id,
            )
            assert forward_total == Decimal("250.00")
            _assert_account_anchors_reconcile(scenario_id)

            # Reproduce the pre-C6 historical state: corrections + twins gone.
            _BOUNDARY_MIGRATION._remove_account_anchor_postings(db.session)
            db.session.commit()
            assert ledger_account_of_kind(
                db.session, savings.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is None
            # The ledger is now the bare source legs (no opening / true-up).
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) != forward_total

            # The deploy backfill re-derives the corrections identically.
            account_posting_service.backfill_all_account_anchor_postings()
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == forward_total
            assert ledger_account_of_kind(
                db.session, savings.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is not None
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 10. Adversarial: the oracle is not vacuous (it fails on real breakage)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

    def test_tampered_latest_anchor_makes_the_sweep_fail(
        self, app, db, seed_user,
    ):
        """Forcing the latest anchor balance off its posted value breaks the sweep.

        A reconciled $500.00 Savings has linked ledger 500.00 and latest anchor
        500.00.  Forcing the anchor-history row's balance to 999 via raw SQL
        leaves the posted ledger at 500.00 but pushes the invariant's RHS to
        999 + 0 -- so the per-account reconciliation the oracle relies on now
        FAILS.  Driven through the REAL sweep helper under ``pytest.raises`` (so
        a regression in the helper itself is caught, not only an inline
        re-derivation), with ``match`` pinning the anchor comparison message
        specifically -- the tooth cannot be lost undetected.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Tamper Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            # Reconciled before tampering.
            _assert_account_anchors_reconcile(scenario_id)

            # Tamper the latest anchor balance; the posted ledger is
            # unchanged.
            #
            # **The append-only refusal is lifted for that one statement**
            # (plan step X-f3c-2c).  This comment used to read "history carries
            # no balance trigger, so this commits", which stopped being true:
            # ``budget.refuse_append_only_change`` refuses every UPDATE on the
            # table.  Tampering is the point here -- the case exists to prove
            # the sweep CAN fail -- and the only way to corrupt an append-only
            # row is to reach past the guard that makes it one.
            row_id = (
                _db.session.query(AccountAnchorHistory.id)
                .filter_by(account_id=savings.id)
                .order_by(
                    AccountAnchorHistory.created_at.desc(),
                    AccountAnchorHistory.id.desc(),
                )
                .scalar()
            )
            with append_only_guard_lifted(
                db.session, "budget.account_anchor_history",
            ):
                db.session.execute(_db.text(
                    "UPDATE budget.account_anchor_history "
                    "SET anchor_balance = 999 WHERE id = :i"
                ), {"i": row_id})
                db.session.commit()

            # Ledger unchanged (500), but the anchor truth drifted (999) -- the
            # absolute invariant no longer holds, so the real sweep raises.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("500.00")
            _, latest_anchor = _latest_assertion(savings.id)
            assert latest_anchor == Decimal("999.00")
            with pytest.raises(AssertionError, match="latest anchor"):
                _assert_account_anchors_reconcile(scenario_id)

    def test_trial_balance_catches_an_injected_leg(self, app, db, seed_user):
        """Injecting one extra leg pushes the trial balance off zero.

        A balanced book has trial balance 0.00.  A $500.00 Savings opening posts
        a balanced +500 / -500 pair; inserting one unmatched +50 leg onto its
        opening entry (raw SQL, flushed but never committed, so the DEFERRED
        per-entry trigger never fires) makes the whole-ledger sum 0 + 50 =
        50.00 -- so the trial-balance ``= 0`` assertion is a real check, not one
        the per-entry trigger makes vacuously true.  Rolled back so the leg
        never lands.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Inject Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            assert _trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto the opening entry (picked by
            # its correction source).  Flush (not commit) makes it visible; the
            # DEFERRED balanced trigger validates only at COMMIT, never reached.
            linked = linked_ledger_account(_db.session, savings.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            # The LATEST opening entry, and named as a choice rather than
            # taken as the only one.  Since plan step X-f3c-2b the factory
            # restates an account's books, and a restatement REVERSES the
            # opening entry and re-posts it -- three opening-sourced entries
            # where there used to be one, which is production's own shape
            # after the same act.  Any of them carries a leg on this ledger,
            # so the injection below is equally unbalanced whichever is
            # picked; ``.scalar()`` over the set would simply raise.
            entry_id = (
                _db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .order_by(JournalEntry.id.desc())
                .limit(1)
                .scalar()
            )
            assert entry_id is not None, (
                "no opening entry to inject into -- this class's whole name is "
                "a promise that it is not vacuous, and a None here would "
                "inject nothing and still pass"
            )
            _db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": linked.id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.OPENING),
            })
            _db.session.flush()

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            _db.session.rollback()


# ---------------------------------------------------------------------------
# 11. F1: a settled transfer's attribution mutation stays reconciled
# ---------------------------------------------------------------------------


def _transfer_net_in_period(account_id, scenario_id, period_id) -> Decimal:
    """Sum an account's transfer-cash LINKED-ledger legs in one pay period.

    The transfer cash on *account_id*'s linked ledger -- entries of the
    ``transfer_movement`` source, one per side's covering movement since plan
    step ``balance:X-bi-6-3`` (it read entries carrying a ``transfer_id``
    while the cash was one entry per transfer) -- scoped to a single
    ``pay_period_id``, so a settled period move can be checked to have moved
    the effect (R2): the old period nets to zero and the new period carries
    it.
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
            JournalEntry.pay_period_id == period_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER_MOVEMENT,
            ),
        )
        .scalar()
    )


class TestSettledTransferAttributionMutation:
    """A settled transfer's period / settle-day edit keeps the anchor sound (F1)."""

    @pytest.mark.server_clock
    def test_settled_period_move_reposts_and_reconciles(
        self, app, db, seed_user,
    ):
        """Moving a settled transfer's period re-posts its cash (R2) and reconciles.

        A $150.00 Checking -> Savings transfer settled in the bootstrap period
        (riding on top of both openings; Savings 200 + 150 = 350).  Moving it to
        a second period through ``update_transfer`` -- the settled-row period
        edit the C6 review M2 flagged as unreconciled -- now fires the Step-2
        reconcile because ``pay_period_id`` is in ``_POSTING_RELEVANT_FIELDS``:
        the Savings transfer cash nets to ZERO in the old period and +150.00 in
        the new one (R2), the account total is unchanged (350.00, no
        double-count), and the absolute invariant still holds with NO manual
        account-posting sync.  Before F1 the period edit skipped the reconcile,
        stranding the cash in the old period.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Move Savings",
                anchor_balance=Decimal("200.00"),
            )
            period2 = PayPeriod(user_id=user_id, start_date=date(2026, 2, 6))
            db.session.add(period2)
            db.session.commit()

            transfer = create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            _assert_account_anchors_reconcile(scenario_id)

            # Move the SETTLED transfer's period through the service.
            transfer_service.update_transfer(
                transfer.id, user_id, pay_period_id=period2.id,
            )
            db.session.commit()

            # R2: the transfer cash moved to the new period (old nets to zero),
            # with no double-count in the account total.
            assert _transfer_net_in_period(
                savings.id, scenario_id, seed_user["bootstrap_period"].id,
            ) == Decimal("0.00")
            assert _transfer_net_in_period(
                savings.id, scenario_id, period2.id,
            ) == Decimal("150.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            _assert_account_anchors_reconcile(scenario_id)

    @pytest.mark.server_clock
    def test_settle_day_move_across_the_anchor_reconciles(
        self, app, db, seed_user,
    ):
        """Moving a settled transfer's settle day across the anchor re-derives openings.

        A $150.00 Checking -> Savings transfer settled AFTER both origination
        anchors (rides on top): Savings 200 + 150 = 350, Checking 1000 - 150 =
        850.  Moving its settle day to BEFORE the anchors (a 2024 day)
        through ``update_transfer`` makes the transfer PRE-assertion, so it must
        be ABSORBED into the openings.  The settle day changes no leg, so the
        Step-2 reconcile-to-target writes nothing and its effect-time self-heal
        never fires; the F1 direct resync re-derives both endpoints' openings so
        the absolute invariant still holds with NO manual sync.  Post-move both
        accounts read their anchors (Savings 200, Checking 1000): the transfer
        is absorbed, nothing rides on top.  Before F1 the openings stayed at
        their ride-on-top values (Savings 350) while the invariant's RHS dropped
        to 200 -- a silently stale correction.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "PaidAt Savings",
                anchor_balance=Decimal("200.00"),
            )
            db.session.commit()

            transfer = create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            _assert_account_anchors_reconcile(scenario_id)

            # Move the settle day BEFORE both origination anchors (server-now 2026).
            transfer_service.update_transfer(
                transfer.id, user_id,
                side_days=on_both_sides(
                    transfer.from_account_id, transfer.to_account_id,
                    an_entered_day(date(2024, 1, 5)),
                ),
            )
            db.session.commit()

            # Pre-assertion now: absorbed into the openings, nothing on top.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            latest_asserted_day, _latest = _latest_assertion(savings.id)
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("0.00")
            _assert_account_anchors_reconcile(scenario_id)
