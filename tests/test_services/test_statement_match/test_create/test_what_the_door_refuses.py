"""What the create door refuses, each refusal written to fail if it were deleted: a line the bank
dates made after it posted, refused before an envelope is minted, and a destination outside the
line's own period.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.category import Category
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import statement_match
from app.services.statement_match import NewEnvelope
from tests._test_helpers import open_books_before_the_first_assertion

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_rule,
    a_scope,
    a_transaction,
    an_import,
)

from ._helpers import _closed_from_purchases, _offerable, _record


class TestTheDoorRefusesAnIMPOSSIBLEDayLine:
    """Plan step ``bank_import:X-gm``, finding **N-325**.

    The bank dates the line MADE after it POSTED, so its money left before it
    was spent.  ``entry_service._reject_settled_before_purchase`` has always
    refused the resulting pair, backed by
    ``ck_transaction_entries_settled_not_before_purchase`` -- but it refuses it
    LAST, after ``resolve_destination`` may have minted an envelope for a
    purchase that will never exist, and it says so in the entry door's own
    words rather than the bank line's.  This door refuses it FIRST and in the
    sentence the card shows.

    **Both halves are graded here**, because the reason for the new refusal is
    entirely the ORDER and the WORDING -- an assertion that "it raises" would
    pass against the old tree.
    """

    @staticmethod
    def _an_impossible_line(seed_user):
        """Stage one line the bank dates made a day AFTER it took the money."""
        day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        return a_bank_line(
            seed_user, an_import(seed_user), amount="-31.41", posted_on=day,
            transaction_on=day + timedelta(days=1),
            description="POINT OF SALE DEBIT L340 LOWES #00907 (Lowe's)",
        )

    def test_it_is_refused_BEFORE_an_envelope_is_minted(
        self, app, db, seed_user,
    ):
        """The order is the point, so the case is written on the minting arm.

        Submitted against a NEW ENVELOPE, the old tree resolved the
        destination, created the ``budget.transactions`` row, and only then met
        ``create_entry``'s refusal.  Nothing may be staged now.
        """
        with app.app_context():
            line = self._an_impossible_line(seed_user)
            before = db.session.query(Transaction).count()

            # **NO message match here**, and the omission is deliberate: the
            # entry door refuses this pair too, so a case that asserted the
            # WORDING would fail on the wording before it ever reached the
            # count -- which is the claim.  The sentence is graded by
            # test_the_DOOR_and_the_CARD_say_the_same_thing below, and the
            # two together are what the removed refusal has to break.
            with pytest.raises(ValidationError):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))

            # The query AUTOFLUSHES, so a staged envelope is counted here;
            # this sees what the old order left behind rather than only what
            # a commit would have kept.
            assert db.session.query(Transaction).count() == before
            assert db.session.query(StatementMatch).count() == 0

    def test_a_line_dated_made_BEFORE_it_posted_is_recorded(
        self, app, db, seed_user,
    ):
        """The other side, so the refusal is not refusing every line.

        Without it a door that raised unconditionally would pass the case
        above, and the whole CREATE act would be dead with the suite green.
        """
        with app.app_context():
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-31.41",
                posted_on=day, transaction_on=day - timedelta(days=1),
                description="POINT OF SALE DEBIT L340 LOWES #00907 (Lowe's)",
            )

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            assert db.session.get(Transaction, recorded.transaction_id)

    def test_the_DOOR_and_the_CARD_say_the_same_thing(
        self, app, db, seed_user,
    ):
        """ONE sentence, which is the whole reason it lives in ``_scope``.

        The screen withholds the ADD control on
        ``_leftovers._add_refusal`` and the door raises on
        ``_scope.reject_impossible_days``; a second spelling is what
        ``no_period_refusal`` records having had to delete one refusal over.
        The door appends *Nothing was changed* and nothing else, so the card's
        whole sentence is a prefix of the door's.
        """
        with app.app_context():
            a_transaction(seed_user, name="Groceries", is_envelope=True)
            line = self._an_impossible_line(seed_user)
            db.session.commit()

            withheld = statement_match.review_set(
                a_scope(seed_user),
            ).creatable[0].withheld
            with pytest.raises(ValidationError) as raised:
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))

            assert withheld
            assert str(raised.value) == f"{withheld}  Nothing was changed."


class TestWhatTheCreateDoorRefuses:
    """Every refusal, each written to fail if the refusal were deleted."""

    def test_an_UNCLAIMED_INFLOW_line_is_refused(self, app, db, seed_user):
        """A deposit no rule claims is not a refund, so this door refuses it.

        **The backstop is no longer the schema** (ruling **bank_import:R-II**,
        plan step ``bank_import:X-gj-2b-1``):
        ``ck_transaction_entries_positive_amount`` is ``amount <> 0``, so a
        negative purchase is writable and this refusal is the DOOR's own.
        **And it is no longer about the DIRECTION either** (plan step
        ``bank_import:X-gj-2b-2``): what it refuses is an inflow the owner has
        said NOTHING about, because filing one would name a container they
        never gave, which is the guess ruling **R-HX** refused. The case below
        holds the other arm. 16 of the developer's own unexplained lines are
        inflows, three of them card refunds, so this is the ordinary
        shape rather than a crafted request.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="2573.42",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="not a refund"):
                _record(seed_user, line, transaction_id=envelope.id)
            assert db.session.query(TransactionEntry).count() == 1

    def test_a_CLAIMED_inflow_is_NOT_refused(self, app, db, seed_user):
        """The other side of the same refusal, asserted beside it.

        **The refusal is about the ANSWER and not the direction** since plan
        step ``bank_import:X-gj-2b-2``, so a case asserting only that inflows
        are refused would stay green if the door went back to refusing every
        one of them -- which would silently un-build the refund act. A credit
        whose merchant the owner has placed is a refund and the door takes it.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            a_rule(
                seed_user, "Amazon",
                template_id=envelope.template_id,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="28.29", merchant="Amazon",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)

            assert recorded.amount == Decimal("-28.29")

    def test_an_ALREADY_MATCHED_line_is_refused(self, app, db, seed_user):
        """Refused HERE rather than by the unique index after a write.

        ``uq_statement_match_members_line`` refuses the second act either way,
        and so does ``accept_match``'s own ``_load_lines`` -- but BOTH arrive
        after ``create_entry`` has already staged a purchase.  So the assertion
        that matters is the COUNT, not the raise: deleting this refusal leaves
        an orphan purchase behind on a page the owner merely double-submitted.
        Measured -- without the count assertion this test passed with the
        refusal deleted.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)
            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()
            after_first = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="already matched"):
                _record(seed_user, line, transaction_id=envelope.id)

            assert db.session.query(TransactionEntry).count() == after_first, (
                "a refused second submission staged a purchase anyway"
            )

    def test_a_line_on_ANOTHER_account_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope is the account, and a statement is one bank's record of one.

        Reaching another account's line by id would book money against a
        statement that never showed it.

        **The COUNT is the control and the raise is not**, for the reason the
        already-matched test states: ``accept_match`` scopes its own lines too,
        so deleting this clause still raises -- after ``create_entry`` has put
        a purchase from a foreign statement onto this owner's envelope.
        Measured: without the count assertion this test passed with the clause
        deleted.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_second_user)
            line = a_bank_line(
                seed_second_user, statement, amount="-57.96",
                posted_on=seed_second_user["bootstrap_period"].start_date,
            )
            before = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="no longer on this"):
                _record(seed_user, line, transaction_id=envelope.id)

            assert db.session.query(TransactionEntry).count() == before, (
                "a foreign statement line staged a purchase on this account"
            )

    def test_an_envelope_the_screen_could_not_OFFER_is_refused(
        self, app, db, seed_user,
    ):
        """One scope for the reader and the writer.

        A row closed at a FIXED figure cannot record a new purchase: its
        stored figure was fixed before the purchase was weighed, so the
        purchase's own movement would be counted beside the covering
        movement that already carries the whole close (ruling **R-BAL80**).
        Measured on a production clone under the row-leg fold of the time,
        `-163.95` became `+203.67` -- an expense row publishing an inflow --
        while the anchor true-up moved `$0.00`, so the spending was never
        recorded at all.
        """
        with app.app_context():
            fixed = a_transaction(
                seed_user, name="Mint Mobile", amount="132.69",
                is_envelope=True, status=StatusEnum.DONE,
                settled_on=seed_user["bootstrap_period"].start_date,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            offered = _offerable(seed_user)
            assert fixed.id not in {d.transaction_id for d in offered}
            # The row holds its covering movement and nothing else.
            assert fixed.purchases == []
            before = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="not one this purchase"):
                _record(seed_user, line, transaction_id=fixed.id)
            assert db.session.query(TransactionEntry).count() == before
            assert fixed.purchases == []

    def test_ANOTHER_USERS_category_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The IDOR probe every create door in this project performs.

        A foreign ``category_id`` satisfies the foreign key -- the row exists
        -- and would link another user's category onto this owner's budget.
        """
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            foreign = (
                db.session.query(Category)
                .filter(Category.user_id == seed_second_user["user"].id)
                .first()
            )

            with pytest.raises(ValidationError, match="not one of yours"):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's", category_id=foreign.id,
                ))
            assert db.session.query(Transaction).filter(
                Transaction.name == "Lowe's",
            ).count() == 0

    def test_naming_BOTH_destinations_is_refused(self, app, db, seed_user):
        """A purchase has exactly one parent.

        Stated as a refusal rather than a precedence rule: a door that silently
        preferred an arm would record something the owner did not ask for.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="exactly one place"):
                _record(
                    seed_user, line, transaction_id=envelope.id,
                    new_envelope=NewEnvelope(
                        name="Lowe's",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                )

    def test_naming_NEITHER_destination_is_refused(self, app, db, seed_user):
        """The same rule's other half."""
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="exactly one place"):
                _record(seed_user, line)

    def test_a_line_MADE_after_it_POSTED_is_refused(self, app, db, seed_user):
        """Money does not leave an account before it is spent.

        **The rule is ``entry_service``'s and this door does not restate it.**
        A first version added its own ``_reject_impossible_days`` so the
        message could name the line rather than the purchase, and so the
        refusal preceded the new-envelope write.  Both reasons failed: the SECU
        CSV reader provably cannot produce the pair (it resolves the stated day
        to the most recent one at or before the posting day), so the guard was
        rule 13's speculative shape; and the door already depends on the
        request-level rollback for every other ``entry_service`` refusal, so
        ordering bought nothing it did not already owe.  Removed after
        adversarial design review 2026-08-19.

        What is graded here is that such a line becomes no purchase and leaves
        no budget line behind -- which is the property, whichever rule states
        it.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                transaction_on=day + timedelta(days=1),
            )

            with pytest.raises(
                ValidationError, match="cannot reach your bank before",
            ):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))
            db.session.rollback()
            assert db.session.query(Transaction).filter(
                Transaction.name == "Lowe's",
            ).count() == 0

    def test_a_day_NO_PAY_PERIOD_covers_is_refused(self, app, db, seed_user):
        """130 of the developer's own 361 lines predate their first payday.

        There is no budget for such a purchase to belong to, and
        ``transactions.pay_period_id`` is NOT NULL -- so the honest answer is a
        sentence naming the day rather than a row filed under the nearest
        period, which would misplace real money.

        **The books are opened before the line first, and that is what keeps
        this case reachable at all** (plan step balance:X-f3c-2b-2b).  A
        default fixture account opens its books the day before the bootstrap
        period, so a line 400 days earlier is inside its opening equity and
        meets the books refusal instead -- correctly, because that money is
        already counted.  Books that open BEFORE the budget did is the real
        shape this arm serves: it is what finding **N-368**'s import will
        leave on the developer's own Checking account.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = (
                seed_user["bootstrap_period"].start_date
                - timedelta(days=400)
            )
            open_books_before_the_first_assertion(
                db.session, seed_user["account"], also_before=day,
            )
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
            )

            with pytest.raises(ValidationError, match="No pay period covers"):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))


class TestTheDestinationMustBeInTheLINEsOwnPeriod:
    """The security guard X-f6a-3c-2 added, which had NO test at all.

    Measured by adversarial test-quality review 2026-08-19: deleting
    ``if destination.period.period_id == pay_period_id`` from
    ``_create._existing_envelope`` left the whole suite green.  A security fix
    with no test is a security fix that gets refactored away.

    What the clause stops, in its own docstring's words: a crafted request
    filing a swipe into a Groceries envelope eighteen months forward, or
    raising a closed past envelope's recorded cost in a period the line has
    nothing to do with.  ``destinations_for`` deliberately returns budget lines
    across EVERY period on the account -- the screen renders only the line's
    own -- so this clause is the whole of what keeps the door's set the
    screen's set.
    """

    def test_an_envelope_in_ANOTHER_period_is_refused(
        self, app, db, seed_user,
    ):
        """The crafted request, refused, with nothing written."""
        with app.app_context():
            other = a_later_period(seed_user)
            elsewhere = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True, period=other,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            # It IS offerable -- just not for THIS line.
            assert elsewhere.id in {
                d.transaction_id for d in _offerable(seed_user)
            }

            with pytest.raises(ValidationError, match="not one this purchase"):
                _record(seed_user, line, transaction_id=elsewhere.id)

            db.session.flush()
            assert elsewhere.entries == []

    def test_the_SAME_envelope_in_the_line_s_own_period_is_accepted(
        self, app, db, seed_user,
    ):
        """The control, without which the case above could pass vacuously.

        Same shape, same door, same figures -- only the period differs.  A
        refusal that fired for any other reason would fail here too.
        """
        with app.app_context():
            here = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            recorded = _record(seed_user, line, transaction_id=here.id)

            db.session.flush()
            assert recorded.transaction_id == here.id
            assert [e.amount for e in here.entries] == [Decimal("57.96")]
