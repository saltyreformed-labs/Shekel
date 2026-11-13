"""Schema and migration locks for the two dropped ``LoanParams`` columns.

E-18 / Commit 15 demoted ``budget.loan_params.current_principal`` and
``budget.loan_params.interest_rate`` from NOT NULL to nullable, and both are
GONE.  DH-#56 completed the OPT-1 drop for ``interest_rate`` (the loan's rate
lives in its origination :class:`RateHistory` row).  Plan step
``recurrence:R20`` (ruling **R-R72** part 3, finding **REC-519**) dropped
``current_principal`` by migration ``22b23085394d``: the balance the owner
states at setup is a dated assertion, recorded as a ``tracking_start``
:class:`LoanAnchorEvent`, where the column held it as a value nothing read.

Three locks land here:

* **Both columns are gone** -- ``information_schema`` confirms neither exists,
  so a migration or model edit that re-introduces a stored loan balance or
  rate is caught the moment it lands.  (Until R20 this module locked the
  demoted column's NULLABILITY and grepped ``app/`` for reads of it; a column
  that does not exist needs neither fence.)

* **The R20 migration's backfill arm fires for exactly the loan it names**
  (the old C15-5 round-trip pattern, run against the live test database): the
  downgrade's literal SQL re-adds the column, five loan shapes are planted,
  the migration's own backfill statement runs, and the upgrade's literal SQL
  drops the column again.  Only a loan the old door configured MID-LIFE
  (originated before its setup day, a stored balance, no assertion of its own
  -- a legacy ``origination`` row is not one) gets a ``tracking_start`` --
  dated its setup day, carrying the stored balance -- and neither a loan set
  up on its origination day, nor one whose stored balance is NULL, nor one
  the owner has trued up gets anything.  Without this the arm is graded only
  by the clone rehearsal, where it matched 0 of 2 production loans.

* **The migration declares its revision pair and a working downgrade** -- the
  same surface check ``test_loan_anchor_backfill.py::TestDowngradeSmoke`` makes
  on ``d3d25212504b``, made here on ``22b23085394d``.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db as _db
from app.models.loan_anchor_event import LoanAnchorEvent
from tests._test_helpers import (
    create_loan_account,
    insert_trueup_event,
    load_migration_module,
    loan_params_for,
)


_M_R20 = load_migration_module(
    "22b23085394d_the_stated_balance_is_an_assertion.py"
)


def _column_row(column_name):
    """Return the ``information_schema`` row for a ``loan_params`` column, or None."""
    return _db.session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'budget' "
        "  AND table_name = 'loan_params' "
        "  AND column_name = :c"
    ), {"c": column_name}).fetchone()


def _check_names():
    """Return the CHECK constraint names on ``budget.loan_params``."""
    rows = _db.session.execute(text(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'budget.loan_params'::regclass "
        "  AND contype = 'c'"
    )).fetchall()
    return {r[0] for r in rows}


# -- the columns are gone ----------------------------------------------------


def test_current_principal_column_dropped():
    """R20: ``current_principal`` and its CHECK no longer exist on ``loan_params``.

    Migration ``22b23085394d`` dropped the column E-18 demoted; the balance
    the owner states at setup is a ``tracking_start`` :class:`LoanAnchorEvent`.
    This lock catches any migration or model edit that re-introduces a stored
    loan balance.
    """
    assert _column_row("current_principal") is None, (
        "budget.loan_params.current_principal still exists -- plan step "
        "recurrence:R20 dropped it (migration 22b23085394d); the balance "
        "stated at setup is a tracking_start LoanAnchorEvent, not a column."
    )
    assert "ck_loan_params_curr_principal" not in _check_names(), (
        "ck_loan_params_curr_principal still exists -- it went with the "
        "column at migration 22b23085394d."
    )


def test_interest_rate_column_dropped():
    """DH-#56: ``interest_rate`` no longer exists on ``loan_params``.

    The Commit-15 nullable demotion was the precursor to the OPT-1
    destructive drop, which DH-#56 carried out (migration
    ``b7d2f4a619c5``).  The loan's rate is now derived from its
    origination :class:`RateHistory` row, so the column must be gone --
    this lock catches any migration that re-introduces the denormalized
    scalar.
    """
    assert _column_row("interest_rate") is None, (
        "budget.loan_params.interest_rate still exists -- DH-#56 dropped "
        "it (migration b7d2f4a619c5); the loan's rate is now the "
        "origination RateHistory row, not a stored column."
    )


# -- the R20 migration -------------------------------------------------------


def test_r20_migration_revision_pair_and_downgrade_artefacts():
    """The migration revises the card-terms head and its downgrade names both artefacts."""
    assert _M_R20.revision == "22b23085394d"
    assert _M_R20.down_revision == "97f92340fffc"
    with open(_M_R20.__file__, encoding="utf-8") as handle:
        downgrade_source = handle.read().split("def downgrade():", 1)[1]
    assert '"current_principal"' in downgrade_source
    assert "ck_loan_params_curr_principal" in downgrade_source


def _tracking_starts(account_id):
    """Return ``[(anchor_date, anchor_balance)]`` of an account's tracking_start rows."""
    source_id = ref_cache.loan_anchor_source_id(
        LoanAnchorSourceEnum.TRACKING_START,
    )
    rows = (
        _db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account_id, source_id=source_id)
        .order_by(LoanAnchorEvent.id)
        .all()
    )
    return [(r.anchor_date, r.anchor_balance) for r in rows]


def test_r20_backfill_records_the_unasserted_stated_balance_once(
    app, db, seed_user,
):
    """The backfill writes ONE tracking_start per unasserted mid-life loan and nothing else.

    Round trip on the live test database: the downgrade's literal SQL re-adds
    ``current_principal`` NULL with its CHECK; five loans are planted through
    the shared factory and their stored balances set by raw UPDATE --

    * ``midlife``: originated 2024-01-01, set up today, stored $17,020.47 --
      the REC-519 shape, and the one loan the arm is for;
    * ``legacy``: the same, plus a stored ``origination``-source row (what
      ``d3d25212504b``'s backfill wrote for every loan of its day).  Every
      reader synthesizes the origination and ignores that row, so it is not
      an assertion and the loan IS backfilled -- the predicate excludes the
      two assertion sources by name, never "any row";
    * ``trued``: the same, plus a ``user_trueup`` -- production's shape for
      both live loans -- so NOT backfilled;
    * ``sameday``: originated today, stored $32,402.45 -- the origination IS
      its assertion, so nothing is written;
    * ``blank``: originated 2024-01-01, stored NULL -- nothing to carry;

    then the migration's own backfill statement runs.  Exactly two
    ``tracking_start`` rows result, on ``midlife`` and ``legacy``, each dated
    its setup day (``created_at`` read as the owner's civil day) and carrying
    its stored balance; running the statement again writes nothing more,
    since both now carry an assertion.  The upgrade's literal drop restores
    the post-migration schema for the tests that follow.  A control with a
    carve-out needs a case per direction: ``trued`` is the exclusion firing,
    ``legacy`` is the exclusion NOT firing where a looser reading would.

    The setup day is asserted against the SAME derivation the migration
    spells -- ``(created_at AT TIME ZONE 'America/New_York')::date`` -- read
    off the planted row rather than off the wall clock, so the test cannot
    flake across the UTC-versus-Eastern midnight window the derivation exists
    for; ``sameday`` originates on that derived day for the same reason.
    """
    with app.app_context():
        probe = create_loan_account(
            seed_user, db.session, name="R20 probe",
            principal=Decimal("1.00"), term=12,
            origination_date=date(2024, 1, 1), payment_day=1,
        )
        setup_day = db.session.execute(text(
            "SELECT (created_at AT TIME ZONE 'America/New_York')::date "
            "FROM budget.loan_params WHERE account_id = :a"
        ), {"a": probe.id}).scalar()
        assert date(2024, 1, 1) < setup_day
        db_today = db.session.execute(text(
            "SELECT (now() AT TIME ZONE 'America/New_York')::date"
        )).scalar()
        assert abs(setup_day - db_today) <= timedelta(days=1)

        midlife = create_loan_account(
            seed_user, db.session, name="R20 midlife",
            principal=Decimal("32402.45"), term=72,
            origination_date=date(2024, 1, 1), payment_day=14,
        )
        sameday = create_loan_account(
            seed_user, db.session, name="R20 sameday",
            principal=Decimal("32402.45"), term=72,
            origination_date=setup_day, payment_day=14,
        )
        blank = create_loan_account(
            seed_user, db.session, name="R20 blank",
            principal=Decimal("32402.45"), term=72,
            origination_date=date(2024, 1, 1), payment_day=14,
        )
        legacy = create_loan_account(
            seed_user, db.session, name="R20 legacy",
            principal=Decimal("32402.45"), term=72,
            origination_date=date(2024, 1, 1), payment_day=14,
        )
        db.session.execute(text(
            "INSERT INTO budget.loan_anchor_events "
            "    (account_id, anchor_date, anchor_balance, source_id) "
            "VALUES (:a, :d, :b, :s)"
        ), {
            "a": legacy.id, "d": date(2024, 1, 1), "b": Decimal("32402.45"),
            "s": ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.ORIGINATION),
        })
        trued = create_loan_account(
            seed_user, db.session, name="R20 trued",
            principal=Decimal("32402.45"), term=72,
            origination_date=date(2024, 1, 1), payment_day=14,
        )
        insert_trueup_event(
            loan_params_for(db.session, trued.id), Decimal("17020.47"),
            anchor_date=date(2026, 5, 22),
        )
        db.session.commit()
        for account in (probe, midlife, sameday, blank, legacy, trued):
            assert _tracking_starts(account.id) == []

        # Down: the migration's literal downgrade -- the column back, NULL.
        db.session.execute(text(
            "ALTER TABLE budget.loan_params "
            "ADD COLUMN current_principal NUMERIC(12, 2) NULL"
        ))
        db.session.execute(text(
            "ALTER TABLE budget.loan_params "
            "ADD CONSTRAINT ck_loan_params_curr_principal "
            "CHECK (current_principal >= 0)"
        ))
        db.session.execute(text(
            "UPDATE budget.loan_params SET current_principal = :b "
            "WHERE account_id = :a"
        ), {"a": midlife.id, "b": Decimal("17020.47")})
        db.session.execute(text(
            "UPDATE budget.loan_params SET current_principal = :b "
            "WHERE account_id = :a"
        ), {"a": sameday.id, "b": Decimal("32402.45")})
        for account in (legacy, trued):
            db.session.execute(text(
                "UPDATE budget.loan_params SET current_principal = :b "
                "WHERE account_id = :a"
            ), {"a": account.id, "b": Decimal("17020.47")})
        db.session.commit()

        try:
            unasserted = db.session.execute(
                text(_M_R20._UNASSERTED_MIDLIFE_LOANS_SQL),  # pylint: disable=protected-access
            ).fetchall()
            assert sorted((r[0], r[1], r[2]) for r in unasserted) == sorted([
                (midlife.id, setup_day, Decimal("17020.47")),
                (legacy.id, setup_day, Decimal("17020.47")),
            ])

            db.session.execute(text(_M_R20._BACKFILL_TRACKING_START_SQL))  # pylint: disable=protected-access
            db.session.commit()

            assert _tracking_starts(midlife.id) == [
                (setup_day, Decimal("17020.47")),
            ]
            assert _tracking_starts(legacy.id) == [
                (setup_day, Decimal("17020.47")),
            ]
            assert _tracking_starts(trued.id) == []
            assert _tracking_starts(sameday.id) == []
            assert _tracking_starts(blank.id) == []
            assert _tracking_starts(probe.id) == []

            # A second run finds both loans asserted and writes nothing more.
            db.session.execute(text(_M_R20._BACKFILL_TRACKING_START_SQL))  # pylint: disable=protected-access
            db.session.commit()
            assert _tracking_starts(midlife.id) == [
                (setup_day, Decimal("17020.47")),
            ]
            assert _tracking_starts(legacy.id) == [
                (setup_day, Decimal("17020.47")),
            ]
            assert _tracking_starts(trued.id) == []
        finally:
            # Up: the migration's literal drop, so the schema the rest of the
            # suite reads is the post-R20 one whatever this test asserted.
            db.session.rollback()
            db.session.execute(text(
                "ALTER TABLE budget.loan_params "
                "DROP CONSTRAINT ck_loan_params_curr_principal"
            ))
            db.session.execute(text(
                "ALTER TABLE budget.loan_params DROP COLUMN current_principal"
            ))
            db.session.commit()

        assert _column_row("current_principal") is None
