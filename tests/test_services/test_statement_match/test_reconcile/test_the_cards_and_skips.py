"""Which kind of card a page holds, the Skipped tab, a doubly answered line, the bound, chips."""

from decimal import Decimal

from app.models.statement_line_skip import StatementLineSkip
from app.services.statement_match import Tab, skip_line
from app.services.statement_match._cards import CardKind
from app.services.statement_match._sentence import for_skip
from app.services.statement_match._accepted_view import REGISTER_LIMIT
from app.services.statement_match._verbs import Verb

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_rule,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_acts,
    filed_by,
)

from ._helpers import _CARD_PAYMENT, _page, _cards, _counts


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
    ``test_statement_reconcile.test_the_doors
    .TestEveryTabTheServiceBuildsIsServed
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
        ``test_skipping.test_the_skipped_tab.TestTheSkippedTabIsBOUNDEDAndSaysWhatItWithheld``.
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
    broken -- they are not, and
    :mod:`tests.test_services.test_statement_match.test_skipping` grades that
    they are not.  *An earlier draft of this paragraph said "past BOTH
    doors", and the two service docstrings that cite this class were
    corrected before it was.*
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
    than merely unlisted.  The arithmetic is ``test_release
    .test_the_screen_and_the_register.TestTheRegisterBoundsWhatItRenders``'s;
    this grades that the PAGE threads the parameter to the settled tabs and to no others.
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
