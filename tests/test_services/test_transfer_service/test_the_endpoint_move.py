"""The edit door's ENDPOINT arm (plan step R10-b): moving a transfer's source or destination, the
moves it refuses, and where a settled payment's lines land when it moves, off the loan or account
it leaves and onto the one it reaches.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import AccountType
from app.services import (
    account_service,
    transfer_service,
)
from app.services.ledger_lines import linked_key
from app.services.loan_ledger import walk_loan_ledgers
from app.exceptions import NotFoundError, ValidationError
from tests._ledger_reads import account_net
from tests._test_helpers import create_loan_account
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
    edit outright because nothing could carry it.  The arm moves the transfer,
    and the worked-out ledger follows it to both sides of the move (the arm
    reconciled the stored ledger too until plan task plan#3075).  Until plan step
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
    update's own flush, as the reconcile inside the update did, and
    :meth:`test_moving_a_settled_payment_ONTO_a_loan_moves_its_relationship`'s
    relationship read is the firing control for a move that assigns the
    foreign key and not the relationship, which a re-read row cannot be: it
    loads the relationship by that column.
    """

    def _other_savings(self, td, name="Second Savings", observed_on=None):
        """Create a second savings account for this owner.

        Through ``account_service.create_account`` rather than an ``Account``
        insert, because that factory is the real door: it stages the account's
        origination assertion and its books' opening, the bound the transfer
        arm reads (below).  *It also PAIRED the account with its
        chart-of-accounts row until plan task plan#3133, and a hand-rolled row
        failed a move with a missing-pairing ``PostingError`` rather than
        exercising it.*

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
        was false (plan task plan#3075 deleted the function).

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
            # makes reachable at all -- it was the vacated-SOURCE arm of
            # ``_resync_vacated_loan`` (deleted at plan task plan#3075), whose
            # stated reason for taking both endpoints was false while the
            # guard refused every such row.
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                from_account_id=td["account"].id,
            )
            assert _reread(xfer.id).from_account_id == td["account"].id

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

    def test_moving_a_settled_payment_off_a_loan_takes_its_lines_off_it(
        self, app, db, transfer_data
    ):
        """A settled payment moved off a loan takes its lines off the loan's own line.

        A $250.00 payment into a loan, settled, is re-pointed at a savings
        account: the move succeeds, its lines still balance, and none of them
        is left on the loan's own line.  The loan's split of the payment is
        worked out from the loan's walk and no record, so it is not among the
        transfer's lines.  What this case pinned besides, the vacated loan's
        STORED re-sync after the move (``_loan_posting._resync_vacated_loan``),
        was graded by an idempotence oracle on the stored journal until plan
        step plan#2914 took the store's grading off the suite, and plan task
        plan#3075 deleted the re-sync.
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

    def test_moving_a_settled_payment_ONTO_a_loan_moves_its_relationship(
        self, app, db, transfer_data
    ):
        """The relationship moves with the column to the loan a payment JOINS.

        **The firing control for assigning the RELATIONSHIP rather than the
        foreign key.**  The settle's auto-derived loan freeze asks which account
        the transfer's destination IS (and ``_sync_loan_postings_if_loan`` did,
        until plan task plan#3075 deleted it), and SQLAlchemy does not refresh
        ``Transfer.to_account`` when only ``to_account_id`` moves -- not even
        across the following flush.  Measured before the fix: after re-pointing
        a Mortgage payment at savings, ``xfer.to_account.name`` still answered
        ``Mortgage``.  So a move OFF a loan reconciled the old loan by accident
        and a move ONTO one -- this case -- reconciled NOTHING, leaving the new
        loan's genesis ledger without the payment it had just received.

        The relationship read below is the control.  The stored re-sync it led
        to was graded by an idempotence oracle on the stored journal until plan
        step plan#2914 took the store's grading off the suite, and plan task
        plan#3075 deleted it.
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

    def test_a_move_and_a_settle_in_ONE_call_settle_against_the_new_loan(
        self, app, db, transfer_data
    ):
        """A move and a settle in ONE call land on the destination it MOVES TO.

        The two acts compose: the call re-points the transfer at the loan and
        settles it, so its $250.00 leaves Checking and arrives on the LOAN's
        line, not the Savings it left, and the loan's walk splits it.  That
        the loan's STORED split was written during the call was graded by an
        idempotence oracle on the stored journal until plan step plan#2914
        took the store's grading off the suite; the walk's payment is its
        place on the read (plan task plan#3394).  The split's own arithmetic
        is ``test_ledger_lines/test_a_loans_lines``'s, graded by hand there.

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
            # A projected transfer holds no record, so asking its records could
            # answer nothing whatever the ledger held: ask the account.
            assert account_net(
                db.session, td["account"].id, td["scenario"].id,
            ) == Decimal("1000.00"), "setup: a projected transfer moves nothing"

            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                to_account_id=loan.id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.flush()

            assert xfer.to_account_id == loan.id
            # The cash lines post, and against the LOAN's line: 250.00 out of
            # Checking, 250.00 into the loan, none on the Savings it left.
            assert _ledger_nets_for_transfer(xfer.id) == {
                linked_key(td["account"]): Decimal("-250.00"),
                linked_key(loan): Decimal("250.00"),
            }
            # The loan's walk holds the payment, split into parts that sum
            # back to its cash (interest + escrow + principal + excess).
            (split,) = [
                outcome for outcome in walk_loan_ledgers(
                    [loan.id], td["scenario"].id,
                )[loan.id].payment_splits
                if outcome.source.transfer.id == xfer.id
            ]
            assert split.cash == Decimal("250.00")
            assert (
                split.interest + split.escrow + split.principal + split.excess
            ) == Decimal("250.00")

    def test_a_settled_transfer_s_cash_legs_follow_the_move(
        self, app, db, transfer_data
    ):
        """The ledger moves with the money it is about.

        The vacated account holds nothing of the transfer after the move, and
        the account it now reaches holds it.  Asserted on the NETS, because
        what must be true is that no account is left holding money the
        transfer no longer moves through it.
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
