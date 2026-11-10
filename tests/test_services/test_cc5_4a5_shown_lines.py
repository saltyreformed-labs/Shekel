"""Plan step ``credit_card:CC-5-4a-5``: the removal act asks what the owner was SHOWN.

Ruling **R-CC81** (developer 2026-09-23): *"The shared removal function
requires every button that calls it to pass what you were shown before the
press, or to name the ruling that lets it stay silent (Mark Paid)."*  Ruling
**R-CC127** (developer 2026-09-30), the picked text: *"Each warning also sends
back the bank lines it named, and the function compares them with what it
would undo.  At 10:10 they differ, so nothing is saved ... A button with no
warning sends nothing, so it can undo a match only by naming its ruling in
code."*

Every case runs a production verb over a MATCHED payment: the Hotel bill's
``$120.00`` payment matched to the bank's ``HOTEL -$120.00`` line, which a
``$0.00`` Actual takes off the books (the status seam's ``_withdraw``).  The
refused cases roll back as the doors' refusal paths do
(``routes/transactions/_helpers._error_transaction_response``) and assert the
act and the payment both still stand.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import SettledDayBasisEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services import (
    match_press,
    match_withdrawal,
    transaction_service,
    transfer_service,
)
from app.services.settle_day import SettleDay
from app.services.statement_match import accept_match
from app.utils.log_events import EVT_STATEMENT_MATCH_WITHDRAWN
from tests._test_helpers import create_account_of_type, create_transfer, typed

# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)


def _day(seed_user):
    """A bank day inside the bootstrap period, after the books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _hotel(seed_user):
    """The $120.00 Hotel bill on checking, settled on :func:`_day`."""
    txn = a_transaction(seed_user, name="Hotel", amount="120.00")
    db.session.commit()
    transaction_service.settle_transaction(
        txn, settle_day=SettleDay(
            day=_day(seed_user), basis=SettledDayBasisEnum.ENTERED,
        ),
    )
    db.session.commit()
    return txn


def _line(seed_user, amount, description, account=None):
    """One bank line on *account* (checking by default), posted on :func:`_day`."""
    line = a_bank_line(
        seed_user, an_import(seed_user, account), amount=amount,
        posted_on=_day(seed_user), description=description,
    )
    db.session.commit()
    return line


def _match(seed_user, line, *, transactions=(), transfers=()):
    """Accept *line* against the rows' payments on checking; return the act's id."""
    scope = a_scope(seed_user)
    accepted = accept_match(
        a_submission(
            scope, lines=[line], transactions=list(transactions),
            transfers=list(transfers),
        ),
        scope,
    )
    db.session.commit()
    return accepted.match_id


def _matched_hotel(seed_user):
    """The Hotel bill, its payment matched to ``HOTEL -$120.00``."""
    hotel = _hotel(seed_user)
    line = _line(seed_user, "-120.00", "HOTEL")
    match_id = _match(seed_user, line, transactions=[hotel])
    return hotel, line, match_id


def _record_zero(hotel, **shown):
    """The popover's Actual box at ``$0.00``, through its door."""
    transaction_service.apply_requested_status(
        hotel, hotel.status_id,
        stated=transaction_service.StatedRecord(figure=typed(Decimal("0.00"))),
        **shown,
    )


def _still_standing(hotel_id, match_id):
    """Assert the refused press left the act and the payment where they were."""
    db.session.rollback()
    assert db.session.get(StatementMatch, match_id) is not None
    assert db.session.get(Transaction, hotel_id).covering_movements


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
        """Return the withdrawal events this block logged."""
        return [
            record for record in self.records
            if getattr(record, "event", None) == EVT_STATEMENT_MATCH_WITHDRAWN
        ]


class TestADoorThatSaysNothingFreesNothing:
    """*"A button with no warning sends nothing"*: the verbs' default."""

    def test_an_undeclared_door_that_would_free_a_line_is_refused(
        self, app, seed_user,
    ):
        """No ``shown`` at all: the default is NOTHING_SHOWN, and it refuses."""
        with app.app_context():
            hotel, _line_row, match_id = _matched_hotel(seed_user)

            with pytest.raises(ValidationError, match="out of date"):
                _record_zero(hotel)

            _still_standing(hotel.id, match_id)

    def test_an_undeclared_door_that_frees_nothing_goes_ahead(
        self, app, seed_user,
    ):
        """An UNMATCHED payment re-recorded at $0.00 frees no line: no caption owed."""
        with app.app_context():
            hotel = _hotel(seed_user)

            _record_zero(hotel)
            db.session.commit()

            assert not db.session.get(Transaction, hotel.id).covering_movements


class TestThePressIsGradedAgainstWhatThePageNamed:
    """Equality both ways (*"At 10:10 they differ, so nothing is saved"*)."""

    def test_the_lines_the_page_named_are_the_lines_the_press_frees(
        self, app, seed_user,
    ):
        """The caption's own read, posted back: the act withdraws and the line is free."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)
            named = match_withdrawal.pending_for_movements(
                hotel.covering_movements,
            )
            assert named.line_ids == frozenset({line.id})

            with match_press.Press(match_press.Shown(named.line_ids)) as press:
                _record_zero(hotel, press=press)
            db.session.commit()

            assert db.session.get(StatementMatch, match_id) is None

    def test_a_page_drawn_before_the_match_existed_is_refused(
        self, app, seed_user,
    ):
        """R-CC127's 10:00 / 10:05 / 10:10: the page named nothing, the press would free one."""
        with app.app_context():
            hotel = _hotel(seed_user)
            drawn = match_withdrawal.pending_for_movements(
                hotel.covering_movements,
            )
            assert not drawn.frees_a_line
            match_id = _match(
                seed_user, _line(seed_user, "-120.00", "HOTEL"),
                transactions=[hotel],
            )

            with pytest.raises(ValidationError, match="out of date"):
                with match_press.Press(match_press.Shown(drawn.line_ids)) as press:
                    _record_zero(
                        hotel, press=press,
                    )

            _still_standing(hotel.id, match_id)

    def test_a_page_naming_a_line_the_press_no_longer_frees_is_refused(
        self, app, seed_user,
    ):
        """The other direction: the match was undone in another tab after the page drew."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)
            db.session.delete(db.session.get(StatementMatch, match_id))
            db.session.commit()

            with pytest.raises(ValidationError, match="out of date"):
                with match_press.Press(match_press.Shown(frozenset({line.id}))) as press:
                    _record_zero(
                        hotel, press=press,
                    )

            db.session.rollback()
            assert db.session.get(Transaction, hotel.id).covering_movements

    def test_a_named_line_the_save_never_frees_refuses_it(
        self, app, seed_user,
    ):
        """A posted id the save does not free refuses the save, wherever its line is.

        The posted ids are owner input, never a scope: the press only compares
        them, whole, with what the save frees.  Until plan step
        ``credit_card:CC-5-4a-5``'s leaf 5c-2b a named line on an account the
        call never touched was not graded -- the per-account comparison a
        transfer's two calls needed -- and this case pinned the save going
        ahead.  Rewritten under ruling **R-CC135** (developer 2026-10-04, "One
        check per save"): *"One-row buttons behave as now, except that a
        warning naming a line another tab has already freed now redraws
        instead of saving."*  A line the save does not free is that line, or
        a crafted id: nothing saves.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("2000.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
            elsewhere = _line(seed_user, "-120.00", "ELSEWHERE", savings)
            hotel = _hotel(seed_user)
            hotel_id = hotel.id

            with pytest.raises(ValidationError, match="out of date"):
                with match_press.Press(
                    match_press.Shown(frozenset({elsewhere.id})),
                ) as press:
                    _record_zero(hotel, press=press)

            db.session.rollback()
            assert db.session.get(Transaction, hotel_id).covering_movements


class TestANamedSilence:
    """R-CC81: a no-caption door withdraws only by naming what lets it."""

    def test_a_silent_door_withdraws_and_the_event_names_its_ruling(
        self, app, seed_user,
    ):
        """The grid's Mark Paid (R-CC56): withdrawn, logged, and the log says why."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)

            with _Events() as events:
                with match_press.Press(match_press.MARK_PAID) as press:
                    _record_zero(hotel, press=press)
                db.session.commit()

            assert db.session.get(StatementMatch, match_id) is None
            (record,) = events.withdrawn()
            assert record.silent_by == "R-CC56"
            assert record.freed_line_ids == [line.id]

    def test_a_shown_press_logs_no_silence(self, app, seed_user):
        """A captioned press names no ruling: ``silent_by`` is empty."""
        with app.app_context():
            hotel, line, _match_id = _matched_hotel(seed_user)

            with _Events() as events:
                with match_press.Press(match_press.Shown(frozenset({line.id}))) as press:
                    _record_zero(
                        hotel, press=press,
                    )
                db.session.commit()

            (record,) = events.withdrawn()
            assert record.silent_by is None


class TestATransfersTwoSidesAreOnePress:
    """One posted set, two seam calls: each graded on its own side's account."""

    @staticmethod
    def _matched_transfer(seed_user):
        """$500.00 checking -> savings, its CHECKING leg matched to ``TRANSFER -$500.00``."""
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings",
            anchor_balance=Decimal("2000.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
        xfer = create_transfer(
            seed_user, seed_user["account"], savings,
            seed_user["bootstrap_period"], Decimal("500.00"),
        )
        db.session.commit()
        line = _line(seed_user, "-500.00", "TRANSFER")
        match_id = _match(seed_user, line, transfers=[xfer])
        return xfer, line, match_id

    def test_the_caption_over_both_legs_grades_both_calls(self, app, seed_user):
        """The checking call frees the named line; the savings call frees none of its own."""
        with app.app_context():
            xfer, line, match_id = self._matched_transfer(seed_user)

            with match_press.Press(match_press.Shown(frozenset({line.id}))) as press:
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id, figure=typed(Decimal("0.00")),
                    press=press,
                )
            db.session.commit()

            assert db.session.get(StatementMatch, match_id) is None

    def test_the_popover_naming_nothing_is_refused(self, app, seed_user):
        """The same $0.00 with nothing named: the checking call refuses the whole press."""
        with app.app_context():
            xfer, _line_row, match_id = self._matched_transfer(seed_user)

            with pytest.raises(ValidationError, match="out of date"):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    figure=typed(Decimal("0.00")),
                    press=None,
                )

            db.session.rollback()
            assert db.session.get(StatementMatch, match_id) is not None
