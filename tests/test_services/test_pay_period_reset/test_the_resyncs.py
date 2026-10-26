"""What a full reset re-posts after the period wipe cascades it away: the loan's genesis entries
and the other accounts' openings, each reconciling after the rebuild.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.services import (
    account_posting_service,
    loan_posting_service,
    pay_period_admin,
    posting_service,
)
from scripts.integrity_check import check_referential_integrity
from tests._test_helpers import (
    rhythm_of,
    all_periods,
    assert_pay_period_invariants,
    create_loan_with_trueup,
    create_savings_account,
    derived_span,
    make_expense_template,
    make_transfer_template,
)

from ._helpers import _NEW_START, _seed_old_schedule, _reset_and_populate


# A configured loan for the genesis-resync regression (review M2 / R7):
# origination $250,000, trued up to $100,000, so its genesis ledger nets
# -(100000) = opening -250000 + true-up +150000.  Both anchor dates predate the
# rebuilt schedule's 2026-06-05 start, so after reset every correction
# re-attributes to the new earliest period.
_LOAN_ORIGINATION = Decimal("250000.00")


_LOAN_ANCHOR_BALANCE = Decimal("100000.00")


_LOAN_ANCHOR_DATE = date(2026, 1, 10)


_LOAN_ORIGINATION_DATE = date(2025, 1, 1)


_LOAN_RATE = Decimal("0.06000")


_LOAN_GENESIS_NET = Decimal("-100000.00")


def _loan_genesis_entries(db_session, user_id):
    """Return the user's loan opening + true-up journal entries.

    The genesis corrections a full reset must re-post: their
    ``source_kind_id`` is ``loan_opening`` or ``loan_trueup`` (never a
    settled-transaction source), and each carries a ``pay_period_id`` that
    CASCADE-deletes with the wiped periods.
    """
    opening = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
    trueup = ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP)
    return (
        db_session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.source_kind_id.in_([opening, trueup]),
        )
        .all()
    )


class TestResetResyncsLoanGenesis:
    """A full reset re-posts the loan genesis entries the period wipe cascades.

    A configured loan's opening / true-up postings carry a ``pay_period_id`` and
    so CASCADE-delete with the wiped periods, yet they exist independently of any
    settled transaction -- the zero-settled reset gate does NOT protect them.
    Their source facts (``LoanParams``, the ``user_trueup`` ``LoanAnchorEvent``)
    survive, so reset must re-derive and re-post them onto the rebuilt schedule
    in the same transaction (review M2 / R7).  Without the re-sync the loan's
    ledger reads empty until the next loan write and every ledger-authoritative
    loan surface degrades to the replay fallback.
    """

    def test_genesis_reposted_and_reconciles_after_reset(
        self, app, db, seed_user,
    ):
        """Genesis nets -(anchor) before AND after reset, re-attributed anew.

        Opening -250000 + true-up +150000 = -100000 == -(anchor 100000).  Before
        reset the two entries attribute to old periods; the wipe CASCADE-deletes
        them; the re-sync re-posts exactly two entries (opening + true-up), now
        attributed to the rebuilt schedule's earliest period (both anchor dates
        precede its 2026-06-05 start), and the loan-linked ledger nets -100000
        again -- it would be 0 without the fix.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            _seed_old_schedule(db.session, seed_user)
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_LOAN_ORIGINATION,
                anchor_balance=_LOAN_ANCHOR_BALANCE,
                anchor_date=_LOAN_ANCHOR_DATE,
                rate=_LOAN_RATE,
                origination_date=_LOAN_ORIGINATION_DATE,
            )
            # Post the genesis corrections (opening + true-up) as the precondition.
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            old_ids = {p.id for p in all_periods(user_id)}
            before = _loan_genesis_entries(db.session, user_id)
            assert len(before) == 2
            assert {e.pay_period_id for e in before} <= old_ids
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == _LOAN_GENESIS_NET

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # The genesis entries re-post onto the rebuilt schedule: exactly two,
            # attributed to the new earliest period (both anchor dates precede
            # it), and the loan-linked ledger reconciles to -(anchor) again.
            after = _loan_genesis_entries(db.session, user_id)
            assert len(after) == 2
            assert {e.pay_period_id for e in after} == {new_periods[0].id}
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == _LOAN_GENESIS_NET
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_the_repopulation_cannot_invalidate_either_resync(
        self, app, db, seed_user,
    ):
        """The re-syncs are still at their FIXPOINT after the repopulation.

        **This is what makes ruling R-R38's reordering safe, graded rather
        than argued.**  The repopulation used to run BETWEEN the rebuild and
        the two posting re-syncs; the door now returns before it, so it runs
        AFTER both.  If a freshly generated row could move what either re-sync
        derives, re-running them at this point would rewrite something.

        The argument the code makes is that neither can see such a row: every
        read either side makes of ``budget.transactions`` or
        ``budget.transfers`` is keyed on ids taken from the POSTED ledger --
        the linked ledger's nonzero per-row nets on the account side, the stale
        lineage transfers and stale payment shadows on the loan side, and the
        loan walk's own ``settled_income_legs`` -- and a ``Projected`` row
        posts nothing, so it is in none of them.  **A first draft of this
        docstring said the account side reads no transaction or transfer at
        all, and an adversarial review MEASURED that false**; the conclusion
        survived and the roll-call is what was wrong, which is why the property
        is stated instead.  This case is the measurement of it: an owner
        holding a loan AND both kinds of active template, reset and
        repopulated, whose re-syncs then write nothing.

        The reconcile is self-healing, so a re-run that DID change something
        would change it here -- which is exactly what the assertions read.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            _seed_old_schedule(db.session, seed_user)
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_LOAN_ORIGINATION,
                anchor_balance=_LOAN_ANCHOR_BALANCE,
                anchor_date=_LOAN_ANCHOR_DATE,
                rate=_LOAN_RATE,
                origination_date=_LOAN_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            new_periods = _reset_and_populate(
                user_id, new_start_date=_NEW_START, num_periods=4,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # The repopulation really ran: without rows in the new periods
            # there is nothing that could have disturbed a re-sync, and this
            # case would pass for the wrong reason.
            generated = db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_([p.id for p in new_periods]),
                Transaction.template_id.isnot(None),
            ).count()
            assert generated == len(new_periods), (
                f"the repopulation wrote {generated} template rows into "
                f"{len(new_periods)} periods; with none there is nothing for "
                f"a re-sync to have been disturbed by"
            )

            def _fingerprint():
                """Every genesis entry as (source, period, its posting pairs)."""
                return sorted(
                    (
                        entry.source_kind_id, entry.pay_period_id,
                        tuple(sorted(
                            (posting.ledger_account_id, posting.amount)
                            for posting in entry.postings
                        )),
                    )
                    for entry in _loan_genesis_entries(db.session, user_id)
                )

            before = _fingerprint()
            net_before = posting_service.account_posting_total(
                loan.id, scenario_id,
            )
            assert before, "no genesis entry was posted, so nothing is graded"
            assert net_before == _LOAN_GENESIS_NET

            # Re-run BOTH re-syncs at the post-repopulation state.  Each is
            # reconcile-to-target and self-healing, so anything the
            # repopulation had invalidated is rewritten here.
            loan_posting_service.resync_user_loan_postings(user_id)
            account_posting_service.resync_user_account_anchor_postings(user_id)
            db.session.flush()

            assert _fingerprint() == before, (
                "re-running the re-syncs after the repopulation rewrote a "
                "genesis entry, so the order R-R38 puts them in is NOT "
                "order-independent"
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == net_before
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestResetResyncsAccountOpenings:
    """A full reset re-posts the non-loan account openings the wipe cascades.

    The Step-5 analogue of the loan re-sync above (plan Section 3.3, point
    4): a non-loan account's opening correction carries a ``pay_period_id``
    and CASCADE-deletes with the wiped periods, so the per-user account
    re-sync must re-derive each opening onto the rebuilt schedule in the same
    transaction.  Without it every non-loan ledger reads empty (the
    absolute invariant silently degrades to changes-only) until the
    account's next anchor event.

    **What it re-derives FROM changed at ruling R-EO** (plan step X-f1c3c).
    The wipe used to take the account's ``AccountAnchorHistory`` rows with it
    -- they carried a ``pay_period_id`` on a CASCADE FK -- and a
    ``_reanchor_accounts`` pass staged one fabricated origination row per
    account for this re-sync to read.  The assertion carries no period now, so
    the wipe cannot reach it and the re-sync reads the observations that were
    always there.  ``_reanchor_accounts`` is deleted; this docstring named it
    until X-f1c3c.
    """

    def test_openings_reposted_and_reconcile_after_reset(
        self, app, db, seed_user,
    ):
        """The Checking opening nets +1000 before AND after, re-attributed anew.

        The seed Checking's $1000.00 opening posts at fixture time.  The
        wipe disposes it with the old periods; the re-sync re-posts exactly
        one opening entry attributed to the re-anchored period (the rebuilt
        schedule's resolved anchor), and the linked ledger nets 1000.00
        again -- it would be 0 without the fix.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking_id = seed_user["account"].id
            _seed_old_schedule(db.session, seed_user)
            assert posting_service.account_posting_total(
                checking_id, scenario_id,
            ) == Decimal("1000.00")

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Re-fetch: the reset's identity-map wipe detached the fixture
            # object.
            checking = db.session.get(Account, checking_id)
            openings = (
                db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == user_id,
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.ACCOUNT_OPENING,
                    ),
                )
                .all()
            )
            assert len(openings) == 1
            # The opening correction books in the period CONTAINING the
            # assertion's own day (ruling R-EA: the period is DERIVED from the
            # day, never read beside it).  The containing period is resolved
            # HERE from the dates, not by calling the resolver the writer
            # calls, so the two cannot agree by sharing one implementation.
            #
            # A first version of this assertion only checked membership in the
            # set of all live periods -- true of any of the six, so it would
            # have passed against a correction filed in the wrong one.
            rebuilt = all_periods(user_id)
            opening_assertion = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id)
                .order_by(AccountAnchorHistory.observed_on.asc())
                .first()
            )
            # The fixture's Checking is asserted on 2024-01-05 and the rebuilt
            # schedule starts 2026-06-05, so the assertion's day precedes EVERY
            # period.  ``pay_calendar.PayCalendar.filing_period`` clamps such a
            # correction into the user's EARLIEST period -- index 0 -- so the
            # reader, which bounds by period start, counts it from the first
            # period on.
            assert all(
                opening_assertion.observed_on < p.start_date for p in rebuilt
            ), (
                f"this test's expected period is the no-containing-period "
                f"branch; the assertion ({opening_assertion.observed_on}) must "
                f"precede every rebuilt period"
            )
            earliest = min(rebuilt, key=lambda p: derived_span(p).period_index)
            assert derived_span(earliest).period_index == 0
            assert openings[0].pay_period_id == earliest.id
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))
