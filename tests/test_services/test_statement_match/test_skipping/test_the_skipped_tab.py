"""The reader over what the doors recorded, and the Skipped tab's bound."""

from datetime import timedelta
from decimal import Decimal

from app.services.statement_match import skip_line, unskip_line
from app.services.statement_match._accepted_view import REGISTER_LIMIT
from app.services.statement_match._skipping import skipped_acts, skipped_count

from tests.test_services.test_statement_match._builders import a_bank_line, a_sighting, an_import

from ._helpers import _owner, _skip


class TestTheReaderOverWhatTheDoorsRecorded:
    """Plan step ``bank_import:X-gj-4c-2``: what the Skipped tab lists.

    **Three claims, and each has its own way of being wrong.**  WHOSE rows come
    back is an ownership question a missing filter answers wrongly and silently;
    WHAT ORDER they come back in is a decision the locked direction states and
    a reader can only get wrong once; and whether the COUNT agrees with the LIST
    is finding **N-389**'s defect, which shipped a caption promising a card its
    tab did not render.
    """

    def _skipped_line(self, seed_user, db, **fields):
        """Stage one line, skip it, and return the line and the act.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            **fields: Passed to :func:`a_bank_line`.

        Returns:
            ``(line, SkippedLine)``.
        """
        line = a_bank_line(seed_user, an_import(seed_user), **fields)
        db.session.flush()
        return line, _skip(seed_user, line)

    def test_it_carries_the_act_id_the_undo_door_accepts(
        self, app, db, seed_user,
    ):
        """The pairing the whole tab rests on.

        A card offers ``skip_id`` and :func:`unskip_line` takes it, so a reader
        returning any other id would render an Undo that refuses -- which is
        the reason the reader and the door are one module.  **The door is
        actually CALLED**, because a test comparing the reader's id with the
        door's own return value would grade two spellings of one field rather
        than the pairing.
        """
        owner_id, account_id = _owner(seed_user)
        line, recorded = self._skipped_line(seed_user, db)

        listed = skipped_acts(owner_id, account_id).shown

        assert [act.skip_id for act in listed] == [recorded.skip_id]
        assert unskip_line(listed[0].skip_id, owner_id, account_id) == line.id
        assert skipped_acts(owner_id, account_id).shown == ()

    def test_a_line_TWO_imports_sighted_is_one_act_and_the_bound_counts_it_once(
        self, app, db, seed_user,
    ):
        """The window count and the bound are over LINES, not joined rows.

        The line's ``sightings`` ride its query as a JOIN (plan step
        ``bank_import:X-f6b-1``), so a line two imports showed is two joined
        rows; ``count() OVER ()`` and ``LIMIT`` have to see one.  Two skipped
        lines, each sighted twice, at a bound of two: both shown, nothing
        withheld -- where a count over joined rows would report two withheld.
        """
        owner_id, account_id = _owner(seed_user)
        lines = [
            self._skipped_line(
                seed_user, db, amount="-9.99", sequence_in_group=ordinal,
                description=f"LINE {ordinal}",
            )[0]
            for ordinal in range(2)
        ]
        # Staged AFTER the two imports above, so by act order it is the
        # LATEST and its wording is what each line reads.
        again = an_import(seed_user)
        for ordinal, line in enumerate(lines):
            a_sighting(seed_user, again, line, description=f"LINE {ordinal} AGAIN")
        db.session.flush()

        acts = skipped_acts(owner_id, account_id, limit=2)

        assert len(acts.shown) == 2
        assert len({act.line.line_id for act in acts.shown}) == 2
        assert acts.withheld_count == 0
        assert {act.line.description for act in acts.shown} == {
            "LINE 0 AGAIN", "LINE 1 AGAIN",
        }

    def test_it_carries_the_BANK_facts_the_card_prints(
        self, app, db, seed_user,
    ):
        """Every field the card renders, from the line the skip names.

        **The MERCHANT is the one worth naming.**  It is not a column on the
        line -- it is reached through a relationship -- so a reader that
        selected columns rather than the row would have to load it per card,
        which is the N+1 finding **N-309** already paid for.
        """
        owner_id, account_id = _owner(seed_user)
        line, _recorded = self._skipped_line(
            seed_user, db, amount="-9.99", merchant="Target",
            description="POINT OF SALE DEBIT L340 TARGET",
            source_category="Retail/Department Store",
        )

        act = skipped_acts(owner_id, account_id).shown[0]

        assert act.line.line_id == line.id
        assert act.line.merchant == "Target"
        assert act.line.merchant_id == line.merchant_id
        assert act.line.amount == Decimal("-9.99")
        assert act.line.posted_on == line.posted_on
        assert act.line.description == "POINT OF SALE DEBIT L340 TARGET"
        assert act.line.source_category == "Retail/Department Store"

    def test_the_NEWEST_bank_day_is_first_and_a_tie_is_stable(
        self, app, db, seed_user,
    ):
        """The locked direction's order, and what happens on one day.

        **Three lines over two days**, because a two-line case cannot tell a
        descending sort from an ascending one that happens to have been
        inserted backwards, and a case with no tie cannot grade the tie-break
        at all.  The pass hands lines over ASCENDING, so a reader that did not
        sort would return exactly the wrong order -- which is the defect plan
        step ``bank_import:X-gj-1b`` found in the inbox's own sections.
        """
        owner_id, account_id = _owner(seed_user)
        older = seed_user["bootstrap_period"].start_date
        newer = older + timedelta(days=1)
        first, _a = self._skipped_line(
            seed_user, db, posted_on=older, sequence_in_group=0,
        )
        same_day_a, _b = self._skipped_line(
            seed_user, db, posted_on=newer, sequence_in_group=1,
        )
        same_day_b, _c = self._skipped_line(
            seed_user, db, posted_on=newer, sequence_in_group=2,
        )

        listed = skipped_acts(owner_id, account_id).shown

        assert [act.line.line_id for act in listed] == [
            same_day_b.id, same_day_a.id, first.id,
        ]

    def test_it_returns_NOTHING_of_another_owners(
        self, app, db, seed_user, seed_second_user,
    ):
        """The ownership narrowing, with a real row on the other side.

        **Both owners really have a skip**, so an unfiltered reader returns two
        and this fails; a case where only one owner had ever skipped anything
        would pass with the filter deleted.
        """
        mine, _a = self._skipped_line(seed_user, db)
        theirs = a_bank_line(seed_second_user, an_import(seed_second_user))
        db.session.flush()
        skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )
        db.session.flush()

        listed = skipped_acts(*_owner(seed_user)).shown

        assert [act.line.line_id for act in listed] == [mine.id]
        assert skipped_count(*_owner(seed_user)) == 1
        assert skipped_count(
            seed_second_user["user"].id, seed_second_user["account"].id,
        ) == 1

    def test_the_owner_id_is_a_FILTER_and_not_a_decoration(
        self, app, db, seed_user, seed_second_user,
    ):
        """FIRING CONTROL for the ``user_id`` term of :func:`_mine`.

        The sibling above passes each owner their OWN account, which the
        ``account_id`` term alone answers correctly.  This asks for another
        owner's account under this caller's id -- the pairing that gets past
        that term -- and the answer must be nothing rather than their rows.
        Delete ``StatementLineSkip.user_id == owner_id`` and this case fails
        while every other one here still passes.
        """
        theirs = a_bank_line(seed_second_user, an_import(seed_second_user))
        db.session.flush()
        skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )
        db.session.flush()

        crossed = seed_user["user"].id, seed_second_user["account"].id

        assert skipped_acts(*crossed).shown == ()
        assert skipped_count(*crossed) == 0

    def test_the_COUNT_equals_the_LIST_at_every_size(
        self, app, db, seed_user,
    ):
        """Finding **N-389**'s defect, refused before it can happen.

        The caption is one query and the cards are another, so the two could
        disagree; what makes them agree structurally is the shared clause plus
        an INNER JOIN a foreign key guarantees.  Asserted at four sizes,
        including zero, because an equality that holds only where both are
        empty grades nothing.
        """
        owner_id, account_id = _owner(seed_user)

        assert skipped_count(owner_id, account_id) == 0
        assert skipped_acts(owner_id, account_id).shown == ()

        for expected in range(1, 4):
            self._skipped_line(seed_user, db, sequence_in_group=expected)
            register = skipped_acts(owner_id, account_id)

            assert skipped_count(owner_id, account_id) == expected
            assert len(register.shown) == expected
            assert register.withheld_count == 0

    def test_an_UNDONE_skip_leaves_both_readers(self, app, db, seed_user):
        """Ruling **R-JG**: undoing DELETES the row, so the tab empties.

        **The pair is the point.**  A reader that answered from a cache, or one
        the door's ``flush`` did not reach, would go on listing a card whose
        Undo the door can no longer find -- which is the state
        :func:`unskip_line` flushes to prevent, asked here of the surface that
        would show it.
        """
        owner_id, account_id = _owner(seed_user)
        _line, recorded = self._skipped_line(seed_user, db)
        assert skipped_count(owner_id, account_id) == 1

        unskip_line(recorded.skip_id, owner_id, account_id)

        assert skipped_count(owner_id, account_id) == 0
        assert skipped_acts(owner_id, account_id).shown == ()


class TestTheSkippedTabIsBOUNDEDAndSaysWhatItWithheld:
    """Ruling **bank_import:R-GX**'s shape on a third tab (**R-JW**).

    **The bound governs BYTES, which is what R-GX actually says.**  This step
    first shipped the tab unbounded and defended it on the claim that the
    settled tabs bound because they VALUE every act they render -- adversarial
    review measured that false against
    :data:`~app.services.statement_match._accepted_view.REGISTER_LIMIT`'s own
    docstring: the fold reads every act either way, and what is bounded is
    what is RENDERED.  Measured 2026-09-04 on the real page, a skip card costs
    1,427 bytes against about 980 for a settled act, so the tab that had no
    bound cost more per card than the one that did.

    **A truncated list that does not say it is truncated is a page claiming to
    be the whole record**, and this tab is the only surface a skipped line can
    be found and undone on -- so what the bound withholds has to be reachable
    rather than merely unlisted.

    **The bound is the PAGE's one** (:data:`~app.services.statement_match
    ._accepted_view.REGISTER_LIMIT`), not a constant of this tab's own: a
    first version of this step declared a ``SKIPPED_LIMIT`` the page never
    read, so these cases were green against a literal that governed nothing.
    Asserting the constant the ROUTE threads is what makes them grade the
    bound in force.
    """

    def _skips(self, seed_user, db, how_many):
        """Record *how_many* skips on the seeded account.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            how_many: How many lines to stage and skip.

        Returns:
            Nothing; the rows are recorded and committed.
        """
        owner_id, account_id = _owner(seed_user)
        for index in range(how_many):
            line = a_bank_line(
                seed_user, an_import(seed_user), sequence_in_group=index,
            )
            db.session.flush()
            skip_line(line.id, owner_id, account_id)
        db.session.commit()

    def test_it_CUTS_at_the_limit_and_says_how_many_it_withheld(
        self, app, db, seed_user,
    ):
        """One skip past the boundary, so the bound must actually fire.

        **An equal pair of counts would be satisfied by an account that never
        reached the bound at all**, which is why this stages
        :data:`REGISTER_LIMIT` + 1 rather than a round number: the rendered
        list is the limit, the withheld count is one, and the two sum to the
        whole record.
        """
        owner_id, account_id = _owner(seed_user)
        self._skips(seed_user, db, REGISTER_LIMIT + 1)

        register = skipped_acts(owner_id, account_id)

        assert len(register.shown) == REGISTER_LIMIT
        assert register.withheld_count == 1
        assert len(register.shown) + register.withheld_count == (
            skipped_count(owner_id, account_id)
        )

    def test_None_LIFTS_the_bound_and_withholds_nothing(
        self, app, db, seed_user,
    ):
        """What the *show the other N* link asks for.

        The whole record, and a withheld count of zero -- which is what tells
        the surface it may stop offering the link.
        """
        owner_id, account_id = _owner(seed_user)
        self._skips(seed_user, db, REGISTER_LIMIT + 1)

        register = skipped_acts(owner_id, account_id, limit=None)

        assert len(register.shown) == REGISTER_LIMIT + 1
        assert register.withheld_count == 0

    def test_what_it_KEEPS_is_the_newest_and_what_it_drops_is_the_oldest(
        self, app, db, seed_user,
    ):
        """The bound cuts the OLD end, which is what makes it survivable.

        A bound that dropped the newest would hide the skip the owner just
        made, which is the one they are most likely to be undoing.  **Asserted
        on the actual bank days**, not on ids: the order is
        ``posted_on DESC, id DESC`` and a case reading ids alone would pass on
        an insertion order that happened to agree.

        **It grades the DIRECTION of the cut and not its SIZE**, deliberately:
        it would pass under any bound from 2 to the limit, because it never
        asserts ``len(shown)``.  The size is
        :meth:`test_it_CUTS_at_the_limit_and_says_how_many_it_withheld`'s, and
        the two are separate cases so that a wrong size and a wrong end fail
        distinguishably.  Named by adversarial review.
        """
        owner_id, account_id = _owner(seed_user)
        opens = seed_user["bootstrap_period"].start_date
        for index in range(REGISTER_LIMIT + 2):
            line = a_bank_line(
                seed_user, an_import(seed_user),
                posted_on=opens + timedelta(days=index),
                sequence_in_group=index,
            )
            db.session.flush()
            skip_line(line.id, owner_id, account_id)
        db.session.commit()

        shown = skipped_acts(owner_id, account_id).shown
        days = [act.line.posted_on for act in shown]

        assert days == sorted(days, reverse=True)
        assert days[0] == opens + timedelta(days=REGISTER_LIMIT + 1)
        # The two OLDEST are the ones withheld, which is the whole claim.
        assert opens not in days
        assert opens + timedelta(days=1) not in days

    def test_the_bound_and_the_COUNT_answer_different_questions(
        self, app, db, seed_user,
    ):
        """The tab bar states the whole record; the tab renders part of it.

        **This is the caption-over-a-count defect finding N-389 measured**, in
        the one state where it can now appear: the bar says 51 and the list
        holds 50, and the difference is SAID rather than silent.  A caption
        that fell to the rendered figure would tell the owner they have fewer
        skips than they do.
        """
        owner_id, account_id = _owner(seed_user)
        # **SEVEN past the bound, not one.**  Its sibling above stages one, and
        # two cases asserting the same triple over the same fixture are one
        # case with two names -- a remainder greater than 1 also catches an
        # off-by-one that a remainder OF 1 cannot distinguish from a constant.
        self._skips(seed_user, db, REGISTER_LIMIT + 7)

        register = skipped_acts(owner_id, account_id)

        assert skipped_count(owner_id, account_id) == REGISTER_LIMIT + 7
        assert len(register.shown) == REGISTER_LIMIT
        assert register.withheld_count == 7
