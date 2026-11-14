"""Migration ``cddb15ffba5f``: a mis-dated loan statement is withdrawn (``recurrence:R23``).

Both directions are driven through the migration's own shipped callables over
a world holding every shape the predicate must tell apart, each built the way
the migration that wrote the copies left it: ``d3d25212504b`` inserted a legacy
``origination`` row and a ``user_trueup`` copy in ONE transaction, so the two
share a ``created_at``, and the copy is dated that migration's run day.

* ``copied`` -- the defect: the copy is the loan's earliest statement, dated
  after the setup day, and the owner recorded a statement of their own AFTER
  it.  Selected.
* ``owner_first`` -- the same copy, but the owner recorded a ``tracking_start``
  BEFORE it.  Excluded: the stated balance is on record already.
* ``no_signature`` -- a ``user_trueup`` on the same days whose instant is its
  own.  Excluded: nothing says a migration copied it.
* ``copy_on_setup_day`` -- a copy dated the setup day itself.  Excluded:
  nothing to move.
* ``set_up_at_origination`` -- a loan originated on its setup day.  Excluded:
  its origination is its assertion.
* ``tracking_start_copy`` -- a ``tracking_start`` sharing the origination
  row's instant.  Excluded: only a ``user_trueup`` was ever copied.
* ``recorded_earlier_that_day`` -- the copy, and an owner's statement for the
  SAME day recorded earlier.  Excluded: the copy is not the earliest, and only
  the key's ``created_at`` term says so (its ``id`` term separates only rows
  sharing an instant, which the copy's signature gives no second row).

Every clause of the predicate has a shape that fails only it, so a predicate
missing any one clause selects a second row and the first case fails; the
civil-day derivation of the setup day has a case of its own, set up late in
the evening so that UTC reads the NEXT day.  The round trip grades ruling
**R-R99**'s convergence, and a separate case plants decoys that each differ
from the recorded statement in ONE term the downgrade's DELETE keys on, so
"deletes ONLY the statement the upgrade recorded" is graded rather than
asserted.  Every assertion reads the DATABASE.  Figures and dates are made
up.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import text

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db as _db
from app.services import loan_loaders
from tests._test_helpers import (
    create_loan_account,
    load_migration_module,
    loan_params_for,
    run_migration_callable as _run,
)

_MIGRATION = load_migration_module(
    "cddb15ffba5f_a_mis_dated_loan_statement_is_withdrawn.py",
)

_SETUP_DAY = date(2026, 3, 10)
_RUN_DAY = date(2026, 4, 20)
_ORIGINATED = date(2023, 2, 1)
#: The copy migration's transaction instant, and the setup instant: both
#: midday UTC, so each reads as the same civil day in America/New_York.
_RUN_INSTANT = datetime(2026, 4, 20, 16, 0, tzinfo=timezone.utc)
_SETUP_INSTANT = datetime(2026, 3, 10, 16, 0, tzinfo=timezone.utc)
_OWNER_LATER = date(2026, 6, 26)


def _sql(statement, **params):
    """Run one statement and return every row."""
    return _db.session.execute(text(statement), params).all()


def _source(member):
    """The ``ref.loan_anchor_sources`` id of *member*."""
    return ref_cache.loan_anchor_source_id(member)


def _loan(seed_user, name, *, originated=_ORIGINATED, set_up=_SETUP_INSTANT):
    """A loan whose params were created at *set_up* (default: :data:`_SETUP_DAY`)."""
    loan = create_loan_account(
        seed_user, _db.session, name=name, principal=Decimal("30000.00"),
        term=72, origination_date=originated, payment_day=20,
    )
    _db.session.execute(text(
        "UPDATE budget.loan_params SET created_at = :at WHERE account_id = :a"
    ), {"at": set_up, "a": loan.id})
    return loan


def _statement(loan, member, day, balance, instant):
    """Insert one statement with an explicit instant; returns its id."""
    return _db.session.execute(text(
        "INSERT INTO budget.loan_anchor_events "
        "(account_id, anchor_date, anchor_balance, source_id, created_at) "
        "VALUES (:a, :d, :b, :s, :at) RETURNING id"
    ), {
        "a": loan.id, "d": day, "b": Decimal(balance),
        "s": _source(member), "at": instant,
    }).scalar()


def _copied_by_the_old_migration(loan, day=_RUN_DAY, balance="20000.00"):
    """The legacy origination row and the copy, sharing one instant."""
    _statement(
        loan, LoanAnchorSourceEnum.ORIGINATION, loan_params_for(
            _db.session, loan.id,
        ).origination_date, "30000.00", _RUN_INSTANT,
    )
    return _statement(
        loan, LoanAnchorSourceEnum.USER_TRUEUP, day, balance, _RUN_INSTANT,
    )


def _world(seed_user):
    """Build every shape the module docstring names; returns their ids."""
    copied = _loan(seed_user, "Copied")
    copy_id = _copied_by_the_old_migration(copied)
    later_id = _statement(
        copied, LoanAnchorSourceEnum.USER_TRUEUP, _OWNER_LATER,
        "19000.00", datetime(2026, 6, 26, 16, 0, tzinfo=timezone.utc),
    )

    owner_first = _loan(seed_user, "Owner First")
    _statement(
        owner_first, LoanAnchorSourceEnum.TRACKING_START, date(2026, 3, 15),
        "20500.00", datetime(2026, 3, 15, 16, 0, tzinfo=timezone.utc),
    )
    _copied_by_the_old_migration(owner_first)

    no_signature = _loan(seed_user, "No Signature")
    _statement(
        no_signature, LoanAnchorSourceEnum.ORIGINATION, _ORIGINATED,
        "30000.00", _RUN_INSTANT,
    )
    _statement(
        no_signature, LoanAnchorSourceEnum.USER_TRUEUP, _RUN_DAY, "20000.00",
        datetime(2026, 4, 20, 17, 0, tzinfo=timezone.utc),
    )

    same_day = _loan(seed_user, "Copy On Setup Day")
    _copied_by_the_old_migration(same_day, day=_SETUP_DAY)

    at_origination = _loan(
        seed_user, "Set Up At Origination", originated=_SETUP_DAY,
    )
    _copied_by_the_old_migration(at_origination)

    earlier_that_day = _loan(seed_user, "Recorded Earlier That Day")
    _statement(
        earlier_that_day, LoanAnchorSourceEnum.USER_TRUEUP, _RUN_DAY,
        "20100.00", datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
    )
    _copied_by_the_old_migration(earlier_that_day)

    tracking_copy = _loan(seed_user, "Tracking Start Copy")
    _statement(
        tracking_copy, LoanAnchorSourceEnum.ORIGINATION, _ORIGINATED,
        "30000.00", _RUN_INSTANT,
    )
    _statement(
        tracking_copy, LoanAnchorSourceEnum.TRACKING_START, _RUN_DAY,
        "20000.00", _RUN_INSTANT,
    )

    _db.session.commit()
    return {
        "copied": copied.id, "copy": copy_id, "later": later_id,
        "owner_first": owner_first.id,
    }


def _statements(account_id):
    """``[(source, day, balance)]`` of an account's stored statements, by id."""
    return [
        (row.name, row.anchor_date, row.anchor_balance)
        for row in _sql(
            "SELECT s.name, e.anchor_date, e.anchor_balance "
            "FROM budget.loan_anchor_events e "
            "JOIN ref.loan_anchor_sources s ON s.id = e.source_id "
            "WHERE e.account_id = :a ORDER BY e.id", a=account_id,
        )
    ]


def _statement_ids(account_id):
    """The ids of an account's stored statements."""
    return [
        row.id for row in _sql(
            "SELECT id FROM budget.loan_anchor_events WHERE account_id = :a",
            a=account_id,
        )
    ]


def _trigger_count(pattern):
    """How many non-internal triggers match *pattern*."""
    return _sql(
        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
        "AND tgname LIKE :p", p=pattern,
    )[0][0]


def _copies_sql():
    """The migration's own predicate, graded directly."""
    # Pylint: protected-access -- the predicate is module-private to the
    # migration by design, and grading that exact text is this module's point.
    return _MIGRATION._MIS_DATED_COPIES_SQL  # pylint: disable=protected-access


def _function_names_withdrawals():
    """Whether the installed append-only body carries the withdrawal arm."""
    body = _sql(
        "SELECT pg_get_functiondef("
        "'budget.refuse_append_only_change()'::regprocedure)"
    )[0][0]
    return "loan_anchor_withdrawals" in body


def test_the_predicate_selects_the_copy_and_nothing_else(app, db, seed_user):
    """Exactly one row: ``copied``'s copy, with its setup day.

    Run as the migration's own SELECT over the world, so each excluding shape
    is graded against the clause it exists for.
    """
    ids = _world(seed_user)

    rows = _sql(_copies_sql())

    assert [(r.id, r.account_id, r.setup_day) for r in rows] == [
        (ids["copy"], ids["copied"], _SETUP_DAY),
    ]


def test_down_up_down_up_re_dates_the_copy_and_undoes_it_exactly(
    db, seed_user,
):
    """The shipped upgrade and downgrade, graded on the rows they leave.

    The template is built at head, so the world is first stepped DOWN (no
    withdrawal exists yet, so nothing is deleted) and then up: that upgrade is
    the act.  Run in the ``db`` fixture's own app context, for the lock reason
    ``run_migration_callable`` names.
    """
    ids = _world(seed_user)
    owner_first_before = _statements(ids["owner_first"])

    _run(_MIGRATION.downgrade, db.session)
    assert _sql("SELECT to_regclass('budget.loan_anchor_withdrawals')") == [
        (None,),
    ]
    assert _trigger_count("ck\\_append\\_only%") == 12
    assert not _function_names_withdrawals()
    before = _statements(ids["copied"])

    _run(_MIGRATION.upgrade, db.session)

    # The act: the stated balance recorded on the setup day, the copy
    # withdrawn, nothing edited or deleted.
    assert _statements(ids["copied"]) == before + [
        ("tracking_start", _SETUP_DAY, Decimal("20000.00")),
    ]
    assert _sql(
        "SELECT account_id, anchor_event_id "
        "FROM budget.loan_anchor_withdrawals"
    ) == [(ids["copied"], ids["copy"])]
    assert _statements(ids["owner_first"]) == owner_first_before
    # The walk reads the setup-day statement and the owner's later one; the
    # copy is not a fact.
    facts = loan_loaders.load_loan_anchor_facts(
        loan_params_for(db.session, ids["copied"]),
    )
    assert [(f.anchor_date, f.is_tracking_start) for f in facts[1:]] == [
        (_SETUP_DAY, True), (_OWNER_LATER, False),
    ]
    assert ids["copy"] not in [f.event_id for f in facts]
    assert _trigger_count("ck\\_append\\_only%") == 15
    assert _trigger_count("audit\\_loan\\_anchor\\_withdrawals") == 1
    assert _function_names_withdrawals()
    recorded_id = _sql(
        "SELECT max(id) FROM budget.loan_anchor_events WHERE account_id = :a",
        a=ids["copied"],
    )[0][0]

    _run(_MIGRATION.downgrade, db.session)

    # R-R99: exactly the recorded statement goes, and the audit log keeps it.
    assert _statements(ids["copied"]) == before
    assert _statements(ids["owner_first"]) == owner_first_before
    assert _sql(
        "SELECT old_data->>'anchor_date', old_data->>'anchor_balance' "
        "FROM system.audit_log WHERE table_name = 'loan_anchor_events' "
        "AND operation = 'DELETE' AND row_id = :i", i=recorded_id,
    ) == [(_SETUP_DAY.isoformat(), "20000.00")]
    assert _sql("SELECT to_regclass('budget.loan_anchor_withdrawals')") == [
        (None,),
    ]
    assert _sql(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'uq_loan_anchor_events_account_id'"
    ) == [(0,)]
    assert _trigger_count("ck\\_append\\_only%") == 12
    assert not _function_names_withdrawals()

    _run(_MIGRATION.upgrade, db.session)

    # Converges: the same copy selected, the same statement recorded again.
    assert _statements(ids["copied"]) == before + [
        ("tracking_start", _SETUP_DAY, Decimal("20000.00")),
    ]
    assert _sql(
        "SELECT anchor_event_id FROM budget.loan_anchor_withdrawals"
    ) == [(ids["copy"],)]
    assert _trigger_count("ck\\_append\\_only%") == 15


def test_the_setup_day_is_the_owners_civil_day(app, db, seed_user):
    """The setup day is ``created_at`` read in America/New_York, not in UTC.

    A loan set up at 02:00 UTC was set up the EVENING BEFORE for its owner.
    Its copy is dated the UTC day: after the owner's setup day, so selected
    with the earlier day -- where a UTC reading would call the copy dated ON
    the setup day and pass it by.
    """
    evening = _loan(
        seed_user, "Set Up In The Evening",
        set_up=datetime(2026, 3, 10, 2, 0, tzinfo=timezone.utc),
    )
    copy_id = _copied_by_the_old_migration(evening, day=date(2026, 3, 10))
    _db.session.commit()

    rows = _sql(_copies_sql())

    assert [(r.id, r.setup_day) for r in rows] == [
        (copy_id, date(2026, 3, 9)),
    ]


def test_the_downgrade_deletes_ONLY_the_statement_it_recorded(db, seed_user):
    """R-R99's "only", graded by decoys on the SAME loan.

    Each decoy is a ``tracking_start``-shaped statement the downgrade's DELETE
    must leave, differing from the recorded one in exactly ONE term the DELETE
    keys on: its own instant; the withdrawal's instant on another day; the
    withdrawal's instant with another balance; the withdrawal's instant, day
    and balance as a ``user_trueup``.  A DELETE missing any one term takes
    that decoy.  Planted AFTER the upgrade, since a statement earlier than the
    copy would otherwise keep the predicate from selecting it.  Rows are
    tracked by id: the first decoy equals the recorded row in every value
    but its instant.
    """
    ids = _world(seed_user)
    copied = SimpleNamespace(id=ids["copied"])
    _run(_MIGRATION.downgrade, db.session)
    before = set(_statement_ids(copied.id))
    _run(_MIGRATION.upgrade, db.session)
    (recorded_id,) = set(_statement_ids(copied.id)) - before
    instant = _sql(
        "SELECT created_at FROM budget.loan_anchor_withdrawals"
    )[0][0]
    decoy_ids = {
        _statement(copied, member, day, balance, at)
        for member, day, balance, at in (
            (LoanAnchorSourceEnum.TRACKING_START, _SETUP_DAY, "20000.00",
             datetime(2026, 3, 12, 16, 0, tzinfo=timezone.utc)),
            (LoanAnchorSourceEnum.TRACKING_START, date(2026, 3, 11),
             "20000.00", instant),
            (LoanAnchorSourceEnum.TRACKING_START, _SETUP_DAY, "20050.00",
             instant),
            (LoanAnchorSourceEnum.USER_TRUEUP, _SETUP_DAY, "20000.00",
             instant),
        )
    }
    db.session.commit()
    assert len(decoy_ids) == 4

    _run(_MIGRATION.downgrade, db.session)

    assert set(_statement_ids(copied.id)) == before | decoy_ids
    assert recorded_id not in _statement_ids(copied.id)
