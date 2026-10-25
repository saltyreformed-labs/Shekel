"""The edit door's ENDPOINT arm (plan step R10-b): moving a transfer's source or destination, the
moves it refuses, and what a settled payment's move re-derives on the loan or account it leaves
and on the one it reaches.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.ref import AccountType
from app.services import (
    account_posting_service,
    account_service,
    loan_posting_service,
    transfer_service,
)
from app.exceptions import NotFoundError, ValidationError
from tests._test_helpers import add_anchor_history, create_loan_account
from app.models.amount_ownership import AmountOwnership

from ._helpers import (
    _ledger_nets_for_transfer,
    _the_day_before_today,
    _create_basic_transfer,
    _reread,
)


class TestMovingATransferBetweenAccounts:
    """``update_transfer``'s ENDPOINT arm (plan step R10-b).

    A transfer's source and destination are two of the six columns a recurring
    definition states, and this door could write only four of them -- so a
    definition's account change reached its generated rows by DESTROYING and
    re-creating every one of them, and a NON-repeating transfer refused the same
    edit outright because nothing could carry it.  The arm moves the transfer
    and reconciles the ledger on both sides of the move.  Until plan step
    ``balance:X-bi-6-4d-2`` its two legs were two shadow rows, which the arm
    re-pointed and re-named (that upkeep is deleted at the step's leaf 3b);
    each is derived from the transfer's endpoints now.  So a case that grades
    where the move LEFT the transfer reads the endpoint columns of the row
    re-read from the database since that step's checkpoint 2e-2b-1b-3, where
    it read the session's columns and, in five of the six, legs' accounts or
    labels (ruling R-BAL290 part 1).  Which endpoint each side is on is
    ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestLegOf`` and its
    ``TestBothSidesShowTheTransfer``, and each side's label, composed from
    the endpoints' current names, its ``TestALegsDisplayProjections``.  A
    ledger case that reads the transfer reads the session's row after the
    update's own flush, as the reconcile inside the update does, and
    :meth:`test_moving_a_settled_payment_ONTO_a_loan_reconciles_that_loan`'s
    relationship read is the firing control for a move that assigns the
    foreign key and not the relationship, which a re-read row cannot be: it
    loads the relationship by that column.
    """

    def _other_savings(self, td, name="Second Savings", observed_on=None):
        """Create a second savings account for this owner.

        Through ``account_service.create_account`` rather than an ``Account``
        insert, because that factory is what PAIRS the account with its
        chart-of-accounts ledger -- and an endpoint move posts to that ledger,
        so a hand-rolled row fails the move with a missing-pairing
        ``PostingError`` rather than exercising it.

        *observed_on* is the day its books open (today when ``None``).  A
        case that moves money SETTLED today onto it opens them a day earlier
        (:func:`_the_day_before_today`): the transfer arm refuses a side
        moving on or before its account's books open, by name, since leaf
        ``balance:X-bi-6-4d-2`` (the database refused it at commit before,
        which those cases never reach).  Setup only, approved by the
        developer 2026-10-08 (rule 5, ruling **balance:R-BAL234**): every
        figure they check is unchanged.
        """
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=td["user"].id,
                account_type_id=savings_type.id,
                name=name,
                anchor_balance=Decimal("0.00"),
                observed_on=observed_on,
            ),
        )
        db.session.flush()
        return account

    def _entries_a_re_sync_writes_after_moving_off(self, td, vacated, endpoint):
        """Move a settled transfer off *vacated*, then count what a re-sync writes.

        The two vacated-account cases' one body: the basic transfer is
        settled, an anchor is asserted on *vacated* AFTER the settle (the
        destination case says why) and its corrections synced, the endpoint
        named *endpoint* is moved to a new account, and *vacated*'s anchor
        corrections are synced again.  That second sync writes nothing when
        the move re-derived them, which is the oracle.

        Args:
            td: The ``transfer_data`` fixture.
            vacated: The account the move leaves -- the basic transfer's
                destination or its source.
            endpoint: ``"to_account_id"`` or ``"from_account_id"``, the
                ``update_transfer`` keyword that moves off *vacated*.

        Returns:
            The number of journal entries the second sync wrote.
        """
        elsewhere = self._other_savings(
            td, name="Moved Onto", observed_on=_the_day_before_today(),
        )
        xfer = _create_basic_transfer(td)
        transfer_service.settle_transfer(xfer.id, td["user"].id)
        db.session.flush()
        add_anchor_history(db.session, vacated, Decimal("900.00"))
        account_posting_service.sync_account_anchor_postings(
            vacated.id, td["scenario"].id,
        )
        db.session.flush()

        transfer_service.update_transfer(
            xfer.id, td["user"].id, **{endpoint: elsewhere.id},
        )
        db.session.flush()

        before_entries = db.session.query(JournalEntry).count()
        account_posting_service.sync_account_anchor_postings(
            vacated.id, td["scenario"].id,
        )
        db.session.flush()
        return db.session.query(JournalEntry).count() - before_entries

    def test_a_destination_move_writes_the_new_destination(
        self, app, transfer_data
    ):
        """Moving the destination writes the new one and leaves the source where it was.

        What the move wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' accounts and labels until plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-3: the legs were two
        shadow rows whose accounts and names the arm re-wrote until that
        step, and the expense leg's label had to follow a destination it does
        not sit on.  Each leg is derived from the transfer now, so a side's
        account is its endpoint column (``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestLegOf``) and its label is composed from the endpoints' current
        names (that file's ``TestALegsDisplayProjections``).  Renamed from
        ``test_the_destination_move_carries_the_income_leg_and_its_name``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                td["account"].id, elsewhere.id,
            )

    def test_a_source_move_writes_the_new_source(self, app, transfer_data):
        """The mirror of the case above, on the source.

        Renamed from ``test_the_source_move_carries_the_expense_leg_and_its_name``
        at the same checkpoint, which dropped its legs' accounts and labels
        likewise.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td, name="New Source")

            transfer_service.update_transfer(
                xfer.id, td["user"].id, from_account_id=elsewhere.id,
            )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                elsewhere.id, td["savings_account"].id,
            )

    def test_an_update_naming_no_account_leaves_the_transfer_where_it_is(
        self, app, transfer_data
    ):
        """The control: an ordinary edit moves neither endpoint.

        **It carried a NAME half until plan step ``balance:X-bi-6-4d-2``,
        dropped there as a DOMINATED read, with the rename that set nothing.**
        It renamed the destination account and then asserted the edit left
        the two shadow rows' STORED names alone (the arm re-wrote them only on
        a real MOVE).  The rename was set on the fixture's ``Account``, which
        is not in this test's session, so it reached no row: re-read from the
        database after its flush, the account still has its old name
        (measured by the review of that step's checkpoint 2e-1, and again at
        2e-1a).  So the half never exercised its intended subject, the arm's
        re-write of stored names gated on a real move, which goes with that
        arm at the step's leaf 3b.

        Two mutants were measured against the half.  Under one that defaults
        an absent endpoint to the OTHER side, the name half fails on its own,
        and in its original order the endpoint half fails first (measured at
        checkpoint 2e-1a and by its review).  Under one that renames the
        destination ACCOUNT on every edit (the review of checkpoint 2e-2a-1
        named it R-RENAME-ON-EDIT), the name half, re-expressed on the legs,
        fails while the endpoint half passes; in this file the source move's
        expense-label assert failed under it (measured at checkpoint 2e-2a-1a
        and by that review), and only because that case's expected label was
        read off a fixture account outside the test's session.  That case
        reads no label since that step's checkpoint 2e-2b-1b-3, and nothing
        in this file catches the mutant since (measured there).  That a
        leg's name is composed from its endpoints' current names is pinned by
        ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestALegsDisplayProjections``
        (``test_the_label_is_composed_from_the_endpoints_current_names``).
        The endpoint half stands, read off the row re-read from the database
        since that checkpoint, where it read the object in the session
        (ruling R-BAL290 part 1).  Renamed from
        ``test_an_update_naming_no_account_leaves_the_pair_where_it_is``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, amount_ownership=AmountOwnership.own(Decimal("300.00")),
            )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                td["account"].id, td["savings_account"].id,
            )

    def test_a_move_that_would_make_the_endpoints_equal_is_refused(
        self, app, db, transfer_data
    ):
        """A transfer between one account and itself moves no money.

        ``ck_transfers_different_accounts`` says so at the storage tier; the
        door says it first, so the refusal is a message rather than an
        IntegrityError that rolls back the whole enclosing transaction.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="must be different"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    to_account_id=td["account"].id,
                )
            db.session.rollback()

    def test_a_move_making_a_loan_the_SOURCE_is_refused(
        self, app, db, transfer_data, seed_user
    ):
        """A transfer OUT of a loan is a disbursement, which is not modelled.

        The create path has refused it since the loan ledger was built
        (``_reject_transfer_out_of_loan``); the edit path could not, because it
        could not move a source at all.  Now it can, so it inherits the rule --
        otherwise the one guard would have a second door straight past it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Refusing Loan",
                origination_date=date(2020, 1, 1),
            )

            with pytest.raises(ValidationError, match="out of a loan"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, from_account_id=loan.id,
                )
            db.session.rollback()

    def test_a_legacy_loan_SOURCE_row_can_still_move_its_destination(
        self, app, db, transfer_data
    ):
        """The source refusal is asked about a source MOVE, not about every edit.

        **Found by an adversarial review of plan step R10-b.**  Asked on any
        endpoint move, ``_reject_transfer_out_of_loan`` re-graded an arrangement
        the edit does not touch: a transfer whose SOURCE is an amortizing loan
        -- written before ``create_transfer`` guarded it -- could not move its
        DESTINATION either, which froze exactly the legacy row that
        ``_reject_installment_move_before_loan`` states the discipline against.
        It also made the vacated-SOURCE arm of ``_resync_vacated_loan``
        unreachable, so that function's stated reason for taking both endpoints
        was false.

        The legacy state is PLANTED, because the create door no longer produces
        it -- which is the point: the row exists on data written before the
        guard, and an edit must not be refused for a shape it does not change.
        It is planted on the transfer alone since plan step
        ``balance:X-bi-6-4d-2``, where it was planted on the transfer AND its
        expense shadow: a leg is derived from its transfer, so there is no
        second row to plant it on (ruling R-BAL167 class 1).  Each move is
        read off the row re-read from the database since that step's
        checkpoint 2e-2b-1b-3, where it read the object in the session and
        the moved side's leg (ruling R-BAL290 part 1).
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Legacy Source Loan",
                origination_date=date(2020, 1, 1),
            )
            elsewhere = self._other_savings(td, name="New Destination")
            xfer = _create_basic_transfer(td)
            # The pre-guard shape: money leaving a loan.
            xfer.from_account = loan
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )

            row = _reread(xfer.id)
            assert row.from_account_id == loan.id, (
                "the source it did not touch stayed where it was"
            )
            assert row.to_account_id == elsewhere.id

            # And moving that source OFF the loan is the edit the narrowing
            # makes reachable at all -- it is the vacated-SOURCE arm of
            # ``_resync_vacated_loan``, whose stated reason for taking both
            # endpoints was false while the guard refused every such row.
            before_entries = db.session.query(JournalEntry).count()
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                from_account_id=td["account"].id,
            )
            assert _reread(xfer.id).from_account_id == td["account"].id
            # The loan it left was reconciled by the move: a follow-up sync
            # writes nothing.
            after_move = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == after_move, (
                "the loan the source left was not reconciled"
            )
            assert after_move >= before_entries

    def test_a_move_onto_a_loan_is_graded_against_THAT_loan_s_origination(
        self, app, db, transfer_data
    ):
        """Ruling R-C follows the destination, not just the dates.

        A payment sitting comfortably after one loan's origination can sit
        BEFORE another's without its own installment moving at all, so an
        endpoint move re-asks the question even when no date field is in the
        payload.  A payment the fold would ERASE is refused here rather than
        silently splitting against a zero balance while the cash side debits
        checking in full.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Late Loan",
                origination_date=date(2099, 1, 1),
            )

            with pytest.raises(ValidationError, match="before it originates"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, to_account_id=loan.id,
                )
            db.session.rollback()

    def test_a_move_to_another_user_s_account_is_not_found(
        self, app, db, transfer_data, seed_second_user
    ):
        """Cross-user endpoints answer 404, not 400 (the security response rule).

        The service tier checks its own ownership so a caller that skips the
        route cannot re-point a transfer across an ownership line, and the
        message is identical to a missing account's -- no existence oracle.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    to_account_id=seed_second_user["account"].id,
                )
            db.session.rollback()

    def test_a_refused_amount_does_not_move_the_transfer_first(
        self, app, transfer_data
    ):
        """A refusal LATER in the payload still leaves the transfer and its legs untouched.

        **RE-WRITTEN after an adversarial review of plan step R10-b showed the
        first version could not fire.**  It paired a valid amount with an
        endpoint refusal, so the refusal it triggered was ``_resolve_endpoints``'
        own -- the FIRST gate in the function, and green however the writes
        below were ordered.  The property worth pinning is the opposite pairing:
        a VALID move beside an ILLEGAL amount.  The amount's refusal used to sit
        at the arm that assigns it, two writes after the endpoint move and after
        a loan payment's split reversal, so this exact payload moved the pair
        and reversed a ledger correction before deciding the edit was illegal.

        It reads what the refused update would have WRITTEN: the transfer's
        row re-read after a commit, which writes whatever the update staged
        (ruling R-BAL290 part 1, since plan step ``balance:X-bi-6-4d-2``'s
        checkpoint 2e-2b-1b-3).  A move assigns the endpoint RELATIONSHIPS
        (``_apply_endpoint_move``), whose columns sync only at a flush, and
        the update does not flush before the amount's refusal raises; with
        the refusal moved below the endpoint apply, the commit writes the
        staged move and the re-read destination column fails the case
        (measured at that checkpoint).  Until then it flushed explicitly and
        read the session's three columns and both legs' accounts and labels,
        the legs two shadow rows until that step (ruling R-BAL167 class 4);
        at checkpoint 2e-1a the flush was added, since its column elements had
        caught the move only through its leg re-read, a query whose autoflush
        wrote the staged move.  Renamed from
        ``test_a_refused_amount_does_not_move_the_pair_first``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td)
            before = (xfer.from_account_id, xfer.to_account_id, xfer.amount)

            with pytest.raises(ValidationError, match="must be positive"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    to_account_id=elsewhere.id,
                    amount_ownership=AmountOwnership.own(Decimal("-5.00")),
                )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id, row.amount) == before

    def test_a_refused_move_leaves_the_transfer_untouched(
        self, app, transfer_data
    ):
        """An endpoint refusal itself writes nothing either.

        The complementary half: the endpoint gates are read-only, so a move
        refused for its OWN reason -- here, endpoints that would end up equal --
        leaves the transfer exactly as it was, beside an amount the same
        request states.  Read off the row re-read after a commit, which writes
        whatever the refused update staged (ruling R-BAL290 part 1), since
        plan step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-3, where it
        read the session's three columns and both legs' accounts and labels.
        Renamed from
        ``test_a_refused_move_leaves_the_transfer_and_its_legs_untouched``,
        and before that ``test_a_refused_move_leaves_all_three_rows_untouched``:
        the legs were the transfer's two shadow rows until that step (ruling
        R-BAL167 class 4).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            before = (xfer.from_account_id, xfer.to_account_id, xfer.amount)

            with pytest.raises(ValidationError, match="must be different"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("999.00")),
                    to_account_id=td["account"].id,
                )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id, row.amount) == before

    def test_moving_a_settled_payment_off_a_loan_reconciles_the_loan_it_left(
        self, app, db, transfer_data
    ):
        """The accounts a move VACATES are reconciled, and reconciled LAST.

        ``sync_transfer_postings`` heals the LEGS by itself, but the two things
        it emits that are scoped to the transfer's CURRENT endpoints reach no
        further: its Step-5 anchor self-heal names ``(from_account_id,
        to_account_id)``, and a vacated LOAN's genesis ledger and recurring
        payment window still count a payment it no longer has.  Both are
        re-derived explicitly, after the cash reconcile has moved the legs.

        **The ORDER is pinned, but not by this case** -- an adversarial review
        of R10-b measured which tests fail when the vacated walk is hoisted
        above the cash reconcile, and this is not one of them.  The three that
        are: ``test_a_vacated_ACCOUNT_s_anchor_corrections_are_re_derived``,
        ``test_moving_a_settled_payment_ONTO_a_loan_reconciles_that_loan`` and
        ``test_a_settled_transfer_s_cash_legs_follow_the_move``.  Run first, the
        vacated walk reads a ledger still holding a net for a transfer with no
        shadow on that account and ``walk_account_ledger`` raises
        ``PostingError: Ledger account N holds a nonzero net for transfer ids
        [...] but no active shadow on account M resolves them; Transfer
        Invariant 1 is broken`` -- measured on a production clone before the
        order was fixed.  What THIS case pins is the vacated loan's re-sync
        AFTER the move (``_loan_posting._resync_vacated_loan``): remove it and
        the loan keeps a correction for a payment it no longer has.  Through
        plan step ``balance:X-bi-6-1b`` it pinned a split reversal BEFORE the
        move as well; the split links no row since ``X-bi-6-3`` (ruling
        R-BAL102), so the after-resync alone reverses the departed key.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Vacated Loan",
                origination_date=date(2020, 1, 1),
            )
            elsewhere = self._other_savings(
                td, name="Not A Loan", observed_on=_the_day_before_today(),
            )
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=loan.id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                ),
            )
            transfer_service.settle_transfer(
                xfer.id, td["user"].id,
            )
            db.session.flush()
            posted = _ledger_nets_for_transfer(xfer.id)
            assert len(posted) == 2, (
                "setup: a settled payment posts a cash leg on each side"
            )

            # The move itself is the assertion: a wrong order raises here, and
            # ``sync_loan_postings``' checked-projection assert would raise if
            # the loan's ledger and its walk disagreed afterwards.
            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            assert xfer.to_account_id == elsewhere.id
            after = _ledger_nets_for_transfer(xfer.id)
            assert sum(after.values()) == Decimal("0")
            # The loan's own ledger is left holding nothing of this transfer.
            assert set(after).isdisjoint(
                {ledger for ledger, net in posted.items() if net > 0}
            )

            # **The oracle for the vacated reconcile itself**: both walks are
            # idempotent, so re-running each one now must write NOTHING.  It is
            # the only assertion that fails when the vacated arm is DELETED
            # rather than merely mis-ordered -- the transfer's own legs
            # reconcile either way, and what is left behind is the LOAN's split
            # correction for a payment it no longer has, plus the vacated
            # account's own anchor corrections.  Without this the arm had no
            # firing control at all: removing it kept every other case green.
            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            account_posting_service.sync_account_anchor_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the loan the payment LEFT was not reconciled by the move"
            )

    def test_moving_a_settled_payment_ONTO_a_loan_reconciles_that_loan(
        self, app, db, transfer_data
    ):
        """The loan a payment JOINS is re-derived, not just the one it left.

        **The firing control for assigning the RELATIONSHIP rather than the
        foreign key.**  ``_sync_loan_postings_if_loan`` asks which account the
        transfer's destination IS, and SQLAlchemy does not refresh
        ``Transfer.to_account`` when only ``to_account_id`` moves -- not even
        across the following flush.  Measured before the fix: after re-pointing
        a Mortgage payment at savings, ``xfer.to_account.name`` still answered
        ``Mortgage``.  So a move OFF a loan reconciled the old loan by accident
        and a move ONTO one -- this case -- reconciled NOTHING, leaving the new
        loan's genesis ledger without the payment it had just received.

        The oracle is idempotence: both walks write nothing at a state they have
        already reconciled, so a second sync that WRITES means the move did not
        run the first.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Joined Loan",
                origination_date=date(2020, 1, 1),
            )
            xfer = _create_basic_transfer(td)
            transfer_service.settle_transfer(
                xfer.id, td["user"].id,
            )
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=loan.id,
            )
            db.session.flush()

            assert xfer.to_account_id == loan.id
            assert xfer.to_account.id == loan.id, (
                "the relationship must move with the column"
            )
            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the loan the payment JOINED was not reconciled by the move"
            )

    def test_a_vacated_LOAN_s_downstream_payments_are_re_split(
        self, app, db, transfer_data
    ):
        """Removing one payment re-derives the ones whose balance it moved.

        **The firing control for the post-move vacated reconcile.**  The
        pre-move reversal takes back the DEPARTING payment's own split; every
        LATER payment on that loan rode the running balance the departing one
        advanced, so their splits are stale the moment it goes.  That is
        exactly what ``_resync_loan_after_payment_left`` exists for on the
        delete path, and an endpoint move needs it for the identical reason.

        The oracle is idempotence plus the loan's own checked-projection
        assert: re-running ``sync_loan_postings`` at a reconciled state writes
        nothing, and refuses to commit at an unreconciled one.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Two Payment Loan",
                origination_date=date(2020, 1, 1),
                principal=Decimal("5000.00"),
            )
            elsewhere = self._other_savings(
                td, name="Off The Loan", observed_on=_the_day_before_today(),
            )
            payments = []
            for index in (0, 1):
                payment = transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=loan.id,
                        pay_period_id=td["periods"][index].id,
                        scenario_id=td["scenario"].id,
                        amount_ownership=AmountOwnership.own(Decimal("400.00")),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                        due_date=td["periods"][index].start_date,
                    ),
                )
                transfer_service.settle_transfer(
                    payment.id, td["user"].id,
                )
                payments.append(payment)
            db.session.flush()

            transfer_service.update_transfer(
                payments[0].id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the surviving payment's split was left riding a balance the "
                "departed payment no longer advances"
            )

    def test_a_vacated_ACCOUNT_s_anchor_corrections_are_re_derived(
        self, app, transfer_data
    ):
        """An account the money stopped passing through re-bases its assertion.

        The other half of the post-move vacated reconcile, and the half
        ``sync_transfer_postings`` cannot reach: its Step-5 self-heal names the
        transfer's CURRENT endpoints, so the account just vacated is outside it.
        An anchor correction is "what the owner asserted minus what the ledger
        walked", and the walk on that account just lost this transfer's leg --
        so the correction is stale until something re-derives it.

        The true-up is dated AFTER the settle deliberately: an assertion made
        BEFORE the money moved does not depend on it, so the correction would
        not move and the case would pass whether or not the arm ran.
        """
        with app.app_context():
            td = transfer_data
            assert self._entries_a_re_sync_writes_after_moving_off(
                td, td["savings_account"], "to_account_id",
            ) == 0, (
                "the vacated account's anchor correction still counts a "
                "transfer that no longer passes through it"
            )

    def test_a_vacated_SOURCE_s_anchor_corrections_are_re_derived(
        self, app, transfer_data
    ):
        """The case above on the other side: the account the money stopped LEAVING.

        The door works out the vacated source and the vacated destination as
        two separate expressions (``_endpoints._resolve_endpoints``), and the
        case above moves only the destination.  So the source's expression had
        no catcher: a mutant handing the walk the owner's id in place of the
        vacated source's account id failed no case in the transfer, statement
        match and reconcile families that passes without it (measured
        2026-10-22, ruling ``balance:R-BAL356``'s site ``ID1-VACATED-SRC``).
        That id names no account, so the walk re-derived nothing, and
        checking's anchor correction kept counting a transfer that no longer
        leaves it.  Both cases share one body,
        :meth:`_entries_a_re_sync_writes_after_moving_off`.
        """
        with app.app_context():
            td = transfer_data
            assert self._entries_a_re_sync_writes_after_moving_off(
                td, td["account"], "from_account_id",
            ) == 0, (
                "the vacated source's anchor correction still counts a "
                "transfer that no longer leaves it"
            )

    def test_a_move_and_a_settle_in_ONE_call_settle_against_the_new_loan(
        self, app, db, transfer_data
    ):
        """A move and a settle in ONE call land on the destination it LEAVES.

        The two acts compose: the cash legs post against the loan the transfer
        is re-pointed at, and that loan books the payment's split -- asserted by
        idempotence, since a follow-up ``sync_loan_postings`` that WRITES means
        the call did not.

        **It does not pin the ORDER of the endpoint apply against the settle
        dispatch, and that is said because it was checked**: moving the apply
        after the dispatch leaves this case green.  The one reader that would
        answer differently is the auto-derived loan freeze, which needs a
        template-linked derive-mode payment re-pointed between two loans while
        settling -- unreachable from any route and from the maintain pass.  See
        ``_endpoints._apply_endpoint_move`` for why the order is kept anyway.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="One Call Loan",
                origination_date=date(2020, 1, 1),
                principal=Decimal("5000.00"),
            )
            xfer = _create_basic_transfer(td)
            db.session.flush()
            before = _ledger_nets_for_transfer(xfer.id)
            assert before == {}, "setup: a projected transfer posts nothing"

            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                to_account_id=loan.id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.flush()

            assert xfer.to_account_id == loan.id
            # The cash legs posted, and against the LOAN's ledger.
            after = _ledger_nets_for_transfer(xfer.id)
            assert len(after) == 2
            assert sum(after.values()) == Decimal("0")
            # And the loan booked the payment: a follow-up sync is a no-op, so
            # the split the settle owed was written during the call.
            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the settle did not book this payment against the loan it "
                "was re-pointed at"
            )

    def test_a_settled_transfer_s_cash_legs_follow_the_move(
        self, app, db, transfer_data
    ):
        """The posted ledger moves with the money it is about.

        ``reconcile_periods`` takes the per-ledger-account delta over the UNION
        of what is posted and what is targeted, so the vacated ledger account
        reverses to zero in the same pass the new one posts -- one pass, no
        residue.  Asserted on the NETS rather than on entry counts, because what
        must be true is that no account is left holding money the transfer no
        longer moves through it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(
                td, observed_on=_the_day_before_today(),
            )
            transfer_service.settle_transfer(
                xfer.id, td["user"].id,
            )
            db.session.flush()
            vacated = _ledger_nets_for_transfer(xfer.id)
            assert len(vacated) == 2, "setup: a settled transfer posts two legs"

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            after = _ledger_nets_for_transfer(xfer.id)
            assert len(after) == 2
            # The MAGNITUDE, not just the shape: the transfer is $250.00, so
            # the source ledger holds -250.00 (money leaving) and the ledger of
            # whichever account the money now arrives at holds +250.00.  An
            # adversarial review of this step found the first version asserting
            # only "two legs summing to zero", which a mutation posting $125.00
            # each way would have passed.
            [(source_ledger, source_net)] = [
                (ledger, net) for ledger, net in vacated.items() if net < 0
            ]
            assert source_net == Decimal("-250.00")
            assert after[source_ledger] == Decimal("-250.00")
            destination = [
                ledger for ledger in after if ledger != source_ledger
            ]
            assert len(destination) == 1
            assert after[destination[0]] == Decimal("250.00")
            # And it is a DIFFERENT ledger than the one it left, which is at
            # zero and so absent from the nets entirely.
            assert destination[0] not in vacated
            assert sum(after.values()) == Decimal("0")
