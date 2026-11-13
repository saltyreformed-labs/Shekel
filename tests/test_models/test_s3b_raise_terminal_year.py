"""``salary.salary_raises.terminal_year`` -- plan step **salary:S3-b**.

The additive half of ruling **R-SAL11**: the last year a raise is believed
to happen is a fact ON THE RAISE, ``NULL`` meaning indefinitely.  This step
adds the column and its three CHECKs and writes no value; the reader, the
write door and the deletion of
``auth.user_settings.merit_raise_horizon_years`` are the cutover step's.

**What these tests grade, and why the identity case is the important one.**
``app.services.salary_raises.apply_raises`` read this attribute
through ``getattr(raise_obj, "terminal_year", None)`` from plan step
**salary:S3-a**, so the column was LIVE to the paycheck engine the moment it
existed on the model -- there was no dormant period to rely on.  *Plan step
salary:X-av-3a deleted ``apply_raises``; the walk that reads the column is
:meth:`app.services.payroll_basis.PayrollBasis.base_pay_on` since, and
:class:`TestTheColumnIsLiveAndNullIsTheIdentity` asks it there.*  The migration
therefore claims something specific: that an all-``NULL`` column is the
identity, because ``NULL`` is exactly what that ``getattr`` answered when
the attribute did not exist.  That claim is the one thing the rest of the
suite cannot see, since every existing test passes rows whose answer is
unchanged either way.  :class:`TestTheColumnIsLiveAndNullIsTheIdentity`
states both directions of it.

Four classes, each grading a different tier, because an adversarial review
of this step found the first draft grading only one:

* :class:`TestTerminalYearChecks` -- the three CHECKs, through raw SQL.
* :class:`TestTheColumnStoresNothingByItself` -- the live catalog: nullable,
  and no ``server_default``.  Asked of PostgreSQL rather than of the model,
  because a migration can add a default without touching the class and
  SQLAlchemy omits a valueless column from the INSERT, so the database's
  default would fire with every model-level assertion still green.  That is
  the F-068 / F-134 drift class ``test_c25_column_invariants`` exists for.
* :class:`TestTheColumnIsLiveAndNullIsTheIdentity` -- the arithmetic.
* :class:`TestTheMigrationDoesWhatItSays` -- what ``upgrade`` and
  ``downgrade`` actually CALL, since a constraint defined and never created
  is the silent form of this failure and CI runs only the upgrade
  direction.

The CHECK tests go through raw SQL rather than the ORM so the assertion is
about the STORAGE tier and nothing else.  *No form layer is being bypassed
today* -- ``terminal_year`` is in no Marshmallow schema and no template, so
there is nothing above the database to refuse first.  An adversarial review
of this step corrected a draft of this paragraph that borrowed the C-24
sweep's rationale wholesale; that rationale becomes true at the cutover, and
these tests are already the right shape for it.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    start_test_pay_list,
)


#: The three constraint names, kept as constants so a rename in
#: ``app/models/salary_raise.py`` or in the migration fails here loudly
#: rather than turning an assertion into ``None == None``.
CK_ORDERED = "ck_salary_raises_terminal_year_not_before_effective"
CK_WINDOW = "ck_salary_raises_valid_terminal_year"
CK_RECURRING = "ck_salary_raises_terminal_year_only_on_a_recurring_raise"

#: A made-up pay entry (ruling salary:R-SAL98: test figures are made up).
#: The ruling this file grades was decided on the owner's own figures, which
#: these tests repeated until plan step salary:X-av-3b swapped them out.
BASE = Decimal("2847.53")
MERIT_PCT = Decimal("0.0250")

#: What that merit raise compounds ONE PAYCHECK to at 2040-12-01, both ways
#: -- the pay list's walk (:meth:`PayrollBasis.base_pay_on`), each 2.5% step
#: rounded half-up to the cent (ruling R-SAL60), from the entry on the
#: owner's first saved payday (2024-01-05, before the raise's first landing
#: on 2027-01-01).  By hand, from 2,847.53: 2027 2,918.72 (2,918.71825);
#: 2028 2,991.69 (2,991.688); 2029 3,066.48 (3,066.48225); 2030 3,143.14
#: (3,143.142); 2031 3,221.72 (3,221.7185); 2032 3,302.26 (3,302.263);
#: 2033 3,384.82 (3,384.8165); 2034 3,469.44 (3,469.4405); 2035 3,556.18
#: (3,556.176); 2036 3,645.08 (3,645.0845); 2037 3,736.21 (3,736.207);
#: 2038 3,829.62 (3,829.61525); 2039 3,925.36 (3,925.3605); 2040 4,023.49
#: (4,023.494).  The yearly walk this replaced at X-av-3a compounded the
#: annual salary and rounded once: from 2,847.53 x 26 = 74,035.78 that is
#: ``83,764.69`` and ``104,610.62``, where the pay list's figures are
#: ``3,221.72 x 26 = 83,764.72`` and ``4,023.49 x 26 = 104,610.74``.  Named
#: absolutely rather than compared side to side: an equality whose two sides
#: run ONE producer passes when that producer is wrong identically on both,
#: which is the hole an adversarial review of this step found in the
#: identity assertion below.
TERMINATED_2031_AT_2040 = Decimal("3221.72")     # 2027..2031, five times
UNTERMINATED_AT_2040 = Decimal("4023.49")        # 2027..2040, fourteen


def _merit_type_id() -> int:
    """Return the ``merit`` raise-type id through the ref cache.

    The spelling production uses (``pension_calculator`` resolves the COLA
    id the same way), rather than a ``.name`` query repeated once per case.
    """
    return ref_cache.raise_type_id(RaiseTypeEnum.MERIT)


def _make_profile(seed_user) -> SalaryProfile:
    """Return a committed salary profile to hang raises off."""
    single_id = (
        db.session.query(FilingStatus).filter_by(name="single").one().id
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=single_id,
        name="S3-b",
    )
    db.session.add(profile)
    start_test_pay_list(profile, BASE)
    db.session.commit()
    return profile


def _insert_raise(profile_id: int, effective_year: int,
                  terminal_year: "int | None", *, recurring: bool = True):
    """Raw-INSERT one 3% merit raise straight into the storage tier."""
    db.session.execute(
        text(
            "INSERT INTO salary.salary_raises "
            "(salary_profile_id, raise_type_id, effective_date, "
            " percentage, is_recurring, terminal_year, "
            " version_id, created_at) "
            "VALUES (:pid, :tid, make_date(:eff, 1, 1), 0.0300, :rec, :term, "
            " 1, now())"
        ),
        {
            "pid": profile_id, "tid": _merit_type_id(),
            "eff": effective_year, "term": terminal_year, "rec": recurring,
        },
    )
    db.session.flush()


def _stored_raise(profile_id: int) -> SalaryRaise:
    """Return the one raise on *profile_id*."""
    return (
        db.session.query(SalaryRaise)
        .filter_by(salary_profile_id=profile_id).one()
    )


class TestTerminalYearChecks:
    """Storage refuses an end year that cannot mean anything."""

    def test_terminal_year_before_effective_year_rejected(
        self, app, seed_user,
    ):
        """A raise that ends before it starts is unstorable.

        The state the global horizon it replaces left storable and silently
        dropped: under a 2031 cutoff a raise recorded for 2035 applied in no
        projected year at all.  Here the database refuses the row instead.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            with pytest.raises(IntegrityError) as info:
                _insert_raise(profile.id, 2035, 2031)
            db.session.rollback()
            assert constraint_name_from(info.value) == CK_ORDERED

    def test_terminal_year_above_2100_rejected(self, app, seed_user):
        """The end year is held to its start year's own 2000-2100 window.

        2101 satisfies the ordering CHECK and the recurring one, so only the
        window CHECK can fire and the name assertion is unambiguous.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            with pytest.raises(IntegrityError) as info:
                _insert_raise(profile.id, 2027, 2101)
            db.session.rollback()
            assert constraint_name_from(info.value) == CK_WINDOW

    def test_an_end_year_on_a_one_time_raise_is_rejected(
        self, app, seed_user,
    ):
        """A one-time raise cannot carry an end year (developer, 2026-09-05).

        It would be INERT rather than wrong -- ``_applications`` gates a
        one-time raise on ``eff_year <= terminal_year``, which the ordering
        CHECK already guarantees -- and a stored value that provably cannot
        move a figure is what this constraint exists to make unstorable.
        Both adversarial reviews of this step found the state; the developer
        ruled the constraint in rather than leaving it to a later migration
        against a table that would by then hold values.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            with pytest.raises(IntegrityError) as info:
                _insert_raise(profile.id, 2027, 2031, recurring=False)
            db.session.rollback()
            assert constraint_name_from(info.value) == CK_RECURRING

    def test_terminal_year_equal_to_effective_year_accepted(
        self, app, seed_user,
    ):
        """One believed year is a real answer, so the bound is ``>=``.

        A recurring raise believed for exactly the year it starts applies
        once and then stops -- which is a different fact from a one-time
        raise, because it says the owner expected it to repeat and no
        longer does.  ``>`` would refuse it.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            _insert_raise(profile.id, 2027, 2027)
            db.session.commit()
            assert _stored_raise(profile.id).terminal_year == 2027

    def test_terminal_year_null_accepted(self, app, seed_user):
        """``NULL`` is the belief that the raise carries no end.

        Not an unanswered question: a COLA is ``NULL`` because inflation
        does not stop at a planning horizon, and it is what every row this
        migration adds the column to starts at.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            _insert_raise(profile.id, 2026, None)
            db.session.commit()
            assert _stored_raise(profile.id).terminal_year is None


class TestTheColumnStoresNothingByItself:
    """PostgreSQL supplies no value for this column, so every row is NULL.

    Asked of the live catalog rather than of the mapped class, and an
    adversarial review of this step is why: a migration can add a
    ``server_default`` without touching ``app/models/salary_raise.py``, and
    SQLAlchemy omits a column with no value from the INSERT, so the
    database's default fires while every model-level assertion stays green.
    ``tests/test_models/test_c25_column_invariants.py`` states that drift
    class (F-068 / F-134) and this borrows its catalog read.
    """

    @staticmethod
    def _catalog(column: str):
        """Return ``(attnotnull, server_default_text)`` for one column."""
        row = db.session.execute(text(
            "SELECT a.attnotnull, "
            "       pg_get_expr(d.adbin, d.adrelid) AS server_default "
            "FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_attrdef d "
            "    ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "WHERE n.nspname = 'salary' "
            "  AND c.relname = 'salary_raises' "
            "  AND a.attname = :column "
            "  AND a.attnum > 0 AND NOT a.attisdropped"
        ), {"column": column}).one()
        return row.attnotnull, row.server_default

    def test_the_column_is_nullable_with_no_server_default(self, app, db):  # pylint: disable=unused-argument,redefined-outer-name
        """NOT NULL or a default would clamp every raise nobody answered for.

        Either one ends the identity this step rests on, and in the
        money-moving direction: a defaulted end year terminates raises the
        owner never gave one to, understating a long projection.
        """
        with app.app_context():
            not_null, server_default = self._catalog("terminal_year")
            assert not_null is False, (
                "salary.salary_raises.terminal_year became NOT NULL; "
                "'believed indefinitely' has no representation left and "
                "every raise now carries a clamp"
            )
            assert server_default is None, (
                f"salary.salary_raises.terminal_year gained a server "
                f"default ({server_default!r}); every INSERT that omits "
                f"the column now stores an end year, and the paycheck "
                f"engine clamps a raise nobody answered for"
            )

    def test_a_row_written_without_the_column_reads_null(
        self, app, seed_user,
    ):
        """An INSERT that never names the column stores ``NULL``.

        The catalog assertion above says PostgreSQL supplies nothing; this
        observes it doing so.  It is the shape of every row that existed
        before the migration ran, which is the population the migration's
        "nothing is backfilled" claim is about and which no test could
        otherwise reach -- the template is built by running the migration,
        so its rows are all post-column.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            db.session.execute(
                text(
                    "INSERT INTO salary.salary_raises "
                    "(salary_profile_id, raise_type_id, effective_date, "
                    " percentage, is_recurring, "
                    " version_id, created_at) "
                    "VALUES (:pid, :tid, DATE '2027-01-01', 0.0250, true, 1, "
                    " now())"
                ),
                {"pid": profile.id, "tid": _merit_type_id()},
            )
            db.session.commit()
            assert _stored_raise(profile.id).terminal_year is None


class TestTheColumnIsLiveAndNullIsTheIdentity:
    """The engine reads this column already, and ``NULL`` changes nothing.

    Both halves are load bearing and they grade opposite directions.  The
    first is the migration's whole claim to be additive: were ``NULL`` not
    the identity, adding the column would move money inside a step whose
    docstring says it moves none.  The second proves the column is not inert
    -- so the cutover step reads THIS field rather than needing a second
    horizon derived beside it, which is the shape ruling **R-SAL11** exists
    to delete.
    """

    class _NoSuchAttribute:
        """A raise-like value with no ``terminal_year`` attribute at all.

        What every :class:`~app.models.salary_raise.SalaryRaise` row looked
        like to ``apply_raises`` before this column existed.  Written as
        a class with fixed fields rather than a ``SalaryRaise`` with the
        attribute deleted, because deleting a mapped attribute on an ORM
        instance triggers a lazy reload and puts the field back.

        **It is now a REFUSED input rather than an equivalent one**, which
        is what ``test_a_shape_without_an_end_year_is_refused_not_defaulted``
        below pins -- see that test for the re-derivation.
        """

        effective_date = date(2027, 1, 1)
        is_recurring = True
        percentage = MERIT_PCT
        flat_amount = None

    @staticmethod
    def _row(profile_id: int, terminal_year: "int | None") -> SalaryRaise:
        """Commit and return the developer's own merit raise as an ORM row."""
        row = SalaryRaise(
            salary_profile_id=profile_id,
            raise_type_id=_merit_type_id(),
            effective_date=date(2027, 1, 1),
            percentage=MERIT_PCT,
            is_recurring=True,
            terminal_year=terminal_year,
        )
        db.session.add(row)
        db.session.commit()
        return row

    def test_a_null_end_year_is_believed_indefinitely(
        self, app, seed_user,
    ):
        """A stored ``NULL`` means the raise carries no end.

        The developer's own merit raise as a committed row, walked to 2040.
        The engine reads the profile's ORM rows through
        :attr:`PayrollBasis.raises`, so this is the production shape and not
        a fabricated one.  *Asked of ``apply_raises`` on the yearly salary
        until plan step salary:X-av-3a deleted it; re-stated on the pay
        list's walk at the per-paycheck figure :data:`UNTERMINATED_AT_2040`
        works by hand.*

        **It names an ABSOLUTE figure**, and an adversarial review of plan
        step salary:S3-b is why: this case was written as an equality
        against a second walk, and an equality whose two sides run ONE
        producer passes when that producer is wrong identically on both -- a
        regression returning ``base_salary`` unchanged would have satisfied
        it.

        *It compared against a shape with NO ``terminal_year`` attribute
        until plan step salary:S3-c, because that identity was S3-b's whole
        claim to be additive.  S3-c deleted the attribute-less shape from
        the codebase, so the comparison has no second side left; what it
        asserted about THIS side is kept, and the refusal that replaced the
        other side is the next test.*
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            row = self._row(profile.id, None)
            assert row.terminal_year is None
            basis = PayrollBasis(profile, calendar_for(profile.user_id))
            assert (
                basis.base_pay_on(date(2040, 12, 1)).per_paycheck
                == UNTERMINATED_AT_2040
            )

    def test_a_shape_without_an_end_year_is_refused_not_defaulted(
        self, app, seed_user,
    ):
        """The walk has no silent default for a missing end year (salary:S3-c).

        **The re-derivation of what the identity above used to assert.**
        :func:`apply_raises` read ``getattr(raise_obj, "terminal_year",
        None)`` while the pension projector fabricated ``TerminatedRaise``
        values that predated the column; plan step salary:S3-c deleted that
        fabrication, every caller passes a row, and the read is now plain
        attribute access.

        The direction that matters is the money one.  A default of ``None``
        means "believed indefinitely", so a malformed or half-built raise
        would compound FOREVER and read as a larger salary rather than
        failing -- ``UNTERMINATED_AT_2040`` instead of an error, on a
        pension that reads the projection's last years.  Re-adding the
        ``getattr`` default would restore exactly that, and this is what
        fails if someone does.

        It is written against the walk rather than the reader because the
        reader is private; the failure is what a caller would see.  *The walk
        was ``apply_raises`` until plan step salary:X-av-3a deleted it; it is
        :meth:`PayrollBasis.base_pay_on` now, handed the shape as the raise
        set a what-if supplies (``raise_terms``, ruling R-SAL20), which every
        read converts through :meth:`~app.services.salary_raises.RaiseTerms
        .of` -- plain attribute access, so the refusal is still an
        ``AttributeError`` naming the missing end year.*
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            basis = PayrollBasis(
                profile, calendar_for(profile.user_id),
                raise_terms=(self._NoSuchAttribute(),),
            )
            with pytest.raises(AttributeError, match="terminal_year"):
                basis.base_pay_on(date(2040, 12, 1))

    def test_a_stored_end_year_actually_terminates_the_raise(
        self, app, seed_user,
    ):
        """A row carrying an end year stops compounding after it.

        The mutation that proves the test above is not vacuous: if
        the walk ignored the column, both walks there would
        agree at ``UNTERMINATED_AT_2040`` and the identity would be
        measuring nothing.  2027..2031 is five applications of 2.5%, each
        rounded to the cent on the paycheck (ruling R-SAL60, plan step
        salary:X-av-3a; quantized once at the end of ``apply_raises``'s
        yearly walk until then): 2,847.53 -> 2,918.72 -> 2,991.69 ->
        3,066.48 -> 3,143.14 -> 3,221.72, and nothing after 2031.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            row = self._row(profile.id, 2031)
            basis = PayrollBasis(profile, calendar_for(profile.user_id))
            assert (
                basis.base_pay_on(date(2040, 12, 1)).per_paycheck
                == TERMINATED_2031_AT_2040
            )


class TestTheMigrationDoesWhatItSays:
    """``upgrade`` creates exactly the three CHECKs; ``downgrade`` drops them.

    Driven rather than scanned -- ``op`` is replaced with a recorder and each
    direction is CALLED -- which is the pattern
    ``test_ref_identity_sequences.TestUpgradeExecutesEveryResyncStatement``
    established for the same reason: a constraint DEFINED in the module and
    never created reads as though the migration installed it, and the deploy
    is the first thing to find out.

    It also gives the DOWNGRADE direction its only coverage in the suite.
    CI builds the test template by running the Alembic chain to head, so the
    upgrade path is exercised on every run and the downgrade path on none;
    Definition of Done item 7 asks for both.  The DDL itself is not executed
    here, because ``ALTER TABLE`` takes an ACCESS EXCLUSIVE lock that
    conflicts with the xdist workers -- the standing reason the repo's other
    migration-direction suites are execution-anchored source checks
    (``test_anchor_cache_downgrade`` states it).
    """

    MIGRATION = "c9a4e17b53d8_a_raise_carries_the_last_year_it_is_believed.py"

    class _Recorder:
        """Stand-in for ``alembic.op`` recording calls instead of running them."""

        def __init__(self):
            """Start with an empty call log."""
            self.calls: "list[tuple]" = []

        def add_column(self, table, column, schema=None):
            """Record one ADD COLUMN."""
            self.calls.append(
                ("add_column", schema, table, column.name,
                 column.nullable, column.server_default),
            )

        def create_check_constraint(self, name, table, condition, schema=None):
            """Record one CREATE CHECK."""
            self.calls.append(
                ("create_check", schema, table, name, condition),
            )

        def drop_constraint(self, name, table, type_=None, schema=None):
            """Record one DROP CONSTRAINT."""
            self.calls.append(("drop_constraint", schema, table, name, type_))

        def drop_column(self, table, column, schema=None):
            """Record one DROP COLUMN."""
            self.calls.append(("drop_column", schema, table, column))

        def execute(self, statement):
            """Record any raw SQL -- there should be none."""
            self.calls.append(("execute", statement))

    def _drive(self, monkeypatch, direction: str) -> "list[tuple]":
        """Call *direction* on the migration with a recording ``op``."""
        module = load_migration_module(self.MIGRATION)
        recorder = self._Recorder()
        monkeypatch.setattr(module, "op", recorder)
        getattr(module, direction)()
        return recorder.calls

    def test_upgrade_adds_a_nullable_column_and_three_checks_and_nothing_else(
        self, monkeypatch,
    ):
        """The column is nullable with no server default, then three CHECKs.

        **The absence of an ``execute`` is the assertion that matters.**  The
        migration's central claim is that it BACKFILLS NOTHING -- an
        all-NULL column being the identity is what makes the step additive --
        and a backfill would arrive as raw SQL.  Nothing else in the suite
        can see a statement added here.
        """
        calls = self._drive(monkeypatch, "upgrade")
        assert calls[0] == (
            "add_column", "salary", "salary_raises", "terminal_year",
            True, None,
        )
        assert [c[:4] for c in calls[1:]] == [
            ("create_check", "salary", "salary_raises",
             CK_ORDERED),
            ("create_check", "salary", "salary_raises",
             CK_WINDOW),
            ("create_check", "salary", "salary_raises",
             CK_RECURRING),
        ]
        assert not [c for c in calls if c[0] == "execute"], (
            "the migration executes raw SQL; it claims to backfill nothing, "
            "and a backfill here changes what the paycheck engine answers "
            "inside a step whose docstring says it changes nothing"
        )

    def test_downgrade_drops_all_three_checks_and_the_column(
        self, monkeypatch,
    ):
        """Reverting leaves the table as it was, in dependency-safe order.

        A downgrade that dropped the column while keeping a constraint
        defined would fail mid-revert on a real database, and a downgrade
        that forgot a constraint would leave one behind under a later
        re-upgrade's name.
        """
        calls = self._drive(monkeypatch, "downgrade")
        assert [c[:4] for c in calls] == [
            ("drop_constraint", "salary", "salary_raises", CK_RECURRING),
            ("drop_constraint", "salary", "salary_raises", CK_WINDOW),
            ("drop_constraint", "salary", "salary_raises", CK_ORDERED),
            ("drop_column", "salary", "salary_raises", "terminal_year"),
        ]
        assert all(
            c[4] == "check" for c in calls if c[0] == "drop_constraint"
        )
