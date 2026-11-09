"""Migration ``e6b4a2d8c713``'s DOWNGRADE, executed against planted posted rows.

The upgrade seeds three reference rows and moves nothing; the downgrade is
where the data work is, and it is the direction that can destroy a ledger in
silence.  ``budget.account_postings.ledger_account_id`` is ``ON DELETE
CASCADE`` (:mod:`app.models.journal_entry`) and the balanced-entry trigger has
no DELETE arm (:mod:`app.posting_infrastructure`), so a leg the move misses is
simply deleted with its chart row and the trial balance goes out with no error
raised anywhere.  A source-level check cannot see that; this suite runs the
statements.

**Executed, not read**, on the precedent
``tests/test_models/test_anchor_cache_downgrade.py`` sets: the repo's other
migration-direction suites settle for a source check because their DDL wants an
ACCESS EXCLUSIVE lock that fights the xdist workers.  This downgrade contains no
DDL at all -- four DML statements and two ref deletes -- so there is nothing to
lock and every statement runs here exactly as ``downgrade()`` orders them.

The shapes it is run over are the ones the step actually produces: a natively
modelled account whose true-up books straight to its counter row, a RE-POINTED
account carrying the counter-only delta entry (the shape with no linked leg,
which is what makes the move non-trivial), and an account whose ``anchor_equity``
row does not exist, which is the only arm with nothing to move onto.  The rows
are planted by SQL in those shapes (:func:`_chart_row`, :func:`_book`); the
stored ledger's writers posted them until plan task plan#3132.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import LedgerAccountKindEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.ref import AccountType
from app.utils.dates import display_today
from tests._stored_ledger import chart_row_or_plant, ledger_account_of_kind
from tests._test_helpers import (
    create_account_of_type,
    load_migration_module,
)

_MIGRATION_FILENAME = "e6b4a2d8c713_add_the_true_up_counter_leg_ref_rows.py"


def _migration():
    """Return the loaded migration module."""
    return load_migration_module(_MIGRATION_FILENAME)


def _entry_sums():
    """Return ``{journal_entry_id: SUM(amount)}`` over the whole ledger.

    The invariant the downgrade must preserve exactly: every entry keeps the
    total it had, and every total is zero.  Keyed by entry rather than summed
    overall because a move that swapped two legs between entries would leave
    the grand total untouched.
    """
    return {
        row.id: row.total
        for row in db.session.execute(text(
            "SELECT je.id, SUM(p.amount) AS total "
            "FROM budget.journal_entries je "
            "JOIN budget.account_postings p ON p.journal_entry_id = je.id "
            "GROUP BY je.id"
        )).all()
    }


def _counter_row_count():
    """Return how many chart rows carry one of the two kinds the step adds."""
    return db.session.execute(text(
        "SELECT count(*) FROM budget.ledger_accounts la "
        "JOIN ref.ledger_account_kinds k ON la.kind_id = k.id "
        "WHERE k.name IN ('interest_income', 'unrealized_change')"
    )).scalar()


# The frozen mint statement names the bucket flag by its own-revision name; the
# column is ``is_owner_bucket`` at HEAD since plan step ``balance:X-bi-6-3``'s
# migration ``c7d1e9a4b2f8`` renamed it (ruling R-BAL99).  A real downgrade
# reaches ``e6b4a2d8c713`` only after ``c7d1e9a4b2f8`` has renamed the column
# back, so the frozen text is right where it runs; this harness runs it at
# HEAD, so it re-homes that one identifier, anchored on the frozen text.
_FROZEN_FLAG_COLUMN = "(user_id, class_id, kind_id, account_id, is_fallback, name)"
_HEAD_FLAG_COLUMN = "(user_id, class_id, kind_id, account_id, is_owner_bucket, name)"


def _run_downgrade():
    """Execute the migration's downgrade statements, in ``downgrade()``'s order.

    The order is the only one the RESTRICT foreign keys permit -- mint any
    missing equity row, move the legs onto it, drop the emptied chart rows,
    then delete the reference values -- so running them in this sequence is
    itself part of what is under test.
    """
    module = _migration()
    restore_sql = module._RESTORE_MISSING_ANCHOR_EQUITY_SQL
    assert _FROZEN_FLAG_COLUMN in restore_sql, (
        "the frozen e6b4a2d8c713 mint statement changed; update the anchor here"
    )
    for statement in (
        restore_sql.replace(_FROZEN_FLAG_COLUMN, _HEAD_FLAG_COLUMN),
        module._MOVE_COUNTER_LEGS_TO_ANCHOR_EQUITY_SQL,
        module._DROP_TRUEUP_COUNTER_CHART_ROWS_SQL,
        module._DROP_TRUEUP_COUNTER_LEDGER_KINDS_SQL,
        module._DROP_UNREALIZED_LEDGER_CLASS_SQL,
    ):
        db.session.execute(text(statement))
    db.session.flush()


#: Each chart-row kind these cases plant: its ledger class and the label its
#: display name snapshots (``None`` for the account's own row, which renders
#: the account's name).  The three test account types are all assets.
_CHART_ROWS = {
    "linked": ("Asset", None),
    "anchor_equity": ("Equity", "Opening"),
    "interest_income": ("Income", "Interest Income"),
    "unrealized_change": ("Unrealized", "Change in Value"),
}


def _chart_row(account, kind):
    """Return *account*'s chart row of *kind*, planting it by SQL when absent.

    Through :func:`tests._stored_ledger.chart_row_or_plant`, with the class and
    label :data:`_CHART_ROWS` names.  The stored ledger's writers minted these
    rows until plan task plan#3132, when the cases started planting them, as a
    migration test plants what its revision reads.
    """
    class_name, label = _CHART_ROWS[kind]
    return chart_row_or_plant(
        db.session, account, kind, class_name,
        None if label is None else f"{account.name} -- {label}",
    )


def _book(seed_user, source, day, legs):
    """Plant one balanced *source*-kind entry with *legs* ``{ledger id: amount}``.

    Filed in the seed bootstrap period: the downgrade reads no entry's period
    or day, only its legs and the chart rows they name.  The legs carry the
    posting kind the writers gave an anchor correction's, ``opening`` or
    ``trueup``.  Flushed, not committed; the deferred balanced trigger reads
    the entry at the test's end.
    """
    entry_id = db.session.execute(text(
        "INSERT INTO budget.journal_entries (user_id, scenario_id, "
        "pay_period_id, entry_date, source_kind_id, description) "
        "SELECT :u, :s, :p, :d, id, 'planted correction' "
        "FROM ref.posting_sources WHERE name = :source RETURNING id"
    ), {
        "u": seed_user["user"].id, "s": seed_user["scenario"].id,
        "p": seed_user["bootstrap_period"].id, "d": day, "source": source,
    }).scalar_one()
    posting_kind = "opening" if source == "account_opening" else "trueup"
    for ledger_id, amount in legs.items():
        db.session.execute(text(
            "INSERT INTO budget.account_postings (journal_entry_id, "
            "ledger_account_id, amount, posting_kind_id) "
            "SELECT :e, :l, :a, id FROM ref.posting_kinds WHERE name = :k"
        ), {"e": entry_id, "l": ledger_id, "a": amount, "k": posting_kind})
    db.session.flush()


def _opened(seed_user, type_name, name, opening):
    """Create an account of *type_name* opened three days ago at *opening*.

    Its opening correction is planted beside it, the account's own row
    against its equity twin, which is what the writers posted for it.
    """
    account = create_account_of_type(
        seed_user, db.session, type_name, name,
        anchor_balance=Decimal(str(opening)),
        observed_on=display_today() - timedelta(days=3),
    )
    amount = Decimal(str(opening))
    _book(seed_user, "account_opening", display_today() - timedelta(days=3), {
        _chart_row(account, "linked"): amount,
        _chart_row(account, "anchor_equity"): -amount,
    })
    return account


def _true_up(seed_user, account, balance, counter):
    """Assert a later balance on *account* and plant its true-up correction.

    Appends the ``AccountAnchorHistory`` row a day after the opening and books
    the difference (the fixtures settle nothing, so it is the new balance
    less the opening) on the account's own row against its *counter* row, the
    kind ruling R-FO's dispatch names for the account's type.
    """
    opening = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .first()
    )
    db.session.add(AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        created_at=opening.created_at + timedelta(seconds=1),
        observed_on=opening.observed_on + timedelta(days=1),
        # The ENTERED day moves with the observed one (**N-299**): the column
        # defaults to the wall clock, which a historical row must not inherit.
        recorded_on=opening.observed_on + timedelta(days=1),
    ))
    db.session.flush()
    delta = Decimal(str(balance)) - opening.anchor_balance
    _book(seed_user, "account_trueup", opening.observed_on + timedelta(days=1), {
        _chart_row(account, "linked"): delta,
        _chart_row(account, counter): -delta,
    })


def _retype(seed_user, account, type_name, amount, counter):
    """Re-type *account* in place and plant the re-point delta it produced.

    A true-up booked on equity before the re-type moves onto the new type's
    *counter* row: an entry of two counter legs and no linked leg, equity
    ``+amount`` against the counter ``-amount``.  That is the shape the move
    is non-trivial for.
    """
    account.account_type_id = (
        db.session.query(AccountType).filter_by(name=type_name).one().id
    )
    db.session.flush()
    db.session.expire(account)
    _book(seed_user, "account_trueup", display_today() - timedelta(days=2), {
        _chart_row(account, "anchor_equity"): Decimal(str(amount)),
        _chart_row(account, counter): -Decimal(str(amount)),
    })


class TestTheDowngradeReturnsEveryCounterLegToEquity:
    """The reverse move preserves every entry and empties the new kinds."""

    def test_it_moves_every_leg_and_leaves_no_entry_changed(
        self, app, db, seed_user,
    ):
        """Posting count and every per-entry sum survive the move untouched.

        Run over three shapes at once, because the move is a single UPDATE and
        a bug in its join would show on one shape and not another: a Money
        Market booking interest income natively, a Roth booking a value change
        natively, and a Checking RE-TYPED to a Roth, which carries the
        counter-only delta entry -- two counter legs and no linked leg -- that
        the whole scoping argument turns on.

        ``posting_count`` is asserted beside the sums because the destructive
        half is a DELETE through a CASCADE: a leg the move missed would vanish
        with its chart row, and a per-entry sum of a now-single-legged entry
        would simply read as that leg's amount rather than raising.
        """
        with app.app_context():
            native_interest = _opened(
                seed_user, "Money Market", "Native MM", "1000.00",
            )
            _true_up(seed_user, native_interest, "1015.01", "interest_income")
            native_change = _opened(
                seed_user, "Roth IRA", "Native Roth", "1000.00",
            )
            _true_up(seed_user, native_change, "1150.00", "unrealized_change")
            re_pointed = _opened(
                seed_user, "Checking", "Re-pointed", "1000.00",
            )
            _true_up(seed_user, re_pointed, "1150.00", "anchor_equity")
            _retype(seed_user, re_pointed, "Roth IRA", "150.00", "unrealized_change")

            assert _counter_row_count() == 3
            before_sums = _entry_sums()
            before_postings = db.session.execute(text(
                "SELECT count(*) FROM budget.account_postings"
            )).scalar()

            _run_downgrade()

            assert db.session.execute(text(
                "SELECT count(*) FROM budget.account_postings"
            )).scalar() == before_postings
            assert _entry_sums() == before_sums
            assert all(total == 0 for total in before_sums.values())
            assert _counter_row_count() == 0

    def test_the_moved_legs_land_on_the_accounts_own_equity_row(
        self, app, db, seed_user,
    ):
        """Each account's equity row absorbs exactly its own counter net.

        The join could plausibly move a leg onto ANOTHER account's equity row
        and still leave every entry balanced and every count intact, so the
        destination is asserted per account rather than in aggregate.
        """
        with app.app_context():
            roth = _opened(seed_user, "Roth IRA", "Destination Roth", "1000.00")
            _true_up(seed_user, roth, "1150.00", "unrealized_change")
            other = _opened(
                seed_user, "Money Market", "Destination MM", "2000.00",
            )
            _true_up(seed_user, other, "2015.01", "interest_income")

            _run_downgrade()

            for account, expected in ((roth, "-1150.00"), (other, "-2015.01")):
                equity = ledger_account_of_kind(
                    db.session, account.id,
                    LedgerAccountKindEnum.ANCHOR_EQUITY,
                )
                assert db.session.execute(text(
                    "SELECT COALESCE(SUM(amount), 0) FROM "
                    "budget.account_postings WHERE ledger_account_id = :id"
                ), {"id": equity.id}).scalar() == Decimal(expected)

    def test_it_mints_the_equity_row_when_the_account_has_none(
        self, app, db, seed_user,
    ):
        """The RESTORE arm covers an account whose opening booked nothing.

        An opening whose delta is zero mints no ``anchor_equity`` row, so a
        later true-up's counter row can be the account's only one and the move
        would have nothing to land on.  Reproduced by deleting the row the
        fixture's non-zero opening created, which is the same state.
        """
        with app.app_context():
            account = _opened(seed_user, "Roth IRA", "No Equity Row", "1000.00")
            _true_up(seed_user, account, "1150.00", "unrealized_change")
            equity = ledger_account_of_kind(
                db.session, account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            db.session.execute(text(
                "DELETE FROM budget.account_postings WHERE "
                "ledger_account_id = :id"
            ), {"id": equity.id})
            db.session.execute(text(
                "DELETE FROM budget.ledger_accounts WHERE id = :id"
            ), {"id": equity.id})
            db.session.flush()
            assert ledger_account_of_kind(
                db.session, account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is None

            _run_downgrade()

            restored = ledger_account_of_kind(
                db.session, account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert restored is not None
            assert restored.name == "No Equity Row -- Opening"
            assert restored.class_id == db.session.execute(text(
                "SELECT id FROM ref.ledger_account_classes WHERE name = 'Equity'"
            )).scalar()
            assert db.session.execute(text(
                "SELECT COALESCE(SUM(amount), 0) FROM "
                "budget.account_postings WHERE ledger_account_id = :id"
            ), {"id": restored.id}).scalar() == Decimal("-150.00")

    def test_it_deletes_exactly_the_three_reference_rows(
        self, app, db, seed_user,
    ):
        """The two kinds and the class go; nothing else in either table does.

        Both foreign keys into these tables are ``ON DELETE RESTRICT``, so this
        also proves the ORDER: the deletes only succeed once every chart row
        carrying them is gone.
        """
        with app.app_context():
            account = _opened(seed_user, "Roth IRA", "Ref Rows", "1000.00")
            _true_up(seed_user, account, "1150.00", "unrealized_change")
            kinds_before = db.session.execute(text(
                "SELECT count(*) FROM ref.ledger_account_kinds"
            )).scalar()
            classes_before = db.session.execute(text(
                "SELECT count(*) FROM ref.ledger_account_classes"
            )).scalar()

            _run_downgrade()

            assert db.session.execute(text(
                "SELECT count(*) FROM ref.ledger_account_kinds"
            )).scalar() == kinds_before - 2
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.ledger_account_classes"
            )).scalar() == classes_before - 1
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.ledger_account_kinds WHERE name "
                "IN ('interest_income', 'unrealized_change')"
            )).scalar() == 0
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.ledger_account_classes "
                "WHERE name = 'Unrealized'"
            )).scalar() == 0


class TestTheUpgradeSeedsWhatTheDispatchResolves:
    """The three rows exist at head and resolve through the ref cache."""

    def test_every_row_the_dispatch_needs_is_seeded_and_resolvable(
        self, app, db,
    ):
        """The kinds and the class resolve by ID, which is what the app reads.

        The migration's whole upgrade is an inline seed, and its failure mode
        is silent: a name that does not match the enum's ``.value`` leaves
        ``ref_cache.init()`` raising at app start on a freshly upgraded
        database, long after the migration reported success.
        """
        with app.app_context():
            for kind in (
                LedgerAccountKindEnum.INTEREST_INCOME,
                LedgerAccountKindEnum.UNREALIZED_CHANGE,
            ):
                assert ref_cache.ledger_account_kind_id(kind) > 0
            assert db.session.execute(text(
                "SELECT is_debit_normal FROM ref.ledger_account_classes "
                "WHERE name = 'Unrealized'"
            )).scalar() is False
