"""The drill-down: what makes up a day, and what is already explained."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import bank_agreement, transaction_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    add_entry,
    an_entered_day,
    generate_row_of,
    make_expense_template,
    make_income_template,
)
from tests.test_services.test_statement_import.test_anchor import _seed_import

from ._helpers import _FILE_CHAIN, _settled, _agreement, _day


class TestTheDrillDownNamesWhatMakesUpADay:
    """A number a reader can act on, rather than one they must go and chase."""

    def test_it_lists_both_sides_with_their_match_state(
        self, app, seed_user, seed_periods, db,
    ):
        """The 2026-06-02 shape again: six app rows, no bank line.

        Every row and every line is listed rather than only the unmatched
        ones -- a row matched to a line the bank posted on a DIFFERENT day
        contributes to both days' residues while being unmatched on neither.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Card payback", "100.00",
                date(2026, 3, 3),
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [line.amount for line in detail.lines] == [
                Decimal("-40.00"),
            ]
            assert detail.lines[0].matched is False
            assert [(row.description, row.amount) for row in detail.rows] == [
                ("Card payback", Decimal("-100.00")),
            ]
            assert detail.rows[0].matched is False

    def test_each_item_names_the_record_it_is(
        self, app, seed_user, seed_periods, db,
    ):
        """A line carries its statement line's id and a row its movement's.

        Plan step ``balance:X-bk-2`` (plan#1192): the reconcile's act sheet
        cancels or matches exactly the movement and the line this lists, so
        each item names its own record -- two lines of one day and figure, or
        two rows of one name, could not otherwise be told apart.  The second
        line and the second row share the first's day and figure, so an id
        read off the wrong record of a pair cannot pass.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00"),
                       (date(2026, 3, 3), "-40.00")],
            )
            first = _settled(
                db, seed_user, seed_periods[4], "Gas", "40.00",
                date(2026, 3, 3),
            )
            second = _settled(
                db, seed_user, seed_periods[4], "Gas", "40.00",
                date(2026, 3, 3),
            )
            db.session.commit()
            line_ids = [
                line_id for (line_id,) in db.session.query(
                    BankStatementLine.id,
                ).filter(
                    BankStatementLine.account_id == seed_user["account"].id,
                ).order_by(BankStatementLine.id)
            ]
            entry_ids = {
                movement.id
                for txn in (first, second)
                for movement in txn.covering_movements
            }

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            # The lines are listed in recording order (``_lines_on``), so the
            # ids read back in the order the statement recorded them.
            assert [line.line_id for line in detail.lines] == line_ids
            assert len(line_ids) == 2
            # The rows are ordered by size and the two tie, so the ids are
            # compared as a set: each row names one movement, and together
            # they name both.
            assert {row.entry_id for row in detail.rows} == entry_ids
            assert len(entry_ids) == 2

    def test_the_rows_it_lists_SUM_to_the_days_recorded_total(
        self, app, seed_user, seed_periods, db,
    ):
        """The items shown explain exactly the figure they sit under.

        Listing the account's transactions by ``settled_on`` instead of reading
        the walk would be a second statement of which rows count as settled
        cash and on what day, and a drill-down that did not add up is how a
        reader learns to stop trusting the number above it.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            for name, amount in (("A", "10.00"), ("B", "25.50"),
                                 ("C", "4.50")):
                _settled(
                    db, seed_user, seed_periods[4], name, amount,
                    date(2026, 3, 3),
                )
            # Rows on OTHER days, so the day filter is exercised.  Without
            # them every settled row in the account sat on the one day asked
            # about, and deleting ``fact.settled_on == day`` from ``_rows_on``
            # left the suite green -- the drill-down would list the whole
            # account under every day.  Found by adversarial review
            # 2026-08-24: setup that does not create the condition it names.
            _settled(
                db, seed_user, seed_periods[4], "Elsewhere", "99.00",
                date(2026, 3, 2),
            )
            _settled(
                db, seed_user, seed_periods[4], "Later", "77.00",
                date(2026, 3, 5),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            detail = bank_agreement.day_detail(
                seed_user["account"], ctx, date(2026, 3, 3),
            )
            row = _day(_agreement(seed_user), date(2026, 3, 3))

            assert sum(item.amount for item in detail.rows) == row.recorded
            assert sum(item.amount for item in detail.lines) == row.bank_lines
            assert {item.description for item in detail.rows} == {
                "A", "B", "C",
            }

    def test_a_day_with_nothing_on_it_answers_with_empty_lists(
        self, app, seed_user, seed_periods, db,
    ):
        """"Neither of us has anything" is an answer a reader asked for."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 9),
            )

            assert detail.lines == []
            assert detail.rows == []


class TestTheDrillDownSaysWhatIsALREADYEXPLAINED:
    """The match flag, and the envelope-purchase branch beside it."""

    def test_a_MATCHED_line_and_a_MATCHED_row_are_marked(
        self, app, seed_user, seed_periods, db,
    ):
        """Both flags, against a real accepted match.

        A FIRING control: with the flag hardcoded ``False`` the whole
        ``matched_subjects`` claim read, both template branches and their copy
        are dead, and the screen tells the owner nothing is explained when
        everything is.  The axis no other test in this package varies.

        The app-side member names the row's PAYMENT, its covering movement
        (rulings **R-CC43**, **R-CC45**): since plan step
        ``credit_card:CC-5-4a-2`` the member table has no row column, and
        this case named the row through it until then.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            txn = _settled(
                db, seed_user, seed_periods[4], "Groceries", "40.00",
                date(2026, 3, 3),
            )
            line = db.session.query(BankStatementLine).filter(
                BankStatementLine.account_id == seed_user["account"].id,
            ).one()
            match = StatementMatch(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                applied_by_rule=False,
            )
            db.session.add(match)
            db.session.flush()
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                bank_statement_line_id=line.id,
            ))
            (payment,) = txn.covering_movements
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                transaction_entry_id=payment.id,
            ))
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [line.matched for line in detail.lines] == [True]
            assert [row.matched for row in detail.rows] == [True]

    @pytest.mark.parametrize(
        ("is_income", "amount", "line_amount"),
        [(False, "40.00", "-40.00"), (True, "2572.78", "2572.78")],
        ids=["a covered bill", "a covered paycheck"],
    )
    def test_a_COVERED_row_matched_by_its_PAYMENT_reads_matched(
        self, app, seed_user, seed_periods, db, is_income, amount, line_amount,
    ):
        """The mirror's match state is its own: the act names the payment.

        Settled through the seam, a bill's (X-bi-3a) or a paycheck's
        (X-bi-3b) money walks as its covering movement's fact, and the match
        names that movement (rulings **R-CC43**, **R-CC45**), in either
        direction.  Until plan step ``credit_card:CC-5-4a-2`` this case named
        the ROW -- the shape the acts before ``CC-5-4a-1`` held, under which
        asking the entry's own claim alone read every matched bill and
        paycheck as unexplained here (adversarial review of X-bi-3b,
        2026-09-16); that migration re-keyed each such member onto the
        payment and dropped the row column, so the case now grades the one
        shape there is.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), line_amount)],
            )
            make = make_income_template if is_income else make_expense_template
            txn = generate_row_of(
                make(db.session, seed_user, amount=amount, name="Row"),
                seed_periods[4],
            )
            transaction_service.settle_transaction(
                txn, settle_day=an_entered_day(date(2026, 3, 3)),
            )
            db.session.flush()
            assert len(txn.covering_movements) == 1
            line = db.session.query(BankStatementLine).filter(
                BankStatementLine.account_id == seed_user["account"].id,
            ).one()
            match = StatementMatch(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                applied_by_rule=False,
            )
            db.session.add(match)
            db.session.flush()
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                bank_statement_line_id=line.id,
            ))
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                transaction_entry_id=txn.covering_movements[0].id,
            ))
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [row.amount for row in detail.rows] == [Decimal(line_amount)]
            assert [row.matched for row in detail.rows] == [True]

    def test_an_ENVELOPE_PURCHASE_is_named_from_its_ENTRY(
        self, app, seed_user, seed_periods, db,
    ):
        """The two-subject split, which no other test reaches.

        A purchase is a cash fact in its own right, carrying an ``entry_id``,
        so its name comes from ``transaction_entries.description`` and its
        match state from the ENTRY rather than the parent.  With the entry
        branch dead the row renders as ``(unnamed)`` -- and that branch is
        precisely what ``_rows_on``'s docstring names as the reason the lookup
        is keyed on the pair.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            envelope = _settled(
                db, seed_user, seed_periods[4], "Groceries", "100.00",
                date(2026, 3, 3),
            )
            add_entry(
                db.session, seed_user, envelope, Decimal("31.00"),
                date(2026, 3, 3), settled_on=date(2026, 3, 3),
                description="Food Lion",
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            named = {row.description for row in detail.rows}
            assert "Food Lion" in named
            assert "(unnamed)" not in named

    def test_the_biggest_movement_reads_FIRST(
        self, app, seed_user, seed_periods, db,
    ):
        """The item explaining most of a day's difference leads.

        Unpinned, the ordering is whatever the walk happened to produce, and
        the drill-down's whole job is putting the explanation in front of the
        reader.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            for name, amount in (("Small", "5.00"), ("Biggest", "900.00"),
                                 ("Middle", "50.00")):
                _settled(
                    db, seed_user, seed_periods[4], name, amount,
                    date(2026, 3, 3),
                )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [row.description for row in detail.rows] == [
                "Biggest", "Middle", "Small",
            ]
