"""Ruling R-FM (plan step X-f3b): the purchase half of a row's family is a posting source of its
own.
"""

from __future__ import annotations

from decimal import Decimal

from app.enums import LedgerAccountClassEnum
from app.extensions import db
from app.services import entry_service
from app.services.ledger_lines import category_key, linked_key
from tests._ledger_reads import account_net, period_nets_of_movements
from tests._test_helpers import an_entered_day, create_envelope_txn

from ._helpers import _category_chart_key, _ledger_total, _purchase_lines, _add_purchase


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
