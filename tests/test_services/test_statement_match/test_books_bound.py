"""A bank line the account's books cannot hold is neither offered nor matched.

Plan step **balance:X-f3c-2b-2b**, finding **N-383**, ruling **balance:R-HG**.

An opening equity is the balance at the CLOSE of the day the books open, so
every dollar the bank moved on or before that day is inside the one figure.
Recording such a line -- as a purchase, as income, or as the evidence for
settling a row the owner already had -- counts that money twice.

**The two halves this module grades, and the second is a MONEY defect the
first would not have caught.**

* The review pass no longer OFFERS such a line: it reaches no card, no
  proposal and no create control, and what the screen says instead is a bound
  naming the opening and the restatement act.
* The three doors REFUSE it, which the offer split does not make redundant: a
  page rendered before a restatement moved the books forward posts line ids
  the current books no longer hold.  The match door is the sharp case --
  ``record_match`` settles every member on the LATEST of its bank days, so a
  GROUP holding one pre-opening line and one later line settles after the
  books open and clears ``reject_movement_before_books_open`` untouched.

**Measured on a restored production clone 2026-08-31, at this branch's
migration head, with the developer's own 378 SECU lines loaded**: Checking's
books open 2026-03-26 holding `$689.16`, which is the pay calendar's own first
day, so four lines post inside the opening.  Before this step two of them were
offered live controls with no withholding at all -- a `$2,573.42` payroll
deposit and a `-$108.87` Amazon swipe -- and the other two were PROPOSED
matches inside the one-click sweep.  Accepting a group of the 2026-03-26
`-$15.96` line with the 2026-08-17 `-$64.04` line against one `$80.00`
envelope was ACCEPTED and settled it on 2026-08-17.

**Why the fixture restates the books FORWARD.**  A factory account's books
open the day before the bootstrap pay period, so every line inside them is
also before the pay calendar and the calendar bound catches it first -- the
two bounds are disjoint and the calendar is applied first.  The shape that
exercises this one is the developer's own: books opening ON the calendar's
first day.  :func:`_books_open_on` goes through the table's ONE writer rather
than a raw ``INSERT``, which is the right habit -- but it is NOT evidence that
a door could produce the state, and a first version of this paragraph said it
was.  ``stage_account_opening`` explicitly does not bound the day; the DOOR
does.  What makes these fixtures production shapes is that the day chosen is
one ``apply_opening_restatement`` would accept, which is asserted where it
matters -- ``test_opening_restatement.py``'s own door cases.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import AccountOpeningSourceEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import account_service, opening_service
from app.services.opening_service import (
    BooksOpening,
    apply_opening_restatement,
)
from app.services.cash_ledger import (
    books_hold,
    earliest_matched_line_day,
    earliest_recorded_movement_day,
)
from app.services.statement_match import (
    accept_match,
    awaiting_review_count,
    create_purchase_from_line,
    file_new_swipes,
    IncomeCreation,
    MerchantAnswers,
    MintedEnvelopes,
    NewEnvelope,
    PurchaseCreation,
    reconcile_page,
    record_income_from_line,
    review_set,
    RuleView,
    Tab,
)

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_submission,
    a_transaction,
    a_rule,
    an_import,
)


def _books_open_on(db, seed_user, day, equity="1000.00"):
    """Restate the seeded account's books onto *day*, through the ONE writer.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.
        day: The civil day the books should open on.
        equity: What they open holding.

    Returns:
        *day*, so a caller can build lines around it in one expression.

    Note:
        The WRITER, not the door: ``stage_account_opening`` appends without
        bounding the day (``opening_service`` states that in as many words),
        so a caller here is responsible for choosing a day the door would take.
        Every day this module passes is one the account's own records allow.
    """
    opening_service.stage_account_opening(
        account=seed_user["account"],
        opening=opening_service.BooksOpening(
            opened_on=day, equity=Decimal(equity),
        ),
        source=AccountOpeningSourceEnum.USER_DECLARED,
    )
    db.session.flush()
    return day


def _the_calendars_first_day(db, seed_user):
    """Open the books ON the pay calendar's first day -- production's shape.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.

    Returns:
        That day.
    """
    return _books_open_on(
        db, seed_user, seed_user["bootstrap_period"].start_date,
    )


def _an_account_opening_inside_the_calendar(db, seed_user, days_in):
    """Return an account whose books open *days_in* days after the calendar.

    **Account 10's production shape**: Fidelity Money Market Savings opens
    2026-04-05 against a pay calendar that opens 2026-03-26, so ten whole days
    sit inside the calendar and inside the books at once.  The seeded checking
    account cannot be restated into that shape -- its origination assertion is
    on the calendar's first day and the books may never open after an
    assertion (:func:`~app.services.cash_ledger
    .reject_books_open_after_an_assertion`) -- so the account is created with
    a later ``observed_on`` instead, which is how the real one got there.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.
        days_in: How far into the calendar the books open.

    Returns:
        ``(account, opened_on)``.
    """
    opened_on = (
        seed_user["bootstrap_period"].start_date + timedelta(days=days_in)
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Fidelity Money Market Savings",
            anchor_balance=Decimal("1000.00"),
            observed_on=opened_on,
        ),
    )
    db.session.flush()
    return account, opened_on


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
        ``test_the_CREATE_door_ACCEPTS_a_swipe_made_before_the_books`` below.
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


class TestTheThreeDoorsRefuseIt:
    """The doors, which the offer split does not make redundant.

    A page rendered before a restatement moved the books forward posts line
    ids the current books no longer hold, so every one of these is reachable
    by an ordinary owner rather than only by a crafted request.

    **WHAT THESE CASES CATCH BETWEEN THEM, MEASURED BY MUTATION 2026-08-31**,
    and it is written here rather than in either arc document because the
    person it is for is the one deciding whether an EIGHTH case is needed.
    The CREATE door's refusal must fire before the writes it guards, and
    "before" is not one position -- there are two writes and the cases have
    DISJOINT fail sets:

    ==================================== ============ ==============
    refusal moved below                  MINT case    EXISTING case
    ==================================== ============ ==============
    ``resolve_destination``              FAILS        passes
    ``_born_purchase`` / ``close_``      FAILS        FAILS
    ==================================== ============ ==============

    So ``test_the_CREATE_door_refuses_and_MINTS_NO_ENVELOPE`` catches a
    refusal that has slipped past the MINT, and
    ``test_the_CREATE_door_refuses_an_EXISTING_destination_and_STAGES_NOTHING``
    catches one that has slipped past the WRITE -- **and the first cannot
    catch the second's mutation.**  The reason is that on the
    existing-destination path ``resolve_destination`` mints nothing, so no row
    is staged between those two positions for that case's assertion to see.
    Neither case alone pins the placement; the pair brackets it.

    **This was found by disbelieving a docstring rather than by a failure.**
    The EXISTING case shipped claiming it graded placement "as its siblings
    do"; the two mutations above measured that false, and a docstring that
    overstates what a case controls is worse than a missing case, because it
    retires the question.  An eighth case here should say which mutation it
    kills that these seven do not.
    """

    def test_the_CREATE_door_refuses_and_MINTS_NO_ENVELOPE(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the PLACEMENT of the refusal, not its existence.

        ``create_entry``'s settle verb refuses this day too -- but only after
        ``resolve_destination`` has staged the new budget line for a purchase
        that will never exist.  This door's own promise is that every refusal
        fires before anything is written, and the savepoint that would
        otherwise cover it belongs to the batch rather than to this door.
        """
        day = _the_calendars_first_day(db, seed_user)
        line = a_bank_line(seed_user, an_import(seed_user), posted_on=day)
        scope = a_scope(seed_user)

        with pytest.raises(
            ValidationError, match="Your bank posted this line on",
        ):
            create_purchase_from_line(
                PurchaseCreation(
                    line_id=line.id,
                    new_envelope=NewEnvelope(
                        name="Lowe's",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                ),
                scope,
                MintedEnvelopes.none_yet(),
                MerchantAnswers.build(seed_user["user"].id, seed_user["account"].id,),
                applied_by_rule=False,
            )

        assert db.session.query(Transaction).filter(
            Transaction.name == "Lowe's",
        ).count() == 0

    def test_the_CREATE_door_ACCEPTS_a_swipe_made_before_the_books(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the RULED posting-day reading, at the door.

        ``reject_line_before_books_open`` rules it: "the POSTING day and never
        the transaction day".  A card swiped BEFORE the books opened and TAKEN
        after is money that left the account after the opening, so it is
        recordable, and the purchase's ``purchased_on`` is a budget clock
        rather than a movement.

        The mutation this exists to kill: ``_create`` already computes
        ``_made_on(line)`` as ``line.transaction_on or line.posted_on`` for the
        purchase's own day, so asking the refusal that value instead of
        ``line.posted_on`` is a one-word edit that reads as a tidy-up.  It
        would make every swipe-before/post-after purchase silently
        unrecordable.  Every other case in this file leaves ``transaction_on``
        NULL, where the two are equal and the mutation is invisible.
        """
        # Books open INSIDE the calendar, so the swipe day can sit below them
        # and still fall in a pay period -- otherwise the door refuses for an
        # unrelated reason and the case would grade the calendar, not the
        # books.
        account, opened_on = _an_account_opening_inside_the_calendar(
            db, seed_user, days_in=10,
        )
        line = a_bank_line(
            seed_user, an_import(seed_user, account=account),
            posted_on=opened_on + timedelta(days=1),
            transaction_on=opened_on - timedelta(days=3),
        )

        purchase = create_purchase_from_line(
            PurchaseCreation(
                line_id=line.id,
                new_envelope=NewEnvelope(
                    name="Swiped Early",
                    category_id=seed_user["categories"]["Groceries"].id,
                ),
            ),
            a_scope(seed_user, account=account),
            MintedEnvelopes.none_yet(),
            MerchantAnswers.build(seed_user["user"].id, account.id),
            applied_by_rule=False,
        )

        assert purchase is not None
        assert db.session.query(Transaction).filter(
            Transaction.name == "Swiped Early",
        ).count() == 1

    def test_the_CREATE_door_refuses_an_EXISTING_destination_and_STAGES_NOTHING(
        self, app, db, seed_user,
    ):
        """The EXISTING-destination path, which the two cases above do not reach.

        Added by ``pay_calendar:C4-a-4`` after this class merged with it
        (adversarial coordination, 2026-08-31).  Both CREATE cases beside it
        pass a :class:`NewEnvelope`, so both exercise the MINT path and
        neither ever selects an existing destination -- and destination
        SELECTION is exactly what that step rewrote (``destinations_for``
        scopes by the calendar's saved period ids where it scoped by
        ``pay_periods.user_id``).  So this door's placement promise was pinned
        on one of its two arms.

        **The state could not be built before that merge**, which is why the
        gap is a property of the harness rather than an oversight: the seeded
        account's books open on or before its calendar
        (``open_books_before_the_first_assertion`` takes a minimum that
        includes the owner's earliest pay period), so "a day a pay period
        covers AND before the books open" is EMPTY there and the calendar
        bound fires first.  It needs an account whose books open INSIDE the
        calendar, which is :func:`_an_account_opening_inside_the_calendar`.

        **What this case controls was MEASURED, and it is NOT what a first
        draft of this docstring claimed.**  It kills a refusal moved below
        ``_born_purchase`` -- the first thing this path stages -- and it does
        NOT kill one moved below ``resolve_destination``, which its mint-path
        sibling does.  The class docstring carries both mutations and both
        fail sets; the table is stated once, there.
        """
        account, opened_on = _an_account_opening_inside_the_calendar(
            db, seed_user, days_in=10,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            account=account,
        )
        line = a_bank_line(
            seed_user, an_import(seed_user, account=account),
            posted_on=opened_on,
        )

        with pytest.raises(
            ValidationError, match="Your bank posted this line on",
        ):
            create_purchase_from_line(
                PurchaseCreation(
                    line_id=line.id, transaction_id=envelope.id,
                ),
                a_scope(seed_user, account=account),
                MintedEnvelopes.none_yet(),
                MerchantAnswers.build(seed_user["user"].id, account.id),
                applied_by_rule=False,
            )

        db.session.flush()
        assert envelope.entries == []

    def test_that_SAME_envelope_takes_a_line_the_books_DO_hold(
        self, app, db, seed_user,
    ):
        """The firing control, and it is also the OVER-refusal check.

        Without it the case above passes for an envelope
        ``destinations_for`` never offered at all -- which is the failure
        direction ``pay_calendar:C4-a-4`` could plausibly have introduced,
        since it narrowed that producer's ownership clause from
        ``pay_periods.user_id`` to the calendar's own saved period ids.  Same
        account, same envelope, one day later: offered, and the purchase
        lands.
        """
        account, opened_on = _an_account_opening_inside_the_calendar(
            db, seed_user, days_in=10,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            account=account,
        )
        line = a_bank_line(
            seed_user, an_import(seed_user, account=account),
            posted_on=opened_on + timedelta(days=1),
        )

        recorded = create_purchase_from_line(
            PurchaseCreation(line_id=line.id, transaction_id=envelope.id),
            a_scope(seed_user, account=account),
            MintedEnvelopes.none_yet(),
            MerchantAnswers.build(seed_user["user"].id, account.id),
            applied_by_rule=False,
        )

        db.session.flush()
        assert recorded.transaction_id == envelope.id
        assert len(envelope.entries) == 1
    def test_a_STANDING_RULE_cannot_auto_file_a_deposit_before_the_books(
        self, app, db, seed_user,
    ):
        """The no-press path, and WHICH of two mechanisms holds it.

        **Two independent things guard this today and that is not redundancy
        until their fail sets are known.**  ``_gaps._split_at_books_open``
        takes a pre-books line OUT of the pass, so a rule never sees it; and
        this door's own refusal sits above the write, so a line that somehow
        reached it is refused.  Either alone would hold the line -- which means
        a regression in either is INVISIBLE until the day the other is also
        touched, and by then two changes are in flight and neither looks
        responsible.

        So this case pins the FIRST: it asserts the deposit never reaches
        ``recordable_inflows`` at all, and therefore that the filing pass files
        nothing and reports nothing about it.  Break the split and this fails
        even while the door still refuses.

        Its pair is :meth:`test_the_INCOME_door_refuses_and_WRITES_NO_ROW`,
        which pins the second by calling the door directly.
        """
        a_rule(
            seed_user, "Dividend Earned",
            income_category_id=seed_user["categories"]["Salary"].id,
        )
        day = _the_calendars_first_day(db, seed_user)
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="0.15", posted_on=day,
            merchant="Dividend Earned",
            description="DIVIDEND EARNED (Dividend Earned)",
        )
        db.session.commit()

        scope = a_scope(seed_user)
        review = review_set(scope)
        filing = file_new_swipes(scope, statement.id)
        db.session.flush()

        # THE MECHANISM: the line is not in the pass, so no rule can reach it.
        assert [
            inflow.line.line_id for inflow in review.recordable_inflows
        ] == []
        # ...and therefore nothing is filed and nothing is withheld: a line the
        # pass does not hold is not this pass declining to act on it.
        assert filing.filed_count == 0
        assert filing.withheld == ()
        assert tuple(filing.outcome.refused) == ()

    def test_the_INCOME_door_refuses_and_WRITES_NO_ROW(
        self, app, db, seed_user,
    ):
        """The `$2,573.42` payroll deposit's door, graded on PLACEMENT.

        ``mint_uncategorized`` writes the row and THEN settles it, so deleting
        this door's refusal does not make the act succeed: the settle verb
        refuses the same day one tier down, leaving a staged row for a
        savepoint that belongs to the batch rather than to this door.  That is
        the ordering the future-day refusal beside it was moved for.

        **Which is why this case asserts the door's OWN sentence and the empty
        table, and a first version of it did neither.**  It matched a phrase
        both refusals share, so deleting the door's refusal left it GREEN
        against the settle verb's -- measured by planting exactly that defect
        on 2026-08-31.  A refusal case that cannot tell which tier refused is
        not testing the tier it names.
        """
        day = _the_calendars_first_day(db, seed_user)
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="2573.42", posted_on=day,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        scope = a_scope(seed_user)

        with pytest.raises(
            ValidationError, match="Your bank posted this line on",
        ):
            record_income_from_line(
                IncomeCreation(line_id=line.id), scope,
                RuleView.build(scope.owner_id, scope.account_id),
            )

        assert db.session.query(Transaction).filter(
            Transaction.name.like("ACH DEPOSIT TOWN OF CLAYTON%"),
        ).count() == 0

    def test_a_GROUP_whose_EARLIEST_line_predates_the_books_is_refused(
        self, app, db, seed_user,
    ):
        """THE MONEY CASE, and the one no settle verb would have caught.

        Every member settles on the LATEST of the match's bank days, so this
        group settles thirty days after the books open and
        ``reject_movement_before_books_open`` passes -- while the earlier
        line's `$15.96` is already inside the opening equity.

        Reproduced on a production clone before the fix: lines of 2026-03-26
        (`-$15.96`) and 2026-08-17 (`-$64.04`) against one `$80.00` envelope
        were ACCEPTED and settled it on 2026-08-17, booking `$15.96` twice.
        """
        day = _the_calendars_first_day(db, seed_user)
        statement = an_import(seed_user)
        early = a_bank_line(
            seed_user, statement, amount="-15.96", posted_on=day,
            sequence_in_group=0,
        )
        later = a_bank_line(
            seed_user, statement, amount="-64.04",
            posted_on=day + timedelta(days=30), sequence_in_group=1,
        )
        # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
        # developer-confirmed 2026-09-22 ("Yes: open books before rows"; this
        # module's option: the row in the SECOND paycheck).  The books open ON
        # the calendar's first day -- this module's subject -- and since ruling
        # R-PC85 a recurring row dated on that day is inside them and never
        # generated, so the row the lines match against is filed a paycheck on.
        row = a_transaction(
            seed_user, name="Gas", amount="80.00",
            period=a_later_period(seed_user),
        )
        db.session.flush()
        scope = a_scope(seed_user)

        with pytest.raises(
            ValidationError, match="Your bank posted this line on",
        ):
            accept_match(
                a_submission(
                    scope, lines=[early, later], transactions=[row],
                ),
                scope,
            )

        assert db.session.get(Transaction, row.id).settled_on is None

    def test_a_group_entirely_AFTER_the_books_is_still_accepted(
        self, app, db, seed_user,
    ):
        """The refusal is a BOUND and not a blanket.

        Without this the case above passes against a match door that refuses
        every group, which is the mutation a single refusal case cannot see.
        The assertion on ``posts_on`` also states the mechanism the case above
        turns on: the members settle on the LATEST bank day.
        """
        day = _the_calendars_first_day(db, seed_user)
        statement = an_import(seed_user)
        first = a_bank_line(
            seed_user, statement, amount="-15.96",
            posted_on=day + timedelta(days=1), sequence_in_group=0,
        )
        second = a_bank_line(
            seed_user, statement, amount="-64.04",
            posted_on=day + timedelta(days=30), sequence_in_group=1,
        )
        # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
        # developer-confirmed 2026-09-22 ("Yes: open books before rows"; this
        # module's option: the row in the SECOND paycheck).  The books open ON
        # the calendar's first day -- this module's subject -- and since ruling
        # R-PC85 a recurring row dated on that day is inside them and never
        # generated, so the row the lines match against is filed a paycheck on.
        row = a_transaction(
            seed_user, name="Gas", amount="80.00",
            period=a_later_period(seed_user),
        )
        db.session.flush()
        scope = a_scope(seed_user)

        accepted = accept_match(
            a_submission(scope, lines=[first, second], transactions=[row]),
            scope,
        )
        db.session.flush()

        assert accepted.posts_on == day + timedelta(days=30)
        assert db.session.get(
            Transaction, row.id,
        ).settled_on == day + timedelta(days=30)


class TestTheBoundAgainstAMatchADOORMade:
    """The matched-line bound, over a match ``record_match`` actually wrote.

    **Every database-tier case builds its match with raw SQL**, which is right
    where the subject IS a trigger and wrong for a bound the DOOR applies: a
    door-side rule that has never seen the row shape a door leaves is
    asserting against a state it hopes resembles production.  ``record_match``
    writes SUBJECT members beside the line members and SETTLES the row it
    matched; the raw fixture does neither.

    Named by adversarial test-quality review 2026-08-31.
    """

    def test_the_books_cannot_be_restated_into_a_REAL_matches_gap(
        self, app, db, seed_user,
    ):
        """The mechanism and the bound, joined in ONE case.

        Two lines a month apart, matched through the real door against one
        row: the members settle on the LATER day, so the account's earliest
        recorded MOVEMENT is that later day while its earliest matched LINE is
        the earlier one.  Every day between them passes the movement bound and
        is refused by this one -- which is the whole claim of the arm,
        measured end to end rather than in two halves that never meet.
        """
        day = _the_calendars_first_day(db, seed_user)
        statement = an_import(seed_user)
        early = day + timedelta(days=1)
        late = early + timedelta(days=30)
        first = a_bank_line(
            seed_user, statement, amount="-15.96", posted_on=early,
            sequence_in_group=0,
        )
        second = a_bank_line(
            seed_user, statement, amount="-64.04", posted_on=late,
            sequence_in_group=1,
        )
        # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
        # developer-confirmed 2026-09-22 ("Yes: open books before rows"; this
        # module's option: the row in the SECOND paycheck).  The books open ON
        # the calendar's first day -- this module's subject -- and since ruling
        # R-PC85 a recurring row dated on that day is inside them and never
        # generated, so the row the lines match against is filed a paycheck on.
        row = a_transaction(
            seed_user, name="Gas", amount="80.00",
            period=a_later_period(seed_user),
        )
        db.session.flush()
        scope = a_scope(seed_user)

        accept_match(
            a_submission(scope, lines=[first, second], transactions=[row]),
            scope,
        )
        db.session.commit()

        # The MECHANISM: settled on the LATER day, so the movement bound sees
        # nothing before it and calls the whole gap legal.
        assert earliest_recorded_movement_day(
            seed_user["account"].id,
        ) == late
        assert earliest_matched_line_day(seed_user["account"].id) == early

        with pytest.raises(ValidationError, match="matched a bank line"):
            apply_opening_restatement(
                account=seed_user["account"],
                opening=BooksOpening(
                    early + timedelta(days=5), Decimal("10.00"),
                ),
            )
