"""What a side of a transfer shows: which side an account is on decides its direction, each side
shows its transfer's stored columns, and its label is composed from the endpoints' current names
while it names no transaction definition and does not recur.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.amount_ownership import AmountOwnership
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.transfer_legs import (
    TransferLeg,
    grid_transfer_leg,
    leg_label,
    leg_of,
    transfer_side_leg,
)
from app.services.transfer_service import (
    TransferSpec,
    create_transfer as create_transfer_from_spec,
    settle_transfer,
    update_transfer,
)
from tests._test_helpers import basis_for, generate_transfer_of

from ._helpers import _savings, _shown, _held, _reread_sides


class TestLegOf:
    """Which side of a transfer an account is on decides the leg's direction."""

    def test_the_to_side_is_income_and_the_from_side_is_expense(self):
        transfer = Transfer(id=7, from_account_id=1, to_account_id=2)

        income = leg_of(transfer, 2)
        expense = leg_of(transfer, 1)

        assert income == TransferLeg(
            transfer=transfer, account_id=2, is_income=True,
        )
        assert income.is_expense is False
        assert expense == TransferLeg(
            transfer=transfer, account_id=1, is_income=False,
        )
        assert expense.is_expense is True

    def test_an_account_on_neither_side_is_refused(self):
        transfer = Transfer(id=7, from_account_id=1, to_account_id=2)

        with pytest.raises(ValueError, match="neither side of transfer 7"):
            leg_of(transfer, 3)

    def test_a_leg_reads_its_parents_columns_not_its_relationships(self, app):
        """A leg reads its parent's foreign-key COLUMNS, never their relationships.

        Every column the leg's code (``transfer_legs._leg``) reads that has a
        relationship on :class:`~app.models.transfer.Transfer` is here: the
        status, the category, the scenario, the period and the two
        endpoints, read on both sides.  The owner's column is here as well,
        though it has no relationship on a transfer: a leg's money is booked
        in its owner's ledger, and :class:`TestBothSidesShowTheTransfer`'s
        three transfers share one owner, so they cannot grade that read.  The
        transfer is never added to a session: it holds the columns and none
        of the rows they name, so a leg that read a relationship's id would
        raise here.
        :class:`TestBothSidesShowTheTransfer` cannot tell the two reads
        apart, because it reads a committed, re-read row, where a column and
        its relationship always agree.  The edit door writes a period or a
        category to the column alone (``transfer_service._update``; the
        status seam expires ``status`` after its write), so until the row is
        flushed and re-read that relationship can still name the old row;
        whether any reader asks a leg inside that window is not measured.

        The status is the Projected one, born as a transfer is; its id is
        the seed's, so a guard checks first that no two of the transfer's
        ids coincide, and a read of the wrong column cannot agree.
        """
        with app.app_context():
            transfer = Transfer(
                id=7, from_account_id=31, to_account_id=37,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=5, scenario_id=13, pay_period_id=11, user_id=17,
            )
            ids = (
                transfer.id, transfer.from_account_id, transfer.to_account_id,
                transfer.status_id, transfer.category_id, transfer.scenario_id,
                transfer.pay_period_id, transfer.user_id,
            )
            assert len(set(ids)) == len(ids), (
                f"precondition: two of the transfer's ids coincide: {ids}"
            )

            assert [
                (leg.account_id, leg.status_id, leg.category_id,
                 leg.scenario_id, leg.pay_period_id, leg.user_id)
                for leg in (leg_of(transfer, 31), leg_of(transfer, 37))
            ] == [
                (31, transfer.status_id, 5, 13, 11, 17),
                (37, transfer.status_id, 5, 13, 11, 17),
            ]


class TestBothSidesShowTheTransfer:
    """What a side of a transfer shows is what the transfer's row holds (ruling R-BAL290)."""

    def test_both_sides_show_the_row_in_every_field_the_table_names(
        self, app, seed_user, seed_periods,
    ):
        """ONE fact, one test: each side shows the stored row's value, field by field.

        A transfer is one stored row since plan step ``balance:X-bi-6-4d-2``,
        and its two sides are read off it every time, so "both sides show the
        transfer's amount" can break only in the code that READS a side,
        never in an edit (ruling **R-BAL290** part 1, the developer,
        2026-10-14).  This is that reader's one test; a door's test checks
        what the door wrote on the row.

        The table names nine of the row's columns -- the amount, status,
        period, due date, category, override flag, notes and the two
        endpoints, each side showing its own -- and the status, period and
        category ROWS readers take through a side (the grid's cell its
        status, the dashboard's bills its period and category).  Four more
        of the row's columns are read through a side and are not in it: the
        id (the side's cell key), the owner, the scenario and the delete
        flag; :class:`TestLegOf`'s in-memory case grades the owner's and the
        scenario's reads.  The table also reads each side's
        ``template_id``, which no column answers: a side names no
        transaction definition, so the table expects ``None`` on every
        side.  The three transfers are ad-hoc and committed, so a side that
        answered a persisted id for a transfer with no definition -- its
        transfer's own -- disagrees here, which neither case of
        :class:`TestALegsDisplayProjections` can see: the label case's
        transfers have no id, and the saved transfer there has a definition
        (the mutant TMPL-ONEOFF-ID of the review of plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-2a, which passes both).

        Three transfers are written through the doors: Savings to Reserve
        at $1,062.48, Projected, in the sixth period, due nine days in,
        Rent, with notes, the override flag clear; Reserve to Savings at
        $437.19, Cancelled, in the fourth period, due four days in,
        Groceries, with other notes and the flag set (through the edit door,
        which the create door does not take); and Reserve to Savings at
        $83.06, Projected, in the second period, the flag clear, with no due
        date, no category and no notes.  The third is the row whose optional
        columns are empty, so a side that answers something for an empty
        one -- a default category, day or note -- disagrees with it.  It
        joined the table at plan step ``balance:X-bi-6-4d-2``'s checkpoint
        2e-2b-1b-2: until then both rows carried a category, and a side that
        answered one for a transfer filed under none failed only
        ``test_transfer_service/test_the_edit_door.py``'s
        ``test_a_category_cleared_to_none_leaves_the_transfer_under_none``,
        whose two side reads were dropped there.  None of the three is
        settled: a settled status writes the payment records this case is
        not about.  All three are committed; each side is read off the
        re-read row, and what the table expects is the row as independent
        SQL reads it.

        Two guards on the input come first: the doors stored every stated
        value, and over the three transfers no named column holds one value
        on all three, and each named column's values, transfer by transfer,
        differ from every other column's, the columns taken from the model's
        table (``Transfer.__table__``) rather than listed here.
        So a side that reads one of the nine named columns off any other
        column of its row, or answers a constant for one, disagrees with the
        table on at least one of the three transfers.  That is the guard's
        whole reach: it says nothing of a side reading another table, a
        computed value, or a relationship in place of its column (a committed
        row's two always agree; :class:`TestLegOf` pins the columns in
        memory), and the four columns the table leaves out and the
        ``template_id`` read, whose expected value is the constant ``None``,
        are outside it.

        Each side is read through two of the doors to a leg: by SIDE
        (``transfer_side_leg``, how the transfer service reads one) and by
        ACCOUNT (``grid_transfer_leg``, through ``leg_of``, how the grid and
        the fold reach one), and its amount through ``leg_amounts_by_key``,
        the producer the grid prices a leg's cell with.
        """
        with app.app_context():
            owner_id = seed_user["user"].id
            # Two fresh accounts, not the seeded checking: its id is the
            # Cancelled status's, the kind of coincidence the guard below
            # refuses.
            reserve = _savings(seed_user, name="Reserve")
            savings = _savings(seed_user)
            stated = {
                "projected": {
                    "amount": Decimal("1062.48"),
                    "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
                    "pay_period_id": seed_periods[5].id,
                    "due_date": seed_periods[5].start_date + timedelta(days=9),
                    "category_id": seed_user["categories"]["Rent"].id,
                    "is_override": False,
                    "notes": "Insurance reserve",
                    "from_account_id": savings.id,
                    "to_account_id": reserve.id,
                },
                "cancelled": {
                    "amount": Decimal("437.19"),
                    "status_id": ref_cache.status_id(StatusEnum.CANCELLED),
                    "pay_period_id": seed_periods[3].id,
                    "due_date": seed_periods[3].start_date + timedelta(days=4),
                    "category_id": seed_user["categories"]["Groceries"].id,
                    "is_override": True,
                    "notes": "Quarterly top-up",
                    "from_account_id": reserve.id,
                    "to_account_id": savings.id,
                },
                "unfiled": {
                    "amount": Decimal("83.06"),
                    "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
                    "pay_period_id": seed_periods[1].id,
                    "due_date": None,
                    "category_id": None,
                    "is_override": False,
                    "notes": None,
                    "from_account_id": reserve.id,
                    "to_account_id": savings.id,
                },
            }
            ids = {
                label: create_transfer_from_spec(TransferSpec(
                    user_id=owner_id,
                    from_account_id=value["from_account_id"],
                    to_account_id=value["to_account_id"],
                    pay_period_id=value["pay_period_id"],
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(value["amount"]),
                    status_id=value["status_id"],
                    category_id=value["category_id"],
                    notes=value["notes"],
                    due_date=value["due_date"],
                )).id
                for label, value in stated.items()
            }
            update_transfer(ids["cancelled"], owner_id, is_override=True)
            db.session.commit()
            db.session.expire_all()
            table = Transfer.__table__
            stored = {
                label: db.session.execute(
                    select(table).where(table.c.id == transfer_id),
                ).mappings().one()
                for label, transfer_id in ids.items()
            }

            # Guards on the INPUT, not reads.
            named = tuple(stated["cancelled"])
            assert {
                label: {column: row[column] for column in named}
                for label, row in stored.items()
            } == stated, "precondition: the doors stored every stated value"
            values = {
                column: tuple(row[column] for row in stored.values())
                for column in table.columns.keys()
            }
            assert (
                [column for column in named if len(set(values[column])) == 1],
                [
                    (column, other) for column in named for other in values
                    if other != column and values[other] == values[column]
                ],
            ) == ([], []), (
                "precondition: a named column holds one value on every "
                "transfer, or the same values as another column, so a wrong "
                "read of it could agree with the table"
            )

            rows = {
                label: db.session.get(Transfer, transfer_id)
                for label, transfer_id in ids.items()
            }
            basis = basis_for(reserve, seed_user["scenario"])

            assert {
                label: {
                    "expense by side": _shown(
                        transfer_side_leg(row, is_income=False), basis,
                    ),
                    "income by side": _shown(
                        transfer_side_leg(row, is_income=True), basis,
                    ),
                    "expense by account": _shown(
                        grid_transfer_leg(row, row.from_account_id), basis,
                    ),
                    "income by account": _shown(
                        grid_transfer_leg(row, row.to_account_id), basis,
                    ),
                }
                for label, row in rows.items()
            } == {
                label: {
                    "expense by side": _held(row, is_income=False),
                    "income by side": _held(row, is_income=True),
                    "expense by account": _held(row, is_income=False),
                    "income by account": _held(row, is_income=True),
                }
                for label, row in stored.items()
            }


class TestALegsDisplayProjections:
    """The parent's columns read through the leg, and the label composition."""

    def test_the_label_is_composed_from_the_endpoints_current_names(self):
        """Each side's label names the OTHER endpoint, as it is named now.

        Three transfers, one of each shape the model's own accessors
        (``Transfer.recurs``, ``Transfer.is_placed``) tell apart: GENERATED,
        its definition carrying a recurrence rule; PLACED, its definition
        carrying none (a one-time transfer); and AD-HOC, naming no
        definition.  Each carries a custom name, and each linked one carries
        its link on BOTH spellings, the column and the relationship, its
        definition active.  Guards on the input check the three shapes and
        the links first.  On all six sides the case reads the label, the
        ``template_id`` and ``recurs``: the label names the other endpoint
        whatever the transfer is named, and no side names a transaction
        definition or recurs, whatever its transfer's shape (the grid's
        row-key contract, ``TransferLeg.template_id``).  So a side that
        answered its transfer's own name, or its link, on any one of the
        three shapes -- through the column or the relationship, or only where
        the rule, the active flag, ``recurs`` or ``is_placed`` is set --
        disagrees here.  None of the three has an id or a record, so the
        case cannot see an answer gated on either.  For the link and
        ``recurs``, the next case asks a saved GENERATED transfer's sides
        while it is still Projected and again once it is settled, and the
        projection table (:class:`TestBothSidesShowTheTransfer`) committed
        AD-HOC ones; a saved PLACED transfer's sides are asked by no case
        here.  Neither reads a label, so a label gated on a saved transfer
        or on a record is graded in other files, both sides in each of two
        corners, and both catchers hold an AD-HOC transfer: a saved transfer
        whose sides hold no record (a Projected one) by
        ``test_statement_match/test_leg_candidates.py``'s
        ``TestAStillPlannedTransferIsOfferedAsItsLeg`` case
        ``test_each_side_is_offered_on_its_own_account_keyed_by_the_transfer``,
        and a settled one, each side holding its dated record, by
        ``test_calendar_service.py``'s ``TestATransferIsALegOfItsParent``
        case ``test_a_settled_leg_is_a_paid_cell_worth_its_record``.  Each
        fails under a side answering its transfer's own name in its corner
        (only where the transfer is saved and the side holds no record, or
        only where the side holds one), on the expense side alone and on the
        income side alone (measured at the fix of the delta review of plan
        step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-2c, and again by
        that fix's own review).  Two neighbouring states are asked their
        label by no case named here: a REVERTED transfer, Projected over
        each side's kept un-dated record (ruling R-BAL61), and a saved
        transfer of a definition, generated or placed.  A side answering its
        transfer's own name only in either state passed every file named
        here (measured by that fix's review).  ``test_calendar_service.py``'s
        ``TestDayAssignment::test_due_date_none_fallback``,
        ``test_spending_report_service.py``'s
        ``TestATransferIsALegOfItsParent`` and
        ``test_the_entry_doors_ask_the_movement.py``'s
        ``TestATransfersPaymentIsNamedByItsLeg`` read the expense side's
        label alone.

        ``test_transfer_service/test_the_create_door.py``'s ``test_with_custom_name`` and
        ``test_with_template_id`` asked a created transfer's two sides these
        questions until plan step ``balance:X-bi-6-4d-2``'s checkpoint
        2e-2b-1b-2, which moved them here (ruling R-BAL290 part 1); the
        second asked them of an ENGINE-generated transfer of a definition
        whose rule recurs every period.  Its
        ``test_leg_names_reference_the_other_endpoint``, which asked only
        each side's label, was deleted there.  The case moved held one
        transfer, its link on the column alone, and two reviews widened it.
        After that checkpoint's review the link rides the relationship too
        (a side reading the relationship passed this case and, by that
        review's full-suite run, changed no other test's outcome: its mutant
        TMPL-REL), which made the transfer PLACED, and an ad-hoc transfer is
        asked its label.  After checkpoint 2e-2b-1b-2a's review the
        generated transfer joined and every side is asked all three: a side
        answering its link only where its transfer recurs passed (its HIGH 1).
        """
        savings = Account(name="Savings")
        checking = Account(name="Checking")
        generated = Transfer(
            from_account=checking, to_account=savings, name="Mortgage Payment",
            template=TransferTemplate(
                id=5, name="Mortgage", is_active=True,
                recurrence_rule=RecurrenceRule(),
            ),
        )
        placed = Transfer(
            from_account=checking, to_account=savings, name="Car Down Payment",
            template=TransferTemplate(id=6, name="Down Payment", is_active=True),
        )
        ad_hoc = Transfer(
            from_account=checking, to_account=savings, name="Rent Share",
        )
        generated.transfer_template_id, placed.transfer_template_id = 5, 6
        transfers = (generated, placed, ad_hoc)
        for transfer in transfers:
            transfer.from_account_id, transfer.to_account_id = 1, 2
        # Guards on the INPUT: the three shapes, and each link on both of
        # its spellings.
        assert [(t.recurs, t.is_placed) for t in transfers] == [
            (True, False), (False, True), (False, False),
        ]
        assert [
            (t.transfer_template_id, None if t.template is None else t.template.id)
            for t in transfers
        ] == [(5, 5), (6, 6), (None, None)]
        sides = [
            TransferLeg(transfer=t, account_id=account_id, is_income=is_income)
            for t in transfers
            for account_id, is_income in ((1, False), (2, True))
        ]

        assert [(side.name, side.template_id, side.recurs) for side in sides] == [
            ("Transfer to Savings", None, False),
            ("Transfer from Checking", None, False),
        ] * 3
        assert leg_label(checking, savings) == (
            "Transfer to Savings", "Transfer from Checking",
        )
        assert [side.account for side in sides] == [checking, savings] * 3

        savings.name = "Emergency Fund"
        assert [side.name for side in sides if side.is_expense] == [
            "Transfer to Emergency Fund",
        ] * 3
        assert all(side.record is None for side in sides)

    def test_a_saved_generated_transfers_sides_name_no_definition_projected_or_settled(
        self, app, seed_full_user_data,
    ):
        """A saved generated transfer names no definition on either side, Projected or settled.

        The fact the label case above asks in memory -- a side names no
        transaction definition and does not recur, whatever its transfer's
        shape -- asked on the two axes an in-memory transfer cannot hold:
        this transfer is PERSISTED (it has an id), and it is asked in the two
        corners a saved transfer passes through on its way to being paid.
        First still Projected, no side carrying a record: the state a
        generated transfer is born in and keeps until it is settled or
        cancelled, and the state ``test_transfer_service/test_the_create_door.py``'s
        ``test_with_template_id`` asked each side in until plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-2.  Then settled, each
        side carrying its RECORD (its payment).  Every leg the grid draws in
        production is persisted, and the grid's row match reads a side's
        ``template_id`` (``grid_view_service._match_row_in_period``).  A
        Cancelled GENERATED transfer is not asked: the grid's two readers of
        a side's link skip a cancelled item before they read it
        (``grid_view_service.build_row_keys`` and ``_match_row_in_period``),
        and the calendar's two readers of a side's ``recurs``
        (``calendar_infrequency.badge_cadence`` and ``is_infrequent``) never
        see one, since the calendar's loader leaves a Cancelled parent out
        (``balance_predicates.balance_contributing_clause``).  The projection
        table asks a Cancelled AD-HOC transfer's sides their link.
        The label case's transfers are unsaved and carry no status
        (``Transfer.status_id`` has no default).  No production path makes an
        unsaved transfer whose side carries a record, since a settle needs a
        saved row, though a leg built in memory can carry one
        (``TransferLeg(record=...)``).

        A side that answered its link only for a saved transfer, or only
        where it carries a record, passed the label case and the projection
        table (measured at that step's checkpoint 2e-2b-1b-2b: the mutants
        TMPL-IF-PERSISTED and TMPL-IF-RECORD).  A side that answered its link
        or ``recurs`` only while its saved transfer holds no record, or its
        link only while the transfer is Projected, passed this case too
        while it read the settled corner alone (measured at checkpoint
        2e-2b-1b-2c, which added the Projected read: the mutants
        RV-TMPL-SAVED-PLANNED, RV-RECURS-SAVED-PLANNED and
        RV-TMPL-IF-PROJECTED of the review of checkpoint 2e-2b-1b-2b).

        The transfer is the ENGINE's (:func:`generate_transfer_of`) over the
        fixture's every-period definition, committed and re-read, then
        settled through the transfer service's door (``settle_transfer``),
        committed and re-read again.  The row is saved by construction: it is
        read back by its id after a commit (``_reread_sides``), and a row that
        was not there would fail that read before any guard ran.  Guards on
        the input come first at each read: the row is linked and recurs, and
        it is Projected with no side carrying a record before the settle, and
        every side carries one after it.  Each side is read through two
        doors: by ACCOUNT (``grid_transfer_leg``, the grid's) and by SIDE
        (``transfer_side_leg``, the transfer service's).  It asks
        ``template_id`` and ``recurs`` alone; where a saved side's label is
        graded, the label case says.
        """
        with app.app_context():
            data = seed_full_user_data
            transfer_id = generate_transfer_of(
                data["transfer_template"], data["periods"][0],
            ).id
            row, planned = _reread_sides(transfer_id)
            # Guards on the INPUT: a saved, still-Projected transfer of the
            # recurring definition, no side of it carrying a record.
            assert (row.transfer_template_id, row.recurs, row.status_id) == (
                data["transfer_template"].id, True,
                ref_cache.status_id(StatusEnum.PROJECTED),
            ), "precondition: a saved, Projected transfer of the recurring definition"
            assert [side.record is None for side in planned] == [True] * 4, (
                "precondition: no side carries a record"
            )

            assert [(side.template_id, side.recurs) for side in planned] == [
                (None, False),
            ] * 4

            settle_transfer(transfer_id, data["user"].id)
            row, settled = _reread_sides(transfer_id)
            # Guards on the INPUT: the same saved transfer, every side of it
            # carrying its record now.
            assert (row.transfer_template_id, row.recurs) == (
                data["transfer_template"].id, True,
            ), "precondition: a saved transfer of the recurring definition"
            assert [side.record is not None for side in settled] == [True] * 4, (
                "precondition: every side carries its record"
            )

            assert [(side.template_id, side.recurs) for side in settled] == [
                (None, False),
            ] * 4
