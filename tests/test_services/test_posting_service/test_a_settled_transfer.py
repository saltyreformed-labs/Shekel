"""A settled transfer's two balanced entries: the day each is dated, a repeat sync that writes
nothing, a reversal read back from the ledger, and the per-account reconciliation helpers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import Posting
from app.services import posting_service, transfer_service
from tests._test_helpers import create_account_of_type, create_settled_transfer
from app.services.amount_ownership import state_own_amount
from app.models.amount_ownership import AmountOwnership

from ._helpers import (
    _ledger_id,
    _entries_for_transfer,
    _transit_ledger_id,
    _side_entries,
    _covering_movement_of_side,
    _legs_by_ledger,
    _scenario_id,
    _ledger_total,
)


# ---------------------------------------------------------------------------
# Settle: one balanced entry per confirmed transfer
# ---------------------------------------------------------------------------


class TestSyncSettlePostsBalancedEntry:
    """A settled transfer posts exactly two balanced entries, one per side."""

    def test_asset_to_asset_signs_balance_and_metadata(
        self, app, db, seed_user, savings,
    ):
        """Checking -> Savings $100 posts {Chk -100, T +100} and {Sav +100, T -100}.

        Arithmetic (ruling **R-BAL45**'s shape C, built at plan step
        ``balance:X-bi-6-3``): the from-side entry is -100.00 on Checking (a
        credit: money leaving) against +100.00 on Transfers in transit; the
        to-side entry is +100.00 on Savings (a debit: money entering) against
        -100.00 on transit.  Each entry sums to zero; transit nets 0.00 across
        the pair; Checking and Savings carry exactly the -100.00 / +100.00 the
        one-entry shape gave them.  Both ledgers are Asset class, but the
        builder never branches on class -- the sign follows direction.  Also
        pins the header metadata (the ``transfer_movement`` source kind, the
        link to the side's covering movement and NO ``transfer_id``, owner,
        scenario, period, the movement's own description) and the per-leg
        ``transfer`` posting kind.  Re-expressed from the one-entry shape when
        it moved; the per-account figures are unchanged.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            # Commit-5 wiring: settling through the transfer service already
            # auto-posted both entries; read them back (a re-sync would no-op).
            by_side = _side_entries(transfer.id)
            assert set(by_side) == {checking.id, savings.id}
            [from_entry] = by_side[checking.id]
            [to_entry] = by_side[savings.id]

            # Header metadata, each side linking ITS covering movement.
            for entry, account in ((from_entry, checking), (to_entry, savings)):
                movement = _covering_movement_of_side(transfer.id, account.id)
                assert entry.transfer_id is None
                assert entry.transaction_id is None
                assert entry.transaction_entry_id == movement.id
                assert entry.user_id == seed_user["user"].id
                assert entry.scenario_id == _scenario_id(seed_user)
                assert entry.pay_period_id == seed_user["bootstrap_period"].id
                assert entry.source_kind_id == ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER_MOVEMENT,
                )
                assert entry.description == movement.description
                # entry_date is a concrete civil date -- the movement's own
                # recorded day, materialized.
                assert entry.entry_date == movement.settled_on
                assert isinstance(entry.entry_date, date)
                # Every leg carries the transfer posting kind.
                kinds = {
                    leg.posting_kind_id
                    for leg in _db.session.query(Posting)
                    .filter_by(journal_entry_id=entry.id)
                    .all()
                }
                assert kinds == {
                    ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
                }
            assert from_entry.description == "Transfer to Posting Savings"
            assert to_entry.description == "Transfer from Checking"
            # Legs: -100 from Checking against +100 transit; +100 to Savings
            # against -100 transit; each entry sums to zero.
            from_legs = _legs_by_ledger(from_entry.id)
            assert from_legs == {
                checking_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            to_legs = _legs_by_ledger(to_entry.id)
            assert to_legs == {
                savings_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            assert sum(from_legs.values()) == Decimal("0.00")
            assert sum(to_legs.values()) == Decimal("0.00")
            # Transit nets to zero across the pair.
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            # Exactly two entries for the transfer.
            assert len(_entries_for_transfer(transfer.id)) == 2

    def test_asset_to_liability_signs(self, app, db, seed_user):
        """Checking -> Mortgage $250 posts -250 / +250 (pay-down), via transit.

        Arithmetic (plan Section 1, second worked example): paying down a
        liability is still from=-amount / to=+amount.  -250.00 on the Asset
        Checking ledger against +250.00 transit, +250.00 on the Liability
        Mortgage ledger against -250.00 transit; each entry sums to zero and
        transit nets to zero -- the sign rule is class-independent.
        Re-expressed for the pair at plan step ``balance:X-bi-6-3``.
        """
        with app.app_context():
            mortgage = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Posting Mortgage",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], mortgage,
                seed_user["bootstrap_period"], amount=Decimal("250.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            mortgage_ledger = _ledger_id(mortgage)
            transit_ledger = _transit_ledger_id(seed_user)

            # Commit-5 wiring: the mortgage pay-down auto-posted on settle as
            # two per-movement entries (plan step ``balance:X-bi-6-3``); read
            # them back by side (a re-sync would no-op).
            by_side = _side_entries(transfer.id)
            [from_entry] = by_side[seed_user["account"].id]
            [to_entry] = by_side[mortgage.id]
            assert _legs_by_ledger(from_entry.id) == {
                checking_ledger: Decimal("-250.00"),
                transit_ledger: Decimal("250.00"),
            }
            assert _legs_by_ledger(to_entry.id) == {
                mortgage_ledger: Decimal("250.00"),
                transit_ledger: Decimal("-250.00"),
            }
            assert _ledger_total(transit_ledger) == Decimal("0.00")

    def test_settle_uses_effective_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer's FIGURE overrides the transfer amount.

        The transfer's nominal amount is $100, but its settled figure is
        $97.50 (stored once on the transfer; neither side's record holds
        one), so each side's
        leg is $97.50 -- the value the balance calculator and the oracle use.
        The postings must be -97.50 / +97.50 on the real accounts, NOT -100 /
        +100 (the plan Section 5 prose said ``xfer.amount``; the correct,
        oracle-reconciling value is the settled figure, matching the Commit-3
        backfill).  Re-expressed for the pair at plan step
        ``balance:X-bi-6-3``.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_amount=Decimal("97.50"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            # Commit-5 wiring: the divergent settled figure auto-posted as two
            # per-movement entries (plan step ``balance:X-bi-6-3``); read them
            # back by side (a re-sync would no-op).
            by_side = _side_entries(transfer.id)
            [from_entry] = by_side[seed_user["account"].id]
            [to_entry] = by_side[savings.id]
            assert _legs_by_ledger(from_entry.id) == {
                checking_ledger: Decimal("-97.50"),
                transit_ledger: Decimal("97.50"),
            }
            assert _legs_by_ledger(to_entry.id) == {
                savings_ledger: Decimal("97.50"),
                transit_ledger: Decimal("-97.50"),
            }


#: A settle day in the PAST of this suite's frozen today (2026-03-20, set by
#: ``tests/test_services/conftest.py``), and inside the seeded period range.
#: Ruling **R-EJ** refuses a settle dated ahead of the clock, so a fixture that
#: means "this money moved" has to name a day that has happened.
_A_RECORDED_SETTLE_DAY = date(2026, 3, 9)


class TestSyncSettleEntryDate:
    """``entry_date`` is each side's recorded settle DAY.

    ``test_a_settled_transfer_cannot_be_left_without_a_day`` was deleted by
    ruling **R-BAL342** (the developer, 2026-10-20, finding **BAL-581**): it
    asked :class:`~app.services.transfer_service.SideDay` to refuse ``None``
    and wrote nothing, the refusal
    ``test_the_due_date_and_settle_day.py``'s
    ``test_a_settled_transfer_refuses_to_have_its_day_CLEARED`` asks.
    """

    def test_entry_date_is_the_rows_recorded_settle_day(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer's postings are dated by its recorded settle day.

        The entry date is each side's record's stored ``settled_on``,
        unmodified: no conversion, no fallback.

        **The rule it grades has moved twice.**  Ruling R-DH (b) inverted it in
        2026-07-31: ``entry_date`` had been ``paid_at``'s UTC civil date and
        became the USER's, together with the cash fold and the loan fold,
        because an entry date is compared against and bucketed by plain ``DATE``
        columns that mean the user's civil days -- and the three writers had to
        move as one, since moving any alone would put a transfer's two legs on
        different days.  Plan step X-f1 then deleted the derivation entirely:
        the day is stored, so there is no zone left to get wrong HERE.  The zone
        rule now lives at the write door and is pinned there
        (``test_status_seam.py``); what this case still owns is that the writer
        reads the recorded day rather than re-deriving one.

        The day is a PAST one relative to this suite's frozen today, and since
        ruling **R-EJ** it has to be: a settled row asserts that money has
        already moved, so the write door refuses a settle dated ahead of the
        clock.  It was ``2026-05-09`` against a today of ``2026-03-20`` -- two
        months in its own future, which nothing had ever refused.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=_A_RECORDED_SETTLE_DAY,
            )
            _db.session.commit()
            # Commit-5 wiring: the settle auto-posted both sides; read them
            # back.  Each entry is dated by ITS movement's recorded day (plan
            # step ``balance:X-bi-6-3``); the helper presses Paid on the one
            # day, which both sides then borrow, so the two entries share it.
            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 2
            # The recorded day, verbatim -- no conversion, no fallback.
            assert {entry.entry_date for entry in entries} == {
                _A_RECORDED_SETTLE_DAY,
            }


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestSyncIdempotency:
    """A repeat sync at the same target writes nothing."""

    def test_repeat_settle_is_noop(self, app, db, seed_user, savings):
        """Re-syncing an already-auto-posted settle returns [], no 2nd entry.

        Arithmetic: ``create_settled_transfer`` auto-posted +100 to the Savings
        ledger (Commit-5 wiring), so both manual re-syncs see current 100 ==
        target 100, delta 0, and write nothing.  This is the double-mark-done
        guard -- a repeated settle never double-posts.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            # Both manual re-syncs are no-ops: create_settled_transfer already
            # auto-posted the two per-movement entries, so current == target
            # and delta == 0 on each (the door reads each movement's own
            # state since plan step ``balance:X-bi-6-3``; there is no flag).
            first = posting_service.sync_transfer_postings(transfer)
            second = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert first == []
            assert second == []
            assert len(_entries_for_transfer(transfer.id)) == 2

    def test_cancel_with_nothing_posted_is_noop(
        self, app, db, seed_user, savings,
    ):
        """A sync of a never-posted, Projected transfer writes nothing.

        Arithmetic: current 0, target 0 (its movements are un-dated, so
        nothing posts), delta 0 -> no entry.  This is the projected ->
        cancelled path (a transfer cancelled before it ever settled has no
        ledger effect to reverse).  It passed ``settled=False`` until plan
        step ``balance:X-bi-6-3`` deleted the flag.
        """
        with app.app_context():
            transfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_user["bootstrap_period"].id,
                    scenario_id=_scenario_id(seed_user),
                    amount_ownership=AmountOwnership.own(Decimal("100.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            _db.session.commit()

            result = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert result == []
            assert _entries_for_transfer(transfer.id) == []


# ---------------------------------------------------------------------------
# Reversal: negate exactly what was posted
# ---------------------------------------------------------------------------


class TestSyncReversal:
    """A reversal reads the posted amount back from the ledger."""

    def test_reverse_negates_posted_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """A teardown posts the negation of what is posted, ignoring xfer.amount.

        Arithmetic: the settle posts +100 to the Savings ledger and -100 to
        Checking (each against transit).  Then the transfer amount is mutated
        to 999 (the value a naive ``target = xfer.amount`` reversal would
        use).  The reversal instead reads each side's posted net back and
        posts the delta to reach 0: -100 on Savings against +100 transit,
        +100 on Checking against -100 transit.  The Savings ledger nets back
        to its $100.00 opening; the reversal legs are 100, NOT 999.

        The door is the TEARDOWN twin (plan step ``balance:X-bi-6-3``, ruling
        **R-BAL101**): ``sync_transfer_postings`` reads each movement's own
        state and would find two live, dated movements and leave them posted;
        reversing a still-settled pair is what the delete door does, through
        ``reverse_transfer_postings_before_delete``.  It called the sync with
        ``settled=False`` until that step deleted the flag.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            # Mutate the transfer amount to a value a naive reversal would
            # wrongly use; the reversal must still negate the posted 100.
            state_own_amount(transfer, Decimal("999.00"))
            _db.session.flush()

            posting_service.reverse_transfer_postings_before_delete(transfer)
            _db.session.commit()

            by_side = _side_entries(transfer.id)
            [_, from_reversal] = by_side[checking.id]
            [_, to_reversal] = by_side[savings.id]
            assert _legs_by_ledger(to_reversal.id) == {
                savings_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _legs_by_ledger(from_reversal.id) == {
                checking_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            # Settled then reversed: the Savings ledger nets back to its
            # $100.00 opening (the absolute Step-5 semantics).
            assert posting_service.account_posting_total(
                savings.id, _scenario_id(seed_user),
            ) == Decimal("100.00")
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            # Four entries survive (append-only correction, never an edit).
            assert len(_entries_for_transfer(transfer.id)) == 4

    def test_revert_edit_amount_resettle_posts_new_amount(
        self, app, db, seed_user, savings,
    ):
        """A revert -> edit-amount -> re-settle posts the new amount.

        Arithmetic: settle $100 (+100 on Savings), revert (-100, net 0), edit
        the amount to $150, re-settle (current 0 -> target 150, delta +150).
        The transfer's entries net to +150 on Savings -- the new settled
        record -- so the Savings total is its $100.00 opening + 150 =
        250.00.  Six entries since plan step ``balance:X-bi-6-3``: two per
        act, one per side.  The revert UN-DATES both movements (ruling
        **R-BAL61**), which is what the door's reconcile reads to reverse;
        the manual syncs between the doors are no-ops that prove the doors
        already left the ledger at target (they were told ``settled=`` until
        that step deleted the flag).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            # Revert to Projected: the door un-dates the movements and its
            # reconcile reverses the pair; a manual sync then finds nothing.
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            # Edit the amount while Projected, then re-settle and re-post.
            transfer_service.update_transfer(
                transfer.id, user_id, amount_ownership=AmountOwnership.own(Decimal("150.00")),
            )
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            # 100 (opening) + 100 (settle) - 100 (reverse) + 150 (re-settle)
            # = +250.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("150.00")
            assert _ledger_total(_transit_ledger_id(seed_user)) == Decimal("0.00")
            # Six entries: settle, reverse, re-settle -- two sides each.
            assert len(_entries_for_transfer(transfer.id)) == 6


# ---------------------------------------------------------------------------
# Reconciliation helpers
# ---------------------------------------------------------------------------


class TestReconciliationHelpers:
    """The per-account posting total equals the settled transfer effect."""

    def test_helpers_match_after_settle_both_accounts(
        self, app, db, seed_user, savings,
    ):
        """After a $100 settle, both helpers agree on both accounts.

        Arithmetic: Savings (the to-account, the income side) is +100;
        Checking (the from-account, the expense side) is -100.
        ``settled_transfer_effect`` (the sum over each side's dated record)
        reports exactly those; ``account_posting_total`` (sum over postings) reports
        each on top of the account's opening -- Savings 100 + 100 = 200.00,
        Checking 1000 - 100 = 900.00 -- so the two independent tables agree
        on the transfer's contribution.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            checking = seed_user["account"]
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("900.00")
            assert posting_service.settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-100.00")

    def test_helpers_net_to_zero_after_reverse(
        self, app, db, seed_user, savings,
    ):
        """A settled-then-reverted transfer reconciles at zero on both sides.

        Arithmetic: the transfer's postings net to zero (+100 then -100); the
        revert un-dates each side's record (ruling **R-BAL61**), so the
        settled transfer effect drops it to zero too.  Each account's posting total lands back
        exactly on its opening (Savings 100.00, Checking 1000.00) -- the
        transfer contributes nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            for account, opening in (
                (savings, Decimal("100.00")),
                (seed_user["account"], Decimal("1000.00")),
            ):
                assert posting_service.account_posting_total(
                    account.id, scenario_id,
                ) == opening
                assert posting_service.settled_transfer_effect(
                    account.id, scenario_id,
                ) == Decimal("0.00")

    def test_settled_effect_uses_effective_amount(
        self, app, db, seed_user, savings,
    ):
        """The settled transfer effect honours a divergent settled figure.

        Arithmetic: nominal $100, settled actual $97.50, so the effect
        reader reports +97.50 and the posting total carries it on top of
        the Savings opening: 100 + 97.50 = 197.50.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_amount=Decimal("97.50"),
            )
            _db.session.commit()
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("97.50")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("197.50")
