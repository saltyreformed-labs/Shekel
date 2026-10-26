"""Grid route tests: the settle day's lifecycle, and what a reverted row shows a re-pay will book
in the grid.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.services import balance_at, posting_service, status_seam, transaction_service
from app.services.balance_at import BalanceContext
from app.utils.dates import display_today

from tests._test_helpers import (
    typed as typed_figure,
    an_entered_day,
    current_pay_period,
    family_journal_filter,
    field_is_disabled,
    net_posted_by_day,
    one_off_row_of,
    settlement_if_settling,
)


class TestSettleDayLifecycle:
    """Tests for settled_on management during status changes, at the route."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Create a projected expense transaction for testing."""
        from app import ref_cache
        from app.enums import TxnTypeEnum

        expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

        txn = one_off_row_of(
            seed_periods_today[0],
            name="Test Expense",
            amount=Decimal("100.00"),
            user_id=seed_periods_today[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type_id,
            category_id=seed_user["categories"]["Rent"].id,
            due_date=seed_periods_today[0].start_date,
        )
        db.session.commit()
        return txn

    def _create_income_txn(self, seed_user, seed_periods_today):
        """Create a projected income transaction for testing."""
        from app import ref_cache
        from app.enums import TxnTypeEnum

        income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

        txn = one_off_row_of(
            seed_periods_today[0],
            name="Test Income",
            amount=Decimal("2000.00"),
            user_id=seed_periods_today[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=income_type_id,
            category_id=seed_user["categories"]["Salary"].id,
            due_date=seed_periods_today[0].start_date,
        )
        db.session.commit()
        return txn

    def test_settle_day_set_on_mark_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done records the settle day for expenses."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on == display_today()

    def test_settle_day_set_on_mark_received(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done records the settle day for income."""
        with app.app_context():
            txn = self._create_income_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on == display_today()

    def test_settle_day_cleared_on_status_revert(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> reverting to Projected clears the settle day."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done to record the settle day.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on is not None

            # Revert to projected via PATCH.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on is None

    def test_re_mark_after_a_revert_sets_a_fresh_settle_day(self, app, auth_client, seed_user, seed_periods_today):
        """Mark done, revert to projected, mark done again -- the day is set both times.

        A revert genuinely CLEARS the day (the row is no longer settled, so the
        invariant says it carries none), and the next settle stamps a fresh one.
        The assertion is against ``display_today()`` rather than against the
        first day: this row is settled twice under one clock, so "the second is
        not earlier than the first" is true by construction and would hold even
        if the seam had never cleared it.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark done.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

            # Revert to projected.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            db.session.refresh(txn)
            assert txn.settled_on is None

            # Mark done again -- a FIRST entry into the settled band, because
            # the revert cleared the day, so the seam stamps today afresh.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

    def test_settle_day_not_set_on_non_settling_status_change(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/cancel records no settle day."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on is None

    def test_settle_day_preserved_on_non_status_update(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transactions/<id> editing a non-status field keeps the day.

        Edits a display field (``notes``) rather than ``estimated_amount``:
        the finalised-row edit lock (#26) refuses money/period/category edits on
        a Paid row, but display fields stay editable, so this is the faithful
        probe that a non-status edit neither clears nor moves the settle day
        (the seam clears it only on a status change out of the settled band).

        **It could not fail until this was written** (finding **N-182**'s
        sibling, found by a neutral review that PROVED it: with
        ``_apply_regular_update`` wrapped to move every settled row it touched
        by 7 days, this class stayed green and so did 703 tests around it).  It
        captured the original day and then asserted only ``is not None``, so it
        graded "the row still has a day" where its own name promises "the row
        still has THAT day" -- and the difference is money, because since plan
        step E1a the settle day IS the ``entry_date`` its postings are filed
        under.  **This is N-146's class on the transaction side**: an ordinary
        notes edit moving a settled payment's money to today, which is exactly
        the live production defect that opened X-aj1 on the transfer side.

        So it back-dates THROUGH the seam first and asserts the LEDGER as well
        as the column, the same shape as
        :meth:`test_a_replayed_mark_done_does_not_re_date_the_money`.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum
            from app.models.journal_entry import JournalEntry
            from app.services import posting_service, status_seam

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done to record the settle day.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

            # Back-date THROUGH the seam and re-reconcile, so the posted ledger
            # follows the column and the assertions below start from the state a
            # genuinely week-old settle is really in.
            settled_a_week_ago = display_today() - timedelta(days=7)
            status_seam.apply_status_change(
                txn,
                ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_entered_day(settled_a_week_ago),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            posting_service.sync_transaction_postings(txn)
            db.session.commit()

            def _ledger_days():
                # The row's family, as ``_ledger_days_for`` reads it.
                return sorted(
                    entry.entry_date
                    for entry in db.session.query(JournalEntry)
                    .filter(family_journal_filter(txn))
                    .all()
                )

            days_before = _ledger_days()
            assert days_before, "fixture posted no journal entry to grade"

            # Edit a non-status, non-locked field -- no status change.
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"notes": "Reconciled against statement"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.notes == "Reconciled against statement"
            assert txn.settled_on == settled_a_week_ago, (
                "A notes-only edit re-dated the settle: "
                f"{settled_a_week_ago} -> {txn.settled_on} (finding N-146)."
            )
            assert _ledger_days() == days_before, (
                "A notes-only edit moved the posted ledger: "
                f"{days_before} -> {_ledger_days()} (finding N-146)."
            )

    def test_a_replayed_mark_done_does_not_re_date_the_money(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A replayed mark-done leaves a settled row's money where it is.

        An idempotent re-mark (Paid -> Paid) keeps the ORIGINAL settle day: the
        seam stamps the user's today only on the first entry into a settled
        status and leaves an existing day untouched on a re-settle.  ``done ->
        done`` is a legal transition and this route does not gate on status, so
        a stale page, a second tab or a replayed POST really does re-submit the
        settle.

        **The row is back-dated THROUGH the seam first, and that is what makes
        this control able to fail** (finding **N-182**).  Settling twice under
        one clock leaves both calls yielding the same ``display_today()``, so
        the old ``second == first`` assertion was true by construction --
        measured, it passed with the preserve arm deleted.  This is the
        transaction-side mirror of
        ``test_transfers/test_ad_hoc.py::test_re_marking_a_settled_transfer_does_not_re_date_it``,
        which back-dates through ``update_transfer`` for the same reason.

        It asserts the LEDGER as well as the column, because since plan step
        E1a the settle day IS the ``entry_date`` the row's postings are filed
        under -- so a re-stamp does not merely churn a field, it moves the
        money (finding **N-146**'s class).
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum
            from app.models.journal_entry import JournalEntry
            from app.services import posting_service, status_seam

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark done.
            resp1 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp1.status_code == 200
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

            # Back-date THROUGH the seam and re-reconcile, so the posted ledger
            # follows the column.  Writing the attribute directly would leave
            # the entries dated today and the ledger assertion below could pass
            # on a stale ledger rather than on a preserved one.
            settled_a_week_ago = display_today() - timedelta(days=7)
            status_seam.apply_status_change(
                txn,
                ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_entered_day(settled_a_week_ago),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            posting_service.sync_transaction_postings(txn)
            db.session.commit()

            def _ledger_days():
                # The row's family, as ``_ledger_days_for`` reads it.
                return sorted(
                    entry.entry_date
                    for entry in db.session.query(JournalEntry)
                    .filter(family_journal_filter(txn))
                    .all()
                )

            days_before = _ledger_days()
            assert days_before, "fixture posted no journal entry to grade"

            # The replay (idempotent Paid -> Paid).
            resp2 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp2.status_code == 200
            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)

            assert txn.settled_on == settled_a_week_ago, (
                "A replayed mark-done re-dated the settle: "
                f"{settled_a_week_ago} -> {txn.settled_on} (finding N-146)."
            )
            assert _ledger_days() == days_before, (
                "The replayed mark-done moved the posted ledger: "
                f"{days_before} -> {_ledger_days()} (finding N-146)."
            )

    def _ledger_days_for(self, txn_id):
        """Return the ``entry_date`` of every journal entry linked to *txn_id*.

        The LEDGER half of every settle-day assertion below.  Ruling **R-ED**
        names it as this half's gate in terms: the test must assert that the
        ledger FOLLOWED, not merely that the column changed -- since plan step
        E1a the settle day IS the ``entry_date`` a row's postings are filed
        under, so a column that moves without the ledger is the books silently
        disagreeing with the screen.
        """
        from app.models.journal_entry import JournalEntry

        # The row's FAMILY (plan step X-bi-3a): a settled bill's money is
        # posted under its covering movement, so the ledger half reads the
        # row and its mirror together -- every day asserted below is unchanged.
        return sorted(
            entry.entry_date
            for entry in db.session.query(JournalEntry)
            .filter(family_journal_filter(txn_id))
            .all()
        )

    def _settled_txn_dated(self, seed_user, seed_periods_today, day):
        """Return a settled transaction whose money moved on *day*, ledger in step.

        Built THROUGH the seam and reconciled, so the fixture's own postings
        carry *day* -- a row dated by a bare attribute write would leave the
        ledger at today and let a "the ledger followed" assertion pass on a
        stale comparison rather than on the edit under test.
        """
        from app import ref_cache
        from app.enums import StatusEnum
        from app.services import status_seam

        txn = self._create_test_txn(seed_user, seed_periods_today)
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE), settle_day=an_entered_day(day),
            settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
        )
        posting_service.sync_transaction_postings(txn)
        db.session.commit()
        assert txn.settled_on == day
        assert self._ledger_days_for(txn.id) == [day], (
            "fixture did not post its entry at the settle day"
        )
        return txn

    def test_editing_the_settle_day_moves_the_ledger_with_it(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH ``settled_on`` on a settled row re-dates its postings (R-ED).

        The correction ruling **R-ED** exists for: the user read their
        statement and the money moved on a different day than the one-click
        settle recorded.  ``settled_on`` is in
        ``mutations._POSTING_RELEVANT_FIELDS``, so the per-``(period,
        entry_date)`` reconcile reverses the stale-dated entry at ITS own day
        and re-posts the effect at the corrected one (finding **N-13**).

        The assertion is on the LEDGER, which is R-ED's stated gate.  The
        corrected day must be present and the original must be net-zero
        (reversal + original), never simply absent -- the reversal is history,
        not an erasure.
        """
        with app.app_context():
            original_day = display_today() - timedelta(days=6)
            corrected_day = display_today() - timedelta(days=2)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, original_day,
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": corrected_day.isoformat()},
            )
            assert response.status_code == 200

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == corrected_day

            # The WHOLE effect must now sit at the corrected day and nothing at
            # the original.  The original day's ENTRIES survive as history --
            # the reconcile adds a reversal rather than erasing them -- so the
            # raw list of ``entry_date`` values still contains it; only the NET
            # separates "re-dated" from "posted twice", which is why the
            # assertion is on the net and not on membership.
            assert self._net_by_day(txn.id) == {corrected_day: Decimal("100.00")}, (
                "the settle day moved but the posted ledger did not follow: "
                f"net effect by day is {self._net_by_day(txn.id)}, expected the "
                f"whole $100.00 at {corrected_day} and nothing left at "
                f"{original_day}"
            )
            assert original_day in self._ledger_days_for(txn.id), (
                "the original day's entries vanished instead of reversing -- a "
                "correction restates history, it does not erase it"
            )

    def _net_by_day(self, txn_id):
        """Return ``{entry_date: net posted magnitude}`` for one transaction.

        Thin wrapper over the shared
        :func:`tests._test_helpers.net_posted_by_day` -- the transfer suite asks
        the same question through the same reduction with a different filter
        clause, and carrying two copies is the duplication R0801 cannot see
        because it does not run on ``tests/``.
        """
        return net_posted_by_day(family_journal_filter(txn_id))

    def test_reverting_to_projected_ignores_the_submitted_settle_day(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The unlock path survives the form re-submitting the row's own day.

        Ruling **R-EG**.  The full-edit form submits every enabled field, and
        the documented way to unlock a finalised row is to set Status to
        Projected in that same form -- so a revert arrives carrying the settle
        day the row already had.  The seam's
        ``reject_settle_day_without_settled_status`` refuses that pair with a
        400 (correctly, for a service caller asserting both facts on purpose),
        and applying that refusal here would break the ONLY unlock path on
        every settled row.

        ``status_seam.settle_day_for_status`` drops the day instead: the user
        picked Projected, which says the money did not move.  Graded on the
        400 NOT happening, on the column being cleared, and on the ledger
        being reversed -- a revert that left postings behind would be the
        balance keeping money the user just said was never spent.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            settled_day = display_today() - timedelta(days=3)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, settled_day,
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    # Exactly what the rendered form re-submits: the day the
                    # row already carries, untouched by the user.
                    "settled_on": settled_day.isoformat(),
                },
            )
            assert response.status_code == 200, (
                "the documented unlock path (Status -> Projected) was refused "
                f"because the form re-submitted the row's own settle day: "
                f"{response.get_data(as_text=True)[:300]}"
            )

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None, (
                "the revert kept the settle day, breaking the settled-iff-dated "
                "invariant"
            )
            # The WHOLE mapping, not just the old day.  Checking only that the
            # settled day cleared would pass a revert that reversed that day and
            # re-posted the effect at some OTHER date -- money still spent, just
            # moved.  A neutral review found this side asserting the weak form
            # while its transfer sibling asserted the strong one, and the
            # sibling's docstring claiming the asymmetry had already been fixed.
            assert self._net_by_day(txn.id) == {}, (
                "reverting to Projected left a posted effect somewhere"
            )

    def test_a_settle_day_cannot_date_a_projected_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A day submitted for a Projected row is dropped, not written.

        Finding **N-185**'s structural half.  ``settled_on`` is now exactly as
        load-bearing as ``status_id`` -- the invariant binds them -- so the
        PATCH handler's generic ``setattr`` loop must never see it: a bare
        ``setattr`` would date a row whose money has not moved, which is
        finding **N-183** re-run on the transaction side.  ``_SEAM_OWNED_FIELDS``
        excludes it from the loop and the seam is the only writer.

        This is the falsifiable form of that claim: remove ``settled_on`` from
        ``_SEAM_OWNED_FIELDS`` and the loop dates this Projected row.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": (display_today() - timedelta(days=1)).isoformat()},
            )
            assert response.status_code == 200

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on is None, (
                "a Projected row was given a settle day -- the PATCH setattr "
                "loop is writing the column the seam owns (finding N-185)"
            )
            assert self._ledger_days_for(txn.id) == [], (
                "a Projected row posted to the ledger"
            )

    def test_a_future_settle_day_is_refused_and_moves_no_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day that has not happened is refused, and the balance holds.

        Ruling **R-EJ**.  A settled source counts from its own ``settled_on``,
        and ``walk_cash_ledger`` absorbs one into an assertion only when the
        assertion is dated ON OR AFTER it -- so a future-dated settle rides on
        top of every assertion until that day arrives, putting already-spent
        money back in the rendered balance.  Measured on the live route before
        the guard existed: a ``$100`` expense settled three days ago read
        ``$900`` against a ``$1,000`` anchor, and PATCHing its day to
        ``today + 400`` answered **200** with the balance back at ``$1,000``.

        **It is the LIKELY input, not an exotic one.**  The box tells the user
        to correct the day against their statement, and a statement's most
        common disagreement is a PENDING item carrying a future posting date.

        The BALANCE is asserted, not just the status code: a 400 that still let
        the write through would pass a status-only check, and the balance is the
        thing the defect actually moved.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=3)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, settled_day,
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "settled_on": (display_today() + timedelta(days=400)).isoformat(),
                },
            )
            assert response.status_code == 400, (
                f"a settle day 400 days out was accepted: {response.status_code}"
            )

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == settled_day, (
                "the refused day was written anyway"
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            ) == before, (
                "the refused future settle moved the rendered balance -- "
                "already-spent money came back"
            )

    def test_a_mistyped_year_is_refused_and_moves_no_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A settle day before the schedule is refused at the DOOR.

        Ruling **R-EL**, the mirror of R-EJ above and the input it was decided
        on.  A day at or before an assertion is ABSORBED into it by
        ``walk_cash_ledger``, which then resets the running total to the
        asserted balance -- so the row's delta is discarded and the projection
        RISES by its amount while the row still reads Paid.
        ``fields.Date()`` deserializes ``"0202-08-04"`` to a real ``date``, so a
        mistyped YEAR is an ordinary slip in a box whose own tooltip invites
        correction.

        **The bound is at the door, not at the seam**, because a genuine
        pre-schedule settle is legitimate -- money moved before you started
        budgeting, which a bank import produces in bulk, and which the seam must
        keep accepting (``test_status_seam.TestTheSettleDayFloor``).  What is
        never legitimate is a human typing a year wrong.

        The BALANCE is asserted, not just the status code, for the reason the
        R-EJ case above gives: a 400 that still let the write through would pass
        a status-only check.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=3)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, settled_day,
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": "0202-08-04"},
            )
            assert response.status_code == 400, (
                "a settle day in the year 202 was accepted: "
                f"{response.status_code}"
            )

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == settled_day, (
                "the refused day was written anyway"
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            ) == before, (
                "the mistyped year moved the rendered balance -- spent money "
                "came back out of the projection"
            )

    def test_todays_settle_day_is_accepted_at_the_boundary(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """TODAY is on the allowed side of the future refusal.

        The boundary control for ruling **R-EJ**: the guard is ``>`` today, not
        ``>=``, because settling something today is the ordinary case and a
        one-click settle stamps exactly this day.  Without this, a refusal
        written with the wrong comparison would pass the test above and break
        every settle.
        """
        with app.app_context():
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today,
                display_today() - timedelta(days=4),
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": display_today().isoformat()},
            )
            assert response.status_code == 200, response.get_data(as_text=True)[:300]

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on == display_today()

    def test_full_edit_offers_the_settle_day_only_on_a_settled_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The correction input renders for a settled row and not before.

        A Projected row's money has not moved, so there is no day to state and
        offering the box would invite a forecast in a fact column -- the same
        rule ``_transaction_entries.html`` applies to a purchase's posting date.
        The paired assertions are what make this a test of the CONDITION rather
        than of the field merely existing somewhere.

        **The settle day is moved OFF today before the pre-fill is graded, and
        that is load-bearing.**  ``mark-done`` stamps today, and the template
        also renders ``max="<today>"`` -- so a row settled today puts today's
        ISO string in the body TWICE, and ``today in body`` passes with
        ``value=""``.  A neutral review proved exactly that.  Re-dating first
        makes the assertion grade ``value=`` alone, and the ``max`` is asserted
        separately below so neither hides the other.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            projected_response = auth_client.get(
                f"/transactions/{txn.id}/full-edit",
            )
            assert projected_response.status_code == 200
            projected_body = projected_response.get_data(as_text=True)
            assert 'name="settled_on"' not in projected_body

            auth_client.post(f"/transactions/{txn.id}/mark-done")
            # Off today, so the pre-fill cannot be satisfied by the ``max``.
            corrected = display_today() - timedelta(days=6)
            auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": corrected.isoformat()},
            )
            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == corrected

            settled_response = auth_client.get(
                f"/transactions/{txn.id}/full-edit",
            )
            assert settled_response.status_code == 200
            settled_body = settled_response.get_data(as_text=True)
            assert 'name="settled_on"' in settled_body
            assert f'value="{corrected.isoformat()}"' in settled_body, (
                "the correction input is not pre-filled with the stored day"
            )
            # The browser-side half of ruling R-EJ, and it must be the USER's
            # today rather than the process's: a display-vs-UTC split would let
            # the input refuse a day the seam accepts.  Distinct from the stored
            # day above, so one cannot satisfy the other.
            assert f'max="{display_today().isoformat()}"' in settled_body, (
                "the settle-day input does not bound itself at the user's today"
            )
            # It must NOT be disabled on a finalised row -- that is exactly the
            # distinction ruling R-ED draws against the money/period fields
            # beside it, and a disabled input submits nothing.
            assert not field_is_disabled(settled_body, "settled_on"), (
                "the settle day is locked on a finalised row, so the "
                "correction R-ED exists for is unreachable"
            )

    def test_settle_day_stamped_on_patch_settle(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transactions/<id> settling to Paid records the day (the C5 fix).

        Before the status seam, the inline PATCH path assigned status_id without
        recording the day, leaving a Paid row with no settle day -- it
        bypassed the now()-stamp that mark_done and transfers apply.  The seam
        now owns the settle day for every status change, so a PATCH that settles a
        Projected row records the timestamp the same way mark_done does.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            done_id = ref_cache.status_id(StatusEnum.DONE)
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(done_id)},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status_id == done_id
            assert txn.settled_on is not None


class TestARevertedRowShowsWhatARePayWillBook:
    """The developer's 2026-08-17 rule: one figure, shown everywhere.

    A row reverted out of the settled band KEEPS what it recorded and a
    re-settle honours it, so its PLAN is what the balance counts and its
    RETAINED figure is what marking it paid will book.  Those are two different
    numbers about one row, and until this the second was visible on no surface
    but the reconcile panel: the grid and the full-edit popover both showed a
    ``$500.00`` plan for a row that would book ``$245.32``.

    Both surfaces are driven through their real routes, because the map has to
    reach each of them and they build their context differently -- the page
    assembles it and the popover fragment takes it from
    :class:`app.routes._render_helpers.RenderAmounts`.
    """

    @staticmethod
    def _reverted_corrected_row(seed_user, period):
        """Settle a bill at a human's figure, then revert it -- through the doors."""
        txn = one_off_row_of(
            period,
            name="Reverted Bill",
            amount=Decimal("500.00"),
            user_id=period.user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=seed_user["categories"]["Rent"].id,
        )
        db.session.flush()
        transaction_service.settle_transaction(
            txn, submitted=typed_figure(Decimal("245.32")),
        )
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()
        return txn

    def test_the_grid_cell_shows_the_retained_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The plan is still the row's value; the retained figure is beside it.

        The plan is what the balance column counts, so it stays in the money
        slot -- replacing it with a figure no balance uses would make the cell
        disagree with the column it sits in.  The retained figure rides as its
        own badge, captioned with what a tick will do.
        """
        with app.app_context():
            period = current_pay_period(
                seed_user["user"].id,
            )
            self._reverted_corrected_row(seed_user, period)

        html = auth_client.get("/grid").data.decode()

        assert "245.32" in html, (
            "the grid never showed the figure a re-pay will book"
        )
        assert "Marking this paid will record" in html
        # **And the PLAN is still the row's value in the money slot.**  Without
        # this half a build that swapped one figure for the other passes -- and
        # that build is wrong: the balance column counts the plan, so a cell
        # showing the retained figure as the row's amount would disagree with
        # the column it sits in.  (Rendered by ``money(..., cents=false)``, so
        # the desktop cell reads ``500`` rather than ``500.00``.)
        assert "500" in html, (
            "the plan left the money slot: the cell now disagrees with the "
            "balance column beside it"
        )

    def test_the_full_edit_popover_shows_the_retained_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The surface that TELLS the user to revert must say what that costs.

        This card's own hint reads "Set Status to Projected to edit the
        amounts", so the row a user is most likely looking at here is exactly
        the one carrying a retained figure.
        """
        with app.app_context():
            period = current_pay_period(
                seed_user["user"].id,
            )
            txn_id = self._reverted_corrected_row(seed_user, period).id

        resp = auth_client.get(f"/transactions/{txn_id}/full-edit")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "245.32" in html
        assert "Marking this paid will record" in html

    def test_an_ordinary_projected_row_shows_no_such_marker(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control: the marker is drawn for a REAL gap only.

        Without this, a build that rendered the badge unconditionally -- or one
        that put the plan in it -- would pass the two cases above while adding
        noise to every row on the grid.
        """
        with app.app_context():
            period = current_pay_period(
                seed_user["user"].id,
            )
            one_off_row_of(
                period,
                name="Ordinary Bill",
                amount=Decimal("500.00"),
                user_id=period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

        html = auth_client.get("/grid").data.decode()
        assert "Marking this paid will record" not in html
