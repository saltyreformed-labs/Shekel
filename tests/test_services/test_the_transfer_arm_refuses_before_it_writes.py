"""Plan step ``balance:X-bi-6-4d-2`` checkpoint 2b: the Transfer arm's refusals, and the moves it carries.

The fixes the neutral review of checkpoint 2a asked for, and the developer's
four answers to it (2026-10-08), each graded through a public door of
:mod:`app.services.transfer_service` (or, where only a writer around the doors
can reach the state, through the status seam itself) and read back as STORED:

* a day typed on a pair closed at ``$0.00`` is refused with the developer's
  sentence, where it was accepted and dropped (ruling **R-BAL230**, review M1);
* a corrected ``$0.00`` close is dated by its due date but never after today
  (ruling **R-BAL231**, amending R-BAL169; review M3);
* the arm grades BOTH sides' days against their books before it writes
  either, so a refused settle stages no record (review L1);
* a hidden transfer is refused by its name, or "Transfer" when it has none,
  and "was archived" when its definition is (finding **BAL-547**);
* a transfer whose twin row was deleted behind the app's back settled like
  any other (ruling **R-BAL235**, retiring R-BAL148 / R-BAL158); its case went
  with the twins (ruling **R-BAL380**), since no twin can be stored (ruling
  **R-BAL258**);
* a page that NAMES a matched record's bank line frees it for an endpoint
  move and for the occurrence delete, and logs it (ruling **R-BAL229**'s
  positive path, review L5);
* an endpoint move moves the side's money in the posted ledger (design D5's
  money, review L5);
* the band rule refuses a writer around the doors that deletes one side's
  record of a Paid transfer, or re-links it to another transfer (design D8,
  review L5).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import or_

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import (
    match_press,
    match_withdrawal,
    status_seam,
    transfer_legs,
    transfer_service,
)
from app.services.posting_reads import account_posting_total
from app.services.settle_day import SettleDay, recorded_settle_day
from app.services.statement_match import accept_match
from app.utils.dates import display_today
from app.utils.log_events import EVT_STATEMENT_MATCH_WITHDRAWN
from tests._test_helpers import (
    an_entered_day,
    an_observed_day,
    create_account_of_type,
    create_account_via_service,
    create_transfer,
    generate_transfer_of,
    make_transfer_template,
    refused_by_database_rule,
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
_ZERO_CLOSE = r"A \$0\.00 close moved no money, so it has no day\."
_SETTLED_REFUSED = r"transfer \d+ is settled but its sides hold"


def _day(seed_user):
    """A bank day inside the bootstrap period, after every account's books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _account(seed_user, type_name, name):
    """An account asserted at the bootstrap start, its books open before any day used."""
    return create_account_of_type(
        seed_user, db.session, type_name, name,
        anchor_balance=Decimal("2000.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )


def _transfer(seed_user, *, due_date=None):
    """A Projected $500.00 Checking -> Savings transfer, committed, behind a decoy.

    The decoy comes first so the subject's ids cannot coincide with another
    row's by numbering.
    """
    savings = _account(seed_user, "Savings", "Cp2b Savings")
    create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], Decimal("7.00"),
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


def _stored(xfer_id):
    """The transfer as stored: ``(status id, version id)``."""
    db.session.expire_all()
    xfer = db.session.get(Transfer, xfer_id)
    return xfer.status_id, xfer.version_id


def _closed_at_zero(seed_user, *, due_date=None):
    """A transfer settled at ``$0.00``: Paid, neither side holding a record."""
    xfer = _transfer(seed_user, due_date=due_date)
    transfer_service.settle_transfer(
        xfer.id, _owner(seed_user), submitted=typed(Decimal("0.00")),
    )
    db.session.commit()
    assert _records(xfer.id) == (None, None)
    return xfer


def _matched(seed_user, xfer):
    """Accept a checking bank line against *xfer*'s checking leg; return the act's id.

    The matcher settles the transfer as it accepts, so the act names the
    from-side's record.
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


def _naming_its_line(record):
    """A page's press that NAMES the line *record* is matched to."""
    return match_press.Press(match_press.Shown(
        match_withdrawal.pending_for_movements([record]).line_ids,
    ))


def _withdrawals(caplog):
    """The withdrawal events the save logged."""
    return [
        record for record in caplog.records
        if getattr(record, "event", None) == EVT_STATEMENT_MATCH_WITHDRAWN
    ]


class TestADayTypedOnAZeroCloseIsRefused:
    """Ruling R-BAL230 ("Refuse, say why"): a $0.00 close has no day to correct."""

    def test_a_day_on_a_zero_closed_pair_is_refused_and_nothing_changes(
        self, app, seed_user,
    ):
        """The review's p10: it answered OK and stored nothing; now it says why."""
        with app.app_context():
            xfer = _closed_at_zero(seed_user)
            before = _stored(xfer.id)

            with pytest.raises(ValidationError, match=_ZERO_CLOSE):
                transfer_service.update_transfer(
                    xfer.id, _owner(seed_user),
                    side_days=(transfer_service.SideDay(
                        xfer.from_account_id, an_entered_day(_day(seed_user)),
                    ),),
                )
            db.session.rollback()

            assert _records(xfer.id) == (None, None)
            assert _stored(xfer.id) == before

    def test_a_zero_correction_beside_a_typed_day_is_refused_too(
        self, app, seed_user,
    ):
        """The same Save shape from the other side: the $0.00 it types drops the day."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            kept = [record.id for record in _records(xfer.id)]
            before = _stored(xfer.id)

            with pytest.raises(ValidationError, match=_ZERO_CLOSE):
                transfer_service.update_transfer(
                    xfer.id, owner, figure=typed(Decimal("0.00")),
                    side_days=(transfer_service.SideDay(
                        xfer.to_account_id, an_entered_day(_day(seed_user)),
                    ),),
                )
            db.session.rollback()

            assert [record.id for record in _records(xfer.id)] == kept
            # The figure is the TRANSFER's since plan step
            # balance:X-bi-6-4d-2's 4d-4 half (ruling R-BAL366), where each
            # record carried a copy until then.
            assert db.session.get(Transfer, xfer.id).settled_figure == _AMOUNT
            assert _stored(xfer.id) == before

    def test_a_zero_settle_from_projected_beside_a_day_is_refused(
        self, app, seed_user,
    ):
        """The rule's deliberate width (review L-1): a settle INTO the band at $0.00 with a day."""
        with app.app_context():
            xfer = _transfer(seed_user)
            before = _stored(xfer.id)

            with pytest.raises(ValidationError, match=_ZERO_CLOSE):
                transfer_service.settle_transfer(
                    xfer.id, _owner(seed_user),
                    submitted=typed(Decimal("0.00")),
                    side_days=(transfer_service.SideDay(
                        xfer.from_account_id, an_entered_day(_day(seed_user)),
                    ),),
                )
            db.session.rollback()

            assert _records(xfer.id) == (None, None)
            assert _stored(xfer.id) == before

    def test_the_amount_the_bank_took_beside_a_day_dates_the_pair_by_it(
        self, app, seed_user,
    ):
        """CONTROL, the sentence's own remedy: $500.00 and a day are both written."""
        with app.app_context():
            xfer = _closed_at_zero(seed_user)
            day = _day(seed_user)

            transfer_service.update_transfer(
                xfer.id, _owner(seed_user), figure=typed(_AMOUNT),
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_entered_day(day),
                ),),
            )
            db.session.commit()

            expense, income = _records(xfer.id)
            assert recorded_settle_day(expense) == an_entered_day(day)
            assert recorded_settle_day(income) == SettleDay(day=day, basis=BORROWED)
            # The figure is the TRANSFER's since plan step
            # balance:X-bi-6-4d-2's 4d-4 half (ruling R-BAL366), where each
            # record carried a copy until then.
            assert db.session.get(Transfer, xfer.id).settled_figure == _AMOUNT


class TestACorrectedZeroCloseIsNeverDatedAfterToday:
    """Ruling R-BAL231 (amends R-BAL169): the due date, but never a day after today."""

    def test_a_due_date_still_ahead_dates_both_sides_today(self, app, seed_user):
        """The review's p7: refused as a future day with Projected advice; now today."""
        with app.app_context():
            today = display_today()
            xfer = _closed_at_zero(seed_user, due_date=today + timedelta(days=10))

            transfer_service.update_transfer(
                xfer.id, _owner(seed_user), figure=typed(_AMOUNT),
            )
            db.session.commit()

            guess = SettleDay(day=today, basis=BORROWED)
            assert [recorded_settle_day(r) for r in _records(xfer.id)] == [
                guess, guess,
            ]

    def test_a_due_date_already_past_still_wins(self, app, seed_user):
        """R-BAL169 unchanged where the due date has come: both sides on it."""
        with app.app_context():
            due = _day(seed_user) + timedelta(days=2)
            assert due < display_today(), "the fixture's due date must be past"
            xfer = _closed_at_zero(seed_user, due_date=due)

            transfer_service.update_transfer(
                xfer.id, _owner(seed_user), figure=typed(_AMOUNT),
            )
            db.session.commit()

            guess = SettleDay(day=due, basis=BORROWED)
            assert [recorded_settle_day(r) for r in _records(xfer.id)] == [
                guess, guess,
            ]


class TestTheArmRefusesEitherSideBeforeItWritesOne:
    """Review L1: each side's day is graded against its books before any record is written."""

    def test_a_refused_settle_stages_no_record_and_stores_nothing(
        self, app, seed_user,
    ):
        """The review's p9: the to-side's books open after the borrowed day.

        Graded inside the writer, the from-side's new record was staged when
        the to-side was refused; graded first, the session holds none.
        """
        with app.app_context():
            owner = _owner(seed_user)
            day = _day(seed_user)
            xfer = _transfer(seed_user)
            late = create_account_via_service(
                seed_user, db.session, "Savings", "Cp2b Late Savings",
                anchor_balance=Decimal("0.00"), observed_on=day + timedelta(days=5),
            )
            db.session.commit()
            transfer_service.update_transfer(xfer.id, owner, to_account_id=late.id)
            db.session.commit()
            before = _stored(xfer.id)

            with pytest.raises(ValidationError, match=day.isoformat()):
                transfer_service.settle_transfer(
                    xfer.id, owner,
                    side_days=(transfer_service.SideDay(
                        xfer.from_account_id, an_observed_day(day),
                    ),),
                )
            staged = [
                row for row in db.session.new if isinstance(row, TransactionEntry)
            ]
            db.session.rollback()

            assert staged == []
            assert _records(xfer.id) == (None, None)
            assert _stored(xfer.id) == before


class TestAHiddenTransferIsToldByItsName:
    """Finding BAL-547: the arm's refusal of a hidden transfer names its from-side's leg label.

    The plan of record's wording (``docs/audits/balance_architecture/README.md``,
    ``X-bi-6-4d``: "a hidden transfer named by its leg label, never the nullable
    ``Transfer.name``"); cp2b named it by ``Transfer.name`` with a "Transfer"
    fallback until cp2c (the cp2b-1 review's L-3).
    """

    @staticmethod
    def _refusal(xfer):
        """Hand the arm a settle of *xfer* the way the transfer writer would; return the words."""
        legs = (
            transfer_legs.transfer_side_leg(xfer, is_income=False),
            transfer_legs.transfer_side_leg(xfer, is_income=True),
        )
        day = SettleDay(day=display_today(), basis=BORROWED)
        with pytest.raises(ValidationError) as refused:
            status_seam.sync_side_records(
                legs, ref_cache.status_id(StatusEnum.DONE), days=(day, day),
                settlement=status_seam.Settlement(
                    amount=_AMOUNT, source=MovementFigureSourceEnum.TYPED,
                ),
                press=None,
            )
        db.session.rollback()
        return str(refused.value)

    def test_a_nameless_deleted_transfer_is_named_by_its_leg_label(
        self, app, seed_user,
    ):
        """``Transfer.name`` is nullable: it would print "None was deleted"."""
        with app.app_context():
            xfer = _transfer(seed_user)
            xfer.name = None
            xfer.is_deleted = True
            db.session.commit()

            assert self._refusal(xfer) == (
                "Transfer to Cp2b Savings was deleted: a payment cannot be "
                "recorded under it.  Reload the page."
            )
            assert _records(xfer.id) == (None, None)

    def test_a_named_deleted_transfer_is_named_by_its_leg_label_too(
        self, app, seed_user,
    ):
        """The leg label, never the transfer's own name: one answer for every transfer."""
        with app.app_context():
            xfer = _transfer(seed_user)
            xfer.name = "Emergency"
            xfer.is_deleted = True
            db.session.commit()

            assert self._refusal(xfer).startswith(
                "Transfer to Cp2b Savings was deleted: a payment cannot be",
            )

    def test_a_transfer_hidden_by_its_definitions_archive_says_archived(
        self, app, seed_user,
    ):
        """Ruling R-CC107's sentence for a transfer: its item is archived."""
        with app.app_context():
            savings = _account(seed_user, "Savings", "Cp2b Archive Savings")
            template = make_transfer_template(db.session, seed_user, savings)
            xfer = generate_transfer_of(template, seed_user["bootstrap_period"])
            template.is_active = False
            xfer.is_deleted = True
            db.session.commit()

            assert self._refusal(xfer).startswith(
                f"Transfer to {savings.name} was archived: a payment cannot "
                "be recorded",
            )


class TestAPageThatNamesTheLineFreesIt:
    """Ruling R-BAL229's positive path: the line the page named is freed and logged."""

    def test_an_endpoint_move_frees_the_named_line_and_carries_the_record(
        self, app, seed_user, caplog,
    ):
        """The matched Checking record moves to the second checking account."""
        with app.app_context():
            xfer = _transfer(seed_user)
            match_id = _matched(seed_user, xfer)
            elsewhere = _account(seed_user, "Checking", "Cp2b Second Checking")
            db.session.commit()
            expense, _income = _records(xfer.id)
            record_id = expense.id

            caplog.set_level(logging.INFO)
            with _naming_its_line(expense) as press:
                transfer_service.update_transfer(
                    xfer.id, _owner(seed_user), from_account_id=elsewhere.id,
                    press=press,
                )
            db.session.commit()

            moved, _income = _records(xfer.id)
            assert moved.id == record_id
            assert moved.account_id == elsewhere.id
            assert db.session.get(StatementMatch, match_id) is None
            assert len(_withdrawals(caplog)) == 1

    def test_the_occurrence_delete_frees_the_named_line(
        self, app, seed_user, caplog,
    ):
        """The soft delete takes both records off and the named match with them."""
        with app.app_context():
            xfer = _transfer(seed_user)
            match_id = _matched(seed_user, xfer)
            expense, _income = _records(xfer.id)

            caplog.set_level(logging.INFO)
            with _naming_its_line(expense) as press:
                transfer_service.delete_transfer(
                    xfer.id, _owner(seed_user), soft=True, press=press,
                )
            db.session.commit()

            assert _records(xfer.id) == (None, None)
            assert db.session.get(StatementMatch, match_id) is None
            assert db.session.get(Transfer, xfer.id).is_deleted is True
            assert len(_withdrawals(caplog)) == 1


class TestAnEndpointMoveMovesTheLedger:
    """Design D5's money: the moved side's posting leaves one account and lands on the other."""

    def test_the_old_savings_gives_up_500_and_the_new_one_takes_it(
        self, app, seed_user,
    ):
        """The review's p13: old Savings -500.00, new Savings +500.00, Checking unmoved."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            new_savings = _account(seed_user, "Savings", "Cp2b New Savings")
            db.session.commit()
            scenario_id = xfer.scenario_id
            accounts = (xfer.from_account_id, xfer.to_account_id, new_savings.id)
            before = [account_posting_total(a, scenario_id) for a in accounts]

            transfer_service.update_transfer(
                xfer.id, owner, to_account_id=new_savings.id,
            )
            db.session.commit()

            after = [account_posting_total(a, scenario_id) for a in accounts]
            assert [b - a for a, b in zip(before, after)] == [
                Decimal("0.00"), -_AMOUNT, _AMOUNT,
            ]


class TestTheBandRuleRefusesAWriterAroundTheDoors:
    """Design D8: a settled transfer records both sides or neither, whoever writes."""

    def test_deleting_one_sides_record_of_a_paid_transfer_is_refused(
        self, app, seed_user,
    ):
        """The review's p14: the delete attachment fires at COMMIT."""
        with app.app_context():
            xfer = _transfer(seed_user)
            transfer_service.settle_transfer(xfer.id, _owner(seed_user))
            db.session.commit()
            expense, _income = _records(xfer.id)

            db.session.delete(expense)
            with refused_by_database_rule(_SETTLED_REFUSED):
                db.session.commit()
            db.session.rollback()

            assert None not in _records(xfer.id)

    def test_re_linking_a_record_to_another_transfer_is_refused(
        self, app, seed_user,
    ):
        """The review's p17: the record leaves its Paid transfer with one side."""
        with app.app_context():
            xfer = _transfer(seed_user)
            owner = _owner(seed_user)
            other = create_transfer(
                seed_user, db.session, seed_user["account"],
                db.session.get(Transfer, xfer.id).to_account,
                seed_user["bootstrap_period"], Decimal("9.00"),
            )
            db.session.commit()
            transfer_service.settle_transfer(xfer.id, owner)
            transfer_service.settle_transfer(other.id, owner)
            db.session.commit()
            expense, _income = _records(xfer.id)
            others_expense, _others_income = _records(other.id)

            db.session.delete(others_expense)
            db.session.flush()
            expense.expense_transfer_id = other.id
            with refused_by_database_rule(_SETTLED_REFUSED):
                db.session.commit()
            db.session.rollback()

            assert _records(xfer.id)[0].id == expense.id
