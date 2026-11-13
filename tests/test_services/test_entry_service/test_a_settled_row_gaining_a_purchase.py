"""A settled row may still gain a purchase, and a purchase may be born carrying its posting day."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.exceptions import ValidationError
from app import ref_cache
from app.enums import StatusEnum
from app.services import cash_ledger, status_seam, transaction_service
from app.services.row_valuation import settled_figure
from app.utils.dates import display_today
from tests._test_helpers import (
    typed,
    an_entered_day,
    figure_source_columns,
    purchases_of,
    settle_day_columns,
    settlement_if_settling,
)

from ._helpers import _make_entry


class TestASettledRowMayStillGAINAPurchase:
    """Plan step ``bank_import:X-f6a-3b``: the ADD is its own rule.

    **Adding a purchase and removing one are not the same question**, and
    keeping them under one refusal made the evidenced direction share a guard
    with the optimistic one.  Removing a purchase shrinks a recorded cost on
    nothing but the user's second thoughts.  Adding one to a row whose figure IS
    its purchases raises that cost by exactly the figure a bank statement just
    showed -- which is the whole of what the statement importer does.

    **The rule is about the row's FIGURE, not its status**, and the two bases
    behave oppositely (measured on a production clone 2026-08-18):

      * a ``purchases`` settlement stores no figure, so the close IS
        ``Sigma(entries)`` and has no covering movement.  A new posted
        purchase is a movement of its own on its own day and the row books
        nothing (ruling **R-BAL80**), so nothing else moves.  Adding `$18.64`
        to one 2026-05-21 close shrank that day's anchor true-up by exactly
        `$18.64`;
      * a ``derived`` or ``corrected`` settlement stores its figure, fixed
        before the purchase existed, and its covering movement carries the
        whole close.  The purchase's own movement would then be counted
        BESIDE it -- a `$500.00` close with a `$600.00` purchase inserted
        reads `-1,100.00` -- a double count of spending the close never held.
        (Under the row-leg fold of the time the same insert read the money
        back the other way: `-163.95` became **`+203.67`**, an expense row
        publishing an inflow, while the true-up moved `$0.00` so the
        spending was not recorded at all.)

    :func:`~app.services.entry_service._doors._reject_settled_addition` is the
    only thing that makes the second state unrepresentable --
    ``cash_ledger.movement_cash_leg`` states no precondition and cannot see
    one.
    """

    @staticmethod
    def _close(txn):
        """Move *txn* Projected -> Paid through the real seam."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(
                txn, ref_cache.status_id(StatusEnum.DONE),
            ),
        )
        db.session.flush()

    def test_a_purchases_basis_close_ADMITS_a_new_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The row's recorded cost GROWS by exactly the purchase.

        That is not a side effect to be tolerated -- it is what the row's
        settlement record MEANS.  A ``purchases`` basis stores no figure, so
        saying "this envelope also paid for that" is saying "it cost this much
        more", and the bank is the evidence.

        **The purchase states the day the bank took it**, which is what the
        rule requires of an addition to a closed row: without it the amount
        would come out of the envelope's own leg on the day the row closed
        rather than booking its own dated cash.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            assert txn.covering_movements == []
            recorded_before = settled_figure(txn)

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                    settle_day=an_entered_day(display_today()),
                ),
            )
            db.session.flush()

            assert settled_figure(txn) == recorded_before + Decimal("30.00")
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 2

    def test_a_POSTED_addition_adds_its_own_movement_and_moves_nothing_else(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The money property the whole rule rests on.

        A purchase carrying a bank posting day is a cash movement of its own
        (ruling **R-FM**), and on a ``purchases`` basis the row books nothing
        of its own (ruling **R-BAL80**; it is worth nothing to the matcher
        either, ruling **R-BAL81**).  So the import RECORDS the movement
        instead of redistributing one it already had: the dated stream gains
        exactly the new purchase on its own day, and the `$50.00` already in
        flight stays there untouched.

        Through plan step ``balance:X-bi-4a``'s first cut this pinned the
        row's own leg -- ``gross`` and ``Sigma(posted)`` rose together, so
        ``settled_cash_leg`` read `-50.00` before and after -- and its firing
        control was that term; R-BAL81 deleted the leg, and the control is
        the composition of the two streams.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            held = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            account_id, scenario_id = txn.account_id, txn.scenario_id
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0.00")
            assert [
                (item.entry_id, item.delta)
                for item in cash_ledger.in_flight_movements(account_id, scenario_id)
            ] == [(held.id, Decimal("-50.00"))]
            dated_before = [
                (fact.settled_on, fact.delta)
                for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)
            ]

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                    settle_day=an_entered_day(display_today()),
                ),
            )
            db.session.flush()
            db.session.expire(txn)

            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0.00")
            assert [
                (item.entry_id, item.delta)
                for item in cash_ledger.in_flight_movements(account_id, scenario_id)
            ] == [(held.id, Decimal("-50.00"))]
            assert [
                (fact.settled_on, fact.delta)
                for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)
            ] == dated_before + [(display_today(), Decimal("-30.00"))]
            assert cash_ledger.posted_purchase_sum(txn) == Decimal("30.00")

    def test_a_STORED_FIGURE_close_refuses_a_new_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The arm that would fabricate an inflow, refused at the door.

        The row closed with no purchases, so its settlement stores `$500.00`
        and nothing can raise it.  Recording a `$600.00` purchase against it
        would take `$600.00` out of a `$500.00` gross.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            assert len(txn.covering_movements) == 1

            with pytest.raises(ValidationError, match="records a fixed figure"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("600.00")),
                        description="BJs",
                        purchased_on=display_today(),
                        settle_day=an_entered_day(display_today()),
                    ),
                )

            # No purchase landed; the covering movement is not one (X-bi-3a).
            assert purchases_of(txn.id) == []

    def test_a_close_of_NOTHING_admits_a_new_purchase_and_then_records_it(
        self, app, db, seed_user, seed_entry_template,
    ):
        """A ``$0.00`` close holds no covering movement, so nothing doubles.

        Ruling **R-BAL82** (plan step ``balance:X-bi-4b-1``): the rule reads
        the covering movement, and ``ck_transaction_entries_positive_amount``
        admits no movement of nothing -- so a row closed at a typed ``$0.00``
        is a close with no entries, the door ADMITS a later purchase, the row
        then records exactly that purchase (``settled_figure``), the family
        moves it once, and its removal is admitted by the same reading.  The
        column read this replaced filed the row under "a fixed figure" and
        refused both.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("0.00"))),
            )
            db.session.flush()
            assert txn.covering_movements == []
            assert settled_figure(txn) == Decimal("0")
            account_id, scenario_id = txn.account_id, txn.scenario_id
            facts_before = sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            )

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("12.00")),
                    description="Late fee after all",
                    purchased_on=display_today(),
                    settle_day=an_entered_day(display_today()),
                ),
            )
            db.session.flush()

            assert purchases_of(txn.id) == [entry]
            assert settled_figure(txn) == Decimal("12.00")
            assert sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            ) == facts_before - Decimal("12.00")
            assert entry_service.removal_refusal(txn) is None

    def test_the_state_the_refusal_prevents_publishes_a_DOUBLE_COUNT(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The guard's firing control, built the only way it can be reached.

        ``movement_cash_leg`` is TOTAL in every other direction and states no
        precondition here, so nothing but
        :func:`~app.services.entry_service._doors._reject_settled_addition`
        keeps this unrepresentable.  A test that only asserted the refusal
        would pass just as well if the underlying state were harmless -- so
        this one builds it through the ORM, past every door, and measures what
        the money rule then answers.

        The family reads `-1,100.00` for a close that recorded `$500.00`: the
        covering movement carries the whole close and the illegal purchase's
        own movement is counted BESIDE it (ruling **R-BAL80**), `$600.00` of
        spending the close never held.  Every reader of the family -- the fold
        (``settled_cash_facts``) and the worked-out ledger -- sees the double
        count; the row's own valuation does not move (ruling **R-BAL81**: it
        is worth its covering movement, still `-500.00`), which is why the
        matcher alone would be blind to it.

        Through plan step ``balance:X-bi-4a``'s first cut the composition
        under test was the row's OWN leg, which read the whole illegal
        purchase back as an INFLOW (`0 - 600` beside the covering movement;
        `-500 + 600 = +100` before X-bi-3a); that leg is deleted.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            account_id, scenario_id = txn.account_id, txn.scenario_id
            # The covering movement carries the `$500.00`; the row is worth
            # it (R-BAL81) and the family moves it.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("-500.00")
            assert sum(
                (cash_ledger.movement_cash_leg(txn, entry) for entry in txn.entries),
                Decimal("0.00"),
            ) == Decimal("-500.00")
            facts_before = sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            )

            # PAST the door on purpose -- see the docstring.
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("600.00"), description="BJs",
                purchased_on=display_today(), **settle_day_columns(display_today()),
                is_credit=False,
            )
            db.session.add(entry)
            db.session.flush()
            db.session.expire(txn)

            # The family now moves `$1,100.00` for a `$500.00` close, and the
            # fold reads the same: the illegal purchase's own fact beside the
            # covering movement's.
            assert sum(
                (cash_ledger.movement_cash_leg(txn, entry) for entry in txn.entries),
                Decimal("0.00"),
            ) == Decimal("-1100.00")
            assert sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            ) == facts_before - Decimal("600.00")
            # ...while the row's own valuation is untouched, so only a reader
            # of the family can see the double count.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("-500.00")

    def test_an_UNDATED_purchase_is_admitted_on_a_closed_row_and_is_in_flight(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The un-dated arm is LIFTED (ruling R-BAL77, plan step balance:X-bi-4a).

        The developer's 2026-08-19 ruling refused an undated purchase on a
        closed envelope because the row's own leg then booked it ON THE DAY
        THE ROW CLOSED -- a past day the owner may already have checked
        against a statement, with no external evidence for the movement.  A
        plan row books nothing of its own now (ruling **R-BAL80**): the
        purchase is a movement IN FLIGHT, held by the projection from
        tomorrow and absent from the actual until the bank is seen to take
        it, exactly as one under an open envelope is.  The second half
        measures that: the settled stream reads nothing for it and the
        in-flight tier reads its figure.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                ),
            )
            db.session.flush()
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 2

            # Nothing dated on the row, so the settled stream holds nothing of
            # it; both purchases are in flight at their own figures.
            facts = cash_ledger.settled_cash_facts(txn.account_id, txn.scenario_id)
            assert [fact for fact in facts if fact.transaction_id == txn.id] == []
            in_flight = {
                item.delta
                for item in cash_ledger.in_flight_movements(
                    txn.account_id, txn.scenario_id,
                )
                if item.transaction_id == txn.id
            }
            assert in_flight == {Decimal("-50.00"), Decimal("-30.00")}


class TestAPurchaseMayBeBornCarryingItsPostingDay:
    """``EntryDetails.settled_on``, plan step ``bank_import:X-f6a-3b``.

    The field was deliberately absent on the premise that *at the moment a
    purchase is recorded there is nothing to have observed*.  True of the
    add-purchase form; false of a purchase created FROM a bank statement line,
    where the observation is what caused the record to exist.

    **Both of the update door's posting-day rules come with it**, because a
    door that accepts a field and leaves its rules to the other door is a
    boundary that holds on one and not the other.
    """

    def test_the_posting_day_is_written_at_create(
        self, app, db, seed_user, seed_entry_template,
    ):
        """One act, not two.

        A follow-up ``update_entry`` would re-run the payback sync (and, until
        plan task plan#3097, the posting reconcile) against an intermediate state
        in which a purchase the bank has already taken looks outstanding -- and
        would leave that state committed if anything after it refused.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            posted = display_today()
            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=posted - timedelta(days=2),
                    settle_day=an_entered_day(posted),
                ),
            )
            db.session.flush()

            assert entry.settled_on == posted
            assert entry.purchased_on == posted - timedelta(days=2)

    def test_omitting_it_leaves_the_purchase_OUTSTANDING(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The default is unchanged: a hand-typed purchase observes nothing."""
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                ),
            )
            db.session.flush()

            assert entry.settled_on is None

    def test_a_posting_day_BEFORE_the_purchase_day_is_refused(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Money cannot leave the account before it is spent.

        ``ck_transaction_entries_settled_not_before_purchase`` is the backstop;
        this is the door, so the user gets both dates rather than a 500 from an
        ``IntegrityError``.  Shown to FIRE: without the call the insert reaches
        the CHECK.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            with pytest.raises(
                ValidationError, match="cannot reach your bank before",
            ):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("30.00")),
                        description="Food Lion",
                        purchased_on=display_today(),
                        settle_day=an_entered_day(display_today() - timedelta(days=1)),
                    ),
                )
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_a_FUTURE_posting_day_is_refused(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Ruling **R-FM**'s bound, applied at both doors rather than one.

        A day the bank has not reached releases the purchase's reservation now
        and books its cash later.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            with pytest.raises(ValidationError, match="cannot be in the future"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("30.00")),
                        description="Food Lion",
                        purchased_on=display_today(),
                        settle_day=an_entered_day(display_today() + timedelta(days=1)),
                    ),
                )
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0
