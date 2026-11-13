"""The undo's rules for a removal: a settled parent's rule as its arithmetic (finding N-229, ruling
R-GG), the delete of the row it was handed (finding N-371), and the two removals it refuses as
edits.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.exceptions import PageOutOfDate, ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    credit_workflow,
    entry_service,
    statement_match,
    status_seam,
    transaction_service,
)
from app.services.cash_ledger import in_flight_movements, movement_cash_leg
from app.services.entry_credit_workflow import sync_entry_payback
from app.models.statement_match import StatementMatch, StatementMatchCreation, StatementMatchMember
from app.services.statement_match._release import planned_removals

from tests._test_helpers import typed

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    an_envelope,
    an_import,
)

from ._helpers import _record, _a_new_envelope, _release, _posted_total, _a_swipe


class TestTheSettledParentRuleIsTheArithmetic:
    """Finding **N-229**'s removal half, corrected at the root by **R-GG**."""

    @staticmethod
    def _closed_holding(seed_user, *, posted=True):
        """Return an envelope closed from two purchases, the second doomed.

        Args:
            seed_user: The seeded user bundle.
            posted: Whether the doomed purchase carries the day the bank took
                it.  That flag is the whole rule: a posted purchase sits in
                the settled figure AND in ``posted_purchase_sum``, so removing
                it moves both and the row's own close books what it always
                booked; an unposted one sits only in the figure, so removing
                it re-prices the close.

        Returns:
            ``(envelope, doomed_entry)``.
        """
        start = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            template=False,
        )
        a_purchase(
            seed_user, envelope, amount="120.00", description="Kroger",
            purchased_on=start + timedelta(days=1),
            settled_on=start + timedelta(days=1),
        )
        doomed = a_purchase(
            seed_user, envelope, amount="57.96", description="Walmart",
            purchased_on=start + timedelta(days=3),
            settled_on=start + timedelta(days=3) if posted else None,
        )
        db.session.flush()
        transaction_service.settle_from_entries(envelope)
        db.session.flush()
        return envelope, doomed

    def test_a_POSTED_purchase_may_be_removed_and_the_close_is_untouched(
        self, app, db, seed_user,
    ):
        """The measurement the rule is written from.

        The envelope is worth nothing of its own before and after -- a
        ``purchases`` close has no covering movement, its purchases carrying
        the money (ruling **R-BAL81**) -- and the account's posted total
        moves ``822.04 -> 880.00``, which is the removed purchase's own
        `$57.96` line gone from its own day and nothing else.  Before this
        step the removal was refused outright, and 103 purchases a statement
        pass created in error had no door at all.
        """
        envelope, doomed = self._closed_holding(seed_user)
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert _posted_total(seed_user) == Decimal("822.04")

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()
        db.session.expire(envelope)

        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert _posted_total(seed_user) == Decimal("880.00")

    def test_an_UNPOSTED_purchase_is_admitted_since_the_row_books_nothing(
        self, app, db, seed_user,
    ):
        """The case the refusal was written about, and ruling R-BAL77 lifts it.

        Through plan step ``X-bi-3e`` removing it moved the envelope's own leg
        ``-57.96 -> 0.00`` -- the close shrinking on a past day with no
        external evidence, already-spent money handed back to the projection.
        Since plan step ``balance:X-bi-4a`` a plan row books nothing of its
        own (ruling **R-BAL80**): the un-dated purchase is a movement in
        flight, and removing it removes that and nothing else.  The row is
        worth nothing to the matcher before and after (ruling **R-BAL81**:
        a ``purchases`` close has no covering movement); through X-bi-4a's
        first cut the matcher's row-leg producer read the removal as
        ``-57.96 -> 0.00``, a figure no other reader booked, and R-BAL81
        deleted it.
        """
        envelope, doomed = self._closed_holding(seed_user, posted=False)
        account_id, scenario_id = envelope.account_id, envelope.scenario_id
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert [
            (flight.entry_id, flight.delta)
            for flight in in_flight_movements(account_id, scenario_id)
        ] == [(doomed.id, Decimal("-57.96"))]

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()

        assert db.session.get(TransactionEntry, doomed.id) is None
        db.session.expire(envelope)
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert in_flight_movements(account_id, scenario_id) == []

    def test_a_STORED_figure_settlement_is_still_refused(
        self, app, db, seed_user,
    ):
        """A ``derived`` close fixed its figure before this purchase existed.

        Its cost cannot fall by the purchase, whose own movement would
        otherwise be counted beside the covering movement that already
        carries the whole close (ruling **R-BAL80**).

        **A fourth case stood beside this one until plan step balance:X-am**:
        an ARCHIVED parent, refused whatever its basis and carrying its own
        sentence, because the shared message told the owner to *set the row
        back to Projected* -- a transition the state machine refused for the
        terminal status, which is finding **N-302**'s shape.  Deleting the
        status deleted both the case and the second sentence: every settled row
        can now take that repair, so one message is true for all of them.
        """
        start = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            status=StatusEnum.DONE, settled_on=start + timedelta(days=1),
        )
        doomed = a_purchase(
            seed_user, envelope, amount="57.96", description="Walmart",
            purchased_on=start, settled_on=start + timedelta(days=1),
        )
        db.session.flush()
        assert status_seam.recorded_settlement(envelope).source is (
            MovementFigureSourceEnum.RESOLVED
        )

        with pytest.raises(ValidationError, match="records a fixed figure"):
            entry_service.delete_entry(doomed.id, seed_user["user"].id)

        assert db.session.get(TransactionEntry, doomed.id) is not None

    def test_a_CREDIT_purchase_may_be_removed_and_the_payback_follows(
        self, app, db, seed_user,
    ):
        """The arm the rule's own paragraph is written about, graded.

        A card purchase moves no cash through THIS account
        (``cash_ledger.movement_cash_leg`` reads it as ``0.00``; it leaves
        through its CC Payback), so removing it changes nothing the family
        moves here: the movements' cash sum reads the same before and after,
        and the row itself is worth nothing of its own either way (ruling
        **R-BAL81**).  The card's own liability follows: the CC Payback is
        re-derived down by the purchase.  (Through X-bi-4a's first cut this
        pinned ``settled_cash_leg``, whose credit term moved with the figure
        so the row's own close read the same; R-BAL81 deleted it.)

        **It had no test at all until adversarial financial review 2026-08-24
        deleted the ``is_credit`` clause and watched the whole suite pass** --
        the create arm never mints a credit purchase, so the matcher's own
        cases cannot reach it and only a hand delete does.
        """
        start = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            template=False,
        )
        a_purchase(
            seed_user, envelope, amount="120.00", description="Kroger",
            purchased_on=start + timedelta(days=1),
            settled_on=start + timedelta(days=1),
        )
        a_later_period(seed_user)
        doomed = entry_service.create_entry(
            transaction_id=envelope.id, user_id=seed_user["user"].id,
            details=entry_service.EntryDetails(
                figure=typed(Decimal("57.96")), description="Amazon",
                purchased_on=start + timedelta(days=3), is_credit=True,
            ),
        )
        payback = sync_entry_payback(envelope.id, seed_user["user"].id)
        db.session.flush()
        transaction_service.settle_from_entries(envelope)
        db.session.flush()
        assert movement_cash_leg(envelope, doomed) == Decimal("0.00")
        before = sum(
            (movement_cash_leg(envelope, entry) for entry in envelope.entries),
            Decimal("0.00"),
        )
        assert before == Decimal("-120.00")
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert payback.estimated_amount == Decimal("57.96")

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()
        db.session.expire(envelope)

        assert db.session.get(TransactionEntry, doomed.id) is None
        # What the family moves through THIS account is untouched: the card
        # purchase moved none of it, which is the whole argument for
        # admitting this.
        assert sum(
            (movement_cash_leg(envelope, entry) for entry in envelope.entries),
            Decimal("0.00"),
        ) == before
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        # ...and the card owes less, because the spend it repays has gone.
        assert db.session.get(Transaction, payback.id) is None

    def test_a_PROJECTED_parent_is_unaffected(self, app, db, seed_user):
        """The rule speaks only about a settled row; an open one is untouched."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        doomed = a_purchase(seed_user, envelope, amount="57.96")
        db.session.flush()

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()

        assert db.session.get(TransactionEntry, doomed.id) is None


class TestTheDeleteRemovesTheRowItWasHANDED:
    """Finding **N-371**, plan step ``bank_import:X-gf-3a``.

    ``_remove``'s transaction arm did ``db.session.get(Transaction, row_id)``
    and handed the result to ``transaction_service.delete_transaction``, whose
    signature says *the user the caller proved owns it* and which re-checks
    ``user_id`` nowhere -- so the ownership of a row this path DESTROYS rested
    on where its id had come from.  Its sibling arm never had the gap:
    ``entry_service.delete_entry`` re-validates ownership through the parent
    transaction chain itself, and that asymmetry is what made this one
    invisible.

    **A behavioural test cannot see this.**  Both spellings reach the same
    instance through SQLAlchemy's identity map, so the same rows are deleted
    either way and every figure agrees.  What the cases below grade is that
    ``_remove`` reads :attr:`PlannedRemoval.subject` and not :attr:`row_id`.

    **That is NARROWER than the invariant the finding states**, and saying so
    is the point: *no id re-enters a query on a path that destroys* is not
    graded here and cannot be, because ``db.session.get(Transaction,
    row.subject.id)`` would satisfy every case below while re-introducing
    exactly the lookup N-371 names.  The invariant is held by reading
    ``_remove``, which is four lines long; these cases hold the half a test
    can.
    """

    @staticmethod
    def _an_act_that_created_an_envelope(seed_user):
        """Record a line into a NEW envelope and return its match id.

        The shape with a CONTAINER as well as a purchase, because the container
        is what goes through the transaction arm -- the purchase goes through
        ``entry_service``, which was never the finding.
        """
        line = _a_swipe(seed_user)
        db.session.flush()
        recorded = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.commit()
        return recorded.match_id

    def test_the_planned_removal_carries_the_row_it_names(
        self, app, db, seed_user,
    ):
        """The instance, not just its id -- and it IS that row.

        Asserted by identity against the row read back independently, so a
        field carrying some other transaction of the right shape would fail.
        """
        match_id = self._an_act_that_created_an_envelope(seed_user)
        match = db.session.get(StatementMatch, match_id)

        planned = planned_removals(match)

        container = next(
            row for row in planned.rows if row.is_container
        )
        assert container.subject is db.session.get(
            Transaction, container.row_id,
        )
        assert container.subject.id == container.row_id

    def test_the_row_REMOVED_is_the_one_carried_and_not_the_one_NAMED(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL, and the first version of it could not fire.

        That version recorded which object reached
        ``transaction_service.delete_transaction`` and compared it with the one
        the removal carries -- and it passed with the refetch RESTORED, because
        ``db.session.get`` returns the same instance out of the identity map.
        A mutation run measured it: the whole file stayed green with
        ``db.session.get(Transaction, row.row_id)`` back in ``_remove``.

        What distinguishes the two is a removal whose ``subject`` and
        ``row_id`` DISAGREE -- deliberately inconsistent, because *which of the
        two does it use* is the only question here, and no consistent value can
        be asked it.  A door reading the id deletes ``named``; one removing the
        row it was handed deletes ``carried``.
        """
        # NOT RECURRING, because that is the only shape this path ever
        # removes: a residual is a rule-less definition's row since plan step
        # balance:X-bi-7b and a created envelope is built link-less (until
        # 7b-3), so the delete verb's SOFT arm -- keyed on ``recurs`` -- is
        # unreachable from here and its hard delete is what runs.  A RECURRING
        # row would be flagged rather than removed, and both assertions below
        # would read the same either way.  Built link-less here: the question
        # is which of two ids the door reads, not what it deletes.
        carried = a_transaction(
            seed_user, name="Carried", amount="11.00", template=False,
        )
        named = a_transaction(
            seed_user, name="Named", amount="12.00", template=False,
        )
        db.session.commit()
        removal = statement_match.PlannedRemoval(
            kind=statement_match.RowKind.TRANSACTION,
            row_id=named.id,
            # **Agreeing with NEITHER row**, so no reader keyed on the
            # label can produce the right answer either: a first version said
            # "Carried" and a label lookup would have passed.
            label="neither of them",
            cash_amount=Decimal("0.00"),
            is_container=True,
            subject=carried,
        )

        statement_match._release._remove(  # pylint: disable=protected-access
            removal, seed_user["user"].id,
        )
        db.session.flush()

        assert db.session.get(Transaction, carried.id) is None
        assert db.session.get(Transaction, named.id) is not None

    def test_two_removals_naming_ONE_row_are_EQUAL_whatever_they_carry(
        self, app, db, seed_user,
    ):
        """The carried instance is out of equality, and that is deliberate.

        What makes two planned removals the same is which row they NAME; a
        value whose equality depended on which instance happened to be attached
        would make every comparison in this suite an identity-map assertion.

        **A first version compared two `planned_removals` calls and could not
        fail.**  Both run in one session, so both tuples hold the identical
        instances out of the identity map, and `app/models/` defines no
        `__eq__` -- so deleting `compare=False` compared an object with itself
        and stayed green.  Two removals naming ONE row while carrying
        DIFFERENT instances is the only shape that asks the question.
        """
        carried = a_transaction(
            seed_user, name="Carried", amount="11.00", template=False,
        )
        other = a_transaction(
            seed_user, name="Other", amount="12.00", template=False,
        )
        db.session.flush()
        first = statement_match.PlannedRemoval(
            kind=statement_match.RowKind.TRANSACTION,
            row_id=carried.id,
            label="Carried",
            cash_amount=Decimal("0.00"),
            is_container=True,
            subject=carried,
        )

        assert first == replace(first, subject=other)


class TestARevertedSubjectIsRefusedAsAnEdit:
    """A subject the owner reverted since the match refuses the undo as an EDIT.

    **Plan step balance:X-bx made this path live, and nothing entered it
    before it.**  That step widened
    :func:`~app.services.row_valuation.settled_contribution` -- and through it
    the matcher's row-leg producer of the time, ``cash_ledger.settled_cash_leg``
    -- to REFUSE a row that has not settled, where it used to price the row's
    plan column; three readers in this package caught that refusal, because
    they render the review page and a raise there would strand the account
    with no in-app repair (finding **N-302**), and on those three the refusal
    changed an ANSWER rather than failing a test.  ``grep -rn
    AmountUnresolvable tests/test_services/test_statement_match/`` returned
    NOTHING before this class: the step's own full-suite mutation probe could
    not have seen a defect there.  It had one, graded below.

    **Since ruling R-BAL81 (plan step balance:X-bi-4a) nothing here can fail
    to price.**  A row is worth what its covering movement moves, a stored
    figure: a REVERTED subject's kept movement is un-dated, so it is worth
    ``0.00`` (``status_seam.covered_cash_leg``), and the version test refuses
    the undo as the edit it is -- the same row and the same sentence the
    catching arm produced.  The catches and the second sentence ("the app
    can no longer work out what that row is worth", for a refusal with no
    edit behind it) are deleted with the refusal, and the case that pinned
    that arm on an UNEDITED subject is deleted with them: its state, a
    reverted row at its creation revision, was one no door could write.

    The reachable shape is an ordinary REVERT.  A subject a match minted is
    settled when the act records it, so reverting that row to Projected both
    bumps ``version_id`` (the owner has edited it) and un-dates its covering
    movement (it is worth nothing to the undo).
    """

    def test_a_REVERTED_subject_is_refused_for_the_EDIT_not_for_the_price(
        self, app, db, seed_user,
    ):
        """The message defect X-bx introduced, as its own control.

        Both refusals were honest about refusing, so no money moved either
        way and a test asserting "the undo refuses" would pass on the defect.
        What separated them is what the owner is TOLD: the price sentence
        said the app cannot work out what the row is worth and offered no
        repair, which was false here -- the row was reverted, and the edit
        sentence names that and tells them what to do about it.

        ``_subject_removal`` priced BEFORE it compared revisions, so once the
        price started refusing, the price sentence won a race the edit
        sentence used to win; the fix stated the revision test in the refusal
        path too.  Under ruling **R-BAL81** the price cannot refuse -- the
        reverted subject is worth ``0.00`` -- so the edit sentence is the only
        one left, and this case pins that the price sentence is gone.
        """
        subject = a_transaction(
            seed_user, name="Residual", amount="41.00", template=False,
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.flush()
        creation = StatementMatchCreation(
            match_id=0, account_id=seed_user["account"].id,
            transaction_id=subject.id, transaction_entry_id=None,
            # The revision this act left the row at.
            created_version_id=subject.version_id,
        )

        # The owner REVERTS it: out of the settled band, and a revision later.
        status_seam.apply_status_change(
            subject, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.flush()
        assert subject.version_id != creation.created_version_id
        # The precondition the case rests on: the reverted row is worth
        # nothing (its kept movement is un-dated, ruling R-BAL81), and
        # nothing refuses to say so.
        assert status_seam.covered_cash_leg(subject, subject.account_id) == Decimal("0.00")

        row, refusal = statement_match._release._subject_removal(  # pylint: disable=protected-access
            creation, subject, subject.account_id,
        )

        assert refusal is not None
        assert "you have edited that row since" in refusal
        assert "can no longer work out what that row is worth" not in refusal
        # A refused act reports nothing to remove.
        assert row.cash_amount == Decimal("0.00")


class TestTheUndoFreesNoLineAnotherActHolds:
    """The one removal that could free ANOTHER act's line is refused as an edit.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2b), ruling **R-CC135**.  An
    Undo names no bank line, so it opens no press of its own: each removal it
    makes is a press of one call naming nothing, which refuses any line that
    call would free (``match_withdrawal.take_out_of_matches``, ruling
    **R-CC127**: *"A button with no warning sends nothing"*).  The one removal
    that could reach a line ANOTHER act holds is the purchase arm's payback
    teardown: deleting an envelope's last CARD purchase deletes its CC payback
    in the same removal act (``entry_service._doors.delete_entry``), and a
    payback set back to Projected keeps its payment un-dated, which an act may
    still name.

    **No door reaches it, measured through the doors below.**  A purchase the
    create arm records is born a debit one (``_create._born_purchase`` states
    no card flag), and the card total counts ``is_credit`` alone
    (``app.utils.entry_partition``), so the purchase becomes its envelope's
    last card purchase only by a CC tick -- an EDIT, which moves its revision,
    and the Undo refuses an edited subject before it writes anything
    (``_subject_removal``).  An envelope the act minted is settled and
    refuses the tick until it is set back to Projected, another edit on the
    way.  So the default press is a fence that does not fire here, and these
    cases are what hold that: with the revision test deleted, each Undo
    instead reaches the teardown and is refused as out of date.
    """

    @staticmethod
    def _its_payback_matched_and_reverted(seed_user, created):
        """Make the act's purchase the envelope's last card purchase, its payback matched.

        Through the doors the owner has, on a Projected envelope: the
        purchase ticked CC (its payback created in the next period), the
        payback's payment matched to a CARD PAYMENT line by a SECOND act, and
        the payback set back to Projected, which keeps that payment un-dated
        and the second act on it -- the one shape in which deleting the last
        card purchase deletes a payback a match still names.  Commits, so the
        Undo is read against what the database holds.

        Args:
            seed_user: The seeded user bundle.
            created: The create arm's result, its envelope Projected.

        Returns:
            ``(payback_id, payment_line_id)``.
        """
        projected = ref_cache.status_id(StatusEnum.PROJECTED)
        envelope = db.session.get(Transaction, created.transaction_id)
        assert envelope.status_id == projected
        a_later_period(seed_user)
        db.session.flush()
        entry_service.update_entry(
            created.entry_id, seed_user["user"].id, is_credit=True,
        )
        db.session.flush()
        payback = credit_workflow.get_active_payback(envelope.id)
        payment_line = a_bank_line(
            seed_user, an_import(seed_user), amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date
            + timedelta(days=6),
            description="CARD PAYMENT",
        )
        db.session.flush()
        statement_match.accept_match(
            a_submission(
                a_scope(seed_user), lines=[payment_line],
                transactions=[payback],
            ),
            a_scope(seed_user),
        )
        transaction_service.apply_requested_status(payback, projected)
        db.session.commit()
        return payback.id, payment_line.id

    @staticmethod
    def _assert_refused_as_an_edit_and_nothing_moved(
        seed_user, created, payback_id, payment_line_id,
    ):
        """Press the Undo; assert the edit sentence, and that both acts stand.

        The session is COMMITTED after the refusal, so anything the door
        wrote before refusing is in the database the re-reads see -- a
        rollback here would hide exactly that.
        """
        with pytest.raises(ValidationError) as caught:
            _release(seed_user, created.match_id)
        assert not isinstance(caught.value, PageOutOfDate), str(caught.value)
        assert "you have edited that row since" in str(caught.value)
        db.session.commit()
        db.session.expire_all()

        assert db.session.get(StatementMatch, created.match_id) is not None
        assert db.session.get(TransactionEntry, created.entry_id) is not None
        assert db.session.get(Transaction, payback_id) is not None
        payment_member = db.session.query(StatementMatchMember).filter(
            StatementMatchMember.bank_statement_line_id == payment_line_id,
        ).one()
        assert payment_member.match_id != created.match_id

    def test_a_purchase_filed_in_an_envelope_the_owner_PICKED(
        self, app, db, seed_user,
    ):
        """Route 1: the line filed in an existing Groceries envelope."""
        envelope = an_envelope(seed_user)
        db.session.flush()
        created = _record(seed_user, _a_swipe(seed_user), transaction_id=envelope.id)
        db.session.commit()
        payback_id, payment_line_id = self._its_payback_matched_and_reverted(
            seed_user, created,
        )

        self._assert_refused_as_an_edit_and_nothing_moved(
            seed_user, created, payback_id, payment_line_id,
        )

    def test_a_purchase_in_an_envelope_the_act_MINTED(
        self, app, db, seed_user,
    ):
        """Route 2: the line recorded into a new envelope the act created.

        The act closes the envelope it mints on the bank's day, and a settled
        envelope refuses the CC tick (``is_credit`` re-costs its purchase), so
        the owner sets the envelope back to Projected first.
        """
        created = _record(
            seed_user, _a_swipe(seed_user),
            new_envelope=_a_new_envelope(seed_user),
        )
        db.session.commit()
        with pytest.raises(ValidationError, match="has settled"):
            entry_service.update_entry(
                created.entry_id, seed_user["user"].id, is_credit=True,
            )
        db.session.rollback()
        transaction_service.apply_requested_status(
            db.session.get(Transaction, created.transaction_id),
            ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()
        payback_id, payment_line_id = self._its_payback_matched_and_reverted(
            seed_user, created,
        )

        self._assert_refused_as_an_edit_and_nothing_moved(
            seed_user, created, payback_id, payment_line_id,
        )
