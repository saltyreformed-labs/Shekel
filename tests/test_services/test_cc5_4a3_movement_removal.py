"""Plan step ``credit_card:CC-5-4a-3``: a payment leaves the books through ONE act.

Ruling **R-CC54** (developer 2026-09-22), its first part: *"ONE act takes a
payment or purchase off the books: it reverses its ledger, takes it out of any
match (withdrawing a match left with nothing and saying which bank line that
freed), then deletes it.  Every door calls it, including the popover's two
paths."*  Finding **CC-358** is the door that did not: the status seam deleted
a matched payment on a ``$0.00`` or ``purchases`` record and asked no match,
so the act kept its bank line alone -- the review screen called the line
unexplained, the day drill-down called it matched, and matching it again raised
``IntegrityError`` on ``uq_statement_match_members_line``.

The two seam classes are FIRING controls against that tree -- each asserts a
state the old seam could not produce (the act gone, the line re-matchable, the
withdrawal logged), through the production doors:
``transaction_service.apply_requested_status`` for the popover's ``$0.00``,
``settle_transaction`` for Paid on a row whose purchases replace its payment.
The group case beside them is a REGRESSION pin, and says so; the last class
grades the act itself -- its return is its read's, and a surviving act's
loaded members agree with the table.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.services import (
    credit_workflow,
    entry_service,
    match_press,
    match_withdrawal,
    movement_removal,
    transaction_service,
    transfer_service,
)
from app.services.settle_day import SettleDay
from app.services.statement_match import accept_match, matched_subjects
from app.services.transaction_service import settle_transaction
from app.utils.log_events import EVT_STATEMENT_MATCH_WITHDRAWN
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    generate_row_of,
    make_expense_template,
    transfer_side_record,
    typed,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)


def _day(seed_user):
    """A bank day inside the bootstrap period, after the books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _settled(seed_user, name, amount):
    """A plain bill on checking, settled on :func:`_day` with its own figure."""
    txn = a_transaction(seed_user, name=name, amount=amount)
    db.session.commit()
    settle_transaction(
        txn, settle_day=SettleDay(
            day=_day(seed_user), basis=SettledDayBasisEnum.ENTERED,
        ),
    )
    db.session.commit()
    return txn


def _line(seed_user, amount, description):
    line = a_bank_line(
        seed_user, an_import(seed_user), amount=amount,
        posted_on=_day(seed_user), description=description,
    )
    db.session.commit()
    return line


def _match(seed_user, line, *txns, transfers=()):
    """Accept *line* against *txns* -- the act names each settled row's payment.

    *transfers* are ticked as their legs on the scope's account (leaf
    ``balance:X-bi-6-4c-1``).
    """
    scope = a_scope(seed_user)
    accepted = accept_match(
        a_submission(
            scope, lines=[line], transactions=list(txns),
            transfers=list(transfers),
        ),
        scope,
    )
    db.session.commit()
    return accepted


def _what_its_page_names(movements):
    """The lines a caption over *movements* names, as its page posts them back.

    What the popovers read (``match_withdrawal.pending_for_movements``) and
    send with the press since plan step ``credit_card:CC-5-4a-5`` (ruling
    **R-CC127**): the act refuses a press whose freed lines differ.
    """
    return match_press.Shown(
        match_withdrawal.pending_for_movements(movements).line_ids,
    )


def _record_zero(txn):
    """The popover's Actual box re-recorded at ``$0.00``, through its door."""
    with match_press.Press(_what_its_page_names(txn.covering_movements)) as press:
        transaction_service.apply_requested_status(
            txn, txn.status_id,
            stated=transaction_service.StatedRecord(figure=typed(Decimal("0.00"))),
            press=press,
        )
    db.session.commit()


def _claimed_lines(seed_user):
    return matched_subjects(seed_user["account"].id).lines


def _side_records(xfer):
    """The payment records *xfer*'s two sides hold, read by their own SQL.

    Each side's record hangs off the TRANSFER since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**); the transfer cases read
    their shadows' ``covering_movements`` until then (ruling **R-BAL167**
    class 4).  A side holding none is left out.
    """
    return [
        record for record in (
            transfer_side_record(db.session, xfer.id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        )
        if record is not None
    ]


class _Events(logging.Handler):
    """Collect the withdrawal module's structured events for one block."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []
        self._logger = logging.getLogger("app.services.match_withdrawal")
        self._prior_level = self._logger.level

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self._logger.addHandler(self)
        self._logger.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self)
        self._logger.setLevel(self._prior_level)

    def withdrawn(self):
        return [
            record for record in self.records
            if getattr(record, "event", None) == EVT_STATEMENT_MATCH_WITHDRAWN
        ]


class TestAZeroFigureTakesThePaymentOutOfItsMatch:
    """CC-358's own path: the Actual box re-recorded at ``$0.00``."""

    def test_the_act_is_withdrawn_and_the_line_can_be_matched_again(
        self, app, seed_user,
    ):
        """The $120 Hotel bill matched to 9/24 HOTEL -$120, then recorded at $0.00.

        On the old seam the act stayed holding the line alone and the second
        accept below raised ``IntegrityError`` on
        ``uq_statement_match_members_line``; here the act is gone, the line is
        unexplained, and matching it to another row records.
        """
        with app.app_context():
            hotel = _settled(seed_user, "Hotel", "120.00")
            line = _line(seed_user, "-120.00", "HOTEL")
            _match(seed_user, line, hotel)
            assert line.id in _claimed_lines(seed_user)

            _record_zero(hotel)

            db.session.expire_all()
            assert db.session.query(StatementMatch).count() == 0
            assert db.session.query(StatementMatchMember).count() == 0, (
                "the line member goes with the withdrawn act"
            )
            assert not db.session.get(Transaction, hotel.id).covering_movements
            assert line.id not in _claimed_lines(seed_user)

            motel = _settled(seed_user, "Motel", "120.00")
            _match(seed_user, line, motel)
            assert db.session.query(StatementMatch).count() == 1
            assert line.id in _claimed_lines(seed_user)

    def test_the_withdrawal_is_logged_with_the_re_record_sentence(
        self, app, seed_user,
    ):
        """The seam's own event sentence: the row stays, only its payment went."""
        with app.app_context():
            hotel = _settled(seed_user, "Hotel", "120.00")
            _match(seed_user, _line(seed_user, "-120.00", "HOTEL"), hotel)

            payment_id = hotel.covering_movements[0].id
            line_id = db.session.query(StatementMatchMember).filter(
                StatementMatchMember.bank_statement_line_id.isnot(None),
            ).one().bank_statement_line_id
            with _Events() as events:
                _record_zero(hotel)

            (record,) = events.withdrawn()
            assert record.getMessage() == match_withdrawal.RE_RECORDED
            assert record.match_count == 1
            assert record.freed_line_count == 1
            assert record.transaction_ids == [hotel.id], (
                "the row that STAYED is named"
            )
            assert record.transaction_entry_ids == [payment_id]
            assert record.freed_line_ids == [line_id]

    def test_a_group_act_keeps_standing_and_loses_only_that_member(
        self, app, seed_user,
    ):
        """One -$120 line against Hotel $70 + Parking $50; Hotel re-recorded at $0.00.

        The act keeps Parking's payment, so it is NOT withdrawn -- it reads
        amber on the register -- and only Hotel's member goes.  A REGRESSION
        pin rather than a firing control: the old seam reached the same end
        state through the member key's cascade, which the act now does by
        hand so that plan step ``credit_card:CC-5-4a-4`` can stop the key
        cascading (ruling **R-CC54**) without this door changing.
        """
        with app.app_context():
            hotel = _settled(seed_user, "Hotel", "70.00")
            parking = _settled(seed_user, "Parking", "50.00")
            line = _line(seed_user, "-120.00", "HOTEL AND PARKING")
            _match(seed_user, line, hotel, parking)
            parking_payment_id = parking.covering_movements[0].id

            _record_zero(hotel)

            db.session.expire_all()
            (act,) = db.session.query(StatementMatch).all()
            assert {
                (member.bank_statement_line_id, member.transaction_entry_id)
                for member in act.members
            } == {(line.id, None), (None, parking_payment_id)}
            assert line.id in _claimed_lines(seed_user)


class TestPaidFromPurchasesTakesTheKeptPaymentOutOfItsMatch:
    """The popover's second path: a reverted row whose purchases replace its payment."""

    def test_the_kept_payment_leaves_and_its_act_is_withdrawn(
        self, app, seed_user,
    ):
        """A matched $120 envelope reverted, a $120 purchase added, then Paid.

        The revert keeps the payment un-dated and the act naming it standing
        (ruling R-BAL61); settling from the purchase is a ``purchases``
        record, which the seam answers by taking that payment off the books
        -- so the act goes and the line is unexplained.  The old seam deleted
        the payment and left the act holding the line alone.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="120.00", name="Hotel",
                category_key="Rent", is_envelope=True,
            )
            hotel = generate_row_of(template, seed_user["bootstrap_period"])
            db.session.commit()
            settle_transaction(
                hotel, settle_day=SettleDay(
                    day=_day(seed_user), basis=SettledDayBasisEnum.ENTERED,
                ),
            )
            db.session.commit()
            line = _line(seed_user, "-120.00", "HOTEL")
            _match(seed_user, line, hotel)
            transaction_service.apply_requested_status(
                hotel, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert db.session.query(StatementMatch).count() == 1, (
                "a revert keeps the act on the un-dated payment"
            )
            purchase = entry_service.create_entry(
                hotel.id, seed_user["user"].id, entry_service.EntryDetails(
                    figure=typed(Decimal("120.00")), description="Hilton",
                    purchased_on=_day(seed_user),
                ),
            )
            db.session.commit()

            with match_press.Press(_what_its_page_names(hotel.covering_movements)) as press:
                settle_transaction(
                    hotel, press=press,
                )
            db.session.commit()

            db.session.expire_all()
            hotel = db.session.get(Transaction, hotel.id)
            assert hotel.status.is_settled
            assert not hotel.covering_movements
            assert [entry.id for entry in hotel.purchases] == [purchase.id]
            assert db.session.query(StatementMatch).count() == 0
            assert line.id not in _claimed_lines(seed_user)


class TestTheActWithdrawsWhatItsReadPrinted:
    """The disclosure and the press are one derivation."""

    def test_a_surviving_act_loses_the_member_from_its_loaded_collection(
        self, app, seed_user,
    ):
        """The session and the table agree with no re-read in between.

        ``take_out_of_matches`` takes a surviving act's member out of the
        collection the act was loaded with, so a reader later in the SAME
        request -- nothing is committed or expired here -- does not see a
        member the table no longer holds.  The re-point spelled this as a
        bulk ``DELETE`` with ``synchronize_session=False`` until this step,
        which leaves that collection holding the stale member.
        """
        with app.app_context():
            hotel = _settled(seed_user, "Hotel", "70.00")
            parking = _settled(seed_user, "Parking", "50.00")
            line = _line(seed_user, "-120.00", "HOTEL AND PARKING")
            accepted = _match(seed_user, line, hotel, parking)
            act = db.session.get(StatementMatch, accepted.match_id)
            assert len(act.members) == 3
            payment = hotel.covering_movements[0]

            movement_removal.remove_movements(
                [payment], seed_user["user"].id,
                because=match_withdrawal.RE_RECORDED,
                press=None,
            )

            assert len(act.members) == 2
            assert payment.id not in {
                member.transaction_entry_id for member in act.members
            }

    def test_the_read_and_the_act_agree_on_a_matched_payment(
        self, app, seed_user,
    ):
        """``pending_for_movements`` before; ``remove_movements``' return after."""
        with app.app_context():
            hotel = _settled(seed_user, "Hotel", "120.00")
            line = _line(seed_user, "-120.00", "HOTEL")
            _match(seed_user, line, hotel)
            payment = hotel.covering_movements[0]

            pending = match_withdrawal.pending_for_movements([payment])
            with match_press.Press(match_press.Shown(pending.line_ids)) as press:
                done = movement_removal.remove_movements(
                    [payment], seed_user["user"].id,
                    because=match_withdrawal.RE_RECORDED,
                    press=press,
                )

            assert done == pending
            assert done.matches == 1
            assert [freed.line_id for freed in done.lines] == [line.id]
            assert done.lines[0].amount == Decimal("-120.00")
            assert done.kept_rows == 0


class TestEveryDoorWithdrawsTheActItself:
    """The act is GONE -- not merely hidden -- at each door that removes a movement.

    The older cases for these doors (``test_withdrawal``) assert the LINE is
    no longer claimed, which ``matched_subjects``' leftover-match check
    answers whether or not the act ran: an act left holding its line alone
    reads unclaimed through that check (finding **CC-363**'s state).  Plan
    step ``credit_card:CC-5-4a-4`` deletes the check (ruling **R-CC54**), so
    each door here is graded on the act's own row -- a control that fails
    with the door's ``remove_movements`` call deleted.
    """

    def test_deleting_a_matched_purchase_withdraws_its_act(
        self, app, seed_user,
    ):
        """``entry_service.delete_entry``: a -$25.00 line matched to a Kroger purchase."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            db.session.commit()
            purchase = entry_service.create_entry(
                envelope.id, seed_user["user"].id, entry_service.EntryDetails(
                    figure=typed(Decimal("25.00")), description="Kroger",
                    purchased_on=_day(seed_user),
                ),
            )
            db.session.commit()
            line = _line(seed_user, "-25.00", "KROGER")
            scope = a_scope(seed_user)
            accepted = accept_match(
                a_submission(scope, lines=[line], entries=[purchase]), scope,
            )
            db.session.commit()

            # The X posts the lines its confirmation named (plan step
            # CC-5-4a-5, ruling R-CC127), read through the list's own twin.
            shown = entry_service.purchase_removals([envelope])[purchase.id]
            with match_press.Press(match_press.Shown(shown.delete.line_ids)) as press:
                entry_service.delete_entry(
                    purchase.id, seed_user["user"].id,
                    press=press,
                )
            db.session.commit()

            assert db.session.get(StatementMatch, accepted.match_id) is None
            assert db.session.query(StatementMatchMember).count() == 0

    def test_undo_cc_withdraws_the_act_naming_the_payback(
        self, app, seed_user,
    ):
        """``credit_workflow.delete_payback_on_credit_revert``."""
        with app.app_context():
            source = a_transaction(
                seed_user, name="Rogue Equipment", amount="200.00",
                template=False,
            )
            db.session.flush()
            a_later_period(seed_user)
            credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
            db.session.commit()
            payback = credit_workflow.get_active_payback(source.id)
            line = _line(seed_user, "-200.00", "CARD PAYMENT")
            accepted = _match(seed_user, line, payback)

            # Undo CC posts the lines its caption named (plan step
            # CC-5-4a-5, ruling R-CC127), read through the card's own twin.
            with match_press.Press(match_press.Shown(
                    credit_workflow.pending_for_credit_revert(source).line_ids,
                )) as press:
                credit_workflow.delete_payback_on_credit_revert(
                    source, seed_user["user"].id,
                    press=press,
                )
            db.session.commit()

            assert db.session.get(StatementMatch, accepted.match_id) is None
            assert db.session.query(StatementMatchMember).count() == 0

    def test_a_hard_transfer_delete_withdraws_the_act_naming_its_leg(
        self, app, seed_user,
    ):
        """``transfer_service.delete_transfer(soft=False)``.

        The page names the lines its transfer's side records free, where it
        read the Checking shadow's ``covering_movements`` until plan step
        ``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 4).
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("2000.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], Decimal("500.00"),
            )
            db.session.commit()
            line = _line(seed_user, "-500.00", "TRANSFER")
            accepted = _match(seed_user, line, transfers=[xfer])

            with match_press.Press(_what_its_page_names(_side_records(xfer))) as press:
                transfer_service.delete_transfer(
                    xfer.id, seed_user["user"].id, soft=False,
                    press=press,
                )
            db.session.commit()

            assert db.session.get(StatementMatch, accepted.match_id) is None
            assert db.session.query(StatementMatchMember).count() == 0

    def test_removing_the_last_card_purchase_withdraws_the_act_naming_its_payback(
        self, app, seed_user,
    ):
        """``entry_credit_workflow.sync_entry_payback``'s DELETE branch.

        An envelope's card purchase makes an entry-level payback; the payback
        is matched (settled, its payment the member), reverted -- its payment
        kept un-dated and the act kept on it (ruling R-BAL61), the branch
        refusing a settled payback -- and then the card purchase is deleted,
        so no credit entry remains and the payback goes, its payment through
        the act.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Groceries",
                category_key="Groceries", is_envelope=True,
            )
            envelope = generate_row_of(template, seed_user["bootstrap_period"])
            db.session.commit()
            a_later_period(seed_user)
            db.session.commit()
            purchase = entry_service.create_entry(
                envelope.id, seed_user["user"].id, entry_service.EntryDetails(
                    figure=typed(Decimal("60.00")), description="Kroger",
                    purchased_on=_day(seed_user), is_credit=True,
                ),
            )
            db.session.commit()
            payback = credit_workflow.get_active_payback(envelope.id)
            assert payback is not None
            line = _line(seed_user, "-60.00", "CARD PAYMENT")
            accepted = _match(seed_user, line, payback)
            transaction_service.apply_requested_status(
                payback, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert db.session.get(StatementMatch, accepted.match_id) is not None

            # The X posts the lines its confirmation named -- here the
            # payback's, which the press deletes (plan step CC-5-4a-5, ruling
            # R-CC80) -- read through the list's own twin.
            shown = entry_service.purchase_removals([envelope])[purchase.id]
            with match_press.Press(match_press.Shown(shown.delete.line_ids)) as press:
                entry_service.delete_entry(
                    purchase.id, seed_user["user"].id,
                    press=press,
                )
            db.session.commit()

            assert credit_workflow.get_active_payback(envelope.id) is None
            assert db.session.get(StatementMatch, accepted.match_id) is None
            assert db.session.query(StatementMatchMember).count() == 0


class TestTheEventFilesATransfersPaymentUnderItsTransfer:
    """Plan step ``balance:X-bi-6-4c-4`` (ruling **R-BAL160**): the event's parents.

    The withdrawal event recorded "the rows the movements were under" by
    reading each movement's ``transaction_id`` -- for a transfer's payment,
    the id of the shadow it hangs off, which ``X-bi-6-4d`` leaves unset when
    it re-parents the movement onto the transfer.  It asks
    ``transfer_legs.movement_parent`` now: a plan row by its id, a transfer's
    payment under ``transfer_ids``.  Both transfer doors that take a matched
    payment off the books are graded; a ROW's case is
    :class:`TestAZeroFigureTakesThePaymentOutOfItsMatch`' own.
    """

    def _a_matched_transfer(self, seed_user):
        """A $500.00 transfer checking -> savings, its checking leg matched.

        Ticked as its LEG on the scope's account (leaf
        ``balance:X-bi-6-4c-1``); the accept pays the Projected transfer
        (ruling **R-BAL89**), so the act names the checking leg's covering
        movement.

        Returns:
            The transfer.  It returned the expense shadow's id beside it,
            for a failure message, until plan step ``balance:X-bi-6-4d-2``
            made a transfer one row (ruling **R-BAL258**).
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings",
            anchor_balance=Decimal("2000.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], Decimal("500.00"),
        )
        db.session.commit()
        _match(
            seed_user, _line(seed_user, "-500.00", "TRANSFER"),
            transfers=[xfer],
        )
        return xfer

    def test_a_zero_figure_files_the_payment_under_the_transfer_alone(
        self, app, seed_user,
    ):
        """The transfer popover's ``$0.00``: no row leaves, so no row is named."""
        with app.app_context():
            xfer = self._a_matched_transfer(seed_user)

            # The transfer's side records, where the payments under its
            # shadows were read until plan step balance:X-bi-6-4d-2 (ruling
            # R-BAL167 class 4).
            payments = _side_records(xfer)
            with _Events() as events:
                with match_press.Press(_what_its_page_names(payments)) as press:
                    transfer_service.update_transfer(
                        xfer.id, seed_user["user"].id,
                        figure=typed(Decimal("0.00")),
                        press=press,
                    )
                db.session.commit()

            (record,) = events.withdrawn()
            assert record.getMessage() == match_withdrawal.RE_RECORDED
            assert record.transfer_ids == [xfer.id]
            assert record.transaction_ids == [], (
                "a transfer's payment is filed under its transfer, and no row "
                f"is a parent the owner sees: {record.transaction_ids}"
            )

    def test_a_hard_delete_names_the_transfer_and_no_row(
        self, app, seed_user,
    ):
        """The delete names its transfer, and no row leaves with it.

        Renamed from
        ``test_a_hard_delete_names_the_transfer_beside_its_leaving_shadows``:
        the delete handed its two shadows over as leaving rows, and the event
        named their ids, until plan step ``balance:X-bi-6-4d-2`` made a
        transfer one row (ruling **R-BAL258**).
        """
        with app.app_context():
            xfer = self._a_matched_transfer(seed_user)

            # The transfer's side records, where the payments under its
            # shadows were read until plan step balance:X-bi-6-4d-2 (ruling
            # R-BAL167 class 4).
            payments = _side_records(xfer)
            with _Events() as events:
                with match_press.Press(_what_its_page_names(payments)) as press:
                    transfer_service.delete_transfer(
                        xfer.id, seed_user["user"].id, soft=False,
                        press=press,
                    )
                db.session.commit()

            (record,) = events.withdrawn()
            assert record.getMessage() == match_withdrawal.LEFT_THE_BOOKS
            assert record.transfer_ids == [xfer.id]
            assert record.transaction_ids == []


class TestTheRemovedMovementLeavesItsParentsLoadedList:
    """The act's step 3: deleted, AND out of the list its parent loaded.

    A reconcile later in the same request walks that list -- the settle verbs',
    the entry door's re-derivation -- so a movement deleted but left in it is
    one the walk still meets.  Pinned for a row's payment and for a
    transfer's, whose list was its shadow's through the interval: plan step
    ``balance:X-bi-6-4c-4`` moved the act's reach for that list into
    ``transfer_legs.parent_entries``, and the list it answers must stay the
    one the parent loaded (a copy would leave the movement in the parent's).
    Plan step ``balance:X-bi-6-4d-2`` rewrote the transfer case onto the side
    links: the list is the transfer's ``expense_movements``.  Nothing is
    expired between the act and the assertion.
    """

    def test_a_rows_payment_leaves_the_rows_entries(self, app, seed_user):
        """A Paid $120.00 Hotel's payment, taken off by the act."""
        with app.app_context():
            hotel = _settled(seed_user, "Hotel", "120.00")
            family = hotel.entries
            (payment,) = hotel.covering_movements
            assert payment in family

            movement_removal.remove_movements(
                [payment], seed_user["user"].id,
                because=match_withdrawal.RE_RECORDED,
                press=None,
            )

            assert payment not in hotel.entries
            assert hotel.entries is family

    def test_a_transfers_payment_leaves_the_from_sides_movements(self, app, seed_user):
        """A Paid $500.00 transfer's checking-side payment, taken off by the act.

        Its list is the transfer's from-side collection since plan step
        ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), where it was the
        Checking shadow's ``entries`` until then (ruling **R-BAL167** class
        1).  Renamed from
        ``test_a_transfers_payment_leaves_its_shadows_entries`` at plan step
        ``balance:X-bi-6-4d-2``'s vacuous-residue checkpoint.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("2000.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], Decimal("500.00"),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            family = xfer.expense_movements
            payment = transfer_side_record(
                db.session, xfer.id, seed_user["account"].id,
            )
            assert payment in family

            movement_removal.remove_movements(
                [payment], seed_user["user"].id,
                because=match_withdrawal.RE_RECORDED,
                press=None,
            )

            assert payment not in xfer.expense_movements
            assert xfer.expense_movements is family
