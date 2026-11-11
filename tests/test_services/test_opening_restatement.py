"""An owner can say when the books opened, and with how much (plan step X-f3c-2b-2a).

**The rule, in one sentence.** ``budget.account_openings`` is append-only and
its latest recorded row governs, so correcting what an account's books opened
with is a NEW row -- and every balance the app renders for that account is
stacked on the figure it names.

**What each class here covers, and why the split is where it is.**

* :class:`TestTheWriter` -- the ONE writer, which both events reach: an
  origination (``account_service.create_account``) and a restatement.  Ruling
  **R-ES** applied one table over, so the owner's write lock, ruling **R-EQ**'s
  did-this-change compare and the audit line are properties of the TABLE.
* :class:`TestTheDaysItRefuses` -- the two bounds a restatement has, and the
  one it deliberately does NOT have.  The movement bound is where this suite
  earns its keep: the service has to refuse EXACTLY what
  ``budget.assert_account_books_hold_its_movements`` refuses, or a submission
  passes the door and aborts at COMMIT with a driver message no surface
  can render.
* :class:`TestTheTwoTiersAgree` -- that equality, graded rather than asserted
  in prose.  Both tiers read ONE SQL statement
  (:data:`app.opening_infrastructure.SETTLED_MOVEMENTS_SQL`); these cases plant
  the rows the two could most plausibly disagree about -- a SOFT-DELETED
  movement (which the balance fold ignores and the constraint counts) and a
  posted PURCHASE (the other half of the movement UNION) -- and pin that
  they do not.
* :class:`TestThePostedLedgerFollows` -- the half a reader would assume and
  should not.  The opening's ledger line is DATED on ``opened_on``, so moving
  the day moves it: the ledger holds it on the new day and nothing on the old.

**Why the movement row set is graded from the WIDE side.** A predicate that
narrowed to the fold's contributing rows would look right and be wrong: a
soft-deleted movement is restored by an ``UPDATE`` of ``is_deleted`` alone, and
the movement trigger fires ``UPDATE OF settled_on, account_id``, so a restored
pre-books row would pass every tier untouched.  The cost is over-refusal, which
is the safe direction, and it is only safe if both tiers over-refuse the SAME
rows.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import (
    AccountOpeningSourceEnum,
    LedgerAccountKindEnum,
    PostingSourceEnum,
    SettledDayBasisEnum,
)
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app.models.transaction_entry import TransactionEntry
from app.services import match_withdrawal, movement_removal
from app.services.cash_ledger import (
    account_opening_fact,
    earliest_assertion_day,
    earliest_recorded_movement_day,
    governing_account_opening,
)
from app.services.opening_service import (
    AmortizingAccountOpeningError,
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.services.settle_day import SettleDay, record_settle_day
from app.utils.dates import display_today
from app.services.ledger_lines import account_counter_key, linked_key
from tests._ledger_reads import scenario_lines, source_lines
from tests._test_helpers import (
    account_never_asserted,
    create_account_of_type,
    create_settled_cash_transaction,
    figure_source_columns,
    match_two_lines,
    refused_by_database_rule,
)

_ONE_DAY = timedelta(days=1)

#: Every ``budget.account_openings`` row an account carries, oldest RECORDING
#: first, read straight out of SQL.  The append-only claim is about rows that
#: SURVIVE a restatement, so the count and the order are read rather than
#: inferred from what the loader hands back.
_ALL_OPENINGS = (
    "SELECT id, opened_on, opening_equity, source_id FROM "
    "budget.account_openings WHERE account_id = :a ORDER BY created_at, id"
)

#: The constraint's own governing-day function, asked directly.  Going through
#: it rather than re-writing its ``ORDER BY`` here is the point: a hand-written
#: query would agree with the Python loader while the function the CONSTRAINT
#: calls disagreed with both.
_OPENED_ON_IN_SQL = "SELECT budget.account_books_opened_on(:a)"


def _openings(account):
    """Return *account*'s opening rows as tuples, oldest recorded first."""
    return _db.session.execute(
        sa.text(_ALL_OPENINGS), {"a": account.id},
    ).fetchall()


def _cash_account(seed_user, name, *, anchor_balance=Decimal("1000.00")):
    """Return a fresh Checking account with a known opening.

    **It already carries TWO opening rows, and a test must count from there
    rather than from one.**  ``create_account_of_type`` writes the origination
    and then re-opens the books before anything the fixture could date
    (``open_books_before_the_first_assertion``), because an opening equity is
    the closing balance for its own day and every settle the fixture then
    records has to land after it.  Baselines here are therefore RELATIVE:
    :func:`_opening_count` before, compared after.
    """
    return create_account_of_type(
        seed_user, _db.session, "Checking", name,
        anchor_balance=anchor_balance,
    )


def _opening_count(account):
    """Return how many opening rows *account* carries right now."""
    return len(_openings(account))


def _linked_ledger_total(scenario_id, account, source, entry_date=None):
    """Return the net of the account's OWN line for one posting source.

    **Only the linked key, never the counter**, and that distinction is what a
    first version of these cases got wrong: an anchor correction books a
    balanced PAIR, and both legs name the same account, so summing over the
    account returns ``0.00`` for every state and the assertion passes on the
    broken tree and on the fixed one alike.

    Args:
        scenario_id: The scenario.
        account: The account whose own line to total.
        source: The :class:`~app.enums.PostingSourceEnum` member to restrict to.
        entry_date: One civil day, or ``None`` for every day.

    Returns:
        The signed ``Decimal`` net of those lines.
    """
    return sum(
        (
            line.amount
            for line in source_lines(_db.session, linked_key(account), scenario_id, source)
            if entry_date is None or line.entry_date == entry_date
        ),
        Decimal("0.00"),
    )


class TestTheWriter:
    """One writer, two events, and the rules that belong to the TABLE."""

    def test_a_restatement_APPENDS_and_the_superseded_row_survives(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The correction is a new row; what the books used to say is still there.

        The whole of why this table is append-only: an UPDATE would make a
        restatement invisible to the app -- ``system.audit_log`` would hold it,
        but nothing could render it, which is the gap finding **N-205** built
        the balance-history card to close.
        """
        with app.app_context():
            account = _cash_account(seed_user, "Appending")
            before = account_opening_fact(account.id)
            standing = _opening_count(account)
            new_day = before.opened_on - _ONE_DAY

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(new_day, Decimal("742.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            rows = _openings(account)
            assert len(rows) == standing + 1
            # The superseded row, unchanged in both of its facts.
            superseded = next(row for row in rows if row.id == before.opening_id)
            assert superseded.opened_on == before.opened_on
            assert Decimal(str(superseded.opening_equity)) == (
                before.opening_equity
            )
            # The restatement governs.
            governing = account_opening_fact(account.id)
            assert governing.opening_id == rows[-1].id
            assert governing.opened_on == new_day
            assert governing.opening_equity == Decimal("742.00")

    def test_a_restatement_is_USER_DECLARED_whatever_it_replaced(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The door cannot write ``migration_derived``, and that is the point.

        A ``migration_derived`` figure is the pre-X-f3c-2a inference frozen;
        the column exists so a surface can tell a guess from an observation
        (findings **N-275**, **N-379**).  A door that let an owner mark their
        own figure derived would erase the distinction it exists for.
        """
        with app.app_context():
            account = _cash_account(seed_user, "Declared")
            # Plant the production shape: the standing row is the migration's.
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=account_opening_fact(account.id).opened_on,
                opening_equity=Decimal("1.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.MIGRATION_DERIVED,
                ),
            ))
            db.session.flush()
            assert account_opening_fact(account.id).source_id == (
                ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.MIGRATION_DERIVED,
                )
            )

            apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    account_opening_fact(account.id).opened_on,
                    Decimal("2.00"),
                ),
            )

            assert account_opening_fact(account.id).source_id == (
                ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                )
            )

    def test_restating_what_already_stands_writes_NOTHING(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling **R-EQ**: idempotent success, not an error and not a row.

        A restatement naming the day and the figure already in force is what
        already stands.  Appending it anyway would grow the history by a row
        saying nothing, on a table whose whole purpose is that every row is a
        statement somebody made.
        """
        with app.app_context():
            account = _cash_account(seed_user, "Idempotent")
            standing = account_opening_fact(account.id)
            before = _opening_count(account)
            db.session.commit()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    standing.opened_on, standing.opening_equity,
                ),
            )

            assert outcome is OpeningRestatementOutcome.UNCHANGED
            assert _opening_count(account) == before
            assert account_opening_fact(account.id).opening_id == (
                standing.opening_id
            )

    def test_CONFIRMING_a_derived_figure_promotes_its_provenance(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The guess that turns out RIGHT must still stop being a guess.

        Found by adversarial review 2026-08-31: the did-this-change compare
        was over ``(opened_on, opening_equity)`` alone, so an owner who opened
        their bank statement, confirmed the migration's figure was correct and
        submitted the pre-filled form got "Nothing was changed" -- and the row
        stayed ``migration_derived`` forever, with the card's "derived, not
        stated" badge and its "Nobody stated this figure ... so it can be
        wrong" paragraph standing on a figure a human had just verified.  There
        is no other door that clears it.

        The door exists because "a figure a surface flags as a guess with no
        way to replace it is a defect, not a caption"; handling only the guess
        being WRONG leaves the caption lying about the seven production
        openings the X-f3c-2a migration derived.
        """
        with app.app_context():
            account = _cash_account(seed_user, "Confirmed")
            standing = account_opening_fact(account.id)
            derived = ref_cache.account_opening_source_id(
                AccountOpeningSourceEnum.MIGRATION_DERIVED,
            )
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=standing.opened_on,
                opening_equity=standing.opening_equity,
                source_id=derived,
            ))
            db.session.flush()
            before = _opening_count(account)

            # Byte-for-byte what the pre-filled form submits.
            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    standing.opened_on, standing.opening_equity,
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert _opening_count(account) == before + 1
            promoted = account_opening_fact(account.id)
            assert promoted.source_id == ref_cache.account_opening_source_id(
                AccountOpeningSourceEnum.USER_DECLARED,
            )
            # The two facts it did NOT change, so the case cannot pass by the
            # door having rewritten the figure instead of its provenance.
            assert promoted.opened_on == standing.opened_on
            assert promoted.opening_equity == standing.opening_equity

    def test_the_DAY_alone_changing_is_a_restatement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The compare is over BOTH facts, so moving one is a change.

        Paired with :meth:`test_the_EQUITY_alone_changing_is_a_restatement` so
        a compare written over the equity alone -- or over the day alone --
        fails exactly one of the two rather than passing both.
        """
        with app.app_context():
            account = _cash_account(seed_user, "DayOnly")
            standing = account_opening_fact(account.id)
            before = _opening_count(account)

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    standing.opened_on - _ONE_DAY, standing.opening_equity,
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert _opening_count(account) == before + 1

    def test_the_EQUITY_alone_changing_is_a_restatement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other half of the pair above."""
        with app.app_context():
            account = _cash_account(seed_user, "EquityOnly")
            standing = account_opening_fact(account.id)

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    standing.opened_on,
                    standing.opening_equity + Decimal("0.01"),
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(account.id).opening_equity == (
                standing.opening_equity + Decimal("0.01")
            )

    def test_create_account_writes_its_opening_through_THIS_writer(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The origination reaches the same door, which is ruling R-ES's shape.

        Graded through the writer's own OUTPUT rather than by asserting the
        call: the factory's row has to be indistinguishable from one this
        module wrote, because it IS one.
        """
        with app.app_context():
            account = _cash_account(
                seed_user, "Originated", anchor_balance=Decimal("321.00"),
            )
            rows = _openings(account)
            # The factory writes the origination and then re-opens the books
            # before anything it could date, so the figure travels forward
            # unchanged across both -- which is what makes the EQUITY the
            # thing to assert on rather than the row count.
            assert rows
            assert all(
                Decimal(str(row.opening_equity)) == Decimal("321.00")
                for row in rows
            )
            assert all(
                row.source_id == ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                )
                for row in rows
            )

    def test_governing_account_opening_answers_NONE_where_the_reader_raises(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """"No opening yet" is honest to a writer and a broken invariant to a reader.

        The pair that lets ONE query serve both policies, exactly as
        ``governing_anchor_on`` serves ``resolve_anchor``'s.  Without it the
        writer would have to catch the reader's ``RuntimeError`` -- which is
        the state ``account_service.create_account`` is in every time it runs.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Openingless",
            )
            db.session.flush()

            assert governing_account_opening(account.id) is None
            with pytest.raises(RuntimeError, match="zero"):
                account_opening_fact(account.id)


class TestTheDaysItRefuses:
    """The two bounds a restatement has, and the one it deliberately has not."""

    def test_a_FUTURE_day_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An opening equity is the CLOSE of its day, and tomorrow has no close.

        Refused here as well as at the assertion door so a RESTATEMENT cannot
        reach a state CREATION cannot: ``resolve_observation_day`` already
        refuses a future day for the origination beside it.
        """
        with app.app_context():
            account = _cash_account(seed_user, "Futured")
            before = _opening_count(account)
            tomorrow = display_today() + _ONE_DAY
            with pytest.raises(ValidationError, match=r"not happened yet"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(tomorrow, Decimal("1.00")),
                )
            assert _opening_count(account) == before

    def test_TODAY_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The boundary's other side, so a ``<`` written for ``<=`` fails.

        Paired with the case above: today HAS a close, and an account with no
        movement recorded may legitimately open its books on it.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="OpenToday",
            )
            db.session.flush()
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=display_today() - _ONE_DAY,
                opening_equity=Decimal("5.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.flush()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(display_today(), Decimal("5.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(account.id).opened_on == display_today()

    def test_the_day_a_movement_is_recorded_ON_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling **R-HG** from the OPENING side: that money would count twice.

        The ``>=`` half.  Paired with the case below so neither can pass while
        the comparison is off by one in either direction.
        """
        with app.app_context():
            account = _cash_account(seed_user, "OnTheMovement")
            txn = create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("25.00"),
                account=account, name="A movement",
            )
            db.session.flush()

            with pytest.raises(ValidationError, match=r"already records money"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(txn.settled_on, Decimal("1.00")),
                )

    def test_the_day_BEFORE_the_earliest_movement_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The last legal day, so the bound is pinned from both sides."""
        with app.app_context():
            account = _cash_account(seed_user, "JustBefore")
            txn = create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("25.00"),
                account=account, name="A movement",
            )
            db.session.flush()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    txn.settled_on - _ONE_DAY, Decimal("1.00"),
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_a_day_AFTER_an_asserted_balance_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The bound the first two rules did not see, and it is a MONEY defect.

        Found by code review 2026-08-31 and reproduced on the developer's own
        Roth IRA before it was fixed.  An account with no settled movement is
        unbounded by the movement rule -- and that is every investment,
        retirement and property account in production -- so the books could be
        restated forward past every balance the owner had recorded.

        Measured on that account, six assertions from 2026-03-31 to 2026-07-16,
        restated to 2026-08-01 at ``$100.00``: ACCEPTED, ``unrealized_change``
        moved from ``-$4,523.33`` to ``-$27,332.35`` -- ``$22,809.02`` of
        investment return that never happened -- and the ``$100.00`` opening
        was read by nothing, because the earliest assertion RESETS the fold
        above it.
        """
        with app.app_context():
            account = _cash_account(seed_user, "PastAnAssertion")
            first = earliest_assertion_day(account.id)
            assert first is not None, "the fixture asserted no balance"
            before = _opening_count(account)

            with pytest.raises(
                ValidationError, match=r"already recorded a balance",
            ):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(first + _ONE_DAY, Decimal("1.00")),
                )
            assert _opening_count(account) == before

    def test_the_day_OF_the_earliest_assertion_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Equality is legal, and it is the ORDINARY case rather than an edge.

        ``account_service.create_account`` writes the origination opening and
        the origination assertion for the SAME day, so every account starts
        life exactly here -- three of the developer's nine still sit there.
        Refusing equality would make the factory's own output unrestatable.

        Paired with the case above so the comparison cannot be off by one in
        either direction: a ``>=`` written for ``>`` fails this one and a
        deleted bound fails that one.
        """
        with app.app_context():
            account = _cash_account(seed_user, "OnTheAssertion")
            first = earliest_assertion_day(account.id)

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first, Decimal("55.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(account.id).opened_on == first

    def test_it_is_NOT_bounded_by_the_owners_pay_calendar(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The bound the plan says this door must NOT have.

        ``pay_period_service.earliest_recordable_day`` is a rule about
        ASSERTIONS (ruling **R-ER**) -- it exists because an assertion opens the
        modelled-return window -- and applying it here would make an account's
        books unopenable before the owner's calendar begins, which is the
        common case for a real bank account.  The opening's ledger line needs
        no period of its own: ``PayCalendar.filing_period`` CLAMPS a
        pre-calendar day onto the earliest period rather than refusing it.
        """
        with app.app_context():
            account = _cash_account(seed_user, "PreCalendar")
            before_the_calendar = seed_periods[0].start_date - timedelta(
                days=365,
            )

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(before_the_calendar, Decimal("11.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(account.id).opened_on == (
                before_the_calendar
            )

    def test_an_AMORTIZING_account_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A loan's opening is its original principal, so this row is unread.

        Refused rather than written, because a write here would report success
        and move no figure -- ``balance_at.balance_at`` dispatches a configured
        loan onto the amortization replay and never reads this table.  The twin
        of ``anchor_service.AmortizingAccountAnchorError``.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, db.session, "Mortgage", "A Loan",
                anchor_balance=Decimal("-1000.00"),
            )
            before = _opening_count(loan)
            with pytest.raises(AmortizingAccountOpeningError):
                apply_opening_restatement(
                    account=loan,
                    opening=BooksOpening(display_today(), Decimal("-900.00")),
                )
            assert _opening_count(loan) == before


def _deleted_as_the_door_leaves_it(seed_user, db, period, account, name):
    """A settled $25.00 row, its payment taken off, then hidden: a delete's result.

    The row keeps its own recorded day, which is the movement the boundary
    counts; the removal act runs first because the database refuses a row
    hidden while it holds a payment (ruling **R-CC92**).

    Returns:
        The hidden row, holding nothing.
    """
    txn = create_settled_cash_transaction(
        seed_user, period, Decimal("25.00"),
        account=account, name=name,
    )
    movement_removal.remove_movements(
        list(txn.entries), seed_user["user"].id,
        because=match_withdrawal.LEFT_THE_BOOKS,
        press=None,
    )
    txn.is_deleted = True
    db.session.flush()
    assert not txn.entries and txn.settled_on is not None
    return txn


class TestTheTwoTiersAgree:
    """The service refuses EXACTLY what the database constraint refuses.

    Graded rather than trusted, and the reason is the failure mode: a service
    predicate NARROWER than the trigger's lets a submission through the door
    and aborts it at COMMIT with a driver message no surface can render,
    while a WIDER one refuses acts the database would allow.  Both tiers read
    one SQL statement (``opening_infrastructure.SETTLED_MOVEMENTS_SQL``); these
    cases plant the rows they could most plausibly disagree about.
    """

    def test_a_SOFT_DELETED_movement_still_bounds_the_books(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The balance fold ignores it; the boundary counts it, both tiers.

        Narrowing to the fold's row set would open a hole on RESTORE:
        un-deleting is an ``UPDATE`` of ``is_deleted`` alone and the movement
        trigger fires ``UPDATE OF settled_on, account_id``, so a restored
        pre-books row would pass every tier untouched.

        Built as the delete leaves it (ruling **R-CC75**): its payment taken
        off through the one removal act, then the row hidden, keeping its own
        recorded day -- the database refuses a row hidden while it still
        holds one (ruling **R-CC92**), which this flagged with its payment
        inside and never committed.  Re-expressed under rule 5,
        developer-confirmed 2026-09-23.
        """
        with app.app_context():
            account = _cash_account(seed_user, "SoftDeleted")
            txn = _deleted_as_the_door_leaves_it(
                seed_user, db, seed_periods[1], account, "Deleted movement",
            )
            settled_on = txn.settled_on
            db.session.commit()

            assert earliest_recorded_movement_day(account.id) == settled_on
            with pytest.raises(ValidationError, match=r"already records money"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(settled_on, Decimal("1.00")),
                )

    def test_a_POSTED_PURCHASE_bounds_the_books_too(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The OTHER half of the movement union, which one table alone hides.

        The constraint's row set is ``budget.transactions`` UNION
        ``budget.transaction_entries`` -- a settled transaction and a purchase
        whose bank posting day the owner recorded are ONE kind of fact to the
        fold (ruling **R-FM**).  A suite that only ever planted transactions
        would pass with the entries arm of that UNION deleted, which is the
        gap this case exists to close: the purchase here is dated EARLIER than
        its own parent, so the answer can only come from the entries table.

        Deliberately NOT the Cancelled status, which is the case a first draft
        of this file graded.  Leaving the settled band calls
        ``record_settle_day(row, None)`` and CLEARS the day
        (``status_seam._seam``), so a Cancelled row carrying a ``settled_on``
        is a legacy shape no door can produce -- an arm grading an impossible
        state, which is this project's own "a green gate can be measuring
        nothing".
        """
        with app.app_context():
            account = _cash_account(seed_user, "PostedPurchase")
            opened_on = account_opening_fact(account.id).opened_on
            parent = create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("40.00"),
                account=account, name="Envelope",
                settled_on=opened_on + timedelta(days=5),
            )
            purchase_day = opened_on + timedelta(days=2)
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=parent.id,
                account_id=account.id,
                owner_id=parent.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="A posted purchase",
                purchased_on=purchase_day,
                is_credit=False,
            )
            db.session.add(entry)
            db.session.flush()
            record_settle_day(
                entry,
                SettleDay(day=purchase_day, basis=SettledDayBasisEnum.OBSERVED),
            )
            db.session.flush()

            # Earlier than the PARENT's own settle day, so only the entries
            # arm of the union can be answering.
            assert earliest_recorded_movement_day(account.id) == purchase_day
            assert purchase_day < parent.settled_on
            with pytest.raises(ValidationError, match=r"already records money"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(purchase_day, Decimal("1.00")),
                )

    def test_the_DATABASE_refuses_a_soft_deleted_movement_too(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The tier the rest of this class does NOT reach.

        Adversarial review, 2026-08-31: the two cases above assert
        ``earliest_recorded_movement_day`` and then assert the service refuses
        -- and the service's refusal IS a call to that same function, so
        neither asks the DATABASE anything.  The class is named for the two
        tiers agreeing; this is the half that was missing, and a soft-deleted
        movement is graded at the database tier nowhere else in the repo.

        It goes around the door on purpose -- a direct ``AccountOpening``
        INSERT -- because the point is what the deferred constraint trigger
        does when the service is not there to have refused first.  The refusal
        arrives at COMMIT, which is why the flush alone is not enough.  The
        row is built as the delete leaves it, as the case above says why.
        """
        with app.app_context():
            account = _cash_account(seed_user, "DatabaseTier")
            txn = _deleted_as_the_door_leaves_it(
                seed_user, db, seed_periods[1], account, "Soft-deleted movement",
            )
            settled_on = txn.settled_on
            db.session.commit()

            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=settled_on,
                opening_equity=Decimal("1.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            with refused_by_database_rule(r"counted twice"):
                db.session.commit()
            db.session.rollback()

    def test_the_service_refuses_BEFORE_the_database_has_to(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The pairing's whole purpose: a sentence, not a COMMIT abort.

        ``ValidationError`` is a 400 a date box renders verbatim; the trigger's
        ``RAISE EXCEPTION`` arrives as a driver error at COMMIT, after the
        request has already decided it succeeded.  Asserting the TYPE is what
        distinguishes the two, and asserting the session is clean afterwards is
        what proves the refusal came first.
        """
        with app.app_context():
            account = _cash_account(seed_user, "ServiceFirst")
            txn = create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("25.00"),
                account=account, name="A movement",
            )
            db.session.flush()
            db.session.commit()
            before = _opening_count(account)

            with pytest.raises(ValidationError, match=r"already records money"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(txn.settled_on, Decimal("1.00")),
                )

            # Nothing staged, so the transaction is still usable -- which it
            # would not be had the database raised.
            assert _opening_count(account) == before
            assert db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar() == account_opening_fact(account.id).opened_on

    def test_the_LATER_ROW_governs_even_when_its_instant_is_EARLIER(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The governing order is ``id``, and ``created_at`` would invert it.

        **The defect this pins is a silent no-op on the level every balance
        rests on** (adversarial review, 2026-08-31).  The order led on
        ``created_at`` until then, justified as "set by the database on INSERT
        and therefore monotone in recording order".  It is not:
        :class:`~app.models.mixins.CreatedAtMixin` defaults it to
        ``db.func.now()``, and PostgreSQL's ``now()`` is
        ``transaction_timestamp()`` -- the instant the transaction BEGAN.
        ``loan_anchor_service._governing_loan_anchor`` already said so about the
        loan twin; this table never carried it across.

        Two tabs produce it: B's transaction opens, A's opens later, A takes
        the owner's write lock first and commits, B then blocks, appends, and
        commits SECOND carrying the EARLIER instant.  Under ``created_at DESC``
        B's restatement sorts below the row it was meant to supersede -- the
        owner is told "Books restated" and nothing moves.

        **It is SIMULATED rather than raced**, because the two orders are
        indistinguishable on anything a single transaction writes: every row
        shares one ``now()`` there, so ``id`` carries the whole answer and a
        reverted build passes.  The instants are supplied explicitly, inverted
        against the ids, exactly as ``test_books_boundary``'s own
        ``_record_two_restatements`` does.
        """
        with app.app_context():
            account = _cash_account(seed_user, "InvertedInstants")
            source_id = account_opening_fact(account.id).source_id
            db.session.commit()

            # Two appends, ids ascending, instants DESCENDING: the second row
            # is the one the owner recorded last and the one that must govern.
            db.session.execute(sa.text("""
                INSERT INTO budget.account_openings
                       (account_id, opened_on, opening_equity, source_id,
                        created_at)
                VALUES (:a, :first_day, 111.00, :s, now() + interval '2 second'),
                       (:a, :second_day, 222.00, :s, now() + interval '1 second')
            """), {
                "a": account.id,
                "s": source_id,
                "first_day": date(2026, 1, 5),
                "second_day": date(2026, 1, 6),
            })
            db.session.commit()

            rows = _openings(account)
            newest = max(rows, key=lambda row: row.id)
            assert newest.opened_on == date(2026, 1, 6), (
                "the fixture did not write the rows in the order this case "
                "needs"
            )
            oldest_of_the_pair = min(
                (row for row in rows if row.opened_on in {
                    date(2026, 1, 5), date(2026, 1, 6),
                }),
                key=lambda row: row.id,
            )
            assert oldest_of_the_pair.opened_on == date(2026, 1, 5)

            # BOTH tiers must name the LATER-INSERTED row.  Under
            # ``created_at DESC`` both would name 2026-01-05 instead.
            assert account_opening_fact(account.id).opened_on == (
                date(2026, 1, 6)
            )
            assert db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar() == date(2026, 1, 6)

    def test_the_python_reader_and_the_SQL_function_name_one_row(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """After a restatement both tiers agree which row governs.

        The two order by ``(created_at DESC, id DESC)`` from one stated
        constant (``opening_infrastructure.GOVERNING_ORDER_SQL``); a
        restatement recorded in the same transaction as the row it supersedes
        shares its ``now()``, so only ``id`` can break the tie and the two
        implementations have to break it the same way.
        """
        with app.app_context():
            account = _cash_account(seed_user, "OneGoverningRow")
            standing = account_opening_fact(account.id)
            apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    standing.opened_on - _ONE_DAY, Decimal("77.00"),
                ),
            )

            in_sql = db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar()
            assert in_sql == account_opening_fact(account.id).opened_on
            assert in_sql == standing.opened_on - _ONE_DAY


class TestThePostedLedgerFollows:
    """The half a reader would assume, measured instead.

    ``budget.account_openings`` is read by BOTH the balance fold and the
    ledger (ruling **R-GX**), and the ``account_opening`` line is DATED on
    ``opened_on``.  Moving either fact therefore moves that line, and a ledger
    that kept it on the old day would describe an opening the app no longer
    holds.
    """

    def test_the_opening_entry_moves_to_the_new_day_and_the_old_one_nets_to_zero(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The opening books on its new day and holds nothing on its old one.

        The account's OWN line sums to the new equity at the new day and to
        nothing at the old one, rather than to both.
        """
        with app.app_context():
            account = _cash_account(
                seed_user, "LedgerFollows", anchor_balance=Decimal("400.00"),
            )
            db.session.commit()
            old_day = account_opening_fact(account.id).opened_on
            new_day = old_day - _ONE_DAY

            apply_opening_restatement(
                account=account,
                opening=BooksOpening(new_day, Decimal("650.00")),
            )

            # The account's OWN leg, day by day.  Summing over ``account_id``
            # instead would fold the balanced counter leg in beside it and
            # read 0.00 on every state -- broken tree and fixed one alike.
            scenario_id = seed_user["scenario"].id
            opening = PostingSourceEnum.ACCOUNT_OPENING
            assert _linked_ledger_total(
                scenario_id, account, opening, new_day,
            ) == Decimal("650.00")
            assert _linked_ledger_total(
                scenario_id, account, opening, old_day,
            ) == Decimal("0.00")
            # And ACROSS every day: the opening books exactly once.
            assert _linked_ledger_total(
                scenario_id, account, opening,
            ) == Decimal("650.00")

    def test_the_opening_books_its_counter_to_ANCHOR_EQUITY(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The counter leg names what the difference WAS (ruling **R-FO**).

        An opening is contributed capital, so its counter is ``anchor_equity``
        -- never ``interest_income`` or ``unrealized_change``, which are what a
        TRUE-UP books on an interest-bearing or modelled account.  A
        restatement that re-keyed the entry but let the dispatch fall through
        to the true-up branch would leave the balance right and put the whole
        opening on the income statement as a day-one return, which is the
        defect R-FO exists to prevent.

        **This case replaced an app-wide trial-balance assertion that could not
        fail** (adversarial review, 2026-08-31): while the door wrote the store,
        ``ck_account_postings_balanced``, a deferred per-entry constraint, aborted
        its COMMIT on a single-legged emission, so the sum was identically zero
        whenever a commit succeeded.  It graded "the restatement commits", which
        the case above already grades with figures.
        """
        with app.app_context():
            account = _cash_account(
                seed_user, "CounterLeg", anchor_balance=Decimal("400.00"),
            )
            db.session.commit()
            new_day = account_opening_fact(account.id).opened_on - _ONE_DAY

            apply_opening_restatement(
                account=account,
                opening=BooksOpening(new_day, Decimal("650.00")),
            )

            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            counters = {
                line.key: line.amount
                for line in scenario_lines(db.session, seed_user["scenario"].id)
                if line.source_kind_id == opening_source
                and line.key.record_id == account.id
                and line.key != linked_key(account)
            }

            # The linked line is +650.00 (asserted above), so its counter is
            # -650.00 and it lands on the equity key and nowhere else.
            assert counters == {
                account_counter_key(
                    LedgerAccountKindEnum.ANCHOR_EQUITY, account.id,
                ): Decimal("-650.00"),
            }

    def test_a_restatement_re_bases_the_LATER_assertions_correction(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Moving the level moves every correction stacked on it.

        This is what the door's own message warns the owner about, measured:
        the account's assertions still say what they said, so raising the
        opening by ``$100.00`` drops the first assertion's correction by the
        same ``$100.00`` rather than being absorbed.  An owner told nothing
        would read the unchanged balance as the door having failed.
        """
        with app.app_context():
            account = _cash_account(
                seed_user, "Rebased", anchor_balance=Decimal("400.00"),
            )
            db.session.commit()
            standing = account_opening_fact(account.id)

            def _trueup_total():
                return _linked_ledger_total(
                    seed_user["scenario"].id, account, PostingSourceEnum.ACCOUNT_TRUEUP,
                )

            # The ABSOLUTES, hand-computed, because a delta whose two sides
            # come from one producer read twice passes on a build carrying a
            # constant offset on both.  The account opens at $400.00 and its
            # origination assertion says $400.00, so the true-up's correction
            # is 400.00 - 400.00 = 0.00.
            assert _trueup_total() == Decimal("0.00")

            apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    standing.opened_on,
                    standing.opening_equity + Decimal("100.00"),
                ),
            )

            # Raising the opening to $500.00 leaves the assertion saying
            # $400.00, so the correction becomes 400.00 - 500.00 = -100.00.
            # That is the sentence the door's own message warns about.
            assert _trueup_total() == Decimal("-100.00")


class TestTheDoorRefusesAMatchedLineItWouldSwallow:
    """The DOOR asks the matched-line bound, not just the bound existing.

    Plan step **balance:X-f3c-2b-2b**.  **This class exists because a planted
    defect survived without it** (2026-08-31): deleting the door's call to
    ``cash_ledger.reject_books_open_on_or_after_matched_lines`` left the whole
    suite green, because the service suite graded the FUNCTION and the
    database suite graded the TRIGGER and nothing joined them.

    What that costs is precisely what the pairing exists to prevent: the
    constraint would still refuse the restatement, at COMMIT, as the
    driver's ``RaiseException`` -- so an owner typing a date would meet a
    500 instead of the sentence ``app.opening_infrastructure`` says an
    ordinary date box gets.
    """

    def test_the_door_gives_a_SENTENCE_not_a_commit_abort(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL for the door's own call.

        The account records NO movement, so the movement bound returns without
        counting anything and the matched-line bound is the only thing between
        the owner and a double count.  The refusal must be this module's
        ``ValidationError``, and the assertion on the type is the half that
        fails when the door stops asking: the trigger's abort is a database
        rule's refusal (SQLSTATE ``P0001``), not a designed one.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Door Matched",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()
            early = opened + timedelta(days=10)
            match_two_lines(
                db.session, account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )
            assert earliest_recorded_movement_day(account.id) is None, (
                "this case isolates the MATCHED-LINE bound; a movement would "
                "make the bound beside it refuse first"
            )
            before = _opening_count(account)

            with pytest.raises(
                ValidationError, match="matched a bank line",
            ):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(
                        early + timedelta(days=5), Decimal("10.00"),
                    ),
                )

            db.session.rollback()
            assert _opening_count(account) == before

    def test_a_day_BELOW_the_matched_line_still_restates(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The bound is a bound: the door still accepts a legal day.

        Without this the case above passes against a door that refuses every
        restatement on an account that has ever matched a line, which is the
        mutation a single refusal case cannot see.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Door Matched Below",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()
            early = opened + timedelta(days=10)
            match_two_lines(
                db.session, account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    early - timedelta(days=1), Decimal("12.00"),
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(
                account.id,
            ).opened_on == early - timedelta(days=1)
