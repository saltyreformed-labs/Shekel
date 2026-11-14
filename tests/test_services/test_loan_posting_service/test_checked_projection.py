"""The checked projection (plan step E1a): the posted linked ledger equals the fold of the loan's
events at every write, and the date-aware reconcile (finding N-13).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.services import loan_posting_service, posting_service, transfer_service
from tests._ledger_reads import linked_key_of, linked_net_by_date
from tests._stored_ledger import stored_net_by_date
from tests._test_helpers import an_entered_day, on_both_sides

from ._helpers import (
    _P1,
    _make_loan,
    _settle_payment,
    _linked_ledger_id,
    _find_loan_ledger,
    _loan_side_movement,
    _source_entry_count,
)


class TestCheckedProjection:
    """The posted linked ledger must equal the fold of the loan's events (E1a).

    ``sync_loan_postings`` runs the per-visible-date assert after both
    reconciles: the walk's dated deltas (negated into posting space) must match
    the linked ledger's per-``entry_date`` nets exactly.  These tests prove the
    assert has teeth (a forced $1.00 walk-invisible posting fires it) and that
    the date-aware reconcile keeps it from firing on the one legitimate edit
    that moves a date without moving an amount -- a settled settle-day edit
    (finding N-13) -- converging in ONE pass in both directions.
    """

    def test_walk_invisible_posting_fires_the_assert(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1.00 posting the walk cannot model makes the sync refuse to run green.

        The negative control (verification standard 7.3): a balanced raw entry
        with a $1.00 leg on the loan's LINKED ledger -- the N-11 legacy shape,
        forbidden at the source since BG/R6 but forced here directly -- is
        invisible to the walk, so the per-date comparison must fail AT THAT
        DATE and name it.  Without this control the assert could be deleted
        and every test would stay green.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            # The settle chokepoint already ran the sync + assert green.

            equity_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            drift_entry = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=scenario_id,
                pay_period_id=seed_periods[_P1].id,
                entry_date=date(2026, 2, 15),
                # A purchase linking no movement: a kind the loan walk does
                # not model (it was the ``transaction`` source, deleted at
                # card plan#1845).
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.PURCHASE
                ),
                description="forced walk-invisible drift",
            )
            db.session.add(drift_entry)
            db.session.flush()
            expense_kind = ref_cache.posting_kind_id(PostingKindEnum.EXPENSE)
            db.session.add(Posting(
                journal_entry_id=drift_entry.id,
                ledger_account_id=_linked_ledger_id(loan),
                amount=Decimal("1.00"),
                posting_kind_id=expense_kind,
            ))
            db.session.add(Posting(
                journal_entry_id=drift_entry.id,
                ledger_account_id=equity_ledger.id,
                amount=Decimal("-1.00"),
                posting_kind_id=expense_kind,
            ))
            db.session.flush()

            with pytest.raises(
                posting_service.PostingError,
                match="diverges from the fold .*2026-02-15",
            ):
                loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.rollback()

    def test_settle_day_edit_redates_the_postings(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled settle-day edit moves every posting to the new settle date.

        The N-13 regression.  P1's $1,000.00 payment settles 2026-01-20:
        interest 100000 * 0.06 / 12 = 500.00, principal 500.00, so the linked
        ledger nets +1000.00 (cash) - 500.00 (correction) = +500.00 on 01-20.
        A PURE settle-day edit to 2026-02-05 changes no amount, so the
        pre-E1a reconcile wrote nothing and the entries kept the old date --
        while the fold moved to 02-05, a divergence the checked-projection
        assert now refuses.  After the edit: 01-20 holds no line (the store nets
        0.00 there, reversed at its own date), 02-05 nets +500.00, and a repeat sync
        writes NOTHING (the
        reconcile converged in one pass -- no churn).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            linked = linked_key_of(_db.session, loan.id)
            assert linked_net_by_date(_db.session, linked, scenario_id)[
                date(2026, 1, 20)
            ] == Decimal("500.00")

            # The pure settle-day edit -- no amount, status, or period change.
            # update_transfer runs the (now date-aware) reconciles + the
            # checked-projection assert; a raise here IS the N-13 regression.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(date(2026, 2, 5)),
                ),
            )
            db.session.commit()

            by_date = linked_net_by_date(_db.session, linked, scenario_id)
            # Old date: nothing left there, the read holding no $0.00 line.
            assert date(2026, 1, 20) not in by_date
            # New date: the full effect -- cash +1000.00, correction -500.00.
            assert by_date[date(2026, 2, 5)] == Decimal("500.00")

            # Convergence: a repeat of BOTH syncs writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            posting_service.sync_transfer_postings(xfer)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_backward_settle_day_move_converges(
        self, app, db, seed_user, seed_periods,
    ):
        """Moving the settle day EARLIER re-dates and converges in one pass too.

        The churn control: a latest-entry-date staleness heuristic would see
        the old-dated reversal as "the latest posting" after a BACKWARD move
        and re-churn (reverse + repost) on every later sync forever.  The
        per-(period, date) reconcile keys each delta to its exact date, so the
        backward move (02-05 -> 01-20) lands in one pass: 02-05 holds no line,
        01-20 nets +500.00, and a repeat sync writes nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 2, 5),
            )
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(date(2026, 1, 20)),
                ),
            )
            db.session.commit()

            by_date = linked_net_by_date(
                _db.session, linked_key_of(_db.session, loan.id), scenario_id,
            )
            assert date(2026, 2, 5) not in by_date
            assert by_date[date(2026, 1, 20)] == Decimal("500.00")

            before = _source_entry_count(xfer.id, loan.id)
            posting_service.sync_transfer_postings(xfer)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_legacy_stale_dated_cash_pair_self_heals(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-E1a cross-date transfer residue heals in the loan sync itself.

        The REAL-data shape the dev-clone sweep found (the Mortgage's July
        payment): the date-blind pre-E1a reconcile reversed a cash entry at a
        DIFFERENT date than it posted, leaving a net-zero transfer-source pair
        straddling two dates -- residue no loan-side reconcile can touch, so
        without the lineage-transfer pass the checked-projection assert would
        fire on it forever (the C9a self-500 class).  Forged here by re-dating
        the settled payment's cash entry (exactly what the legacy data
        contains), the loan sync must re-date it back (reverse at the forged
        date, re-post at the settle date), pass its own assert, and converge
        (a second sync writes nothing).  The cash entry is the loan-side
        MOVEMENT's since plan step ``balance:X-bi-6-3``, and the probe keys
        on it (re-expressed; the forge targeted the one ``transfer_id``
        entry).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            linked_id = _linked_ledger_id(loan)

            # Forge the legacy shape: the loan-side cash entry carries a WRONG
            # date, as a pre-E1a revert / re-settle left it.  Raw SQL, because
            # the forge must bypass the ORM append-only listener exactly as
            # the legacy writes predate it (the test runs as the table owner;
            # production's role REVOKE does not bind here).
            cash_entry_id = (
                db.session.query(JournalEntry.id)
                .filter(
                    JournalEntry.transaction_entry_id
                    == _loan_side_movement(xfer, loan).id,
                    JournalEntry.scenario_id == scenario_id,
                )
                .scalar()
            )
            db.session.execute(
                sa.text(
                    "UPDATE budget.journal_entries SET entry_date = :day "
                    "WHERE id = :entry_id"
                ),
                {"day": date(2026, 1, 5), "entry_id": cash_entry_id},
            )
            db.session.commit()
            assert stored_net_by_date(_db.session, linked_id, scenario_id)[
                date(2026, 1, 5)
            ] == Decimal("1000.00")

            # The loan sync heals it: lineage-transfer pass re-dates, then
            # the checked-projection assert passes.
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()
            by_date = stored_net_by_date(_db.session, linked_id, scenario_id)
            # Forged date: reversed at its own date; settle date: the full
            # effect (cash +1000.00, correction -500.00).
            assert by_date[date(2026, 1, 5)] == Decimal("0.00")
            assert by_date[date(2026, 1, 20)] == Decimal("500.00")

            # Convergence: a repeat sync writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_reverted_transfer_stale_residue_self_heals(
        self, app, db, seed_user, seed_periods,
    ):
        """Pre-E1a cross-date residue on a REVERTED payment heals too (review H2).

        A reverted payment is OUTSIDE the walk's settled set, so a heal pass
        keyed on the walk's payments would never touch its entries -- while
        the pre-E1a date-blind reconcile could leave its settle/reversal pair
        straddling two dates (net zero in TOTAL, nonzero per DATE), and the
        checked-projection assert would then fire on every later sync of the
        loan with nothing able to converge it.  The lineage pass therefore
        derives its candidates from the LEDGER: forged here by re-dating the
        reverted payment's reversal entry (the legacy shape), the loan sync
        must re-sync that transfer with its CURRENT unsettled sense, zero
        every date, pass its own assert, and converge.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            linked_id = _linked_ledger_id(loan)
            # Clean revert: the settle date nets zero (the anchor dates --
            # the opening and true-up -- legitimately keep their nets).
            assert stored_net_by_date(_db.session, linked_id, scenario_id)[
                date(2026, 1, 20)
            ] == Decimal("0.00")

            # Forge the pre-E1a residue: the loan-side REVERSAL entry (the
            # newest entry linked to the loan-side movement, plan step
            # ``balance:X-bi-6-3``) re-dated away from the settle date, so
            # 01-20 holds +1000.00 and 01-08 holds -1000.00.
            reversal_id = (
                db.session.query(JournalEntry.id)
                .filter(
                    JournalEntry.transaction_entry_id
                    == _loan_side_movement(xfer, loan).id,
                    JournalEntry.scenario_id == scenario_id,
                )
                .order_by(JournalEntry.id.desc())
                .limit(1)
                .scalar()
            )
            db.session.execute(
                sa.text(
                    "UPDATE budget.journal_entries SET entry_date = :day "
                    "WHERE id = :entry_id"
                ),
                {"day": date(2026, 1, 8), "entry_id": reversal_id},
            )
            db.session.commit()
            by_date = stored_net_by_date(_db.session, linked_id, scenario_id)
            assert by_date[date(2026, 1, 20)] == Decimal("1000.00")
            assert by_date[date(2026, 1, 8)] == Decimal("-1000.00")

            # The loan sync heals it from the LEDGER-derived candidate set
            # (the transfer is NOT in the walk -- it is reverted) and passes
            # its own checked-projection assert: both payment dates net zero.
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()
            by_date = stored_net_by_date(_db.session, linked_id, scenario_id)
            assert by_date[date(2026, 1, 20)] == Decimal("0.00")
            assert by_date[date(2026, 1, 8)] == Decimal("0.00")

            # Convergence: a repeat sync writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before
