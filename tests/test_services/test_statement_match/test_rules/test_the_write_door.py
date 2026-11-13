"""Stating a rule moves no money; the write door's restatements and refusals, and what a rule may
name.
"""

import pytest

from app.exceptions import ValidationError
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services.statement_match import (
    account_merchants,
    PlacementKind,
    review_set,
    RuleAnswer,
    RuleSubmission,
    state_rules,
)
from app.services.statement_match._rules import offerable_templates, rules_for
from app.services.statement_match._stating import _refuse_unknown_merchants

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_rule,
    a_scope,
    a_statement,
    a_transaction,
    an_import,
)


class TestStatingARuleMovesNoMoney:
    """The property the whole design rests on, asserted rather than assumed."""

    def test_a_stated_rule_records_no_purchase_and_no_row(
        self, app, db, seed_user,
    ):
        """Answering for every merchant writes rules and nothing else.

        THE central claim.  If a rule could record a purchase, then a
        remembered answer would be a default that moves money -- which is
        exactly what ruling R-FZ removed from the destination select, and the
        reason this is a separate door rather than a third kind of batch item.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()
        before_entries = db.session.query(TransactionEntry).count()
        before_txns = db.session.query(Transaction).count()

        state_rules(
            (a_statement(
                seed_user, "Amazon", RuleAnswer.TEMPLATE,
                template_id=envelope.template_id,
            ),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.query(TransactionEntry).count() == before_entries
        assert db.session.query(Transaction).count() == before_txns
        assert db.session.query(MerchantRule).count() == 1

    def test_a_placement_does_not_TICK_the_line_it_places(
        self, app, db, seed_user,
    ):
        """The select still opens on "leave this line alone".

        A placement is rendered BESIDE the control, never into it.  The screen
        proved the rendered default separately, in the review queue's own
        ``TestTheCreateArm``; plan step ``bank_import:X-gi-2`` deleted that page
        and the case with it, and the Reconcile card has no *leave this line
        alone* default to grade -- its OK checkbox is the consent, so the
        rendered-default half is MOOT rather than lost.  What this pins is the
        service half: a placed line is still an offer, and nothing about it
        says the owner accepted anything.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        placed = [
            item for item in review.creatable
            if item.placement is not None
        ]
        assert len(placed) == 1
        assert placed[0].placement.kind is PlacementKind.RECORD_IN
        # ...and the account still holds no purchase at all.
        assert db.session.query(TransactionEntry).count() == 0


class TestStatingAndRestatingARule:
    """The write door: record, restate, and say only what changed.

    **There is no withdrawal** as of ruling **R-GS** (plan step
    ``bank_import:X-gd-2``): a rule row, once made, is only ever restated, and
    *ask me every time* is the answer that replaced taking one back.
    """

    def _state(self, db, seed_user, *statements):
        """Run the door for this owner's checking account.

        **The precondition is a MERCHANT ROW, and :func:`a_statement` stages
        it** (plan step ``bank_import:X-gd-1``).  ``state_rules`` refuses a
        statement about a merchant this account has never seen, and that scope
        is ``budget.merchants``.  This helper used to record a bank line per
        merchant, because the scope was a DISTINCT over recorded lines and a
        merchant with no line was outside it.  A merchant row OUTLIVES its
        lines now, so a row with no line is an ordinary production state -- the
        import that first recorded it was deleted -- and staging one is no
        longer a fixture shortcut past a guard.  The guard itself is graded by
        ``TestWhatTheWriteDoorRefuses``, on an id this account does not hold.
        """
        db.session.flush()
        return state_rules(
            tuple(statements),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_it_records_each_of_the_four_answers(
        self, app, db, seed_user,
    ):
        """One row per merchant, carrying the columns its answer sets."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        category = seed_user["categories"]["Groceries"]

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
            a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=category.id),
            a_statement(seed_user, "Capital One Credit Card", RuleAnswer.NEVER),
        )
        db.session.flush()

        assert len(recorded.stated) == 3
        assert recorded.refused == ()
        held = rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )
        by_name = {rule.merchant: rule for rule in held.values()}
        assert by_name["Amazon"].answer is RuleAnswer.TEMPLATE
        assert by_name["Amazon"].template_id == envelope.template_id
        assert by_name["Lowe's"].answer is RuleAnswer.NEW_ENVELOPE
        assert by_name["Lowe's"].envelope_name == "Lowe's"
        assert by_name["Capital One Credit Card"].answer is RuleAnswer.NEVER

    def test_restating_the_SAME_answer_writes_nothing_and_says_so(
        self, app, db, seed_user,
    ):
        """The section submits every merchant it renders, so most is no-ops.

        The receipt has to carry the denominator: "0 recorded" with nothing
        beside it would read as though every answer had failed, when in fact
        every one was already what the owner had said.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1

    def test_a_DIFFERENT_answer_replaces_the_row_rather_than_adding_one(
        self, app, db, seed_user,
    ):
        """One answer per merchant is structural, so restating is an UPDATE.

        Two rows would be two answers to one question, which is what
        ``uq_merchant_rules_account_merchant`` makes unwritable.
        """
        first = a_transaction(seed_user, name="Groceries", is_envelope=True)
        second = a_transaction(seed_user, name="Gas", is_envelope=True)
        a_rule(seed_user, "Amazon", template_id=first.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=second.template_id),
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        rows = db.session.query(MerchantRule).all()
        assert len(rows) == 1
        assert rows[0].template_id == second.template_id

    def test_ALWAYS_ASK_replaces_a_rule_rather_than_deleting_it(
        self, app, db, seed_user,
    ):
        """Ruling **R-GS**: a rule is restated, never un-stated.

        **This case replaced ``test_answering_NOT_SAID_WITHDRAWS_a_rule``**,
        which asserted the row COUNT went to zero.  The behaviour it graded was
        withdrawn by the developer on 2026-08-25: the control's do-nothing
        option used to delete the row and return the merchant to *you have not
        said*, and *ask me every time* is what replaced it.  The reason the
        withdrawal existed is unchanged and is still met -- when the credit-card
        arc gives Capital One its own account the Checking-side answer stops
        being right -- but taking an answer BACK and saying *I want no standing
        answer* are different statements, and only the second survives a screen
        that has to know which merchants it may still ask about.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        row = db.session.query(MerchantRule).one()
        assert row.template_id is None
        assert row.envelope_name is None
        assert row.category_id is None
        # ...and NOT the other container-less answer, which is the whole of
        # what the flag is for: *never a purchase* would BAR every line this
        # merchant ever shows.
        assert row.never_a_purchase is False

    def test_ALWAYS_ASK_on_an_unanswered_merchant_STATES_it(
        self, app, db, seed_user,
    ):
        """It is an answer even where there was nothing to replace.

        The other half of the fourth answer, and the one that separates it from
        the absence of a row: an owner who has never answered for a merchant
        and picks *ask me every time* has DECIDED something, and the exception
        queue ``bank_import:X-gf`` builds reads exactly that difference to
        decide whether to prompt them again.
        """
        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )
        db.session.flush()

        assert recorded.stated == ("Amazon: ask me every time.",)
        row = db.session.query(MerchantRule).one()
        assert row.never_a_purchase is False
        assert RuleAnswer.of(row) is RuleAnswer.ALWAYS_ASK

    def test_restating_ALWAYS_ASK_is_reported_as_UNCHANGED(
        self, app, db, seed_user,
    ):
        """The fourth answer restates like the other three.

        The section submits every merchant it renders, so an ordinary pass is
        mostly no-ops; a receipt reading "1 recorded" with no denominator would
        read as though the rest had failed.  This is the arm that would have
        been missed by writing the flag but not comparing it -- the row would
        be rewritten to the value it already held and reported as a change on
        every single Save.
        """
        a_rule(seed_user, "Amazon", always_ask=True)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1

    def test_NEVER_and_ALWAYS_ASK_are_DIFFERENT_answers_to_the_door(
        self, app, db, seed_user,
    ):
        """The two share every column, so only the flag tells them apart.

        **The case a "same answer" comparison written over the three container
        columns would pass while being wrong**: it would find nothing changed
        between *never a purchase* and *ask me every time* and leave a bar
        standing that the owner had just lifted.  Both directions, because a
        comparison can be blind in one.
        """
        a_rule(seed_user, "Amazon")
        db.session.flush()

        lifted = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )
        db.session.flush()

        assert lifted.unchanged_count == 0
        assert db.session.query(MerchantRule).one().never_a_purchase is False

        restored = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.NEVER),
        )
        db.session.flush()

        assert restored.stated == ("Amazon is never a purchase.",)
        assert db.session.query(MerchantRule).one().never_a_purchase is True


class TestWhatTheWriteDoorRefuses:
    """Each refusal, written to fail if the refusal were deleted."""

    def _state(self, db, seed_user, *statements):
        """Run the door for this owner's checking account.

        :func:`a_statement` stages the merchant these submissions are about,
        which is the precondition ``state_rules`` checks -- see
        :meth:`TestStatingAndRestatingARule._state` for why that is no longer
        a bank line.
        """
        db.session.flush()
        return state_rules(
            tuple(statements),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_a_template_on_ANOTHER_ACCOUNT_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """A statement is one bank's record of ONE account.

        ``fk_merchant_rules_template_account`` makes it unwritable
        anyway, and this is what turns that into a sentence rather than an
        ``IntegrityError`` reaching the owner as "Something went wrong".
        """
        other = a_transaction(
            seed_second_user, name="Groceries", is_envelope=True,
        )
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=other.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert "no recurring envelope on this account" in recorded.refused[0]
        assert db.session.query(MerchantRule).count() == 0

    def test_a_template_that_does_NOT_TRACK_PURCHASES_is_refused(
        self, app, db, seed_user,
    ):
        """The create door's own refusal, applied where the answer is stated.

        ``entry_service.create_entry`` refuses a parent that does not track
        purchases, so a rule naming a plain budget line would be an answer
        every one of whose placements is refused -- the chooser-that-always-
        fails shape, moved one tier back.
        """
        plain = a_transaction(seed_user, name="Electricity", is_envelope=False)

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=plain.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert db.session.query(MerchantRule).count() == 0

    def test_an_INCOME_template_is_refused(self, app, db, seed_user):
        """Money coming in is not a purchase.

        ``destinations_for`` excludes an income row for the same reason, so
        offering one here would be the offer-versus-accept drift this arc keeps
        closing.
        """
        income = a_transaction(
            seed_user, name="Paycheck", income=True, is_envelope=True,
        )

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=income.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_an_INACTIVE_template_is_refused(self, app, db, seed_user):
        """A definition the owner has turned off generates no more rows."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_ANOTHER_OWNERS_category_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The IDOR probe every create door in this project performs.

        A foreign ``category_id`` satisfies a bare foreign key perfectly well.
        ``fk_merchant_rules_category_owner`` makes it unwritable and
        this makes the refusal a sentence.
        """
        foreign = seed_second_user["categories"]["Groceries"]

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=foreign.id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert "not one of yours" in recorded.refused[0]
        assert db.session.query(MerchantRule).count() == 0

    def test_an_ARCHIVED_category_is_refused(self, app, db, seed_user):
        """The picker renders only active categories, so the door takes only those."""
        category = seed_user["categories"]["Groceries"]
        category.is_active = False
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=category.id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_a_REFUSED_statement_costs_only_ITSELF(
        self, app, db, seed_user,
    ):
        """Per-item isolation, and here it protects work rather than money.

        Refusing the whole submission would re-render the section from the
        DATABASE and so discard the other answers the owner had just picked.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        plain = a_transaction(seed_user, name="Electricity", is_envelope=False)

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
            a_statement(seed_user, "Walmart", RuleAnswer.TEMPLATE,
                            template_id=plain.template_id),
            a_statement(seed_user, "Capital One Credit Card", RuleAnswer.NEVER),
        )
        db.session.flush()

        assert len(recorded.stated) == 2
        assert len(recorded.refused) == 1
        held = rules_for(seed_user["user"].id, seed_user["account"].id)
        assert sorted(rule.merchant for rule in held.values()) == [
            "Amazon", "Capital One Credit Card",
        ]

    def test_a_merchant_this_account_NEVER_SAW_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope check, as the SENTENCE a stale page gets.

        Since plan step ``bank_import:X-gd-1`` this is no longer what makes a
        stored answer correct -- that is
        ``fk_merchant_rules_merchant_account``, graded by
        :meth:`TestTheTableRefusesWhatIsNotAnAnswer
        .test_ANOTHER_ACCOUNTS_merchant_is_unwritable` -- so what it buys is a
        sentence rather than an ``IntegrityError`` and a logged traceback for
        what is ordinarily a stale page.  The whole submission is refused,
        because the section renders only this account's merchants and a
        statement about another cannot have come from the screen.

        **The id is ANOTHER ACCOUNT'S real merchant**, not an invented number:
        an id no row carries would also be refused by the foreign key, and the
        case that matters is the one a crafted request would actually try.
        """
        theirs = a_merchant(
            seed_second_user, "Nowhere Ltd",
            account=seed_second_user["account"],
        )
        db.session.flush()

        with pytest.raises(ValidationError) as caught:
            _refuse_unknown_merchants(
                (RuleSubmission(theirs.id, RuleAnswer.NEVER),),
                account_merchants(seed_user["account"].id),
            )

        assert "not ones your bank has shown" in str(caught.value)

    def test_a_merchant_this_account_HAS_seen_is_admitted(
        self, app, db, seed_user,
    ):
        """The other side of the scope check, so the refusal is not vacuous."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()

        _refuse_unknown_merchants(
            (a_statement(seed_user, "Amazon", RuleAnswer.NEVER),),
            account_merchants(seed_user["account"].id),
        )

    def test_a_merchant_from_a_line_that_is_already_MATCHED_is_admitted(
        self, app, db, seed_user,
    ):
        """The scope is every merchant SEEN, not the ones still outstanding.

        A merchant whose every line is explained today is still one the owner
        may want to answer for -- the next statement brings more of it -- and a
        scope narrowed to the leftovers would refuse an answer the section
        itself renders.  The line staged here is matched by nothing, which is
        the point: what puts a merchant in scope is that it EXISTS, and no
        reader has to remember to widen past this pass's leftovers.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()

        assert sorted(
            account_merchants(seed_user["account"].id).values(),
        ) == ["Amazon"]


class TestWhatARuleMayNAME:
    """The option list, and that it is the same set the door checks against."""

    def test_it_offers_only_purchase_tracking_expense_definitions(
        self, app, db, seed_user,
    ):
        """One set, read by the control that renders and the door that writes.

        A template this does not return cannot be reached by crafting a
        request, and one it does return cannot be refused by the write door --
        the property ``destinations_for`` rests on, applied one tier up.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_transaction(seed_user, name="Electricity", is_envelope=False)
        a_transaction(
            seed_user, name="Paycheck", income=True, is_envelope=True,
        )

        offered = offerable_templates(seed_user["account"].id)

        assert offered == {envelope.template_id: "Groceries"}

    def test_a_template_on_another_account_is_not_offered(
        self, app, db, seed_user, seed_second_user,
    ):
        """A statement is one bank's record of ONE account."""
        a_transaction(seed_second_user, name="Groceries", is_envelope=True)
        db.session.flush()

        assert offerable_templates(seed_user["account"].id) == {}


class TestRestatingOneCOLUMNIsStillAChange:
    """`_same_answer` compares three columns, and two graded nothing."""

    def _record(self, db, seed_user, merchant, **columns):
        """Stage a line for *merchant* and a rule row carrying *columns*."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant=merchant)
        a_rule(seed_user, merchant, **columns)
        db.session.flush()

    def test_changing_only_the_NAME_is_written(self, app, db, seed_user):
        """Delete the ``envelope_name`` term and this restatement is dropped.

        The owner renames the envelope a merchant should get, the receipt says
        nothing changed, and the next statement still creates it under the old
        name.
        """
        category = seed_user["categories"]["Groceries"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's",
            category_id=category.id,
        )

        recorded = state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="Yard & Garden",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        assert rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )[a_merchant(seed_user, "Lowe's").id].envelope_name == "Yard & Garden"

    def test_changing_only_the_CATEGORY_is_written(self, app, db, seed_user):
        """Delete the ``category_id`` term and this restatement is dropped.

        The envelope keeps being created under the category the owner moved it
        away from, which is what every spending report groups by.
        """
        was = seed_user["categories"]["Groceries"]
        now = seed_user["categories"]["Rent"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's", category_id=was.id,
        )

        recorded = state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="Lowe's", category_id=now.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        assert rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )[a_merchant(seed_user, "Lowe's").id].category_id == now.id

    def test_a_NEW_answer_is_stored_TRIMMED(self, app, db, seed_user):
        """The write-path trim, which the no-op case above cannot observe.

        That case is decided by the unchanged check, which trims before it
        compares; this is the first statement, where there is nothing to
        compare against.  Stored untrimmed, the name differs from what the
        database CHECK reads (``btrim``) and from what the owner typed next
        time, so every later Save rewrites the row.
        """
        category = seed_user["categories"]["Groceries"]
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Lowe's")
        db.session.flush()

        state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="  Lowe's  ",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )[a_merchant(seed_user, "Lowe's").id].envelope_name == "Lowe's"

    def test_a_name_differing_only_by_WHITESPACE_is_not_a_change(
        self, app, db, seed_user,
    ):
        """`_trimmed` had no control, and the database CHECK compares btrim.

        Without it the section rewrites that row on every Save, and the stored
        name drifts from the one the CHECK would accept.
        """
        category = seed_user["categories"]["Groceries"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's",
            category_id=category.id,
        )

        recorded = state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="  Lowe's  ",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1


class TestOneSubmISSIONNamingOneMerchantTwice:
    """The crafted shape whose blast radius is the whole pass."""

    def test_the_second_statement_RESTATES_rather_than_inserting(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for keeping ``stored`` in step.

        Without it both statements take the insert branch,
        ``uq_merchant_rules_account_merchant`` raises an
        ``IntegrityError`` -- which is not a designed refusal, so it escapes
        the per-item savepoint into the route's database arm and rolls back
        every answer that had already landed.  Unreachable from the rendered
        form, which emits one row per merchant; reachable by a crafted body.
        """
        first = a_transaction(seed_user, name="Groceries", is_envelope=True)
        second = a_transaction(seed_user, name="Gas", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Amazon")
        db.session.flush()

        recorded = state_rules(
            (
                a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                                template_id=first.template_id),
                a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                                template_id=second.template_id),
            ),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert recorded.refused == ()
        rows = db.session.query(MerchantRule).all()
        assert len(rows) == 1
        # The LAST statement wins, which is what restating means.
        assert rows[0].template_id == second.template_id
