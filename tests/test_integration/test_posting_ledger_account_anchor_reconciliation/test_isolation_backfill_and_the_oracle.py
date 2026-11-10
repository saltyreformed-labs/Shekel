"""Scenario and owner isolation, and the oracle and the transfer attribution each shown not
to be vacuous.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.services import transfer_service
from tests import _ledger_reads
from tests._ledger_reads import (
    account_net,
    correction_net_in_period,
    linked_key_of,
    trial_balance,
)
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    on_both_sides,
    reassert_balance_on,
    restate_account_opening,
)

from ._helpers import (
    _latest_assertion,
    _independent_post_assertion_source_effect,
    _assert_account_anchors_reconcile,
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
        otherwise ship on top of it.  The defect was the store's self-heal;
        since plan#2914 this grades the ledger worked out from the records,
        which opens the fresh scenario's ledger from the opening record.
        """
        with app.app_context():
            checking = seed_user["account"]
            opened = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
            reassert_balance_on(db.session, checking, opened)
            # **The BOOKS go back further than the restamp puts them** (plan
            # step X-f3c-2b, ruling **R-HG**), the production shape.  This
            # comment named a second settle between the books and the
            # assertion, which this case never had; the sibling that had one
            # went at plan task plan#3132.  This case's one settle, after the
            # assertion, needs only the books open before it.
            restate_account_opening(db.session, checking, date(2025, 11, 30))
            db.session.commit()

            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("70.00"), account=checking, scenario=whatif,
                settled_on=date(2026, 3, 3),
            )
            db.session.commit()

            assert account_net(
                _db.session, checking.id, whatif.id,
            ) == Decimal("930.00")
            # The baseline is untouched by the fresh scenario's activity.
            assert account_net(
                _db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("1000.00")
            _assert_account_anchors_reconcile(whatif.id)
            _assert_account_anchors_reconcile(seed_user["scenario"].id)

    def test_scenarios_reconcile_independently(self, app, db, seed_user):
        """Checking's opening + sources reconcile per scenario, never bleeding.

        The seeded Checking's $1000.00 opening lives in the baseline.  A $40.00
        baseline expense and a $70.00 what-if expense (both settled at
        server-now, the same UTC day as the account's origination) land on it.
        Scoped to the baseline the Checking ledger is 1000 - 40 = 960.00;
        scoped to the what-if it is 1000 - 70 = 930.00 -- never the
        cross-scenario 890.  Each scenario carries its OWN copy of the opening
        and its OWN sources, and reconciles independently.
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
                seed_user, seed_user["bootstrap_period"],
                Decimal("40.00"), account=checking, scenario=baseline,
            )
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("70.00"), account=checking, scenario=whatif,
            )
            db.session.commit()

            assert account_net(
                _db.session, checking.id, baseline.id,
            ) == Decimal("960.00")
            assert account_net(
                _db.session, checking.id, whatif.id,
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
        Each owner's sweep reconciles only their own accounts.
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
                seed_user, seed_user["bootstrap_period"],
                Decimal("60.00"), account=savings1,
            )
            create_settled_cash_transaction(
                seed_second_user,
                seed_second_user["bootstrap_period"],
                Decimal("80.00"), account=savings2,
            )
            db.session.commit()

            assert account_net(
                _db.session, savings1.id, scenario1,
            ) == Decimal("340.00")
            assert account_net(
                _db.session, savings2.id, scenario2,
            ) == Decimal("820.00")

            _assert_account_anchors_reconcile(scenario1)
            _assert_account_anchors_reconcile(scenario2)


# ---------------------------------------------------------------------------
# 10. Adversarial: the oracle is not vacuous (it fails on real breakage)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

    def test_the_anchor_sweep_catches_a_wrong_read(
        self, app, db, seed_user, monkeypatch,
    ):
        """A read that loses an account's opening fails the anchor comparison.

        A reconciled $500.00 Savings reads ledger 500.00 = latest anchor 500.00 +
        effect 0.00.  A read dropping every line that names no movement (an
        opening or a true-up) answers ledger 0.00 with the anchor still 500.00, so
        the sweep FAILS.  ``match`` pins the anchor comparison message
        specifically, so a future edit that weakened THAT comparison but left
        another arm firing cannot keep this test green.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Wrong Read Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            _assert_account_anchors_reconcile(scenario_id)

            real_read = _ledger_reads.worked_out_lines
            monkeypatch.setattr(_ledger_reads, "worked_out_lines", lambda *args: [
                line for line in real_read(*args) if line.movement_id is not None
            ])

            assert account_net(
                db.session, savings.id, scenario_id,
            ) == Decimal("0.00")
            with pytest.raises(AssertionError, match="latest anchor"):
                _assert_account_anchors_reconcile(scenario_id)

    def test_the_trial_balance_catches_a_wrong_read(
        self, app, db, seed_user, monkeypatch,
    ):
        """A read that loses one leg of an opening pushes the trial balance off zero.

        A $500.00 Savings opening books Savings +500.00 and its equity counter
        -500.00, over the seeded Checking's own pair.  A read dropping every
        account's counter line leaves only the linked lines: Checking's seeded
        $1,000.00 + Savings 500.00 = 1,500.00, so the trial-balance ``= 0``
        assertion is a real check on the read.
        """
        with app.app_context():
            create_account_of_type(
                seed_user, db.session, "Savings", "Wrong Read Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            assert trial_balance(db.session) == Decimal("0.00")

            real_read = _ledger_reads.worked_out_lines
            monkeypatch.setattr(_ledger_reads, "worked_out_lines", lambda *args: [
                line for line in real_read(*args)
                if line.key.kind is LedgerAccountKindEnum.LINKED
            ])

            assert trial_balance(db.session) == Decimal("1500.00")

# ---------------------------------------------------------------------------
# 11. F1: a settled transfer's attribution mutation stays reconciled
# ---------------------------------------------------------------------------


def _transfer_net_in_period(account_id, scenario_id, period_id) -> Decimal:
    """Return the transfer cash on an account's own line in one pay period.

    Scoped to a single paycheck, so a settled period move can be checked to have
    moved the effect (R2): the old period nets to zero and the new period carries
    it.
    """
    return correction_net_in_period(
        _db.session, linked_key_of(_db.session, account_id), scenario_id,
        PostingSourceEnum.TRANSFER_MOVEMENT, period_id,
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
        edit the C6 review M2 flagged as unreconciled -- moves the cash with it:
        the Savings transfer cash nets to ZERO in the old period and +150.00 in
        the new one (R2), the account total is unchanged (350.00, no
        double-count), and the absolute invariant still holds.  Before F1 the
        period edit skipped the reconcile, stranding the STORED cash in the old
        period; since plan#2914 this grades the worked-out read, which files
        the cash in the record's period.
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
            assert account_net(
                _db.session, savings.id, scenario_id,
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
            assert account_net(
                _db.session, savings.id, scenario_id,
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
        be ABSORBED into the openings.  The move re-dates each side's record to
        2024-01-05, so both endpoints' openings absorb it and the absolute
        invariant still holds.  Post-move both accounts read their anchors
        (Savings 200, Checking 1000): the transfer is absorbed, nothing rides
        on top.  Before F1 the STORED openings stayed at their ride-on-top
        values (Savings 350) while the invariant's RHS dropped to 200 -- a
        silently stale correction; since plan#2914 this grades the worked-out
        read, which derives the openings from the records.
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
            assert account_net(
                _db.session, savings.id, scenario_id,
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
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("200.00")
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            latest_asserted_day, _latest = _latest_assertion(savings.id)
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("0.00")
            _assert_account_anchors_reconcile(scenario_id)
