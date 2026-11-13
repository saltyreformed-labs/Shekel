"""The pass's failure policy (the developer's ruling of 2026-08-19): a refused item costs the
others nothing, what is not caught per item, and the pass as one unit of work for its caller.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services import statement_match
from app.services.statement_match import NewEnvelope

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_transaction,
    an_import,
)

from ._helpers import _batch, _match, _creation


class TestARefusedItemDoesNotCostTheOthers:
    """The developer's ruling of 2026-08-19, which is the whole failure policy.

    Not a hypothetical: on the developer's own statement, the day of that
    ruling, a few proposals were refused by a settled card payback's
    re-derive refusal (``entry_credit_workflow.sync_entry_payback``), and
    every other one was a correction worth making.
    """

    @staticmethod
    def _good_and_bad(seed_user):
        """Return (a match that lands, a match that cannot).

        The bad one is an UNBALANCED GROUP -- **finding N-239's own shape**,
        where the bank shows ONE payroll deposit `$0.05` above what the two app
        rows it splits into sum to.

        **It was a one-to-one match until ruling R-GD (2026-08-22)**, and had
        to change with it: a one-to-one difference is no longer a refusal but a
        CORRECTION, because the bank's figure names exactly one row and simply
        becomes it.  A GROUP is what still refuses, and it is the shape this
        docstring always claimed -- nothing says WHICH of the two rows is the
        five cents wrong, which is the indeterminacy R-FV described and R-GD
        left standing.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        good_line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        good_row = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        bad_line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=day,
            sequence_in_group=1,
        )
        bad_rows = [
            a_transaction(
                seed_user, name="Salary", amount="2473.38", income=True,
            ),
            a_transaction(
                seed_user, name="Phone Allowance", amount="100.00",
                income=True,
            ),
        ]
        return (good_line, good_row, bad_line, bad_rows)

    def test_the_good_one_lands_and_the_bad_one_is_quoted(
        self, app, db, seed_user,
    ):
        """One pass, two outcomes, and the refusal keeps its own sentence."""
        with app.app_context():
            good_line, good_row, bad_line, bad_rows = self._good_and_bad(
                seed_user,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[bad_line], transactions=bad_rows),
                _match(seed_user, lines=[good_line], transactions=[good_row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "do not add up" in outcome.refused[0].reason
            assert outcome.refused[0].line_ids == (bad_line.id,)
            assert outcome.applied[0].line_ids == (good_line.id,)

    def test_the_refused_item_wrote_nothing(self, app, db, seed_user):
        """The item that could not be applied left no trace of trying.

        **This refusal fires BEFORE the item writes anything** -- an unbalanced
        match is rejected before any settle verb runs -- so it grades the
        loop's ``continue`` rather than the savepoint.  The savepoint's own
        property, an item that has ALREADY written when it is refused, is
        :meth:`test_a_HALF_APPLIED_group_is_undone` and
        :meth:`test_a_refused_CREATION_takes_its_new_envelope_with_it`.  Stated
        because a test whose name promises the stronger thing is worse than no
        test: both survive removing the savepoint entirely, and only the two
        named here fail.
        """
        with app.app_context():
            _, good_row, bad_line, bad_rows = self._good_and_bad(seed_user)

            _batch(seed_user, matches=[
                _match(seed_user, lines=[bad_line], transactions=bad_rows),
                _match(
                    seed_user,
                    lines=[db.session.query(
                        type(bad_line),
                    ).filter_by(amount=Decimal("-180.00")).one()],
                    transactions=[good_row],
                ),
            ])
            db.session.flush()

            assert all(row.settled_on is None for row in bad_rows)
            assert good_row.settled_on is not None
            assert db.session.query(StatementMatch).count() == 1

    def test_a_HALF_APPLIED_group_is_undone(self, app, db, seed_user):
        """A match can refuse AFTER it has already moved one of its members.

        The accept door applies each member in turn, and a settle verb can
        refuse on the second -- which is exactly the refusal the developer's
        own statement met, where a settled credit-card payback cannot be
        re-derived.
        Here the shape is narrower and buildable: a purchase whose recorded day
        the bank's stated day would move PAST the day the bank posted, which
        ``entry_service.update_entry`` refuses because money cannot clear
        before it is spent.

        Without a savepoint the first purchase keeps the settle day the refused
        match wrote, so the app records money as having moved on the strength
        of a match it also says was never accepted.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            first = a_purchase(
                seed_user, envelope, amount="10.00", description="Aldi",
                purchased_on=day,
            )
            second = a_purchase(
                seed_user, envelope, amount="15.00", description="Kroger",
                purchased_on=day + timedelta(days=3),
            )
            line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
                transaction_on=day + timedelta(days=2),
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], entries=[first, second]),
            ])
            db.session.flush()

            assert outcome.applied_count == 0
            assert outcome.refused_count == 1
            assert first.settled_on is None, (
                "the first member kept the day a REFUSED match wrote"
            )
            assert second.settled_on is None
            assert db.session.query(StatementMatch).count() == 0

    def test_a_refused_CREATION_takes_its_new_envelope_with_it(
        self, app, db, seed_user,
    ):
        """The arm that stages a budget row BEFORE the refusal can fire.

        A creation stages an envelope, then a purchase, and only then records
        the match -- so an item refused at the last step has a budget line to
        undo.  Without the savepoint that row survives into the commit, and the
        owner gets an envelope for a purchase that was never recorded.

        **The refusal has to fire AFTER the envelope is staged**, or this
        asserts nothing about a savepoint.  The one that does is a line the
        bank says was MADE after it POSTED -- 2 of 361 of the developer's own
        OFX lines are that shape -- because ``entry_service.create_entry``
        refuses a posting day before its purchase day, and by then
        ``_create_envelope`` has already flushed a budget row.  A first draft
        used an INFLOW, which ``_load_line`` refuses before anything at all is
        staged; it passed against a door with no savepoint.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            made_later = a_bank_line(
                seed_user, statement, amount="-12.00", posted_on=day,
                transaction_on=day + timedelta(days=1),
            )
            ordinary = a_bank_line(
                seed_user, statement, amount="-12.00", posted_on=day,
                sequence_in_group=1,
            )
            category = seed_user["categories"]["Groceries"]

            outcome = _batch(seed_user, creations=[
                _creation(
                    seed_user, made_later,
                    new_envelope=NewEnvelope(
                        name="Refused", category_id=category.id,
                    ),
                ),
                _creation(
                    seed_user, ordinary,
                    new_envelope=NewEnvelope(
                        name="Landed", category_id=category.id,
                    ),
                ),
            ])
            db.session.flush()

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert outcome.refused[0].line_ids == (made_later.id,)
            names = {
                row.name for row in db.session.query(Transaction).all()
            }
            assert "Landed" in names
            assert "Refused" not in names, (
                "the refused item's envelope survived, so the savepoint did "
                "not roll its staged rows back"
            )


class TestWhatIsNOTCaughtPerItem:
    """The other half of the isolation rule, and it has to be asserted.

    A savepoint that swallowed everything would turn a broken ledger invariant
    into one line of a receipt, on a screen whose whole job is to move money
    correctly.  ``ValidationError`` is this project's DESIGNED refusal -- a
    sentence written for the person who submitted the form -- and it is the
    only thing the loop catches.
    """

    def test_a_broken_invariant_fails_the_whole_pass(
        self, app, seed_user, monkeypatch,
    ):
        """A broken invariant is a fact about the account, not about an item."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            # A walk raises a RuntimeError on one (cash_ledger's readers).  Until plan#3132
            # it was the stored ledger's PostingError, which no door raises without its writers.
            def _boom(*_args, **_kwargs):
                raise RuntimeError("the ledger does not balance")

            monkeypatch.setattr(
                statement_match._batch, "accept_match", _boom,
            )

            with pytest.raises(RuntimeError, match="the ledger does not balance"):
                _batch(seed_user, matches=[
                    _match(seed_user, lines=[line], transactions=[row]),
                ])

    def test_a_ValidationError_is_the_ONLY_thing_reported_per_item(
        self, app, db, seed_user, monkeypatch,
    ):
        """The control for the arm above: the designed refusal IS caught."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            def _refuse(*_args, **_kwargs):
                raise ValidationError("a designed refusal")

            monkeypatch.setattr(
                statement_match._batch, "accept_match", _refuse,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])

            assert outcome.refused_count == 1
            assert outcome.refused[0].reason == "a designed refusal"


class TestThePassIsOneUnitOfWorkForTheCALLER:
    """The savepoints bound a REFUSAL; they do not commit anything.

    ``apply_reviewed`` does not commit, so a caller that abandons the request
    leaves no trace of a pass that had already "applied" 195 items -- which is
    what makes the route's own "nothing was changed" true on a database error.
    """

    def test_nothing_survives_a_rollback_by_the_caller(
        self, app, db, seed_user,
    ):
        """A released savepoint is still inside the outer transaction."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])
            assert outcome.applied_count == 1

            _db.session.rollback()

            assert db.session.query(StatementMatch).count() == 0
