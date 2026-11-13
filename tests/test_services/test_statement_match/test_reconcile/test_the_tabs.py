"""The tabs partition the account, the settled tab's bound and verb, the doors, the sweep."""

from datetime import timedelta

from app.services import statement_match
from app.services.statement_match import Tab
from app.services.statement_match._accepted_view import (
    REGISTER_LIMIT,
    accepted_counts,
    accepted_register,
)
from app.services.statement_match._verbs import Verb
from app.services.statement_match._create import MintedEnvelopes
from app.services.statement_match._creations import PurchaseCreation
from app.services.statement_match._release import acts_of
from app.services.statement_match._scope import no_period_refusal

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_rule,
    a_scope,
    a_submission,
    a_transaction,
    an_answers,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import _CARD_PAYMENT, _page, _cards, _counts


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
