"""Shared definitions for the side-record band rule: a transfer's records follow its status.

**A transfer's two side records are dated exactly while the transfer is
settled** (plan step ``balance:X-bi-6-4d-2``, design D8; the drift the
balance arc's specification says is "made unrepresentable here").  A
transfer's status is ONE column on ``budget.transfers``, and each side's
payment record (``transaction_entries.expense_transfer_id`` /
``income_transfer_id``) carries that side's day.  The transfer writer moves
both together (``transfer_service._status.apply_status_to_all_three``), so
every state a door writes is one of three:

* **settled, both sides recorded** -- each side holds one DATED record;
* **settled, neither side recorded** -- a ``$0.00`` close (rulings
  **R-BAL82**, **R-BAL90**, **R-BAL141**: a close of nothing stores no record,
  no day and no link);
* **not settled** -- no side's record is dated; a revert keeps each record
  UN-dated (ruling **R-BAL61**).

Two states no door writes are what this rule refuses: a DATED record under a
transfer that is not settled (ruling **R-BAL79**'s "counted once" case, and
ruling **R-BAL140**'s reverse), and a settled transfer with an UN-dated
record, or a record on one side only (ruling **R-BAL147**'s
``UndatedSettleError``).  Until this rule the readers carried branches for
both, and the transfer service carried their repairs (``drifted_sides_only``
with ledger row **BAL-578**, the restore's status repair); the repairs went
with this rule, and the readers' branches are plan step ``X-bi-6-5``'s to
delete (finding **BAL-527**).

**The figure arm** (:data:`FIGURE_ARM`; plan step ``balance:X-bi-6-4d-2``'s
4d-4 half, rulings **R-BAL365**, **R-BAL366**) ties the transfer's ONE settled
figure (``budget.transfers.settled_figure``) to the same records: a figure
above zero is recorded on BOTH sides, and a transfer recording neither side
stores no figure or -- a ``$0.00`` close -- ``0.00``.  A settled transfer
always states its figure, and a transfer that is not settled never holds
``0.00`` (ruling R-BAL365: "a revert of a $0.00 close clears it").  A side
record holds no figure of its own (``transaction_entries``'
``ck_transaction_entries_side_record_states_no_figure``), so this is the
rule that says WHICH transfers' records may exist at all.

**A deferred constraint trigger, because the rule is about two tables at
COMMIT.**  A settle writes the transfer's status, its figure and both
records in one unit of work, in an order the unit of work chooses; between
those statements the state is exactly what the rule forbids.  So the check
is asked of each transfer a statement touched, once the transaction
commits, against what is stored then.  **Every attachment but one carries a
``WHEN`` clause**: a deferred trigger without one queues an event on every
row it watches, and pending events block an ``ALTER TABLE`` later in the
same transaction (the lesson ``opening_infrastructure._movement`` records,
which broke CI twice).  An entry that links no transfer and a transfer whose
status and figure did not move queue nothing.  **The exception is a
transfer's INSERT** (the figure arm's): "settled" is a fact of a
``ref.statuses`` row, which a ``WHEN`` clause cannot read, so a transfer
inserted settled with no figure is graded only by an attachment that fires
on every insert (rulings **R-BAL339**: a refusal is the database's, and
**R-BAL357**: a database refusal is a backstop behind the create's own).
One event per inserted transfer, graded at commit; a later revision that
inserts transfers and then alters ``budget.transfers`` gets no commit between
the two, because the deploy runs the whole chain in ONE transaction
(``scripts/init_database.py``, ruling **R-BAL105**) through
``app.migration_runner.upgrade_to_head``, which commits nothing (ruling
**R-BAL114**).  It must drain the event before the ``ALTER`` instead, with
``SET CONSTRAINTS budget.ck_side_record_figure_on_insert IMMEDIATE``: the
drain :mod:`app.opening_infrastructure` states as ``SET CONSTRAINTS ALL
IMMEDIATE``, and the one ``test_dc02_detects_self_transfer``
(``tests/test_scripts/test_integrity_check/test_the_consistency_checks.py``)
runs.

**The arms are a LITERAL per revision** (:data:`BAND_ARM`,
:data:`FIGURE_ARM`, :data:`ALL_ARMS`), the precedent ``opening_infrastructure``
and ``deleted_row_infrastructure`` set: a migration imports the LIVE module,
and a chain replay of an old revision must install what that revision
declared, not what a later step added.  A revision passes the tuple it
censused; the two scripts that materialise a HEAD database pass
:data:`ALL_ARMS`.  The check function is COMPOSED from the arms
(:func:`_check_function_sql`), so a replay of ``e616adf7fe22``'s
``(BAND_ARM,)`` installs the band's text byte for byte.

**Three callers must produce identical infrastructure**, the contract
:mod:`app.append_only_infrastructure` states: the Alembic revisions that
install it (``e616adf7fe22`` the band, ``b7d2e4f19a63`` the figure arm),
``scripts/init_database.py`` and ``scripts/build_test_template.py``.  The
test image's check counts :data:`SIDE_BAND_TRIGGERS` on the baked template,
so a template missing an attachment is rebuilt rather than trusted.

**Caller contract: both tables and the side links must already exist.**
"""

from __future__ import annotations

from typing import Callable

#: The first arm: a side's record is dated exactly while its transfer is
#: settled.  A later revision that changes the rule adds an arm rather than
#: editing this one's text, so a replay of ``e616adf7fe22`` installs what it
#: declared.
BAND_ARM = "band"

#: The second arm (``b7d2e4f19a63``): the transfer's settled figure agrees
#: with its records and its status.  It reads the band's record counts, so it
#: extends the band arm and means nothing without it.
FIGURE_ARM = "figure"

#: Every arm this module builds, in the order they were added: the tuple a
#: HEAD database is made of.
ALL_ARMS: tuple[str, ...] = (BAND_ARM, FIGURE_ARM)

#: The check, asked of one transfer.
_CHECK_FUNCTION = "budget.refuse_a_side_record_off_its_band_for"

#: The trigger function each attachment runs.
_TRIGGER_FUNCTION = "budget.refuse_a_side_record_off_its_band"

#: ``(trigger name, schema-qualified table)`` for every attachment, the band
#: arm's first.  Exported so the image check can count them and a lift can
#: name them.
SIDE_BAND_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("ck_side_record_band_on_insert", "budget.transaction_entries"),
    ("ck_side_record_band_on_update", "budget.transaction_entries"),
    ("ck_side_record_band_on_delete", "budget.transaction_entries"),
    ("ck_side_record_band_on_status", "budget.transfers"),
    ("ck_side_record_figure_on_insert", "budget.transfers"),
    ("ck_side_record_figure_on_change", "budget.transfers"),
)

#: The rule's opening, over one transfer as it stands at COMMIT.  A transfer
#: that no longer exists has no records to grade (its side keys are NO
#: ACTION, so its records went first).  At most one record per side
#: (``uq_transaction_entries_one_*_side_record``), so two dated records are
#: one per side.
_CHECK_HEAD_SQL = f"""
CREATE OR REPLACE FUNCTION {_CHECK_FUNCTION}(p_transfer_id integer)
RETURNS void AS $$
DECLARE
    v_settled boolean;
    v_dated integer;
    v_undated integer;
"""

#: The figure arm's one variable, declared only when the arm is installed.
_FIGURE_DECLARATION_SQL = "    v_figure numeric(12, 2);\n"

#: The band arm's clauses, byte for byte what ``e616adf7fe22`` installed.
_BAND_BODY_SQL = f"""BEGIN
    IF p_transfer_id IS NULL THEN
        RETURN;
    END IF;
    SELECT s.is_settled INTO v_settled
    FROM budget.transfers x
    JOIN ref.statuses s ON s.id = x.status_id
    WHERE x.id = p_transfer_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    SELECT count(*) FILTER (WHERE e.settled_on IS NOT NULL),
           count(*) FILTER (WHERE e.settled_on IS NULL)
      INTO v_dated, v_undated
    FROM budget.transaction_entries e
    WHERE e.expense_transfer_id = p_transfer_id
       OR e.income_transfer_id = p_transfer_id;
    IF v_settled AND (v_undated > 0 OR v_dated NOT IN (0, 2)) THEN
        RAISE EXCEPTION
            'transfer % is settled but its sides hold % dated and % '
            'un-dated payment record(s): a settled transfer records each '
            'side on its day, or neither side when it closed at zero (rule '
            '{_TRIGGER_FUNCTION})',
            p_transfer_id, v_dated, v_undated;
    END IF;
    IF NOT v_settled AND v_dated > 0 THEN
        RAISE EXCEPTION
            'transfer % is not settled but % of its payment records are '
            'dated: a record is dated only while its transfer is settled '
            '(rule {_TRIGGER_FUNCTION})',
            p_transfer_id, v_dated;
    END IF;
"""

#: The figure arm's clauses, after the band's: a state both arms refuse is
#: worded by the band.  The record count is the band's (``v_dated +
#: v_undated``): two records when the figure is above zero, none otherwise,
#: so ONE record is refused whatever the figure.  No ``CASE`` in an ``IF``:
#: PL/pgSQL ends an ``IF``'s condition at its first ``THEN``.
_FIGURE_BODY_SQL = f"""    SELECT x.settled_figure INTO v_figure
    FROM budget.transfers x
    WHERE x.id = p_transfer_id;
    IF v_settled AND v_figure IS NULL THEN
        RAISE EXCEPTION
            'transfer % is settled but stores no settled figure: a settled '
            'transfer states what moved, 0.00 when it closed at zero (rule '
            '{_TRIGGER_FUNCTION})',
            p_transfer_id;
    END IF;
    IF NOT v_settled AND v_figure = 0 THEN
        RAISE EXCEPTION
            'transfer % is not settled but stores a settled figure of 0.00: '
            'the revert of a close of nothing clears its figure (rule '
            '{_TRIGGER_FUNCTION})',
            p_transfer_id;
    END IF;
    IF v_dated + v_undated <> 2 * coalesce(v_figure > 0, false)::integer THEN
        RAISE EXCEPTION
            'transfer % stores a settled figure of % but holds % payment '
            'record(s): a figure above zero is recorded on both sides, and '
            'no figure or 0.00 on neither (rule {_TRIGGER_FUNCTION})',
            p_transfer_id, coalesce(v_figure::text, 'none'),
            v_dated + v_undated;
    END IF;
"""

#: The rule's close.
_CHECK_TAIL_SQL = """END;
$$ LANGUAGE plpgsql
"""

#: The trigger function: the transfer a row names, before and after the event.
_CREATE_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_TABLE_NAME = 'transfers' THEN
        PERFORM {_CHECK_FUNCTION}(NEW.id);
        RETURN NULL;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        PERFORM {_CHECK_FUNCTION}(
            coalesce(NEW.expense_transfer_id, NEW.income_transfer_id)
        );
    END IF;
    IF TG_OP <> 'INSERT' THEN
        PERFORM {_CHECK_FUNCTION}(
            coalesce(OLD.expense_transfer_id, OLD.income_transfer_id)
        );
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

#: The arm each attachment belongs to, by trigger name.  An attachment is
#: made only when a revision's arms name its arm, so an arm a later revision
#: adds -- with attachments on columns that revision creates -- is never
#: installed by a replay of ``e616adf7fe22``, whose literal ``(BAND_ARM,)``
#: predates those columns (the per-arm shape of
#: ``deleted_row_infrastructure._arms_rows``).
_ARM_OF = {
    "ck_side_record_band_on_insert": BAND_ARM,
    "ck_side_record_band_on_update": BAND_ARM,
    "ck_side_record_band_on_delete": BAND_ARM,
    "ck_side_record_band_on_status": BAND_ARM,
    "ck_side_record_figure_on_insert": FIGURE_ARM,
    "ck_side_record_figure_on_change": FIGURE_ARM,
}

#: Each attachment's firing clause, by trigger name: everything ``CREATE
#: CONSTRAINT TRIGGER`` says between the name and ``EXECUTE FUNCTION``.
_FIRING = {
    "ck_side_record_band_on_insert": (
        "AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.expense_transfer_id IS NOT NULL "
        "OR NEW.income_transfer_id IS NOT NULL)"
    ),
    "ck_side_record_band_on_update": (
        "AFTER UPDATE OF settled_on, expense_transfer_id, income_transfer_id "
        "ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.expense_transfer_id IS NOT NULL "
        "OR NEW.income_transfer_id IS NOT NULL "
        "OR OLD.expense_transfer_id IS NOT NULL "
        "OR OLD.income_transfer_id IS NOT NULL)"
    ),
    "ck_side_record_band_on_delete": (
        "AFTER DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (OLD.expense_transfer_id IS NOT NULL "
        "OR OLD.income_transfer_id IS NOT NULL)"
    ),
    "ck_side_record_band_on_status": (
        "AFTER UPDATE OF status_id ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (OLD.status_id IS DISTINCT FROM NEW.status_id)"
    ),
    # No WHEN: the module docstring says why this one fires on every insert.
    "ck_side_record_figure_on_insert": (
        "AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW"
    ),
    # The figure alone: its source is the pairing CHECK's, and the rule
    # never reads it.
    "ck_side_record_figure_on_change": (
        "AFTER UPDATE OF settled_figure ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (OLD.settled_figure IS DISTINCT FROM NEW.settled_figure)"
    ),
}


def _check_function_sql(arms: tuple[str, ...]) -> str:
    """Return the check function's ``CREATE`` for *arms*: the band's, then each later arm's.

    Composed rather than one literal so a revision's arm tuple decides the
    text: ``(BAND_ARM,)`` yields exactly what ``e616adf7fe22`` installed, a
    function that names no column a later revision adds.
    """
    figure = FIGURE_ARM in arms
    return (
        _CHECK_HEAD_SQL
        + (_FIGURE_DECLARATION_SQL if figure else "")
        + _BAND_BODY_SQL
        + (_FIGURE_BODY_SQL if figure else "")
        + _CHECK_TAIL_SQL
    )


def _reject_unknown_arms(arms: tuple[str, ...]) -> None:
    """Refuse an arm set this module cannot build.

    Raises:
        ValueError: When *arms* names an arm this module does not build, or
            omits :data:`BAND_ARM` -- which every arm set needs: the figure
            arm reads the band's record counts and attaches nothing that
            grades a record on its own.
    """
    unknown = tuple(arm for arm in arms if arm not in ALL_ARMS)
    if unknown:
        raise ValueError(
            f"apply_side_band_infrastructure: unknown arm(s) {unknown}; this "
            f"module builds {ALL_ARMS}."
        )
    if BAND_ARM not in arms:
        raise ValueError(
            f"apply_side_band_infrastructure: {arms} omits {BAND_ARM!r}, which "
            "every arm set extends; a database with no band rule is "
            "remove_side_band_infrastructure."
        )


def apply_side_band_infrastructure(
    executor: Callable[[str], object], *, arms: tuple[str, ...],
) -> None:
    """Make the side-record band rule equal exactly *arms*.

    The functions are created or replaced, then every attachment is dropped
    and the named arms' made again -- PostgreSQL has no ``CREATE TRIGGER IF
    NOT EXISTS`` -- so a second run leaves exactly what the first did.

    **Nothing is legalised here**: the rule grades what a transaction leaves,
    never a row already stored.  The revision that installs it refuses to run
    while a stored transfer breaks it (``e616adf7fe22``'s census).

    Args:
        executor: Single-argument callable that runs a SQL string -- ``op.execute``
            inside a migration, ``lambda s: session.execute(text(s))``
            elsewhere.  Errors propagate; the caller owns the transaction.
        arms: The arms this database's rule consists of: a migration's
            literal tuple, or :data:`ALL_ARMS` for a HEAD database.

    Raises:
        ValueError: From :func:`_reject_unknown_arms`.
    """
    _reject_unknown_arms(arms)
    executor(_check_function_sql(arms))
    executor(_CREATE_TRIGGER_FUNCTION_SQL)
    for name, table in SIDE_BAND_TRIGGERS:
        executor(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        if _ARM_OF[name] in arms:
            executor(
                f"CREATE CONSTRAINT TRIGGER {name} "
                f"{_FIRING[name].format(table=table)} "
                f"EXECUTE FUNCTION {_TRIGGER_FUNCTION}()"
            )


def remove_side_band_infrastructure(executor: Callable[[str], object]) -> None:
    """Drop every attachment, then both functions -- the downgrade's half.

    Args:
        executor: As :func:`apply_side_band_infrastructure`.
    """
    for name, table in SIDE_BAND_TRIGGERS:
        executor(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    executor(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")
    executor(f"DROP FUNCTION IF EXISTS {_CHECK_FUNCTION}(integer)")
