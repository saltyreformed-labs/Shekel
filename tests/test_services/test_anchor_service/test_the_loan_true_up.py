"""The loan true-up door: what it commits, ruling R-EQ's unchanged re-submission, and an
IntegrityError that propagates.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.loan_anchor_service import apply_loan_anchor_true_up

from ._helpers import _make_loan_account


class TestApplyLoanAnchorTrueUpCommitted:
    """COMMITTED outcome: helper appends an event and commits."""

    def test_commits_appends_new_event_without_mutating_prior(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Helper writes a single new user_trueup event; prior rows untouched.

        Hand-check: fixture seeds exactly one origination event at
        $20,000 dated 365 days ago.  After
        :func:`apply_loan_anchor_true_up` with anchor $18,500 dated
        today:
          * Outcome is COMMITTED.
          * Total event count is 2 (origination + new trueup).
          * The new event has ``source_id`` == USER_TRUEUP id, the
            posted balance, and the posted date.
          * The origination event is byte-identical (no UPDATE).
            Compared by primary key + every persisted column to
            prove append-only semantics.
          * :class:`LoanParams` is unchanged: the trueup writes an
            event, never a params column (the balance has none since
            plan step R20 dropped the demoted seed).
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            origination = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .one()
            )
            orig_snapshot = (
                origination.id,
                origination.anchor_date,
                origination.anchor_balance,
                origination.source_id,
                origination.created_at,
            )
            params_before = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            params_snapshot = (
                params_before.original_principal, params_before.term_months,
                params_before.origination_date, params_before.payment_day,
                params_before.updated_at,
            )

            outcome = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("18500.00"),
                anchor_date=date.today(),
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .order_by(LoanAnchorEvent.id)
                .all()
            )
            assert len(events) == 2, (
                "Trueup must append exactly one new event."
            )

            origination_after = next(e for e in events if e.id == orig_snapshot[0])
            after_snapshot = (
                origination_after.id,
                origination_after.anchor_date,
                origination_after.anchor_balance,
                origination_after.source_id,
                origination_after.created_at,
            )
            assert after_snapshot == orig_snapshot, (
                "Prior origination event must NOT be mutated by a "
                "trueup (LoanAnchorEvent is structurally append-only)."
            )

            trueup = next(e for e in events if e.id != orig_snapshot[0])
            user_trueup_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            assert trueup.source_id == user_trueup_source_id
            assert trueup.anchor_balance == Decimal("18500.00")
            assert trueup.anchor_date == date.today()

            # The trueup must NOT mutate :class:`LoanParams`: the balance
            # is the event's, and the params row is not written at all.
            params_after = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            assert (
                params_after.original_principal, params_after.term_months,
                params_after.origination_date, params_after.payment_day,
                params_after.updated_at,
            ) == params_snapshot


class TestApplyLoanAnchorTrueUpUnchanged:
    """UNCHANGED: a resubmit of the governing (date, balance) is idempotent."""

    def test_double_call_same_balance_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical trueups produce exactly one new event row.

        Hand-check: :func:`apply_loan_anchor_true_up` called twice
        with the same ``(date, balance)`` returns ``COMMITTED`` then
        ``UNCHANGED``, and the event log shows exactly one trueup row
        (plus the origination).
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            outcome_first = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            outcome_second = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            assert outcome_second is AnchorTrueUpOutcome.UNCHANGED

            db.session.expire_all()
            trueups = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("17000.00"),
                    anchor_date=today,
                )
                .all()
            )
            assert len(trueups) == 1, (
                "A repeated submission must append nothing: expected exactly "
                "one event row at that (date, balance)."
            )

    def test_reasserting_a_superseded_balance_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Re-asserting a loan balance that was corrected away is a real change.

        The loan twin of the checking regression, and the one whose exposure is
        LIVE today: the loan form already lets the user type the date, so the
        deleted ``uq_loan_anchor_events_acct_date_bal_day`` could refuse this
        sequence on any recording day.  ``$17,000`` -> ``$16,500`` -> back to
        ``$17,000`` for one anchor date; the third changes what governs and
        must be recorded.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            for balance in ("17000.00", "16500.00", "17000.00"):
                assert apply_loan_anchor_true_up(
                    account=account,
                    anchor_balance=Decimal(balance),
                    anchor_date=today,
                ) is AnchorTrueUpOutcome.COMMITTED, (
                    f"Asserting ${balance} changed what governs, so it is a "
                    "fact and must be recorded."
                )

            db.session.expire_all()
            trueup_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            governing = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=trueup_source_id)
                .order_by(
                    LoanAnchorEvent.anchor_date.desc(),
                    LoanAnchorEvent.created_at.desc(),
                    LoanAnchorEvent.id.desc(),
                )
                .first()
            )
            assert governing.anchor_balance == Decimal("17000.00"), (
                "The correction back to $17,000.00 must be what governs; the "
                "deleted unique index left $16,500.00 governing while the "
                "route flashed that the balance was already recorded."
            )

    def test_back_dated_resubmit_is_idempotent(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-submitted BACK-DATED true-up appends nothing.

        **The defect the first version of ruling R-EQ's implementation had, and
        the reason the comparison is scoped to the submitted DATE.**  Comparing
        against the account's LATEST event instead, a submission for an earlier
        date can never compare equal -- so every double-click on a back-dated
        correction appended another row, permanently, in a table whose ORM
        guards refuse UPDATE and DELETE.  Two independent adversarial reviews
        reproduced it here, on the door that has had a user-supplied date field
        since Commit 16.

        Sequence: a true-up for TODAY, then one back-dated 10 days, then the
        back-dated one again.  The third governs the same date as the second and
        changes nothing, so it must write nothing -- even though a LATER event
        exists.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            back_dated = date.today() - timedelta(days=10)

            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("17000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("16000.00"),
                anchor_date=back_dated,
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("16000.00"),
                anchor_date=back_dated,
            ) is AnchorTrueUpOutcome.UNCHANGED, (
                "The re-submitted back-dated true-up changes nothing at its own "
                "date, so it must append nothing -- comparing against the LATEST "
                "event instead makes every back-dated retry a duplicate."
            )

            db.session.expire_all()
            rows = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_date=back_dated,
                    anchor_balance=Decimal("16000.00"),
                )
                .all()
            )
            assert len(rows) == 1, (
                f"Expected exactly one event at the back-dated key, found "
                f"{len(rows)} -- each surplus row renders a phantom line on the "
                f"loan dashboard's drift card and cannot be deleted."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day trueups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) MUST NOT be blocked.  Both calls
        return ``COMMITTED``; the resolver's (anchor_date, created_at) DESC
        ordering naturally picks the later one for display.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            outcome_a = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            outcome_b = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("16500.00"),
                anchor_date=today,
            )
            assert outcome_a is AnchorTrueUpOutcome.COMMITTED
            assert outcome_b is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            balances = {
                row.anchor_balance for row in
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .all()
            }
            assert Decimal("17000.00") in balances
            assert Decimal("16500.00") in balances


class TestApplyLoanAnchorTrueUpReraisesUnknownIntegrityError:
    """An unexpected ``IntegrityError`` propagates rather than being swallowed.

    The loan twin of the checking class above.  Its ``except IntegrityError``
    lived in ``loan_posting_service.sync_all_scenarios_or_duplicate``, which the
    loan anchor path stopped calling at plan step X-f1c4b (the ARM rate change kept that
    translation, its EDITABLE table's unique key being a real business rule, and holds it
    itself since plan task plan#3076).  The contract it protected is graded here directly.
    """

    def test_integrity_error_from_the_commit_is_not_swallowed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ``IntegrityError`` raised by the door's commit reaches the caller."""
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            # At the commit, as the checking twin is (it patched the loan re-sync until plan#3076).
            with patch.object(
                db.session, "commit",
                side_effect=IntegrityError("stmt", {}, Exception("boom")),
            ):
                with pytest.raises(IntegrityError):
                    apply_loan_anchor_true_up(
                        account=account,
                        anchor_balance=Decimal("19000.00"),
                        anchor_date=today,
                    )

            db.session.rollback()
            trueups_at_balance = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("19000.00"),
                )
                .all()
            )
            assert not trueups_at_balance, (
                "The failed true-up must not have committed: the event was "
                "staged in the transaction the IntegrityError aborted."
            )
