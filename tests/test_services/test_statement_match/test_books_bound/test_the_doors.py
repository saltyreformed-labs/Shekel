"""A line the books cannot hold is refused: the three doors, and the bound over a match a door
made.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services.opening_service import BooksOpening, apply_opening_restatement
from app.services.cash_ledger import earliest_matched_line_day, earliest_recorded_movement_day
from app.services.statement_match import (
    accept_match,
    create_purchase_from_line,
    file_new_swipes,
    IncomeCreation,
    MerchantAnswers,
    MintedEnvelopes,
    NewEnvelope,
    PurchaseCreation,
    record_income_from_line,
    review_set,
    RuleView,
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

from ._helpers import _the_calendars_first_day, _an_account_opening_inside_the_calendar


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
