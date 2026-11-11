"""Lifecycle tests for a transaction's ledger lines through its doors (Step 3, Commit 6).

Commit 6 wired :func:`~app.services.posting_service.sync_transaction_postings`
into every ordinary-transaction effect boundary -- the mark-done / PATCH /
cancel / delete routes, the envelope entry-mutation service, the credit
payback-delete paths, and carry-forward -- and plan task plan#3097 took it out
of every one of them: no transaction door writes the stored ledger.  These
tests drive transactions END TO END through those handlers and assert the
ledger worked out from the records, which is what settling, editing,
reverting, deleting and carrying forward a transaction still move (plan step
plan#2914).

The load-bearing properties, each pinned by a test below (until plan task plan#3097
the store's sync held the first and third by running LAST and gated):

  * a manual ``actual_amount`` books the ACTUAL not the estimate, and a
    settle-and-recategorize PATCH books the NEW category (the 2.8b HIGH, both
    directions);
  * a revert-and-recategorize PATCH leaves neither the OLD category nor the
    new ``category_id`` holding the row (the 2.8 CRITICAL), at the route level;
  * a metadata-only edit moves nothing;
  * an envelope books its debit-only effect (``effective - Sigma(credit)``), and
    an entry mutation on a posted envelope moves its ledger figure;
  * every delete path (hard / soft, the settled-payback unmark, and a recursive
    payback chain) leaves the accounts where they were before the settle;
  * each envelope source that carry-forward settles books its confirmed effect.

After each mutation the per-account reconciliation invariant is asserted in
its Build-Order Step 5 ABSOLUTE form: the account's worked-out net ``== opening
anchor + settled_transfer_effect + posted_purchase_effect``.  The read books
the seed Checking's $1000.00 opening the day before its origination assertion, and every
settle in this suite is stamped at SERVER now (the seam's ``db.func.now()``,
including carry-forward's ``settle_from_entries`` default) -- after the
origination assertion -- so the settled effects ride on top of the opening
and the Checking totals below are ``1000.00 + effect``.  All money is
``Decimal`` from strings, with the arithmetic shown per the testing standard.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern;
# test bodies bind fixtures (``auth_client``, ``seed_user``, ...) by name.
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.balance_at import BalanceContext
from app.services import carry_forward_service, credit_workflow, entry_service
from app.services.entry_service import EntryDetails
from app.services.ledger_lines import category_key, linked_key
from tests._ledger_reads import (
    account_net,
    family_movement_ids,
    ledger_net,
    period_nets_of_movements,
    scenario_lines,
)
from tests._records_oracle import posted_purchase_effect, settled_transfer_effect
from tests._test_helpers import (
    add_txn,
    an_entered_day,
    create_envelope_txn,
    figure_source_columns,
    reassert_balance_on,
    settle_instant_on,
    typed,
)
from tests.test_routes.test_cc5_4a5_popover_presses import dialog_delete_values
from app.services import cash_ledger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _category_chart_key(seed_user, category_name, ledger_class):
    """Return the chart key of a seed category's account."""
    return category_key(seed_user["categories"][category_name].id, ledger_class)


def _cc_payback_chart_key(seed_user):
    """Return the chart key of the Expense account of the user's CC Payback category."""
    cc_category = credit_workflow.get_or_create_cc_category(seed_user["user"].id)
    return category_key(cc_category.id, LedgerAccountClassEnum.EXPENSE)


def _ledger_total(seed_user, key):
    """Return the worked-out net of chart account *key* in the owner's baseline."""
    return ledger_net(db.session, key, seed_user["scenario"].id)


def _family_nets(transaction_id, scenario_id):
    """Return ``{period id: {chart key: net}}`` over the lines of the row's movements."""
    return period_nets_of_movements(family_movement_ids(transaction_id), scenario_id)


def _purchase_lines(entry_id, scenario_id):
    """Return the worked-out lines of one purchase."""
    return [
        line for line in scenario_lines(db.session, scenario_id)
        if line.movement_id == entry_id
    ]


def _add_purchase(seed_user, txn, amount, *, is_credit=False):
    """Attach one purchase entry (debit or credit) to *txn* directly and flush.

    Bypasses ``entry_service`` so the envelope's pre-settle state can be built
    without triggering the entry-level payback workflow; the tests that exercise
    the entry-mutation hook call ``entry_service`` explicitly instead.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="purchase",
        purchased_on=txn.pay_period.start_date,
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _assert_reconciles(scenario_id, *accounts):
    """Assert ledger == opening + settled-source effect (the absolute oracle).

    The per-account reconciliation invariant in its Step-5 ABSOLUTE form: the
    net of an account's ledger lines equals its opening anchor correction (its
    opening equity, equal to the origination row's balance, the one assertion
    here) plus the net effect of its settled, non-deleted transfers AND
    ordinary transactions.  Every settle in this suite
    is stamped at server-now, after the assertion instant, so the effects
    ride on top of the opening.  Asserted from independent producers so any
    divergence between the ledger and the source rows fails loudly.

    **"Rides on top of the opening" is a PRECONDITION, not a claim**, and since
    plan step X-f3b one test breaks it deliberately: a purchase's cash leg is
    dated the day the BANK took the money, which may be on or before an
    assertion, in which case that assertion's correction absorbs it and the
    ledger lands on the asserted balance rather than on opening + effect.  That
    test asserts the figure directly and says so at the call site.

    **TWO terms since plan step ``balance:X-bi-4a``** (ruling **R-BAL80**): a
    transfer's settled effect (off the income shadow's record then, off the
    side's record and the transfer's one figure since plan step
    ``balance:X-bi-6-4d-2``), and every DATED
    movement of a contributing non-transfer parent (``posted_purchase_effect``,
    keyed on the movement's own account).  A plan row posts nothing of its
    own, so the transaction source's term (``settled_transaction_effect``,
    whose ``effective - credit`` sum was what the parent's leg and its
    purchases' legs added up to) went with the leg.
    """
    for account in accounts:
        posted = account_net(db.session, account.id, scenario_id)
        effect = (
            settled_transfer_effect(account.id, scenario_id)
            + posted_purchase_effect(account.id, scenario_id)
        )
        opening = Decimal(str(cash_ledger.resolve_anchor(account).balance))
        assert posted == opening + effect, (
            f"account {account.id}: ledger {posted} != opening {opening} "
            f"+ source effect {effect}"
        )


# ---------------------------------------------------------------------------
# mark-done route: settling books the confirmed effect, actuals included
# ---------------------------------------------------------------------------


class TestMarkDoneRoutePostsTransaction:
    """A transaction the mark-done route settles books its cash split."""

    def test_plain_expense_mark_done_posts(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $50 Groceries expense, marked Paid, posts -50 / +50.

        Arithmetic (plan Section 1): a plain expense has no entries, so the
        effect is ``effective_amount`` (50) with the expense sign -- cash leg
        -50.00 (money leaving Checking, a credit) and Groceries-Expense leg
        +50.00 (the expense lands, a debit).  -50 + 50 = 0.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            assert _family_nets(txn_id, scenario_id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("-50.00"),
                _category_chart_key(
                    seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
                ): Decimal("50.00"),
            }}
            _assert_reconciles(scenario_id, checking)

    def test_income_mark_done_posts(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $2000 Salary income, marked Received, posts +2000 / -2000.

        Arithmetic (plan Section 1): income has no entries, so the effect is
        ``effective_amount`` (2000) with the INCOME sign -- cash leg +2000.00
        (money entering Checking, a debit) and Salary-Income leg -2000.00 (a
        credit).  The sign follows the transaction TYPE, never the account class.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Salary", "2000.00",
                is_income=True, category_key="Salary",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            assert _family_nets(txn_id, scenario_id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("2000.00"),
                _category_chart_key(
                    seed_user, "Salary", LedgerAccountClassEnum.INCOME,
                ): Decimal("-2000.00"),
            }}
            _assert_reconciles(scenario_id, checking)

    def test_mark_done_manual_actual_posts_actual_not_estimate(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A manual ``actual_amount`` posts the ACTUAL, not the estimate (2.8b HIGH).

        Arithmetic: the row is estimated at 50 but settled with a manual actual
        of 75, and the settle records its covering movement at that actual:
        cash leg -75.00, not -50.00.  (The store's reconcile ran AFTER
        ``actual_amount`` was applied, until plan task plan#3097, so that it was
        not fired at the status flip, which would have booked the stale 50.)
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "75.00"},
            )
            assert resp.status_code == 200

            assert _family_nets(txn_id, seed_user["scenario"].id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("-75.00"),
                _category_chart_key(
                    seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
                ): Decimal("75.00"),
            }}
            _assert_reconciles(seed_user["scenario"].id, checking)


# ---------------------------------------------------------------------------
# PATCH route: settle / recategorize / revert / metadata-only
# ---------------------------------------------------------------------------


class TestPatchPostingLifecycle:
    """What the inline-edit PATCH route's fields move on the ledger."""

    def test_settle_and_recategorize_patch_posts_new_category(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A settle+recategorize PATCH books the NEW category, not the old one.

        A Projected Groceries expense is settled and recategorized to Rent in
        ONE PATCH.  ``category_id`` is applied in the setattr loop and
        ``status_id`` through the seam, and the read books the row's current
        ``category_id`` = Rent: cash -50.00 / Rent-Expense +50.00, with the
        Groceries ledger never touched.  (While the store's reconcile ran LAST,
        until plan task plan#3097, its ordering proof for a field applied
        *after* the seam was the manual-``actual_amount`` test above;
        ``category_id`` here is set pre-seam, so this pins the recategorize-on-
        settle OUTCOME rather than isolating that ordering.)
        """
        with app.app_context():
            checking = seed_user["account"]
            done_id = ref_cache.status_id(StatusEnum.DONE)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id), "category_id": str(rent_id)},
            )
            assert resp.status_code == 200

            assert _ledger_total(seed_user, _category_chart_key(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            )) == Decimal("50.00")
            assert _ledger_total(seed_user, _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("950.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_revert_and_recategorize_reconciles(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Revert+recategorize clears the OLD category, not the new one (2.8 CRITICAL).

        A Paid Groceries expense (posted -50 / +50 Groceries) is reverted to
        Projected AND recategorized to Rent in ONE PATCH (the lock lifts on the
        revert).  Groceries nets back to zero (+50 -> 0), Rent stays untouched,
        and Checking nets back to its $1,000.00 opening.  A later re-settle then
        posts cleanly to Rent.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("50.00")

            # Revert + recategorize in one PATCH (the lock lifts on the revert).
            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(projected_id),
                    "category_id": str(rent_id),
                },
            )
            assert resp.status_code == 200
            rent_ledger = _category_chart_key(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            )
            # OLD category reversed to zero; NEW category never touched yet.
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("0.00")
            assert _ledger_total(seed_user, rent_ledger) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            _assert_reconciles(scenario_id, checking)

            # Re-settle: now it posts to Rent (the current category).
            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id)},
            )
            assert resp.status_code == 200
            assert _ledger_total(seed_user, rent_ledger) == Decimal("50.00")
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_notes_only_edit_posts_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A metadata-only PATCH on a Paid row leaves the ledger untouched.

        ``notes`` moves no record the worked-out ledger reads, so Checking
        still nets $950.00.  The PATCH door gated its store reconcile on
        ``_POSTING_RELEVANT_FIELDS`` until plan task plan#3097 deleted both;
        what this grades is the ledger, which never depended on the gate.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"notes": "reconciled at the bank"},
            )
            assert resp.status_code == 200
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("950.00")


# ---------------------------------------------------------------------------
# cancel route: never crosses the settled boundary -> posts nothing
# ---------------------------------------------------------------------------


class TestCancelPostsNothing:
    """Cancelling a Projected transaction is an idempotent ledger no-op."""

    def test_cancel_projected_posts_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Cancelling a never-posted Projected expense books nothing.

        Cancelled is non-settled and reachable only from Projected, so the row
        moves no money: Checking stays at its $1,000.00 opening.
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/cancel")
            assert resp.status_code == 200
            assert account_net(
                db.session, seed_user["account"].id, seed_user["scenario"].id,
            ) == Decimal("1000.00")


# ---------------------------------------------------------------------------
# Envelopes: debit-only effect + what an entry change moves; toggle_cleared no-op
# ---------------------------------------------------------------------------


class TestEnvelopePostingLifecycle:
    """An envelope books ``effective - Sigma(credit)``, and an entry edit moves it."""

    def test_envelope_mark_done_posts_debit_only(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 60 debit + 40 credit envelope: the dated $60 posts -60 / +60, the close nothing.

        Arithmetic (plan Section 1 / Decision D2, re-expressed under ruling
        **R-BAL77** at plan step ``balance:X-bi-4a``): the $60 debit purchase
        the bank took posts its own leg when it is dated -- cash -60.00 /
        Groceries-Expense +60.00 -- and the close books NOTHING: an un-dated
        purchase is in flight, not the row's leg, and a plan row posts nothing
        of its own (ruling **R-BAL80**).  The $40 credit posts nothing here.
        Through ``X-bi-3e`` the close booked ``sum(entries) - credit = 60``
        as the row's leg; the same family net, one writer fewer.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            self._posted_purchase(seed_user, txn, "60.00")
            _add_purchase(seed_user, txn, "40.00", is_credit=True)
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            # The close booked nothing: the family's lines are the dated purchase's.
            assert _family_nets(txn_id, seed_user["scenario"].id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("-60.00"),
                _category_chart_key(
                    seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
                ): Decimal("60.00"),
            }}
            _assert_reconciles(seed_user["scenario"].id, checking)

    # The three cases below graded ``_resync_after_entry_change``'s ledger
    # reconcile on a SETTLED envelope until plan step X-au-c3.  That row shape
    # is unreachable now -- a settled row's purchases are closed on all three
    # doors -- so they grade the shape ruling **R-FM** is about: a PROJECTED
    # envelope whose purchases carry a recorded bank posting day.  The
    # reconcile itself went at plan task plan#3097; what they grade is the
    # worked-out ledger each entry change moves.  Every end figure is the one
    # the settled ordering produced.
    #
    # The posting day is the period start PLUS A DAY throughout, and that
    # matters: a leg dated ON the opening assertion's day is ABSORBED by its
    # correction (which is what ``test_recording_a_posting_day_moves_the_leg_
    # onto_its_own_day`` below is about), so a day inside the assertion would
    # make every total read $1,000.00 and grade nothing.

    @staticmethod
    def _posted_purchase(seed_user, txn, amount, *, is_credit=False):
        """Attach a purchase the bank has TAKEN, and return it.

        Two acts because the entry doors are two: the purchase is recorded, then
        the day it posted is.  Both move what the case grades.
        """
        entry = _add_purchase(seed_user, txn, amount, is_credit=is_credit)
        db.session.flush()
        return entry_service.update_entry(
            entry.id, seed_user["user"].id,
            settle_day=an_entered_day(txn.pay_period.start_date + timedelta(days=1)),
        )

    def test_entry_create_on_a_posted_envelope_books_the_purchase(
        self, app, seed_user, seed_periods,
    ):
        """Adding a purchase the bank took grows the envelope's posted outflow.

        Arithmetic: one $40 debit purchase, posted the day after the opening
        assertion, books -40 / +40.  A late $30 posted purchase books its own
        -30 / +30 -> net cash -70.00 / Groceries +70.00, i.e. checking 930.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            self._posted_purchase(seed_user, txn, "40.00")
            db.session.commit()
            txn_id = txn.id
            groceries_ledger = _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("40.00")

            late = entry_service.create_entry(
                txn_id, user_id,
                EntryDetails(
                    figure=typed(Decimal("30.00")), description="late",
                    purchased_on=seed_periods[0].start_date, is_credit=False,
                ),
            )
            entry_service.update_entry(
                late.id, user_id,
                settle_day=an_entered_day(seed_periods[0].start_date + timedelta(days=1)),
            )
            db.session.commit()

            assert _ledger_total(seed_user, groceries_ledger) == Decimal("70.00")
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("930.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_entry_credit_flip_on_a_posted_envelope_takes_its_cash_line_off(
        self, app, seed_user, seed_periods,
    ):
        """Flipping a posted purchase to credit reverses its checking outflow.

        Arithmetic: one $40 posted debit books -40 / +40.  Flipping it to credit
        says the card paid, not the account, so its checking leg goes and the
        ledger returns to the $1,000.00 opening (the $40 now rides its CC
        Payback).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            entry = self._posted_purchase(seed_user, txn, "40.00")
            db.session.commit()
            entry_id = entry.id
            groceries_ledger = _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("40.00")

            entry_service.update_entry(entry_id, user_id, is_credit=True)
            db.session.commit()

            assert _ledger_total(seed_user, groceries_ledger) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("1000.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_entry_delete_on_a_posted_envelope_takes_its_lines_off(
        self, app, seed_user, seed_periods,
    ):
        """Deleting a posted purchase shrinks the envelope's posted outflow.

        Arithmetic: two posted debits 40 + 30 book -70 / +70.  Deleting the $30
        takes its -30 / +30 off -> net cash -40.00 / Groceries +40.00, i.e.
        checking 960.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            self._posted_purchase(seed_user, txn, "40.00")
            doomed = self._posted_purchase(seed_user, txn, "30.00")
            db.session.commit()
            doomed_id = doomed.id
            groceries_ledger = _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("70.00")

            entry_service.delete_entry(doomed_id, user_id)
            db.session.commit()

            assert _ledger_total(seed_user, groceries_ledger) == Decimal("40.00")
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("960.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_recording_a_posting_day_moves_the_leg_onto_its_own_day(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Recording when the bank took a purchase RE-DATES its cash leg.

        Successor to the ``toggle_cleared`` control (plan step S1-c, ruling
        R-DH (d)): the flag and its toggle are deleted, and the fact they stood
        in for is the purchase's own ``settled_on``.

        **Its property is RE-RULED at plan step X-f3b (ruling R-FM), and this
        fixture turns out to be finding N-274 itself.**  It asserted that
        recording a posting day left the ledger untouched, because a settled
        row's confirmed cash effect was ``effective - Sigma(credit)`` and the
        posting day appeared nowhere in it.  That effect now nets the row's
        already-posted purchases, so recording the day moves the ``$40.00`` OUT
        of the envelope's close and INTO a leg of its own on the day the bank
        took it.

        Hand-computed.  The purchase's ``purchased_on`` is the period's start
        (``_add_purchase``), which is the very day the fixture's opening
        assertion declares ``$1,000.00`` for -- so recording that as the posting
        day says *the bank had taken this $40.00 when I read that balance*:

        * BEFORE: the envelope's whole ``$40.00`` was booked at its own settle
          day, after the assertion, so the ledger read ``$960.00`` -- the
          ``$40.00`` counted once inside the declared balance and once again on
          the way out.  That double count is **N-274**;
        * AFTER: the purchase's leg lands on the assertion's own day, the
          opening correction absorbs it, and the ledger reads the ``$1,000.00``
          the owner actually declared.

        The purchase's own line appears on that day, and the envelope books
        nothing of its own.  Both halves are asserted: a test checking only the
        total would pass against a build that recorded the day and posted
        nothing.

        **The ORDER of the two acts changed at plan step X-au-c3** and the end
        state did not.  The parent used to be SETTLED when the day was
        recorded -- ``update_entry`` was described here as *the door a user has
        for a purchase on an already-settled envelope*.  That door is closed: a
        settled row's purchases are closed too, because carry-forward has
        already moved its leftover into a later period and re-pricing it from a
        partial record hands spent money back to the projection.  So the day is
        recorded while the row is still PROJECTED -- which is the order the
        reconcile panel produces anyway, its purchase arm admitting only
        projected parents (``reconcile_service._purchases._outstanding_scope``)
        -- and the close follows.  Every figure below is the one the old
        ordering produced.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            entry = _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            entry_id = entry.id
            purchased_on = entry.purchased_on
            txn_id = txn.id
            # **The balance is asserted for the purchase's own day, and this
            # case says so rather than inheriting it** (plan step X-f3c-2c).
            # Its whole premise is that the purchase's leg lands on the
            # assertion's day and is therefore ABSORBED by that day's
            # correction; the seeded account asserts only its origination, on
            # the bootstrap day before the calendar, so the premise has to be
            # built.
            reassert_balance_on(
                db.session, checking, settle_instant_on(purchased_on),
            )
            db.session.commit()

            updated = entry_service.update_entry(
                entry_id, user_id, settle_day=an_entered_day(purchased_on),
            )
            db.session.commit()
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            db.session.commit()

            # The fact really was recorded -- otherwise "the total did not move"
            # would be true of a no-op and prove nothing.
            assert updated.settled_on == purchased_on
            # The purchase carries the money on the day the bank was seen to
            # take it; the envelope posts nothing of its own (a plan row has
            # no leg, ruling **R-BAL80**).
            assert [
                (line.entry_date, line.amount)
                for line in _purchase_lines(entry_id, seed_user["scenario"].id)
                if line.key == linked_key(checking)
            ] == [(purchased_on, Decimal("-40.00"))]
            # And the ledger now equals the balance the owner declared, rather
            # than that balance minus a purchase it already contained.
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("1000.00")
            # ``_assert_reconciles`` is deliberately NOT called here, and its
            # own docstring says why: it grades ledger == opening + source
            # effect, which holds only while every source RIDES ON TOP of the
            # opening.  This test is the one case in the suite that breaks that
            # precondition on purpose -- the purchase's leg lands on the
            # assertion's own day, so the opening correction ABSORBS it and the
            # ledger lands on the asserted balance instead.  Asserting the
            # figure above is the stronger statement anyway: it is the number
            # the owner typed.


class TestAPurchaseIsAPostingSourceOfItsOwn:
    """Ruling **R-FM** (plan step X-f3b): the PURCHASE half of a row's family.

    A purchase whose bank posting day the owner has recorded books its own
    balanced cash leg, on its own day, whatever its envelope's status is -- and
    its envelope's close then books only what its purchases did not.  These
    grade the three things that makes true which nothing else does: a
    still-PROJECTED envelope can hold ledger legs; a re-category moves them; and
    deleting either half takes them off with it.
    """

    def test_a_projected_envelopes_purchase_posts_on_its_own_day(
        self, app, seed_user, seed_periods,
    ):
        """The case that could not exist before: postings under a Projected row.

        A ``$100.00`` envelope nobody has closed, carrying one ``$40.00``
        purchase the bank took on the period's start day.  The envelope has
        no leg of its own (a plan row books nothing, ruling **R-BAL80**), and
        the purchase's is present: cash ``-40.00`` against the category
        ``+40.00``, dated the day the money moved.

        Hand-computed and asserted in BOTH directions -- the leg's own two
        amounts, and the account's total ``1000 - 40 = 960.00`` -- because a
        posting that balanced against the wrong ledger account would satisfy
        either alone.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food",
                Decimal("100.00"),
            )
            entry = _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            entry_id = entry.id

            entry_service.update_entry(
                entry_id, user_id, settle_day=an_entered_day(seed_periods[0].start_date),
            )
            db.session.commit()

            lines = _purchase_lines(entry_id, seed_user["scenario"].id)
            assert {line.entry_date for line in lines} == {seed_periods[0].start_date}
            assert {line.key: line.amount for line in lines}[
                linked_key(checking)
            ] == Decimal("-40.00")
            assert sorted(line.amount for line in lines) == [
                Decimal("-40.00"), Decimal("40.00"),
            ]

    def test_re_categorising_the_parent_moves_its_purchases_legs(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A purchase books to its ENVELOPE's category, so a re-category moves it.

        The counter leg is the parent's category (ruling **R-FM**), which makes
        a re-category of a still-PROJECTED envelope a ledger act -- a state that
        did not exist before X-f3b, and one the store's sync reconciled only through
        its purchase arm, until plan task plan#3097 took the sync out of the doors.

        Asserted as a MOVE rather than as an addition: the old category's legs
        must net to zero afterwards, or the expense would be counted in two
        categories at once.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food",
                Decimal("100.00"),
            )
            entry = _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            entry_service.update_entry(
                entry.id, user_id, settle_day=an_entered_day(seed_periods[0].start_date),
            )
            db.session.commit()
            old_category = category_key(
                txn.category_id, LedgerAccountClassEnum.EXPENSE,
            )
            txn_id = txn.id

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"category_id": seed_user["categories"]["Car Payment"].id},
            )
            assert resp.status_code == 200

            assert _ledger_total(seed_user, old_category) == Decimal("0")
            assert _ledger_total(seed_user, _category_chart_key(
                seed_user, "Car Payment", LedgerAccountClassEnum.EXPENSE,
            )) == Decimal("40.00")

    def test_moving_the_parent_between_periods_re_files_its_purchases_legs(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A line carries its source row's BUDGET column, so a move re-files it.

        The R2 attribution rule on the one row type that can now reach it: a
        PROJECTED envelope holds ledger legs once a purchase's bank posting day
        is recorded, and ``pay_period_id`` was NOT among the PATCH fields that
        raised the store's reconcile (until plan task plan#3097), on "a settled
        row's period cannot move anyway", true while only a settled row held them.

        Asserted as a MOVE: the old period must hold NOTHING for this purchase
        afterwards, or the same $40.00 would be attributed to two columns at
        once.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food",
                Decimal("100.00"),
            )
            entry = _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            entry_id, txn_id = entry.id, txn.id
            entry_service.update_entry(
                entry_id, user_id, settle_day=an_entered_day(seed_periods[0].start_date),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            assert list(period_nets_of_movements([entry_id], scenario_id)) == [
                seed_periods[0].id,
            ]

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"pay_period_id": seed_periods[1].id},
            )
            assert resp.status_code == 200

            by_period = period_nets_of_movements([entry_id], scenario_id)
            assert seed_periods[0].id not in by_period, (
                "the period the row left must hold nothing for this purchase"
            )
            assert by_period[seed_periods[1].id][
                linked_key(seed_user["account"])
            ] == Decimal("-40.00")

    def test_deleting_a_purchase_takes_its_leg_off_with_the_row(
        self, app, seed_user, seed_periods,
    ):
        """The purchase half: a deleted purchase's leg goes with the row.

        A purchase that posted its $40.00 leg is deleted: the ledger holds no
        line for it afterwards, and the account is whole again.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food",
                Decimal("100.00"),
            )
            entry = _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            entry_id = entry.id
            before = account_net(
                db.session, checking.id, seed_user["scenario"].id,
            )
            entry_service.update_entry(
                entry_id, user_id, settle_day=an_entered_day(seed_periods[0].start_date),
            )
            db.session.commit()
            # The purchase really posted, or the delete below would be a no-op
            # and prove nothing: Checking's own line falls by its $40.00.
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == before - Decimal("40.00")

            entry_service.delete_entry(entry_id, user_id)
            db.session.commit()

            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == before


# ---------------------------------------------------------------------------
# Delete: every path takes the posted effect off with the row
# ---------------------------------------------------------------------------


class TestDeleteReversesPostings:
    """Deleting a posted transaction (or a posted payback) takes its money off the ledger."""

    def test_hard_delete_adhoc_settled_reverses(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Hard-deleting a Paid ad-hoc expense takes its lines off with it.

        The $50 expense posts -50 / +50, and after the delete the read holds no
        line for it, so every account is back where it was.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("50.00")

            resp = auth_client.delete(f"/transactions/{txn_id}")
            assert resp.status_code == 200

            # Row is gone with its movement.
            assert db.session.get(Transaction, txn_id) is None
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            _assert_reconciles(scenario_id, checking)

    def test_soft_delete_template_settled_reverses(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Soft-deleting a Paid template row takes its family's lines off.

        A template-linked envelope holds one $40 purchase the bank took
        (posted -40 / +40 by its own leg -- the close books nothing, ruling
        **R-BAL77**) and settles.  The delete soft-deletes it (template_id
        set): the row survives with ``is_deleted=True`` holding nothing, so it
        books nothing and drops out of ``posted_purchase_effect`` too.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            TestEnvelopePostingLifecycle._posted_purchase(seed_user, txn, "40.00")
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("40.00")

            # Pressed as the card's Delete sends it: the purchase its dialog
            # names (plan step credit_card:CC-5-4a-5, ruling R-CC131).
            resp = auth_client.delete(
                f"/transactions/{txn_id}",
                query_string=dialog_delete_values(auth_client, txn_id),
            )
            assert resp.status_code == 200

            # Soft delete: the row survives holding nothing.  Its purchase goes
            # with the delete since ruling R-CC75.
            survivor = db.session.get(Transaction, txn_id)
            assert survivor is not None
            assert survivor.is_deleted is True
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn_id,
            ).count() == 0
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            _assert_reconciles(scenario_id, checking)

    def test_settled_payback_reversed_on_unmark_credit(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Un-marking credit takes off a payback that was already settled.

        Mark an $80 expense Credit (creating a Projected payback), settle that
        payback (posting -80 / +80 on the CC Payback account), then un-mark the
        source: the CC Payback ledger nets to zero.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            source = add_txn(
                db.session, seed_user, seed_periods[0], "Dinner", "80.00",
                category_key="Groceries",
            )
            db.session.commit()
            source_id = source.id

            payback = credit_workflow.mark_as_credit(source_id, user_id)
            db.session.commit()
            payback_id = payback.id
            auth_client.post(f"/transactions/{payback_id}/mark-done")
            cc_ledger = _cc_payback_chart_key(seed_user)
            assert _ledger_total(seed_user, cc_ledger) == Decimal("80.00")

            credit_workflow.unmark_credit(source_id, user_id)
            db.session.commit()

            # Payback gone, and the CC Payback account back to zero.
            assert db.session.get(Transaction, payback_id) is None
            assert _ledger_total(seed_user, cc_ledger) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            _assert_reconciles(scenario_id, checking)

    def test_recursive_payback_chain_reverses_on_source_delete(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Deleting a source takes off a settled payback DEEP in the credit chain.

        source -> payback1 -> payback2, with payback2 settled (posted -100 /
        +100 on CC Payback).  Deleting the source recurses through the chain, and
        the CC Payback ledger nets to zero.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            source = add_txn(
                db.session, seed_user, seed_periods[0], "Chain", "100.00",
                category_key="Groceries",
            )
            db.session.commit()
            source_id = source.id

            payback1 = credit_workflow.mark_as_credit(source_id, user_id)
            db.session.commit()
            payback2 = credit_workflow.mark_as_credit(payback1.id, user_id)
            db.session.commit()
            payback2_id = payback2.id
            auth_client.post(f"/transactions/{payback2_id}/mark-done")
            cc_ledger = _cc_payback_chart_key(seed_user)
            assert _ledger_total(seed_user, cc_ledger) == Decimal("100.00")

            resp = auth_client.delete(f"/transactions/{source_id}")
            assert resp.status_code == 200

            # Whole chain gone; the CC Payback account back to zero.
            assert db.session.get(Transaction, payback2_id) is None
            assert _ledger_total(seed_user, cc_ledger) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            _assert_reconciles(scenario_id, checking)


# ---------------------------------------------------------------------------
# Carry-forward: each envelope source it settles books its confirmed effect
# ---------------------------------------------------------------------------


class TestCarryForwardPostsSettledSources:
    """Each envelope that carry-forward settles books its confirmed effect."""

    def test_carry_forward_posts_partially_spent_envelope(
        self, app, db, seed_user, seed_periods,
    ):
        """A partially-spent carried envelope's money is its dated purchase's leg.

        Arithmetic: a $100 envelope in the source period holds one $30 debit
        the bank took (posted -30 / +30 by its own leg when it was dated).
        Carry-forward settles the source at sum(entries) = 30 and rolls the
        $70 leftover into the target.  The close books nothing (ruling
        **R-BAL77**, plan step ``balance:X-bi-4a``; through ``X-bi-3e`` it
        booked the -30 / +30 itself), so the family's lines are exactly the
        purchase's; the target row (Projected) posts nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            source = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            TestEnvelopePostingLifecycle._posted_purchase(seed_user, source, "30.00")
            db.session.commit()
            source_id = source.id

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, scenario_id,
                balance_ctx=BalanceContext.build(user_id),
            )
            db.session.commit()

            assert _family_nets(source_id, scenario_id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("-30.00"),
                _category_chart_key(
                    seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
                ): Decimal("30.00"),
            }}
            _assert_reconciles(scenario_id, checking)

    def test_carry_forward_empty_envelope_posts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """An empty carried envelope settles at zero and books nothing.

        With no entries the source settles at ``actual_amount`` = 0, so its
        effect is zero and Checking stays at its $1,000.00 opening -- the full
        estimate rolls forward instead.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, scenario_id,
                balance_ctx=BalanceContext.build(user_id),
            )
            db.session.commit()

            assert account_net(
                db.session, seed_user["account"].id, scenario_id,
            ) == Decimal("1000.00")
