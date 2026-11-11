"""The service refuses exactly what the database constraint refuses."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import AccountOpeningSourceEnum, SettledDayBasisEnum
from app.exceptions import ValidationError
from app.models.account_opening import AccountOpening
from app.models.transaction_entry import TransactionEntry
from app.services.cash_ledger import account_opening_fact, earliest_recorded_movement_day
from app.services.opening_service import BooksOpening, apply_opening_restatement
from app.services.settle_day import SettleDay, record_settle_day
from tests._test_helpers import (
    create_settled_cash_transaction,
    figure_source_columns,
    refused_by_database_rule,
)

from ._helpers import (
    _ONE_DAY,
    _OPENED_ON_IN_SQL,
    _openings,
    _cash_account,
    _opening_count,
    _deleted_as_the_door_leaves_it,
)


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
