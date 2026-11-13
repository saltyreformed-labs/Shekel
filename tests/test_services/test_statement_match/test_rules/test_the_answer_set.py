"""The answer set as stored and as routed: the table's CHECKs, the five answers' round trip, the
bank's sign, and which pipeline a line enters.
"""

from decimal import Decimal

import pytest

from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.services.statement_match import (
    LinePipeline,
    pipeline_for,
    RuleAnswer,
    RuleSubmission,
    state_rules,
)
from app.services.statement_match._rules import is_inflow
from app.services.statement_match._stating import _columns_of

from tests.test_services.test_statement_match._builders import (
    a_merchant,
    a_rule,
    a_statement,
    a_transaction,
)


class TestTheTableRefusesWhatIsNotAnAnswer:
    """The CHECKs, each shown refusing a row the ORM would happily write."""

    def _row(self, db, seed_user, **columns):
        """Stage a rule row with these columns and flush it.

        ``never_a_purchase`` is NOT NULL with no default, so it is stated here
        or nothing reaches the CHECK under test at all -- ``False`` unless the
        case names it, which is what every container answer carries.
        """
        columns.setdefault("never_a_purchase", False)
        row = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=a_merchant(seed_user, "Amazon").id,
            **columns,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def test_a_template_AND_a_new_envelope_is_unwritable(
        self, app, db, seed_user,
    ):
        """Two answers to one question.

        ``ck_merchant_rules_one_answer`` spells the three legal shapes
        as three shapes, because a count-the-NULLs form cannot say this: one
        answer sets two columns and one sets none.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        with pytest.raises(Exception) as caught:
            self._row(
                db, seed_user, template_id=envelope.template_id,
                envelope_name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            )

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_a_new_envelope_stated_by_HALVES_is_unwritable(
        self, app, db, seed_user,
    ):
        """A name with no category, or a category with no name, is neither answer.

        ``transactions.category_id`` is what every spending report groups by,
        so a row created without one would be invisible to the analysis the
        purchase exists to feed.
        """
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user, envelope_name="Lowe's")

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_a_BLANK_merchant_is_unwritable(self, app, db, seed_user):
        """A blank name is a merchant the owner could neither read nor restate.

        **The rule moved to where the string now lives once** (plan step
        ``bank_import:X-gd-1``): it was stated on the answer table AND on the
        line table, two CHECKs over two copies of one value, and it is
        ``ck_merchants_name_not_blank`` alone now.  The adapter answers ``None``
        for the same input (``_secu_csv._stated_merchant``), so the two cannot
        drift.
        """
        row = Merchant(account_id=seed_user["account"].id, name="   ")
        db.session.add(row)
        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "ck_merchants_name_not_blank" in str(caught.value)

    def test_ANOTHER_ACCOUNTS_merchant_is_unwritable(
        self, app, db, seed_user, seed_second_user,
    ):
        """THE guard the scope check used to be, now structural.

        A stated answer keyed on a merchant belonging to somebody else's
        account is what ``_refuse_unknown_merchants`` was the only thing
        refusing.  ``fk_merchant_rules_merchant_account`` is composite
        -- ``(merchant_id, account_id)`` against ``uq_merchants_id_account`` --
        so the row is unwritable rather than merely unreached, which is what
        demotes that Python check to a sentence.
        """
        theirs = a_merchant(
            seed_second_user, "Theirs",
            account=seed_second_user["account"],
        )
        db.session.flush()
        row = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=theirs.id,
            never_a_purchase=True,
        )
        db.session.add(row)
        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "fk_merchant_rules_merchant_account" in str(caught.value)

    def test_TWO_answers_for_ONE_merchant_are_unwritable(
        self, app, db, seed_user,
    ):
        """One answer per merchant per account, structurally."""
        a_rule(seed_user, "Amazon")
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user)

        assert "uq_merchant_rules_account_merchant" in str(
            caught.value,
        )


class TestTheFiveAnswersRoundTrip:
    """What an answer WRITES and what a row READS BACK are one mapping.

    ``_columns_of`` turns an answer into five columns and ``RuleAnswer.of``
    turns five columns back into an answer.  They are inverse functions stated
    twice, which is the shape a new answer breaks by being added to one side
    only -- so what is graded is the round trip over EVERY member rather than
    one hand-picked case each, and a member added to the enum without a column
    mapping fails here on the day it is added.

    **It did exactly that on 2026-08-31**, when ruling **R-HT(a)**'s
    :attr:`~._rules.RuleAnswer.INCOME_CATEGORY` was added: all three cases
    failed on their ``set(fields) == set(RuleAnswer)`` guard before any of them
    reached a database, which is the cheapest place to learn it.  That guard is
    why the maps below stay literal rather than derived from the enum.
    """

    def test_every_answer_reads_back_as_itself(self, app, db, seed_user):
        """All four, driven off the enum rather than off a list here.

        ``for answer in RuleAnswer`` is the point: a list re-typed in this file
        would go stale exactly when the mapping did.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        category = seed_user["categories"]["Groceries"]
        fields = {
            RuleAnswer.TEMPLATE: {"template_id": envelope.template_id},
            RuleAnswer.NEW_ENVELOPE: {
                "envelope_name": "Lowe's", "category_id": category.id,
            },
            RuleAnswer.INCOME_CATEGORY: {"income_category_id": category.id},
            RuleAnswer.NEVER: {},
            RuleAnswer.ALWAYS_ASK: {},
        }
        assert set(fields) == set(RuleAnswer), (
            "a RuleAnswer member with no case here is one this round trip "
            "does not grade"
        )

        for answer in RuleAnswer:
            merchant = a_merchant(seed_user, f"Merchant {answer.value}")
            row = MerchantRule(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                merchant_id=merchant.id,
                **_columns_of(RuleSubmission(
                    merchant_id=merchant.id, answer=answer, **fields[answer],
                )),
            )
            db.session.add(row)
            # Flushed one at a time so the CHECK grades each shape on its own:
            # a batch would report the first violation and say nothing about
            # the rest.
            db.session.flush()

            assert RuleAnswer.of(row) is answer

    def test_every_answer_gets_its_OWN_sentence(self, app, db, seed_user):
        """The receipt is a fourth place the answer set is enumerated.

        ``_columns_of`` derives the flag and ``_apply_one`` DISPATCHED on three
        members and fell through to the fourth, so a fifth member would be
        STORED correctly and RECEIPTED as *ask me every time* -- telling the
        owner they said something they did not.  **That fall-through is a raise
        since plan step ``bank_import:X-gj-2a``**, and this case is what makes
        it a test failure rather than a 500.  The round trip above catches the storage half; this catches the
        sentence, which is the half the owner reads.  Named by adversarial
        review 2026-08-26.

        Distinct sentences rather than four literals, because what has to hold
        is that no two answers report the same thing, not what any one of them
        says.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        category = seed_user["categories"]["Groceries"]
        fields = {
            RuleAnswer.TEMPLATE: {"template_id": envelope.template_id},
            RuleAnswer.NEW_ENVELOPE: {
                "envelope_name": "Lowe's", "category_id": category.id,
            },
            RuleAnswer.INCOME_CATEGORY: {"income_category_id": category.id},
            RuleAnswer.NEVER: {},
            RuleAnswer.ALWAYS_ASK: {},
        }
        assert set(fields) == set(RuleAnswer)

        said = []
        for answer in RuleAnswer:
            db.session.flush()
            recorded = state_rules(
                (a_statement(
                    seed_user, f"Merchant {answer.value}", answer,
                    **fields[answer],
                ),),
                seed_user["user"].id,
                seed_user["account"].id,
            )
            db.session.flush()
            assert recorded.refused == ()
            assert len(recorded.stated) == 1
            said.append(recorded.stated[0])

        assert len(set(said)) == len(RuleAnswer)

    def test_a_field_belonging_to_ANOTHER_answer_is_not_written(
        self, app, db, seed_user,
    ):
        """A submission that pairs an answer with the wrong arm states nothing.

        ``state_rules`` is exported from the package, so its input is a public
        contract: a caller that built *never a purchase* while leaving a
        ``template_id`` on the value would, under a mapping that copied all
        four columns, store a TEMPLATE row whose own stated answer said
        otherwise.  ``ck_merchant_rules_one_answer`` would not catch it --
        template-and-nothing-else is a legal shape -- so the refusal has to be
        that the field is never read.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )

        columns = _columns_of(RuleSubmission(
            merchant_id=1, answer=RuleAnswer.NEVER,
            template_id=envelope.template_id,
            envelope_name="Lowe's",
            category_id=seed_user["categories"]["Groceries"].id,
            income_category_id=seed_user["categories"]["Groceries"].id,
        ))

        assert columns == {
            "template_id": None,
            "envelope_name": None,
            "category_id": None,
            "income_category_id": None,
            "never_a_purchase": True,
        }


class TestTheFlagIsPinnedOnTheContainerAnswers:
    """``ck_merchant_rules_one_answer``'s new term, shown refusing a row.

    Without it a row could name a template AND claim *never a purchase*, and
    the two readers that ask in different orders would disagree:
    ``RuleAnswer.of`` looks at the container first and calls it a TEMPLATE,
    while ``CreationBars`` looks at the flag and bars the line.  One suggests
    a destination and the other forbids one, on the same row.
    """

    def _row(self, db, seed_user, **columns):
        """Stage a rule row with these columns and flush it."""
        row = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=a_merchant(seed_user, "Amazon").id,
            **columns,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def test_a_TEMPLATE_answer_claiming_never_is_unwritable(
        self, app, db, seed_user,
    ):
        """The contradiction, on the arm that names a recurring envelope."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )

        with pytest.raises(Exception) as caught:
            self._row(
                db, seed_user, template_id=envelope.template_id,
                never_a_purchase=True,
            )

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_a_NEW_ENVELOPE_answer_claiming_never_is_unwritable(
        self, app, db, seed_user,
    ):
        """...and on the arm that creates one."""
        with pytest.raises(Exception) as caught:
            self._row(
                db, seed_user, envelope_name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
                never_a_purchase=True,
            )

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_BOTH_container_less_shapes_are_writable(
        self, app, db, seed_user,
    ):
        """The firing control: the CHECK closes a contradiction, not an answer.

        Without this the two cases above would be satisfied by a constraint
        that refused the flag outright, which would make *never a purchase*
        unwritable -- the answer worth `-$7,412.94` on the developer's own
        statement.
        """
        # Read BACK from the database in both arms: asserting the attribute on
        # the instance just constructed re-reads what Python set and would pass
        # against a column that was never written.  Found by adversarial review
        # 2026-08-26.
        barred = self._row(db, seed_user, never_a_purchase=True).id
        db.session.expire_all()
        assert db.session.get(MerchantRule, barred).never_a_purchase is True

        db.session.rollback()

        asking = self._row(db, seed_user, never_a_purchase=False).id
        db.session.expire_all()
        assert db.session.get(MerchantRule, asking).never_a_purchase is False

    def test_a_row_that_STATES_no_flag_is_unwritable(
        self, app, db, seed_user,
    ):
        """NOT NULL with no default, and this is what that buys.

        A writer that does not state the answer gets a refusal rather than one
        the schema picked for it.  ``never_a_purchase`` is the only column here
        whose absence nothing else would catch: a row with no container is a
        legal answer, so the three columns beside it raise nothing on its
        behalf.
        """
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user)

        assert "never_a_purchase" in str(caught.value)


class TestTheBanksSignConvention:
    """:func:`is_inflow` -- the ONE statement of which sign means ARRIVING.

    Plan step ``bank_import:X-gj-2b``.  Seven readers in this package need a
    bank line's direction, and each spelled the comparison for itself until
    that step's own adversarial review counted EIGHT such comparisons -- three
    of them added by the very change whose commit title read *the SIGN stops
    routing*.  A convention written eight times is eight places to write it
    backwards, and it decides which way real money is reported to have moved.

    **Graded on LITERALS, not on the constant it is derived from**, which is
    the whole value of the case: an assertion phrased against the function's
    own expression would pass with that expression inverted.
    """

    def test_a_CREDIT_is_money_arriving(self):
        """The bank states money INTO the account as positive."""
        assert is_inflow(Decimal("28.29")) is True

    def test_a_DEBIT_is_not(self):
        """And money out of it as negative."""
        assert is_inflow(Decimal("-57.96")) is False

    def test_the_smallest_representable_figures_take_the_same_arms(self):
        """One cent either way, which is where an off-by-one boundary shows.

        ``bank_statement_lines.amount`` is ``NUMERIC(12, 2)``, so a cent is the
        smallest step the column can hold and these are the two rows nearest
        the boundary that the table can actually store.
        """
        assert is_inflow(Decimal("0.01")) is True
        assert is_inflow(Decimal("-0.01")) is False


class TestWhichPipelineALineEnters:
    """:func:`pipeline_for` -- the ONE place the two acts are told apart.

    Plan step ``bank_import:X-gj-2b-2``.  What an unexplained bank line BECOMES
    was decided by its SIGN until this step, in two separate sign tests that
    ran before the owner's rule was consulted at all.  A merchant credit is
    money ARRIVING that must become a PURCHASE -- a refund back into the
    container the owner named -- so the sign could not decide it and never
    could have; it was standing in for the ANSWER.

    **Graded as a whole TABLE rather than as cases**, because the failure this
    shape invites is an uncovered cell rather than a wrong one: a sixth
    ``RuleAnswer`` added without a decision here would silently take whatever
    the fall-through does, and "no suggestion" is a plausible-looking answer
    that nobody chose.  The round-trip assertion below is the same
    ``set(...) == set(RuleAnswer)`` guard this module already uses for
    :meth:`RuleAnswer.of`.
    """

    #: Every cell of (answer x direction), stated rather than derived, so a new
    #: answer fails the coverage assertion below instead of inheriting a
    #: default.  ``None`` is the sixth key: no rule at all.
    EXPECTED = {
        (RuleAnswer.TEMPLATE, False): LinePipeline.PURCHASE,
        (RuleAnswer.TEMPLATE, True): LinePipeline.PURCHASE,
        (RuleAnswer.NEW_ENVELOPE, False): LinePipeline.PURCHASE,
        (RuleAnswer.NEW_ENVELOPE, True): LinePipeline.PURCHASE,
        (RuleAnswer.INCOME_CATEGORY, False): LinePipeline.PURCHASE,
        (RuleAnswer.INCOME_CATEGORY, True): LinePipeline.INCOME,
        (RuleAnswer.NEVER, False): LinePipeline.PURCHASE,
        (RuleAnswer.NEVER, True): LinePipeline.INCOME,
        (RuleAnswer.ALWAYS_ASK, False): LinePipeline.PURCHASE,
        (RuleAnswer.ALWAYS_ASK, True): LinePipeline.INCOME,
        (None, False): LinePipeline.PURCHASE,
        (None, True): LinePipeline.INCOME,
    }

    def test_the_table_covers_every_answer_in_both_directions(self):
        """A new ``RuleAnswer`` fails HERE rather than falling through.

        The guard, and the reason this class states a table at all: the
        dispatcher's own shape is *name what routes and fall through*, which is
        right for safety and silent for coverage.  This is what makes the
        silence visible.
        """
        answered = {answer for answer, _ in self.EXPECTED}

        assert answered == set(RuleAnswer) | {None}, (
            "every RuleAnswer needs a decision in BOTH directions -- a new "
            "answer must name its pipeline rather than inherit one"
        )
        assert len(self.EXPECTED) == (len(RuleAnswer) + 1) * 2

    #: One real figure per direction, on the bank's own convention.  Stated as
    #: DATA here rather than taken from :func:`~app.services.statement_match.
    #: _rules.is_inflow`, so this table grades that convention instead of
    #: inheriting it: a test that asked the production predicate which sign
    #: means *arriving* would pass with the predicate inverted.
    AMOUNTS = {True: Decimal("28.29"), False: Decimal("-57.96")}

    @pytest.mark.parametrize("answer,arriving", sorted(
        EXPECTED, key=lambda k: (k[0].value if k[0] else "", k[1]),
    ))
    def test_each_cell_routes_where_the_table_says(self, answer, arriving):
        """Every cell, one case each."""
        assert pipeline_for(
            amount=self.AMOUNTS[arriving], answer=answer,
        ) is self.EXPECTED[(answer, arriving)]

    def test_a_CONTAINER_answer_is_what_makes_an_inflow_a_purchase(self):
        """The whole content of the change, stated as one property.

        An inflow is a purchase exactly when its merchant's answer names a
        SPENDING CONTAINER.

        **Asserted against a LITERAL pair, not against ``CONTAINER_ANSWERS``**
        (plan step ``bank_import:X-gj-2b-3``, found by adversarial test-quality
        review).  It read ``answer in CONTAINER_ANSWERS`` -- the same constant
        ``pipeline_for`` branches on -- so both sides of the assertion came
        from one place and adding ``INCOME_CATEGORY`` to that constant left the
        case GREEN while every unclaimed deposit became a purchase.  Its own
        docstring claimed the opposite: that the property and the constant
        "cannot drift apart".  They cannot, because the case could not see
        drift.
        """
        for answer in list(RuleAnswer) + [None]:
            files_a_purchase = pipeline_for(
                amount=self.AMOUNTS[True], answer=answer,
            ) is LinePipeline.PURCHASE
            assert files_a_purchase is (
                answer in (RuleAnswer.TEMPLATE, RuleAnswer.NEW_ENVELOPE)
            )

    def test_an_OUTFLOW_is_always_a_purchase_candidate(self):
        """Whatever the answer -- the rule only decides what is SUGGESTED.

        ``NEVER`` is answered further down by
        :class:`~app.services.statement_match.CreationBars`, which is where
        ruling **R-GJ** put it; routing it away from the purchase pipeline here
        would be a second statement of that refusal.
        """
        for answer in list(RuleAnswer) + [None]:
            assert pipeline_for(
                amount=self.AMOUNTS[False], answer=answer,
            ) is LinePipeline.PURCHASE
