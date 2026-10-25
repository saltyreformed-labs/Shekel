"""
Shekel Budget App -- Data Integrity Check: the types every check family shares

:class:`CheckSpec` (one check's definition), :class:`CheckResult` (what it
found) and :func:`run_check` (the one runner), read by the four family
modules and by the command line in ``scripts/integrity_check.py``.

**The sweep is one module per check FAMILY since plan step
``balance:X-bi-6-4c-4``** (ruling **R-BAL161**).  It had reached pylint's
1000-line module ceiling as one file just as that step had to add to three of
its families, so it was cut along the seam its ``--category`` flag already
names -- ``_integrity_referential`` (FK-*), ``_integrity_orphan`` (OR-*),
``_integrity_balance`` (BA-*) and ``_integrity_consistency`` (DC-*) --
leaving the command line and the orchestration in ``integrity_check.py``.
Each check id is defined in its family's module and nowhere else, so a note
elsewhere naming "``scripts/integrity_check.py``'s DC-11" names the tool's
check by its id, which is how the report prints it.  The move itself changed
no check and one name: the runner lost its leading underscore, because every
family module now calls it.  What the same step then changed in the checks
(FK-10, the transfer twins FK-14..16, OR-03 and BA-06) is stated at each;
its change to DC-11's join went with DC-11's leg arm at plan step
``balance:X-bi-6-4d-2``.
"""

from dataclasses import dataclass, field

from sqlalchemy import text


@dataclass
class CheckResult:
    """Result of a single integrity check.

    Attributes:
        check_id: Identifier like 'FK-01', 'OR-03', 'BA-02', 'DC-05'.
        category: One of 'referential', 'orphan', 'balance', 'consistency'.
        severity: 'critical' or 'warning'.
        description: Human-readable description of what was checked.
        passed: True if no issues found.
        detail_count: Number of violations found (0 if passed).
        details: List of dicts with violation specifics (e.g., row IDs).
    """

    check_id: str
    category: str
    severity: str
    description: str
    passed: bool
    detail_count: int = 0
    details: list = field(default_factory=list)


@dataclass(frozen=True)
class CheckSpec:
    """Declarative definition of one integrity check.

    The identity fields mirror :class:`CheckResult`; ``sql`` is the
    violation query. One spec is one row in a category's check catalog.

    Attributes:
        check_id: Identifier like 'FK-01', 'OR-03', 'BA-02', 'DC-05'.
        category: One of 'referential', 'orphan', 'balance', 'consistency'.
        severity: 'critical' or 'warning'.
        description: Human-readable description of what is checked.
        sql: SQL query that returns violating rows (empty = pass).
    """

    check_id: str
    category: str
    severity: str
    description: str
    sql: str


# ── Shared SQL ───────────────────────────────────────────────────


#: A transfer's LEG RECORDS, in the raw SQL the sweep speaks: joined onto a
#: query over ``budget.transfers x``, it yields one row per movement ``e``
#: filed under one of ``x``'s two SIDES -- each side's money, on that side's
#: account (``e.account_id``) and day (``e.settled_on``).
#:
#: It is ``app.services.transfer_legs``' one join from a transfer to its legs'
#: records (``_records._movement_link``: the from-side's key or the to-side's,
#: ruling **R-BAL88**), spelled again because raw SQL cannot call an ORM
#: expression -- the second spelling DC-11's leg arm introduced at leaf
#: ``balance:X-bi-6-4a``.  **Stated ONCE for the sweep since plan step
#: ``balance:X-bi-6-4c-4``**, when BA-06 began asking a transfer's legs too,
#: so plan step ``balance:X-bi-6-4d-2`` re-pointed it off the shadow rows and
#: onto the side links here, in one place.  BA-06 is its one reader since plan
#: step ``balance:X-bi-6-4d-2`` deleted DC-11's leg arm, whose state the
#: database refuses at commit (``app.side_band_infrastructure``).  **It asks
#: no covering test and no live-row test,
#: as the app's join asks none** (``_records._side_records``): a side link is
#: a record by ``ck_transaction_entries_side_link_is_a_record``, and a record
#: under a hidden transfer is unstorable by the deleted-row rule's transfer
#: arm.  Each check asks the TRANSFER's own status and ``is_deleted``.
TRANSFER_LEG_RECORDS_JOIN = """
            JOIN budget.transaction_entries e
              ON e.expense_transfer_id = x.id OR e.income_transfer_id = x.id"""


# ── Helper ───────────────────────────────────────────────────────


def run_check(session, spec: CheckSpec) -> CheckResult:
    """Execute a single integrity check query and return a CheckResult.

    Args:
        session: SQLAlchemy session.
        spec: The check definition: identity fields plus the SQL query
            whose result rows are the violations.

    Returns:
        CheckResult with pass/fail status and violation details.
    """
    result = session.execute(text(spec.sql))
    rows = result.fetchall()
    columns = list(result.keys()) if rows else []
    details = [dict(zip(columns, row)) for row in rows]
    return CheckResult(
        check_id=spec.check_id,
        category=spec.category,
        severity=spec.severity,
        description=spec.description,
        passed=len(rows) == 0,
        detail_count=len(rows),
        details=details,
    )
