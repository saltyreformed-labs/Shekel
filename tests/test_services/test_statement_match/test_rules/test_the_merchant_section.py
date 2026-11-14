"""What the screen reads: the section and directory, a line dated made after it posted, the
merchant scope and read filters, and the control's stored answer.
"""

from datetime import timedelta
from decimal import Decimal

from app.models.merchant_rule import MerchantRule
from app.models.statement_import import BankStatementLine
from app.models.transaction_template import TransactionTemplate
from app.services.statement_import import delete_import
from app.services.statement_match import (
    account_merchants,
    merchant_directory,
    review_set,
    RuleAnswer,
    RuleView,
    StandingRule,
    state_rules,
)
from app.services.statement_match._bars import CreationBars
from app.services.statement_match._section import merchant_summary
from app.services.statement_match._rules import _named_templates, rules_for

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_rule,
    a_scope,
    a_statement,
    a_transaction,
    the_merchant_id,
    an_import,
)


class TestTheSectionTheScreenRenders:
    """What each rule control lists, and what the inbox's rows count.

    **The membership rule changed at plan step ``bank_import:X-gf-2``** (ruling
    **bank_import:R-GX**): one control was every merchant with pending work
    PLUS every merchant answered for, and it became two -- the inbox asked
    about the merchants with no answer, and the register showed the answers.

    **Plan step ``bank_import:X-gi-3`` deleted the register's half**, and with
    it the PARTITION these cases graded: the MERCHANTS surface
    (:func:`~app.services.statement_match.merchant_directory`, ruling
    **bank_import:R-IC**) lists every merchant the account has seen, answered
    or not, so *reaches exactly one of the two* is no longer a property anyone
    holds.  What survives is the half that is still a rule -- the inbox asks
    only where there is work AND no answer -- plus the reach that mattered: an
    answer whose lines are all explained is still editable, which is the case
    below.
    """

    def test_the_queue_lists_only_the_merchants_with_no_answer(
        self, app, db, seed_user,
    ):
        """A decision already made is not a question the queue asks.

        Measured on the developer's own data 2026-08-27: this control was 30
        rows of which 29 were answers he had already given, and 225,472 bytes
        of a 578,523-byte page.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Walmart",
            sequence_in_group=1,
        )
        # Answered for, and its line is still waiting: the answer is a
        # decision made, so the row is the register's whatever is pending.
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [
            row.summary.merchant for row in review.merchants.merchants
        ] == ["Walmart"]

    def test_the_directory_lists_an_answer_whose_lines_are_all_explained(
        self, app, db, seed_user,
    ):
        """This is what makes an answer changeable once its work is done.

        Without it a rule could only be changed while there was still an
        unexplained line for that merchant.

        **It asked the REGISTER until plan step ``bank_import:X-gi-3``**, whose
        membership was the answers themselves.  The register's model went with
        the screen ``bank_import:X-gi-2`` deleted, and the reach survives
        because :func:`~app.services.statement_match.account_merchants` IS that
        union: an answered ``Merchant`` row outlives its lines, and deleting an
        import sweeps only the merchants no line names AND no answer is about.
        So a merchant with no line at all is in the directory exactly as one
        with ten is, and this case is what says so.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-30.00", merchant="Walmart")
        a_rule(seed_user, "Old Merchant", template_id=envelope.template_id)
        db.session.commit()

        listed = {
            entry.summary.merchant: entry.summary
            for entry in merchant_directory(
                seed_user["user"].id, seed_user["account"].id, {},
            ).entries
        }

        assert "Old Merchant" in listed, (
            "an answer whose merchant has no line left is out of reach"
        )
        assert listed["Old Merchant"].rule is not None

    def test_a_queue_row_carries_how_many_lines_and_how_much_money(
        self, app, db, seed_user,
    ):
        """The queue decides several lines at once, so it says how much.

        On the developer's own statement one row of it covers `-$7,412.94`.
        **A surface with no pass carries no such figure and must not**: it has
        not measured one -- see
        ``TestASurfaceWithNoPassStatesNoFigureItHasNotMeasured``, which this
        step renamed from ``TestTheRegisterStatesNoFigureItHasNotMeasured``
        when the register went.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Amazon",
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        row = review.merchants.merchants[0]
        assert row.summary.merchant == "Amazon"
        assert row.line_count == 2
        assert row.total == Decimal("-55.00")

    def test_a_NEVER_line_leaves_the_creatable_list_ENTIRELY(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**, end to end through the reader.

        The caption cannot promise a number the control does not deliver -- and
        since plan step ``bank_import:X-ga`` it cannot render a control either.
        A merchant answered *never a purchase* is not one line of the create
        card with its select disabled: it is not in that card at all, and it is
        listed among the pass's ``answered_never`` lines instead -- which is
        where ruling **bank_import:R-JH** put it at plan step
        ``bank_import:X-gj-4c``, having been in ``parked`` before that.  Both
        lists are out of ``creatable``, which is the fact this case grades:
        what changed is where such a line RENDERS, never whether the create
        door would take it.

        The other half of the old sweep rule -- a rule that does not reach
        this line's pay period places nothing either -- is graded by
        ``test_what_a_rule_places.py``'s ``TestWhatARuleResolvesTo``'s two
        UNRESOLVED cases, which is where that partition lives.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Capital One",
            sequence_in_group=1,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        a_rule(seed_user, "Capital One")
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [item.line.merchant for item in review.creatable] == ["Amazon"]
        assert [
            item.line.merchant for item in review.answered_never
        ] == ["Capital One"]
        # **And in NEITHER of the other two**, which is what makes the line
        # above a partition claim rather than a membership one: a line counted
        # twice would put it on a holding tab AND in the inbox.
        assert review.parked == ()
        # ...and the ANSWER that parked it is still reachable, on the MERCHANTS
        # surface: both of these merchants have been answered for, so the inbox
        # asks about neither and the directory shows both (rulings
        # **bank_import:R-GX**, **R-IC**).  A parked line whose answer had no
        # row anywhere would be ruling R-GJ's own dead end -- an act refused
        # with the door that permits it hidden.
        #
        # *The sweep-per-evidence-group assertions that stood here went with
        # the queue at plan step ``bank_import:X-gi-3``; the live coupling they
        # were about -- no SWEPT card carries a sentence -- is
        # ``test_reconcile.test_the_tabs.TestOnlyTheInboxSweeps``, which stages a
        # non-empty swept set and a non-empty withheld set so it can fire.*
        assert review.merchants.merchants == ()
        assert {
            entry.summary.merchant for entry in merchant_directory(
                seed_user["user"].id, seed_user["account"].id, {},
            ).entries
        } == {"Amazon", "Capital One"}


class TestASurfaceWithNoPassStatesNoFigureItHasNotMeasured:
    """The directory runs no pass, so it carries no count of waiting lines.

    Plan step ``bank_import:X-gf-2``.  ``line_count`` and ``total`` used to sit
    on :class:`~app.services.statement_match.MerchantSummary`, which both
    surfaces share -- so the register would have carried a ``0`` for a merchant
    whose lines are waiting, and printed *none right now* over the developer's
    own ``Capital One``, whose *never a purchase* answer parks 9 lines worth
    `-$7,412.94`.  A figure that is FALSE rather than absent.

    The remedy is the composition: the pass's share is
    :class:`~app.services.statement_match.WaitingMerchant`'s and only the queue
    builds one, so the surface with no pass cannot state one.
    """

    def test_the_register_row_has_no_waiting_figure_to_state(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the fields are not reachable, not merely unset.

        An assertion that a count READ zero would pass against exactly the
        defect this exists to prevent, so what is asserted is that the value
        has no such attribute at all.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        row = merchant_directory(
            seed_user["user"].id, seed_user["account"].id, {},
        ).entries[0].summary

        assert row.merchant == "Amazon"
        assert not hasattr(row, "line_count")
        assert not hasattr(row, "total")

    def test_the_QUEUE_row_states_it_because_the_queue_measured_it(
        self, app, db, seed_user,
    ):
        """The other side, so the absence above is a boundary and not a hole."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.commit()

        row = review_set(a_scope(seed_user)).merchants.merchants[0]

        assert row.summary.merchant == "Amazon"
        assert row.line_count == 1
        assert row.total == Decimal("-25.00")


class TestALineDatedMadeAfterItPosted:
    """Finding **N-325**, ruled 2026-08-19: the ADD is withheld, and SAID.

    **The rule is unchanged and its PLACEMENT is what plan step
    ``bank_import:X-gm`` moved** (developer 2026-09-05).  The app still refuses
    to choose between the bank's two days; what it stopped doing is dropping
    the line out of every list to say so, which left it on no tab of the
    Reconcile page at all.
    """

    def test_it_is_offered_with_its_ADD_withheld(self, app, db, seed_user):
        """The line is on the page, and the control that cannot succeed is not.

        ``entry_service.create_entry`` refuses a purchase whose money left
        before it was spent -- correctly, because money cannot -- so a line the
        bank dates MADE after it POSTED has no day a purchase could happen on.
        The line is still WORK: it keeps MATCH, so it is in ``creatable``
        carrying the refusal and carrying NO placement, which is what a sweep
        would otherwise press.  0 of the developer's 378 recorded lines are
        this shape; the OFX adapter's own measurement found 2 of 361.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
            transaction_on=day + timedelta(days=1), merchant="Amazon",
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 1
        offered = review.creatable[0]
        assert offered.placement is None
        assert offered.withheld is not None
        # The sentence names BOTH of the bank's own days, because a refusal
        # the owner cannot check against their statement is not actionable.
        assert day.isoformat() in offered.withheld
        assert (day + timedelta(days=1)).isoformat() in offered.withheld
        # It is not a BOUND: nothing here was left unexamined.  *It asked
        # ``bounds.any_limit`` until plan step ``bank_import:X-gi-3`` deleted
        # that property with its last reader; these are the two bounds this
        # fixture could set, and they are the two the page states for itself
        # (``ReconcilePage.unexamined``).*
        assert review.bounds.crowded_days == ()
        assert review.bounds.unpriceable_count == 0
        assert review.bounds.before_calendar_count == 0
        assert review.bounds.books is None

    def test_a_line_dated_made_BEFORE_it_posted_keeps_its_placement(
        self, app, db, seed_user,
    ):
        """The firing control's other side, so the withholding is not vacuous."""
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
            transaction_on=day - timedelta(days=1), merchant="Amazon",
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 1
        assert review.creatable[0].withheld is None


class TestAMerchantOutLIVESTheLinesThatNamedIt:
    """A merchant with an answer stays answerable when its lines are gone.

    **This was the second half of a UNION and it is now the table** (plan step
    ``bank_import:X-gd-1``).  The scope used to be *every merchant this
    account's recorded lines name*, unioned with *every merchant already
    answered for*, and the second half existed because deleting an import took
    a merchant's lines and would otherwise have made its answer unwithdrawable.
    A merchant ROW survives its lines, so what these cases grade is that
    survival rather than a second clause someone has to remember to keep.
    """

    def test_a_merchant_stays_in_scope_when_its_IMPORT_is_deleted(
        self, app, db, seed_user,
    ):
        """The real event, through the real door.

        ``statement_import.delete_import`` is what removes an import and its
        lines (plan step ``bank_import:X-f6a-4``).  Before the merchant was a
        row, that deletion took the ONLY evidence the merchant had ever been
        seen, and the answer stated about it became unrestatable -- the door
        refuses a merchant outside the scope, and the whole submission with it.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()
        a_rule(seed_user, "Amazon")
        db.session.flush()

        delete_import(
            statement.id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.query(BankStatementLine).count() == 0
        held = account_merchants(seed_user["account"].id)
        assert sorted(held.values()) == ["Amazon"]
        # ...and the door still admits a restatement of the answer.
        state_rules(
            (a_statement(seed_user, "Amazon", RuleAnswer.NEVER),),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_ANOTHER_ACCOUNTS_merchant_is_not_in_this_ones_scope(
        self, app, db, seed_user, seed_second_user,
    ):
        """THE security-relevant filter, and it is one clause on one table.

        Delete ``Merchant.account_id == account_id`` from
        :func:`~app.services.statement_match.account_merchants` and every
        merchant on every account in the database widens this account's scope.
        The stored row is unwritable either way since
        ``fk_merchant_rules_merchant_account`` -- this is the reader's
        half, which is what decides whether the SCREEN offers the question.
        """
        theirs = an_import(
            seed_second_user, account=seed_second_user["account"],
        )
        a_bank_line(
            seed_second_user, theirs, amount="-9.99", merchant="Theirs Only",
        )
        db.session.flush()

        assert account_merchants(seed_user["account"].id) == {}


class TestEveryReadFilterHasAControlThatFiresOnItsOwn:
    """One guard per case, because a case that needs two to fail grades neither.

    **The owner/account pair below was measured to survive its own deletion**
    by an adversarial test-quality review on 2026-08-19: the suite stayed green
    with each filter removed one at a time, and the one case that looked like a
    control for two of them differed on both, so it fired only when both went.
    That sentence is scoped to that member on purpose -- it is a MEASUREMENT
    with a date, and the name-join case beside it was written on 2026-08-25 and
    is not covered by it.  A false provenance line is worse than none, because
    the next reader trusts it instead of re-measuring.

    The scope filters this class also used to cover are now
    :class:`TestAMerchantOutLIVESTheLinesThatNamedIt`'s, because the scope
    became one clause on one table.
    """

    def test_rules_are_read_for_THIS_OWNER_and_THIS_ACCOUNT(
        self, app, db, seed_user, seed_second_user,
    ):
        """Both filters on ``rules_for``, each observed on its own.

        The OWNER filter protects against a CALLER, not against a data state:
        ``fk_merchant_rules_owner`` already holds a row's owner equal to
        its account's, so no row with the wrong pair exists to be found.  What
        it refuses is a producer asked for one owner's answers with another
        owner's id -- which is why the case passes a mismatched pair rather
        than staging an impossible row.  A first version of this test staged
        the other owner's rule on the OTHER owner's account, where the
        account filter alone answers correctly, so it graded nothing.

        The ACCOUNT filter is the one a screen can hit: a Checking answer
        suggested on a card statement resolves to nothing at best, and the
        credit-card arc is about to give this owner a second account.
        """
        mine = a_transaction(seed_user, name="Groceries", is_envelope=True)
        a_rule(seed_user, "Amazon", template_id=mine.template_id)
        theirs = a_transaction(
            seed_second_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_second_user, "Theirs", template_id=theirs.template_id,
                 account=seed_second_user["account"])
        db.session.flush()

        # Asked for the WRONG owner of a real account: nothing.
        assert rules_for(
            seed_second_user["user"].id, seed_user["account"].id,
        ) == {}
        # Asked for the right owner and the WRONG account: nothing.
        assert rules_for(
            seed_user["user"].id, seed_second_user["account"].id,
        ) == {}
        # ...and the right pair finds exactly its own.
        assert sorted(
            rule.merchant for rule in rules_for(
                seed_user["user"].id, seed_user["account"].id,
            ).values()
        ) == ["Amazon"]

    def test_the_NAME_a_rule_carries_is_ITS_OWN_merchants(
        self, app, db, seed_user,
    ):
        """The join in ``rules_for``, which two answers can tell apart.

        The name travels with the answer so every sentence the door writes can
        print it without a second read.  Join on ``merchant_id`` alone and one
        account's two answers still resolve correctly; drop the join's
        ``account_id`` term and nothing observable changes here either, which
        is why the composite is stated for the reason the FOREIGN KEY is and
        not because a case could catch it.  What this DOES catch is the join
        pairing an answer with the wrong merchant's name.
        """
        a_rule(seed_user, "Amazon")
        a_rule(seed_user, "Walmart")
        db.session.flush()

        held = rules_for(seed_user["user"].id, seed_user["account"].id)
        by_id = {
            merchant_id: rule.merchant
            for merchant_id, rule in held.items()
        }
        assert by_id == {
            the_merchant_id(seed_user, "Amazon"): "Amazon",
            the_merchant_id(seed_user, "Walmart"): "Walmart",
        }


class TestAStoredAnswerThatStoppedBeingOfferable:
    """A template deactivated under a rule's feet (finding from review)."""

    def test_the_view_can_still_NAME_it(self, app, db, seed_user):
        """THE FIRING CONTROL for the whole ``stale_templates`` derivation.

        Without it the section has no option carrying the stored value, so the
        select shows and submits its FIRST -- *I have not said* -- and the next
        Save silently WITHDRAWS a rule the owner never touched.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        view = RuleView.build(
            seed_user["user"].id, seed_user["account"].id,
        )

        assert view.template_names == {}
        assert view.stale_templates == {envelope.template_id: "Groceries"}
        assert view.label_for(envelope.template_id) == "Groceries"

    def test_restating_it_UNCHANGED_is_a_no_op_rather_than_a_refusal(
        self, app, db, seed_user,
    ):
        """The order of the unchanged check is the fix, not a saving.

        The section renders the stale answer back, so Save submits it.
        Validating before comparing would refuse it -- reporting a refusal for
        a merchant the owner never touched, on a press about a different one.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Amazon")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        recorded = state_rules(
            (a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                             template_id=envelope.template_id),),
            seed_user["user"].id, seed_user["account"].id,
        )

        assert recorded.refused == ()
        assert recorded.stated == ()
        assert recorded.unchanged_count == 1
        assert db.session.query(MerchantRule).count() == 1


class TestAMerchantSectionOverMixedLines:
    """The NULL-merchant guard is reachable and was graded by nothing."""

    def test_a_NULL_merchant_line_contributes_no_row(
        self, app, db, seed_user,
    ):
        """Remove the guard and ``sorted()`` raises on a set holding ``None``.

        Both shapes in one account is the ordinary state of a second adapter:
        one source names merchants and the other does not.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant=None,
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 2
        assert [
            row.summary.merchant for row in review.merchants.merchants
        ] == ["Amazon"]

    def test_two_spellings_of_one_merchant_are_two_rules(
        self, app, db, seed_user,
    ):
        """The model's own load-bearing claim, which nothing graded.

        ``merchant_rules`` does not case-fold, on the ground that
        deciding two bank strings name one merchant is a guess.  That is a real
        consequence rather than a detail: the owner answers twice, and the
        screen has to show both rows so they can.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="amazon",
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [
            row.summary.merchant for row in review.merchants.merchants
        ] == ["Amazon", "amazon"]


class TestNamingAStoredTemplateIsScopedToTheAccount:
    """Finding **N-353**, closed here.

    ``_named_templates`` selected by id alone and the NAME it returns is
    rendered on this control.  Nothing reachable leaked -- a rule's template is
    held to its account by ``fk_merchant_rules_template_account`` -- but that
    is safety by derivation over an open set of future callers, and the caller
    already holds the account.
    """

    def test_ANOTHER_accounts_template_is_not_named(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope clause, shown withholding a name it would have returned.

        Asked at the function rather than through ``RuleView`` because a rule
        naming a foreign template is UNWRITABLE, so the leak is only reachable
        by a future caller -- which is exactly what the finding says, and
        exactly why the guard has to be local.
        """
        theirs = a_transaction(
            seed_second_user, name="Their Envelope", is_envelope=True,
        )
        db.session.flush()

        assert _named_templates(
            {theirs.template_id}, seed_user["account"].id,
        ) == {}

    def test_THIS_accounts_template_IS_named(
        self, app, db, seed_user,
    ):
        """The firing control: the clause narrows, it does not empty.

        Without it the case above would pass against a function that returned
        nothing at all -- and the name it returns is what a stale answer's
        option carries, so an empty answer here is a select that submits the
        wrong template.
        """
        mine = a_transaction(seed_user, name="Groceries", is_envelope=True)
        db.session.flush()

        assert _named_templates(
            {mine.template_id}, seed_user["account"].id,
        ) == {mine.template_id: "Groceries"}


class TestTheControlAlwaysCarriesTheAnswerItHOLDS:
    """A select with no option for its stored value submits its FIRST one.

    ``RuleView`` reads the offerable templates and the stale ones in two
    statements, so a template deleted between them is named by NEITHER: not
    offerable, and not in ``stale_templates``.  On every row the database can
    hold the two agree -- ``fk_merchant_rules_template_account`` keeps a rule's
    template on its account, and the hard-delete door cascades the rule away
    with it -- so the gap is the window between the two reads, which is why it
    is produced HERE and not through the screen.

    **Plan step ``bank_import:X-gd-2`` is what made it matter.**  The first
    option used to be *I have not said*, and falling onto it silently WITHDREW
    the rule; that option is no longer rendered for an answered merchant, so
    the first option is now a real recurring envelope and the silent outcome
    would be a rule RE-AIMED at a destination the owner never picked.
    """

    def test_a_template_named_by_NEITHER_read_still_gets_a_label(
        self, app, db, seed_user,
    ):
        """The totality property, asked of the row the screen renders.

        ``label_for`` is total by design; what is graded is that
        ``_merchant_summary`` ASKS it whenever the answer is not offerable,
        rather than only when the stale read happened to find a name.
        """
        merchant = a_merchant(seed_user, "Amazon")
        rule = StandingRule(
            merchant_id=merchant.id, merchant="Amazon",
            answer=RuleAnswer.TEMPLATE, template_id=9_999,
        )
        view = RuleView(
            rules={merchant.id: rule},
            template_names={},
            active_categories=frozenset(),
            stale_templates={},
            stale_categories={},
            placeable_templates=frozenset(),
            placed_periods={},
        )

        # THE SHARED ROW, because an answered merchant is where a stored
        # answer can be stale and the inbox asks about none (ruling
        # **bank_import:R-GX**).  *It went through ``answered_merchants``, the
        # register's control, until plan step ``bank_import:X-gi-3`` deleted
        # it; ``merchant_summary`` is the producer that control and the
        # MERCHANTS surface always shared, so the row graded is the same one.*
        row = merchant_summary(
            merchant.id, "Amazon", view,
            CreationBars(never=frozenset(), account_payments=frozenset()),
        )

        assert row.unofferable.template == "a recurring envelope"

    def test_an_OFFERABLE_template_gets_no_stale_label(
        self, app, db, seed_user,
    ):
        """The firing control: the label is for answers the list cannot show.

        Without it the case above would be satisfied by labelling every
        template answer, which would render a duplicate option beside the real
        one -- two options carrying the same value, one of them saying the
        envelope is no longer offered when it is.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        merchant = a_merchant(seed_user, "Amazon")
        rule = StandingRule(
            merchant_id=merchant.id, merchant="Amazon",
            answer=RuleAnswer.TEMPLATE, template_id=envelope.template_id,
        )
        view = RuleView(
            rules={merchant.id: rule},
            template_names={envelope.template_id: "Groceries"},
            active_categories=frozenset(),
            stale_templates={},
            stale_categories={},
            placeable_templates=frozenset(),
            placed_periods={},
        )

        row = merchant_summary(
            merchant.id, "Amazon", view,
            CreationBars(never=frozenset(), account_payments=frozenset()),
        )

        assert row.unofferable.template is None
