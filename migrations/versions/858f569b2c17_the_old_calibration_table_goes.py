"""the old calibration table goes: salary.calibration_overrides is dropped

Revision ID: 858f569b2c17
Revises: 69634349bdce
Create Date: 2026-11-02
Review: developer, 2026-10-05 (ruling R-SAL124, plan#676: "Yes, delete it":
"The table and its old entries are deleted, but only once every calibration
date the app still has a record of has its pay stub entered.  Undoing the step
brings the table back empty; the pre-release backup keeps the old entries."
Destructive: the table, its rows and its audit trigger are dropped)

Plan step **salary:S11-d**, card plan#1155 (rulings **R-SAL42** fork 9,
**R-SAL124**, **R-SAL127**).  A calibration was one pay stub's five figures --
Gross Pay and the four taxes -- with four EFFECTIVE RATES derived from them,
which priced every paycheck the profile had, past and future.  Since plan step
salary:S11-c-2c a paycheck's taxes are priced from the profile's transcribed,
dated pay stubs, the door that wrote calibrations is deleted, and nothing reads
the table (ruling **R-SAL100**).  This revision deletes it:

  1. **It refuses, writing nothing** (:func:`refuse_a_calibration_without_its_stub`),
     while any calibration it finds held has no pay stub on its date, naming
     each date and job (fork 9: "the drop refuses while an old calibration's
     date has no stub").
  2. It prints, for each calibration it finds held, any of the four taxes that
     differ from its stub's, or that all four agree (:func:`tax_report`;
     ruling **R-SAL127**, "Taxes in the update, gross at rehearsal").
  3. It drops the table's audit trigger, then the table.

**What "held" can mean, and its bound.**  The calibrations this revision finds
(:func:`held_calibrations`) are the live rows and every state of the table the
change log still holds: an INSERT's and an UPDATE's ``new_data``, an UPDATE's
and a DELETE's ``old_data``.  That is every calibration the database still has
a record of, R-SAL124's words, and NOT every calibration the data has ever
held: the change log begins at the audit infrastructure's rebuild and is pruned
after ``AUDIT_RETENTION_DAYS`` (``scripts/audit_cleanup.py``), so a calibration
deleted before the one or older than the other is beyond it.  The states are
read by one extraction over ``to_jsonb`` of each, the shape the audit trigger
writes, and identical ones are counted once.

**What "a stub on its date" means.**  A pay stub of the same salary profile
whose ``payday`` is the calibration's ``pay_stub_date``.  Its "Use for pricing"
switch is not read: the question is whether the document is transcribed, not
whether it prices.

**What is compared, and where** (R-SAL127).  The four taxes are stored on both
sides, so this revision sets each calibration's beside its stub's withholding
of the same kind (``ref.withholding_kinds``) and prints any that differ.  It
adds nothing up and loads no app module: a stub's Gross Pay is derived, never
stored, so it is compared before the release, at the rehearsal on a copy of
production while the old rows still exist, by
``tests/manual/measure_s11d_old_calibrations.py``, which reads the calibrations
through :func:`held_calibrations` and adds each stub's Gross Pay up with the
stub page's own rule (``app.services.pay_stub_gross.checked_gross``).  This
printout lands in a deploy log, after the rows are gone, for the record.

**No money moves.**  Nothing has read the table since plan step
salary:S11-c-2c, so the drop changes no figure.

**The drop and the change log.**  ``AUDITED_TABLES`` loses the table in the
same change, so ``EXPECTED_TRIGGER_COUNT`` falls by one.  The trigger is
dropped first and by name so the intent is visible; ``DROP TABLE`` fires no
row-level DELETE, so each live row's last state stays in the log as the
``new_data`` of its last INSERT or UPDATE.  No foreign key points at the table
(the last, ``calibration_deduction_overrides``, went at ``78782c6ac75e``), and
the drop names no CASCADE, so one added later makes it refuse.

**On a database built from an empty chain** the table holds nothing and the log
nothing for it, so the refusal passes vacuously; the rebuild migration
(``a5be2a99ea14``) no longer attaches this table's trigger on replay, which the
``IF EXISTS`` absorbs.  A database cloned from production before its stubs were
entered (the shared dev database's 2026-09-05 snapshot) is refused here until
they are: catching it up is the developer's call.

**The downgrade recreates the table EMPTY** (R-SAL124), with the shape
production held at ``69634349bdce`` (measured on its catalogue 2026-11-02):
sixteen columns, the primary key and the CASCADE foreign key under
PostgreSQL's generated names, the unique profile key, the ten CHECKs, both
timestamps NOT NULL (``8a21d16c9bde``) and ``is_active`` defaulting true
(``c5d20b701a4e``).  No audit trigger: the table is not in ``AUDITED_TABLES``,
whose rebuild migration owns the triggers (``78782c6ac75e``'s precedent).  The
rows are not restored; the deploy's pre-release dump keeps them.
"""
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '858f569b2c17'
# The head of ``dev`` at authoring, measured (222 revisions, one head).
# Another lane landing a revision first makes two heads, which git merges
# cleanly and ``flask db upgrade`` refuses: re-point this at the measured head
# as the last edit before the pull request.
down_revision = '69634349bdce'
branch_labels = None
depends_on = None


#: The table this revision drops and its downgrade recreates.
_TABLE = 'calibration_overrides'

#: Each of the four taxes a calibration stored, as its column, the
#: ``ref.withholding_kinds`` name a stub records the same tax under, and the
#: words the printout uses.  Frozen here: a migration reads no app module.
_TAXES = (
    ('actual_federal_tax', 'federal_income', 'Federal income tax'),
    ('actual_state_tax', 'state_income', 'State income tax'),
    ('actual_social_security', 'social_security', 'Social Security'),
    ('actual_medicare', 'medicare', 'Medicare'),
)

#: Every calibration the database still has a record of, each distinct state
#: once (the module docstring): the live rows and both images of every change
#: the log holds for the table, read by ONE extraction over ``to_jsonb``.
_HELD_CALIBRATIONS_SQL = """
    WITH states AS (
        SELECT to_jsonb(c) AS state FROM salary.calibration_overrides c
        UNION ALL
        SELECT a.old_data FROM system.audit_log a
         WHERE a.table_schema = 'salary' AND a.table_name = 'calibration_overrides'
           AND a.old_data IS NOT NULL
        UNION ALL
        SELECT a.new_data FROM system.audit_log a
         WHERE a.table_schema = 'salary' AND a.table_name = 'calibration_overrides'
           AND a.new_data IS NOT NULL
    )
    SELECT DISTINCT
           (state->>'pay_stub_date')::date AS pay_stub_date,
           (state->>'salary_profile_id')::integer AS salary_profile_id,
           (state->>'id')::integer AS calibration_id,
           (state->>'actual_gross_pay')::numeric(10, 2) AS actual_gross_pay,
           (state->>'actual_federal_tax')::numeric(10, 2) AS actual_federal_tax,
           (state->>'actual_state_tax')::numeric(10, 2) AS actual_state_tax,
           (state->>'actual_social_security')::numeric(10, 2) AS actual_social_security,
           (state->>'actual_medicare')::numeric(10, 2) AS actual_medicare
      FROM states
     ORDER BY pay_stub_date, salary_profile_id, calibration_id,
              actual_gross_pay, actual_federal_tax, actual_state_tax,
              actual_social_security, actual_medicare
"""

#: Every pay stub's withholdings, by kind name; a stub holding none comes back
#: once with a NULL kind, so it still counts as a stub on its date.
_STUB_TAXES_SQL = """
    SELECT s.salary_profile_id, s.payday, k.name AS kind, w.amount
      FROM salary.pay_stubs s
      LEFT JOIN salary.pay_stub_withholdings w ON w.pay_stub_id = s.id
      LEFT JOIN ref.withholding_kinds k ON k.id = w.withholding_kind_id
"""

#: The jobs' names, for the printout and the refusal.
_PROFILE_NAMES_SQL = "SELECT id, name FROM salary.salary_profiles"


def held_calibrations(bind) -> list:
    """Return every calibration the database still has a record of, each state once.

    The live rows and every state the change log holds for the table (the
    module docstring's bound).  Also read by the rehearsal's measure
    (``tests/manual/measure_s11d_old_calibrations.py``), so "held" has one
    spelling.

    Args:
        bind: The connection the revision runs on.

    Returns:
        Rows with ``pay_stub_date``, ``salary_profile_id``, ``calibration_id``,
        ``actual_gross_pay`` and the four ``actual_*`` taxes, by date.
    """
    return bind.execute(sa.text(_HELD_CALIBRATIONS_SQL)).all()


def stub_taxes(bind) -> dict:
    """Return every pay stub's four taxes, keyed by its salary profile and payday.

    Args:
        bind: The connection the revision runs on.

    Returns:
        ``{(salary_profile_id, payday): {withholding kind name: amount}}``; a
        stub holding no withholding maps to an empty dict.
    """
    stubs: dict = {}
    for row in bind.execute(sa.text(_STUB_TAXES_SQL)):
        taxes = stubs.setdefault((row.salary_profile_id, row.payday), {})
        if row.kind is not None:
            taxes[row.kind] = row.amount
    return stubs


def profile_names(bind) -> dict:
    """Return each salary profile's name, keyed by its id.

    Args:
        bind: The connection the revision runs on.

    Returns:
        ``{salary_profile_id: name}``.
    """
    return {row.id: row.name for row in bind.execute(sa.text(_PROFILE_NAMES_SQL))}


def job_words(profile_id: int, names: dict) -> str:
    """Name a calibration's job in the printout's and the refusal's plain words.

    Args:
        profile_id: The calibration's ``salary_profile_id``.
        names: :func:`profile_names`.

    Returns:
        ``job 'Name' (salary profile 1)``, or, for a job deleted since, that it
        no longer exists.
    """
    if profile_id in names:
        return f"job '{names[profile_id]}' (salary profile {profile_id})"
    return f"salary profile {profile_id}, which no longer exists"


def refuse_a_calibration_without_its_stub(held: list, stubs: dict, names: dict) -> None:
    """Refuse, naming each, while a held calibration has no pay stub on its date.

    Fork 9 of ruling R-SAL42 as R-SAL124 approved it.  A date and job are named
    once however many states of the table carry them.

    Args:
        held: :func:`held_calibrations`.
        stubs: :func:`stub_taxes`.
        names: :func:`profile_names`.

    Raises:
        RuntimeError: Naming each (date, job) without a stub, before anything
            is written.
    """
    missing = sorted({
        (row.pay_stub_date, row.salary_profile_id)
        for row in held
        if (row.salary_profile_id, row.pay_stub_date) not in stubs
    })
    if not missing:
        return
    raise RuntimeError(
        "S11-d refuses: an old calibration has no pay stub on its date, so the "
        "old calibration table cannot be deleted yet (ruling R-SAL124).  Enter "
        "each on the Pay stubs page: "
        + "; ".join(
            f"{day.isoformat()} for {job_words(profile_id, names)}"
            for day, profile_id in missing
        )
        + ".  A calibration counts while salary.calibration_overrides or the "
        "change log (system.audit_log) still holds it.  A job that no longer "
        "exists can take no stub: the developer rules what happens to it.  "
        "Nothing was written: the revision rolls back whole."
    )


def _money(amount: Decimal) -> str:
    """Word an amount as the app does: ``$1,234.56``."""
    return f"${amount:,.2f}"


def tax_report(held: list, stubs: dict, names: dict) -> list[str]:
    """Return the printout: each held calibration's four taxes beside its stub's.

    Ruling R-SAL127: any of the four that differ, "State $91.00 then, $90.00
    now", or that all four agree.  Called only after
    :func:`refuse_a_calibration_without_its_stub` passed, so every calibration
    has its stub.  A tax the stub does not record reads as differing, with the
    stub's side worded so.

    Args:
        held: :func:`held_calibrations`.
        stubs: :func:`stub_taxes`.
        names: :func:`profile_names`.

    Returns:
        One line per held calibration, in date order.
    """
    lines = []
    for row in held:
        stub = stubs[(row.salary_profile_id, row.pay_stub_date)]
        which = (
            f"the old calibration of {row.pay_stub_date.isoformat()} "
            f"({job_words(row.salary_profile_id, names)}, calibration {row.calibration_id})"
        )
        differ = [
            f"{label} {_money(getattr(row, column))} then, "
            + (f"{_money(stub[kind])} on the stub" if kind in stub else "none on the stub")
            for column, kind, label in _TAXES
            if stub.get(kind) != getattr(row, column)
        ]
        if differ:
            lines.append(f"S11-d: {which} and its pay stub differ: " + "; ".join(differ) + ".")
        else:
            lines.append(f"S11-d: {which} matches its pay stub's four taxes.")
    return lines


def upgrade():
    """Refuse while a held calibration lacks its stub; else print the taxes and drop the table."""
    bind = op.get_bind()
    held = held_calibrations(bind)
    stubs = stub_taxes(bind)
    names = profile_names(bind)
    refuse_a_calibration_without_its_stub(held, stubs, names)
    print(
        f"S11-d: {len(held)} old calibration(s) found in salary.calibration_overrides "
        "and the change log it still keeps; each has its pay stub.  Gross Pay is "
        "compared at the release rehearsal (ruling R-SAL127)."
    )
    for line in tax_report(held, stubs, names):
        print(line)
    op.execute(f"DROP TRIGGER IF EXISTS audit_{_TABLE} ON salary.{_TABLE}")
    op.drop_table(_TABLE, schema='salary')
    print("S11-d: salary.calibration_overrides dropped with its audit trigger.")


def downgrade():
    """Recreate the table EMPTY with the shape production held; no rows, no trigger."""
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('salary_profile_id', sa.Integer(), nullable=False),
        sa.Column('actual_gross_pay', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('actual_federal_tax', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('actual_state_tax', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('actual_social_security', sa.Numeric(precision=10, scale=2),
                  nullable=False),
        sa.Column('actual_medicare', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('effective_federal_rate', sa.Numeric(precision=12, scale=10),
                  nullable=False),
        sa.Column('effective_state_rate', sa.Numeric(precision=12, scale=10),
                  nullable=False),
        sa.Column('effective_ss_rate', sa.Numeric(precision=12, scale=10), nullable=False),
        sa.Column('effective_medicare_rate', sa.Numeric(precision=12, scale=10),
                  nullable=False),
        sa.Column('pay_stub_date', sa.Date(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'),
                  nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.CheckConstraint('actual_gross_pay > 0',
                           name='ck_calibration_overrides_positive_gross'),
        sa.CheckConstraint('actual_federal_tax >= 0',
                           name='ck_calibration_overrides_nonneg_federal'),
        sa.CheckConstraint('actual_state_tax >= 0',
                           name='ck_calibration_overrides_nonneg_state'),
        sa.CheckConstraint('actual_social_security >= 0',
                           name='ck_calibration_overrides_nonneg_ss'),
        sa.CheckConstraint('actual_medicare >= 0',
                           name='ck_calibration_overrides_nonneg_medicare'),
        sa.CheckConstraint(
            'effective_federal_rate >= 0 AND effective_federal_rate <= 1',
            name='ck_calibration_overrides_valid_federal_rate',
        ),
        sa.CheckConstraint(
            'effective_state_rate >= 0 AND effective_state_rate <= 1',
            name='ck_calibration_overrides_valid_state_rate',
        ),
        sa.CheckConstraint(
            'effective_ss_rate >= 0 AND effective_ss_rate <= 1',
            name='ck_calibration_overrides_valid_ss_rate',
        ),
        sa.CheckConstraint(
            'effective_medicare_rate >= 0 AND effective_medicare_rate <= 1',
            name='ck_calibration_overrides_valid_medicare_rate',
        ),
        sa.ForeignKeyConstraint(['salary_profile_id'], ['salary.salary_profiles.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('salary_profile_id', name='uq_calibration_overrides_profile'),
        schema='salary',
    )
