"""The cache ITSELF: what is loaded, when, and the guard every reader passes.

Split out of the flat ``app/ref_cache.py`` at plan step
**bank_import:X-f6e-1**, when a twenty-sixth reference table took the module
past pylint's 1,000-line ceiling (996 lines before it, 1,013 after).

**The MOVE is pure and the step's own addition rides with it**: every name
below stands exactly as it stood in the flat module, and the one thing that is
new is ``StatementBalanceEvidenceEnum``'s spec -- the table whose arrival forced
the split.  :mod:`app.ref_cache` re-exports every name this package DEFINES, so
the 98 modules that ``from app import ref_cache`` are untouched.  What it no
longer re-exports is the flat module's incidental import leakage: 26 enum
classes and 6 stdlib bindings that were module-level names only because they
were imported.  Measured before the split: 0 references to any of them through
the ``ref_cache`` namespace, anywhere in the repository.

The seam is the one the module already had: this half decides WHAT is cached
and refuses a reader who asks before it is, and :mod:`._accessors` is the
answer to "what id is this enum member".  The arrow runs one way -- the
accessors import this and it imports none of them.
"""


import functools
import logging
from dataclasses import dataclass, field
from enum import Enum
from types import ModuleType
from typing import TypedDict

import sqlalchemy.exc
from sqlalchemy.orm import Session

from app.enums import (
    AccountOpeningSourceEnum,
    AcctCategoryEnum,
    AcctTypeEnum,
    AmountSourceEnum,
    SettledDayBasisEnum,
    MovementFigureSourceEnum,
    StatementBalanceEvidenceEnum,
    BusinessDayShiftEnum,
    CalcMethodEnum,
    CompoundingFrequencyEnum,
    FilingStatusEnum,
    InterestAccrualConventionEnum,
    PaycheckLineKindEnum,
    EmployerContributionTypeEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    LoanAnchorSourceEnum,
    PeriodPlacementEnum,
    PostingKindEnum,
    PostingSourceEnum,
    RaiseTypeEnum,
    RecurrenceUnitEnum,
    RoleEnum,
    StatementSourceEnum,
    StatusEnum,
    TxnTypeEnum,
    WithholdingKindEnum,
)
from app.utils.db_errors import is_schema_behind_code

_logger = logging.getLogger(__name__)


class _AcctTypeMeta(TypedDict):
    """Cached presentation metadata for a built-in account type."""

    icon_class: str | None
    max_term_months: int | None


@dataclass
class _RefState:
    """Process-lifetime reference-cache state.

    A single module-level instance (``_cache``) holds every cached map.
    ``init()`` mutates this object's dicts in place and never rebinds it
    or the module name, so no ``global`` statement is required.

    ``enum_ids`` maps each reference enum class to its ``{member: database
    PK}`` lookup; ``acct_type_meta`` maps an account-type PK to its
    presentation metadata; ``ledger_class_debit_normal`` maps a
    ledger-account-class PK to its natural-balance side (TRUE =
    debit-normal); ``recurrence_unit_months`` maps each recurrence unit
    measured in months to its span, and ``recurrence_day_of_month_units``
    holds the units whose row says ``has_day_of_month`` (plan step plan#2302,
    ruling plan#2748); ``enum_members`` is the INVERSE of ``enum_ids``, so a
    reader holding a stored ``*_id`` resolves its member in one lookup rather
    than scanning the enum.  Written once at startup (re-written in tests) and
    read-only thereafter via the accessor functions below.

    **``enum_members`` is built for EVERY ref enum, not for the two that
    happen to need it today**, and that is a DRY decision rather than
    generosity: it replaced a bespoke ``acct_category_members`` map, and the
    second reader that wanted an id-to-member lookup (plan step
    **bank_import:X-f6e-1**) would have added a third named field doing the
    identical thing.  One inverse, built in the same sweep that builds the
    forward map, costs one dict per ref table and cannot go out of step with
    its own forward map the way two hand-maintained fields can.
    """

    enum_ids: dict[type[Enum], dict[Enum, int]] = field(default_factory=dict)
    enum_members: dict[type[Enum], dict[int, Enum]] = field(
        default_factory=dict
    )
    acct_type_meta: dict[int, _AcctTypeMeta] = field(default_factory=dict)
    ledger_class_debit_normal: dict[int, bool] = field(default_factory=dict)
    recurrence_unit_months: dict[Enum, int] = field(default_factory=dict)
    recurrence_day_of_month_units: set[Enum] = field(default_factory=set)
    initialized: bool = False


_cache = _RefState()


@dataclass(frozen=True)
class _RefSpec:
    """Declarative description of one reference table for ``init()`` to load.

    ``label`` (the warning text and ``unavailable`` key) and ``error_prefix``
    (the missing-row error prefix) are derived from the model so there is a
    single source of truth: ``label`` is the table name and ``error_prefix``
    the model class name (e.g. the ``RoleEnum`` table's model is ``UserRole``,
    so its errors read ``UserRole.<member>``).
    """

    enum: type[Enum]
    model: type
    # Filter the query to seeded built-ins (``user_id IS NULL``); set only for
    # account_types.  After commit C-28 / F-044 owners can register custom
    # types whose names collide with built-ins (a user's own "HYSA" alongside
    # the seeded "HYSA").  The cache promises a single stable ID per
    # ``AcctTypeEnum`` member, so it must see only the built-in rows; custom
    # types resolve via the ORM relationship in templates, never this cache.
    builtin_only: bool = False

    @property
    def label(self) -> str:
        """Return the reference table name (warning text / unavailable key)."""
        return self.model.__tablename__

    @property
    def error_prefix(self) -> str:
        """Return the model class name used to prefix missing-row errors."""
        return self.model.__name__

    def query(self, db_session) -> dict[str, int]:
        """Return a ``{row.name: row.id}`` lookup for this table's rows.

        Args:
            db_session: An active SQLAlchemy session.

        Returns:
            dict[str, int]: Row name mapped to its integer primary key.
        """
        model_query = db_session.query(self.model)
        if self.builtin_only:
            model_query = model_query.filter(self.model.user_id.is_(None))
        return {row.name: row.id for row in model_query.all()}


def _load_rows(db_session, label, query_callable):
    """Run a ref-table query, tolerating a schema that is behind the code.

    A table or column the schema does not have yet means a migration
    that creates the ref table, or adds a column to it, is pending --
    the bootstrap window during ``flask db upgrade`` and the development
    entrypoint, which build the app before they migrate.  Catch it, roll
    the session back (a failed query poisons the transaction so
    subsequent queries would otherwise fail with "current transaction is
    aborted"), log loud, and return ``None`` so the caller can record the
    table as unavailable.

    All other database errors propagate -- a misconfigured DSN, an
    absent privilege or a corrupted ref row is a real failure that must
    surface, not a bootstrap quirk to swallow.  The schema being behind
    is read off the SQLSTATE
    (:func:`~app.utils.db_errors.is_schema_behind_code`) because the
    ``ProgrammingError`` class this caught until plan step
    balance:X-dj also holds a malformed query and an absent privilege,
    and since the change to psycopg 3 a trigger's ``RAISE`` as well.

    Args:
        db_session: SQLAlchemy session for rollback on failure.
        label: Short table label for the warning message.
        query_callable: Zero-arg callable that runs the query and
            returns the name->id dict.

    Returns:
        dict[str, int] on success, ``None`` if the table, or a column the
        query reads, does not exist yet.
    """
    try:
        return query_callable()
    except sqlalchemy.exc.ProgrammingError as exc:
        db_session.rollback()
        if not is_schema_behind_code(exc):
            raise
        _logger.warning(
            "ref_cache: ref table %s not available "
            "(likely pre-migration bootstrap); enums for this table will "
            "not be cached until the next app start after migrations run.",
            label,
        )
        return None


def _build_ref_specs(ref_models) -> list[_RefSpec]:
    """Return the ordered reference-table specs for ``init()`` to load.

    Built here (not at module scope) because the ORM models are imported
    lazily inside ``init()`` to break the import cycle.  The order matches
    the historical load order, which fixes the order of the ``unavailable``
    list and of the missing-row error message.

    Args:
        ref_models: The lazily-imported ``app.models.ref`` module.

    Returns:
        list[_RefSpec]: One spec per cached reference table.
    """
    return [
        _RefSpec(StatusEnum, ref_models.Status),
        _RefSpec(TxnTypeEnum, ref_models.TransactionType),
        _RefSpec(AcctTypeEnum, ref_models.AccountType, builtin_only=True),
        _RefSpec(AcctCategoryEnum, ref_models.AccountTypeCategory),
        _RefSpec(PaycheckLineKindEnum, ref_models.PaycheckLineKind),
        _RefSpec(CalcMethodEnum, ref_models.CalcMethod),
        _RefSpec(RaiseTypeEnum, ref_models.RaiseType),
        _RefSpec(GoalModeEnum, ref_models.GoalMode),
        _RefSpec(IncomeUnitEnum, ref_models.IncomeUnit),
        _RefSpec(RoleEnum, ref_models.UserRole),
        _RefSpec(LoanAnchorSourceEnum, ref_models.LoanAnchorSource),
        _RefSpec(
            AccountOpeningSourceEnum, ref_models.AccountOpeningSource
        ),
        _RefSpec(
            EmployerContributionTypeEnum, ref_models.EmployerContributionType
        ),
        _RefSpec(CompoundingFrequencyEnum, ref_models.CompoundingFrequency),
        _RefSpec(
            InterestAccrualConventionEnum, ref_models.InterestAccrualConvention,
        ),
        _RefSpec(LedgerAccountClassEnum, ref_models.LedgerAccountClass),
        _RefSpec(PostingKindEnum, ref_models.PostingKind),
        _RefSpec(PostingSourceEnum, ref_models.PostingSource),
        _RefSpec(LedgerAccountKindEnum, ref_models.LedgerAccountKind),
        _RefSpec(RecurrenceUnitEnum, ref_models.RecurrenceUnit),
        _RefSpec(PeriodPlacementEnum, ref_models.PeriodPlacement),
        _RefSpec(BusinessDayShiftEnum, ref_models.BusinessDayShift),
        _RefSpec(AmountSourceEnum, ref_models.AmountSource),
        _RefSpec(StatementSourceEnum, ref_models.StatementSource),
        _RefSpec(SettledDayBasisEnum, ref_models.SettledDayBasis),
        _RefSpec(MovementFigureSourceEnum, ref_models.MovementFigureSource),
        _RefSpec(StatementBalanceEvidenceEnum, ref_models.StatementBalanceEvidence),
        _RefSpec(WithholdingKindEnum, ref_models.WithholdingKind),
        _RefSpec(FilingStatusEnum, ref_models.FilingStatus),
    ]


def _load_row_meta(
    db_session: Session, ref_models: ModuleType, unavailable: list[str],
) -> None:
    """Load the per-row caches the spec loop's ``name -> id`` map cannot carry.

    Each is a dedicated query, so a reader holding a stored ``*_id`` or an
    enum member branches on a cached column without a name compare.  Split
    out of :func:`init` at plan step plan#2302, when the third of them took
    that function past pylint's branch ceiling; the three bodies are as they
    stood there.

    Args:
        db_session: An active SQLAlchemy session.
        ref_models: The lazily-imported ``app.models.ref`` module.
        unavailable: Labels of the ref tables the spec loop found missing (the
            pre-migration bootstrap window); each such table's cache is
            skipped, having already been warned about.
    """
    # Build the account type metadata cache for icon/term-limit lookups.
    # Same built-in-only filter as the account_types map -- the cache is
    # loaded once at startup and only knows about seeded built-ins.  Skipped
    # when that table is unavailable (already warned during loading).
    # Owner-scoped custom types still resolve their icon/max_term via the ORM
    # relationship in templates (``account.account_type.icon_class``).
    if "account_types" not in unavailable:
        for row in (
            db_session.query(ref_models.AccountType)
            .filter(ref_models.AccountType.user_id.is_(None))
            .all()
        ):
            _cache.acct_type_meta[row.id] = {
                "icon_class": row.icon_class,
                "max_term_months": row.max_term_months,
            }

    # Build the ledger-account-class natural-balance map.  Mirrors the
    # account-type metadata block above: a dedicated query keyed by class
    # PK (the spec loop only captures name->id), so a reader holding a
    # ``budget.ledger_accounts.class_id`` can branch on the debit-normal
    # side without a name compare.  Skipped when the table is unavailable
    # (the pre-migration bootstrap window already warned during loading).
    if "ledger_account_classes" not in unavailable:
        for row in db_session.query(ref_models.LedgerAccountClass).all():
            _cache.ledger_class_debit_normal[row.id] = row.is_debit_normal

    # Build the recurrence-unit month-span map and day-of-month set.  The row
    # is the ONE home of which units are measured in months (ruling
    # plan#2748): the occurrence engine walks ``months_per_unit`` and asks
    # ``has_day_of_month``, which the database GENERATES from it, so the
    # stride and the flag a rule's CHECKs read cannot disagree.  Keyed by
    # MEMBER, the shape of the code table they replace, so the engine keeps
    # that table's answers for a value no row names: no span, no day (see the
    # two accessors).  A row no member names is skipped, as the spec loop
    # skips it.  Skipped whole when the table is unavailable (already warned
    # during loading).
    if "recurrence_units" not in unavailable:
        members = {
            db_id: member
            for member, db_id in _cache.enum_ids[RecurrenceUnitEnum].items()
        }
        for row in db_session.query(ref_models.RecurrenceUnit).all():
            member = members.get(row.id)
            if member is None:
                continue
            if row.months_per_unit is not None:
                _cache.recurrence_unit_months[member] = row.months_per_unit
            if row.has_day_of_month:
                _cache.recurrence_day_of_month_units.add(member)


def init(db_session):
    """Load all reference table IDs into the in-memory cache.

    Must be called once during ``create_app()`` after reference data
    has been seeded and committed.  Safe to call multiple times (e.g.
    in tests that create fresh app instances) -- clears and reloads.

    Resilient to missing ref tables during the bootstrap window when
    ``flask db upgrade`` is mid-flight: a ref table that does not
    exist yet is logged as a warning and its enum members are left
    out of the cache, but the cache is still marked initialized so
    accessors for unrelated tables work.  A ref table that EXISTS but
    is missing a seeded enum row is still a fatal ``RuntimeError`` --
    that is a genuine data error, not a bootstrap quirk.

    Args:
        db_session: An active SQLAlchemy session (typically ``db.session``).

    Returns:
        list[str]: Labels of ref tables that were unavailable at init
        time (empty list in a healthy production app).  Callers can
        use this to decide whether to skip downstream work that
        depends on the complete cache (e.g. Jinja globals).

    Raises:
        RuntimeError: If any ref table EXISTS but is missing rows for
            one or more of its enum members.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred import to avoid circular
    # dependencies.  The models module imports from extensions, which must be
    # initialized before the cache loads.
    import app.models.ref as ref_models  # pylint: disable=import-outside-toplevel

    specs = _build_ref_specs(ref_models)

    # Reset prior state (supports re-initialization in tests).  Mutate the
    # _cache dicts in place; do NOT reset ``initialized`` here -- a failed
    # re-init leaves the previous flag value, matching the original behavior.
    _cache.enum_ids.clear()
    _cache.enum_members.clear()
    _cache.acct_type_meta.clear()
    _cache.ledger_class_debit_normal.clear()
    _cache.recurrence_unit_months.clear()
    _cache.recurrence_day_of_month_units.clear()
    for spec in specs:
        _cache.enum_ids[spec.enum] = {}
        _cache.enum_members[spec.enum] = {}

    # Load each ref table and map its enum members to database IDs.  Each
    # query is wrapped in ``_load_rows`` so a missing ref table (the
    # pre-migration bootstrap window) is recorded as unavailable rather than
    # poisoning the whole cache; that table's enum sweep is then skipped.  A
    # missing row in a table that EXISTS is fatal -- a genuine seed/data error.
    unavailable = []
    missing = []
    for spec in specs:
        rows = _load_rows(db_session, spec.label, functools.partial(spec.query, db_session))
        if rows is None:
            unavailable.append(spec.label)
            continue
        target = _cache.enum_ids[spec.enum]
        for member in spec.enum:
            db_id = rows.get(member.value)
            if db_id is None:
                missing.append(
                    f"{spec.error_prefix}.{member.name} (expected name={member.value!r})"
                )
            else:
                target[member] = db_id

    if missing:
        raise RuntimeError(
            "ref_cache.init() failed -- the following enum members have no "
            "matching database row:\n  " + "\n  ".join(missing)
        )

    _load_row_meta(db_session, ref_models, unavailable)

    # Invert EVERY forward map so a reader holding a stored ``*_id`` resolves
    # its member in ONE lookup.  Built here rather than derived per call
    # because the classifier every net-worth surface reaches
    # (``app.services.account_category.account_category``) would otherwise
    # scan the enum, asking this cache once per member -- and built for every
    # table rather than for that one because the second reader to want it
    # would otherwise add a third hand-maintained field beside two others
    # (plan step ``bank_import:X-f6e-1``).  A table unavailable in the
    # bootstrap window inverts to an empty map, which every accessor answers
    # as "no modelled member" -- the same answer it gives for a row this
    # application does not model.
    for enum_type, ids in _cache.enum_ids.items():
        _cache.enum_members[enum_type] = {
            db_id: member for member, db_id in ids.items()
        }

    _cache.initialized = True
    return unavailable


def require_init():
    """Raise if the cache has not been initialized via ``init()``.

    Public to the PACKAGE and private to the world: this module is itself
    private, so ``shekel-private-module-import`` is what keeps the name from
    escaping, and an underscore on top of that would only force
    :mod:`._accessors` into a ``protected-access`` disable thirty-one times
    over.  It was ``_require_init`` while everything lived in one file.
    """
    if not cache().initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")


def cache():
    """Return the one :class:`_RefState` every reader shares.

    **A function rather than the object itself, and that is the whole reason
    the split is safe.**  In the flat module every accessor read one module
    global BY NAME, so replacing the cache object was seen by all of them at
    once.  ``from ._state import _cache`` would instead bind each accessor to
    whatever object existed at import time -- and a rebind would then leave
    :func:`require_init` reading the NEW cache while every reader read the
    stale one, so the guard passes against a cache its readers cannot see and
    the caller gets a ``KeyError`` where the contract promises a
    ``RuntimeError``.  Reading it through this function restores the
    read-by-name the flat module had for free.  Found by an adversarial review
    of this split, 2026-08-23; nothing rebinds it today, and the point is that
    nothing CAN make it wrong.

    Returns:
        The module's :class:`_RefState`.
    """
    return _cache
