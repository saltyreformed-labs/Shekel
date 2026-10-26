"""Plan step ``balance:X-bi-6-4d-2``: the status seam's Transfer arm writes each side's record.

Ruling **R-BAL88** filed a transfer side's payment record under the TRANSFER by
one of two side links (``transaction_entries.expense_transfer_id`` /
``income_transfer_id``); this step's second checkpoint moved the WRITER onto
them (design D3, ``app/services/status_seam/_side.py``) and the one transfer
writer with it (D4, ``transfer_service._status``).  Each case drives a public
door of :mod:`app.services.transfer_service` and grades the rows as STORED --
every assertion reads after a commit and ``expire_all`` -- never the session's
pending state.

Covered here: a settle writes one record per side, filed under the transfer; a
revert un-dates and keeps them (ruling **R-BAL61**); a ``$0.00`` close keeps
none (rulings **R-BAL82**, **R-BAL141**); a corrected ``$0.00`` close is dated
by its due date (ruling **R-BAL169**); the transfer's counter moves
when a side's record or day does (developer ruling 2026-08-18, the
``$214.37`` two-tab lost update); a statement link reaches one side's record
(ruling **R-FL**);
the removal act's list is the side's collection; the occurrence delete takes
the records off (finding **BAL-532**, ruling **R-CC75**); and an endpoint move
carries a side's record (ruling **R-BAL168**) under the page check (ruling
**R-BAL229**).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, or_

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.statement_match import StatementMatch
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import (
    match_withdrawal,
    movement_removal,
    transfer_legs,
    transfer_service,
)
from app.services.settle_day import SettleDay, recorded_settle_day
from app.services.statement_match import accept_match
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    an_observed_day,
    create_account_of_type,
    create_account_via_service,
    create_transfer,
    typed,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_import,
)

BORROWED = SettledDayBasisEnum.BORROWED
_AMOUNT = Decimal("500.00")


def _day(seed_user):
    """A bank day inside the bootstrap period, after every account's books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _savings(seed_user, name="Arm Savings"):
    """A Savings account asserted at the bootstrap start, its books open before any day used."""
    return create_account_of_type(
        seed_user, db.session, "Savings", name,
        anchor_balance=Decimal("2000.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )


def _transfer(seed_user, *, due_date=None):
    """A Projected $500.00 Checking -> Savings transfer, committed, behind two decoys.

    The two decoy transfers come FIRST, so the subject is not its table's first
    transfer: a read that reaches for the wrong transfer meets a decoy and fails
    rather than passing by numbering (lesson (a) of plan step
    ``balance:X-bi-6-4d-1``).  They also kept the subject's id apart from its
    twins' and its records' until the test template gave every table an id
    range of its own (ruling ``balance:R-BAL356``), which keeps those apart now;
    no twin is stored since plan step ``balance:X-bi-6-4d-2`` (ruling
    **R-BAL258**).
    """
    savings = _savings(seed_user)
    for decoy in (Decimal("7.00"), Decimal("8.00")):
        create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], decoy,
        )
    xfer = create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], _AMOUNT, due_date=due_date,
    )
    db.session.commit()
    return xfer


def _owner(seed_user):
    """The owner's id."""
    return seed_user["user"].id


def _records(xfer_id):
    """Return ``(from-side record, to-side record)`` as STORED; ``None`` for an empty side."""
    db.session.expire_all()
    rows = db.session.query(TransactionEntry).filter(or_(
        TransactionEntry.expense_transfer_id == xfer_id,
        TransactionEntry.income_transfer_id == xfer_id,
    )).all()
    expense = [row for row in rows if row.expense_transfer_id == xfer_id]
    income = [row for row in rows if row.income_transfer_id == xfer_id]
    assert len(expense) <= 1 and len(income) <= 1, rows
    return (expense[0] if expense else None, income[0] if income else None)


def _status(name):
    """The ``ref.statuses.id`` of *name*."""
    return ref_cache.status_id(name)


def _version(xfer_id):
    """The transfer's optimistic-lock counter as stored."""
    db.session.expire_all()
    return db.session.get(Transfer, xfer_id).version_id


def _matched(seed_user, xfer):
    """Accept a checking bank line against *xfer*'s checking leg; return the act's id.

    The statement matcher settles the transfer as it accepts (the checking side
    on the bank's day, observed; the savings side borrowing it), so the act
    names the from-side's record -- the shape plan step
    ``credit_card:CC-5-4a-5``'s transfer cases stage.
    """
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-500.00",
        posted_on=_day(seed_user), description="TRANSFER",
    )
    db.session.commit()
    scope = a_scope(seed_user)
    accepted = accept_match(
        a_submission(scope, lines=[line], transfers=[xfer]), scope,
    )
    db.session.commit()
    return accepted.match_id


class TestASettleWritesOneRecordPerSide:
    """Design D3: the arm files each side's payment under the transfer, not a twin."""

    def test_a_paid_press_records_both_sides_on_their_endpoints(self, app, seed_user):
        """One record per side, side-linked, on its endpoint, borrowed today; no figure on it.

        The figure and who wrote it are the TRANSFER's, their one home since
        plan step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where each record carried a copy until then; a side
        record holds neither since that half's 4d-4b-2 leaf.
        """
        with app.app_context():
            xfer = _transfer(seed_user)
            transfer_service.settle_transfer(xfer.id, _owner(seed_user))
            db.session.commit()

            expense, income = _records(xfer.id)
            today = SettleDay(day=display_today(), basis=BORROWED)
            resolved = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.RESOLVED,
            )
            held = db.session.get(Transfer, xfer.id)
            assert (held.settled_figure, held.settled_figure_source_id) == (
                _AMOUNT, resolved,
            )
            for record, account_id, label in (
                (expense, xfer.from_account_id, "Transfer to Arm Savings"),
                (income, xfer.to_account_id, "Transfer from Checking"),
            ):
                assert record is not None
                assert record.transaction_id is None
                assert record.account_id == account_id
                assert (record.amount, record.figure_source_id) == (None, None)
                assert record.description == label
                assert record.covers_settlement is True
                assert recorded_settle_day(record) == today
                assert record.purchased_on == today.day
            assert expense.expense_transfer_id == xfer.id
            assert expense.income_transfer_id is None
            assert income.income_transfer_id == xfer.id
            assert income.expense_transfer_id is None


class TestARevertKeepsTheRecords:
    """Ruling R-BAL61: a revert un-dates each side's record and keeps it."""

    def test_a_revert_un_dates_both_and_a_re_settle_re_dates_the_same_rows(
        self, app, seed_user,
    ):
        """Same ids across settle, revert and re-settle; the revert releases each link."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(_day(seed_user)),
                ),),
            )
            db.session.commit()
            first = _records(xfer.id)
            anchor_id = db.session.query(AccountAnchorHistory.id).filter_by(
                account_id=xfer.from_account_id,
            ).order_by(AccountAnchorHistory.id).first()[0]
            transfer_service.record_leg_clearing(
                transfer_legs.leg_of(db.session.get(Transfer, xfer.id),
                                     xfer.from_account_id),
                anchor_id,
            )
            db.session.commit()
            assert _records(xfer.id)[0].reconciled_by_id == anchor_id

            transfer_service.update_transfer(
                xfer.id, owner, status_id=_status(StatusEnum.PROJECTED),
            )
            db.session.commit()
            reverted = _records(xfer.id)
            assert [r.id for r in reverted] == [r.id for r in first]
            for record in reverted:
                assert record.settled_on is None
                assert record.settled_day_basis_id is None
                assert record.reconciled_by_id is None
            # What moved is kept on the TRANSFER, its one home since plan step
            # balance:X-bi-6-4d-2's 4d-4 half (ruling R-BAL366), where each
            # record carried a copy until then.
            assert db.session.get(Transfer, xfer.id).settled_figure == _AMOUNT

            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            resettled = _records(xfer.id)
            assert [r.id for r in resettled] == [r.id for r in first]
            assert all(r.settled_on == display_today() for r in resettled)


class TestAZeroCloseKeepsNoRecord:
    """Rulings R-BAL82 / R-BAL141: a close of nothing stores no record on either side."""

    def test_a_zero_settle_records_neither_side(self, app, seed_user):
        """Paid at $0.00 from Projected: the transfer is Paid and no side holds a record."""
        with app.app_context():
            xfer = _transfer(seed_user)
            transfer_service.settle_transfer(
                xfer.id, _owner(seed_user), submitted=typed(Decimal("0.00")),
            )
            db.session.commit()
            assert _records(xfer.id) == (None, None)
            assert db.session.get(Transfer, xfer.id).status_id == _status(
                StatusEnum.DONE,
            )

    def test_a_zero_correction_takes_both_records_off(self, app, seed_user):
        """A settled pair corrected to $0.00 loses both records."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            assert None not in _records(xfer.id)

            transfer_service.update_transfer(
                xfer.id, owner, figure=typed(Decimal("0.00")),
            )
            db.session.commit()
            assert _records(xfer.id) == (None, None)


class TestACorrectedZeroCloseIsDatedByItsDueDate:
    """Ruling R-BAL169: no side holds a day, so a correction borrows the due date."""

    def test_both_sides_take_the_due_date_as_a_guess(self, app, seed_user):
        """$0.00 close, then $500.00 typed with no day: both sides borrowed on the due date."""
        with app.app_context():
            due = _day(seed_user) + timedelta(days=3)
            xfer = _transfer(seed_user, due_date=due)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(
                xfer.id, owner, submitted=typed(Decimal("0.00")),
            )
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, owner, figure=typed(_AMOUNT),
            )
            db.session.commit()
            guess = SettleDay(day=due, basis=BORROWED)
            assert [recorded_settle_day(r) for r in _records(xfer.id)] == [
                guess, guess,
            ]
            # The figure is the TRANSFER's since plan step
            # balance:X-bi-6-4d-2's 4d-4 half (ruling R-BAL366), where each
            # record carried a copy until then.
            assert db.session.get(Transfer, xfer.id).settled_figure == _AMOUNT

    def test_with_no_due_date_both_sides_take_today(self, app, seed_user):
        """The fallback's fallback: a transfer with no due date borrows today."""
        with app.app_context():
            xfer = _transfer(seed_user)
            assert xfer.due_date is None
            owner = _owner(seed_user)
            transfer_service.settle_transfer(
                xfer.id, owner, submitted=typed(Decimal("0.00")),
            )
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, owner, figure=typed(_AMOUNT),
            )
            db.session.commit()
            today = SettleDay(day=display_today(), basis=BORROWED)
            assert [recorded_settle_day(r) for r in _records(xfer.id)] == [
                today, today,
            ]


class TestTheTransfersCounterMovesWithItsSides:
    """Developer ruling 2026-08-18: a record or day correction moves the transfer's counter."""

    def test_a_figure_correction_moves_it_and_its_echo_does_not(self, app, seed_user):
        """$214.37 typed over a settled $500.00 moves the counter; the same again does not."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            before = _version(xfer.id)

            transfer_service.update_transfer(
                xfer.id, owner, figure=typed(Decimal("214.37")),
            )
            db.session.commit()
            corrected = _version(xfer.id)
            assert corrected > before
            # Both sides still hold their record, and the figure is the
            # TRANSFER's since plan step balance:X-bi-6-4d-2's 4d-4 half
            # (ruling R-BAL366), where each record carried a copy until then.
            assert None not in _records(xfer.id)
            assert db.session.get(Transfer, xfer.id).settled_figure == (
                Decimal("214.37")
            )

            transfer_service.update_transfer(
                xfer.id, owner, figure=typed(Decimal("214.37")),
            )
            db.session.commit()
            assert _version(xfer.id) == corrected

    def test_a_day_correction_on_one_side_moves_it(self, app, seed_user):
        """A day typed for the checking side alone moves the transfer's counter."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            before = _version(xfer.id)

            day = _day(seed_user) + timedelta(days=2)
            transfer_service.update_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_entered_day(day),
                ),),
            )
            db.session.commit()
            assert _version(xfer.id) > before
            expense, income = _records(xfer.id)
            assert recorded_settle_day(expense) == an_entered_day(day)
            assert recorded_settle_day(income) == SettleDay(day=day, basis=BORROWED)


class TestAStatementLinksOneSidesRecord:
    """Ruling R-FL, per side: a statement of account X links the record on X alone."""

    def test_the_checking_statement_links_the_checking_record(self, app, seed_user):
        """record_leg_clearing on the checking leg: its record linked, the savings record not."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(_day(seed_user)),
                ),),
            )
            db.session.commit()
            anchor_id = db.session.query(AccountAnchorHistory.id).filter_by(
                account_id=xfer.from_account_id,
            ).order_by(AccountAnchorHistory.id).first()[0]

            transfer_service.record_leg_clearing(
                transfer_legs.leg_of(
                    db.session.get(Transfer, xfer.id), xfer.from_account_id,
                ),
                anchor_id,
            )
            db.session.commit()
            expense, income = _records(xfer.id)
            assert expense.reconciled_by_id == anchor_id
            assert income.reconciled_by_id is None

    def test_a_zero_closed_side_keeps_no_link(self, app, seed_user):
        """R-BAL141: a side holding no record is linked by nothing, and nothing fails."""
        with app.app_context():
            xfer = _transfer(seed_user)
            transfer_service.settle_transfer(
                xfer.id, _owner(seed_user), submitted=typed(Decimal("0.00")),
            )
            db.session.commit()
            anchor_id = db.session.query(AccountAnchorHistory.id).filter_by(
                account_id=xfer.from_account_id,
            ).order_by(AccountAnchorHistory.id).first()[0]

            transfer_service.record_leg_clearing(
                transfer_legs.leg_of(
                    db.session.get(Transfer, xfer.id), xfer.from_account_id,
                ),
                anchor_id,
            )
            db.session.commit()
            assert _records(xfer.id) == (None, None)


class TestTheRemovalActsListIsTheSidesCollection:
    """``transfer_legs.parent_entries``: a side record's list is its transfer's side collection."""

    def test_the_act_deletes_the_record_and_takes_it_out_of_that_list(
        self, app, seed_user,
    ):
        """The record leaves ``Transfer.expense_movements``; the flush emits its DELETE alone."""
        with app.app_context():
            xfer = _transfer(seed_user)
            transfer_service.settle_transfer(xfer.id, _owner(seed_user))
            db.session.commit()
            transfer = db.session.get(Transfer, xfer.id)
            family = transfer.expense_movements
            (payment,) = family
            assert transfer_legs.parent_entries(payment) is family
            payment_id = payment.id

            statements = []

            def _capture(_conn, _cursor, statement, _params, _context, _many):
                statements.append(statement)

            event.listen(db.engine, "before_cursor_execute", _capture)
            try:
                movement_removal.remove_movements(
                    [payment], _owner(seed_user),
                    because=match_withdrawal.RE_RECORDED, press=None,
                )
                assert payment not in transfer.expense_movements
                assert transfer.expense_movements is family
                db.session.flush()
            finally:
                event.remove(db.engine, "before_cursor_execute", _capture)

            entry_writes = [
                s for s in statements
                if "transaction_entries" in s and s.lstrip().upper().startswith(
                    ("UPDATE", "DELETE"),
                )
            ]
            assert any(
                s.lstrip().upper().startswith("DELETE") for s in entry_writes
            ), statements
            assert not any(
                s.lstrip().upper().startswith("UPDATE") for s in entry_writes
            ), entry_writes
            db.session.rollback()
            assert db.session.get(TransactionEntry, payment_id) is not None


class TestTheOccurrenceDeleteTakesTheRecordsOff:
    """Finding BAL-532, ruling R-CC75 ("Same as a one-off"), under the page check (R-BAL229)."""

    def test_an_unmatched_pair_comes_off_and_restores_empty(self, app, seed_user):
        """Soft delete: both records gone, the delete commits; restore brings it back with none."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            assert None not in _records(xfer.id)

            transfer_service.delete_transfer(xfer.id, owner, soft=True)
            db.session.commit()
            assert _records(xfer.id) == (None, None)
            assert db.session.get(Transfer, xfer.id).is_deleted is True

            transfer_service.restore_transfer(xfer.id, owner)
            db.session.commit()
            assert db.session.get(Transfer, xfer.id).is_deleted is False
            assert _records(xfer.id) == (None, None)

    def test_a_matched_record_with_no_page_refuses_and_writes_nothing(
        self, app, seed_user,
    ):
        """No page named the line, so the delete is refused and the match and records stand."""
        with app.app_context():
            xfer = _transfer(seed_user)
            match_id = _matched(seed_user, xfer)
            before = [r.id for r in _records(xfer.id)]
            assert None not in before

            with pytest.raises(ValidationError, match="out of date"):
                transfer_service.delete_transfer(
                    xfer.id, _owner(seed_user), soft=True,
                )
            db.session.rollback()

            assert [r.id for r in _records(xfer.id)] == before
            assert db.session.get(StatementMatch, match_id) is not None
            assert db.session.get(Transfer, xfer.id).is_deleted is False


class TestAnEndpointMoveCarriesTheSidesRecord:
    """Rulings R-BAL168 and R-BAL229: the record moves with its side, under the page check."""

    def test_an_unmatched_record_moves_and_its_day_becomes_a_guess(
        self, app, seed_user,
    ):
        """The to-side record lands on the new account, same id, borrowed, its link released."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            elsewhere = _savings(seed_user, "Arm Elsewhere")
            db.session.commit()
            day = _day(seed_user)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.to_account_id, an_observed_day(day),
                ),),
            )
            db.session.commit()
            anchor_id = db.session.query(AccountAnchorHistory.id).filter_by(
                account_id=xfer.to_account_id,
            ).order_by(AccountAnchorHistory.id).first()[0]
            transfer_service.record_leg_clearing(
                transfer_legs.leg_of(
                    db.session.get(Transfer, xfer.id), xfer.to_account_id,
                ),
                anchor_id,
            )
            db.session.commit()
            (_expense, income) = _records(xfer.id)
            assert recorded_settle_day(income) == an_observed_day(day)
            assert income.reconciled_by_id == anchor_id
            income_id = income.id

            transfer_service.update_transfer(
                xfer.id, owner, to_account_id=elsewhere.id,
            )
            db.session.commit()

            (_expense, moved) = _records(xfer.id)
            assert moved.id == income_id
            assert moved.account_id == elsewhere.id
            assert recorded_settle_day(moved) == SettleDay(day=day, basis=BORROWED)
            assert moved.reconciled_by_id is None
            assert db.session.get(Transfer, xfer.id).to_account_id == elsewhere.id

    def test_a_matched_record_with_no_page_refuses_and_nothing_saves(
        self, app, seed_user,
    ):
        """The checking record is matched and no page named its line: the move is refused."""
        with app.app_context():
            xfer = _transfer(seed_user)
            match_id = _matched(seed_user, xfer)
            elsewhere = create_account_of_type(
                seed_user, db.session, "Checking", "Arm Second Checking",
                anchor_balance=Decimal("2000.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()
            (expense, _income) = _records(xfer.id)
            checking_id = expense.account_id

            with pytest.raises(ValidationError, match="out of date"):
                transfer_service.update_transfer(
                    xfer.id, _owner(seed_user), from_account_id=elsewhere.id,
                )
            db.session.rollback()

            (expense, _income) = _records(xfer.id)
            assert expense.account_id == checking_id
            assert db.session.get(StatementMatch, match_id) is not None
            assert db.session.get(Transfer, xfer.id).from_account_id == checking_id

    def test_a_day_the_new_books_do_not_reach_is_refused_by_name(
        self, app, seed_user,
    ):
        """The moved side borrows the new account's opening day: refused, naming the day."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            day = _day(seed_user)
            # The factory's own books: they open ON the observed day, so a
            # movement dated that day is inside the opening and refused.
            late = create_account_via_service(
                seed_user, db.session, "Savings", "Arm Late Savings",
                anchor_balance=Decimal("0.00"), observed_on=day,
            )
            db.session.commit()
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(day),
                ),),
            )
            db.session.commit()
            (_expense, income) = _records(xfer.id)
            savings_id = income.account_id

            with pytest.raises(ValidationError, match=day.isoformat()):
                transfer_service.update_transfer(
                    xfer.id, owner, to_account_id=late.id,
                )
            db.session.rollback()

            (_expense, income) = _records(xfer.id)
            assert income.account_id == savings_id
            assert db.session.get(Transfer, xfer.id).to_account_id == savings_id
