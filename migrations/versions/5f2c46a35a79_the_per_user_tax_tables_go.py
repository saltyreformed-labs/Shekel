"""the per-user tax tables go: five salary tax tables and ref.tax_types are dropped

Revision ID: 5f2c46a35a79
Revises: 6534c1bfeeb1
Create Date: 2026-11-10
Review: developer, 2026-11-10 (rulings plan#3210 and plan#3211, card plan#1142,
answered "1A 2A 3A": the delete checks the rows against "the law as it stood
when the rows stopped being read", and "Approve. All six tables go in one
release, and the undo works as above": "The undo would bring back the five
tables empty, in production's exact shape, and the three-name list with its
names.  Your old rows would not come back."
Destructive: the five tables, their rows and their audit triggers, and
ref.tax_types with its three rows, are dropped)

Plan step **salary:X-at-2**, card plan#1142.  Until plan step salary:X-at-1
each user held a copy of the tax law in five ``salary`` tables -- the federal
bracket sets and their brackets, North Carolina's rate and standard deduction,
FICA, and North Carolina's child-deduction tiers -- which signup and every
deploy copied in and the Settings page could overwrite.  Since X-at-1 the law
lives once, in the code (``app/tax_law``, ruling **R-SAL74**), and nothing reads
or writes the tables.  ``ref.tax_types`` existed only so a stored state row
could name its tax type by a key; the law states it as ``TaxTypeEnum``, which
the calculator now compares itself (plan#2025's amendment to the card, ruling
**plan#3211**).  This revision deletes all six:

  1. **It refuses, writing nothing** (:func:`refuse_a_row_the_law_lacks`), while
     any stored row of the five tables is not a row of :data:`FEDERAL`,
     :data:`FICA`, :data:`STATES`, :data:`NC_STANDARD_DEDUCTION` and
     :data:`NC_CHILD_TIERS`, naming each such row (rulings **plan#3210** and
     **plan#3285**): a row passes only when it is exactly one of the law's,
     so a figure the app never seeded, a year, state or filing status it
     never had, or the law's own figure on another row refuses.  A row the
     law has and a table lacks refuses nothing: its absence throws no figure
     away.
  2. It prints how many rows it found in each table, each a row of the law.
  3. It drops the five tables' audit triggers by name, then the tables, the
     brackets before their sets, then ``ref.tax_types``.

**What is compared against what.**  The law as X-at-1 left it, frozen here,
because the law in the code moves: plan step salary:X-at-7 (plan#1143) corrects
2025's federal standard deductions ($15,000 to $15,750 for a single filer) and
2026's head-of-household 24% / 32% boundary ($201,775 to $201,750), and a
comparison against the code would then refuse rows nobody touched.  It is a
copy because a migration imports nothing from the app.  **Measured on
2026-11-10** (plan#3274): its 130 rows (8 bracket sets, 56 brackets, 8 state
rows, 2 FICA rows and 56 child-deduction tiers) equal row for row, on the
columns below, both ``app.tax_law``'s 2025 and 2026 at ``dev``'s ``2df736ba1``
and the seed constants signup copied, ``app/services/tax_seed_data.py`` at
``adceff2ed`` (``2ceedf9e4^``, before X-at-1 deleted it), each mapped to rows
as signup's ``_seed_tax_data_for_user`` and the seed's row builders mapped it:
no row on one side only, and a figure planted in the code's copy was found.
At ``2df736ba1``, ``app/tax_law/_year_2025.py``, ``_year_2026.py`` and
``_north_carolina.py`` each have one commit, X-at-1's ``2ceedf9e4``.  The 2027
law (plan#1141) adds no stored row.  Each table is compared on the
columns that state law, with its filing status and tax type by their ``ref``
names:

* ``tax_bracket_sets``: year, filing status, the standard deduction, the child
  tax credit, the credit for other dependents, and the child credit's
  refundable cap.  Not ``description``, a label the seed wrote: production's
  sixteen are the seed's own ("2026 Federal - Head Of Household").
* ``tax_brackets``: its set's year and filing status, the rung's position
  (``sort_order``, from 0 at the bottom), its floor, its ceiling and its rate.
* ``state_tax_configs``: state, year, filing status, tax type, flat rate and
  standard deduction.
* ``fica_configs``: year and the five figures.
* ``state_child_deductions``: state, year, filing status, the tier's floor,
  its ceiling and its deduction per child.

No row's id, owner or ``created_at`` is compared: any user's row of the law is
a row of the law.  A stored figure equal in value to the law's at another
scale (``15000`` beside ``15000.00``) is the same figure.

**Measured on production, read-only, 2026-11-10, stamped ``897a6663d221``**:
260 rows -- 16 bracket sets, 112 brackets, 16 state rows, 4 FICA rows and 112
child-deduction tiers, two users' 2025 and 2026 -- every one a row of the law,
so this revision drops them.  **The change log** (``system.audit_log``,
re-counted on production, read-only, 2026-11-10 at 18:14 UTC, stamped
``3748145b83e3``) holds 48 writes to the five tables, all in one minute,
2026-07-07 17:30 UTC, all by the owner role ``shekel_user``: 12 inserts and 4
updates of ``state_tax_configs`` (each update its ``filing_status_id``), and
32 updates of ``tax_bracket_sets`` (16 its ``child_credit_amount``, 16 its
``child_credit_refundable_cap``) -- the writes ``b7f3a2c1d4e5``'s backfill
makes on two users' rows.  It holds no delete, none by the app's role
``shekel_app``, and none to ``tax_brackets``, ``fica_configs`` or
``state_child_deductions``: that migration inserted the 112 tiers before it
attached their trigger.  The log begins 2026-05-06, so it says nothing of a
write before that day; the comparison above is what grades the rows.

**No money moves.**  Nothing has read the tables since X-at-1, so the drop
changes no figure.

**The drop and the change log.**  ``AUDITED_TABLES`` loses the five in the same
change, so ``EXPECTED_TRIGGER_COUNT`` falls by five.  ``DROP TABLE`` fires no
row-level DELETE, so the log keeps what it holds and records no deletion.  The
only foreign key onto the five is ``tax_brackets``' onto ``tax_bracket_sets``,
dropped first; the only one onto ``ref.tax_types`` is ``state_tax_configs``',
dropped with its table (measured on production, 2026-11-10).  No drop names
CASCADE, so a key added later makes it refuse.

**On a database built from an empty chain** the tables hold nothing, so the
refusal passes vacuously; the rebuild migration (``a5be2a99ea14``) no longer
attaches the five triggers on replay, which its existence guard absorbs.

**The downgrade recreates ``ref.tax_types`` with its three names and the five
tables EMPTY, in production's exact shape** (ruling plan#3211), read off
production's catalogue at ``897a6663d221`` on 2026-11-10: every column in
production's order with its type, nullability and default, and every key,
CHECK and index under production's name.  That shape is not quite the one the
chain builds.  Measured on 2026-11-10 on two databases built from the empty
chain, to ``0fe9c332b9b0`` and to this revision's ``down_revision``,
``6534c1bfeeb1``, which came out the same, against production's catalogue as
this revision's tests hold it (read at ``897a6663d221``; its columns re-read
that day at ``3748145b83e3`` and unchanged): every key, CHECK and index of the
six tables is the same, and so is every column of ``ref.tax_types``,
``tax_brackets``, ``fica_configs`` and ``state_child_deductions``.  These
differ, and nothing else:

* ``state_tax_configs``' column order.  Production's is ``id``, ``user_id``,
  ``tax_type_id``, ``state_code``, ``tax_year``, ``flat_rate``,
  ``standard_deduction``, ``created_at``, ``filing_status_id``.  The chain's
  is ``id``, ``user_id``, ``tax_type_id``, ``state_code``, ``flat_rate``,
  ``created_at``, ``standard_deduction``, ``tax_year``, ``filing_status_id``:
  ``22b3dd9d9ed3`` creates the table through ``created_at``, then
  ``02b1ff12b08c`` adds ``standard_deduction`` and ``7abcbf372fff``
  ``tax_year``.  ``tax_year`` is fifth in production and eighth in the chain,
  ``flat_rate`` sixth and fifth, ``created_at`` eighth and sixth;
  ``standard_deduction`` is seventh in both, after ``flat_rate`` in production
  and after ``created_at`` in the chain.
* ``tax_bracket_sets``' column order.  Production holds
  ``child_credit_amount`` and ``other_dependent_credit_amount`` sixth and
  seventh, before ``description`` and ``created_at``; the chain holds them
  eighth and ninth, after those two, where ``b4c7d8e9f012`` adds them.
* Those two credit columns' default: none in production, ``DEFAULT 0`` in the
  chain (``b4c7d8e9f012``).

Production's is what is rebuilt; a migration below this one names columns,
never positions, and nothing writes the tables.  The ids of
``ref.tax_types``' rows come from its sequence, in the seed list's order; the
previous image's ref cache reads them by name.  The downgrade also attaches
the five audit triggers again, unlike the downgrades of ``858f569b2c17`` and
``78782c6ac75e``: the previous image counts at least one audit trigger per
table its ``AUDITED_TABLES`` names before a deploy commits
(``require_audit_triggers``), and its list names these five, so a database
undone without them is one it refuses.  The app role's grants come back
with the tables, from the schemas' default privileges (``scripts/init_db_role.sql``).
No row is restored; the deploy's pre-release dump keeps them.
"""
from collections.abc import Iterable
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '5f2c46a35a79'
# The head of ``dev`` when it was last taken in, measured on 2026-11-10 at
# ``2df736ba1`` (234 revisions, one head; 235 with this one).  Another lane
# landing a revision first makes two heads, which git merges cleanly and
# ``flask db upgrade`` refuses: re-point this at the measured head as the last
# edit before the pull request.
down_revision = '6534c1bfeeb1'
branch_labels = None
depends_on = None


#: The five tables this revision drops and its downgrade recreates, each with
#: its audit trigger, in the order the downgrade creates them.
TABLES = (
    'tax_bracket_sets',
    'tax_brackets',
    'state_tax_configs',
    'fica_configs',
    'state_child_deductions',
)

#: The order the upgrade drops the five in: the brackets before their sets,
#: which a key ties them to.
_DROP_ORDER = (
    'tax_brackets',
    'tax_bracket_sets',
    'state_tax_configs',
    'fica_configs',
    'state_child_deductions',
)

#: The ``ref.filing_statuses`` names of the four filing statuses the law models.
_SINGLE = 'single'
_JOINT = 'married_jointly'
_SEPARATE = 'married_separately'
_HEAD = 'head_of_household'

#: Each year's federal rules for each filing status, as X-at-1 left the law:
#: the standard deduction, the child tax credit, the credit for other
#: dependents, the child credit's refundable cap, and the bracket ladder from
#: the bottom up as ``(floor, ceiling, rate)``, a rung's ``sort_order`` its
#: position from 0.  Frozen here: a migration reads no app module.
FEDERAL = {
    (2025, _SINGLE): ('15000.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '11925.00', '0.1000'),
        ('11925.00', '48475.00', '0.1200'),
        ('48475.00', '103350.00', '0.2200'),
        ('103350.00', '197300.00', '0.2400'),
        ('197300.00', '250525.00', '0.3200'),
        ('250525.00', '626350.00', '0.3500'),
        ('626350.00', None, '0.3700'),
    )),
    (2025, _JOINT): ('30000.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '23850.00', '0.1000'),
        ('23850.00', '96950.00', '0.1200'),
        ('96950.00', '206700.00', '0.2200'),
        ('206700.00', '394600.00', '0.2400'),
        ('394600.00', '501050.00', '0.3200'),
        ('501050.00', '751600.00', '0.3500'),
        ('751600.00', None, '0.3700'),
    )),
    (2025, _SEPARATE): ('15000.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '11925.00', '0.1000'),
        ('11925.00', '48475.00', '0.1200'),
        ('48475.00', '103350.00', '0.2200'),
        ('103350.00', '197300.00', '0.2400'),
        ('197300.00', '250525.00', '0.3200'),
        ('250525.00', '375800.00', '0.3500'),
        ('375800.00', None, '0.3700'),
    )),
    (2025, _HEAD): ('22500.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '17000.00', '0.1000'),
        ('17000.00', '64850.00', '0.1200'),
        ('64850.00', '103350.00', '0.2200'),
        ('103350.00', '197300.00', '0.2400'),
        ('197300.00', '250500.00', '0.3200'),
        ('250500.00', '626350.00', '0.3500'),
        ('626350.00', None, '0.3700'),
    )),
    (2026, _SINGLE): ('16100.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '12400.00', '0.1000'),
        ('12400.00', '50400.00', '0.1200'),
        ('50400.00', '105700.00', '0.2200'),
        ('105700.00', '201775.00', '0.2400'),
        ('201775.00', '256225.00', '0.3200'),
        ('256225.00', '640600.00', '0.3500'),
        ('640600.00', None, '0.3700'),
    )),
    (2026, _JOINT): ('32200.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '24800.00', '0.1000'),
        ('24800.00', '100800.00', '0.1200'),
        ('100800.00', '211400.00', '0.2200'),
        ('211400.00', '403550.00', '0.2400'),
        ('403550.00', '512450.00', '0.3200'),
        ('512450.00', '768700.00', '0.3500'),
        ('768700.00', None, '0.3700'),
    )),
    (2026, _SEPARATE): ('16100.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '12400.00', '0.1000'),
        ('12400.00', '50400.00', '0.1200'),
        ('50400.00', '105700.00', '0.2200'),
        ('105700.00', '201775.00', '0.2400'),
        ('201775.00', '256225.00', '0.3200'),
        ('256225.00', '384350.00', '0.3500'),
        ('384350.00', None, '0.3700'),
    )),
    (2026, _HEAD): ('24150.00', '2200.00', '500.00', '1700.00', (
        ('0.00', '17700.00', '0.1000'),
        ('17700.00', '67450.00', '0.1200'),
        ('67450.00', '105700.00', '0.2200'),
        ('105700.00', '201775.00', '0.2400'),
        ('201775.00', '256200.00', '0.3200'),
        ('256200.00', '640600.00', '0.3500'),
        ('640600.00', None, '0.3700'),
    )),
}

#: Each year's FICA as X-at-1 left the law: the Social Security rate and wage
#: base, the Medicare rate, and the Additional Medicare rate and threshold.
FICA = {
    2025: ('0.0620', '176100.00', '0.0145', '0.0090', '200000.00'),
    2026: ('0.0620', '184500.00', '0.0145', '0.0090', '200000.00'),
}

#: Each year's states as X-at-1 left the law: the ``ref.tax_types`` name of the
#: state's tax type and its flat rate.  Its deductions are the statute's
#: unindexed tables below, which every year reads.
STATES = {
    (2025, 'NC'): ('flat', '0.0425'),
    (2026, 'NC'): ('flat', '0.0399'),
}

#: North Carolina's standard deduction by filing status (N.C.G.S.
#: 105-153.5(a)(1)), as X-at-1 left the law.
NC_STANDARD_DEDUCTION = {
    _SINGLE: '12750.00',
    _JOINT: '25500.00',
    _SEPARATE: '12750.00',
    _HEAD: '19125.00',
}

#: The single and married-filing-separately filers' one tier table.
_NC_SINGLE_OR_SEPARATE_TIERS = (
    ('0.00', '20000.00', '3000.00'),
    ('20000.00', '30000.00', '2500.00'),
    ('30000.00', '40000.00', '2000.00'),
    ('40000.00', '50000.00', '1500.00'),
    ('50000.00', '60000.00', '1000.00'),
    ('60000.00', '70000.00', '500.00'),
    ('70000.00', None, '0.00'),
)

#: North Carolina's child-deduction tiers by filing status (N.C.G.S.
#: 105-153.5(a1)), as X-at-1 left the law: ``(floor, ceiling, deduction per
#: child)`` from the lowest AGI up.
NC_CHILD_TIERS = {
    _SINGLE: _NC_SINGLE_OR_SEPARATE_TIERS,
    _JOINT: (
        ('0.00', '40000.00', '3000.00'),
        ('40000.00', '60000.00', '2500.00'),
        ('60000.00', '80000.00', '2000.00'),
        ('80000.00', '100000.00', '1500.00'),
        ('100000.00', '120000.00', '1000.00'),
        ('120000.00', '140000.00', '500.00'),
        ('140000.00', None, '0.00'),
    ),
    _SEPARATE: _NC_SINGLE_OR_SEPARATE_TIERS,
    _HEAD: (
        ('0.00', '30000.00', '3000.00'),
        ('30000.00', '45000.00', '2500.00'),
        ('45000.00', '60000.00', '2000.00'),
        ('60000.00', '75000.00', '1500.00'),
        ('75000.00', '90000.00', '1000.00'),
        ('90000.00', '105000.00', '500.00'),
        ('105000.00', None, '0.00'),
    ),
}

#: Each table's compared columns, in the order :data:`_STORED_ROWS_SQL` reads
#: them and the refusal names them.
FIELDS = {
    'tax_bracket_sets': (
        'tax_year', 'filing_status', 'standard_deduction', 'child_credit_amount',
        'other_dependent_credit_amount', 'child_credit_refundable_cap',
    ),
    'tax_brackets': (
        'tax_year', 'filing_status', 'sort_order', 'min_income', 'max_income', 'rate',
    ),
    'state_tax_configs': (
        'state_code', 'tax_year', 'filing_status', 'tax_type', 'flat_rate',
        'standard_deduction',
    ),
    'fica_configs': (
        'tax_year', 'ss_rate', 'ss_wage_base', 'medicare_rate', 'medicare_surtax_rate',
        'medicare_surtax_threshold',
    ),
    'state_child_deductions': (
        'state_code', 'tax_year', 'filing_status', 'agi_min', 'agi_max',
        'deduction_per_child',
    ),
}

#: Each table's every row: its id, its owner, then its :data:`FIELDS`.  The
#: ``ref`` names and a bracket's set are read through LEFT JOINs, so no row
#: escapes the comparison: one whose join found nothing names ``None``, which no
#: law row holds.
_STORED_ROWS_SQL = {
    'tax_bracket_sets': """
        SELECT b.id, b.user_id, b.tax_year, f.name, b.standard_deduction,
               b.child_credit_amount, b.other_dependent_credit_amount,
               b.child_credit_refundable_cap
          FROM salary.tax_bracket_sets b
          LEFT JOIN ref.filing_statuses f ON f.id = b.filing_status_id
         ORDER BY b.id
    """,
    'tax_brackets': """
        SELECT t.id, s.user_id, s.tax_year, f.name, t.sort_order, t.min_income,
               t.max_income, t.rate
          FROM salary.tax_brackets t
          LEFT JOIN salary.tax_bracket_sets s ON s.id = t.bracket_set_id
          LEFT JOIN ref.filing_statuses f ON f.id = s.filing_status_id
         ORDER BY t.id
    """,
    'state_tax_configs': """
        SELECT c.id, c.user_id, c.state_code, c.tax_year, f.name, k.name,
               c.flat_rate, c.standard_deduction
          FROM salary.state_tax_configs c
          LEFT JOIN ref.filing_statuses f ON f.id = c.filing_status_id
          LEFT JOIN ref.tax_types k ON k.id = c.tax_type_id
         ORDER BY c.id
    """,
    'fica_configs': """
        SELECT id, user_id, tax_year, ss_rate, ss_wage_base, medicare_rate,
               medicare_surtax_rate, medicare_surtax_threshold
          FROM salary.fica_configs
         ORDER BY id
    """,
    'state_child_deductions': """
        SELECT d.id, d.user_id, d.state_code, d.tax_year, f.name, d.agi_min,
               d.agi_max, d.deduction_per_child
          FROM salary.state_child_deductions d
          LEFT JOIN ref.filing_statuses f ON f.id = d.filing_status_id
         ORDER BY d.id
    """,
}

#: The three names ``ref.tax_types`` carried, in the seed list's order.
#: Inline rather than read from ``app/ref_seeds.py``: this revision's own
#: commit deletes that entry, so a downgrade run from a later checkout would
#: find nothing to restore.  It runs on the table the line before creates, so
#: no name can already be there.
_RESEED_SQL = "INSERT INTO ref.tax_types (name) VALUES ('flat'), ('none'), ('bracket')"

#: The five tables' audit trigger as production carried it, the shape
#: ``app.audit_infrastructure`` attaches; spelled here because a migration
#: reads no app module.
_CREATE_TRIGGER_SQL = (
    "CREATE TRIGGER audit_{table} AFTER INSERT OR UPDATE OR DELETE ON salary.{table} "
    "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
)


def _money(value: str | None) -> Decimal | None:
    """Return a frozen figure as the ``Decimal`` a stored one is read as, ``None`` kept."""
    return None if value is None else Decimal(value)


#: One row of the law on its table's :data:`FIELDS`: a year or a rung's
#: position as ``int``, a ``ref`` name or a state code as ``str``, a figure as
#: ``Decimal``, and an open ceiling as ``None``.
LawRow = tuple[int | str | Decimal | None, ...]


def _federal_rows() -> tuple[frozenset[LawRow], frozenset[LawRow]]:
    """Return :data:`FEDERAL` as its ``tax_bracket_sets`` rows, then its ``tax_brackets`` rows."""
    bracket_sets, brackets = set(), set()
    for (year, status), (deduction, child, other, cap, ladder) in FEDERAL.items():
        bracket_sets.add((
            year, status, _money(deduction), _money(child), _money(other), _money(cap),
        ))
        for position, (floor, ceiling, rate) in enumerate(ladder):
            brackets.add((year, status, position, _money(floor), _money(ceiling), _money(rate)))
    return frozenset(bracket_sets), frozenset(brackets)


def _state_rows() -> tuple[frozenset[LawRow], frozenset[LawRow]]:
    """Return :data:`STATES` as ``state_tax_configs`` rows, then ``state_child_deductions`` rows.

    Each year reads the statute's unindexed tables, :data:`NC_STANDARD_DEDUCTION`
    and :data:`NC_CHILD_TIERS`.
    """
    states, tiers = set(), set()
    for (year, state), (tax_type, rate) in STATES.items():
        for status, deduction in NC_STANDARD_DEDUCTION.items():
            states.add((state, year, status, tax_type, _money(rate), _money(deduction)))
        for status, table in NC_CHILD_TIERS.items():
            for floor, ceiling, per_child in table:
                tiers.add((
                    state, year, status, _money(floor), _money(ceiling), _money(per_child),
                ))
    return frozenset(states), frozenset(tiers)


def law_rows() -> dict[str, frozenset[LawRow]]:
    """Return each table's rows as the law X-at-1 left has them, on :data:`FIELDS`.

    Also read by this revision's tests, so "a row of the law" has one
    spelling.

    Returns:
        ``{table: frozenset of FIELDS tuples}``, one entry per table of
        :data:`TABLES`.
    """
    bracket_sets, brackets = _federal_rows()
    states, tiers = _state_rows()
    return {
        'tax_bracket_sets': bracket_sets,
        'tax_brackets': brackets,
        'state_tax_configs': states,
        'fica_configs': frozenset(
            (year, *(_money(figure) for figure in figures)) for year, figures in FICA.items()
        ),
        'state_child_deductions': tiers,
    }


def stored_rows(bind: sa.engine.Connection) -> dict[str, list[sa.engine.Row]]:
    """Return every stored row of the five tables: id, owner, then its :data:`FIELDS`.

    Args:
        bind: The connection the revision runs on.

    Returns:
        ``{table: rows}``, one entry per table of :data:`TABLES`, by id.
    """
    return {table: bind.execute(sa.text(_STORED_ROWS_SQL[table])).all() for table in TABLES}


def rows_the_law_lacks(stored: dict[str, list[sa.engine.Row]]) -> list[str]:
    """Return a description of each stored row that is not a row of the law, by table and id.

    Args:
        stored: :func:`stored_rows`.

    Returns:
        One phrase per such row, ``salary.tax_brackets row 17 (user 1):
        tax_year 2026, ...``, its compared columns as stored; empty when every
        row is the law's.
    """
    law = law_rows()
    lacking = []
    for table in TABLES:
        for row in stored[table]:
            row_id, user_id, *fields = row
            if tuple(fields) in law[table]:
                continue
            lacking.append(
                f"salary.{table} row {row_id} (user {user_id}): "
                + ", ".join(f"{name} {value}" for name, value in zip(FIELDS[table], fields))
            )
    return lacking


def refuse_a_row_the_law_lacks(stored: dict[str, list[sa.engine.Row]]) -> None:
    """Refuse, naming each, while a stored row is not a row of the law X-at-1 left.

    Rulings plan#3210 and plan#3285: a row that is not exactly one of the
    law's may be one someone typed, so the tables are not deleted while one
    is stored.

    Args:
        stored: :func:`stored_rows`.

    Raises:
        RuntimeError: Naming each such row, before anything is written.
    """
    lacking = rows_the_law_lacks(stored)
    if not lacking:
        return
    raise RuntimeError(
        f"X-at-2 refuses: {len(lacking)} stored row(s) of the five per-user tax "
        "tables are not rows of the tax law as plan step salary:X-at-1 left it "
        "(a figure the app never seeded, or a year, state or filing status it "
        "never had), so the tables cannot be deleted yet (rulings plan#3210 and "
        "plan#3285): "
        + "; ".join(lacking)
        + ".  Nothing has read these tables since X-at-1, so no figure here "
        "prices anything.  Bring each row named above to the developer, who "
        "rules what becomes of it, before this release is tried again.  "
        "Nothing was written: the revision rolls back whole."
    )


def upgrade():
    """Refuse while a stored row is not the law's; else drop the five tables and ref.tax_types."""
    bind = op.get_bind()
    stored = stored_rows(bind)
    refuse_a_row_the_law_lacks(stored)
    print(
        "X-at-2: every stored row of the five per-user tax tables is a row of the "
        "law as X-at-1 left it: "
        + ", ".join(f"{len(stored[table])} in salary.{table}" for table in TABLES)
        + "."
    )
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON salary.{table}")
    for table in _DROP_ORDER:
        op.drop_table(table, schema='salary')
    op.drop_table('tax_types', schema='ref')
    print(
        "X-at-2: salary.tax_bracket_sets, tax_brackets, state_tax_configs, "
        "fica_configs and state_child_deductions dropped with their audit "
        "triggers, then ref.tax_types."
    )


def _year_check(table: str) -> sa.CheckConstraint:
    """Return *table*'s ``tax_year`` range CHECK under production's name."""
    return sa.CheckConstraint(
        'tax_year >= 2000 AND tax_year <= 2100', name=f'ck_{table}_valid_tax_year',
    )


def _created_at() -> sa.Column:
    """Return the ``created_at`` column every one of the five carried."""
    return sa.Column(
        'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
        nullable=False,
    )


def _money_column(
    name: str, *, nullable: bool = False, server_default: str | sa.TextClause | None = None,
) -> sa.Column:
    """Return a ``numeric(12,2)`` column, with no default unless *server_default*."""
    return sa.Column(
        name, sa.Numeric(precision=12, scale=2), nullable=nullable,
        server_default=server_default,
    )


def _rate_column(
    name: str, *, nullable: bool = False, server_default: sa.TextClause | None = None,
) -> sa.Column:
    """Return a ``numeric(5,4)`` column, with no default unless *server_default*."""
    return sa.Column(
        name, sa.Numeric(precision=5, scale=4), nullable=nullable,
        server_default=server_default,
    )


def _user_key(*, name: str | None = None) -> sa.ForeignKeyConstraint:
    """Return the CASCADE key onto ``auth.users``, under PostgreSQL's name unless *name*."""
    return sa.ForeignKeyConstraint(
        ['user_id'], ['auth.users.id'], ondelete='CASCADE', name=name,
    )


def _status_key(table: str) -> sa.ForeignKeyConstraint:
    """Return *table*'s RESTRICT key onto ``ref.filing_statuses`` under production's name."""
    return sa.ForeignKeyConstraint(
        ['filing_status_id'], ['ref.filing_statuses.id'], ondelete='RESTRICT',
        name=f'fk_{table}_filing_status_id',
    )


def _create_tax_types() -> None:
    """Recreate ``ref.tax_types`` and reseed its three names.

    The PK and the ``UNIQUE (name)`` are unnamed, taking PostgreSQL's
    generated names as production's do: the convention for a single-column
    ``ref`` lookup key (developer ruling 2026-08-14, finding recurrence:F-3).
    """
    op.create_table(
        'tax_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref',
    )
    op.execute(_RESEED_SQL)


def _create_federal_tables() -> None:
    """Recreate ``tax_bracket_sets`` and ``tax_brackets`` in production's shape."""
    op.create_table(
        'tax_bracket_sets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('filing_status_id', sa.Integer(), nullable=False),
        sa.Column('tax_year', sa.Integer(), nullable=False),
        _money_column('standard_deduction'),
        _money_column('child_credit_amount'),
        _money_column('other_dependent_credit_amount'),
        sa.Column('description', sa.String(length=200), nullable=True),
        _created_at(),
        _money_column('child_credit_refundable_cap', server_default='0'),
        sa.CheckConstraint(
            'child_credit_amount >= 0', name='ck_tax_bracket_sets_nonneg_child_credit',
        ),
        sa.CheckConstraint(
            'standard_deduction >= 0', name='ck_tax_bracket_sets_nonneg_deduction',
        ),
        sa.CheckConstraint(
            'other_dependent_credit_amount >= 0',
            name='ck_tax_bracket_sets_nonneg_other_credit',
        ),
        sa.CheckConstraint(
            'child_credit_refundable_cap >= 0',
            name='ck_tax_bracket_sets_nonneg_refundable_cap',
        ),
        _year_check('tax_bracket_sets'),
        _status_key('tax_bracket_sets'),
        _user_key(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'tax_year', 'filing_status_id',
            name='uq_tax_bracket_sets_user_year_status',
        ),
        schema='salary',
    )
    op.create_table(
        'tax_brackets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bracket_set_id', sa.Integer(), nullable=False),
        _money_column('min_income'),
        _money_column('max_income', nullable=True),
        _rate_column('rate'),
        sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.CheckConstraint(
            'max_income IS NULL OR max_income >= min_income',
            name='ck_tax_brackets_income_order',
        ),
        sa.CheckConstraint('min_income >= 0', name='ck_tax_brackets_nonneg_min'),
        sa.CheckConstraint('rate >= 0 AND rate <= 1', name='ck_tax_brackets_valid_rate'),
        sa.ForeignKeyConstraint(
            ['bracket_set_id'], ['salary.tax_bracket_sets.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema='salary',
    )
    op.create_index(
        'idx_tax_brackets_bracket_set', 'tax_brackets', ['bracket_set_id', 'sort_order'],
        schema='salary',
    )


def _create_state_and_fica_tables() -> None:
    """Recreate ``state_tax_configs``, ``fica_configs`` and ``state_child_deductions``."""
    op.create_table(
        'state_tax_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tax_type_id', sa.Integer(), nullable=False),
        sa.Column('state_code', sa.String(length=2), nullable=False),
        sa.Column('tax_year', sa.Integer(), nullable=False),
        _rate_column('flat_rate', nullable=True),
        _money_column('standard_deduction', nullable=True),
        _created_at(),
        sa.Column('filing_status_id', sa.Integer(), nullable=False),
        sa.CheckConstraint(
            'standard_deduction IS NULL OR standard_deduction >= 0',
            name='ck_state_tax_configs_nonneg_standard_deduction',
        ),
        sa.CheckConstraint(
            'flat_rate IS NULL OR (flat_rate >= 0 AND flat_rate <= 1)',
            name='ck_state_tax_configs_valid_rate',
        ),
        _year_check('state_tax_configs'),
        _status_key('state_tax_configs'),
        sa.ForeignKeyConstraint(
            ['tax_type_id'], ['ref.tax_types.id'], ondelete='RESTRICT',
            name='fk_state_tax_configs_tax_type_id',
        ),
        _user_key(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'state_code', 'tax_year', 'filing_status_id',
            name='uq_state_tax_configs_user_state_year_status',
        ),
        schema='salary',
    )
    op.create_table(
        'fica_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tax_year', sa.Integer(), nullable=False),
        _rate_column('ss_rate', server_default=sa.text('0.0620')),
        _money_column('ss_wage_base', server_default=sa.text('176100')),
        _rate_column('medicare_rate', server_default=sa.text('0.0145')),
        _rate_column('medicare_surtax_rate', server_default=sa.text('0.0090')),
        _money_column('medicare_surtax_threshold', server_default=sa.text('200000')),
        _created_at(),
        sa.CheckConstraint(
            'medicare_surtax_threshold > 0', name='ck_fica_configs_positive_surtax_threshold',
        ),
        sa.CheckConstraint('ss_wage_base > 0', name='ck_fica_configs_positive_wage_base'),
        sa.CheckConstraint(
            'medicare_rate >= 0 AND medicare_rate <= 1',
            name='ck_fica_configs_valid_medicare_rate',
        ),
        sa.CheckConstraint(
            'ss_rate >= 0 AND ss_rate <= 1', name='ck_fica_configs_valid_ss_rate',
        ),
        sa.CheckConstraint(
            'medicare_surtax_rate >= 0 AND medicare_surtax_rate <= 1',
            name='ck_fica_configs_valid_surtax_rate',
        ),
        _year_check('fica_configs'),
        _user_key(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'tax_year', name='uq_fica_configs_user_year'),
        schema='salary',
    )
    op.create_table(
        'state_child_deductions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filing_status_id', sa.Integer(), nullable=False),
        sa.Column('state_code', sa.String(length=2), nullable=False),
        sa.Column('tax_year', sa.Integer(), nullable=False),
        _money_column('agi_min'),
        _money_column('agi_max', nullable=True),
        _money_column('deduction_per_child'),
        sa.Column('user_id', sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            'agi_max IS NULL OR agi_max > agi_min', name='ck_state_child_deductions_agi_order',
        ),
        sa.CheckConstraint(
            'agi_min >= 0', name='ck_state_child_deductions_nonneg_agi_min',
        ),
        sa.CheckConstraint(
            'deduction_per_child >= 0', name='ck_state_child_deductions_nonneg_deduction',
        ),
        _year_check('state_child_deductions'),
        _status_key('state_child_deductions'),
        _user_key(name='fk_state_child_deductions_user_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'state_code', 'tax_year', 'filing_status_id', 'agi_min',
            name='uq_state_child_deductions_user_state_year_status_agi',
        ),
        schema='salary',
    )


def _attach_audit_triggers(tables: Iterable[str]) -> None:
    """Attach each of *tables*' audit trigger, as production carried it."""
    for table in tables:
        op.execute(_CREATE_TRIGGER_SQL.format(table=table))


def downgrade():
    """Recreate ref.tax_types with its names and the five tables EMPTY, triggered."""
    _create_tax_types()
    _create_federal_tables()
    _create_state_and_fica_tables()
    _attach_audit_triggers(TABLES)
