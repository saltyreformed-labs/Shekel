"""Tests for pay-period CRUD slice (d): truncate (the first destructive op).

Truncate deletes the schedule tail via a single bulk DELETE (PostgreSQL
cascades its transactions and transfers).
Two gates run first: the hard lock classifier (historical / settled /
anchor / rule -- never overridable) and the broadened discard gate
(hand-entered / override / Credit-Cancelled rows -- overridable with
``confirm_discard``).

Because this is the highest-stakes operation in the feature, the suite
carries all four disciplines: structural invariants after every
successful delete (Discipline 1), a hand-computed retained-window balance
(Discipline 2), the production integrity checker (Discipline 3), and the
adversarial refusal tests that assert a bad state is BLOCKED and nothing
is deleted (Discipline 4).  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    PayPeriodUnresolved,
)
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    pay_period_admin,
    pay_period_write,
    posting_service,
    transfer_service,
)
from app.services.pay_period_locks import PeriodLockReason
# The APP's civil day, never ``date.today()`` -- these fixtures build periods
# that must CONTAIN the day the account factory stamps on its origination
# assertion, and that day is the display-timezone one (CI runs a zone where
# the two differ, deliberately).
from app.utils.dates import display_today
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    record_paydays_across_a_hole,
    rhythm_of,
    add_txn,
    all_periods,
    assert_pay_period_invariants,
    bare_expense_template,
    create_savings_account,
    derived_span,
    last_covered_day,
    make_cadence_rule,
    make_every_period_rule,
    make_expense_template,
    make_transfer_template,
    populate_in_a_fresh_pass,
    restate_account_opening,
    seam_cash_balance_at,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from app.models.amount_ownership import AmountOwnership


def _future_periods(db_session, seed_user, count=6, start=date(2026, 7, 3)):
    """Generate `count` biweekly FUTURE periods (indices 1..count)."""
    periods = record_paydays_across_a_hole(
        user_id=seed_user["user"].id,
        first_payday=start,
        num_periods=count,
        rhythm=rhythm_of(14),
    )
    db_session.commit()
    return periods


def _emit_untethered_entry(db_session, seed_user, savings, period, amount):
    """Post one balanced entry whose per-account nets are NON-zero in *period*.

    The shape a loan opening / true-up correction has -- balanced across two
    ledger accounts (Checking ``-amount`` / Savings ``+amount``), each with a
    non-zero net, linked to NO transaction or transfer -- so the LEDGER_POSTINGS
    gate is exercised without a settled source row (which would trip the
    higher-precedence SETTLED_TXN lock instead).  Built directly on the models
    (tests are unfenced); the deferred balanced-journal trigger accepts it.
    """
    ledger_ids = {
        account_id: ledger_id
        for ledger_id, account_id in db_session.query(
            LedgerAccount.id, LedgerAccount.account_id,
        ).filter(LedgerAccount.account_id.isnot(None)).all()
    }
    entry = JournalEntry(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        pay_period_id=period.id,
        entry_date=period.start_date,
        source_kind_id=ref_cache.posting_source_id(PostingSourceEnum.TRANSFER),
        description="Untethered balanced correction",
    )
    db_session.add(entry)
    transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
    entry.postings.append(Posting(
        ledger_account_id=ledger_ids[seed_user["account"].id],
        amount=-amount, posting_kind_id=transfer_kind,
    ))
    entry.postings.append(Posting(
        ledger_account_id=ledger_ids[savings.id],
        amount=amount, posting_kind_id=transfer_kind,
    ))
    db_session.flush()
    return entry


def _count_periods(db_session, user_id):
    """Count the user's pay periods."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _opening_entry_period(db_session, account_id):
    """Return the pay period the account's OPENING correction is filed under.

    The non-vacuity probe for the hard-lock case below: that case is about a
    correction sitting inside the window being truncated, and since plan step
    X-f3c-2a the opening entry is dated where the account's BOOKS open rather
    than where its balance was observed.  A fixture whose books drift out of
    the window would leave the case asserting a lock that nothing triggers, so
    the placement is measured instead of assumed.

    Args:
        db_session: The test ``db.session``.
        account_id: The account whose opening correction to locate.

    Returns:
        The ``budget.pay_periods`` id of its ``account_opening`` journal
        entry, or ``None`` when it books none (a ``$0.00`` opening).
    """
    return (
        db_session.query(JournalEntry.pay_period_id)
        .filter(
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            ),
            JournalEntry.id.in_(
                db_session.query(Posting.journal_entry_id).join(
                    LedgerAccount,
                    Posting.ledger_account_id == LedgerAccount.id,
                ).filter(LedgerAccount.account_id == account_id)
            ),
        )
        .order_by(JournalEntry.id.desc())
        .limit(1)
        .scalar()
    )


def _txns_in(db_session, period_id):
    """Count all transactions physically held in a period (by id).

    Takes an int id, not a PayPeriod object, so callers can query a
    period AFTER truncate has bulk-deleted (and ``expire_all``-ed) it
    without tripping ``ObjectDeletedError`` on a stale instance.
    """
    return db_session.query(Transaction).filter_by(pay_period_id=period_id).count()


def _make_adhoc_transfer(db_session, seed_user, to_account, period):
    """Create an ad-hoc (no template) projected transfer in a period."""
    xfer = transfer_service.create_transfer(transfer_service.TransferSpec(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount_ownership=AmountOwnership.own(Decimal("150.00")),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        category_id=None,
    ))
    db_session.flush()
    return xfer


class TestTruncateHappyPath:
    """Truncate removes the tail and only the tail."""

    def test_deletes_only_indices_above_keep(self, app, db, seed_user):
        """Indices > keep_through go; indices <= keep_through stay."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[2].id,
            )
            db.session.commit()

            assert deleted == 3  # indices 4, 5, 6
            remaining = {
                derived_span(p).period_index
                for p in all_periods(user_id)
            }
            # Bootstrap (0) + kept future indices 1..3.
            assert remaining == {0, 1, 2, 3}
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_cascade_removes_transactions(self, app, db, seed_user):
        """Deleting a period cascades its transactions away."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()
            doomed_id = periods[3].id  # index 4; capture before deletion
            keep_period_id = periods[1].id
            assert _txns_in(db.session, doomed_id) == 1

            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=keep_period_id,
            )
            db.session.commit()
            # The deleted period's row is gone with it.
            assert _txns_in(db.session, doomed_id) == 0
            assert_pay_period_invariants(db.session, user_id)

    def test_cascade_removes_the_doomed_periods_transfer(self, app, db, seed_user):
        """A transfer in a deleted period goes with it.

        Until plan step ``balance:X-bi-6-4d-2`` the case also read that
        neither of the transfer's two shadow rows survived in the deleted
        period.  A transfer is one row since (``ck_transactions_names_no_transfer``),
        so that count found no row to count.  Renamed from
        ``test_cascade_removes_transfers_and_both_shadows``.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()
            doomed_id = periods[3].id  # capture before deletion
            keep_period_id = periods[1].id
            assert db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).count() == 1
            doomed_transfer_id = db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).one().id

            # confirm_discard not needed: template transfers are regenerable.
            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=keep_period_id,
            )
            db.session.commit()
            assert db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).count() == 0
            # The transfer itself is gone, not moved to a surviving period.
            assert db.session.query(Transfer).filter_by(
                id=doomed_transfer_id,
            ).count() == 0
            assert_pay_period_invariants(db.session, user_id)

    def test_idempotent_noop_through_the_last_period(self, app, db, seed_user):
        """Keeping through the LAST period deletes nothing.

        Truncate's no-op case.  It used to be spelled ``keep_through_index=999``
        -- an ordinal above every real one -- and plan step C3-a removed the
        spelling with the ordinal: an id above every real id names no period,
        which is now a REFUSAL rather than a no-op (see
        ``TestTruncateRefusesAnIdItCannotResolve``).  Naming the actual last
        period is the same question asked of the surviving key.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[-1].id,
            )
            db.session.commit()
            assert deleted == 0
            assert _count_periods(db.session, user_id) == before


    def test_balances_correct_after_truncate(self, app, db, seed_user):
        """Disciplines 1-3: the retained-window balance is unchanged.

        Anchor $1000 at the bootstrap (index 0, no expense); a $1200
        every-period expense fills indices 1..6.  Truncating to keep index
        3 removes 4..6 but leaves the projection for index 3 exactly
        1000 - 3*1200 = -2600, both before and after.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            make_expense_template(db.session, seed_user, amount="1200.00")
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            before = seam_cash_balance_at(
                account, scen, last_covered_day(periods[2]),  # index 3
            )
            assert before == Decimal("-2600.00")  # 1000 - 3*1200

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[2].id,
            )
            db.session.commit()
            assert deleted == 3

            after = seam_cash_balance_at(
                account, scen, last_covered_day(periods[2]),
            )
            assert after == before  # retained window untouched
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))


# Three ACCOUNT_ANCHOR cases were DELETED at plan step X-f1c3c (ruling R-EO):
# truncate blocking on a period an account anchored to, the same for a period
# holding no correction, and the raw bulk-DELETE raising an IntegrityError on
# the anchor FK.  All three graded a lock and an FK that are gone -- neither an
# account nor a balance assertion references a pay period, so no period delete
# can strand one.  The period's POSTED state is what is still worth refusing,
# and ``LEDGER_POSTINGS`` (which outranked ``ACCOUNT_ANCHOR`` in the precedence
# anyway) covers it; its own cases in this class are untouched.


class TestTruncateRefusesAnIdItCannotResolve:
    """Finding P13: the wire key names a ROW, so an unowned one is refused.

    Until plan step C3-a the truncate form posted ``period_index``, and the
    service compared it with ``>``.  Every value the comparison accepted
    "worked": an ordinal from a stale page named whichever period now sits at
    that position, and one above the end was a silent no-op.  Keyed on ``id``
    there is exactly one correct answer -- the row -- and everything else is
    refused before the classifier, the discard gate or the DELETE runs.
    """

    def test_an_id_that_names_no_period_is_refused(self, app, db, seed_user):
        """A forged or stale id deletes nothing and says so."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)
            absent = max(p.id for p in periods) + 1000

            with pytest.raises(PayPeriodUnresolved) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=absent,
                )

            # App text shows a person no system id (the developer,
            # 2026-09-23; this line asserted the echo until pay_calendar:C21,
            # approved under rule 5): the id goes to the ACCESS log only.
            assert str(absent) not in str(excinfo.value)
            assert _count_periods(db.session, user_id) == before

    def test_another_owners_period_is_refused_identically(
        self, app, db, seed_user, seed_second_user,
    ):
        """A real id belonging to someone else raises the SAME message.

        The security response rule: "not yours" and "does not exist" must be
        indistinguishable, or this door reports whether another owner's period
        id exists.  Both are asserted against the same rendered message, and
        the second owner keeps every period they had.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            theirs = seed_second_user["bootstrap_period"]
            second_id = seed_second_user["user"].id
            before = _count_periods(db.session, second_id)

            with pytest.raises(PayPeriodUnresolved) as owned_exc:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=theirs.id,
                )
            with pytest.raises(PayPeriodUnresolved) as absent_exc:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=theirs.id + 10_000,
                )

            # Same sentence, only the echoed id differs -- no existence oracle.
            assert str(owned_exc.value).replace(
                str(theirs.id), "X",
            ) == str(absent_exc.value).replace(str(theirs.id + 10_000), "X")
            assert _count_periods(db.session, second_id) == before

class TestTruncateHardLocks:
    """Hard locks refuse the delete and change nothing (Discipline 4)."""

    def test_settled_transaction_blocks_and_deletes_nothing(
        self, app, db, seed_user,
    ):
        """A settled txn in the window raises PayPeriodLocked; nothing goes."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            add_txn(
                db.session, seed_user, periods[2], "Paid Bill", "100.00",
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )

            assert _count_periods(db.session, user_id) == before
            assert db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).count() == 1
            assert_pay_period_invariants(db.session, user_id)

    def test_historical_period_blocks(self, app, db, seed_user):
        """A historical period in the window is hard-locked."""
        with app.app_context():
            user_id = seed_user["user"].id
            # Spanning past->future: early indices have already ended.
            record_paydays_across_a_hole(
                user_id=user_id,
                first_payday=date(2026, 1, 2), num_periods=14, rhythm=rhythm_of(14),
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id,
                    keep_through_period_id=seed_user["bootstrap_period"].id,
                )
            assert PeriodLockReason.HISTORICAL in excinfo.value.blocking.values()
            assert _count_periods(db.session, user_id) == before

    def test_nonzero_anchor_opening_blocks_as_ledger_postings(
        self, app, db, seed_user,
    ):
        """A NON-zero anchor's opening correction hard-locks its period.

        The Step-5 accepted behavior change (plan Section 3.5): a $500.00
        savings whose opening correction lands in a to-delete period makes
        that period's per-ledger nets non-zero, so the double-entry gate
        reports LEDGER_POSTINGS (it precedes ACCOUNT_ANCHOR).  Nothing is
        deleted.

        **The schedule is generated AROUND today deliberately** (plan step
        X-ai-r).  A correction books in the period CONTAINING the day it is
        dated (ruling R-DH), and this fixture puts a to-delete period there.
        It used to force ``anchor_period_id`` onto a FUTURE period and rely on
        the writer copying that stored id, which is the attribution X-ai-r
        removed; the split case moved to the test below.

        **The OPENING correction is dated where the BOOKS open, not where the
        balance was observed** (plan step X-f3c-2a), and the two stopped being
        the same day at plan step X-f3c-2b: the factory opens an account's
        books before anything a fixture could date, which is BEFORE the window
        this case truncates.  So the books are restated into the anchored
        period explicitly and the postings reconciled, which is what puts the
        correction in the to-delete window this case is about.  Legal because
        the account records no movement -- ruling **R-HG** bounds an opening by
        the movements on file, and there are none.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            # Periods straddling today, so the account's observed day (today)
            # falls INSIDE one of them -- production's shape, where a row's
            # period and its observed day are two statements of one fact.
            periods = _future_periods(
                db.session, seed_user, count=6,
                start=display_today() - timedelta(days=28),
            )
            anchored = next(
                period for period in periods
                if period.start_date <= display_today() <= last_covered_day(period)
            )
            # Truncate through the period BEFORE the anchored one, so the
            # anchored period is inside the to-delete window.  Named by id
            # since plan step C3-a; it was ``anchored.period_index - 1``.
            previous = max(
                (p for p in periods if p.start_date < anchored.start_date),
                key=lambda p: p.start_date,
            )
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            restate_account_opening(db.session, savings, anchored.start_date)
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                savings.id,
            )
            db.session.commit()
            assert _opening_entry_period(db.session, savings.id) == anchored.id, (
                "the fixture must place the opening correction inside the "
                "window this case truncates, or it grades nothing"
            )
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=previous.id,
                )
            assert excinfo.value.blocking.get(anchored.id) == (
                PeriodLockReason.LEDGER_POSTINGS
            )
            assert _count_periods(db.session, user_id) == before

    def test_unbalanced_ledger_postings_block_and_delete_nothing(
        self, app, db, seed_user,
    ):
        """A period whose postings do not net to zero per ledger is hard-locked.

        The R2 defense-in-depth gate (the 2026-07-02 adversarial review):
        ``journal_entries.pay_period_id`` is ON DELETE CASCADE and the
        balanced-journal trigger never fires on DELETE, so truncating a period
        holding a NON-self-cancelling entry (the shape a loan opening /
        true-up correction has -- balanced across two ledger accounts, each
        with a non-zero net) would silently mis-state both accounts.  The
        entry here is built directly in that shape (no settled transaction,
        so the pre-existing SETTLED_TXN lock cannot mask the new gate):
        Checking -100 / Savings +100 in a to-delete period.  Truncate must
        refuse with LEDGER_POSTINGS and delete nothing.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            db.session.flush()
            _emit_untethered_entry(
                db.session, seed_user, savings, periods[2],
                Decimal("100.00"),
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )

            assert excinfo.value.blocking.get(periods[2].id) == (
                PeriodLockReason.LEDGER_POSTINGS
            )
            assert _count_periods(db.session, user_id) == before
            assert db.session.query(JournalEntry).filter_by(
                pay_period_id=periods[2].id,
            ).count() == 1

    def test_zero_netting_reversal_pair_does_not_block(
        self, app, db, seed_user,
    ):
        """A period whose entries fully cancel per ledger account may truncate.

        The other half of the R2 gate: after a settle-in-F,
        revert-and-move-to-P flow, F holds the original entry AND its reversal
        (the R2 attribution rule keeps the reversal in the period it
        reverses), netting every ledger account to zero.  Cascading that pair
        moves no account's sum, so the gate must NOT block -- truncating F
        succeeds, the moved (now Projected) transaction survives in P, and
        the whole-ledger trial balance stays zero.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            keep_period = seed_user["bootstrap_period"]
            txn = add_txn(
                db.session, seed_user, periods[2], "Early Bill", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            db.session.commit()
            posting_service.sync_transaction_postings(txn)
            db.session.commit()

            # The H1 mirror flow: revert AND move back to the kept period.
            txn.pay_period_id = keep_period.id
            txn.status_id = ref_cache.status_id(StatusEnum.PROJECTED)
            db.session.flush()
            posting_service.sync_transaction_postings(txn)
            db.session.commit()
            # F holds the self-cancelling pair; the source row left it.
            assert db.session.query(JournalEntry).filter_by(
                pay_period_id=periods[2].id,
            ).count() == 2

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
                confirm_discard=True,
            )

            assert deleted == 2
            # The moved transaction survives in the kept period; the cascade
            # removed the netted pair without moving any account's sum.
            assert db.session.get(Transaction, txn.id) is not None
            assert db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), Decimal("0")),
            ).scalar() == Decimal("0")
            assert_pay_period_invariants(db.session, user_id)

    def test_a_rules_stated_start_no_longer_blocks_a_truncate(
        self, app, db, seed_user,
    ):
        """The period a rule starts in is truncatable, and the bound survives.

        ``PeriodLockReason.RECURRENCE_ANCHOR`` hard-locked it until plan step
        R7b-4, and had to: ``start_period_id`` was ``ON DELETE SET NULL``, so
        truncating the period the FK named silently ERASED the rule's opening
        bound.  The bound is a DATE now, which no cascade reaches, so the
        truncate proceeds and the rule keeps saying what it said.

        Both halves are asserted, because deleting a hard lock is only safe if
        the thing it protected really does survive: the periods go, and the
        rule's ``starts_on`` is unchanged afterwards.

        **Two things rotted at plan step R7c-b and both are fixed here.**  The
        assertion read ``start_date``, a column the write door stopped writing
        -- so it read ``None`` forever and held for any truncate, including one
        that erased the bound.  And the rule was built by
        ``make_every_period_rule``, which authors ``starts_on`` at the
        SCHEDULE'S OPENING; the periods below start at 2026-07-03, so the rule
        no longer started in the truncated period at all and the case had
        stopped describing its own subject.  It is authored ON that period now.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            stated_start = periods[2].start_date  # index 3
            # The definition first, then the cadence onto it (plan step
            # R-F6): a rule carries its owner's FK, so it cannot be written
            # before there is an owner.
            rule = make_cadence_rule(
                bare_expense_template(db.session, seed_user), EVERY_PERIOD,
                starts_on=stated_start,
            )
            assert rule.starts_on == stated_start, (
                "the rule must start IN the period this truncates, or the "
                "case is not about the lock it names"
            )
            # Read the id BEFORE the delete: the instance is expired
            # afterwards and attribute access raises rather than answering.
            truncated_id = periods[2].id
            db.session.commit()

            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()

            assert db.session.query(PayPeriod).filter_by(
                id=truncated_id,
            ).one_or_none() is None
            assert rule.starts_on == stated_start


class TestTruncateDiscardGate:
    """The overridable discard gate (Discipline 4)."""

    def test_adhoc_row_requires_confirm_then_proceeds(self, app, db, seed_user):
        """A hand-entered row blocks without confirm, deletes with it."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            add_txn(db.session, seed_user, periods[2], "Cash", "50.00")
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodDiscardRequired) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )
            assert excinfo.value.count == 1
            assert _count_periods(db.session, user_id) == before

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
                confirm_discard=True,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_override_row_requires_confirm(self, app, db, seed_user):
        """A template row marked override needs confirmation."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            txn = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            txn.is_override = True
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )

    def test_cancelled_row_requires_confirm(self, app, db, seed_user):
        """A Cancelled template row needs confirmation (broadened gate).

        Cancelled is not settled, so it is not hard-locked, but the user's
        cancel decision is not reproducible by regeneration -- so the gate
        must warn before discarding it.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            txn = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )

    def test_a_rule_less_definitions_projected_row_requires_confirm(
        self, app, db, seed_user,
    ):
        """A row no rule would write back needs confirmation, link or not.

        Plan step balance:X-bi-7a, the transaction half of ledger row
        **BAL-492**: the gate's transaction arm read ``template_id IS NULL``
        in SQL, so a rule-less definition's row -- template-linked, Projected,
        not overridden -- was counted REGENERABLE and truncate promised back
        a row no rule will write.  The arm loads the rows and asks each one
        ``recurs`` now (the shape ruling **R-BAL19** gave the companion
        query), so this row counts exactly as the hand-entered one above.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            template = make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            template.recurrence_rule = None
            db.session.commit()
            kept = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            assert kept.template_id == template.id
            assert kept.is_override is False
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodDiscardRequired) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )
            # Two rows in the tail (periods 3 and 4), each unrecoverable.
            assert excinfo.value.count == 2
            assert _count_periods(db.session, user_id) == before

    def test_projected_template_rows_need_no_confirm(self, app, db, seed_user):
        """Plain projected template rows are regenerable -- no confirm gate."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_recurring_transfer_needs_no_confirm(self, app, db, seed_user):
        """A template transfer is regenerable; its shadows do not trip the gate.

        Transfer shadows always carry template_id IS NULL, so a naive
        ``template_id IS NULL`` gate would falsely flag every recurring
        transfer.  The refined predicate counts transfers on their own
        table, so a template transfer needs no confirmation.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_adhoc_transfer_requires_confirm(self, app, db, seed_user):
        """An ad-hoc (no-template) transfer is not regenerable -- confirm needed."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            _make_adhoc_transfer(db.session, seed_user, savings, periods[2])
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )
