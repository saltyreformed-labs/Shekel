"""Where the comparison begins: the books' opening day, its identity, and days not covered."""

from datetime import date, timedelta
from decimal import Decimal

from app.services import bank_agreement, cash_ledger
from app.services.balance_at import BalanceContext
from app.services.scenario_resolver import get_baseline_scenario
from tests._test_helpers import create_account_of_type
from tests.test_services.test_outstanding_difference import _assert_balance, _opened_on, _settle
from tests.test_services.test_statement_import.test_anchor import _seed_import

from ._helpers import _FILE_CHAIN, _ZERO, _agreement, _day


class TestDaysBeforeTheRecordsBeginAreLabelledNotCounted:
    """Finding N-314 is the balance arc's, not this report's.

    "Before the records begin" means ON OR BEFORE the day the books open
    (ruling **R-HG**, :func:`~app.utils.books_boundary.books_hold`); the
    seeded account's books open 2024-01-04, the day before its origination
    assertion, so both cases below date their lines before that.
    """

    def test_a_day_before_the_books_open_is_out_of_the_totals(
        self, app, seed_user, seed_periods, db,
    ):
        """The developer's own shape: months of statement before the books.

        The bank's line is SHOWN -- hiding a day the bank describes would be
        the report deciding what may be seen -- and excluded from the counts,
        because the app holding nothing that far back is not a disagreement
        this arc can act on.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            before = walk.anchor_facts[0].observed_on - timedelta(days=5)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=before, evidence=_FILE_CHAIN,
                lines=[(before, "-77.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)
            row = _day(agreement, before)

            # The case is about a day the books do not cover, so it must be one.
            assert before < walk.opening.opened_on
            # Before the books open they state neither a movement nor a
            # balance, so both are ABSENT rather than 0.00 (rulings R-BAL286,
            # R-BAL291) and the day is not compared.
            assert row.recorded is None
            assert row.app_balance is None
            assert row.compared is False
            assert row.bank_lines == Decimal("-77.00")
            assert agreement.compared == []
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO


    def test_a_statement_ENTIRELY_before_the_records_compares_nothing(
        self, app, seed_user, seed_periods, db,
    ):
        """Every day is outside the records, so every total is zero.

        The far edge of the developer's own shape: his statement reaches 83
        days before his records, and a statement reaching back further still
        would reach before ALL of them.  The report must then say it compared
        nothing rather than report the whole span as disagreement -- which is
        the difference between "your books are wrong by $15,000" and "your
        books do not go back this far".
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            begins = walk.anchor_facts[0].observed_on
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=begins - timedelta(days=10),
                evidence=_FILE_CHAIN,
                lines=[(begins - timedelta(days=20), "-500.00"),
                       (begins - timedelta(days=10), "-77.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.days != []
            assert agreement.compared == []
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO
            assert agreement.bank_ahead == _ZERO
            assert agreement.asserted_total == _ZERO
            assert agreement.constant_offset is None


class TestTheComparisonStartsTheDayAfterTheBooksOpen:
    """Finding **BAL-616**, plan step balance:X-f3c-2b-2c, rulings **R-BAL285** / **R-BAL291**.

    The books hold MOVEMENT from the day AFTER the one they open on (ruling
    **R-HG**: the opening equity is that day's closing balance) -- the days
    :func:`~app.utils.books_boundary.books_hold` admits -- so on an imported
    day the comparison takes exactly those.  It started at the account's
    first CASH FACT until this step, which was wrong in BOTH directions, and
    each case below is one of them: the first is the day a books-side
    boundary one day EARLY would count, the second the day one a day LATE
    would drop.  Every figure is made up.
    """

    def test_a_bank_line_ON_the_opening_day_is_inside_the_opening(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-02-02; the bank's ``-25.00`` that day is not counted.

        The production shape: ``account_service.create_account`` writes the
        opening and the origination assertion on ONE day, so that assertion
        was the first cash fact and the old boundary put the opening day
        itself into the totals -- a ``+25.00`` residue (``0.00`` recorded less
        ``-25.00`` banked) for money the ``$500.00`` opening already holds.
        Feb 4's ``-40.00`` has its row and agrees, so the comparison is not
        empty and the only thing that could disagree is the opening day.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 2, 2))
            _seed_import(
                db, account,
                lines=[(date(2026, 2, 2), "-25.00"),
                       (date(2026, 2, 4), "-40.00")],
            )
            _settle(
                seed_user, seed_periods[2], "40.00", date(2026, 2, 4),
                account=account,
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )
            opening_day = _day(agreement, date(2026, 2, 2))

            assert agreement.books_opened_on == date(2026, 2, 2)
            # Shown, never hidden: the bank's line is on the row.
            assert opening_day.bank_lines == Decimal("-25.00")
            # Inside the opening: the books hold no movement for it.
            assert opening_day.recorded is None
            assert opening_day.compared is False
            assert [day.day for day in agreement.compared] == [
                date(2026, 2, 3), date(2026, 2, 4),
            ]
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO
            assert agreement.bank_ahead == _ZERO

    def test_a_books_day_BEFORE_the_first_entry_is_compared(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-01-01; the first entry is a row on Jan 3.

        ``create_account_of_type`` restates the books back to 2026-01-01,
        before the owner's calendar, while the first thing recorded is the
        ``10.00`` row settled on Jan 3.  The bank's ``-40.00`` on Jan 2 has no
        row: the books claim Jan 2, so that is a real disagreement -- a
        ``+40.00`` residue, ``0.00`` recorded less ``-40.00`` banked.  The old
        boundary started at Jan 3 and never compared Jan 2 at all, so the
        ``$40.00`` was shown on its row and counted in no total.
        """
        with app.app_context():
            late = create_account_of_type(
                seed_user, db.session, "Checking", "Late Checking",
                anchor_balance=Decimal("500.00"),
            )
            _seed_import(
                db, late,
                lines=[(date(2026, 1, 2), "-40.00"),
                       (date(2026, 1, 3), "-10.00")],
            )
            _settle(
                seed_user, seed_periods[0], "10.00", date(2026, 1, 3),
                account=late,
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                late, BalanceContext.build(seed_user["user"].id),
            )
            day_after_open = _day(agreement, date(2026, 1, 2))

            assert agreement.books_opened_on == date(2026, 1, 1)
            assert day_after_open.compared is True
            assert day_after_open.recorded == _ZERO
            assert day_after_open.residue == Decimal("40.00")
            assert [day.day for day in agreement.disagreeing] == [
                date(2026, 1, 2),
            ]
            assert agreement.app_ahead == Decimal("40.00")
            assert _day(agreement, date(2026, 1, 3)).agrees is True


class TestTheBooksStateABalanceFromTheirOpeningDay:
    """Ruling **R-BAL286**, plan step balance:X-f3c-2b-2c.

    The books open at the CLOSE of their opening day (ruling **R-HG**), so
    their BALANCE is known from that day's close and their MOVEMENT from the
    day after.  So the opening day is compared for its balance and not for its
    movement, and a day before it has no books balance at all.  The shape is
    the natural first import: an account created on Feb 2
    from a statement ending Feb 2, whose bank states ``$500.00`` that day.  Its
    lines are Jan 30 ``-25.00`` and Feb 2 ``-10.00``, both inside the opening.
    Every figure is made up; they are the ones the ruling's question showed.
    """

    def _first_import(self, seed_user, db, typed):
        """Create the account at *typed* on Feb 2 and import the statement.

        Args:
            seed_user: The seeded user bundle.
            db: The database fixture.
            typed: The opening balance the owner typed, as a string.

        Returns:
            The new account.
        """
        account = _opened_on(seed_user, date(2026, 2, 2), typed)
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 2, 2),
            evidence=_FILE_CHAIN,
            lines=[(date(2026, 1, 30), "-25.00"),
                   (date(2026, 2, 2), "-10.00")],
            file_name="first.csv",
        )
        return account

    def test_the_opening_day_is_the_HEADLINE_and_earlier_days_state_no_balance(
        self, app, seed_user, seed_periods, db,
    ):
        """Feb 2 shows $500.00 against $500.00, off by $0.00.

        The headline and standing gap are the same on the app before plan
        step balance:X-f3c-2b-2c: the origination assertion made Feb 2 the
        first cash fact, so it was compared.  *What that app did differently
        here: the days before Feb 2 showed a "Your balance" the books never
        stated (now none), and Feb 2's ``-10.00`` line, inside the opening,
        counted as a ``+10.00`` disagreement (now not compared).*
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "500.00")
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert agreement.books_opened_on == date(2026, 2, 2)
            headline = agreement.headline
            assert headline.day == date(2026, 2, 2)
            assert headline.bank_balance == Decimal("500.00")
            assert headline.app_balance == Decimal("500.00")
            # 500.00 - 500.00.
            assert headline.gap == _ZERO
            assert agreement.standing_gap == _ZERO
            for day in (date(2026, 1, 30), date(2026, 1, 31),
                        date(2026, 2, 1)):
                assert _day(agreement, day).app_balance is None
                assert _day(agreement, day).gap is None
            # The MOVEMENT half is unchanged: every drawn day is on or before
            # the opening, so nothing is compared and nothing disagrees.
            assert agreement.compared == []
            assert agreement.disagreeing == []

    def test_a_TYPO_in_the_opening_shows_on_its_first_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Typed $510.00 against the bank's $500.00: off by +$10.00 on Feb 2.

        Positive is the books standing higher,
        :attr:`~app.services.bank_agreement.AgreementDay.gap`'s own
        convention.  *The app before plan step balance:X-f3c-2b-2c showed the
        same +10.00 here, beside a +10.00 disagreement on Feb 2's line that
        the opening already holds.*
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "510.00")
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert agreement.headline.day == date(2026, 2, 2)
            # 510.00 - 500.00.
            assert agreement.headline.gap == Decimal("10.00")
            assert agreement.standing_gap == Decimal("10.00")
            assert agreement.compared == []

    def test_the_opening_day_counts_toward_a_CONSTANT_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """The typo, then a later statement: +$10.00 on Feb 2 AND Feb 3.

        The later statement's one line is Feb 3's ``-40.00``, entered, and it
        states ``$460.00``.  Books: ``510.00`` on Feb 2, ``470.00`` on Feb 3;
        bank: ``500.00`` and ``460.00`` -- apart by ``+10.00`` both days, the
        signature of a wrong starting figure.  The days before Feb 2 are not
        among the days the offset is taken over: the books state no balance
        there.  *The app before plan step balance:X-f3c-2b-2c reported the
        same +10.00 offset here, over the same two days, because the
        origination assertion made Feb 2 its first cash fact; what changed is
        that books restated back before their first cash fact now count their
        opening day too.*
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "510.00")
            _settle(
                seed_user, seed_periods[2], "40.00", date(2026, 2, 3),
                account=account,
            )
            _seed_import(
                db, account, stated="460.00", effective_on=date(2026, 2, 3),
                evidence=_FILE_CHAIN, lines=[(date(2026, 2, 3), "-40.00")],
                file_name="later.csv",
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert _day(agreement, date(2026, 2, 2)).gap == Decimal("10.00")
            assert _day(agreement, date(2026, 2, 3)).gap == Decimal("10.00")
            assert agreement.constant_offset == Decimal("10.00")
            # Feb 3's row matches its line, so the movement half is clean.
            assert [day.day for day in agreement.compared] == [
                date(2026, 2, 3),
            ]
            assert agreement.disagreeing == []

    def test_a_books_day_the_bank_cannot_PRICE_withholds_the_constant(
        self, app, seed_user, seed_periods, db,
    ):
        """The constant fixture plus a third import that states no balance.

        Feb 2 and Feb 3 are apart by ``+10.00`` exactly as above.  A third
        statement's one line is Feb 6's ``-5.00``, entered, and it places no
        balance, so no anchor reaches Feb 4 - Feb 6: the books state a
        balance on each of those days and the bank prices none.  The offset
        was not seen to hold there, so it is not called constant -- reading
        only the priced days would report ``+10.00`` over a span it never
        checked.
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "510.00")
            for amount, day in (("40.00", date(2026, 2, 3)),
                                ("5.00", date(2026, 2, 6))):
                _settle(
                    seed_user, seed_periods[2], amount, day,
                    account=account,
                )
            _seed_import(
                db, account, stated="460.00", effective_on=date(2026, 2, 3),
                evidence=_FILE_CHAIN, lines=[(date(2026, 2, 3), "-40.00")],
                file_name="later.csv",
            )
            _seed_import(
                db, account, stated=None,
                lines=[(date(2026, 2, 6), "-5.00")], file_name="unpriced.csv",
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert _day(agreement, date(2026, 2, 2)).gap == Decimal("10.00")
            assert _day(agreement, date(2026, 2, 3)).gap == Decimal("10.00")
            for day in (date(2026, 2, 4), date(2026, 2, 5), date(2026, 2, 6)):
                assert _day(agreement, day).app_balance is not None
                assert _day(agreement, day).bank_balance is None
            assert agreement.constant_offset is None


class TestTheOpeningDifferencePlusEveryDaysMovementIsTheClosingDifference:
    """The identity that says the two halves of the boundary are one boundary.

    Ruling **R-BAL286** asked for it by name.  Inside one priced run, each
    day's change in the gap is that day's residue plus its correction -- the
    books moved ``recorded + asserted``, the bank moved ``bank_lines``, and
    ``residue = recorded - bank_lines`` -- so the gap on the LAST day equals
    the gap on the OPENING day plus the sum over the days after it.  It holds
    exactly when the level is read FROM the opening day and the movement from
    the day AFTER: a level starting a day late has no opening gap to start
    from, and a movement starting on the opening day adds that day's lines
    twice, once inside the opening and once as a residue.
    """

    def test_the_gaps_telescope_from_the_opening_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open Feb 2 at a typed $510.00; the bank says $500.00.

        Bank lines: Feb 2 ``-10.00`` (inside the opening), Feb 3 ``-40.00``
        (entered), Feb 4 ``-25.00`` (NOT entered), Feb 5 ``-5.00`` (entered);
        it states ``$430.00`` on Feb 5, so it walks back to 435.00, 460.00 and
        500.00.  The owner declares ``$440.00`` on Feb 4, a correction of
        ``440.00 - 470.00 = -30.00``.  Books: 510.00, 470.00, 440.00, 435.00.

        * Gaps: Feb 2 ``+10.00``, Feb 3 ``+10.00``, Feb 4 ``+5.00``, Feb 5
          ``+5.00``.
        * Feb 3: residue ``0.00``.  Feb 4: residue ``0.00 - (-25.00) =
          +25.00``, correction ``-30.00``.  Feb 5: residue ``0.00``.
        * ``+10.00 + (0.00) + (25.00 - 30.00) + (0.00) = +5.00``, Feb 5's gap.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 2, 2), "510.00")
            _seed_import(
                db, account, stated="430.00", effective_on=date(2026, 2, 5),
                evidence=_FILE_CHAIN,
                lines=[(date(2026, 2, 2), "-10.00"),
                       (date(2026, 2, 3), "-40.00"),
                       (date(2026, 2, 4), "-25.00"),
                       (date(2026, 2, 5), "-5.00")],
            )
            for day, amount in ((date(2026, 2, 3), "40.00"),
                                (date(2026, 2, 5), "5.00")):
                _settle(
                    seed_user, seed_periods[2], amount, day,
                    account=account,
                )
            _assert_balance(
                seed_user, db, "440.00", date(2026, 2, 4), account=account,
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )
            opening = _day(agreement, agreement.books_opened_on)

            # One run, priced on every drawn day, so the walk is continuous.
            assert len(agreement.runs) == 1
            assert all(day.bank_balance is not None for day in agreement.days)
            assert opening.gap == Decimal("10.00")
            assert _day(agreement, date(2026, 2, 4)).asserted == Decimal(
                "-30.00",
            )
            assert agreement.days[-1].gap == Decimal("5.00")
            # The level is read ON the opening day and the movement over the
            # days the report COMPARES, each from the producer itself -- so a
            # level starting a day late has no opening gap, and a movement
            # starting on the opening day adds its +10.00 residue.
            assert [day.day for day in agreement.compared] == [
                date(2026, 2, 3), date(2026, 2, 4), date(2026, 2, 5),
            ]
            assert agreement.days[-1].gap == opening.gap + sum(
                (day.residue + day.asserted for day in agreement.compared),
                _ZERO,
            )


class TestADayNoImportCoversIsNotCompared:
    """Finding **BAL-650**, ruling **R-BAL291**, plan step balance:X-f3c-2b-2c.

    The bank covers a day's MOVEMENT only inside the window an import
    declares.  A drawn day between two imports used to read ``0.00`` for the
    bank and be compared against it, so a row the owner entered there counted
    as money the bank's total "ran ahead" on, which the bank never said.  It
    is shown now, with the books' movement and no bank movement, and counted
    in no total.
    """

    def test_a_row_between_two_imports_is_shown_and_never_counted(
        self, app, seed_user, seed_periods, db,
    ):
        """The ruling's own example: 5 compared days, 1 disagreeing.

        Books open Jan 1 (``create_account_of_type`` restates them back).
        Statement A: Dec 30 ``-20.00``, Jan 1 ``-25.00``, Jan 2 ``-40.00``
        (never entered), Jan 5 ``-10.00`` (entered).  Statement B: Jan 20
        ``-15.00`` (entered).  A ``30.00`` purchase is entered on Jan 10, and
        nothing is imported for Jan 6-19.  Compared: Jan 2-5 (inside A, after
        the opening) and Jan 20 (B), so 5; Jan 2 disagrees by ``0.00 -
        (-40.00) = +40.00``, your total running ahead of your bank's.  Jan 10
        is not compared, so the bank's total ran ahead by nothing.  *On the
        app before plan step balance:X-f3c-2b-2c: 16 compared (Jan 5-20, from
        the first entry on, the gap included), Jan 10 disagreeing by -30.00,
        the bank ahead by $30.00, and Jan 2 not compared at all.*
        """
        with app.app_context():
            late = create_account_of_type(
                seed_user, db.session, "Checking", "Late Checking",
                anchor_balance=Decimal("500.00"),
            )
            _seed_import(
                db, late,
                lines=[(date(2025, 12, 30), "-20.00"),
                       (date(2026, 1, 1), "-25.00"),
                       (date(2026, 1, 2), "-40.00"),
                       (date(2026, 1, 5), "-10.00")],
                file_name="a.csv",
            )
            _seed_import(
                db, late, lines=[(date(2026, 1, 20), "-15.00")],
                file_name="b.csv",
            )
            for period, amount, day in (
                (seed_periods[0], "10.00", date(2026, 1, 5)),
                (seed_periods[0], "30.00", date(2026, 1, 10)),
                (seed_periods[1], "15.00", date(2026, 1, 20)),
            ):
                _settle(seed_user, period, amount, day, account=late)
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                late, BalanceContext.build(seed_user["user"].id),
            )
            jan_10 = _day(agreement, date(2026, 1, 10))

            assert agreement.books_opened_on == date(2026, 1, 1)
            # Shown with the books' movement; the bank's is unknown, not 0.00.
            assert jan_10.recorded == Decimal("-30.00")
            assert jan_10.bank_lines is None
            assert jan_10.residue is None
            assert jan_10.compared is False
            # A quiet day inside statement A IS the bank saying nothing moved.
            assert _day(agreement, date(2026, 1, 3)).bank_lines == _ZERO
            assert [day.day for day in agreement.compared] == [
                date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4),
                date(2026, 1, 5), date(2026, 1, 20),
            ]
            assert [day.day for day in agreement.disagreeing] == [
                date(2026, 1, 2),
            ]
            assert agreement.app_ahead == Decimal("40.00")
            assert agreement.bank_ahead == _ZERO
