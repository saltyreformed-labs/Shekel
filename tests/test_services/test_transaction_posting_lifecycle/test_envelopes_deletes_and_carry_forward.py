"""An envelope books effective less its credits and an entry edit moves it; every delete path takes
a settled row's money off the ledger; and carry-forward books each envelope it settles. The
three share TestEnvelopePostingLifecycle._posted_purchase.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.enums import LedgerAccountClassEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.balance_at import BalanceContext
from app.services import carry_forward_service, credit_workflow, entry_service
from app.services.entry_service import EntryDetails
from app.services.ledger_lines import category_key, linked_key
from tests._ledger_reads import account_net
from tests._test_helpers import (
    add_txn,
    an_entered_day,
    create_envelope_txn,
    reassert_balance_on,
    settle_instant_on,
    typed,
)
from tests.test_routes.test_cc5_4a5_popover_presses import dialog_delete_values

from ._helpers import (
    _category_chart_key,
    _ledger_total,
    _family_nets,
    _purchase_lines,
    _add_purchase,
    _assert_reconciles,
)


def _cc_payback_chart_key(seed_user):
    """Return the chart key of the Expense account of the user's CC Payback category."""
    cc_category = credit_workflow.get_or_create_cc_category(seed_user["user"].id)
    return category_key(cc_category.id, LedgerAccountClassEnum.EXPENSE)


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
        self, app, seed_user, seed_periods,
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
        self, app, seed_user, seed_periods,
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
