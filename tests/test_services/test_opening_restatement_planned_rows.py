"""The books may not be restated past a still-projected recurring row (ruling R-PC88).

Plan step ``pay_calendar:C18-a``.  Ruling **R-PC85** made a recurring
definition's occurrences start above the books of every account it moves money
in, compared on the row's due day with a day ON the opening inside it (ruling
**R-PC86**).  So moving an account's books LATER past a recurring row that is
still Projected -- an unpaid, overdue bill the rule generated -- would leave
that row answering an occurrence the walk drops, and a maintain pass that
reaches its paycheck retires such a row, raising the forecast without a word.  Josh ruled the door refuses instead (2026-09-22, "Refuse the move"): the
owner marks the row paid, cancels it or moves it, then restates the books.

Each account here is built with :func:`~tests._test_helpers.account_never_asserted`
and an opening placed before the seeded schedule (2026-01-02 onward), so the
restatement's OTHER record rules -- movements, matched lines, assertions --
have nothing to refuse and the planned-row rule is the one graded.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AccountOpeningSourceEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    planned_rows_books,
    recurrence_engine,
    status_seam,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import account_opening_fact
from app.services.generation_schedule import GenerationSchedule
from app.services.opening_service import (
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.services.planned_rows_books import StrandedRow
from app.services.recurrence import (
    RecurrenceGenerationError,
    RecurrenceResolutionError,
)
from tests._test_helpers import (
    account_never_asserted,
    make_cadence_rule,
    one_off_row_of,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD

_ONE_DAY = timedelta(days=1)

#: Where the test accounts' books open: before the seeded schedule, so every
#: generated row starts above them.
_EARLY_OPENING = date(2025, 12, 1)


class TestAProjectedRecurringRowBoundsTheRestatement:
    """R-PC88 at the boundary, from both sides, for both row kinds."""

    def test_the_rows_due_day_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Opening ON the first row's due day would put the row inside it."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first_due = min(row.due_date for row in rows)
            before = _opening_count(account)

            with pytest.raises(ValidationError, match=r"still projected and due"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            # Refused before anything is staged: no restatement row appended.
            assert _opening_count(account) == before

    def test_the_day_BEFORE_the_rows_due_day_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side, so a ``<`` written for ``<=`` fails one of the pair."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            day = min(row.due_date for row in rows) - _ONE_DAY

            outcome = apply_opening_restatement(
                account=account, opening=BooksOpening(day, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(account.id).opened_on == day

    def test_the_refusal_names_the_row_and_its_due_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The owner is told WHICH row and WHEN, so the repair is findable."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(
                        first.due_date + _ONE_DAY, Decimal("0.00"),
                    ),
                )

            message = str(refused.value)
            assert first.name in message
            assert first.due_date.isoformat() in message

    def test_a_CANCELLED_row_bounds_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Cancelling is one of the three repairs the refusal names."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            status_seam.apply_status_change(
                first, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            _db.session.flush()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first.due_date, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_a_SOFT_DELETED_row_bounds_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A row the owner removed is not a live claim of money owed."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            first.is_deleted = True
            _db.session.flush()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first.due_date, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_a_HAND_ENTERED_projected_row_bounds_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """No rule names a hand-entered row, so nothing would retire it.

        R-PC88 is about RECURRING rows.  A one-off mints a RULE-LESS
        definition (plan step balance:X-bi-7c), so it IS template-linked --
        which is exactly the class a "template_id is not null" predicate
        would wrongly refuse.
        """
        with app.app_context():
            account = _account_opened_early(seed_user)
            one_off = one_off_row_of(
                seed_periods[1], name="Typed by hand", amount=Decimal("12.00"),
                user_id=seed_user["user"].id, account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            )
            _db.session.flush()
            assert one_off.template_id is not None
            assert one_off.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    seed_periods[1].start_date, Decimal("0.00"),
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_a_projected_recurring_TRANSFER_into_the_account_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A transfer moves money in BOTH accounts; the destination's books bind.

        The account here is the transfer's DESTINATION, the side a
        transaction-only predicate would miss.
        """
        with app.app_context():
            account = _account_opened_early(seed_user)
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=account.id,
                name="Monthly save",
            )
            _db.session.add(template)
            _db.session.flush()
            state_template_price(template, Decimal("50.00"))
            make_cadence_rule(
                template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
            )
            _db.session.refresh(template)
            created = transfer_recurrence.generate_for_template(
                template, _schedule(seed_user, seed_periods),
                seed_user["scenario"].id,
            )
            _db.session.flush()
            first_due = min(transfer.due_date for transfer in created)

            with pytest.raises(ValidationError, match=r"Monthly save"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )


class TestWhichRowsTheRefusalIsOver:
    """Which rows the refusal is over (the C18-a round-2 review's L-a).

    A mutation skipping overridden rows passed every test this module had.
    The review's M-A -- pairing rows by paycheck rather than by the
    occurrence they answer -- is killed by ruling R-PC92's day change
    (``tests/test_routes/test_definition_edit_strands_no_row.py``); two
    occurrences in one paycheck need a cadence no owner can author yet
    (the WEEK unit waits on plan step R5).
    """

    def test_an_OVERRIDDEN_row_is_refused_over(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An owner-repriced row is still owed, and the chooser's "use" hands it back.

        The maintain pass keeps it as a conflict rather than retiring it, but
        choosing "use the template" returns it to its definition and the next
        pass retires it -- so it is refused over like any planned row.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            first.is_override = True
            _db.session.flush()

            with pytest.raises(ValidationError, match=r"Recurring rent"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(first.due_date, Decimal("0.00")),
                )


class TestAHiddenRowIsNamedAsTheArchivedDefinitions:
    """Ruling R-PC93's wording: the owner cannot see a hidden row, so the remedy reaches it."""

    def test_a_hidden_BILL_names_the_definition_and_the_unarchive(self):
        """The row is the ARCHIVED definition's, and unarchiving is the way to it."""
        row = StrandedRow(
            name="Rent", books_day=date(2026, 3, 1), is_envelope=False,
            is_hidden=True,
        )

        assert row.described() == (
            '"Rent" is archived and still holds an unpaid item due 2026-03-01 '
            "that unarchiving would bring back"
        )
        # The developer dropped R-PC93's "or delete "Rent" for good" (the
        # round-3 review's M-1, 2026-09-23): the permanent delete refuses any
        # definition with payment history, so the clause was often false.
        # Then "or move it later" (round 9's M1): no generated row can be moved
        # past its books, and "it" is now the definition, by name.
        assert row.remedy() == (
            'Unarchive "Rent", mark that item paid or cancel it'
        )

    def test_a_hidden_ENVELOPE_names_its_paychecks_last_day(self):
        """An envelope compares its paycheck's last day, hidden or not (R-PC89)."""
        row = StrandedRow(
            name="Groceries", books_day=date(2026, 3, 8), is_envelope=True,
            is_hidden=True,
        )

        assert row.described() == (
            '"Groceries" is archived and still holds an unpaid item in the '
            "paycheck ending 2026-03-08 that unarchiving would bring back"
        )

    def test_a_LIVE_row_keeps_R_PC91s_words(self):
        """The live wording is the one ruled at R-PC91; R-PC93 adds, never rewrites."""
        row = StrandedRow(
            name="Rent", books_day=date(2026, 3, 1), is_envelope=False,
        )

        assert row.described() == '"Rent" is still projected and due 2026-03-01'
        # Round 9's M1 dropped "or move it later": a generated row's due day
        # is its schedule's, so no move could clear the refusal.
        assert row.remedy() == "Mark it paid or cancel it first"


class TestAnEnvelopeBoundsTheRestatementByItsPaychecksEnd:
    """R-PC88 under R-PC89: an envelope's day is its paycheck's LAST day.

    The refusal asks the walk itself, so it refuses exactly the restatements
    that would strand the row -- not the ones between its due day and its
    paycheck's end, which the walk still names.
    """

    def test_the_envelope_rows_DUE_day_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Books opening on the first envelope row's payday strand nothing.

        The envelope is spent across its paycheck, which ends after the new
        opening, so the walk still names it; a bill's due day would refuse.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(
                seed_user, seed_periods, is_envelope=True,
            )
            day = min(row.due_date for row in rows)

            outcome = apply_opening_restatement(
                account=account, opening=BooksOpening(day, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_the_envelope_paychecks_LAST_day_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Opening ON the paycheck's last day puts the whole envelope inside it.

        The refusal names the paycheck's end, the day the owner has to move
        the row past, not its due day.
        """
        with app.app_context():
            account, _rows = _account_with_projected_rows(
                seed_user, seed_periods, is_envelope=True,
            )
            last_day = seed_periods[1].start_date - _ONE_DAY
            before = _opening_count(account)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(last_day, Decimal("0.00")),
                )

            message = str(refused.value)
            assert '"Groceries" is still projected in the paycheck ending' in message
            assert last_day.isoformat() in message
            assert _opening_count(account) == before

    def test_the_day_BEFORE_the_paychecks_last_day_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side of the boundary, so ``>=`` for ``>`` fails one."""
        with app.app_context():
            account, _rows = _account_with_projected_rows(
                seed_user, seed_periods, is_envelope=True,
            )
            day = seed_periods[1].start_date - 2 * _ONE_DAY

            outcome = apply_opening_restatement(
                account=account, opening=BooksOpening(day, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED


class TestTheDefinitionsAccountsBoundItsRows:
    """Both accounts bound a row its definition left behind (review L1, then ruling R-PC99).

    A definition moved to another account leaves the rows its pass did not
    reach where they were: on the OLD account, which the balance counts them
    in.  Those rows still answer occurrences of the definition's walk, which
    the NEW account's books bound -- so the new account's restatement past
    them makes a later pass delete them (review L1).  And the old account's
    books hold them where they sit: its restatement past them would count
    them inside its opening a second time (ruling R-PC99, developer
    2026-09-23, the round-6 review's H1, measured ``-$100.00`` against the
    books rule's ``-$80.00``).  Round 1's fix keyed the refusal by the
    definition alone and let that restatement through; R-PC99 asks both.
    """

    def test_the_OLD_account_is_refused_over_the_rows_its_definition_left_behind(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Re-expressed under R-PC99 (developer-ruled): it asserted COMMITTED here.

        The ruled outcome, verbatim from the chosen option: "Restating
        Checking to 01-16 is refused ('Recurring rent' is still projected
        and due 01-02: mark it paid or cancel it first)".
        """
        with app.app_context():
            old, rows = _account_with_projected_rows(seed_user, seed_periods)
            _moved_to_a_new_account(seed_user, rows[0].template)
            first_due = min(row.due_date for row in rows)
            assert all(row.account_id == old.id for row in rows)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=old,
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            assert (
                f'"Recurring rent" is still projected and due '
                f"{first_due.isoformat()}.  An opening is the balance at the "
                "END of its day, so that unpaid item would sit inside it.  "
                "Mark it paid or cancel it first"
            ) in str(refused.value)

    def test_the_OLD_account_may_open_the_day_BEFORE_them(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side of R-PC99's boundary, so ``>=`` for ``>`` fails one."""
        with app.app_context():
            old, rows = _account_with_projected_rows(seed_user, seed_periods)
            _moved_to_a_new_account(seed_user, rows[0].template)
            first_due = min(row.due_date for row in rows)

            outcome = apply_opening_restatement(
                account=old,
                opening=BooksOpening(first_due - _ONE_DAY, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_the_NEW_account_is_refused_over_the_rows_left_on_the_old_one(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The definition's walk is bounded by its new account, wherever its rows sit."""
        with app.app_context():
            _old, rows = _account_with_projected_rows(seed_user, seed_periods)
            new = _moved_to_a_new_account(seed_user, rows[0].template)
            first = min(rows, key=lambda row: row.due_date)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=new,
                    opening=BooksOpening(first.due_date, Decimal("0.00")),
                )

            assert f"and due {first.due_date.isoformat()}" in str(refused.value)


class TestAScheduleThatCannotBeWalkedRefusesTheRestatement:
    """The step's review, L7: a stored rule no walk can read reached the owner as a 500.

    Both of the producer's failure points are driven -- resolving a
    definition's rule and walking it -- each on the day BEFORE the first
    row's due day, which the walkable schedule ACCEPTS
    (``test_the_day_BEFORE_the_rows_due_day_is_accepted``), so the refusal
    here is the unread schedule's and nothing else's.
    """

    @pytest.mark.parametrize(("seam", "error"), [
        ("resolved_with_books", RecurrenceResolutionError),
        ("books_reading", RecurrenceGenerationError),
    ])
    def test_it_is_refused_logged_and_nothing_is_written(
        self, app, db, seed_user, seed_periods, monkeypatch, caplog, seam, error,
    ):  # pylint: disable=unused-argument
        """Fail closed: R-PC97's words, the opening unchanged, the log saying why."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            day = min(row.due_date for row in rows) - _ONE_DAY
            before = _opening_count(account)

            def refusing(*_args, **_kwargs):
                raise error("a rule no door writes")

            monkeypatch.setattr(planned_rows_books, seam, refusing)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=account, opening=BooksOpening(day, Decimal("0.00")),
                )

            assert str(refused.value) == (
                f"These books cannot open on {day.isoformat()} while a "
                "recurring item moving money in this account has a schedule "
                "that cannot be read: repair its schedule first."
            )
            assert _opening_count(account) == before
            assert account_opening_fact(account.id).opened_on == _EARLY_OPENING
            assert (
                f"Refusing to restate account {account.id}'s books"
            ) in caplog.text


# ── helpers ──────────────────────────────────────────────────────────────


def _opening_count(account):
    """Return how many opening records *account* carries."""
    return _db.session.query(AccountOpening).filter_by(
        account_id=account.id,
    ).count()


def _account_opened_early(seed_user, name="Planned-rows account"):
    """Return a never-asserted account whose books open before the schedule."""
    account = account_never_asserted(seed_user, _db.session, name=name)
    _db.session.flush()
    _db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=_EARLY_OPENING,
        opening_equity=Decimal("0.00"),
        source_id=ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
    ))
    _db.session.flush()
    return account


def _moved_to_a_new_account(seed_user, template):
    """Point *template* at a new early-opened account, leaving its rows where they are.

    The state a definition's account move leaves for the rows no maintain
    pass reached (ruling R-CC36): the definition names the new account, its
    rows still sit on the old one.
    """
    account = _account_opened_early(seed_user, name="Moved-to account")
    template.account_id = account.id
    _db.session.flush()
    return account


def _schedule(seed_user, seed_periods):
    """Return a generation schedule over every seeded period."""
    return GenerationSchedule.for_period_ids(
        BalanceContext.build(seed_user["user"].id),
        {period.id for period in seed_periods},
    )


def _account_with_projected_rows(seed_user, seed_periods, *, is_envelope=False):
    """Return an early-opened account and the Projected rows its rule generated.

    Every paycheck, so each row is dated on its payday; *is_envelope* makes
    the definition an envelope, whose rows the books compare on their
    paycheck's LAST day instead (ruling R-PC89).
    """
    account = _account_opened_early(seed_user)
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account.id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Groceries" if is_envelope else "Recurring rent",
        is_envelope=is_envelope,
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template, Decimal("10.00"))
    make_cadence_rule(
        template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
    )
    _db.session.refresh(template)
    rows = recurrence_engine.generate_for_template(
        template, _schedule(seed_user, seed_periods), seed_user["scenario"].id,
    )
    _db.session.flush()
    assert rows, "the fixture must generate rows for the rule to bound"
    return account, rows
