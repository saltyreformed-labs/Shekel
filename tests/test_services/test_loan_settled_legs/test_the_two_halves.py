"""Every payment into the loan in exactly one half, settled or planned (ruling R-BAL140), in the
loan walk and in the contribution feed, and the posting sync resolving a stale movement's
transfer through its lineage.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.settled_figure import SettledFigure
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import loan_loaders, transfer_service
from app.services.loan_ledger import walk_loan_ledger
from app.services.loan_posting_service._linked_ledger import _movement_nets_by_date
from app.services.posting_service import _ledger_account_for
from app.services.recorded_contributions import load_transfer_contributions_for_account
from tests._test_helpers import (
    basis_for,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    refused_by_database_rule,
    settle_day_columns,
    transfer_side_record,
)

from ._helpers import (
    _DUE,
    _PERIOD,
    _CLOSED_ON,
    _loan,
    _SETTLED_UNDATED,
    _stage_an_undated_loan_side,
)


#: The status-band rule's two refusals (``app/side_band_infrastructure``), as
#: the database words them.
_UNSETTLED_DATED = r"is not settled but 1 of its payment records are dated"


def _stage_a_dated_side_record(transfer, account, day, amount):
    """STAGE a DATED record on *transfer*'s side on *account*, of *amount*, uncommitted.

    The side's money moved on *day* while the transfer stays Projected --
    ruling **R-BAL140**'s "counted once by its money" drift as it is spelled
    since plan step ``balance:X-bi-6-4d-2``, where a side's record hangs off
    the TRANSFER (ruling **R-BAL88**) and no longer off its twin.  The
    *amount* that moved is the TRANSFER's settled figure, its one home since
    that step's 4d-4 half (the record carried it until then and states no
    figure now), stated here as a person typed it.  The status-band rule
    refuses the dated record at COMMIT, so it exists only inside this save;
    the caller commits and expects the refusal.  The to-side is the side on
    *account* in every caller here.
    """
    db.session.add(TransactionEntry(
        income_transfer_id=transfer.id,
        account_id=account.id,
        owner_id=transfer.user_id,
        user_id=transfer.user_id,
        description="Drift record",
        purchased_on=day,
        covers_settlement=True,
        **settle_day_columns(day),
    ))
    db.session.get(Transfer, transfer.id).settled_figure_pair = SettledFigure(
        amount,
        ref_cache.movement_figure_source_id(MovementFigureSourceEnum.TYPED),
    )
    db.session.flush()


class TestTheSettledHalfComplementsThePlanHalf:
    """Ruling R-BAL140: every payment into the loan is in exactly ONE half."""

    def test_every_transfer_state_lands_in_exactly_one_half(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The states this case covers, each placed by the transfer's status and its money.

        * Projected, nothing moved                    -> planned;
        * Paid with its movements                     -> settled;
        * a $0.00 close (Paid, no movement)           -> settled;
        * Paid, then reverted to Projected through the
          door, its records kept un-dated             -> planned;
        * Cancelled                                   -> neither;
        * Cancelled over its kept un-dated records    -> neither (excluded);
        * Paid, its loan side's record staged
          un-dated                                    -> refused at commit
          (rolled back: Paid and dated, settled).

        **A soft-deleted transfer is in NEITHER half, by design**, whatever
        its status: each half filters ``Transfer.is_deleted``.  It is no
        member here, because "neither" is a different claim than "exactly
        one".  A soft delete through the door takes a Paid payment's records
        off and returns it as a plan (ruling **R-BAL246**), so the PLAN
        half's filter is what drops a deleted Projected or Paid payment,
        graded by ``test_loan_payment_service/test_the_payment_history.py``'s
        ``test_excludes_deleted_transactions`` (both its cases); a ``$0.00``
        close held nothing and stays Paid (measured 2026-10-15), so the
        SETTLED half's filter is what drops that one, graded by
        :meth:`test_a_soft_deleted_zero_dollar_close_is_in_neither_half`.

        The old partition keyed the settled half on the TWIN's status, so a
        twin Paid under a Projected parent was in BOTH halves and a parent
        Paid over a still-Projected twin in neither.  Each transfer carries
        its own figure because ``uq_transfers_adhoc_dedupe`` refuses two
        identical ad-hoc transfers in one period.

        **The reverted member is the boundary of "its money moved"** (ruling
        **R-BAL140**): the revert KEEPS its records and releases their days
        (ruling **R-BAL61**), so records EXIST and none is DATED; the plan
        emits a leg for it because its transfer is Projected (the status
        alone since plan step ``balance:X-bi-6-5``'s third leaf; ruling
        **R-BAL79** also asked for a side with no dated movement until
        then), and the settled half, the plan half's complement, does not
        hold it.
        It is a NEW member at plan step ``balance:X-bi-6-4d-2``: no member
        stood in that state before.

        **Since plan step ``balance:X-bi-6-4d-2``** each side's record hangs
        off the transfer (ruling **R-BAL88**) and no reader reads a twin
        (ruling **R-BAL167**).  Two plants are REFUSED at commit (class 2)
        and asserted so: Cancelling the Cancelled-over-records member over
        its two DATED records (the status-band rule; it was "Cancelled over a
        dated movement", and its storable neighbour -- Paid, reverted and
        Cancelled through the door, its records kept un-dated -- is what
        stands, excluded as before); and un-dating the last member's loan
        side under its Paid parent (the band again; rolled back, Paid and
        dated, settled as it was -- the walk's refusal of it was graded
        beside this file until plan step ``balance:X-bi-6-5``'s third leaf
        deleted that refusal).

        **Four members left at plan step ``balance:X-bi-6-4d-2``**, where a
        transfer became one row (ruling **R-BAL258**).  Three planted the
        loan-side twin first: Paid with nothing moved, Paid with a dated
        movement under it, and that one with its twin hidden.  The twin was
        refused at the write by ``ck_transactions_names_no_transfer``
        whatever it carried -- measured bare, dated, Paid and dated, and Paid,
        dated and hidden -- so the movement and the hide planted after it
        could never be written: ONE refused shape, pinned on the income side in
        ``test_a_transfer_shadow_is_derived/test_the_declaration.py`` and,
        for a loan payment, in ``test_loan_plan_assembly.py``, until plan step
        balance:X-bi-6-5 dropped the column the twin named its transfer by,
        after which no statement can write it (ruling plan#1850).  The
        fourth, "parent Paid, twin still Projected" (its status written past
        the seam), is without its twin the SAME stored state as the
        ``$0.00`` close: measured, every ``budget.transfers`` column is equal
        bar the literal figure and name (the close stores its PLAN figure:
        ``$0.00`` is the absence of a record, ruling **R-BAL141**), both are
        Paid over no side record, and this partition reads nothing else --
        the posting ledger included.  R-BAL140's "settled parent with no
        movement" drift is therefore no longer a state distinct from the
        close, which holds it.
        """
        with app.app_context():
            loan = _loan(seed_user)
            checking = seed_user["account"]
            period = seed_periods[_PERIOD]

            def projected(amount):
                transfer = create_transfer(
                    seed_user, db.session, checking, loan, period,
                    amount=Decimal(amount), due_date=_DUE,
                )
                db.session.commit()
                return transfer

            plain = projected("1000.00")
            paid = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1001.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            zero = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1002.00"), settled_amount=Decimal("0.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            reverted = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1010.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            transfer_service.update_transfer(
                reverted.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert [
                transfer_side_record(
                    db.session, reverted.id, account.id,
                ).settled_on
                for account in (checking, loan)
            ] == [None, None], "the member: Projected over its two kept records"
            cancelled = projected("1006.00")
            db.session.get(Transfer, cancelled.id).status_id = (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            db.session.commit()
            cancelled_moved = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1008.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            db.session.get(Transfer, cancelled_moved.id).status_id = (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            with refused_by_database_rule(
                r"is not settled but 2 of its payment records are dated",
            ):
                db.session.commit()
            db.session.rollback()
            for status in (StatusEnum.PROJECTED, StatusEnum.CANCELLED):
                transfer_service.update_transfer(
                    cancelled_moved.id, seed_user["user"].id,
                    status_id=ref_cache.status_id(status),
                )
            db.session.commit()
            assert [
                transfer_side_record(
                    db.session, cancelled_moved.id, account.id,
                ).settled_on
                for account in (checking, loan)
            ] == [None, None], "the plant: Cancelled over its two kept records"
            paid_loan_side_undated = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1009.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            _stage_an_undated_loan_side(paid_loan_side_undated, loan)
            with refused_by_database_rule(_SETTLED_UNDATED):
                db.session.commit()
            db.session.rollback()

            halves = loan_loaders.income_legs(
                loan.id, seed_user["scenario"].id, options=(), leg_options=(),
            )
            settled = sorted(leg.transfer.id for leg in halves.settled)
            planned = sorted(leg.transfer.id for leg in halves.projected)

            assert settled == sorted([
                paid.id, zero.id, paid_loan_side_undated.id,
            ])
            assert planned == sorted([plain.id, reverted.id])
            assert cancelled.id not in settled + planned
            assert cancelled_moved.id not in settled + planned

    def test_a_soft_deleted_zero_dollar_close_is_in_neither_half(
        self, app, seed_user, seed_periods,
    ):
        """A $0.00 close deleted through the door: in NEITHER half, and the walk books no split.

        Not a member of the partition above: "neither half" is its own claim.
        The door's soft delete takes a Paid payment's records off and returns
        it as a plan (ruling **R-BAL246**), so a deleted payment that held
        records is the PLAN half's filter's to drop
        (``test_loan_payment_service/test_the_payment_history.py``'s
        ``test_excludes_deleted_transactions``).  A ``$0.00`` close holds no
        record (ruling **R-BAL141**: ``$0.00`` is the absence of one), so the
        delete takes nothing off and keeps it Paid
        (:func:`~app.utils.balance_predicates.delete_returns_to_plan`), and
        only the SETTLED half's own ``Transfer.is_deleted`` filter drops it.
        That half is the view the loan walk, the posting reader and the
        escrow guard read (:func:`~app.services.loan_loaders.income_legs`
        names them), so a deleted close it kept would still book the 03-01
        installment's whole charge as unpaid -- the split
        :class:`TestAZeroDollarCloseIsDatedByTheInstallmentItSkips` reads on
        a live close -- for a payment its owner deleted.

        The close is read before the delete too: in the settled half, with
        one split on the installment, so the delete is what moves it.  The
        guards: before the delete the close holds no side record (why the
        door keeps it Paid); after it, it is hidden and still settled.  A
        side record is not read AFTER the delete, because that read could not
        fail: the door takes every side record off before it hides the
        transfer (``movement_removal.remove_movements``, then ``is_deleted``,
        in ``transfer_service._delete``), so no transfer it deletes holds
        one, and the database makes that unbreakable -- it refuses to hide a
        transfer still holding one
        (``budget.refuse_hiding_a_transfer_holding_money`` in
        ``app/deleted_row_infrastructure.py``, ruling **R-CC92**).  Measured
        2026-10-15 on this case with the close settled at its plan and a
        delete that took nothing off: the commit was refused, "transfer 1
        was deleted while it still holds a recorded payment: take it off the
        books first".

        **The plan-half read is DOMINATED** (``after.projected == []``).  The
        deleted close is hidden AND Paid, so either of the plan half's two
        clauses drops it alone: it survived each dropped by itself, and
        failed first with both dropped at once (measured 2026-10-15).  Each
        clause is graded where it fails first -- ``Transfer.is_deleted`` by
        ``test_loan_payment_service/test_the_payment_history.py``'s
        ``test_excludes_deleted_transactions``, the Projected test by
        :meth:`test_every_transfer_state_lands_in_exactly_one_half` and by the
        two ``$0.00``-close dating cases in ``test_the_zero_dollar_close.py``,
        ``test_the_close_is_visible_from_its_installment_not_its_settle_day``
        and ``test_a_close_due_mar_10_is_dated_by_the_feb_22_installment`` --
        and the status guard here holds the Paid that keeps the second true.
        It stays as the claim's other half.

        Added at plan step ``balance:X-bi-6-4d-2``, because no PASSING test
        failed with the settled half's filter dropped (measured 2026-10-15:
        the full suite's failing set was unchanged, and a test already
        failing could reveal nothing).
        """
        with app.app_context():
            loan = _loan(seed_user)
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            close = create_settled_transfer(
                seed_user, db.session, checking, loan, seed_periods[_PERIOD],
                amount=Decimal("1000.00"), settled_amount=Decimal("0.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            transfer_id = close.id
            assert [
                transfer_side_record(db.session, transfer_id, account.id)
                for account in (checking, loan)
            ] == [None, None], "the guard: a $0.00 close holds no side record"
            before = loan_loaders.income_legs(
                loan.id, scenario_id, options=(), leg_options=(),
            )
            assert [leg.transfer.id for leg in before.settled] == [transfer_id]
            assert [
                outcome.due_date
                for outcome in walk_loan_ledger(loan.id, scenario_id).settled_splits
            ] == [_DUE]

            transfer_service.delete_transfer(
                transfer_id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            db.session.expire_all()
            deleted = db.session.get(Transfer, transfer_id)
            assert deleted.is_deleted is True, "the guard: the door hid it"
            assert deleted.status.is_settled is True, (
                "the guard: a close that held nothing stays settled, so only "
                "the settled half's filter can drop it"
            )

            after = loan_loaders.income_legs(
                loan.id, scenario_id, options=(), leg_options=(),
            )
            assert [leg.transfer.id for leg in after.settled] == []
            assert [leg.transfer.id for leg in after.projected] == []
            assert walk_loan_ledger(loan.id, scenario_id).settled_splits == []

    def test_a_drifted_payment_whose_money_moved_is_counted_once_by_its_money(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-BAL140's case: planned at $1,000.00, $900.00 moved on the loan side, parent Projected.

        The walk counted it ONCE, as a settled payment of the $900.00 that
        moved on 03-05 -- interest 500.00, principal 400.00 -- and the
        payment feed priced it at that record, never at the $1,000.00 plan.
        Under the first design (the transfer's status alone) it was in
        neither half: the plan skips a side whose money moved (R-BAL79) and a
        Projected parent failed the status test.

        **The state is REFUSED at commit since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2): the $900.00
        hung off the income twin, which no reader reads since, and the side's
        own DATED record under a Projected transfer is what the status-band
        rule refuses.  The readers' answers on it -- the walk's (900.00 on
        03-05, interest 500.00, principal 400.00), the payment history's
        confirmed 900.00 and the leg's 900.00 contribution -- are not
        asserted: no commit can leave the state they were asked of.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"), due_date=_DUE,
            )
            db.session.commit()
            transfer_id = transfer.id
            _stage_a_dated_side_record(
                transfer, loan, _CLOSED_ON, Decimal("900.00"),
            )

            with refused_by_database_rule(_UNSETTLED_DATED) as caught:
                db.session.commit()
            db.session.rollback()

            assert f"transfer {transfer_id} " in str(caught.value)
            assert transfer_side_record(
                db.session, transfer_id, loan.id,
            ) is None


class TestTheContributionFeedCountsADriftOnce:
    """The contribution feed reads the SAME partition (ruling R-BAL140).

    It ran its own until plan step balance:X-bi-6-4b -- settled TWIN rows
    beside every still-Projected transfer -- so a twin settled under a
    Projected parent was one contribution counted twice: a confirmed record
    AND a planned one.
    """

    def test_a_twin_settled_with_nothing_moved_is_one_planned_contribution(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """$250.00 planned, twin Paid, nothing moved: no twin can exist, ONE planned $250.00.

        The feed read it as ONE planned $250.00: the parent decides, and no
        money moved.  **The twin was REFUSED at the write from plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2, ruling
        **R-BAL258**): ``ck_transactions_names_no_transfer`` refused the
        income twin with or without the Paid status and the 03-05 day the
        drift wrote onto it (measured both ways), and the case asserted that
        refusal until plan step balance:X-bi-6-5 dropped the column the twin
        named its transfer by; no statement can write one now, so the
        refusal went with the column (ruling plan#1850).  The feed's answer
        on the transfer that stands is asserted as before, one planned
        $250.00, so nothing is left a reader could count twice.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Drift Savings", Decimal("0.00"),
            )
            create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_PERIOD], amount=Decimal("250.00"),
            )
            db.session.commit()

            feed = load_transfer_contributions_for_account(
                basis_for(savings, seed_user["scenario"]), savings.id,
                [period.id for period in seed_periods],
            )
            assert [
                (record.amount, record.is_confirmed) for record in feed.records
            ] == [(Decimal("250.00"), False)]

    def test_a_twin_settled_with_its_money_moved_is_one_confirmed_contribution(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """$250.00 planned, $240.00 moved, parent Projected: refused at commit.

        The feed read it as ONE confirmed $240.00.  **The state is REFUSED at
        commit since plan step ``balance:X-bi-6-4d-2``** (ruling **R-BAL167**
        class 2): the $240.00 hung off the income twin, which no reader reads
        since, and the side's own DATED record under a Projected transfer
        (ruling **R-BAL88**) is what the status-band rule refuses.  The
        feed's answer on it, ``[($240.00, confirmed)]``, is not asserted: no
        commit can leave the state it was asked of.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Drift Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_PERIOD], amount=Decimal("250.00"),
            )
            db.session.commit()
            transfer_id = transfer.id
            _stage_a_dated_side_record(
                transfer, savings, _CLOSED_ON, Decimal("240.00"),
            )

            with refused_by_database_rule(_UNSETTLED_DATED) as caught:
                db.session.commit()
            db.session.rollback()

            assert f"transfer {transfer_id} " in str(caught.value)
            assert transfer_side_record(
                db.session, transfer_id, savings.id,
            ) is None


class TestTheLoanSidesMovementIsItsRecord:
    """A paid loan payment's movement is the loan side's record, posted on its day.

    The class graded the posting sync resolving a stale movement's transfer
    through ``movement_parent``, under a dead shadow; no shadow can exist
    since plan step ``balance:X-bi-6-5`` (the case's docstring says how).
    """

    def test_a_paid_loan_payment_s_movement_posts_on_its_settle_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Paid $1,000.00: the loan side's movement posts $1,000.00 on 03-05.

        The case paid $1,000.00, then soft-deleted its twin around the
        service.  The movement was no leg's record any more (a dead shadow's),
        so the walk expected no cash for it and the posted cash leg was STALE;
        the probe had to still name its transfer so the transfer's pair door
        reversed it.

        **The state is REFUSED since plan step ``balance:X-bi-6-4d-2``**
        (ruling **R-BAL167** class 2): the loan side's payment is its record
        on the TRANSFER (ruling **R-BAL88**), read here off the side link
        (class 1).  Until plan step ``balance:X-bi-6-4d-2`` a movement sat
        under a twin only where it was planted -- both records re-parented
        under their twins in one statement -- and a twin hidden holding one
        was refused by the deleted-row rule's row arm.  **Since that step the
        twin itself cannot be stored** (ruling **R-BAL258**): the loan-side
        twin the plant re-parented the movement under was refused at the
        write by ``ck_transactions_names_no_transfer``, so neither the
        re-parenting nor the hide has a row to act on, and the sync's answer
        on a stale movement under a dead shadow -- the posted cash leg
        reversed, ``_movement_nets_by_date`` reading ``{}`` -- is not
        asserted.  The case asserted that refusal until plan step
        balance:X-bi-6-5 dropped the column the twin named its transfer by,
        after which no statement can write one (ruling plan#1850).  What
        stands is the movement as the side's record, posted on 03-05; the
        read after the plant graded that the refused hide's rollback
        restored the re-parented record, and went with the re-parenting.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            transfer_id = transfer.id
            scenario_id = seed_user["scenario"].id
            linked_id = _ledger_account_for(loan.id).id
            movement_id = transfer_side_record(
                db.session, transfer_id, loan.id,
            ).id
            assert _movement_nets_by_date(linked_id, scenario_id) == {
                movement_id: {_CLOSED_ON: Decimal("1000.00")},
            }
