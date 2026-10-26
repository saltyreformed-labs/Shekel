"""The reviewed pass: its receipt names this act, every unexplained line reaches exactly one list,
and the card's OK files under the answer the card states.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from sqlalchemy.exc import IntegrityError

from app.models.transaction import Transaction
from app.services.statement_match import (
    Consent,
    IncomeCreation,
    ReviewedBatch,
    apply_reviewed,
    review_set,
)

from tests.test_services.test_statement_match._builders import a_rule, a_scope

from ._helpers import _a_deposit


class TestThePassReportsIt:
    """A receipt that did not name this act would be one of two lies."""

    def test_a_pass_that_ONLY_records_a_deposit_did_not_move_nothing(
        self, app, db, seed_user,
    ):
        """``moved_nothing`` over a row this pass created and settled.

        The exact failure ``repriced_count`` was added for, one door over.
        """
        line = _a_deposit(seed_user)

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert outcome.deposited_count == 1
        assert outcome.moved_nothing is False

    def test_it_is_NOT_counted_as_a_purchase(self, app, db, seed_user):
        """``recorded_count``'s caption says *as a purchase*, and it is not."""
        line = _a_deposit(seed_user)

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert outcome.recorded_count == 0
        assert outcome.envelopes_created == 0

    def test_it_is_NOT_counted_as_a_row_MARKED_AS_HAVING_HAPPENED(
        self, app, db, seed_user,
    ):
        """The receipt may not claim the bank confirmed a record the owner had.

        This row goes through ``record_match`` in ``content.rows``, so it
        reaches ``_apply_day`` like any other member -- and it is ALREADY
        settled on the bank's own day by the time it gets there, so that door
        reports ``unchanged``.  Were it born Projected and settled by the
        match instead, the panel would read *"1 row(s) marked as having
        happened"* about a row this act is the only reason exists, which is
        exactly why a minted RESIDUAL is kept out of ``_apply_day``.
        """
        line = _a_deposit(seed_user)

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert (outcome.settled_count, outcome.corrected_count) == (0, 0)
        assert outcome.redated_count == 0

    def test_the_receipt_names_the_figure_and_says_it_has_no_category(
        self, app, db, seed_user,
    ):
        """The one thing the owner still has to act on."""
        line = _a_deposit(seed_user, amount="0.15")

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        summary = outcome.applied[0].summary
        assert "$0.15" in summary
        assert "no category" in summary

    def test_the_receipt_reports_it_in_the_BANKS_direction(
        self, app, db, seed_user,
    ):
        """POSITIVE, because the bank paid it in.

        The purchase arm negates its own figure onto this convention; doing
        the same here would report a deposit as a withdrawal.
        """
        line = _a_deposit(seed_user, amount="0.15")

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert outcome.applied[0].amount == Decimal("0.15")

    def test_a_REFUSED_deposit_does_not_cost_the_others(
        self, app, db, seed_user,
    ):
        """The ruled failure policy, over this door's own refusals."""
        good = _a_deposit(seed_user, amount="0.15")
        bad = _a_deposit(seed_user, amount="-180.00")

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(
                    IncomeCreation(line_id=bad.id),
                    IncomeCreation(line_id=good.id),
                ),
            ),
            a_scope(seed_user),
        )

        assert (outcome.deposited_count, outcome.refused_count) == (1, 1)
        assert "money ARRIVING" in outcome.refused[0].reason


class TestWhichLinesGetAnActAndWhichSTILLDoNot:
    """Every unexplained line reaches exactly one list -- measured both ways.

    ``ck_bank_statement_lines_amount_real_nonzero`` declares ``amount <> 0``,
    and :func:`~._rules.pipeline_for` is total over both directions and every
    answer -- so every line is a candidate for exactly one ACT.  (It was the
    two lists reading the SIGN directly, ``amount < 0`` here and ``amount > 0``
    there, until plan step ``bank_import:X-gj-2b-2`` made the dispatcher the
    partition; the totality argument is the same one, moved.)

    **The LISTS were not total either, and plan step ``bank_import:X-gm``
    is what made them so.**  A first draft of this class asserted totality and
    was wrong: ``_creatable_lines`` dropped an outflow the bank dates MADE
    after it POSTED before the split (finding **N-325**), and that line reached
    none of the lists while still being counted by ``awaiting_review_count``.
    Named by this step's own adversarial review 2026-08-27, and closed by
    deleting the drop rather than the claim -- the class name is kept because
    the second case below is still the one that grades it.
    """

    @pytest.mark.parametrize("amount", ["-180.00", "0.15"])
    def test_every_ORDINARY_unexplained_line_is_offered_SOME_act(
        self, app, db, seed_user, amount,
    ):
        """One card or the other, and never neither.

        Before this step ``creatable`` took the outflows and NOTHING took the
        other side, so an inflow appeared in ``unmatched`` -- and therefore in
        the awaiting count -- with no control anywhere that could dispose of
        it.  This is that hole as a test: whichever direction the line runs,
        exactly one of the two lists holds it.

        **No app row is staged**, deliberately: a `$180.00` envelope beside a
        `-$180.00` line is an exact match, so the proposer would claim the line
        and this case would pass while measuring the proposer instead.
        """
        line = _a_deposit(seed_user, amount=amount)

        review = review_set(a_scope(seed_user))

        offered = (
            [item.line.line_id for item in review.creatable]
            + [item.line.line_id for item in review.recordable_inflows]
        )
        assert offered == [line.id]

    def test_an_IMPOSSIBLE_DAY_outflow_NOW_reaches_a_list(
        self, app, db, seed_user,
    ):
        """The class bank_import:R-GW did not close, closed by X-gm.

        An outflow the bank dates as MADE after it POSTED has no day a
        purchase could happen on -- finding **N-325**, ruled 2026-08-19 -- and
        ``_creatable_lines`` used to answer that by declining the line and
        counting it on ``ReviewBounds``, which left it in ``unmatched`` and in
        the grid's awaiting count with no control anywhere.  *This case was
        written to fail if a later step gave the class an act, and plan step
        ``bank_import:X-gm`` is that step.*

        The line is in ``creatable`` now, carrying the create door's own
        refusal and no placement, so it reaches a CARD while the control that
        could never succeed is still withheld.  Exactly one list holds it,
        which is the same claim the deposit case above makes.
        """
        day = seed_user["bootstrap_period"].start_date
        line = _a_deposit(
            seed_user, amount="-42.00",
            posted_on=day, transaction_on=day + timedelta(days=1),
        )

        review = review_set(a_scope(seed_user))

        # **All four lists are enumerated**, because "exactly one holds it" is
        # a claim about every one of them: the other three are SUBSETS of
        # ``unmatched``, so an enumeration missing one is a claim about three.
        assert [item.line.line_id for item in review.creatable] == [line.id]
        assert review.parked == ()
        assert review.answered_never == ()
        assert review.recordable_inflows == ()
        assert [item.line_id for item in review.unmatched] == [line.id]
        assert review.creatable[0].withheld is not None
        assert review.creatable[0].placement is None

    def test_a_ZERO_line_cannot_EXIST(self, app, db, seed_user):
        """The constraint the two doors' totality rests on, graded directly.

        A FIRING control on a database guarantee rather than on app code: drop
        ``amount <> 0`` and this fails, which is the warning that
        :func:`~._rules.is_inflow` -- the one place the two doors' partition is
        spelled since plan step ``bank_import:X-gj-2b`` -- has a third arm that
        no case covers, because *not arriving* would stop meaning *leaving*.
        """
        with pytest.raises(IntegrityError, match="amount_real_nonzero"):
            _a_deposit(seed_user, amount="0.00")

        db.session.rollback()


class TestTheCARDsOKFilesUnderTheSameAnswerTheCARDSTATES:
    """The automatic door and the press are ONE derivation (**R-HT(a)**).

    **This class exists because they were two** (adversarial code review
    2026-08-31).  The category rode on :class:`IncomeCreation`, set only by
    :meth:`~._placement.InflowPlacement.creation_for`, which the import-time
    rule pass reaches and no route does -- so the Reconcile card rendered *Add
    as Interest income*, the owner pressed OK, and an UNCATEGORIZED row was
    written.  The figure, the day and the period were right; what was wrong is
    that the card asserted something the door did not do.

    **It is reachable on the ordinary sequence**, not a corner: import, then
    state the rule on the merchants page, then return to Reconcile.  The line
    was never `fresh` by then, so the card is what the owner acts on.
    """

    def test_a_TICKED_deposit_is_filed_under_the_rule_the_card_names(
        self, app, db, seed_user,
    ):
        """The whole point: what the card says and what the door writes agree.

        Driven through ``apply_reviewed`` with ``Consent.TICKED`` -- the door
        the page's Apply reaches -- rather than through the rule pass, because
        the rule pass was never the broken half.
        """
        category = seed_user["categories"]["Salary"]
        a_rule(
            seed_user, "Dividend Earned", income_category_id=category.id,
        )
        line = _a_deposit(seed_user, merchant="Dividend Earned")
        db.session.commit()

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )
        db.session.flush()

        assert outcome.deposited_count == 1, outcome.refused
        recorded = (
            db.session.query(Transaction)
            .filter(Transaction.account_id == seed_user["account"].id)
            .order_by(Transaction.id.desc())
            .first()
        )
        assert recorded.category_id == category.id

    def test_a_TICKED_deposit_with_NO_rule_is_still_uncategorized(
        self, app, db, seed_user,
    ):
        """The pairing, so the case above cannot pass by always categorising.

        A deposit no rule answers is what this door has always written and
        must keep writing: the app does not know what it is, and inventing an
        answer is the misfiling ruling **R-FN** refused.
        """
        line = _a_deposit(seed_user, merchant="Someone New")
        db.session.commit()

        outcome = apply_reviewed(
            ReviewedBatch(
                skips=(),
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )
        db.session.flush()

        assert outcome.deposited_count == 1, outcome.refused
        recorded = (
            db.session.query(Transaction)
            .filter(Transaction.account_id == seed_user["account"].id)
            .order_by(Transaction.id.desc())
            .first()
        )
        assert recorded.category_id is None
