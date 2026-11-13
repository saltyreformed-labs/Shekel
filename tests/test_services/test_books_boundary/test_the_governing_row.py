"""The surviving books are what is graded, and the two governing lookups elect the same row."""

from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.account_opening import AccountOpening
from app.models.transaction_entry import TransactionEntry
from app.services.cash_ledger import account_opening_fact
from tests._test_helpers import (
    append_only_guard_lifted,
    create_account_of_type,
    create_settled_cash_transaction,
    figure_source_columns,
    refused_by_database_rule,
    restate_account_opening,
    settle_day_columns,
)

from ._helpers import (
    _ONE_DAY,
    _books_open_on,
    _REMOVE_ONE_OPENING,
    _COUNT_OPENINGS,
    _OPENED_ON_IN_SQL,
    _record_two_restatements,
)


class TestTheGoverningRowIsWhatIsGraded:
    """The openings side grades the SURVIVING books, not the row written.

    ``budget.assert_books_open_before_books_movements`` dispatches and
    ``budget.assert_account_books_hold_its_movements`` decides, and the
    predicate takes an ACCOUNT rather than a row.  That is what lets one
    statement of the rule cover an INSERT, a raw ``UPDATE`` and a raw
    ``DELETE`` -- and the delete is the case a row-oriented check cannot see at
    all, because the row that breaks the invariant is the one that SURVIVED.
    """

    def test_deleting_the_governing_restatement_cannot_strand_a_movement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Removing a restatement PROMOTES an older one, which may not reach.

        Hand-built so the promotion is the whole of what changes: the books are
        restated BACKWARD (legal, and the repair the refusal's own message
        recommends), a movement is then recorded in the span that restatement
        opened up, and the restatement is removed by raw SQL -- which the ORM
        refuses (``AccountOpening._block_delete``) and which is exactly why the
        arm lives in the database.  The older row's day is on or after the
        movement, so the account's books no longer hold what it records.
        """
        with app.app_context():
            account = seed_user["account"]
            original = _books_open_on(account)
            restate_account_opening(
                db.session, account, original - timedelta(days=10),
            )
            db.session.commit()
            restatement_id = account_opening_fact(account.id).opening_id

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=original - timedelta(days=5),
                name="inside-the-older-books",
            )
            db.session.commit()

            # **The append-only refusal is lifted for this statement, and
            # only for it** (plan step X-f3c-2c).  It stands in front of
            # the arm under test here and would answer first, which would
            # leave that arm graded by nothing -- see
            # ``append_only_guard_lifted`` for why the answer is to reach
            # past it rather than to delete a measured control.
            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.execute(
                    sa.text(_REMOVE_ONE_OPENING), {"i": restatement_id},
                )
                with refused_by_database_rule("cannot open its books"):
                    db.session.commit()
            db.session.rollback()

    def test_removing_one_that_does_NOT_govern_is_allowed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The negative half, so the arm above is not simply "no deletes".

        A superseded row decides nothing, so removing it changes no answer and
        the constraint has nothing to say.  Without this case the arm above
        would pass just as well against a trigger that refused every delete,
        which is a different and wrong rule.
        """
        with app.app_context():
            account = seed_user["account"]
            superseded_id = account_opening_fact(account.id).opening_id
            restate_account_opening(
                db.session, account, _books_open_on(account) - _ONE_DAY,
            )
            db.session.commit()
            governing = account_opening_fact(account.id)
            assert governing.opening_id != superseded_id

            # **The append-only refusal is lifted for this statement, and
            # only for it** (plan step X-f3c-2c).  It stands in front of
            # the arm under test here and would answer first, which would
            # leave that arm graded by nothing -- see
            # ``append_only_guard_lifted`` for why the answer is to reach
            # past it rather than to delete a measured control.
            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.execute(
                    sa.text(_REMOVE_ONE_OPENING), {"i": superseded_id},
                )
                db.session.commit()
            assert account_opening_fact(account.id).opening_id == (
                governing.opening_id
            )


    def test_disposing_of_the_ACCOUNT_cascades_its_books_away(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The DELETE arm cannot make an account undeletable.

        The case a new DELETE arm on a constraint trigger owes: a CASCADE
        removes several opening rows at once, each firing this trigger, and if
        the predicate graded the SURVIVING books against movements that were
        also being disposed of it would abort an ordinary disposal at COMMIT.

        **It cannot, and the reason is an FK asymmetry that this case PINS
        rather than assumes.**  ``budget.account_openings.account_id`` is ON
        DELETE CASCADE and ``budget.transactions.account_id`` is ON DELETE
        RESTRICT, so the two halves are asserted in the order they constrain:
        while a movement is on file the delete is refused by the FOREIGN KEY
        (an ``IntegrityError``, never this trigger's message -- if the books
        constraint were what refused it, that would be the bug this case is
        for); once the movement is gone the account and its several openings
        go together.  A first draft asserted only the second half against an
        account that had never recorded anything, which could not fail.

        The movement is a ONE-OFF since plan step ``balance:X-bi-7c`` -- a
        rule-less definition plus its placed row -- and the definition
        references the account too (``transaction_templates.account_id``,
        RESTRICT), so "with the movement gone" disposes of the definition
        with its last row, as the transaction delete verb does (ruling
        **R-BAL27**).
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Disposable",
                anchor_balance=Decimal("500.00"),
            )
            restate_account_opening(
                db.session, account, _books_open_on(account) - timedelta(days=5),
            )
            movement = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY,
                account=account, name="on-the-books",
            )
            db.session.commit()
            account_id, movement_id = account.id, movement.id
            definition_id = movement.template_id
            assert db.session.execute(
                sa.text(_COUNT_OPENINGS), {"a": account_id},
            ).scalar() >= 3, "the cascade must take several rows, not one"

            # While the movement is on file, the FOREIGN KEY refuses -- and it
            # is the FK rather than the books trigger, which is the half that
            # makes the arm above safe.
            with pytest.raises(sa.exc.IntegrityError) as refusal:
                db.session.execute(
                    sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                    {"i": account_id},
                )
                db.session.commit()
            assert "cannot open its books" not in str(refusal.value)
            db.session.rollback()

            # With it gone -- the row and the definition that was its -- the
            # account and its openings go together.
            # Its payments first, in the same raw SQL: a row holding one is no longer
            # deleted with it (R-CC54; rule-5 re-expression, developer-confirmed
            # 2026-09-23).
            db.session.execute(
                sa.text(
                    "DELETE FROM budget.transaction_entries "
                    "WHERE transaction_id = :i"
                ),
                {"i": movement_id},
            )
            db.session.execute(
                sa.text("DELETE FROM budget.transactions WHERE id = :i"),
                {"i": movement_id},
            )
            db.session.execute(
                sa.text("DELETE FROM budget.transaction_templates WHERE id = :i"),
                {"i": definition_id},
            )
            db.session.execute(
                sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                {"i": account_id},
            )
            db.session.commit()
            assert db.session.execute(
                sa.text(_COUNT_OPENINGS), {"a": account_id},
            ).scalar() == 0


    def test_moving_an_opening_BETWEEN_accounts_grades_the_one_it_left(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Both accounts an ``UPDATE`` touches are graded, not just the new one.

        The arm this pins is the whole reason
        ``budget.assert_account_books_hold_its_movements`` takes an ACCOUNT id
        instead of reading ``NEW``: one raw ``UPDATE`` can change which books
        govern TWO accounts, and the account it left is the one a
        ``NEW``-reading predicate never looks at.  The donor is left with an
        older restatement its own movements no longer fit, so a trigger that
        graded only ``NEW.account_id`` would commit this happily.

        No door does this -- the table is append-only and nothing reassigns an
        opening -- which is exactly why it belongs at the database tier, whose
        stated job is the writer nobody enumerated.
        """
        with app.app_context():
            donor = seed_user["account"]
            recipient = create_account_of_type(
                seed_user, db.session, "Savings", "Recipient",
                anchor_balance=Decimal("500.00"),
            )
            original = _books_open_on(donor)
            # The donor's books move BACK, a movement lands in the span that
            # opened up, and only the restatement keeps the two legal.
            restate_account_opening(
                db.session, donor, original - timedelta(days=10),
            )
            db.session.commit()
            moved_id = account_opening_fact(donor.id).opening_id
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=original - timedelta(days=5),
                name="held-by-the-restatement",
            )
            db.session.commit()

            # Hand that restatement to the OTHER account.  The recipient is
            # unaffected -- it records nothing that early -- so any refusal
            # must come from grading the account the row LEFT.
            # **The append-only refusal is lifted for this statement, and
            # only for it** (plan step X-f3c-2c).  It stands in front of
            # the arm under test here and would answer first, which would
            # leave that arm graded by nothing -- see
            # ``append_only_guard_lifted`` for why the answer is to reach
            # past it rather than to delete a measured control.
            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.execute(
                    sa.text(
                        "UPDATE budget.account_openings SET account_id = :b "
                        "WHERE id = :i"
                    ),
                    {"b": recipient.id, "i": moved_id},
                )
                with refused_by_database_rule("cannot open its books") as refusal:
                    db.session.commit()
            assert f"account {donor.id} " in str(refusal.value), (
                "the refusal must name the DONOR, which is the account a "
                "NEW-reading predicate would never have graded"
            )
            db.session.rollback()

    def test_the_PURCHASE_table_carries_the_constraint_too(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The second movement table is attached, graded at the DATABASE tier.

        ``apply_opening_infrastructure`` attaches the movement trigger to
        ``budget.transaction_entries`` as well as ``budget.transactions``, and
        every other case for the entries table goes through the Python
        refusal -- which would still pass if the attachment were dropped from
        ``_MOVEMENT_TABLES`` or silently failed to re-pin.  Purchases are
        where the bulk ``query.update()`` writer lives, so the tier that
        covers it is the one that has to be shown attached.

        Raw SQL on purpose: an ORM write would grade the service rule instead.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            parent = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("40.00"),
                settled_on=opened_on + timedelta(days=5), name="envelope",
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=parent.id,
                account_id=account.id,
                owner_id=parent.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="purchase",
                # BEFORE the books, which is legal: a purchase DATE says
                # when the card was swiped and only ``settled_on`` says when
                # cash moved.  It also has to precede the settle day the
                # UPDATE below writes, or
                # ``ck_transaction_entries_settled_not_before_purchase``
                # refuses first and this case would grade that instead.
                purchased_on=opened_on - _ONE_DAY,
                is_credit=False,
                **settle_day_columns(opened_on + timedelta(days=5)),
            )
            db.session.add(entry)
            db.session.commit()

            db.session.execute(
                sa.text(
                    "UPDATE budget.transaction_entries SET settled_on = :d "
                    "WHERE id = :i"
                ),
                {"d": opened_on, "i": entry.id},
            )
            with refused_by_database_rule("books open"):
                db.session.commit()
            db.session.rollback()


class TestTheTwoGoverningLookupsElectTheSameRow:
    """SQL and Python must not disagree about which restatement is in force.

    ``budget.account_books_opened_on`` decides what the constraint ENFORCES and
    :func:`app.services.cash_ledger.account_opening_fact` decides what the fold
    SEEDS AT.  They are two implementations of one rule (ruling **R-HE**: the
    latest RECORDING instant governs, ``id`` breaking a same-instant tie), and
    nothing but this class holds them to the same answer -- a disagreement
    would let the app render a balance from a level the database is
    simultaneously refusing to let it record against.
    """

    def test_they_agree_when_the_recording_order_contradicts_the_days(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Restatements recorded in the REVERSE order of the days they name.

        A lookup ordered by ``opened_on`` -- which is what the positional read
        plan step X-f3c-2a deleted did -- elects the 1-day-back row here, and
        one ordered by the recording instant elects the 90-days-back row.  The
        days are chosen so those are different rows, so the case fails rather
        than passing vacuously if either side drifts.
        """
        with app.app_context():
            account = seed_user["account"]
            base = _books_open_on(account)
            _record_two_restatements(
                account,
                base - timedelta(days=1),
                base - timedelta(days=90),
                same_instant=False,
            )

            in_sql = db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar()
            assert in_sql == base - timedelta(days=90)
            assert account_opening_fact(account.id).opened_on == in_sql

    def test_they_break_a_SAME_INSTANT_tie_the_same_way(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two rows sharing ``created_at`` to the microsecond, ``id`` deciding.

        Reachable rather than contrived: ``created_at`` carries a ``now()``
        default and PostgreSQL's ``now()`` is the TRANSACTION timestamp, so two
        restatements written in one transaction share it exactly.  Both sides
        break the tie on ``id`` DESC; without that arm each lookup would take
        whichever row its own plan happened to hand it first.
        """
        with app.app_context():
            account = seed_user["account"]
            base = _books_open_on(account)
            _record_two_restatements(
                account,
                base - timedelta(days=30),
                base - timedelta(days=60),
                same_instant=True,
            )

            rows = db.session.query(AccountOpening).filter_by(
                account_id=account.id,
            ).order_by(AccountOpening.id).all()
            assert rows[-1].opened_on == base - timedelta(days=60)

            in_sql = db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar()
            assert in_sql == rows[-1].opened_on
            assert account_opening_fact(account.id).opened_on == in_sql
