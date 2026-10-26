"""What the door refuses: every refusal arm, a screen that offers exactly what the door accepts,
which consent reaches it, and the per-line safeguard against recording what the books already
hold.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.utils.dates import display_today
from app.models.statement_match import StatementMatchMember
from app.services.statement_match import (
    Consent,
    RuleView,
    IncomeCreation,
    MatchSubmission,
    ReviewedBatch,
    record_income_from_line,
    review_set,
)

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_rule,
    a_scope,
    a_transaction,
    an_import,
)
from tests._test_helpers import last_covered_day

from ._helpers import _a_deposit, _record, _rows


class TestWhatItRefuses:
    """Every arm, written to fail if its refusal were deleted."""

    def test_an_OUTFLOW_is_refused(self, app, db, seed_user):
        """The exact mirror of the create door's own inflow refusal.

        Without it a `-$180.00` debit would be booked as `+$180.00` of income:
        the writer takes the direction from the SIGN, so the refusal is what
        keeps the two doors total over the lines rather than overlapping.
        """
        line = _a_deposit(seed_user, amount="-180.00")

        with pytest.raises(ValidationError, match="money ARRIVING"):
            _record(seed_user, line)

        assert _rows(seed_user) == []

    def test_a_CLAIMED_merchant_CREDIT_is_refused_as_income(
        self, app, db, seed_user,
    ):
        """A refund is not income, and this door says so rather than filing it.

        Plan step ``bank_import:X-gj-2b-2``, ruling **R-II**.  The mirror of
        :func:`~._create._load_line`'s own refusal, and the pair is what keeps
        the two doors TOTAL and DISJOINT over the lines the schema allows: a
        credit whose merchant carries a SPENDING answer is a refund the
        purchase door owns, and every other inflow is this door's.

        **This is the misfiling R-II exists to prevent.**  Recording it here
        would book a refund as income -- grossing up both sides of the income
        statement, and, because ``posting_service._settled_target`` takes its
        ledger class from the transaction TYPE, minting an INCOME-class ledger
        account for what is really a contra to a spending category.

        The screen renders no income control for such a line, so this fires on
        a stale page or a crafted body -- which is exactly when a refusal has
        to exist: a refusal a browser can walk around is not a refusal.
        """
        envelope = a_transaction(
            seed_user, name="Amazon", amount="100.00", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        line = _a_deposit(seed_user, amount="28.29", merchant="Amazon")

        with pytest.raises(ValidationError, match="REFUND"):
            _record(seed_user, line)

        # The envelope staged above is the ONLY row: no income row was minted,
        # and no purchase either -- this door writes neither, it refuses.
        assert _rows(seed_user) == [envelope]

    def test_a_line_ANOTHER_MATCH_already_claims_is_refused(
        self, app, db, seed_user,
    ):
        """Recorded twice is money counted twice."""
        line = _a_deposit(seed_user)
        _record(seed_user, line)
        db.session.flush()

        with pytest.raises(ValidationError, match="already matched"):
            _record(seed_user, line)

        assert len(_rows(seed_user)) == 1

    def test_a_line_on_ANOTHER_ACCOUNT_is_refused(
        self, app, db, seed_user, second_user,
    ):
        """The scope is the one statement of whose lines may be reached."""
        theirs = a_bank_line(
            second_user, an_import(second_user), amount="500.00",
        )

        with pytest.raises(ValidationError):
            scope = a_scope(seed_user)
            record_income_from_line(
                IncomeCreation(line_id=theirs.id), scope,
                RuleView.build(scope.owner_id, scope.account_id),
            )

        assert _rows(seed_user) == []

    def test_a_day_the_bank_puts_in_the_FUTURE_is_refused_before_any_write(
        self, app, db, seed_user,
    ):
        """R-EJ's refusal, asked BEFORE the row exists rather than after.

        ``mint_uncategorized`` writes and settles, and the settle verb is what
        refuses a day that has not happened yet -- so until 2026-08-27 this
        door wrote a settled row and then raised, leaving it for the batch's
        SAVEPOINT to take back.

        **Called DIRECTLY rather than through ``apply_reviewed``, and that is
        the whole point of this case**: the savepoint hides the defect, so a
        route-level test cannot see it.  Mutating the refusal away left the
        route case green; it fails here.  ``record_income_from_line`` is in
        ``__all__``, so a caller without a savepoint is a supported one.
        """
        ahead = display_today() + timedelta(days=3)
        line = _a_deposit(seed_user, posted_on=ahead)

        with pytest.raises(ValidationError, match="has not happened yet"):
            _record(seed_user, line)

        assert _rows(seed_user) == []

    def test_a_day_NO_SAVED_PERIOD_covers_is_refused_before_any_write(
        self, app, db, seed_user,
    ):
        """The refusal has to fire BEFORE the row exists, not after it.

        A settled row left behind by a refused act would be money the owner
        never accepted, and this door does not lean on the batch's SAVEPOINT
        to take it back -- which is the ordering ``mint_uncategorized``'s
        docstring states and this is what grades it.
        """
        beyond = last_covered_day(seed_user["bootstrap_period"]) + timedelta(days=400)
        line = _a_deposit(seed_user, posted_on=beyond)

        with pytest.raises(ValidationError, match="No pay period covers"):
            _record(seed_user, line)

        assert _rows(seed_user) == []


class TestTheScreenOffersExactlyWhatTheDoorAccepts:
    """A control the door refuses is the shape this package keeps closing."""

    def test_an_OUTFLOW_is_not_offered(self, app, db, seed_user):
        """It has a create arm instead, which is a different card."""
        _a_deposit(seed_user, amount="-180.00")

        assert review_set(a_scope(seed_user)).recordable_inflows == ()

    def test_a_line_PAST_the_calendar_carries_no_period(
        self, app, db, seed_user,
    ):
        """The screen says so instead of rendering a doomed control.

        ``pay_period_id`` is ``None`` exactly where
        :meth:`ReviewScope.period_holding` would refuse, so the template's own
        condition and the door's refusal are one derivation.
        """
        beyond = last_covered_day(seed_user["bootstrap_period"]) + timedelta(days=400)
        _a_deposit(seed_user, posted_on=beyond)

        offered = review_set(a_scope(seed_user)).recordable_inflows
        assert [item.pay_period_id for item in offered] == [None]


class TestWhichConsentReachesThisDoor:
    """Which act classes a STANDING RULE may perform here (**R-GH**).

    **This class asserted the opposite until plan step
    ``bank_import:X-gj-2a``**, under the name ``TestNoRuleReachesThisDoor`` and
    ruling **bank_import:R-GW**'s reading that *a merchant answer says where
    SPENDING goes*, so no rule could mean *record this deposit*.  Ruling
    **R-HT(a)** amended the answer set with a member that says exactly what a
    deposit from a signature IS, so the boundary moved -- and the boundary that
    did NOT move is the one still asserted below.
    """

    def test_a_rule_consented_batch_MAY_carry_an_income(self, app, db, seed_user):
        """R-HT(a): filing a deposit is an act R-GH consents to once.

        It CREATES a row from a new bank line and modifies nothing the owner
        made by hand, which is the act class the ruling splits on -- so the
        batch is constructible where it used to raise.
        """
        batch = ReviewedBatch(
            skips=(),
            consent=Consent.STANDING_RULE, matches=(), creations=(),
            incomes=(IncomeCreation(line_id=1),),
        )

        assert batch.item_count == 1

    def test_a_rule_consented_batch_carrying_a_MATCH_is_still_unconstructible(
        self, app, db, seed_user,
    ):
        """The boundary R-HT(a) did NOT move, and the reason it did not.

        **R-HT(b)'s group rule names a ROW SET**, which modifies rows the owner
        made by hand, so it applies only on their OK -- and that is exactly the
        act reaching ``accept_match``.  Kept unrepresentable rather than
        maintained, which is what the income arm's removal must not be read as
        weakening.
        """
        with pytest.raises(ValueError, match="cannot carry a match"):
            ReviewedBatch(
                skips=(),
                consent=Consent.STANDING_RULE,
                # The submission's CONTENT is irrelevant here: the refusal is
                # about the act CLASS a rule may consent to, and it fires in
                # ``__post_init__`` before anything reads the item.
                matches=(
                    MatchSubmission(line_ids=frozenset({1}), rows=frozenset()),
                ),
                creations=(), incomes=(),
            )

    def test_a_TICKED_deposit_is_never_marked_applied_by_a_rule(
        self, app, db, seed_user,
    ):
        """R-GT's fact: the door records the consent it was GIVEN.

        The default is the one that claims less, so a caller that says nothing
        records a tick -- and this is the path an owner's own OK takes.
        """
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        member = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.match_id == recorded.match_id)
            .first()
        )
        assert member.match.applied_by_rule is False


class TestTheSafeguardAgainstRecordingWhatTheBooksHold:
    """Ruling **bank_import:R-GW**'s per-line duplicate check (``ArrivalsAlreadyHeld``).

    **The only way this door can double-count money** is by recording a
    deposit the books already hold, and the signal the card was first written
    around -- the pass's own near-miss sentence -- fires only where some TIER
    admitted a candidate and declined it: 4 of 16 on the developer's own data,
    missing three payroll deposits worth `$7,838.92` whose app rows sit outside
    every tier's bound.  This class grades the fact that replaced it.  It had
    no test at all until an adversarial review 2026-08-27 said so.
    """

    def test_it_NAMES_the_rows_the_period_already_holds(
        self, app, db, seed_user,
    ):
        """The count, the total and the labels, so the owner can find them."""
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        # NOT the sum of the two rows, and not either of them: an exact
        # figure is the proposer's to claim, and this case is about the method.
        line = _a_deposit(seed_user, amount="2600.00")

        held = review_set(a_scope(seed_user)).arrivals_already_held_in(line)

        assert held is not None
        assert {row.label for row in held.rows} == {
            salary.name, allowance.name,
        }
        assert held.total == Decimal("2512.92")

    def test_a_period_holding_NOTHING_unexplained_says_nothing(
        self, app, db, seed_user,
    ):
        """The state that makes recording safe gets no sentence at all."""
        line = _a_deposit(seed_user, amount="2600.00")

        assert review_set(
            a_scope(seed_user),
        ).arrivals_already_held_in(line) is None

    def test_rows_in_ANOTHER_period_do_not_warn(self, app, db, seed_user):
        """The question is about THIS deposit's paycheck, not the account.

        Without the span test the sentence would fire on every deposit an
        account with any unexplained income holds, which is the warning-on-
        every-row shape this arc measures money going through.

        **A SECOND line is recorded in the later period, and it is what makes
        this case fire.**  ``unmatched_rows`` only offers a row whose period
        overlaps the span the account's statements COVER
        (``_could_have_been_shown``), so with one line in the bootstrap period
        the later-period row is absent from the set entirely -- and the case
        passed while measuring that absence rather than the period test.
        Mutating the period clause to ``and True`` left it green.  Found by
        mutation testing this step's own controls 2026-08-27; the assertion
        that the row IS offered is what keeps it honest.
        """
        later = a_later_period(seed_user)
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
            period=later,
        )
        _a_deposit(
            seed_user, amount="17.00", posted_on=later.start_date,
            sequence_in_group=1,
        )
        line = _a_deposit(
            seed_user, amount="2600.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )

        review = review_set(a_scope(seed_user))

        # The row really is on offer -- otherwise this measures its absence.
        assert salary.name in {row.label for row in review.unmatched_rows}
        assert review.arrivals_already_held_in(line) is None

    def test_a_deposit_SMALLER_than_the_smallest_row_says_nothing(
        self, app, db, seed_user,
    ):
        """The proof that keeps this a signal rather than noise.

        Every unexplained row is positive, so no subset of them can come to
        less than the smallest -- a `$0.15` dividend cannot BE a `$2,473.38`
        salary row at any tolerance.  These eight lines are what ruling
        **bank_import:R-GW** exists for, and a sentence on them would be the alarm that
        teaches an owner to stop reading alarms.
        """
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        line = _a_deposit(seed_user, amount="0.15")

        assert review_set(
            a_scope(seed_user),
        ).arrivals_already_held_in(line) is None

    def test_a_deposit_AT_OR_ABOVE_the_smallest_row_DOES_warn(
        self, app, db, seed_user,
    ):
        """The boundary the proof turns on, asserted from the other side.

        A test for the suppression alone would pass if the method returned
        ``None`` for everything, so the two are written together.

        **The figure matches no row and no sum of them**, deliberately: an
        exact-amount pair is claimed by the proposer, which takes the row out
        of ``unmatched_rows`` and would make this case measure the proposer
        rather than this method.
        """
        a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        line = _a_deposit(seed_user, amount="60.00")

        held = review_set(a_scope(seed_user)).arrivals_already_held_in(line)

        assert held is not None
        assert held.total == Decimal("2512.92")

    def test_an_EXPENSE_row_is_not_income_the_books_hold(
        self, app, db, seed_user,
    ):
        """A deposit is never explained by a bill the bank has not shown."""
        a_transaction(seed_user, name="Electricity", amount="180.00")
        line = _a_deposit(seed_user, amount="2600.00")

        assert review_set(
            a_scope(seed_user),
        ).arrivals_already_held_in(line) is None

    def test_a_REFUND_is_asked_the_SAME_question_by_the_screen(
        self, app, db, seed_user,
    ):
        """The other pipeline, through ``review_set`` rather than a hand map.

        Plan step ``bank_import:X-gj-2b``.  Ruling **R-II** routes a
        container-answered merchant credit into the PURCHASE pipeline, which
        asked ``search_gap`` and the proposed-destination test and NOT this
        one -- so a hazard this package added a control for was live for the
        class the very next change routed past it.

        **It is asserted end to end deliberately.**  The unit cases in
        ``test_verdict`` hand :func:`~._verdict.ruled` a map they built
        themselves, so a :func:`~._reads._already_held_by_line` that answered
        ``{}`` for every line would leave every one of them green.  What this
        grades is that the SCREEN asks the question at all.
        """
        envelope = a_transaction(
            seed_user, name="Amazon", amount="100.00", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        # A row the bank has never shown, in the credit's own pay period and
        # smaller than it -- so the credit could be it, which is the whole
        # question.
        a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        line = _a_deposit(seed_user, amount="60.00", merchant="Amazon")

        [item] = [
            item for item in review_set(a_scope(seed_user)).creatable
            if item.line.line_id == line.id
        ]

        assert item.verdict is not None
        assert item.verdict.withheld is not None, (
            "a refund whose period already holds unexplained income must not "
            "file itself -- the screen has to ask the same question the "
            "income door does"
        )
        assert "count the same money twice" in item.verdict.withheld
        assert "39.54" in item.verdict.withheld

    def test_a_REFUND_whose_period_holds_NOTHING_is_not_withheld(
        self, app, db, seed_user,
    ):
        """The control, without which the case above grades a screen that
        withheld every refund."""
        envelope = a_transaction(
            seed_user, name="Amazon", amount="100.00", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        line = _a_deposit(seed_user, amount="60.00", merchant="Amazon")

        [item] = [
            item for item in review_set(a_scope(seed_user)).creatable
            if item.line.line_id == line.id
        ]

        assert item.verdict is not None
        assert item.verdict.withheld is None
