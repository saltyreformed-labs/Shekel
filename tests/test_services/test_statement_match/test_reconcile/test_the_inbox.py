"""What enters the inbox, what a line is owed, and an unanswered inflow left unfilled."""

from datetime import timedelta
from decimal import Decimal

from app.enums import StatusEnum
from app.services import statement_match
from app.services.statement_match import Tab
from app.services.statement_match._verbs import Verb

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_rule,
    a_scope,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import _CARD_PAYMENT, _page, _cards, _counts


class TestALineWithNoAvailableActNeverEntersTheInbox:
    """Ruling **R-HQ**, and the arithmetic that makes the inbox reachable.

    Measured 2026-08-29 on the developer's own account: 9 of his 27
    unexplained lines are card payments no screen in the app can resolve, and
    a queue that cannot empty is not a queue.
    """

    def test_a_card_payment_is_a_TRANSFER_and_not_inbox_work(
        self, app, db, seed_user,
    ):
        """It leaves To explain entirely, and lands where its state belongs."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        counts = _counts(_page(seed_user, Tab.TO_EXPLAIN))

        assert counts[Tab.TO_EXPLAIN] == 0
        assert counts[Tab.TRANSFERS] == 1
        assert _cards(_page(seed_user, Tab.TO_EXPLAIN)) == []

    def test_the_transfer_card_offers_NO_button(self, app, db, seed_user):
        """Ruling **R-HW**: a verb with no door renders no submitting control.

        The sentence opens on TRANSFER, so a card that decided this from the
        sentence alone would render a working-looking OK on every one of the
        developer's nine parked lines.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TRANSFERS))[0]

        assert card.suggested is Verb.TRANSFER
        assert card.offers_ok is False
        assert card.sweep_class is None

    def test_the_line_still_carries_the_BAR_s_own_sentence(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**'s bar is unchanged; only where it renders moved."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TRANSFERS))[0]

        assert card.panel.notes
        assert any("Capital One" in note for note in card.panel.notes)


class TestEverySentenceALineIsOwedIsRENDERED:
    """Ruling **R-HB**'s asymmetry, re-pinned on the CARD.

    Each of the three cards this page replaced composed its own evidence
    sentences in Jinja, so a line got whichever its own card knew about --
    which is how a PARKED line came to be the one kind that never printed its
    search gap.  Measured on the developer's own data 2026-08-28: his
    `-$1,000.44` line of 2026-06-01, 1 of 9 parked, carried its bar reason on
    the queue and its gap only on the workbench.

    **It graded the retired queue's rows until plan step
    ``bank_import:X-gi-3``**, and both adversarial reviews of that step
    measured what deleting it cost: `_cards.parked_card` builds
    ``notes=(barred.reason, gap)`` and NOTHING asserted the second term -- the
    string "held too many rows for the app to search them" appeared nowhere in
    the suite, so dropping ``gap`` from that tuple left it green.  The rule is
    the card's now, so the guard is.
    """

    @staticmethod
    def _a_crowded_day(seed_user):
        """Stage a day too crowded for the group search to run.

        Args:
            seed_user: The seeded user bundle.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )

    def test_a_PARKED_card_prints_its_search_gap_BESIDE_its_bar(
        self, app, db, seed_user,
    ):
        """Both sentences, on the card the parked line actually renders."""
        self._a_crowded_day(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TRANSFERS))[0]

        assert any(
            "payment to an account you hold" in note
            for note in card.panel.notes
        ), "the parked card lost its BAR sentence"
        assert any(
            "held too many rows for the app to search them" in note
            for note in card.panel.notes
        ), "the parked card prints its bar but not its search gap (R-HB)"

    def test_a_CREATABLE_card_states_that_gap_ONCE(
        self, app, db, seed_user,
    ):
        """``ruled`` has already folded the gap into ``warning``.

        The firing control for the arm above: asking again on THIS card would
        print the same words twice on the one mechanism whose value already
        carries them, and nothing else in the suite would notice.
        """
        self._a_crowded_day(seed_user)
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]

        printed = sum(
            note.count("held too many rows for the app to search them")
            for note in card.panel.notes
        )
        assert printed == 1, (
            f"the creatable card printed its search gap {printed} times"
        )


class TestEveryUnexplainedLineReachesExactlyOneCard:
    """Every unexplained line reaches exactly ONE card, and none reaches none.

    **The conservation invariant `_card_sections` states in prose**: the four
    source lists are disjoint and together cover ``ReviewSet.unmatched``.  The
    retired queue's ``TestEveryLineIsGroupedExactlyOnce`` was the only case
    asserting it, over a pass staging several mechanisms at once; every
    surviving case stages ONE line, and a partition cannot be graded one member
    at a time.

    **The failure it catches is one this codebase shipped**: finding **N-325**,
    a line that fell out of every list and rendered on no tab at all, in no
    count, with nothing able to notice.  Restored on the live producer at plan
    step ``bank_import:X-gi-3`` after adversarial review measured its absence.
    """

    def test_every_unmatched_line_reaches_exactly_one_card(
        self, app, db, seed_user,
    ):
        """Three mechanisms in one pass, so the sets can actually collide."""
        an_envelope(seed_user)
        # ...a line a rule can file (creatable).
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        # ...a line ruling R-GJ parks (a holding state, on Transfers).
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT, sequence=1,
        )
        # ...money coming IN, which is the recordable-inflow arm.
        an_unexplained_outflow(
            seed_user, merchant="Town Of Clayton", amount="2573.42",
            sequence=2,
        )
        db.session.commit()

        review = statement_match.review_set(a_scope(seed_user))
        drawn = [
            card.line.line_id
            for tab in (Tab.TO_EXPLAIN, Tab.TRANSFERS)
            for card in _cards(_page(seed_user, tab))
            if card.line is not None
        ]

        assert len(review.unmatched) == 3, (
            "the fixture staged fewer than three unexplained lines, so this "
            "grades one mechanism rather than the partition"
        )
        assert len(drawn) == len(set(drawn)), (
            f"a line renders a card on more than one tab: {drawn}"
        )
        assert set(drawn) == {line.line_id for line in review.unmatched}, (
            "a line the pass could not explain reaches no card at all, which "
            "is finding N-325's shape"
        )


class TestALineTheBankDatesImpossiblyIsSTILLInboxWork:
    """Plan step ``bank_import:X-gm``, finding **N-325**.

    **The class this step exists for, graded where it was invisible.**  Such a
    line reached NO CARD on this page at all: ``_creatable_lines`` dropped it
    into ``ReviewBounds.impossible_day_count`` before the split, so it was on
    no tab, in no count, and reachable only from the workbench plan step
    ``bank_import:X-gi-2`` deletes.  Its ADD really is refused, so what it gets
    is a card with the ADD shut -- ruling **R-JH**'s answer for a standing
    *never a purchase* answer, on the one other class whose door is shut and
    whose line is still unexplained.
    """

    @staticmethod
    def _an_impossible_line(seed_user):
        """Stage one line the bank dates made a day AFTER it took the money."""
        day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
        a_bank_line(
            seed_user, an_import(seed_user), amount="-31.41", posted_on=day,
            transaction_on=day + timedelta(days=1), merchant="Amazon",
        )

    def test_it_is_ON_the_inbox_and_counted_there(self, app, db, seed_user):
        """One card, on To explain, and the caption says one.

        The count and the cards are asserted together: a page that counted it
        and rendered nothing would be the caption-over-an-empty-tab shape, and
        one that rendered it uncounted would under-report the work.
        """
        an_envelope(seed_user)
        self._an_impossible_line(seed_user)
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert _counts(page)[Tab.TO_EXPLAIN] == 1
        assert page.hero.to_explain == 1
        assert len(_cards(page)) == 1

    def test_its_ADD_is_SHUT_and_its_MATCH_is_not(self, app, db, seed_user):
        """The card is work with one door closed, not a disposition.

        MATCH staying open is the whole reason this is inbox work rather than a
        holding state: the owner can still pair the line with a row they hold,
        which is the only act the app can honour for it.
        """
        an_envelope(seed_user)
        # **An unexplained row this line cannot be, so MATCH has a pool and no
        # proposal claims the line.**  A row of the line's own figure would be
        # matched by the proposer, and the card's ADD would then be shut by
        # ``ADD_SHUT_BY_A_PROPOSAL`` -- a green-looking case grading the wrong
        # refusal.  The premise is asserted below rather than assumed.
        a_purchase(seed_user, an_envelope(seed_user), amount="812.55")
        self._an_impossible_line(seed_user)
        db.session.commit()

        assert statement_match.review_set(a_scope(seed_user)).proposals == ()
        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]

        add = card.panel.offer_for(Verb.ADD)
        assert add.is_open is False
        assert "before it was spent" in add.waiting_for
        assert card.panel.offer_for(Verb.MATCH).is_open is True

    def test_it_is_on_NO_other_tab(self, app, db, seed_user):
        """Exactly one tab holds it, which is what *no card anywhere* was.

        Asserted over every tab rather than over the two holding ones: the
        defect this step closed was a line on NONE of them, so a case naming a
        subset would be a claim about that subset.
        """
        an_envelope(seed_user)
        self._an_impossible_line(seed_user)
        db.session.commit()

        counts = _counts(_page(seed_user, Tab.TO_EXPLAIN))

        assert counts[Tab.TO_EXPLAIN] == 1
        assert counts[Tab.TRANSFERS] == 0
        assert counts[Tab.SKIPPED] == 0
        assert counts[Tab.EXPLAINED] == 0
        assert counts[Tab.FILED_BY_RULES] == 0

    def test_the_page_reports_it_as_no_UNEXAMINED_bound(
        self, app, db, seed_user,
    ):
        """The bound is deleted, not reworded.

        A sentence in *what this page did not look at* would now be announcing
        work the page is showing on the tab beside it.
        """
        an_envelope(seed_user)
        self._an_impossible_line(seed_user)
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert page.unexamined == ()


class TestAStandingNeverAnswerIsNotADisposition:
    """Ruling **bank_import:R-JH**, plan step ``bank_import:X-gj-4c``.

    **It REPLACES** ``TestAStandingNeverAnswerIsAlreadyASkip``, and the
    replacement is what ``CLAUDE.md`` rule 5's one exception looks like: the
    developer confirmed the expected behaviour CHANGED.  That class asserted a
    *never a purchase* answer put its line on the SKIPPED tab in the past
    tense, on the argument that the standing answer WAS the disposition.  It
    is not one: *not a purchase* is not *explained by nothing* -- a paycheck is
    neither and a transfer to savings is neither -- so the answer shuts the ADD
    door and claims nothing about what the line IS.

    **The deleted arm was REACHABLE, and that is why this class exists rather
    than the deletion standing alone.**  A first draft of R-JH called it
    structurally memberless; the class it replaces was a PASSING test in this
    repository proving otherwise, on exactly the staging below.  So the
    deletion rests on the semantic argument and on nothing else, and what the
    old cases measured -- that such a line exists and lands somewhere -- is
    measured here against the destination the ruling gives it.
    """

    def _a_never_answered_swipe(self, seed_user, db):
        """Stage one outflow whose merchant the owner has answered NEVER for.

        **An ordinary swipe merchant and NOT a card payment**, which is the
        whole population of this class: a line whose source ALSO files it as
        paying an account the owner holds is a transfer whatever they said, and
        is graded by :class:`TestALineWithNoAvailableActNeverEntersTheInbox`.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            Nothing; the rows are staged and committed.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Foundation Donation", amount="-4.00",
        )
        a_rule(seed_user, "Foundation Donation")
        db.session.commit()

    def test_it_is_INBOX_WORK_and_on_neither_holding_tab(
        self, app, db, seed_user,
    ):
        """The line is unexplained, so the hero counts it and the tabs do not.

        **``Tab.SKIPPED`` at zero is the half that used to be one**, and
        ``Tab.TRANSFERS`` at zero is the half that must not become one: leaving
        the line in ``parked`` and merely dropping the tab arm would total that
        list on TRANSFERS, whose chip renders a COUNT and a MAGNITUDE -- a
        money figure over a line no source filed as a payment.
        """
        self._a_never_answered_swipe(seed_user, db)

        counts = _counts(_page(seed_user, Tab.TO_EXPLAIN))

        assert counts[Tab.TO_EXPLAIN] == 1
        assert counts[Tab.SKIPPED] == 0
        assert counts[Tab.TRANSFERS] == 0

    def test_the_TRANSFERS_chip_counts_and_SUMS_neither_of_these_lines(
        self, app, db, seed_user,
    ):
        """**A MIXED population, which is the only staging that can fail.**

        Rulings **R-JH** and **R-JI** both turn on the same fact: the Transfers
        chip's label carries a COUNT and a MAGNITUDE, so a line arriving there
        arrives under a rendered money figure.  A single-card-payment case
        cannot tell a correct sum from one that also added the swipe, because
        there is nothing else to add; this stages both and asserts the figure
        excludes the `$4.00`.
        """
        self._a_never_answered_swipe(seed_user, db)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)
        chip = next(
            one for one in page.chips if one.tab is Tab.TRANSFERS
        )

        assert chip.count == 1
        assert chip.amount == Decimal("793.23")
        assert _counts(page)[Tab.TO_EXPLAIN] == 1

    def test_it_reads_NOTHING_SUGGESTED_and_proposes_no_verb(
        self, app, db, seed_user,
    ):
        """The app has not worked out what this is, and says so.

        A card suggesting SKIP would be the app stating a disposition on the
        strength of an answer that claims nothing about the line -- which is
        the sentence ``_sentence.for_parked_never`` composed and this step
        deleted.
        """
        self._a_never_answered_swipe(seed_user, db)

        page = _page(seed_user, Tab.TO_EXPLAIN)
        section = page.sections[0]
        card = section.cards[0]
        said = " ".join(span.text or "" for span in card.sentence)

        assert section.section is statement_match._cards.Section.NOTHING
        assert card.suggested is None
        assert card.offers_ok is False
        assert Verb.SKIP.past not in said

    def test_ADD_is_shut_and_carries_the_owner_s_own_reason(
        self, app, db, seed_user,
    ):
        """R-GJ's bar is unchanged; only where its line renders moved.

        The reason is in the panel both ways ruling **R-JH** asks for: as the
        ADD verb's own refusal, and as a note the opened card prints without
        the reader having to find the tab.
        """
        self._a_never_answered_swipe(seed_user, db)

        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]
        add = card.panel.offer_for(Verb.ADD)

        assert add.is_open is False
        assert "is never a purchase" in add.waiting_for
        assert card.panel.add is None
        assert any(
            "is never a purchase" in note for note in card.panel.notes
        )

    def test_MATCH_stays_open_and_the_answer_door_is_offered(
        self, app, db, seed_user,
    ):
        """**Why R-HQ is not breached by putting this line in the inbox.**

        A line with no available act is a holding state; this one keeps MATCH,
        so it is work.  And unlike a card payment it has a door worth naming:
        the owner gave this answer and can give another, which
        :attr:`~app.services.statement_match._bars.BarredLine.answer_door`
        withholds for a bar no answer lifts.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        self._a_never_answered_swipe(seed_user, db)

        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]

        assert card.panel.offer_for(Verb.MATCH).is_open is True
        assert card.takes_ok is True
        assert card.panel.answer_door == (
            "Change what you have said about Foundation Donation"
        )


class TestAnUNANSWEREDInflowIsNeverPreFilled:
    """Ruling **bank_import:R-HX**, which bounds **R-HS**.

    Recording a deposit as uncategorized INCOME is one act, and being the ONLY
    act is not a justification: a merchant credit is a refund, and filing one
    as income is the wrong act.  On the developer's own account 16 of the 18
    inbox lines are deposits.

    **What R-HX bounded was an app that could not DEFEND a destination**, and
    plan step ``bank_import:X-gj-2a`` shipped the one it named -- so this class
    now grades the half that is unchanged: a deposit no standing rule answers
    for still asks.  Its sibling, ``test_the_pre_fill_and_pane.py``'s
    :class:`TestAnANSWEREDInflowIsPreFilledByItsRule`, grades the other half.
    """

    def test_a_deposit_asks_rather_than_proposing(self, app, db, seed_user):
        """Its card reads *Choose*, so nothing is one click from landing."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Dividend Earned", amount="0.15",
        )
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        assert cards[0].suggested is None
        assert cards[0].offers_ok is False
        assert cards[0].sentence[0].text == "Choose"

    def test_it_is_still_ADDable_from_the_panel(self, app, db, seed_user):
        """Asking is not refusing: the door exists and the panel offers it."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Dividend Earned", amount="0.15",
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]

        assert card.panel.offer_for(Verb.ADD).is_open
