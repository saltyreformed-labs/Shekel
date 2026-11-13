"""The money property: a recorded line adds the movement the bank showed, born carrying both of the
bank's days and the bank as its figure's source, and stops being unexplained through the same
match door.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum
from app.models.statement_match import StatementMatch
from app.models.transaction_entry import TransactionEntry
from app.services import balance_at, statement_match
from app.services.balance_at import BalanceContext

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    accepted_acts,
    an_import,
)

from ._helpers import _closed_from_purchases, _record


def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*."""
    ctx = BalanceContext(
        user_id=seed_user["user"].id,
        scenario=seed_user["scenario"], as_of=day,
    )
    return balance_at.balance_at(seed_user["account"], ctx, day)


class TestRecordingALineAddsTheMovement:
    """The money property: the app now holds what the bank showed."""

    def test_the_balance_falls_by_what_the_bank_took(
        self, app, db, seed_user,
    ):
        """A movement the app did not have is a movement the app now has.

        This is the whole point of the step and the reason it is marked MOVES
        MONEY.  Accepting a match cannot do this -- it only re-dates a row the
        app already held -- which is why 74 of the developer's own lines
        survive every proposal.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description="POINT OF SALE DEBIT L340 WAL-MART (Walmart)",
            )
            before = _balance_on(seed_user, day + timedelta(days=2))

            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            after = _balance_on(seed_user, day + timedelta(days=2))
            assert after == before - Decimal("57.96")

    @pytest.mark.parametrize("applied_by_rule", [False, True])
    def test_the_act_records_WHICH_consent_it_had(
        self, app, db, seed_user, applied_by_rule,
    ):
        """Ruling **R-GT**, at this package's SECOND writer of a match act.

        Both doors reach ``record_match``, and ``applied_by_rule`` is
        keyword-only with no default the whole way down, so each states the
        fact for itself.  **Plan step ``bank_import:X-ge`` is what made this a
        PAIR**: until it shipped, this door had one entrance -- the review
        screen's per-line destination select, under a human press -- and stated
        ``False`` as a literal; it now has a second, an import filing a NEW
        swipe under a standing rule the owner already gave.

        **BOTH arms, and the false one is not decoration.**  The column is what
        the receipt and the review screen's badge partition on, so a door that
        wrote ``True`` for everything would report every act the owner pressed
        as one the app performed for them -- which is a false claim about
        consent in the direction that matters, and one no case asserting only
        the rule arm could see.  The act is otherwise IDENTICAL, which is the
        point: same purchase, same destination, same days.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description="POINT OF SALE DEBIT L340 WAL-MART (Walmart)",
            )

            _record(
                seed_user, line, transaction_id=envelope.id,
                applied_by_rule=applied_by_rule,
            )
            db.session.flush()

            assert db.session.query(StatementMatch).one().applied_by_rule is (
                applied_by_rule
            )

    def test_the_envelope_s_recorded_cost_grows_by_the_purchase(
        self, app, db, seed_user,
    ):
        """A ``purchases`` close IS its entries, so the row cost more.

        Not a side effect to tolerate: the bank is the evidence that this
        envelope paid for something the records did not name.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user, purchase="120.00")
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)

            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()
            db.session.expire(envelope)

            assert envelope.covering_movements == [], (
                "a purchases settlement records no figure of its own"
            )
            assert sum(
                (entry.amount for entry in envelope.entries), Decimal("0"),
            ) == Decimal("177.96")

    def test_the_purchase_carries_BOTH_of_the_banks_days(
        self, app, db, seed_user,
    ):
        """Ruling **R-FW**: a purchase has a budget clock and a cash clock.

        The day it was MADE is what the bank states inside a card line's
        description (182 of the developer's 361 lines); the day it POSTED is
        when the money left.  Both are the bank's, and both are written in one
        ``create_entry`` call -- so a purchase the bank has already taken never
        exists, even briefly, as an outstanding one.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=posted,
                transaction_on=posted - timedelta(days=3),
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.settled_on == posted
            assert entry.purchased_on == posted - timedelta(days=3)
            assert recorded.made_on == posted - timedelta(days=3)

    def test_the_purchase_says_the_BANK_wrote_its_figure(
        self, app, db, seed_user,
    ):
        """Ruling **R-BAL61**, at the one site that states ``observed``.

        The born-purchase builder (``_create._born_purchase``) is one of the
        two places in the app that state a figure as the bank's, and the
        door no longer infers it from the ``observed`` day beside it (plan
        step X-bi-3e-1) -- so the label is graded here, through the review
        screen's own create path, on the stored column.  Change the builder
        to say ``typed`` and this fails while every day assertion still
        passes.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=posted,
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.amount == Decimal("57.96")
            assert entry.figure_source_id == ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.OBSERVED,
            )

    def test_a_line_stating_no_made_day_takes_the_posting_day(
        self, app, db, seed_user,
    ):
        """179 of 361 lines state none, so this is the majority arm.

        The posting day is the tightest bound a source stating nothing
        supports: money cannot clear before it moves.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=posted, transaction_on=None)

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.purchased_on == posted
            assert entry.settled_on == posted

    def test_the_line_stops_being_unexplained(self, app, db, seed_user):
        """Recorded through the SAME match door as every correspondence.

        Ruling **R-FT**'s table and ruling **R-FV**'s identity-only rule are
        not re-implemented here: the door builds a
        :class:`~app.services.statement_match.MatchSubmission` and hands it to
        :func:`~app.services.statement_match.accept_match`, so a re-import
        cannot re-offer the line and an undo has something to delete.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)
            before = statement_match.review_set(a_scope(seed_user))
            assert line.id in {ln.line_id for ln in before.unmatched}

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            after = statement_match.review_set(a_scope(seed_user))
            assert line.id not in {ln.line_id for ln in after.unmatched}
            assert recorded.match_id in {
                group.match_id for group in accepted_acts(seed_user)
            }
