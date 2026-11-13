"""What a rule's answer places for a line: the resolver's arms, a new envelope's reuse of one of
that name, and always-ask placing nothing.
"""

from app.services.statement_match import (
    PlacementKind,
    review_set,
    RuleAnswer,
    RuleView,
    StandingRule,
)
from app.services.statement_match._placement import placements_for
from app.services.statement_match._rules import active_category_names

from tests._test_helpers import generate_row_of, moved_by_the_owner
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_merchant,
    a_one_off_envelope,
    a_rule,
    a_scope,
    a_transaction,
    an_import,
)

from ._helpers import _MERCHANT, _view, _destination


class TestWhatARuleResolvesTo:
    """The four answers a rule comes to for one line, and their reasons."""

    def test_a_TEMPLATE_answer_finds_that_periods_own_row(
        self, app, db, seed_user,
    ):
        """The whole point of keying on a template rather than a row.

        An envelope belongs to ONE pay period -- the 24 unexplained Amazon
        lines on the developer's own statement fall in ten of them -- so the
        answer has to resolve per period, and the template is what does it.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon", answer=RuleAnswer.TEMPLATE,
            template_id=envelope.template_id,
        )

        placement = placements_for(
            _MERCHANT,
            _view(rule, templates={envelope.template_id: "Groceries"}),
            [_destination(envelope)],
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == envelope.id
        assert placement.select_value == str(envelope.id)

    def test_a_TEMPLATE_answer_with_NO_row_here_says_so(
        self, app, db, seed_user,
    ):
        """Reported, never substituted for.

        Measured on the developer's own clone: template 5 (``Gas``) is
        offerable in 9 of the 11 pay periods their creatable lines fall in, and
        template 38 (``Groceries``) in 10 -- so this is the ordinary state of a
        real statement rather than an edge.  The tempting substitution, falling
        back to a new envelope, would file money somewhere the owner never
        named.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon", answer=RuleAnswer.TEMPLATE,
            template_id=envelope.template_id,
        )

        placement = placements_for(
            _MERCHANT,
            _view(rule, templates={envelope.template_id: "Groceries"}), [],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "Groceries" in placement.unresolved_reason
        assert placement.select_value is None

    def test_a_TEMPLATE_answer_with_TWO_rows_here_refuses_to_guess(
        self, app, db, seed_user, seed_periods,
    ):
        """A template does NOT always make exactly one row in a period.

        Measured on a 2026-08-18 production clone: template 22 (``Kayla's
        Spending Money``) generated two rows in pay period 3, ids 2388 and
        2389.  Picking either would file money in a row the owner did not pick,
        so the placement says which pay period holds two and stops.
        """
        first = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
            period=seed_periods[0],
        )
        # Two rows from ONE template in ONE period, which is what production
        # holds: an override row beside the generated one (2026-08-18 clone,
        # transactions 2388 and 2389, both template 22 in pay period 3).  The
        # app produces that pair two ways -- a carried-forward leftover, or a
        # row the owner MOVED in from a neighbouring paycheck -- and this is
        # the second: the engine's own row of the next paycheck, moved here
        # by the two acts the move door performs (plan step balance:X-cf).
        # Hand-linking a second engine row to this template would give two
        # rows ONE occurrence, which the occurrence index refuses.
        second = moved_by_the_owner(
            generate_row_of(first.template, seed_periods[1]),
            into=seed_periods[0],
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon", answer=RuleAnswer.TEMPLATE,
            template_id=first.template_id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, templates={first.template_id: "Groceries"}),
            [_destination(first), _destination(second)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "2 of them" in placement.unresolved_reason

    def test_a_NEW_ENVELOPE_answer_carries_its_name_and_category(
        self, app, db, seed_user,
    ):
        """The arm that always resolves, because it depends on no existing row.

        On the developer's own data it is the only arm open to the 10 lines in
        a pay period whose every envelope closed at a fixed figure.
        """
        category = seed_user["categories"]["Groceries"]
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Lowe's", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories=frozenset({category.id})), [],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.name == "Lowe's"
        assert placement.new_envelope.category_id == category.id
        assert placement.select_value == "new"

    def test_a_NEW_ENVELOPE_answer_whose_CATEGORY_was_archived_says_so(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the active-category clause.

        ``_create._owned_category`` refuses an archived category, because the
        picker renders only active ones -- so a placement naming one would tick
        a line whose submission can never succeed, which is the shape finding
        **N-325** was closed for one field over and which this arc has now
        shipped four times.  Delete the clause and the placement comes back
        ``CREATE_NEW``, the sweep ticks it, and the item is refused a tier
        deeper with a sentence about the category not being the owner's.
        """
        category = seed_user["categories"]["Groceries"]
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Lowe's", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories=frozenset()), [],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "archived" in placement.unresolved_reason
        assert placement.select_value is None

    def test_the_active_category_set_is_the_pickers_own(
        self, app, db, seed_user,
    ):
        """The premise for the clause above, read from the database.

        The set is not an assumption about which ids are active: it is the
        same predicate ``category_service.list_active_categories`` renders by.
        """
        archived = seed_user["categories"]["Groceries"]
        archived.is_active = False
        db.session.flush()

        active = active_category_names(seed_user["user"].id)

        assert archived.id not in active
        assert seed_user["categories"]["Rent"].id in active

    def test_a_NEVER_answer_PLACES_NOTHING_because_it_is_a_bar(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**: *never a purchase* stopped being a suggestion.

        Capital One Credit Card is 9 of the developer's 91 unexplained outflows
        and `-$7,412.94` of the `-$11,336.36` in that list, and every one of
        them must never become a purchase -- the app holds that money as CC
        Payback rows already.  Until plan step ``bank_import:X-ga`` this answer
        produced a ``NOT_A_PURCHASE`` placement, which withheld a sweep value
        and printed a sentence -- beside a destination select that still
        offered every envelope in the period, and above a create door that read
        no rule at all.  One YTD pass recorded all nine through it.

        **A placement is a suggested DESTINATION, and for this answer there is
        none**, so the honest return is nothing: what the answer now produces
        is a :class:`~app.services.statement_match.CreationBar`, graded in
        ``test_bars``.  This case pins that the placement seam no longer speaks
        for it -- a second statement of a refusal is what the last one cost.

        It is a FIRING CONTROL for the arm that returns early: delete it and
        this stored answer falls through into ``_template_placement`` with a
        ``NULL`` template id, which names every offerable row in the period.
        """
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Capital One Credit Card", answer=RuleAnswer.NEVER,
        )

        assert placements_for(
            _MERCHANT, _view(rule), [],
        ) is None

    def test_a_stored_NEVER_does_not_fall_through_to_a_TEMPLATE_placement(
        self, app, db, seed_user,
    ):
        """The same arm, shown against a period that HAS offerable rows.

        The case above passes an empty offer set, so an early return and a
        fall-through are indistinguishable in it: ``_template_placement`` would
        find no match either way.  This one hands the resolver a real
        destination, which a fall-through WOULD name -- and one whose
        ``template_id`` is ``None``, exactly what a ``NULL`` rule template
        would compare equal to.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True, template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Capital One Credit Card", answer=RuleAnswer.NEVER,
        )

        assert placements_for(
            _MERCHANT, _view(rule), [_destination(envelope)],
        ) is None

    def test_a_line_naming_NO_merchant_reaches_no_rule_at_all(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the nullable merchant column.

        A source that names no merchant records ``NULL``, and this is what that
        NULL buys: no rule can key on it.  Delete the ``merchant is None``
        arm and a second adapter's truncated descriptions -- SECU's own OFX
        cuts 326 of 361 to the same 32 characters -- would each key one rule
        and fire it on every merchant behind them.
        """
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon",
            answer=RuleAnswer.NEVER,
        )

        assert placements_for(None, _view(rule), []) is None


class TestANewEnvelopeAnswerReusesOneOfThatNameHere:
    """Finding **N-327**, developer ruling 2026-08-20 (plan step X-f6a-4).

    A ``new envelope called X`` answer used to mint unconditionally, so a
    rule fragmented its own budget line: measured on the developer's own
    statement, a ``Lowe's`` answer places 4 lines over 3 pay periods, so ONE
    press made 4 envelopes -- two of them in the same period -- and the next
    statement made more beside them, because an ad-hoc row carries no identity
    across periods for anything to converge on.

    **The suggestion is what changed, never the tick.**  The placement prints
    beside the line's own destination select, which still opens on *leave this
    line alone* (ruling **R-FZ**), so the owner sees the envelope it would
    reuse and may pick another -- including "a new envelope" again.
    """

    def test_a_LEGACY_link_less_envelope_of_that_name_HERE_is_recorded_into(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the cross-STATEMENT half of the convergence, on a legacy row.

        The envelope a previous statement created before plan step
        balance:X-bi-7b carries no definition, and until the family's cutover
        mints it one it is what the answer's first firing converges on
        (``PurchaseDestination.names_no_cadence``) -- a first build of leaf
        7b-3 keyed the convergence on ``is_placed`` and minted a second
        "Home Improvement" beside every legacy one (found by 7b-3's
        adversarial review; finding N-327's fragmentation back).  What such a
        row does NOT do is take the rule's NAME: the flip is keyed on
        ``is_placed``, graded in ``test_placing.py``.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}), [_destination(existing)],
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == existing.id
        assert placement.select_value == str(existing.id)

    def test_a_ONE_OFF_envelope_of_that_name_HERE_is_recorded_into(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the cross-STATEMENT half of the convergence.

        An envelope the owner made at the grid carries a rule-less DEFINITION
        (plan step balance:X-bi-7b), and a new-envelope answer's first firing
        converges on it rather than minting a second "Home Improvement"
        beside it in the same period -- finding N-327's fragmentation --
        after which the answer names that definition (leaf 7b-3, ruling
        R-BAL24).  Keyed on ``is_placed``, read off the REAL destinations scan
        here (``a_scope``), so this fires if the scan stops setting it or the
        placement reverts to the link or to ``recurs``.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )
        offered = [
            one for one in a_scope(seed_user).destinations
            if one.transaction_id == existing.id
        ]
        assert len(offered) == 1 and offered[0].template_id is not None

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}), offered,
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == existing.id

    def test_a_period_holding_NONE_of_that_name_still_creates(
        self, app, db, seed_user,
    ):
        """The arm the answer exists for, unchanged.

        Several of the developer's merchants -- a hardware store, a parks fee,
        two subscriptions -- have no envelope in ANY period, which is why
        ruling R-FX made creating one an answer at all.
        """
        category = seed_user["categories"]["Groceries"]
        other = a_transaction(
            seed_user, name="Groceries", is_envelope=True, template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}), [_destination(other)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.name == "Home Improvement"
        assert placement.select_value == "new"

    def test_TWO_of_that_name_here_is_reported_rather_than_guessed(
        self, app, db, seed_user,
    ):
        """The same rule, and the same sentence shape, a template answer takes.

        Reachable on data the defect itself produced -- two same-named
        envelopes in one period is exactly what one press used to make -- which
        is why it may not be papered over by picking the first.
        """
        category = seed_user["categories"]["Groceries"]
        one = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        two = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}),
            [_destination(one), _destination(two)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "already holds 2 of them" in placement.unresolved_reason
        assert placement.select_value is None

    def test_a_same_named_envelope_under_ANOTHER_category_is_NOT_reused(
        self, app, db, seed_user,
    ):
        """MONEY-ADJACENT FIRING CONTROL: a rule names a name AND a category.

        Reusing on the name alone would file this merchant's spending under a
        category the owner did not pick -- which is what every spending report
        groups by, and what the within-press registry already keys on.  A first
        draft of this arm compared the name alone, so the two halves of one
        rule disagreed; found by adversarial design review 2026-08-20.
        """
        answered = seed_user["categories"]["Groceries"]
        other = next(
            category for name, category in seed_user["categories"].items()
            if name != "Groceries"
        )
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False, category=other,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=answered.id,
        )

        placement = placements_for(
            _MERCHANT,
            _view(rule, categories={answered.id, other.id}),
            [_destination(existing)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.category_id == answered.id

    def test_a_same_named_TEMPLATE_row_is_NOT_reused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: naming a template is a DIFFERENT answer.

        It has its own resolution beside this one, including the "this pay
        period holds two of them, pick the one you mean" report that a
        recurring definition needs.  An owner who means the recurring envelope
        has that answer available and did not choose it, so converging onto it
        here would make the two answers indistinguishable in effect -- and
        would silently bypass the reporting.
        """
        category = seed_user["categories"]["Groceries"]
        generated = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
        )
        assert generated.template_id is not None
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}),
            [_destination(generated)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW

    def test_it_matches_the_NAME_and_not_the_label(
        self, app, db, seed_user,
    ):
        """The label appends the pay-period span for a reader.

        A rule comparing against the label would be comparing against the span
        too, so it would never match -- and the convergence would silently do
        nothing while every test above still passed if they compared labels.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        offered = _destination(existing)
        assert offered.label != offered.name

        placement = placements_for(
            _MERCHANT,
            _view(
                StandingRule(
                    merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
                    envelope_name="Home Improvement",
                    category_id=category.id,
                ),
                categories={category.id},
            ),
            [offered],
        )

        assert placement.kind is PlacementKind.RECORD_IN

    def test_an_ARCHIVED_category_still_refuses_before_any_of_this(
        self, app, db, seed_user,
    ):
        """The order of the refusals: an unusable answer resolves to nothing.

        Reusing an envelope of that name would look like a repair, and it would
        be applying an answer the owner can no longer restate.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories=frozenset()),
            [_destination(existing)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "archived" in placement.unresolved_reason


class TestTheScreenSaysWhichLineCREATESTheEnvelope:
    """Finding **N-327**: one press mints one envelope per answer per period.

    ``placements_for`` resolves ONE line against its own period and cannot know
    what another line in the same pass will do, so the flag that says *an
    earlier line here already creates this* is set by the reader -- the only
    thing that sees more than one line at a time.

    **It is about the SENTENCE, not the act.**  Both lines still carry the same
    select value (``new``), because the write converges; what this buys is that
    the screen says so BEFORE the press rather than the receipt saying it after.
    """

    def test_the_SECOND_line_of_one_answer_says_it_joins(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: without it both lines read as making their own."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        category = seed_user["categories"]["Groceries"]
        for amount in ("-30.00", "-45.00"):
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}", merchant="Lowe's",
            )
        a_rule(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=category.id,
        )

        review = review_set(a_scope(seed_user))

        joining = [
            line.placement.joins_new for line in review.creatable
            if line.placement is not None and line.placement.creates
        ]
        assert joining == [False, True]

    def test_lines_in_DIFFERENT_periods_join_one_definition(
        self, app, db, seed_user,
    ):
        """The key is the ANSWER: the second line joins the definition the first mints.

        **It asserted ``[False, False]`` until leaf 7b-3 of balance:X-bi-7b**
        (ruling R-BAL24): the key carried the period, so a line in another
        paycheck created its own envelope -- a second definition of one
        answer.  One press mints ONE rule-less definition per answer now and
        places a row of it per paycheck, so the later line joins.
        """
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        first_day = seed_user["bootstrap_period"].start_date
        later_day = a_later_period(seed_user).start_date
        for amount, day in (("-30.00", first_day), ("-45.00", later_day)):
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {day}", merchant="Lowe's",
            )
        a_rule(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=category.id,
        )

        review = review_set(a_scope(seed_user))

        joining = [
            line.placement.joins_new for line in review.creatable
            if line.placement is not None and line.placement.creates
        ]
        assert joining == [False, True]


class TestAlwaysAskPlacesNothing:
    """The fourth answer reaches the line's own control with no suggestion.

    It is the answer that means *I do not want a standing one*, so a placement
    would be the app answering a question the owner reserved for themselves.
    """

    def test_it_offers_no_placement(self, app, db, seed_user):
        """...and the line is still CREATABLE, which is the other half.

        *ask me every time* is not a bar: the line keeps its own destination
        select, exactly as an unanswered merchant's does.  A dispatch that
        treated the fourth answer like *never a purchase* would silently
        remove the very control this answer exists to send the owner back to.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-25.00", merchant="Amazon",
        )
        a_rule(seed_user, "Amazon", always_ask=True)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 1
        assert review.creatable[0].placement is None

    def test_it_does_not_fall_through_to_the_TEMPLATE_arm(
        self, app, db, seed_user,
    ):
        """The dispatch asked directly, past the screen that renders it.

        ``placements_for`` used to name the answers that place NOTHING and fall
        through to the template arm, so a fourth answer resolved as a template
        with a ``NULL`` id.  Asked here rather than only through
        ``review_set`` because a raise inside a read pass is a 500, and a 500
        is not a value this control could assert about.
        """
        merchant = a_merchant(seed_user, "Amazon")
        a_rule(seed_user, "Amazon", always_ask=True)
        db.session.flush()
        view = RuleView.build(
            seed_user["user"].id, seed_user["account"].id,
        )

        assert placements_for(merchant.id, view, []) is None
