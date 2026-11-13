"""What the OPENED card discloses, and the rows its MATCH tab offers.

Plan step ``bank_import:X-gj-1b``; rulings **bank_import:R-HP**, **R-HR**,
**R-HS**, **R-HW** and **R-HX**.

**The subject is the boundary ruling R-HR draws.**  A card shows the decision;
the panel behind it holds every reason, every verb and every control -- so
these cases are about which act a verb performs for a line, which tab opens,
and which of the account's rows a line may be paired against.
"""

from decimal import Decimal

from app.exceptions import ValidationError

from datetime import timedelta

import pytest


# Pylint: ``shekel-private-module-import`` -- a test of a service's
# INTERNALS reaches for them by name, which is the convention this package's
# own test modules already keep (``test_bars``, ``test_candidates``,
# ``test_reconcile``).  The alternative is widening the package's public
# surface for the tests alone.
# pylint: disable=shekel-private-module-import
from app.services.statement_match._card_sections import to_explain_sections
from app.services.statement_match._cards import parked_card
from app.services.statement_match._panel import (
    AddAct,
    MatchCandidates,
)
from app.services.statement_match._placement import placements_for
from app.services.statement_match._reads import review_set
from app.services.statement_match._rules import RuleAnswer, RuleView
from app.services.statement_match._stating import (
    rule_creating,
    rule_naming,
    state_rules,
)
from app.services.statement_match._skipping import skip_line
from app.services.statement_match._verbs import Verb

from ._builders import (
    a_bank_line,
    a_later_period,
    a_one_off_envelope,
    a_rule,
    a_merchant,
    a_scope,
    a_transaction,
    a_purchase,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    the_merchant_id,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _cards(seed_user):
    """Return the inbox's cards for the seeded account, with their candidates.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        A ``(cards, candidates)`` pair -- the cards in section order, and the
        index they were built from, so a case can assert the two agree.
    """
    scope = a_scope(seed_user)
    review = review_set(scope)
    candidates = MatchCandidates.of(scope, review)
    sections = to_explain_sections(review)
    return (
        [card for section in sections for card in section.cards],
        candidates,
    )


class TestASectionRendersNewestFirst:
    """The locked direction's own rule, and it was not kept.

    ``docs/design/bank_import_audit.md``: *Within a section, newest first*.
    The pass hands its lines over ASCENDING by day, so every section rendered
    oldest first -- the owner's most recent swipes, the ones they can still
    remember, at the bottom of a 27-card list on their own data.
    """

    def test_the_most_recent_bank_day_is_the_first_card(
        self, app, db, seed_user,
    ):
        """Three swipes on three days, newest at the top."""
        an_envelope(seed_user)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        for offset, merchant in ((0, "Oldest Co"), (2, "Middle Co"),
                                 (5, "Newest Co")):
            a_bank_line(
                seed_user, statement, amount="-10.00",
                posted_on=day + timedelta(days=offset), merchant=merchant,
            )
        db.session.commit()

        cards, _ = _cards(seed_user)
        merchants = [card.line.merchant_label for card in cards]

        assert merchants[:3] == ["Newest Co", "Middle Co", "Oldest Co"], (
            f"the section is not newest first: {merchants[:3]}"
        )


class TestTheADDVerbSaysWHICHActItPerforms:
    """Ruling **R-HP** names ADD once and the app has TWO doors under it.

    A purchase is filed into a container the owner picks between; an income
    row is filed against nothing (ruling **bank_import:R-GW**).  They take
    different submissions and offer different controls, so the card states
    which -- and no template asks a bank line's SIGN, which would be a second
    spelling of ``_recordable_inflows``' own partition.
    """

    def test_an_outflow_the_create_door_would_take_records_a_PURCHASE(
        self, app, db, seed_user,
    ):
        """The ordinary swipe."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.panel.add.act is AddAct.PURCHASE
        assert card.panel.add.records_a_purchase
        assert not card.panel.add.records_income

    def test_an_unmatched_deposit_records_INCOME(self, app, db, seed_user):
        """Ruling **bank_import:R-GW**'s other direction."""
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.panel.add.act is AddAct.INCOME
        assert card.panel.add.records_income
        assert not card.panel.add.records_a_purchase
        # **Filed against NO container**, which is the whole difference.
        assert card.panel.add.destinations == ()
        assert card.panel.add.placement is None

    def test_the_two_predicates_PARTITION_the_enum(self):
        """Neither arm may be silently added to.

        The template renders one arm each with no ``else``, so a third act
        added without a pane would render NOTHING -- which is why the
        partition is graded here rather than trusted.
        """
        assert set(AddAct) == {AddAct.PURCHASE, AddAct.INCOME}

    def test_a_line_ruling_R_GJ_bars_has_no_ADD_ACT_AT_ALL(
        self, app, db, seed_user,
    ):
        """A bar is not a shortage of destinations.

        No answer lifts it (**R-GJ**), so there is nothing for the tab to
        offer beyond the refusal its own offer already carries -- and a value
        holding an empty ``AddTab`` would be a control one Jinja condition
        away from rendering.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        scope = a_scope(seed_user)
        review = review_set(scope)
        candidates = MatchCandidates.of(scope, review)
        # pylint: disable-next=shekel-private-module-import
        from app.services.statement_match._cards import parked_card
        card = parked_card(review, review.parked[0])

        assert card.panel.add is None
        assert not card.panel.offer_for(Verb.ADD).is_open


class TestEveryOpenVerbHasSomethingToOfferAndEveryCardOpensOnOne:
    """Ruling **R-HW**, read as an invariant rather than as a screen.

    The template renders a pane per verb and dispatches on what that verb's
    tab offers, so an OPEN ADD with no :class:`AddTab` would render a heading
    and no control -- the silent gap this pins.
    """

    def _a_mixed_inbox(self, seed_user, db):
        """Stage one outflow, one deposit and one barred payment."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            sequence=1, source_category=_CARD_PAYMENT,
        )
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="2573.42", sequence_in_group=2,
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()

    def test_an_OPEN_add_always_carries_the_tab_that_serves_it(
        self, app, db, seed_user,
    ):
        """The invariant the ADD pane's two arms rest on."""
        self._a_mixed_inbox(seed_user, db)

        for card in _cards(seed_user)[0]:
            if card.panel.offer_for(Verb.ADD).is_open:
                assert card.panel.add is not None, (
                    f"line {card.line.line_id} offers ADD and says nothing "
                    f"about what ADD would do"
                )

    def test_a_card_opens_on_a_verb_the_panel_actually_renders(
        self, app, db, seed_user,
    ):
        """``opens_on`` is total over the four, whatever the pass found."""
        self._a_mixed_inbox(seed_user, db)

        for card in _cards(seed_user)[0]:
            assert card.opens_on in {
                offer.verb for offer in card.panel.offers
            }

    def test_a_suggestion_opens_on_ITS_OWN_verb(self, app, db, seed_user):
        """Ruling **R-HS**'s pre-fill, read at the grain of the tab."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        db.session.commit()
        # A rule the owner stated, so the card suggests ADD.
        a_rule(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=seed_user["categories"]["Groceries"].id,
        )
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.suggested is Verb.ADD
        assert card.opens_on is Verb.ADD

    def test_a_card_with_NO_suggestion_opens_on_its_own_ADD_act(
        self, app, db, seed_user,
    ):
        """Ruling **R-HX** leaves most inbox cards with no suggestion.

        Measured 2026-08-29 on the developer's own account: 16 of his 18
        inbox cards read *Choose what this is*, and opening every one of them
        on MATCH would put the only act they have behind a tab.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.suggested is None
        assert card.opens_on is Verb.ADD

    def test_a_parked_line_opens_on_the_verb_that_EXPLAINS_it(
        self, app, db, seed_user,
    ):
        """A shut tab carrying its reason is a disclosure, not a control."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        scope = a_scope(seed_user)
        review = review_set(scope)
        # pylint: disable-next=shekel-private-module-import
        from app.services.statement_match._cards import parked_card
        card = parked_card(review, review.parked[0])

        assert card.opens_on is Verb.TRANSFER
        assert not card.panel.offer_for(Verb.TRANSFER).is_open
        assert card.panel.offer_for(Verb.TRANSFER).waiting_for


class TestTheMatchTabOffersThePeriodAndTheSearchReachesFurther:
    """The developer's ruling of 2026-08-30, and the measurement behind it.

    The PERIOD is what a card renders with the page, so the payroll group a
    card is usually about needs no round trip.  The SEARCH is over every
    unexplained row on the account, because a bound that cannot be widened is
    the cap finding **N-374** refused -- and all 9 of the developer's 9 card
    payments have payback rows their own period does NOT hold.
    """

    def _a_deposit_and_two_rows(self, seed_user, db):
        """Stage a payroll deposit beside the two rows that explain it."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
        )
        a_transaction(
            seed_user, name="Health Insurance Allowance", amount="100.00",
            income=True,
        )
        db.session.commit()
        return line

    def test_the_lines_own_period_holds_the_rows_that_explain_it(
        self, app, db, seed_user,
    ):
        """Finding **salary:N-391**'s own case, reachable in one click.

        Measured 2026-08-30 on the developer's own account: the 2026-03-26
        deposit of `$2,573.42` finds ``Health Insurance Allowance`` `$100.00`
        and ``Data Manager`` `$2,473.38` in its own period, a difference of
        `$0.04`.  *(Cited **N-239** until `balance:X-aw` retired that row and
        split its bank half off as **N-391**.)*

        **Asserted against the INDEX rather than against a card.**  The card
        carried its line's rows until plan step ``bank_import:X-gj-1b`` and
        rendered none of them -- the panel's list is lazy-loaded, so the
        fragment asks :class:`MatchCandidates` for itself -- so the field went
        and this asks the producer that still answers the question.
        """
        line = self._a_deposit_and_two_rows(seed_user, db)

        cards, candidates = _cards(seed_user)
        labels = {
            row.label for row in candidates.for_line(cards[0].line)
        }
        assert cards[0].line.line_id == line.id, (
            "the deposit is not the first card, so this asks about the wrong "
            "line"
        )

        assert "Data Manager" in labels
        assert "Health Insurance Allowance" in labels

    def test_the_search_reaches_a_row_the_period_does_not_hold(
        self, app, db, seed_user,
    ):
        """The half that keeps a card payment groupable.

        A row budgeted in a LATER period is outside the line's own window and
        is not on its card; the search finds it, because the alternative is
        the cap finding **N-374** refused -- *the row that explains a line may
        be number 51*.  Measured 2026-08-30 on the developer's own account:
        all 9 of his 9 card payments have payback rows their own period does
        not hold.

        **The second bank line is what makes the case real rather than
        arranged**: the pass offers only rows inside the span its statements
        cover (``_rows_the_bank_never_showed``), so a row in a period no
        statement reaches is a candidate NOWHERE -- and a case that skipped
        the line would have been asserting against that bound instead of
        against this one.
        """
        self._a_deposit_and_two_rows(seed_user, db)
        later = a_later_period(seed_user)
        # **A figure NO tier can pair with the row**, deliberately: an
        # exact-amount line would be PROPOSED against it, and a proposal
        # withholds both its line and its row from the pass's unexplained
        # sets -- so the case would have graded an empty pool rather than
        # this bound.  Found by running it.
        a_bank_line(
            seed_user, an_import(seed_user), amount="-2490.65",
            posted_on=later.start_date, sequence_in_group=1,
            description="ACH DEBIT CAPITAL ONE MOBILE PMT",
        )
        a_transaction(
            seed_user, name="Rogue Equipment Payback", amount="1958.87",
            period=later,
        )
        db.session.commit()

        cards, candidates = _cards(seed_user)
        deposit = next(
            card for card in cards if card.line.amount > 0
        )
        on_the_card = {
            row.label for row in candidates.for_line(deposit.line)
        }
        found = {row.label for row in candidates.matching("Rogue")}

        assert "Rogue Equipment Payback" not in on_the_card
        assert "Rogue Equipment Payback" in found

    def test_the_search_matches_a_FIGURE_as_readily_as_a_name(
        self, app, db, seed_user,
    ):
        """An owner knows a row by its money as often as by its name."""
        self._a_deposit_and_two_rows(seed_user, db)

        _, candidates = _cards(seed_user)

        assert {row.label for row in candidates.matching("2473.38")} == {
            "Data Manager",
        }

    def test_an_EMPTY_search_matches_nothing(self, app, db, seed_user):
        """An empty search is not a search; the caller renders the period."""
        self._a_deposit_and_two_rows(seed_user, db)

        _, candidates = _cards(seed_user)

        assert candidates.matching("") == ()
        assert candidates.matching("   ") == ()

    def test_the_index_answers_for_EVERY_line_the_page_cards(
        self, app, db, seed_user,
    ):
        """No card may be one the pane cannot price.

        **This asked a different question until plan step
        ``bank_import:X-gj-1b``**: whether each card's own copy of the row
        list matched the index it came from. That copy is gone -- the builders
        stated it and the templates rendered none of it -- and comparing a
        value to the producer it was assigned from could only ever fail if a
        BUILDER forgot to pass it, which the type system now settles because
        there is nothing to pass.

        What is worth pinning is the fact the pane depends on: every line the
        page renders a card for resolves in the index, so no card can open on
        a tab that cannot answer. That is the same property
        ``ReviewSet.card_subject`` keeps on the route side.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        self._a_deposit_and_two_rows(seed_user, db)

        cards, candidates = _cards(seed_user)

        assert cards
        for card in cards:
            assert candidates.for_line(card.line) is not None


class TestARuleNamesTheDestinationTheCardChose:
    """Plan step ``bank_import:X-gj-1b``: the ADD tab's *always* control.

    The rule is read back off the DESTINATION the same card submits, so the
    two can never name different budget lines -- and the mapping is TOTAL,
    which is what lets the control be offered on every purchase card rather
    than withheld on some.
    """

    def test_a_TEMPLATE_generated_row_is_named_by_its_template(
        self, app, db, seed_user,
    ):
        """The identity across pay periods ruling **R-GA** says a rule needs."""
        envelope = an_envelope(seed_user)
        db.session.commit()
        scope = a_scope(seed_user)
        destination = next(
            one for one in scope.destinations
            if one.transaction_id == envelope.id
        )
        assert destination.template_id is not None, (
            "this case needs a templated destination or it grades nothing"
        )

        rule = rule_naming(7, destination)

        assert rule.answer is RuleAnswer.TEMPLATE
        assert rule.template_id == destination.template_id

    def test_a_ONE_OFFS_envelope_is_named_by_its_DEFINITION(
        self, app, db, seed_user,
    ):
        """Leaf 7b-3 of balance:X-bi-7b (ruling R-BAL24): TEMPLATE, naming the definition.

        An envelope the owner made at the grid carries a RULE-LESS definition
        (7b-1).  From 7b-1 to 7b-2 the offered rule stayed NEW-ENVELOPE by
        name and category, because a TEMPLATE answer naming a definition that
        generates nothing resolved UNRESOLVED in every other paycheck; 7b-3
        makes such an answer PLACE the definition's row where a paycheck
        holds none, so the definition is the identity a rule names -- for a
        placed row as for a generated one.  **Through the real scan**, so
        this fires if ``rule_naming`` reverts to the ``recurs`` key.
        """
        envelope = a_one_off_envelope(seed_user, name="Home Improvement")
        scope = a_scope(seed_user)
        destination = next(
            one for one in scope.destinations
            if one.transaction_id == envelope.id
        )
        assert destination.template_id is not None, (
            "this case needs a one-off's DEFINITION-linked row or it grades "
            "nothing"
        )
        assert destination.is_placed is True

        rule = rule_naming(7, destination)

        assert rule.answer is RuleAnswer.TEMPLATE
        assert rule.template_id == destination.template_id
        assert rule.envelope_name is None

    def test_the_rule_RESOLVES_BACK_to_the_row_it_was_read_from(
        self, app, db, seed_user,
    ):
        """The round trip, which is what makes the mapping exact.

        A rule that named a DIFFERENT row than the purchase it was stated
        beside would file the next statement's spending somewhere the owner
        never chose.  This states the rule from a row and resolves it again
        through the very producer the screen reads.  **On a PLACED envelope
        since leaf 7b-3 of balance:X-bi-7b** -- the shape the app writes --
        where it was a legacy link-less row: such a row has no definition
        for a rule to name, so the round trip does not hold for it and the
        family's cutover is what gives it one.  The legacy case is graded in
        ``test_rules.test_what_a_rule_places``, where a new-envelope answer
        records into such a row of its name
        (``test_a_LEGACY_link_less_envelope_of_that_name_HERE_is_recorded_into``).
        """
        envelope = a_one_off_envelope(seed_user, name="Amazon")
        a_merchant(seed_user, "Amazon")
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Amazon")
        scope = a_scope(seed_user)
        destination = next(
            one for one in scope.destinations
            if one.transaction_id == envelope.id
        )

        state_rules(
            (rule_naming(merchant_id, destination),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.commit()

        view = RuleView.build(
            seed_user["user"].id, seed_user["account"].id,
        )
        placement = placements_for(
            merchant_id, view, list(scope.destinations),
        )

        assert placement.records_in
        assert placement.destination.transaction_id == envelope.id

    def test_the_new_envelope_arm_states_what_the_owner_TYPED(self):
        """The other half, for the arm where no row exists yet."""
        # pylint: disable-next=shekel-private-module-import
        from app.services.statement_match._creations import NewEnvelope

        rule = rule_creating(7, NewEnvelope(name="Lowe's", category_id=3))

        assert rule.answer is RuleAnswer.NEW_ENVELOPE
        assert rule.envelope_name == "Lowe's"
        assert rule.category_id == 3


class TestTheSKIPVerbIsLitAndShutWhereTheDoorWouldRefuse:
    """Plan step ``bank_import:X-gj-4b``, rulings **R-HW** and **R-JI**.

    The verb is OPEN now: ``budget.statement_line_skips`` and its doors
    shipped at ``X-gj-4a``, the tab a skip lands on at ``X-gj-4c-2``, and this
    step gave a route a way to call them.  What these cases hold is the ONE
    line class it stays shut for, and -- more importantly -- that the shut set
    is exactly the set the DOOR refuses.

    **The set is the subject, not the sentence.**  A test that only asserted
    the wording would pass over the defect this step was built to avoid:
    :attr:`~._bars.BarredLine.also_pays_an_account` answers *does this pay an
    account you hold* for the two BARRED lists only, and a line a tier has
    PROPOSED a match for is in neither -- it never meets
    :meth:`~._bars.CreationBars.bar_for` at all.  So a panel that read the
    barred value would render an OPEN skip over a door that refuses, which is
    ruling **R-GJ**'s `$7,412.94` shape one verb over.  The third case below
    is that line, driven through BOTH paths.
    """

    def _skip_offer(self, seed_user, line_id):
        """Return the SKIP offer the panel renders for one line.

        Args:
            seed_user: The seeded user bundle.
            line_id: The bank line whose card to find.

        Returns:
            Its :class:`~._verbs.VerbOffer` for :attr:`Verb.SKIP`.
        """
        cards, _ = _cards(seed_user)
        card = next(one for one in cards if one.line.line_id == line_id)
        return card.panel.offer_for(Verb.SKIP)

    def test_an_ordinary_unexplained_line_offers_SKIP(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the verb is lit, so an ordinary line may end on it.

        Without this the two cases below would pass against a build that shut
        SKIP on everything, which is the state this step replaced.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        offer = self._skip_offer(seed_user, line.id)

        assert offer.is_open
        assert offer.waiting_for is None

    def test_a_PARKED_card_payment_line_renders_SKIP_shut(
        self, app, db, seed_user,
    ):
        """Ruling **R-JI**, on a line the pass PARKS.

        **Its card is built from ``review.parked`` and not from the inbox**,
        which is ruling **R-HQ**: money a source says went to another account
        the owner holds is a STATE rather than a task, so it sits on the
        Transfers tab and :func:`~._card_sections.to_explain_sections` does
        not hold it.  A first draft of this case looked for it in the inbox
        and raised ``StopIteration``.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))
        parked = next(
            one for one in review.parked if one.line.line_id == line.id
        )
        offer = parked_card(review, parked).panel.offer_for(Verb.SKIP)

        assert not offer.is_open
        assert "another account you hold" in offer.waiting_for

    def test_a_card_payment_line_is_never_PROPOSED_at_all(
        self, app, db, seed_user,
    ):
        """Ruling **R-HQ** made structural, plan step ``bank_import:X-gm``.

        **The fixture is the one that used to produce a proposal**: an
        envelope holding a `$793.23` purchase named for the card, and a card
        payment of the same figure on the same days.  A tier paired them, so
        the line was in ``proposals`` and in neither barred list, and this case
        existed to prove the panel still shut SKIP for it through the
        pass-level merchant set.

        It cannot happen now.  :func:`~._undisposed.inbox_partition` takes the
        holding states out BEFORE the proposer is given anything, which is what
        makes *a holding state is not a task* a fact about the pass rather than
        a rule each surface keeps -- and is what lets the grid's badge count
        the same set the inbox renders.  So what this case grades is the
        absence: the pairing is not proposed, the line is parked, and the door
        still refuses SKIP in the words the card shows.

        *A matched line is out of the pass entirely, which is why an earlier
        draft citing :mod:`._bars`' seven Van Loan lines as the populated case
        was the WRONG SET -- four are already matched and three fall before the
        pay calendar opens.  Named by adversarial review 2026-09-04.*
        """
        envelope = an_envelope(seed_user)
        a_purchase(
            seed_user, envelope, amount="793.23",
            description="Capital One Credit Card",
        )
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        scope = a_scope(seed_user)
        review = review_set(scope)
        # **The premise, asserted on the ROW and by its FIGURE.**  "Not
        # proposed" is trivially true when there is nothing to propose
        # against, so this case is worthless unless the counterpart the tier
        # would have paired is demonstrably in the pass.  *A first version
        # wrote ``review.unmatched_rows or review.parked``, which
        # short-circuits on the parked list asserted three lines down and so
        # graded nothing about the row at all.*  Named by adversarial review
        # 2026-09-05.
        assert any(
            row.cash_amount == Decimal("-793.23")
            for row in review.unmatched_rows
        ), "the purchase row the tier would have paired is not in the pass"
        assert not any(
            one.lines[0].line_id == line.id for one in review.proposals
        ), "a holding state was offered to the proposer"
        assert any(
            one.line.line_id == line.id for one in review.parked
        ), "the card payment is not parked"

        # **Off the PARKED card, not the inbox.**  ``_skip_offer`` looks in
        # ``to_explain_sections``, which is where this line used to be found
        # because a proposal put it there; it is a holding state now, so
        # reading the inbox would raise ``StopIteration`` rather than fail.
        parked = next(
            one for one in review.parked if one.line.line_id == line.id
        )
        offer = parked_card(review, parked).panel.offer_for(Verb.SKIP)
        assert not offer.is_open, (
            "SKIP was offered on a line the door refuses, which is the shut "
            "tab over an open door ruling R-GJ closed for the ADD verb"
        )

        # The other path: the door itself, on the same line.
        with pytest.raises(ValidationError) as caught:
            skip_line(
                line.id, seed_user["user"].id, seed_user["account"].id,
            )
        assert offer.waiting_for == str(caught.value), (
            "the panel and the door state the same refusal in different "
            "words, which is the drift one shared constant exists to stop"
        )
