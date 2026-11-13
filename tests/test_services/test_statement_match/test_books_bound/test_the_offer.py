"""A line the books cannot hold is not offered: the comparison, the review pass, what the screen
says instead, the badge.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.services.cash_ledger import books_hold
from app.services.statement_match import awaiting_review_count, reconcile_page, review_set, Tab

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_transaction,
    an_import,
)

from ._helpers import (
    _books_open_on,
    _the_calendars_first_day,
    _an_account_opening_inside_the_calendar,
)


class TestTheComparisonItself:
    """``books_hold`` -- the one test every refusal in the boundary asks."""

    def test_a_day_AFTER_the_books_open_is_held(self):
        """Money that moved after the opening is outside it, so recordable."""
        assert books_hold(date(2026, 3, 26), date(2026, 3, 27)) is True

    def test_the_day_the_books_OPEN_is_not(self):
        """FIRING CONTROL for ruling R-HG's ruled half.

        The opening equity is the CLOSING balance for its own day, so a
        movement dated ON it is already inside the figure.  R-HG weighed the
        start-of-day reading and rejected it; a ``>=`` written for a ``>``
        would re-open finding **N-378** for every same-day row, and this is
        not a hypothetical boundary -- the developer's own Checking opens on
        the pay calendar's first day and four real lines post that day.
        """
        assert books_hold(date(2026, 3, 26), date(2026, 3, 26)) is False

    def test_a_day_BEFORE_the_books_open_is_not(self):
        """The unambiguous side, kept so the boundary case has a neighbour."""
        assert books_hold(date(2026, 3, 26), date(2026, 3, 25)) is False


class TestTheReviewPassStopsOfferingIt:
    """The OFFER half: such a line reaches no list the screen draws from."""

    def test_an_outflow_ON_the_books_day_is_not_creatable(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this is the `-$108.87` Amazon line's shape.

        Before this step it sat in ``creatable`` with ``withheld=None``, so
        the screen rendered a live destination chooser and the door refused
        the submission it invited.
        """
        day = _the_calendars_first_day(db, seed_user)
        a_bank_line(seed_user, an_import(seed_user), posted_on=day)

        review = review_set(a_scope(seed_user))

        assert [item.line.line_id for item in review.creatable] == []
        assert [line.line_id for line in review.unmatched] == []

    def test_an_inflow_ON_the_books_day_is_not_recordable(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the `$2,573.42` payroll deposit's shape.

        The inflow arm is a separate list with a separate door, so a fix that
        only narrowed the outflow list would leave the larger figure offered.
        """
        day = _the_calendars_first_day(db, seed_user)
        a_bank_line(
            seed_user, an_import(seed_user), amount="2573.42", posted_on=day,
        )

        review = review_set(a_scope(seed_user))

        assert [item.line.line_id for item in review.recordable_inflows] == []

    def test_no_PROPOSAL_is_made_for_it(self, app, db, seed_user):
        """FIRING CONTROL, and the worst of the three exposures.

        Two of the four lines Checking's books cannot hold were proposals the
        app made itself -- and the screen offers a one-click sweep over every
        proposal in a risk class, so the owner would accept them without ever
        reading the line.  The row here is priced to the line's own figure so
        the proposer would certainly pair the two.
        """
        day = _the_calendars_first_day(db, seed_user)
        # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
        # developer-confirmed 2026-09-22 ("Yes: open books before rows"; this
        # module's option: the row in the SECOND paycheck).  The books open ON
        # the calendar's first day -- this module's subject -- and since ruling
        # R-PC85 a recurring row dated on that day is inside them and never
        # generated, so the row the lines match against is filed a paycheck on.
        a_transaction(
            seed_user, name="Duke Energy", amount="180.00",
            period=a_later_period(seed_user),
        )
        line = a_bank_line(seed_user, an_import(seed_user), posted_on=day)

        review = review_set(a_scope(seed_user))

        assert review.proposals == ()
        assert line.id not in {other.line_id for other in review.unmatched}

    def test_the_same_line_past_EARLIER_books_IS_proposed_against_that_row(
        self, app, db, seed_user,
    ):
        """POSITIVE CONTROL for the case above (C18-a adversarial review, L3).

        That case's row is filed a paycheck on, so its line sits at the very
        edge of the proposer's day window; ``proposals == ()`` would pass
        there for a window that merely shrank.  The same row, the same figure
        and the line on the SAME day -- the same distance from the row -- with
        only the books moved a day earlier, so the line falls past them: it
        IS proposed, so the empty answer above is the books' and not the
        window's.  (Round 2's L-f: this control used to move the LINE a day
        later instead, one day nearer the row, so a window shrunk by one day
        would have emptied the case above and still passed here.)
        """
        day = seed_user["bootstrap_period"].start_date
        _books_open_on(db, seed_user, day - timedelta(days=1))
        a_transaction(
            seed_user, name="Duke Energy", amount="180.00",
            period=a_later_period(seed_user),
        )
        line = a_bank_line(seed_user, an_import(seed_user), posted_on=day)

        review = review_set(a_scope(seed_user))

        assert [
            [bank.line_id for bank in proposal.lines]
            for proposal in review.proposals
        ] == [[line.id]]

    def test_a_line_the_day_AFTER_is_offered(self, app, db, seed_user):
        """The other side of the boundary, so the split is not simply "all".

        Separated from the case above deliberately: a ``>=`` written for a
        ``>`` passes every case that only ever probes one side.
        """
        day = _the_calendars_first_day(db, seed_user)
        line = a_bank_line(
            seed_user, an_import(seed_user),
            posted_on=day + timedelta(days=1),
        )

        review = review_set(a_scope(seed_user))

        assert [item.line.line_id for item in review.creatable] == [line.id]
        assert review.bounds.books is None

    def test_the_bound_grades_the_POSTING_day_and_not_the_swipe_day(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for a RULED behaviour nothing else exercises.

        ``cash_ledger.reject_line_before_books_open`` rules it explicitly --
        "the posting day and never the transaction day" -- and the reason is
        money rather than bookkeeping: a card swiped BEFORE the books opened
        and TAKEN after is money that left the account after the opening, so
        it is recordable, and the purchase's ``purchased_on`` is a budget
        clock rather than a movement.

        This case grades the OFFER SPLIT
        (``_gaps._split_at_books_open``); the DOOR is graded by
        ``test_the_CREATE_door_ACCEPTS_a_swipe_made_before_the_books`` in
        ``test_the_doors.py``.
        Both are needed: an earlier draft of this case claimed to guard the
        door while asserting only on ``review_set``, so the door mutation it
        named would have survived it.  Found by adversarial test review
        2026-08-31, twice -- once for the missing coverage and once for the
        docstring that overstated what this half reached.
        """
        day = _the_calendars_first_day(db, seed_user)
        line = a_bank_line(
            seed_user, an_import(seed_user),
            posted_on=day + timedelta(days=1),
            transaction_on=day - timedelta(days=3),
        )

        review = review_set(a_scope(seed_user))

        assert [item.line.line_id for item in review.creatable] == [line.id]
        assert review.bounds.books is None


class TestWhatTheScreenSaysInstead:
    """The bound: a count, its day, the figure it is inside, and the act."""

    def test_the_bound_names_the_opening_and_the_lines(
        self, app, db, seed_user,
    ):
        """A count with no day and no figure cannot be acted on.

        Built on ACCOUNT 10's shape -- books opening ten days into the pay
        calendar -- so the bound spans more than one day: two lines on the
        books day and one four days earlier, one line the day after.
        ``count`` is therefore not the number of days, and ``last_day`` is
        not the only day the lines fall on.

        **``last_day`` EQUALS ``opened_on`` here, and that is the fixture's
        shape rather than a property of the field.**  This docstring claimed
        the opposite -- "``opened_on`` is not ``last_day`` by accident" --
        while every held line in it falls on or before the books day, so
        ``last_day`` could have been ``opened_on`` and every assertion in the
        class would still have passed.  Found by adversarial test review
        2026-08-31; the case below separates them.
        """
        account, opened_on = _an_account_opening_inside_the_calendar(
            db, seed_user, days_in=10,
        )
        statement = an_import(seed_user, account=account)
        a_bank_line(
            seed_user, statement, posted_on=opened_on - timedelta(days=4),
            sequence_in_group=0,
        )
        a_bank_line(
            seed_user, statement, posted_on=opened_on, sequence_in_group=1,
        )
        a_bank_line(
            seed_user, statement, amount="-12.00", posted_on=opened_on,
            sequence_in_group=2,
        )
        a_bank_line(
            seed_user, statement, amount="-9.00",
            posted_on=opened_on + timedelta(days=1), sequence_in_group=3,
        )

        review = review_set(a_scope(seed_user, account=account))
        bound = review.bounds.books

        assert bound.count == 3
        assert bound.last_day == opened_on
        assert bound.opened_on == opened_on
        assert bound.opening_equity == Decimal("1000.00")
        assert "3 line(s)" in bound.said
        # The figure and the day as the OWNER reads them, which is how the
        # books-opening card two clicks away writes the same two facts.
        assert "$1,000.00" in bound.said
        assert opened_on.strftime("%b %-d, %Y") in bound.said
        # The fourth line is a day past the opening, so it is still work --
        # which is what keeps the bound a bound rather than the whole import.
        assert review.bounds.before_calendar_count == 0
        assert len(review.unmatched) == 1

    def test_the_last_day_is_the_LATEST_HELD_LINE_and_not_the_books_day(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: ``last_day`` is ``max(held)``, not ``opened_on``.

        Every other case in this file puts a held line ON the books day, so
        the two fields coincide and neither ``max``-over-the-lines nor a plain
        ``opening.opened_on`` could be told apart -- ``said`` prints both
        dates, so even the string assertions were blind to a swap.  Here the
        books open two days after the latest line the bound holds, so the two
        differ and only the correct derivation passes.
        """
        account, opened_on = _an_account_opening_inside_the_calendar(
            db, seed_user, days_in=10,
        )
        statement = an_import(seed_user, account=account)
        a_bank_line(
            seed_user, statement, posted_on=opened_on - timedelta(days=4),
            sequence_in_group=0,
        )
        a_bank_line(
            seed_user, statement, amount="-12.00",
            posted_on=opened_on - timedelta(days=2), sequence_in_group=1,
        )

        bound = review_set(a_scope(seed_user, account=account)).bounds.books

        assert bound.count == 2
        assert bound.last_day == opened_on - timedelta(days=2)
        assert bound.last_day < bound.opened_on
        assert bound.opened_on == opened_on

    def test_the_act_is_stated_apart_from_the_bound(self, app, db, seed_user):
        """A surface with a URL links the act; one without prints it.

        They are separate strings for that reason, so neither restates the
        other's half -- ``BarredLine.reason`` / ``answer_door``'s shape.  A
        single string would force every surface without a URL to render an
        anchor's words as prose or drop the remedy.
        """
        day = _the_calendars_first_day(db, seed_user)
        a_bank_line(seed_user, an_import(seed_user), posted_on=day)

        bound = review_set(a_scope(seed_user)).bounds.books

        assert "Restate" in bound.restatement_act
        assert "Restate" not in bound.said

    # **The two bound FLAGS went at plan step ``bank_import:X-gi-3``**, and
    # with them ``test_both_bound_flags_include_it``.  ``any_limit`` was the
    # review queue's *anything unexamined* question and ``any_pick_list_limit``
    # the workbench's; ``bank_import:X-gi-2`` deleted both templates, which
    # left the two properties with no app reader (finding **bank_import:BI-480**).
    # What the books bound is FOR is unchanged and is graded above and below:
    # it is ``ReconcilePage.books_bound``, which carries an ACT, and the
    # Reconcile page decides for itself which bounds it states
    # (``_reconcile._unexamined``).

    def test_the_two_day_bounds_are_DISJOINT(self, app, db, seed_user):
        """A line before BOTH bounds is counted ONCE, under the calendar.

        FIRING CONTROL for the ORDER in ``bounded_lines``.  On the developer's
        own Checking the two overlap almost completely -- 130 lines before the
        calendar and 134 on or before the books -- so applying each to the
        whole set would report 264 held back out of 378 and tell him 134 lines
        are inside an opening balance that accounts for 4.
        """
        day = _the_calendars_first_day(db, seed_user)
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, posted_on=day - timedelta(days=10),
            sequence_in_group=0,
        )
        a_bank_line(seed_user, statement, posted_on=day, sequence_in_group=1)

        bounds = review_set(a_scope(seed_user)).bounds

        assert bounds.before_calendar_count == 1
        assert bounds.books.count == 1

    def test_the_reconcile_page_carries_the_bound_and_its_act(
        self, app, db, seed_user,
    ):
        """The page surfaces the bound whole, with the act the template links.

        A bound with no way to act on it is the shape this package moved the
        near tier's count out of the panel for.

        **It is NOT a holding chip**, and it was one until an adversarial
        design review measured what that cost: the chip carried the count, the
        day and a link, and the sentence under it repeated all three three
        lines later.  The chip row is asserted empty of it here so that going
        back to the chip fails rather than quietly restoring the duplication.
        """
        day = _the_calendars_first_day(db, seed_user)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, posted_on=day, sequence_in_group=0)
        # A line BEFORE the calendar, so the pay-calendar chip renders and the
        # negative below is ABOUT something.  Without it ``page.chips`` is
        # empty and ``all()`` is vacuously true -- it would pass against a
        # build that emits no chip row at all, which is the finding an
        # adversarial test-quality review raised, and a first attempt to fix
        # it asserted a non-empty row the fixture never built.
        a_bank_line(
            seed_user, statement, posted_on=day - timedelta(days=10),
            sequence_in_group=1,
        )

        page = reconcile_page(a_scope(seed_user), None, Tab.TO_EXPLAIN)

        assert page.books_bound is not None
        assert page.books_bound.count == 1
        assert page.books_bound.last_day == day
        assert "Restate" in page.books_bound.restatement_act
        # **The chip row is asserted NON-EMPTY first**, or the negative below
        # is vacuous: with no transfers, no pre-calendar lines and no accepted
        # acts the row is empty and ``all()`` passes against a build that
        # emits no chips at all.  Measured by adversarial test-quality review
        # 2026-08-31.  The pay-calendar bound is what puts one there.
        assert page.chips, "the negative below needs a chip row to be about"
        assert all(
            "opening balance" not in chip.label for chip in page.chips
        )


class TestTheGridBadgeAgreesWithTheScreen:
    """A figure and its caption may not disagree."""

    def test_a_line_the_books_cannot_hold_is_not_counted(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the badge would promise work no screen offers.

        ``awaiting_review_count`` applies exactly the predicates the review
        splits on and no others; a third predicate added to one and not the
        other is how a badge comes to sit permanently non-zero.
        """
        day = _the_calendars_first_day(db, seed_user)
        a_bank_line(seed_user, an_import(seed_user), posted_on=day)

        assert awaiting_review_count(
            seed_user["user"].id, seed_user["account"].id, day,
        ) == 0

    def test_it_still_equals_proposals_plus_unmatched(
        self, app, db, seed_user,
    ):
        """The invariant the badge exists to keep, with BOTH kinds present.

        **The fixture stages a row for the later line to be PROPOSED against,
        and the proposal set is asserted non-empty.**  A first version staged
        two bank lines and nothing else, so ``review.proposals`` was empty and
        the equality reduced to ``1 == 0 + 1`` -- a claim about a sum with one
        term, over a docstring saying both kinds were present.  Measured by
        adversarial test-quality review 2026-08-31.

        **The lines are counted through the PROPOSALS' own line tuples**, not
        by ``len(proposals)``: one proposal can name several lines, so the
        count of proposals is not the count of lines the badge is comparing
        against.  ``test_awaiting_count.py``'s sibling invariant already
        spells it this way.
        """
        day = _the_calendars_first_day(db, seed_user)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, posted_on=day, sequence_in_group=0)
        a_bank_line(
            seed_user, statement, posted_on=day + timedelta(days=1),
            sequence_in_group=1,
        )
        # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
        # developer-confirmed 2026-09-22 ("Yes: open books before rows"; this
        # module's option: the row in the SECOND paycheck).  The books open ON
        # the calendar's first day -- this module's subject -- and since ruling
        # R-PC85 a recurring row dated on that day is inside them and never
        # generated, so the row the lines match against is filed a paycheck on.
        a_transaction(
            seed_user, name="Duke Energy", amount="180.00",
            period=a_later_period(seed_user),
        )
        db.session.flush()

        review = review_set(a_scope(seed_user))
        counted = awaiting_review_count(
            seed_user["user"].id, seed_user["account"].id, day,
        )
        proposed_lines = {
            line.line_id
            for proposal in review.proposals for line in proposal.lines
        }

        assert review.proposals, (
            "this case is about proposals PLUS unmatched; with none staged "
            "the equality below has only one term"
        )
        # ``unmatched`` holds the PARKED lines too since plan step
        # ``bank_import:X-gm``, so this union is the inbox only where none is
        # parked -- which this fixture stages.  Adding one here would fail
        # loudly rather than pass for the wrong reason.
        assert counted == len(proposed_lines) + len(review.unmatched)
        assert counted == 1
