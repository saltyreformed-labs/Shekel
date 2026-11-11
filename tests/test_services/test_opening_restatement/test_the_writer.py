"""The one restatement writer, and the days it refuses."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AccountOpeningSourceEnum
from app.exceptions import ValidationError
from app.models.account_opening import AccountOpening
from app.services.cash_ledger import (
    account_opening_fact,
    earliest_assertion_day,
    governing_account_opening,
)
from app.services.opening_service import (
    AmortizingAccountOpeningError,
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.utils.dates import display_today
from tests._test_helpers import (
    account_never_asserted,
    create_account_of_type,
    create_settled_cash_transaction,
)

from ._helpers import _ONE_DAY, _openings, _cash_account, _opening_count


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
