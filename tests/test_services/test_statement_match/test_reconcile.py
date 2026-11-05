"""The RECONCILE page: which tab a line lands on, and what each tab counts.

Plan step ``bank_import:X-gj-1a``; rulings **bank_import:R-HP**, **R-HQ**,
**R-HW** and **R-HX**.

**The subject is the PARTITION, not the prose.**  The sentence a card carries
is :mod:`.test_verbs_and_sentence`'s, which needs no database; what needs one
is *which* card exists, *which* tab holds it and *what* each tab claims to
hold -- because those are facts about a real pass over real rows.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

# Pylint: ``shekel-private-module-import`` -- a test of a service's
# INTERNALS reaches for them by name, which is the convention this
# package's own test modules already keep (``test_bars``,
# ``test_candidates``, ``test_residual``).  The alternative is widening
# the package's public surface for the tests alone, which is the
# "surface nobody asked for" its ``__init__`` refuses in as many words.
# pylint: disable=shekel-private-module-import
from app.enums import StatementBalanceEvidenceEnum, StatusEnum
from app.models.statement_line_skip import StatementLineSkip
from app.services import bank_agreement, statement_match
from app.services.balance_at import BalanceContext
from app.services.statement_match import (
    OpenedAsk,
    RowKind,
    Tab,
    reconcile_page,
    skip_line,
)
from app.services.statement_match._cards import CardKind
from app.services.statement_match._sentence import for_skip
from app.services.statement_match._accepted_view import (
    REGISTER_LIMIT,
    accepted_counts,
    accepted_register,
)
from app.services.statement_match._verbs import Verb
from app.services.bank_agreement import AgreementDay, BankAgreement, ComparedSpan
from app.services.statement_import import RecordedRun
from app.services.statement_match._create import (
    MintedEnvelopes,
)
from app.services.statement_match._creations import PurchaseCreation
from app.services.statement_match._release import acts_of
from app.services.statement_match._preview import NOTHING_TICKED
from app.services.statement_match._scope import no_period_refusal
from app.services.statement_match._submission import ReviewedDifference
from tests.test_services.test_outstanding_difference import _opened_on, _settle
from tests.test_services.test_statement_import.test_anchor import _seed_import

from .test_reads_lineless import _planted_lineless

from ._builders import (
    a_bank_line,
    a_purchase,
    a_rule,
    a_scope,
    a_submission,
    a_transaction,
    an_answers,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_acts,
    filed_by,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads: a
#: merchant a source files as paying an account the owner holds.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _page(seed_user, tab, agreement=None, limit=REGISTER_LIMIT):
    """Return the Reconcile page for one tab of the seeded account.

    Args:
        seed_user: The seeded user bundle.
        tab: Which :class:`~app.services.statement_match.Tab`.
        agreement: The bank agreement, or ``None`` -- which is the state of an
            account no import has anchored, and the one most cases are in.
        limit: How many SETTLED acts a settled tab may render, or ``None`` for
            the whole record.  **It defaults to the SHIPPED bound** rather than
            to ``None``, so a case that says nothing about it grades the page
            the route actually renders.

    Returns:
        The page.
    """
    return reconcile_page(a_scope(seed_user), agreement, tab, limit)


def _cards(page):
    """Return every card on a page, across its sections.

    Args:
        page: The page.

    Returns:
        The cards, in section order.
    """
    return [card for section in page.sections for card in section.cards]


def _counts(page):
    """Return the tab bar as a plain dict.

    Args:
        page: The page.

    Returns:
        A dict of :class:`~app.services.statement_match.Tab` to count.
    """
    return {count.tab: count.count for count in page.counts}


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
        :attr:`~._bars.BarredLine.answer_door` withholds for a bar no answer
        lifts.
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
    for still asks.  Its sibling
    :class:`TestAnANSWEREDInflowIsPreFilledByItsRule` grades the other half.
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


class TestTheTabsPartitionWhatTheAccountHolds:
    """Every tab's count is a claim, and the claims may not overlap."""

    def test_an_empty_tab_renders_no_section_at_all(
        self, app, db, seed_user,
    ):
        """A heading over no rows reads as work the owner has to do."""
        an_envelope(seed_user)
        db.session.commit()

        assert _page(seed_user, Tab.SKIPPED).sections == ()
        assert _page(seed_user, Tab.TRANSFERS).sections == ()

    def test_the_inbox_groups_by_what_SUGGESTED_the_verb(
        self, app, db, seed_user,
    ):
        """Ruling **R-HP** replaced **R-HB**'s visible evidence grouping.

        A swipe no rule reaches has nothing suggesting a verb for it, so it
        sits under *Nothing suggested* rather than under a heading claiming
        the app looked and found something.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert [section.section.heading for section in page.sections] == [
            "Nothing suggested",
        ]
        assert page.sections[0].count == 1


class TestASettledTabNeverPromisesMoreThanItRenders:
    """The bound is applied to the tab's OWN half, and it travels with it.

    :func:`~app.services.statement_match._accepted_view.accepted_register`
    bounds at :data:`REGISTER_LIMIT`.  A caller that took that bounded list
    and THEN dropped the other half would render fewer rows than its own tab
    caption claims -- with 60 hand acts and 10 rule acts, about 43 rows under
    a tab reading 60.  Narrowing before the bound is what makes the caption
    true, and carrying ``withheld`` is what keeps the truncation from being
    silent.
    """

    def test_narrowing_happens_BEFORE_the_bound_and_in_SQL(
        self, app, db, seed_user,
    ):
        """Each half is loaded and bounded on its own, and the other is not.

        Asserted over STAGED acts rather than an empty account, where every
        clause here is satisfied by zero: ``all([])`` is True twice and
        ``0 + 0 == 0``.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()
        owner, account = seed_user["user"].id, seed_user["account"].id

        by_hand = accepted_register(owner, account, applied_by_rule=False)
        by_rule = accepted_register(owner, account, applied_by_rule=True)
        both = accepted_register(owner, account)

        assert len(by_hand.shown) == 1
        assert len(by_rule.shown) == 1
        assert all(not act.applied_by_rule for act in by_hand.shown)
        assert all(act.applied_by_rule for act in by_rule.shown)
        assert len(both.shown) == 2
        # The half nobody asked for is never LOADED, let alone priced.
        assert len(acts_of(owner, account, applied_by_rule=True)) == 1
        assert len(acts_of(owner, account)) == 2

    def test_the_counts_the_tab_bar_reads_are_the_same_partition(
        self, app, db, seed_user,
    ):
        """The FILTER aggregate, over a non-zero set, scoped to one owner."""
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()

        counts = accepted_counts(
            seed_user["user"].id, seed_user["account"].id,
        )

        assert (counts.total, counts.by_rule, counts.by_hand) == (2, 1, 1)

    def test_it_counts_no_other_owner_s_acts(
        self, app, db, seed_user, seed_second_user,
    ):
        """Scoped by owner AND account, which is what the write door narrows by."""
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        db.session.commit()

        theirs = accepted_counts(
            seed_second_user["user"].id, seed_user["account"].id,
        )

        assert (theirs.total, theirs.by_rule) == (0, 0)

    def _an_act(self, seed_user, db, merchant, envelope, *, by_rule):
        """Record one bank line as a purchase, by hand or by a rule.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            merchant: The merchant whose line to record.
            envelope: The budget line to file it into.
            by_rule: Whether a STANDING RULE performed it (**R-GT**), which is
                the only fact that decides which of the two settled tabs holds
                it.

        Returns:
            The created purchase.
        """
        line = an_unexplained_outflow(
            seed_user, merchant=merchant, amount="-12.34",
            sequence=0 if by_rule else 1,
        )
        db.session.commit()
        return statement_match.create_purchase_from_line(
            PurchaseCreation(
                line_id=line.id, transaction_id=envelope.id,
            ),
            a_scope(seed_user),
            MintedEnvelopes.none_yet(),
            an_answers(seed_user),
            applied_by_rule=by_rule,
        )

    def test_each_settled_tab_renders_its_OWN_half_and_counts_it(
        self, app, db, seed_user,
    ):
        """The partition is real on both sides, not merely arithmetic.

        Two acts, one performed by a rule and one ticked by a person: each tab
        must render exactly one, and each tab's count must equal what it
        rendered.  On an account with no acts at all every assertion here is
        satisfied by zero, which is why the acts are staged.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()

        for tab, expected in (
            (Tab.EXPLAINED, False), (Tab.FILED_BY_RULES, True),
        ):
            page = _page(seed_user, tab)
            cards = _cards(page)
            rendered = sum(section.count for section in page.sections)
            withheld = sum(section.withheld for section in page.sections)

            assert len(cards) == 1, tab
            assert cards[0].act.applied_by_rule is expected, tab
            assert rendered + withheld == _counts(page)[tab], tab
            assert rendered <= REGISTER_LIMIT


class TestASettledActSaysWhichVerbItWas:
    """Ruling **R-HP**: MATCH is *a row the books already hold*, ADD is not.

    **A group match with a difference CREATES something and is still a
    MATCH.**  An unbalanced group mints ruling **R-FN**'s residual row and
    records it as a creation, so *did this act create anything* answers ADD
    for the payroll shape this whole arc exists for -- seven deposits and
    `$18,132.63` on the developer's own account.  Found by adversarial review
    2026-08-29.
    """

    def test_a_payroll_group_with_a_residual_is_MATCHED_not_added(
        self, app, db, seed_user,
    ):
        """It re-dates two rows that already existed; that is a match."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.43",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(
                scope, lines=[line], transactions=[salary, allowance],
                residual="0.05",
            ),
            scope,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.EXPLAINED))[0]
        said = " ".join(span.text or "" for span in card.sentence)

        assert card.act.created_every_row is False
        assert said.startswith(Verb.MATCH.past)
        assert not said.startswith(Verb.ADD.past)

    def test_a_purchase_the_act_created_is_ADDED(self, app, db, seed_user):
        """The mirror: nothing pre-existed, so the act recorded new spending."""
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        statement_match.create_purchase_from_line(
            PurchaseCreation(line_id=line.id, transaction_id=envelope.id),
            a_scope(seed_user),
            MintedEnvelopes.none_yet(),
            an_answers(seed_user),
            applied_by_rule=False,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.EXPLAINED))[0]
        said = " ".join(span.text or "" for span in card.sentence)

        assert card.act.created_every_row is True
        assert said.startswith(Verb.ADD.past)


class TestADoorThatWouldRefuseIsNeverOFFERED:
    """Ruling **R-HW**, on the direction that had no refusal at all.

    ``_one_creatable`` withheld only the PLACEMENT for a line no saved pay
    period covers, and left the line in ``creatable`` -- so a reader taking
    membership of that list as the create door's answer reported ADD as open
    on a line ``ReviewScope.period_holding`` refuses by name.  Found by
    adversarial review 2026-08-29 and reproduced on a swipe MADE before the
    calendar opens and POSTED after it.
    """

    def _a_swipe_made_before_the_calendar_opens(self, seed_user, db):
        """Stage a line the calendar covers on its posting day and not before.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            Nothing; the rows are staged and committed.
        """
        an_envelope(seed_user)
        opens = seed_user["bootstrap_period"].start_date
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-12.34",
            posted_on=opens,
            transaction_on=opens - timedelta(days=3),
            description="POINT OF SALE DEBIT (Walmart)", merchant="Walmart",
        )
        db.session.commit()

    def test_add_is_SHUT_and_says_why(self, app, db, seed_user):
        """The door's own sentence, before the press rather than after it."""
        self._a_swipe_made_before_the_calendar_opens(seed_user, db)

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        offer = cards[0].panel.offer_for(Verb.ADD)
        assert offer.is_open is False
        assert "No pay period covers" in offer.waiting_for

    def test_the_screen_and_the_DOOR_say_the_same_thing(
        self, app, db, seed_user,
    ):
        """One spelling: the withheld sentence IS the refusal's own words.

        A screen and a door with two wordings for one rule is this project's
        own root cause, and this rule had three before the step.
        """
        self._a_swipe_made_before_the_calendar_opens(seed_user, db)
        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]

        withheld = card.panel.offer_for(Verb.ADD).waiting_for
        door = no_period_refusal(card.line.happened_on, "this purchase")

        assert withheld == door


class TestOnlyTheInboxSweeps:
    """Ruling **R-FZ(c)**, and where it does NOT reach."""

    def _a_clean_line_and_a_WITHHELD_one(self, seed_user, db):
        """Stage one sweepable card and one the pass would not file.

        Both in ONE pass, so the assertion runs against a NON-EMPTY swept set
        rather than passing because nothing offered a click at all -- which is
        the guard ``test_queue``'s own version of this keeps, and the reason
        it keeps it.

        **The withheld card is one whose destination a proposal names WHOLE**
        (``_verdict._proposed_destinations``, ruling **R-FZ(d)**): an EMPTY
        Groceries envelope a `-500.00` line matches exactly, which the pass
        proposes as a whole-row match, and an Amazon line whose rule files
        into that same envelope.  Through plan step ``balance:X-bi-4a``'s
        first cut the envelope held a `$180.00` purchase and the line was
        `-180.00`: the row was offered at its un-dated purchase and proposed
        whole.  Under ruling **R-BAL81** a row holding purchases is worth
        ``0`` to the offer and never proposed whole -- its purchase is -- so
        that shape withholds nothing, and the empty envelope is the one
        destination a proposal can still claim.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            Nothing; the rows are staged and committed.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_bank_line(
            seed_user, an_import(seed_user), amount="-500.00", posted_on=day,
            description="POINT OF SALE DEBIT L340 KROGER", sequence_in_group=9,
        )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        clean = a_transaction(
            seed_user, name="Fuel", amount="200.00", is_envelope=True,
        )
        an_unexplained_outflow(
            seed_user, merchant="Shell", amount="-41.10", sequence=4,
        )
        a_rule(seed_user, "Shell", template_id=clean.template_id)
        db.session.commit()

    def test_the_inbox_OFFERS_a_sweep_and_it_reaches_a_card(
        self, app, db, seed_user,
    ):
        """The whole control, through the real producer rather than by hand.

        Without this, ``_sweeps`` returning ``()`` unconditionally passes the
        suite: every other sweep assertion in this module is ``== ()``.
        """
        self._a_clean_line_and_a_WITHHELD_one(seed_user, db)

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert page.sweeps, "a sweep must exist or this grades nothing"
        assert sum(sweep.count for sweep in page.sweeps) >= 1
        assert all(sweep.label for sweep in page.sweeps)

    def test_no_SWEPT_card_carries_a_sentence(self, app, db, seed_user):
        """The coupling, not the property in isolation.

        ``_queue`` kept this by giving sweeps to one evidence group; ruling
        **R-HP** replaced that grouping, so the guard has to be re-pinned
        against the producer that replaced it.
        """
        self._a_clean_line_and_a_WITHHELD_one(seed_user, db)

        page = _page(seed_user, Tab.TO_EXPLAIN)
        cards = _cards(page)
        swept = [card for card in cards if card.sweep_class is not None]
        withheld = [card for card in cards if card.panel.notes]

        assert swept, "a swept card must exist or this grades nothing"
        assert withheld, "a withheld card must exist or this grades nothing"
        # **The withheld card is one a sweep WOULD otherwise reach**, which is
        # what makes the emptiness below a SUPPRESSION rather than an absence:
        # without it a producer that swept nothing at all, or that withheld
        # this card for an unrelated reason, satisfies the assertion.  The
        # retired queue's own version carried this half
        # (``test_it_is_swept_by_NOTHING``); adversarial review 2026-09-06
        # found it missing here when that class was deleted.
        assert any(card.sweep_class is None for card in withheld), (
            "no withheld card is being kept out of a sweep, so the emptiness "
            "below is an absence rather than a suppression"
        )
        assert [card for card in swept if card.panel.notes] == []

    def test_the_sweep_COUNT_is_what_the_control_would_reach(
        self, app, db, seed_user,
    ):
        """A caption may not promise a number the click does not deliver.

        Counted in the service for exactly this reason: the review screen it
        replaces computed these in Jinja with ``selectattr | length``.
        """
        self._a_clean_line_and_a_WITHHELD_one(seed_user, db)

        page = _page(seed_user, Tab.TO_EXPLAIN)
        reachable = {}
        for card in _cards(page):
            if card.sweep_class is not None:
                reachable[card.sweep_class] = (
                    reachable.get(card.sweep_class, 0) + 1
                )

        assert {sweep.css_class: sweep.count for sweep in page.sweeps} == (
            reachable
        )

    def test_a_holding_tab_offers_no_sweep(self, app, db, seed_user):
        """There is no act to sweep a line that has none."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        assert _page(seed_user, Tab.TRANSFERS).sweeps == ()

    def test_a_settled_tab_offers_no_sweep(self, app, db, seed_user):
        """An undo destroys rows one at a time, under **R-GY**'s confirm."""
        an_envelope(seed_user)
        db.session.commit()

        assert _page(seed_user, Tab.EXPLAINED).sweeps == ()


class TestTheHeroReportsADayBOTHRecordsCanSpeakFor:
    """Plan step ``bank_import:X-gj-1a``'s reason for a new derivation.

    :attr:`~app.services.bank_agreement.BankAgreement.standing_gap` reads the
    span's LAST day whether or not an anchor reaches it, so a hero built on it
    prints a dash for an account whose statements simply stop before the span
    does -- which is :attr:`unpriced_days`, a state the report already
    measures and expects.
    """

    def _agreement(self, days, account_id=1, opened_on=None, last_record_on=None):
        """Return an agreement over hand-built days.

        Args:
            days: The :class:`~app.services.bank_agreement.AgreementDay`
                values, ascending.
            account_id: Which account it is about.  Stated rather than left at
                the seeded account's id by luck, because
                :func:`~app.services.statement_match.reconcile_page` refuses a
                pass and an agreement that name different accounts.
            opened_on: The day the books open, or ``None`` for the day before
                the first hand-built day -- which makes every day one the
                books hold movement on, as :meth:`_day` builds it.  A case
                building a day before the books open, or the opening day
                itself, passes the day it built them around.
            last_record_on: The books' last record, or ``None`` -- which every
                hand-built day being quiet makes the default.  A case about
                records running past the last statement (finding
                **BAL-651**) passes the day they run to.

        Returns:
            The :class:`~app.services.bank_agreement.BankAgreement`.

        **``runs`` is ONE run covering the whole hand-built span** (plan step
        ``balance:X-f3c-3`` added the coverage; ``balance:X-bj-1b`` made the
        run the one home of it).  It is deliberately not empty: an empty list
        means "no import covers any of these days", which is a real state and
        not the one this class is about -- every case here is about the HERO,
        and a hand-built agreement claiming its own days are unimported would
        be describing a different account than the one its ``days`` describe.
        The run anchors on nothing; the hand-built days carry their own bank
        balances, which is what the hero reads.
        """
        return BankAgreement(
            account_id=account_id,
            span=ComparedSpan(
                first_day=days[0].day, last_day=days[-1].day,
                recorded_from=days[0].day, recorded_through=days[-1].day,
            ),
            books_opened_on=(
                days[0].day - timedelta(days=1) if opened_on is None
                else opened_on
            ),
            last_record_on=last_record_on,
            runs=[RecordedRun(days[0].day, days[-1].day, None, ())],
            days=list(days),
        )

    def _day(self, day, bank, books):
        """Return one day AFTER the books open, quiet on both sides.

        Args:
            day: The civil day.
            bank: What the bank's record prices it at, or ``None``.
            books: What the app says the account held.

        Returns:
            The :class:`~app.services.bank_agreement.AgreementDay`, with both
            movements known and ``0.00``.
        """
        return AgreementDay(
            day=day, bank_lines=Decimal("0.00"), recorded=Decimal("0.00"),
            asserted=Decimal("0.00"), app_balance=Decimal(books),
            bank_balance=None if bank is None else Decimal(bank),
        )

    def _before_the_books(self, day, bank):
        """Return one day BEFORE the books open.

        The books state neither a movement nor a balance there (rulings
        **R-BAL286**, **R-BAL291**), so both are ``None``; the bank's side is
        whatever its record says.

        Args:
            day: The civil day.
            bank: What the bank's record prices it at, or ``None``.

        Returns:
            The :class:`~app.services.bank_agreement.AgreementDay`.
        """
        return AgreementDay(
            day=day, bank_lines=Decimal("0.00"), recorded=None,
            asserted=None, app_balance=None,
            bank_balance=None if bank is None else Decimal(bank),
        )

    def _opening(self, day, bank, books):
        """Return the day the books OPEN: a balance stated, no movement held.

        Args:
            day: The civil day.
            bank: What the bank's record prices it at, or ``None``.
            books: What the books state at the close of that day.

        Returns:
            The :class:`~app.services.bank_agreement.AgreementDay`.
        """
        return AgreementDay(
            day=day, bank_lines=Decimal("0.00"), recorded=None,
            asserted=None, app_balance=Decimal(books),
            bank_balance=None if bank is None else Decimal(bank),
        )

    def test_it_walks_BACK_past_a_day_the_bank_cannot_price(self):
        """The whole reason it is not ``span.last_day``."""
        agreement = self._agreement([
            self._day(date(2026, 8, 20), "2459.60", "2501.31"),
            self._day(date(2026, 8, 21), None, "2501.31"),
        ])

        headline = agreement.headline

        assert agreement.standing_gap is None
        assert headline.day == date(2026, 8, 20)
        assert headline.bank_balance == Decimal("2459.60")
        assert headline.gap == Decimal("41.71")

    def test_a_day_the_books_state_NO_balance_for_is_not_a_comparison(self):
        """Finding **N-314**, ruling **R-BAL286**: a bank figure alone is no hero.

        Built in the order every real comparison has, the books' figures
        appearing at their opening and never vanishing after it: Aug 18 lies
        before the books open on Aug 19, so the bank's ``2459.60`` there has no
        books balance to stand beside; Aug 19 and 20 state the books' level
        and the bank prices neither.  No day carries both figures, so there is
        no headline -- a hero on Aug 18 would set the bank's balance against a
        figure the books never stated.  *This case hand-built a day with no
        records AFTER a day with them until plan step balance:X-f3c-2b-2c, a
        state no account can be in.*
        """
        agreement = self._agreement([
            self._before_the_books(date(2026, 8, 18), "2459.60"),
            self._opening(date(2026, 8, 19), None, "2501.31"),
            self._day(date(2026, 8, 20), None, "2501.31"),
        ], opened_on=date(2026, 8, 19))

        assert agreement.headline is None
        assert agreement.standing_gap is None

    def test_the_page_refuses_DONE_over_a_comparison_that_stopped_early(
        self, app, db, seed_user,
    ):
        """A balanced comparison three weeks old is not *nothing left to do*.

        The headline walks BACK to the last priceable day, so an account whose
        statements stop before its records do can report ``off by $0.00`` about
        a week that is not this one.
        """
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), None, "2459.60"),
        ], account_id=seed_user["account"].id)

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.off_by == Decimal("0.00")
        assert page.hero.to_explain == 0
        assert page.hero.unpriced_after == 1
        assert page.is_done is False

    def test_a_current_and_balanced_account_IS_done(
        self, app, db, seed_user,
    ):
        """The mirror, so the refusal above is not refusing everything."""
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), "2459.60", "2459.60"),
        ], account_id=seed_user["account"].id)

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.unpriced_after == 0
        assert page.is_done is True

    def test_records_past_the_last_statement_REFUSE_done_and_are_named(
        self, app, db, seed_user,
    ):
        """Finding **BAL-651**, ruling plan#2346: not Done until a statement prices them.

        The statement prices Aug 19, where the books agree to the cent, and the
        books' last record is Aug 21, so the comparison draws Aug 20 and Aug 21
        unpriced.  Before the fix it ended on Aug 19 and read Done.  The page
        says how far it is priced and how far the records run.
        """
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), None, "2459.60"),
            self._day(date(2026, 8, 21), None, "2459.60"),
        ], account_id=seed_user["account"].id,
            last_record_on=date(2026, 8, 21))

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.off_by == Decimal("0.00")
        assert page.hero.to_explain == 0
        assert page.hero.day == date(2026, 8, 19)
        assert page.hero.unpriced_after == 2
        assert page.hero.records_through == date(2026, 8, 21)
        assert page.is_done is False

    def test_records_ending_ON_the_priced_day_are_not_named(
        self, app, db, seed_user,
    ):
        """The mirror: a statement reaching the last record is Done, and names none.

        The books' last record is the priced day itself, so no record waits
        on a statement and the hero's ``records_through`` is ``None``.
        """
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), "2459.60", "2459.60"),
        ], account_id=seed_user["account"].id,
            last_record_on=date(2026, 8, 20))

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.records_through is None
        assert page.is_done is True

    def test_a_pass_and_an_agreement_for_two_accounts_are_REFUSED(
        self, app, db, seed_user,
    ):
        """Two arguments a caller pairs by hand are two chances to mispair.

        The consequence is one account's hero over another's lines, with no
        figure on the page wrong enough to look wrong.
        """
        an_envelope(seed_user)
        db.session.commit()
        other = self._agreement([
            self._day(date(2026, 8, 20), "1.00", "1.00"),
        ], account_id=seed_user["account"].id + 999)

        with pytest.raises(ValueError) as refused:
            _page(seed_user, Tab.TO_EXPLAIN, agreement=other)

        assert "cannot show one account's lines beside another's" in str(
            refused.value,
        )

    @pytest.mark.parametrize(("typed", "off_by", "done"), [
        ("500.00", Decimal("0.00"), True),
        ("510.00", Decimal("10.00"), False),
    ])
    def test_the_statement_the_opening_was_TYPED_FROM_can_be_done(
        self, app, db, seed_user, seed_periods, typed, off_by, done,
    ):
        """Ruling **R-BAL286**: the opening day's balance IS compared.

        An account created on Feb 2 at *typed*, from a statement ending Feb 2
        whose bank states ``$500.00`` that day; its lines (Jan 30 ``-25.00``,
        Feb 2 ``-10.00``) are all inside the opening, and none is waiting to
        be explained.  Typed right, the hero is Feb 2 at ``$500.00`` against
        ``$500.00`` and the page is DONE; typed ``$510.00``, it is off by
        ``510.00 - 500.00 = +10.00`` and not done.  *The app before plan step
        balance:X-f3c-2b-2c gave this same hero and Done: the origination
        assertion made Feb 2 its first cash fact, so the day was compared.*
        """
        account = _opened_on(seed_user, date(2026, 2, 2), typed)
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 2, 2),
            evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
            lines=[(date(2026, 1, 30), "-25.00"),
                   (date(2026, 2, 2), "-10.00")],
        )
        db.session.commit()
        agreement = bank_agreement.bank_agreement(
            account, BalanceContext.build(seed_user["user"].id),
        )

        page = reconcile_page(
            a_scope(seed_user, account), agreement, Tab.TO_EXPLAIN,
        )

        assert page.hero.day == date(2026, 2, 2)
        assert page.hero.bank == Decimal("500.00")
        assert page.hero.books == Decimal(typed)
        assert page.hero.off_by == off_by
        assert page.hero.unpriced_after == 0
        assert page.hero.to_explain == 0
        assert page.is_done is done

    def test_a_GAP_day_after_the_hero_counts_as_UNPRICED(
        self, app, db, seed_user, seed_periods,
    ):
        """A day between two imports is drawn, not compared, and unpriced.

        Books open Feb 2 at ``$500.00``.  The first statement's one line is
        Feb 3's ``-40.00``, entered, and it states ``$460.00`` there; the
        second states no balance and its lines are Feb 6 and Feb 7's
        ``-5.00``, entered.  Nothing is imported for Feb 4 - Feb 5.  The hero
        is Feb 3, ``460.00`` against ``460.00``.  After it come four drawn
        days the books state a balance for and the bank prices none of: the
        two gap days, whose bank movement is unknown so they are not
        compared, and the second statement's two days.  So
        ``unpriced_after`` is ``2 + 2 = 4``; counting only the compared
        days after the hero would say ``2``, and a "priced through" note two
        days short.
        """
        account = _opened_on(seed_user, date(2026, 2, 2))
        _seed_import(
            db, account, stated="460.00", effective_on=date(2026, 2, 3),
            evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
            lines=[(date(2026, 2, 3), "-40.00")], file_name="first.csv",
        )
        _seed_import(
            db, account, stated=None,
            lines=[(date(2026, 2, 6), "-5.00"), (date(2026, 2, 7), "-5.00")],
            file_name="unpriced.csv",
        )
        for amount, day in (("40.00", date(2026, 2, 3)),
                            ("5.00", date(2026, 2, 6)),
                            ("5.00", date(2026, 2, 7))):
            _settle(
                seed_user, db, seed_periods[2], amount, day, account=account,
            )
        db.session.commit()
        agreement = bank_agreement.bank_agreement(
            account, BalanceContext.build(seed_user["user"].id),
        )

        page = reconcile_page(
            a_scope(seed_user, account), agreement, Tab.TO_EXPLAIN,
        )

        assert [day.day for day in agreement.days if not day.compared] == [
            date(2026, 2, 4), date(2026, 2, 5),
        ]
        assert page.hero.day == date(2026, 2, 3)
        assert page.hero.off_by == Decimal("0.00")
        assert page.hero.unpriced_after == 4

    def test_an_account_no_import_prices_has_no_headline_at_all(self):
        """Reporting an unknown as agreement is what this arc exists to stop."""
        agreement = self._agreement([
            self._day(date(2026, 8, 20), None, "2501.31"),
        ])

        assert agreement.headline is None

    def test_the_page_states_all_three_figures_or_none(
        self, app, db, seed_user,
    ):
        """A hero printing two of three invites a subtraction it cannot do."""
        an_envelope(seed_user)
        db.session.commit()

        hero = _page(seed_user, Tab.TO_EXPLAIN, agreement=None).hero

        assert (hero.day, hero.bank, hero.books, hero.off_by) == (
            None, None, None, None,
        )
        assert hero.to_explain == 0

    def test_done_is_never_claimed_for_an_account_nothing_prices(
        self, app, db, seed_user,
    ):
        """An empty inbox is half of *done*, and the other half is unknown."""
        an_envelope(seed_user)
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=None)

        assert page.hero.to_explain == 0
        assert page.is_done is False


class TestACaptionCountsOnlyWhatItsTabCanDraw:
    """Finding **bank_import:N-389**, plan step ``bank_import:X-gj-1c``.

    An act that names no bank line has no day, no amount and no wording, so it
    is not a card and never was.  The two readers disagreed about it:
    :func:`~app.services.statement_match._accepted_view.accepted_counts`
    counted the table, and the fold that builds the cards skipped it in Python
    -- so one such act made the Explained caption one higher than the tab could
    deliver.  Measured on a planted act 2026-08-31 before the fix: caption
    ``1``, rendered ``0``, withheld ``0``.

    :data:`~app.services.statement_match._acts.NAMES_A_BANK_LINE` is that
    invariant stated once, in SQL, and both readers narrow on it.

    **The state needs a code defect to reach**, which is why it is planted
    through raw SQL: ``record_match`` refuses an empty side at the one writer,
    ``fk_statement_match_members_line_account`` refuses to remove a line a
    match names, and migration ``e4a7c0f13b92`` deleted the acts that already
    held none.  A case over an account with no such act grades nothing here --
    both numbers are already equal -- so every assertion below stands beside a
    REAL act, which is what makes the equality mean something.
    """

    def _one_real_and_one_lineless(self, seed_user, db):
        """Stage one act a tab can draw, and one it cannot.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=False)
        db.session.commit()
        _planted_lineless(db, seed_user)

    def test_the_caption_equals_what_the_tab_delivers(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this read ``1`` against ``0 + 0`` before the fix."""
        self._one_real_and_one_lineless(seed_user, db)

        page = _page(seed_user, Tab.EXPLAINED)
        rendered = sum(section.count for section in page.sections)
        withheld = sum(section.withheld for section in page.sections)

        assert rendered == 1, "the REAL act must be drawn, or nothing is graded"
        assert _counts(page)[Tab.EXPLAINED] == rendered + withheld

    def test_the_counts_exclude_it_and_the_loader_never_yields_it(
        self, app, db, seed_user,
    ):
        """One clause, asked of both readers, over a set holding both kinds."""
        self._one_real_and_one_lineless(seed_user, db)
        owner, account = seed_user["user"].id, seed_user["account"].id

        counts = accepted_counts(owner, account)

        assert (counts.total, counts.by_rule, counts.by_hand) == (1, 0, 1)
        assert len(acts_of(owner, account)) == 1
        assert len(accepted_register(owner, account).shown) == 1

    def test_reaching_one_is_an_ALARM_and_not_a_silence(
        self, app, db, seed_user, caplog,
    ):
        """Skipping SILENTLY was the original defect (reviews, 2026-08-20).

        Such an act goes on claiming its transactions in ``matched_subjects``,
        so those rows can never be matched again and no release control exists
        to free them.  The alarm moved from the fold to the counts read with
        the clause; what may not happen is that it stops being raised.
        """
        self._one_real_and_one_lineless(seed_user, db)

        with caplog.at_level(logging.ERROR):
            _page(seed_user, Tab.EXPLAINED)

        assert any(
            record.__dict__.get("event") == "statement_match_lineless"
            for record in caplog.records
        )


class TestWhichKindOfCARDAPageHoldsIsSTATEDByWhatBuiltThem:
    """Plan steps ``bank_import:X-gj-1c`` and ``X-gj-4c-2``; ruling **R-JX**.

    **The two RECONCILING cases that used to live here are DELETED, and the
    deletion is the point.**  Which kind a tab held was a ``Tab.holds``
    property over a ``_TAB_CARD_KIND`` table, sitting beside a dispatch that
    independently built the cards -- one fact in two homes, agreeing only
    because a test said so.  One case drove the table from the enum and
    another paired the table against the cards a real page held; both were
    scaffolding around the defect that the two could differ at all.
    :func:`~._reconcile._tab_sections` returns the kind BESIDE the sections it
    just built, so the disagreement is unrepresentable and the reconciler has
    nothing left to reconcile (``CLAUDE.md`` rule 14: delete a home, do not
    keep two in step).

    **What replaced them is stronger and lives at the ROUTE**:
    ``test_statement_reconcile.TestEveryTabTheServiceBuildsIsServed
    ::test_every_tab_RENDERS_THE_CONTROL_ITS_KIND_CARRIES`` drives every
    :class:`~app.services.statement_match.Tab` through a real render and
    asserts each tab emits its own kind's control and NEITHER of the other
    two -- which grades the service, the page value and the template's three
    arms in one pass, where these graded a table.

    What is left here is the one claim about :class:`CardKind` itself that no
    render can make.
    """

    def test_the_three_predicates_partition_the_kinds(self, app, db, seed_user):
        """Exactly one of the three is true of every kind.

        The body renders one arm per predicate and NO ``else``, so a kind that
        answered ``False`` three times would draw a blank tab and a kind that
        answered ``True`` twice would draw two lists.  Driven from the enum, so
        a fourth kind fails here rather than on the screen.
        """
        for kind in CardKind:
            answers = (kind.is_line, kind.is_act, kind.is_skip)

            assert sum(answers) == 1, (kind, answers)

    def test_the_page_states_the_kind_its_own_cards_ARE(
        self, app, db, seed_user,
    ):
        """The page's ``kind`` against the fields its cards actually carry.

        **Not a reconciler**: there is one producer now, and this grades that
        each of its arms pairs the kind it returns with the cards it built --
        a dispatch arm can still be written ``CardKind.ACT, skip_sections(...)``
        in one place, which is a mistake a reader can see rather than a
        contract two files must keep.

        Staged over an account holding all three at once, because a page can
        be right about one arm and wrong about another.
        """
        envelope = an_envelope(seed_user)
        settled = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-35.72", sequence=1,
        )
        skipped = an_unexplained_outflow(
            seed_user, merchant="Target", amount="-9.99", sequence=2,
        )
        # **A card payment, so TRANSFERS holds one too.**  Every tab has to
        # draw a card or its arm is graded by an empty list, which asserts
        # nothing at all -- the loop below says so and would pass vacuously.
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            sequence=3, source_category=_CARD_PAYMENT,
        )
        db.session.commit()
        filed_by(seed_user, settled, envelope, by_rule=False)
        # ...and one a RULE filed, so FILED BY RULES holds one.
        filed_acts(seed_user, 1, by_rule=True)
        skip_line(
            skipped.id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.commit()

        #: Which attribute each kind's card carries, and which it must not.
        owns = {
            CardKind.LINE: "line",
            CardKind.ACT: "act",
            CardKind.SKIP: "skip",
        }
        for tab in Tab:
            page = _page(seed_user, tab)
            cards = _cards(page)

            assert cards, f"{tab} must hold a card, or this grades nothing"
            for card in cards:
                for kind, field in owns.items():
                    assert hasattr(card, field) is (
                        page.kind is kind
                    ), (tab, kind, field)


class TestTheSkippedTabIsWhereARecordedSkipIsFoundAndUndone:
    """Plan step ``bank_import:X-gj-4c-2``, rulings **R-JG** and **R-JH**.

    **The tab holds only lines the OWNER skipped.**  R-JH is what makes that
    a claim rather than a tautology: the tab USED to hold the lines a standing
    *never a purchase* answer barred, on the argument that the answer was the
    disposition, and ``X-gj-4c-1`` returned those to the inbox.  So a case
    that only planted a skip would pass on the old code too; one of these
    plants BOTH and asserts the barred line is absent.
    """

    def test_a_recorded_skip_is_a_card_on_this_tab_and_on_no_other(
        self, app, db, seed_user,
    ):
        """One skip, five tabs, and the count that names it.

        **Every tab is asked**, because "it is on Skipped" and "it is on
        Skipped ALONE" are different claims and only the second is what makes
        ruling **R-HP**'s *exactly one verb* visible on the screen.
        """
        line = an_unexplained_outflow(
            seed_user, merchant="Target", amount="-9.99",
        )
        db.session.commit()
        recorded = skip_line(
            line.id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.commit()

        holders = {
            tab: [
                card for card in _cards(_page(seed_user, tab))
                if getattr(card, "skip", None) is not None
            ]
            for tab in Tab
        }

        assert [card.skip.skip_id for card in holders[Tab.SKIPPED]] == [
            recorded.skip_id
        ]
        assert holders[Tab.SKIPPED][0].skip.line.line_id == line.id
        for tab in Tab:
            if tab is not Tab.SKIPPED:
                assert not holders[tab], tab
        assert _counts(_page(seed_user, Tab.TO_EXPLAIN))[Tab.SKIPPED] == 1
        assert _cards(_page(seed_user, Tab.TO_EXPLAIN)) == []

    def test_a_line_a_standing_NEVER_answer_bars_is_NOT_on_this_tab(
        self, app, db, seed_user,
    ):
        """Ruling **R-JH**, asked of the tab it names.

        *Not a purchase* is not *explained by nothing*, so such a line is inbox
        work with one door shut -- and this is the case that would still pass
        on the pre-``X-gj-4c-1`` code if the assertion were only about the
        skipped line, which is why both are planted at once.
        """
        an_envelope(seed_user)
        barred = an_unexplained_outflow(
            seed_user, merchant="Foundation Donation", amount="-4.00",
        )
        skipped = an_unexplained_outflow(
            seed_user, merchant="Target", amount="-9.99", sequence=1,
        )
        a_rule(seed_user, "Foundation Donation")
        db.session.commit()
        skip_line(
            skipped.id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.commit()

        page = _page(seed_user, Tab.SKIPPED)
        inbox = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert [card.skip.line.line_id for card in _cards(page)] == [
            skipped.id
        ]
        assert [card.line.line_id for card in inbox] == [barred.id]
        assert _counts(page)[Tab.SKIPPED] == 1

    def test_the_tab_is_EMPTY_with_no_section_when_nothing_is_skipped(
        self, app, db, seed_user,
    ):
        """An empty section is ABSENT rather than rendered empty.

        A heading over no rows reads as work the owner has somewhere to do,
        which is this package's rule for every list on the page.
        """
        an_unexplained_outflow(seed_user, merchant="Target")
        db.session.commit()

        page = _page(seed_user, Tab.SKIPPED)

        assert page.sections == ()
        assert _counts(page)[Tab.SKIPPED] == 0

    def test_the_caption_equals_RENDERED_PLUS_WITHHELD_at_every_size(
        self, app, db, seed_user,
    ):
        """The count and the cards, over an account with several skips.

        **The defect this refuses is finding N-389's**: a caption derived by
        one reader and cards by another, disagreeing by one.  Below the bound
        the withheld count is 0 and the equality is exact; the case that
        grades it ABOVE the bound is
        ``test_skipping.TestTheSkippedTabIsBOUNDEDAndSaysWhatItWithheld``.
        Asserted at three sizes, because an equality that holds only at one is
        satisfied by a constant.
        """
        owner_id, account_id = (
            seed_user["user"].id, seed_user["account"].id,
        )
        lines = [
            an_unexplained_outflow(
                seed_user, merchant=f"Shop {index}",
                amount="-1.00", sequence=index,
            )
            for index in range(3)
        ]
        db.session.commit()

        for how_many, line in enumerate(lines, start=1):
            skip_line(line.id, owner_id, account_id)
            db.session.commit()

            page = _page(seed_user, Tab.SKIPPED)

            rendered = len(_cards(page))
            withheld = sum(one.withheld for one in page.sections)

            assert rendered == how_many
            assert withheld == 0
            assert rendered + withheld == _counts(page)[Tab.SKIPPED]

    def test_the_card_carries_the_BANKS_facts_and_the_past_tense_sentence(
        self, app, db, seed_user,
    ):
        """What the card renders, from the line and from nothing else.

        The locked direction gives this tab as *the same card with Undo*, so
        the facts a reader scans for are the line's own -- and the sentence is
        the To-explain card's one tense over.
        """
        line = an_unexplained_outflow(
            seed_user, merchant="Target", amount="-9.99",
        )
        db.session.commit()
        skip_line(line.id, seed_user["user"].id, seed_user["account"].id)
        db.session.commit()

        card = _cards(_page(seed_user, Tab.SKIPPED))[0]

        assert card.skip.line.merchant == "Target"
        assert card.skip.line.amount == Decimal("-9.99")
        assert card.skip.line.posted_on == line.posted_on
        assert card.sentence == for_skip()
        assert card.sentence[0].text == Verb.SKIP.past


class TestADoublyAnsweredLineIsTHECOSTTwoRefusalsBUY:
    """What the two exclusivity refusals are FOR, made visible.

    Plan step ``bank_import:X-gj-4c-2``, ruling **bank_import:R-HP**.
    :func:`~app.services.statement_match._skipping.skip_line` refuses a line a
    live match answers and
    :func:`~app.services.statement_match._undisposed.skipped_among` refuses the
    mirror, and both docstrings state the same consequence: a line carrying
    both answers renders a card on the Explained tab AND a card on the Skipped
    tab, is absent from the inbox for two independent reasons, and nothing
    raises.

    **That sentence was written at ``X-gj-4a`` and it was FALSE then.**  The
    Skipped tab held the lines a standing *never a purchase* answer barred, not
    recorded skips, so a doubly-answered line rendered on Explained and
    NOWHERE else; ``X-gj-4c-1`` emptied that tab and ``X-gj-4c-2`` gave it the
    store to read.  This class is what makes the claim a measurement rather
    than prose -- which it has to be, because the whole justification for two
    app-tier refusals over a rule no CHECK can carry is what the state they
    refuse would look like.

    **The MATCH is filed through its real door and only the SKIP is planted at
    the ORM tier**, which is one door bypassed rather than two: the match is
    legal at the moment it is made, because no skip exists yet, and it is the
    RESULTING state that neither door would admit.  A case that went through
    both would grade the refusals rather than their consequence.  It is also
    why this is a claim about the SCREEN and not a claim that the doors are
    broken -- they are not, and :mod:`.test_skipping` grades that they are
    not.  *An earlier draft of this paragraph said "past BOTH doors", and the
    two service docstrings that cite this class were corrected before it
    was.*
    """

    def test_both_tabs_render_it_and_the_inbox_does_not(
        self, app, db, seed_user,
    ):
        """One line, two answers, two cards, and an inbox that never asks.

        **The inbox half is not an aside.**  It is the second independent
        reason the state is silent: ``undisposed``'s two ``NOT IN`` terms each
        exclude the line on their own, so removing either answer still hides
        it -- which is why nothing anywhere raises and why the refusals have to
        live at the doors.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=False)
        db.session.commit()
        # **Past the SKIP door, deliberately.**  ``skip_line`` refuses this
        # line by name (a match already answers it), which is the refusal
        # under discussion -- so the row is inserted directly, exactly as a
        # concurrent writer without the row lock would have left it.  The
        # match above went through its real door.
        db.session.add(StatementLineSkip(
            bank_statement_line_id=line.id,
            account_id=seed_user["account"].id,
            user_id=seed_user["user"].id,
        ))
        db.session.commit()

        explained = _cards(_page(seed_user, Tab.EXPLAINED))
        skipped = _cards(_page(seed_user, Tab.SKIPPED))
        counts = _counts(_page(seed_user, Tab.TO_EXPLAIN))

        assert [card.skip.line.line_id for card in skipped] == [line.id]
        assert len(explained) == 1
        assert line.description in explained[0].act.descriptions
        assert counts[Tab.EXPLAINED] == 1
        assert counts[Tab.SKIPPED] == 1
        assert counts[Tab.TO_EXPLAIN] == 0
        assert _cards(_page(seed_user, Tab.TO_EXPLAIN)) == []


class TestTheSettledBoundIsLIFTABLE:
    """Ruling **R-GX**'s bound, and the link the register offered past it.

    Plan step ``bank_import:X-gj-1c`` retires the register as a page
    (**R-HU**), so the tab that replaces it has to reach every act the
    register could: on the developer's own account the bound withholds 171 of
    221, which without a way past it would be 171 acts out of reach rather
    than merely unlisted.  The arithmetic is
    ``test_release.TestTheRegisterBoundsWhatItRenders``'s; this grades that
    the PAGE threads the parameter to the settled tabs and to no others.
    """

    def test_the_bound_cuts_and_says_so_and_None_lifts_it(
        self, app, db, seed_user,
    ):
        """One act past the boundary, both renders, one account.

        The bounded render must WITHHOLD -- an equal pair of counts would be
        satisfied by an account that never reached the bound at all.
        """
        filed_acts(seed_user, REGISTER_LIMIT + 1, by_rule=False)

        bounded = _page(seed_user, Tab.EXPLAINED)
        everything = _page(seed_user, Tab.EXPLAINED, limit=None)

        assert sum(one.count for one in bounded.sections) == REGISTER_LIMIT
        assert sum(one.withheld for one in bounded.sections) == 1
        assert sum(one.count for one in everything.sections) == (
            REGISTER_LIMIT + 1
        )
        assert sum(one.withheld for one in everything.sections) == 0

    def test_the_caption_is_the_whole_record_at_either_bound(
        self, app, db, seed_user,
    ):
        """A tab bar states what the account HOLDS, not what it drew.

        Lifting the bound may not move the caption, and the caption must equal
        rendered plus withheld at both -- which is the same equality
        :class:`TestACaptionCountsOnlyWhatItsTabCanDraw` grades from the other
        side.
        """
        filed_acts(seed_user, REGISTER_LIMIT + 1, by_rule=False)

        for page in (
            _page(seed_user, Tab.EXPLAINED),
            _page(seed_user, Tab.EXPLAINED, limit=None),
        ):
            rendered = sum(one.count for one in page.sections)
            withheld = sum(one.withheld for one in page.sections)

            assert _counts(page)[Tab.EXPLAINED] == REGISTER_LIMIT + 1
            assert rendered + withheld == REGISTER_LIMIT + 1


class TestTheChipsNameOnlyATabThatCanRenderThem:
    """Plan step ``bank_import:X-gj-1c`` deleted the *already explained* chip.

    It carried :attr:`~app.services.statement_match._accepted_view
    .AcceptedCounts.total` and led to the register, which lists every accepted
    act.  Once the two settled TABS exist that total is the union of two of
    them, so the chip would have promised a number neither tab delivers -- the
    caption-over-a-count defect the retired queue's ``_sweeps_for`` existed
    to refuse.

    What must hold now is the property the route rests on: every chip names a
    tab whose count it equals, or names no tab at all.
    """

    def test_no_chip_counts_settled_acts(self, app, db, seed_user):
        """Staged so the deleted chip WOULD have rendered."""
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=False)
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert _counts(page)[Tab.EXPLAINED] == 1, (
            "there must BE a settled act, or the deleted chip is absent for "
            "the wrong reason"
        )
        assert not [chip for chip in page.chips if chip.tab is Tab.EXPLAINED]

    def test_every_chip_equals_the_tab_it_names(self, app, db, seed_user):
        """The property the route's URL builder rests on, over real chips.

        A parked card payment gives the Transfers chip a member, so this is
        asked of a chip that exists rather than of an empty tuple.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-793.23",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEBIT CAPITAL ONE MOBILE PMT",
            merchant="Capital One Credit Card",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)
        counts = _counts(page)

        assert [chip.tab for chip in page.chips] == [Tab.TRANSFERS]
        for chip in page.chips:
            if chip.tab is not None:
                assert chip.count == counts[chip.tab], chip.label


class TestAnANSWEREDInflowIsPreFilledByItsRule:
    """Ruling **R-HT(a)** meeting **R-HS**, plan step ``bank_import:X-gj-2a``.

    **R-HX set a condition rather than a permanent bar**: every unmatched
    inflow reads *Choose what this is* "until ``X-gj-2`` ships R-HT(a)'s inflow
    rule and the destination becomes one the app can defend".  A stated rule is
    exactly that defence, so a deposit it answers for gets the pre-filled
    sentence R-HS asks for -- and one it does not still asks.
    """

    def _deposit(self, seed_user, merchant="Dividend Earned"):
        """Stage one unexplained deposit from *merchant*."""
        an_unexplained_outflow(
            seed_user, merchant=merchant, amount="0.15",
        )

    def test_a_ruled_deposit_states_what_it_will_be_recorded_as(
        self, app, db, seed_user,
    ):
        """*Add as Income: Salary*, with the verb first (**R-HR**)."""
        an_envelope(seed_user)
        category = seed_user["categories"]["Salary"]
        a_rule(
            seed_user, "Dividend Earned", income_category_id=category.id,
        )
        self._deposit(seed_user)
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        card = cards[0]
        said = " ".join(span.text or "" for span in card.sentence)
        assert card.sentence[0].text == "Add"
        assert category.display_name in said
        assert card.offers_ok is True

    def test_a_merchant_CREDIT_is_PRE_FILLED_as_a_REFUND(
        self, app, db, seed_user,
    ):
        """A refund is not income, and the card offers it as the purchase it is.

        The merchant has a SPENDING answer, so ruling **R-HT(a)** makes this
        credit its INVERSE -- a negative purchase back into the same container.
        **This case asserted the card ASKING until plan step
        ``bank_import:X-gj-2b-2``**, because the act did not exist; the card
        said *Choose* and the panel explained why nothing was filed.

        It now takes the PURCHASE verb, which is the visible half of the
        routing this step corrected: what a line becomes is decided by the
        owner's answer and not by the line's sign, so a claimed credit reaches
        the same card an outflow from that merchant reaches.
        """
        envelope = an_envelope(seed_user)
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        self._deposit(seed_user, merchant="Amazon")
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        card = cards[0]
        # The PURCHASE verb, not the deposit's *Choose what this is*.
        assert card.sentence[0].text == "Add"
        # And the rule names WHERE, so the owner is not asked to pick.
        assert card.suggested is not None

    def test_an_ALWAYS_ASK_deposit_is_not_pre_filled(
        self, app, db, seed_user,
    ):
        """The answer whose whole content is *keep asking me*.

        It names no income category, so it must reach the card exactly as
        having said nothing does -- and it must NOT be reported as a rule this
        pass withheld, which would be a sentence about a decision the owner
        made saying the app failed to act on it.
        """
        an_envelope(seed_user)
        a_rule(seed_user, "Dividend Earned", always_ask=True)
        self._deposit(seed_user)
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert cards[0].sentence[0].text == "Choose"
        assert not any("refund" in note for note in cards[0].panel.notes)


class TestTheOpenedPaneIsPricedFromTheAsk:
    """Plan step ``bank_import:X-gi-2a``, finding **BI-478**, ruling **R-BI3**.

    :func:`~app.services.statement_match.reconcile_page` took the line id
    alone and priced every ``?open=`` pane from the proposal the card offers;
    it takes an :class:`~app.services.statement_match.OpenedAsk` now, and the
    pane is priced from what the ask says the form holds.  **The route tests
    grade the page a browser receives; this grades the SEAM**, so a caller
    handing the page a submission cannot have it silently priced from the
    proposal instead.
    """

    @staticmethod
    def _a_card_payment_and_a_payback(seed_user, db):
        """Stage a parked `-$793.23` payment and one `-$93.23` payback.

        Returns:
            ``(line, payback)``.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        payback = a_transaction(
            seed_user, name="Payback", amount="93.23",
        )
        db.session.commit()
        return line, payback

    def _opened(self, seed_user, ask):
        """Return the Transfers tab's opened pane for *ask*."""
        page = reconcile_page(
            a_scope(seed_user), None, Tab.TRANSFERS, opened=ask,
        )
        assert page.opened is not None, "the card did not open at all"
        return page.opened

    def test_no_form_prices_the_proposal_which_for_a_parked_card_is_nothing(
        self, app, db, seed_user,
    ):
        """``submitted=None`` is the GET: the card as it stands untouched."""
        line, _ = self._a_card_payment_and_a_payback(seed_user, db)

        opened = self._opened(seed_user, OpenedAsk(line_id=line.id))

        assert opened.ticked == frozenset()
        assert opened.consent is None
        assert opened.totals.remedy == NOTHING_TICKED

    def test_a_submission_prices_the_pane_from_ITS_rows(
        self, app, db, seed_user,
    ):
        """The form's own tick is what the pane is priced against."""
        line, payback = self._a_card_payment_and_a_payback(seed_user, db)
        scope = a_scope(seed_user)
        submitted = a_submission(
            scope, lines=(line,), transactions=(payback,),
        )

        opened = self._opened(
            seed_user, OpenedAsk(line_id=line.id, submitted=submitted),
        )

        assert opened.ticked == {(RowKind.TRANSACTION, payback.id)}
        assert opened.totals.bank == Decimal("-793.23")
        assert opened.totals.app == Decimal("-93.23")
        assert opened.totals.difference == Decimal("-700.00")
        assert [option.value for option in opened.totals.options] == [
            "-700.00",
        ], "the lone row's one act was not offered against the submitted rows"

    def test_the_consent_is_carried_whole_and_never_read_by_the_preview(
        self, app, db, seed_user,
    ):
        """Ruling **R-BI3**'s seam: the echo is the request's, the options
        are the arithmetic's.

        A consent to a figure the rows no longer come to is still CARRIED --
        the template compares it against each option and picks none -- and
        it changes nothing about which acts are offered.
        """
        line, payback = self._a_card_payment_and_a_payback(seed_user, db)
        scope = a_scope(seed_user)
        stale = a_submission(
            scope, lines=(line,), transactions=(payback,), residual="-50.00",
        )

        opened = self._opened(
            seed_user, OpenedAsk(line_id=line.id, submitted=stale),
        )

        assert opened.consent == ReviewedDifference(figure=Decimal("-50.00"))
        assert [option.value for option in opened.totals.options] == [
            "-700.00",
        ]
        assert all(
            option.accepts != opened.consent for option in opened.totals.options
        ), "an option equals a consent to a figure the rows do not come to"
