"""What the screen and the register show: the removals the undo would make, the import page's
preview and the register's fold, neither scaling with the account, and the register's bound
(ruling bank_import:R-GX).
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, statement_match, status_seam, transaction_service
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services.statement_match import RowKind

from tests._test_helpers import an_entered_day, typed

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    accepted_acts,
    an_import,
)

from ._helpers import _record, _a_new_envelope, _release, _a_swipe, _a_submission


class TestTheScreenNamesWhatTheUndoWouldRemove:
    """One derivation: what the panel prints is what the door does."""

    def test_the_accepted_panel_names_the_rows_and_the_figure(
        self, app, db, seed_user,
    ):
        """The Undo button destroys money records, so the row says which.

        A control naming a count and no figure is the bare consent box ruling
        **R-GD(a)** refused one tier up, for a `$2,473.38` figure.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()

        group = accepted_acts(seed_user)[0]

        assert group.match_id == created.match_id
        assert len(group.removes.rows) == 2
        assert group.removes.cash_amount == Decimal("-57.96")
        assert group.removes.moves_money is True
        labels = [row.label for row in group.removes.rows]
        assert "Walmart: POINT OF SALE DEBIT L340 WAL-MART" in labels
        assert "Walmart" in labels
        assert [row.is_container for row in group.removes.rows] == [
            False, True,
        ]

    def test_the_preview_and_the_door_remove_the_SAME_rows(
        self, app, db, seed_user,
    ):
        """Two derivations would let the screen promise what the button does not.

        The panel is rendered, then the door is run, and the ids the panel
        printed are exactly the rows that are gone.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        group = accepted_acts(seed_user)[0]
        promised = [(row.kind, row.row_id) for row in group.removes.rows]

        _release(seed_user, created.match_id)
        db.session.flush()

        for kind, row_id in promised:
            model = (
                TransactionEntry
                if kind is statement_match.RowKind.PURCHASE else Transaction
            )
            assert db.session.get(model, row_id) is None, (
                f"the panel promised {kind} {row_id} would go and it did not"
            )

    def test_a_match_whose_row_was_EDITED_names_the_REFUSAL_not_a_removal(
        self, app, db, seed_user,
    ):
        """The disagreement one derivation exists to prevent.

        A created purchase the owner has edited stops the undo, so a panel
        that went on listing "2 rows, -$57.96" would be promising a removal the
        button refuses.  The refusal travels on the SAME value the door raises
        from.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        entry_service.update_entry(
            created.entry_id, seed_user["user"].id,
            description="Walmart -- garden hose",
        )
        db.session.flush()

        group = accepted_acts(seed_user)[0]

        assert group.removes.refusal is not None
        assert "you have edited that row since" in group.removes.refusal
        # **A refused act reports NOTHING to remove**, so no reader can print
        # a destruction the press will not perform -- the two fields are
        # exclusive by construction rather than by each reader remembering to
        # branch, which is the defect the import page shipped with until
        # adversarial security review 2026-08-24.
        assert group.removes.rows == ()
        assert group.removes.cash_amount == Decimal("0.00")
        assert group.removes.moves_money is False
        with pytest.raises(ValidationError) as caught:
            _release(seed_user, created.match_id)
        assert str(caught.value) == group.removes.refusal

    def test_a_container_cannot_be_put_BEYOND_the_purchase_door(
        self, app, db, seed_user,
    ):
        """The state this case once drove is unrepresentable since ruling R-BAL78.

        Through plan step ``X-bi-3e`` the owner could put the budget line this
        act created beyond the purchase door -- a stored figure typed over a
        row holding the act's purchase -- and this case graded that the undo
        then refused BEFORE writing anything.  Plan step ``balance:X-bi-4a``
        refuses the typed figure itself (ruling **R-BAL78**: the purchases
        ARE the figure), at the settle verb and at the seam, so the container
        stays on its purchases and the act stays removable; what this case
        grades now is that the door holds and the release's arithmetic arm is
        never reached.  The original account of the path follows, kept
        because it is the record of how the arm was found.

        Found in this step's OWN build, by driving the case rather than
        arguing it.

        The owner puts the budget line this act created beyond the purchase
        door.  ``entry_service`` then refuses to remove the purchase under it,
        and the first build discovered that HALFWAY through the removal: the
        panel had already offered *"Undo removes 1 row"*, and the release
        raised with the act already deleted from the session, which breaks this
        package's promise that a refused act leaves the database exactly as it
        was without depending on the rollback.

        So the preview asks the purchase door's own question, and the refusal
        arrives before anything is written.

        **The specimen was the ARCHIVE until plan step balance:X-am deleted
        that status, and a first attempt DELETED this case on the argument that
        no other container state reaches the arm.  That argument was false**,
        and ``_subject_removal``'s own docstring said so on the same page:
        *"the container this act created can be put beyond that afterwards, by
        being archived OR RE-CLOSED AT A STORED FIGURE."*  Two independent
        adversarial reviews caught it, and the path below was then driven end
        to end rather than reasoned about -- the `lessons.md` entry is *an
        ONLY-way argument is one writer from wrong*.

        The path, all of it through controls the owner has: the container is a
        one-off envelope (a placed row of a rule-less definition since plan
        step balance:X-bi-7b), so its full-edit popover renders *Track
        individual purchases* and lands it on the DEFINITION, and
        ``is_envelope`` is NOT in ``_LOCKED_EDIT_FIELDS`` -- unticking it on a
        settled row is admitted deliberately, because it gives the row its
        own amount back.  ``settles_from_entries`` then goes False, ruling
        **R-FF**'s guard stops biting, and a typed Actual records a
        ``corrected`` basis.  Editing the PARENT does not bump the ENTRY's
        ``version_id``, so the edited-row refusal above does not fire first
        and this arm is the one reached.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        envelope = db.session.get(Transaction, created.transaction_id)
        transaction_service.settle_transaction(envelope)
        db.session.flush()

        # The flag lives on the DEFINITION since the container is a placed
        # row of one (balance:X-bi-7b: leaf 7b-1 minted it, leaf 7b-2 landed
        # the popover's control there), which is the act the card performs.
        envelope.template.is_envelope = False
        db.session.flush()
        with pytest.raises(ValidationError, match="takes its figure from the purchases"):
            transaction_service.apply_requested_status(
                envelope, envelope.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("999.99"))),
            )
        db.session.flush()
        assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
            None, None,
        ), "the container stays on its purchases -- the door held"

        # The undo still names both rows the act created -- the purchase and
        # the container -- and refuses nothing.
        group = accepted_acts(seed_user)[0]
        assert group.removes.refusal is None
        assert {row.kind for row in group.removes.rows} == {
            RowKind.PURCHASE, RowKind.TRANSACTION,
        }
        assert db.session.get(TransactionEntry, created.entry_id) is not None
        assert db.session.get(Transaction, created.transaction_id) is not None

    def test_a_match_that_created_nothing_names_nothing(
        self, app, db, seed_user,
    ):
        """An act between rows that already existed removes nothing.

        What the SCREEN does with that is ruling **bank_import:R-GY**'s and it
        changed at plan step ``bank_import:X-gf-2``: the press still confirms,
        because it still destroys the record of the correspondence, and the
        dialog's wording is what varies
        (``test_statement_reconcile.test_the_settled_tabs
        .TestTheSettledTabsAreWhereAnActIsFoundAndUndone
        .test_an_undo_the_door_would_REFUSE_says_so_and_offers_no_dialog``,
        where that coverage sits now that plan step ``bank_import:X-gi-2`` has
        deleted the register).  What is asserted here is the
        DERIVATION under it -- this undo would take back no row and move no
        money -- which is what that wording is chosen from.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        statement_match.accept_match(
            _a_submission(seed_user, line, txn), a_scope(seed_user),
        )
        db.session.flush()

        group = accepted_acts(seed_user)[0]

        assert group.removes.rows == ()
        assert group.removes.moves_money is False


class TestTheBulkPreviewDoesNotScaleWithTheAccount:
    """The import page folds every act; it may not do it one row at a time.

    ``removals_by_match`` was measured at **478 queries and 0.458 s** on the
    developer's own 230-act account carrying 235 creations, against 5 queries
    before the step -- because ``planned_removals`` reached each subject with
    ``session.get``, which is right for the DOOR and wrong for a fold.

    **The remedy was a bulk WARM whose result had to be HELD**, SQLAlchemy's
    identity map keeping weak references -- so a warm nothing pointed at was
    collected before the fold reached it and the per-row queries came straight
    back.  Plan step ``bank_import:X-gf-2`` deleted both halves of that
    discipline: every subject arrives on its creation through
    ``_acts._WHOLE_ACT``, so it is loaded with the act and held by the act.
    A caller can no longer forget either step, because there is no step.
    Found by adversarial security review 2026-08-24.
    """

    @staticmethod
    def _statements_for(seed_user, count, base=0):
        """Return how many SQL statements folding the account's acts costs.

        Args:
            seed_user: The seeded user bundle.
            count: How many further acts to record before folding.
            base: Where this batch's line ordinals start, so two calls in one
                test do not collide on ``uq_bank_statement_lines_identity``.

        Returns:
            The statement count for one fold over every act on the account.
        """
        for index in range(base, base + count):
            _record(
                seed_user,
                _a_swipe(seed_user, sequence=index, amount="-10.00"),
                new_envelope=_a_new_envelope(seed_user, name=f"M{index}"),
            )
        db.session.flush()
        db.session.expire_all()
        seen = []
        listener = lambda *args, **kwargs: seen.append(1)  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            statement_match.removals_by_match(
                seed_user["user"].id, seed_user["account"].id,
                {
                    row.id for row in db.session.query(StatementMatch)
                    .filter(
                        StatementMatch.account_id
                        == seed_user["account"].id,
                    ).all()
                },
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
        return len(seen)

    def test_the_query_count_is_FLAT_in_the_number_of_acts(
        self, app, db, seed_user,
    ):
        """Two acts and eight acts cost the same handful of statements.

        A count asserted against a constant would drift with any unrelated
        eager load; what this pins is the SHAPE -- that the fold does not pay
        PER ACT.  **Shown to fire**: asking the database for each container's
        remaining entries, which is how ``_container_survives`` was first
        written, takes the eight-act reading to 18 against the two-act 12.

        **It did NOT cover the weak-reference half, and that is now moot
        rather than uncovered.**  Dropping the warm's own reference read
        identically here -- eight subjects are not enough for the collector to
        reach them inside one call -- and cost 478 statements against 9 on the
        developer's own 230-act database.  There is no warm and no reference to
        drop since plan step ``bank_import:X-gf-2``; what would reintroduce the
        cost is removing a chain from ``_acts._WHOLE_ACT``, which THIS
        control sees, because the subjects would then be fetched per act.
        """
        few = self._statements_for(seed_user, 2)
        many = self._statements_for(seed_user, 6, base=2)

        assert many <= few + 2, (
            f"folding 8 acts cost {many} statements where 2 cost {few}: the "
            f"bulk preview is paying per act again"
        )


class TestTheAcceptedFoldDoesNotScaleWithTheAccount:
    """The register folds every act too, and had no control saying so.

    Plan step ``bank_import:X-gf-2``.  ``accepted_groups`` is the same fold as
    ``removals_by_match`` and went without the warm the other one had -- it
    looked cheap only because 218 of the developer's 221 acts pre-date the
    creations relation and short-circuit, so the per-act cost was paid by
    nobody's data yet.  Both are now flat by construction
    (``_acts._WHOLE_ACT``) rather than by a discipline, and this is the
    control that says so for the reader the REGISTER renders.

    It grades the SHAPE, not a constant: a count asserted against a number
    would drift with any unrelated eager load, and what must not happen is
    paying per act.
    """

    @staticmethod
    def _statements_for(seed_user, count, base=0):
        """Return how many SQL statements folding the account's acts costs.

        Args:
            seed_user: The seeded user bundle.
            count: How many further acts to record before folding.
            base: Where this batch's line ordinals start, so two calls in one
                test do not collide on ``uq_bank_statement_lines_identity``.

        Returns:
            The statement count for one fold over every act on the account.
        """
        for index in range(base, base + count):
            _record(
                seed_user,
                _a_swipe(seed_user, sequence=index, amount="-10.00"),
                new_envelope=_a_new_envelope(seed_user, name=f"R{index}"),
            )
        db.session.flush()
        db.session.expire_all()
        seen = []
        listener = lambda *args, **kwargs: seen.append(1)  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            statement_match.accepted_register(
                seed_user["user"].id, seed_user["account"].id, None,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
        return len(seen)

    def test_the_query_count_is_FLAT_in_the_number_of_acts(
        self, app, db, seed_user,
    ):
        """Two acts and eight acts cost the same handful of statements.

        **Shown to fire**: dropping the member-subject chains from
        ``_WHOLE_ACT`` -- which is what this reader did by hand until this step
        -- takes the eight-act reading well past the two-act one, because every
        line, row and purchase is then fetched per act.
        """
        few = self._statements_for(seed_user, 2)
        many = self._statements_for(seed_user, 6, base=2)

        assert many <= few + 2, (
            f"folding 8 acts cost {many} statements where 2 cost {few}: the "
            f"register's fold is paying per act"
        )


class TestTheRegisterBoundsWhatItRenders:
    """Ruling **bank_import:R-GX**: the bound falls on the SETTLED remainder.

    Plan step ``bank_import:X-gf-2``.  The accepted list is a log -- one row
    per act the owner ever accepts, 221 of them and 216,637 bytes on the
    developer's own account after a year -- so the register renders the newest
    :data:`~app.services.statement_match.REGISTER_LIMIT` of it and says how
    many it withheld.

    **An act that NO LONGER HOLDS is never withheld**, whatever its age, and
    that is the half worth grading: whether an act still holds is a VALUATION
    over its member rows and not a query, so the fold reaches every act anyway
    -- and an act explaining less than it claims is the one thing on that page
    worth finding.  What is bounded is what is RENDERED, never what is read.
    """

    @staticmethod
    def _an_act(seed_user, ordinal, *, amount="-10.00"):
        """Accept one match, so the register has an act to list.

        Args:
            seed_user: The seeded user bundle.
            ordinal: The line's ordinal, completing its identity.
            amount: The bank line's signed figure.

        Returns:
            The accepted :class:`~app.services.statement_match.AcceptedMatch`.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount=amount, posted_on=day,
            sequence_in_group=ordinal,
        )
        txn = a_transaction(
            seed_user, name=f"Bill {ordinal}", amount=amount.lstrip("-"),
            status=StatusEnum.DONE, settled_on=day,
        )
        db.session.flush()
        act = statement_match.accept_match(
            a_submission(a_scope(seed_user), lines=[line], transactions=[txn]),
            a_scope(seed_user),
        )
        db.session.flush()
        return act

    def test_it_renders_the_LIMIT_and_says_what_it_withheld(
        self, app, db, seed_user,
    ):
        """Three acts, a bound of one: one rendered, two counted.

        The limit is a PARAMETER here rather than the shipped 50, because what
        is under test is the arithmetic and not the number -- and staging 51
        acts to move one boundary would grade the same line at fifty times the
        cost.  ``TestTheRegisterPage`` drives the shipped default through the
        route.
        """
        for ordinal in range(3):
            self._an_act(seed_user, ordinal)

        register = statement_match.accepted_register(
            seed_user["user"].id, seed_user["account"].id, 1,
        )

        assert len(register.shown) == 1
        assert register.withheld_count == 2

    def test_an_act_that_NO_LONGER_HOLDS_is_shown_however_old(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the exemption, and for the ORDER.

        The oldest act is the one the bound would drop, so a register that
        applied the limit before the exemption would hide exactly the row this
        page exists to surface.  Its row is broken the way finding **N-349**
        names: something else deleted a row it named.
        """
        doomed = self._an_act(seed_user, 0)
        for ordinal in (1, 2):
            self._an_act(seed_user, ordinal)
        # ...and the OLDEST act stops holding, by the hand edit the accepted
        # list is re-reviewable for -- through the row's own door, which
        # mirrors the day onto the covering movement the act names (plan
        # step ``credit_card:CC-5-4a-1``, ruling **R-CC43**; through
        # ``CC-5-3`` the act named the row and this wrote its column).
        # Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        # under R-CC43 -- the member names the payment; a day moves through the seam that mirrors
        # it; a settled row is ticked as its payment."
        member = db.session.query(StatementMatchMember).filter(
            StatementMatchMember.match_id == doomed.match_id,
            StatementMatchMember.transaction_entry_id.isnot(None),
        ).one()
        row = member.entry.transaction
        transaction_service.apply_requested_status(
            row, row.status_id,
            settle_day=an_entered_day(
                seed_user["bootstrap_period"].start_date + timedelta(days=4),
            ),
        )
        db.session.flush()

        register = statement_match.accepted_register(
            seed_user["user"].id, seed_user["account"].id, 1,
        )

        shown = [group.match_id for group in register.shown]
        assert doomed.match_id in shown, (
            "the act that no longer holds was withheld by the bound"
        )
        assert shown[0] == doomed.match_id, (
            "an act that no longer holds must sort above the ones that do"
        )
        assert register.withheld_count == 1

    def test_NO_bound_renders_the_whole_record(self, app, db, seed_user):
        """What the *show everything* link asks for."""
        for ordinal in range(3):
            self._an_act(seed_user, ordinal)

        register = statement_match.accepted_register(
            seed_user["user"].id, seed_user["account"].id, None,
        )

        assert len(register.shown) == 3
        assert register.withheld_count == 0
