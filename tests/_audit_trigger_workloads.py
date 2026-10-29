"""The five workloads the audit trigger is graded and timed on (plan step balance:X-cy).

**ONE definition with two readers, and the line between them is ruling
balance:R-BAL144, 'Count work, report time'.**

* ``tests/test_integration/test_audit_trigger_work.py`` is the REQUIRED gate.
  It runs each workload's act with the audit trigger on and asserts what the
  trigger DID -- exactly one ``system.audit_log`` row per
  ``budget.transactions`` row the act changed -- and that the trigger's cost
  surface is the one the timings below were measured against
  (:data:`PINNED_COST_FINGERPRINT`).  It sits in the ordinary suite, so every
  local run grades it and so does CI, on whichever shard ``tests/_shard.py``
  assigns each case (**R-BAL151**).
* ``tests/test_performance/test_trigger_overhead.py`` is the timing REPORT.  It
  times the same acts with the trigger on and off and prints the ratio, and it
  asserts nothing about time (**R-BAL152**).

A wall-clock ceiling was the gate until this step, and on GitHub's shared
runners it failed the required check four times in about fourteen hours with
no change to the trigger (finding **recurrence:REC-533**): the insert workload
read 32.6%, 37.3% and 32.6% against a 25% ceiling set from a dev box that
measured 6-12%.  A count is deterministic, and a trigger that does more work
per row changes its cost surface, which the pin catches without a clock.

**Every workload changes rows on EVERY run, and two of them did not until this
step** (**R-BAL150**; measured 2026-09-25 on the test image, PostgreSQL 18.6).
``regenerate`` has maintained its rows in place since plan step R10-a, so a
pass over an unchanged template wrote nothing at all; ``update`` re-wrote the
note its first run had already written, which the trigger's no-op guard
answers with no audit row.  Both were timed and printed as the trigger's
overhead while the trigger wrote nothing, and a count over either would have
passed with a trigger that writes no audit row at all.  :class:`Regenerate`
now renames its template before each pass and :class:`Update` alternates
between two notes, and the gate runs every workload twice to hold that.
"""
import abc
import contextlib
import hashlib
from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.ref import AccountType, TransactionType
from app.models.scenario import Scenario
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services import account_service, pay_period_write, recurrence_engine
from app.services.auth_service import hash_password
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.one_off import place_row_of
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today
from tests._test_helpers import make_every_period_rule, rhythm_of, state_template_price

# A 2-year horizon of biweekly paychecks.
PERIOD_COUNT = 52
CADENCE_DAYS = 14

# Rows the UPDATE workload writes per pay period.  One row per period is too
# narrow to measure; see :class:`Update`.
ROWS_PER_PERIOD = 5

# The paychecks a repeating rule still fills: :func:`build_owner` records half
# the calendar behind today, so today opens paycheck ``PERIOD_COUNT // 2``
# (0-based), and the engine drops that one and every one before it, because
# the account's books open today (see :class:`Generate`).
PAYCHECKS_AFTER_THE_BOOKS_OPEN = PERIOD_COUNT - PERIOD_COUNT // 2 - 1


# ---------------------------------------------------------------------------
# The owner every workload writes as
# ---------------------------------------------------------------------------


def build_owner():
    """Create and commit the owner every workload writes as.

    A two-year calendar of biweekly paydays, a checking account, a baseline
    scenario and one category.  Both readers build it through here, so the
    gate counts on the same owner the report times.

    Returns:
        dict: ``user``, ``settings``, ``account``, ``scenario``,
        ``category`` and ``periods`` (the recorded pay periods, in order).
    """
    user = User(
        email="perf@shekel.local",
        password_hash=hash_password("perfpass"),
        display_name="Perf User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The paydays are recorded BEFORE the account, and the order is a
    # precondition of the door rather than a preference:
    # ``account_service.create_account`` refuses an owner with no pay
    # periods (``_require_pay_period_schedule``), because the opening
    # balance it posts derives its pay period from the day it asserts and
    # an empty calendar has no such period.
    #
    # The span is anchored to the APP's civil day, not to a literal.  The
    # property the workloads need is that today falls INSIDE the recorded
    # calendar -- ``create_account`` defaults its observation day to today
    # and must find a period holding it -- and a hard-coded
    # ``date(2026, 1, 2)`` holds that property only until the horizon runs
    # out, at which point the builder would start refusing again for a
    # reason that has nothing to do with the code under test.  Half the
    # periods sit behind today and half ahead, on whatever day this runs.
    periods = pay_period_write.record_paydays(
        user_id=user.id,
        first_payday=display_today() - timedelta(days=CADENCE_DAYS * (PERIOD_COUNT // 2)),
        num_periods=PERIOD_COUNT,
        rhythm=rhythm_of(CADENCE_DAYS),
    )
    db.session.flush()

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Perf Checking",
            anchor_balance=Decimal("5000.00"),
        ),
    )
    db.session.add(account)

    scenario = Scenario(user_id=user.id, name="Baseline", is_baseline=True)
    db.session.add(scenario)
    db.session.flush()

    category = Category(user_id=user.id, group_name="Home", item_name="Perf Expense")
    db.session.add(category)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "category": category,
        "periods": periods,
    }


def _repeating_template(owner):
    """Return a flushed expense template that repeats every paycheck."""
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=owner["user"].id,
        account_id=owner["account"].id,
        category_id=owner["category"].id,
        transaction_type_id=expense_type.id,
        name="Benchmark Expense",
        default_amount=Decimal("150.00"),
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db.session, template)
    db.session.refresh(template)
    return template


def _rule_less_definition(owner, *, name):
    """Return a flushed, priced definition with NO cadence -- a one-off's.

    The definition whose rows ``one_off.place_row_of`` places, one per
    paycheck.
    """
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=owner["user"].id,
        account_id=owner["account"].id,
        category_id=owner["category"].id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=Decimal("50.00"),
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    return template


def _schedule_of(template, owner):
    """Return the owner's generation schedule over every recorded period."""
    return GenerationSchedule.for_period_ids(
        BalanceContext.build(template.user_id), {p.id for p in owner["periods"]},
    )


def _paychecks_of(owner):
    """Return every recorded period as the owner's calendar derives it."""
    calendar = calendar_for(owner["user"].id)
    return [calendar.period_by_id(p.id) for p in owner["periods"]]


# ---------------------------------------------------------------------------
# The workloads
# ---------------------------------------------------------------------------


class Workload(abc.ABC):
    """One act on ``budget.transactions``, made repeatable by an untimed reset.

    Both readers run a workload the same way -- :meth:`reset`, then
    :meth:`act`, then commit -- and own everything around it: the gate its
    census of the table before and after the act, the report its clock.  The
    constructor does the one-time setup and commits it.

    Attributes:
        key: The workload's short name, and its test id in both readers.
        rows: How many ``budget.transactions`` rows the act changes, on EVERY
            run; the gate asserts it exactly, and the label states it.
        label: What the report prints.
        operation: The ONE ``system.audit_log.operation`` every row the act
            changes is audited with.
    """

    key: str
    rows: int
    label: str
    operation: str

    def __init__(self, owner):
        """Hold the owner the workload writes as.

        Args:
            owner: The dict :func:`build_owner` returns.
        """
        self.owner = owner
        self.scenario_id = owner["scenario"].id

    @abc.abstractmethod
    def reset(self):
        """Leave the database one act away from changing rows, committed."""

    @abc.abstractmethod
    def act(self):
        """Perform the write under measurement and flush it; never commit."""


class Generate(Workload):
    """The recurrence engine's bulk create: a row per paycheck after the books open.

    The rule repeats every paycheck, but the engine drops an occurrence that
    would land on or before the day its account's books open
    (``app.services.recurrence._placement``, rulings R-PC85 and R-PC86), and
    :func:`build_owner` opens the account today, the first day of its 27th
    paycheck.  So on the owner's 52-paycheck calendar it wrote 25 rows when
    measured (2026-09-25), one for each paycheck after that one, not the 52
    its label claimed until this step.
    """

    key = "generate"
    rows = PAYCHECKS_AFTER_THE_BOOKS_OPEN
    label = f"generate_for_template ({rows} rows, every paycheck after the books open)"
    operation = "INSERT"

    def __init__(self, owner):
        super().__init__(owner)
        self.template = _repeating_template(owner)
        db.session.commit()

    def reset(self):
        db.session.execute(
            db.text("DELETE FROM budget.transactions WHERE template_id = :tid"),
            {"tid": self.template.id},
        )
        db.session.commit()

    def act(self):
        recurrence_engine.generate_for_template(
            self.template, _schedule_of(self.template, self.owner), self.scenario_id,
        )
        db.session.flush()


class Regenerate(Workload):
    """A template edit's maintain pass: every row the rule names, UPDATED in place.

    **The edit is a RENAME, alternating between two names, because a pass over
    an unchanged template writes nothing** (**R-BAL150**).  Since plan step
    R10-a the pass maintains a row rather than deleting and recreating it: it
    assigns each derived column onto the row it keeps
    (``recurrence_engine._maintain``), and the ORM issues an UPDATE only for a
    row where one of them moved.  ``name`` is one
    (``recurrence_engine._amounts.DerivedRowFields``), so after a rename every
    one of the template's rows is updated.  The rename itself is the untimed
    reset, and the pass is the act.  The two names are the same length: the
    report's warmup and pairing hand each arm the SAME one of them every time,
    so names of different lengths would have one arm writing more bytes than
    the other on every pair.
    """

    key = "regenerate"
    rows = PAYCHECKS_AFTER_THE_BOOKS_OPEN
    label = f"regenerate_for_template after a rename ({rows} rows)"
    operation = "UPDATE"

    _NAMES = ("Benchmark Expense A", "Benchmark Expense B")

    def __init__(self, owner):
        super().__init__(owner)
        self.template = _repeating_template(owner)
        recurrence_engine.generate_for_template(
            self.template, _schedule_of(self.template, owner), self.scenario_id,
        )
        db.session.commit()

    def reset(self):
        first, second = self._NAMES
        self.template.name = second if self.template.name == first else first
        db.session.commit()

    def act(self):
        recurrence_engine.regenerate_for_template(
            self.template, _schedule_of(self.template, self.owner), self.scenario_id,
        )
        db.session.flush()


class Insert(Workload):
    """Bulk INSERT: one row per paycheck of ONE rule-less definition.

    Through the app's row placer (``one_off.place_row_of``, ruling R-BAL24's
    shape: a bank-born envelope's row in each later paycheck) -- one INSERT
    and one flush per row on ``budget.transactions``, the table whose trigger
    the report toggles.  A bare ``Transaction(...)`` was the shape the
    cutover's pricing-link CHECK refuses; the whole one-off producer per row
    would add a definition and a version INSERT on two tables whose triggers
    fire in BOTH arms, tripling the denominator against one table's trigger
    (found by plan step balance:X-bi-7c-5's review); and the engine's bulk
    insert is :class:`Generate`.  The definition and the owner's calendar are
    resolved outside the act.
    """

    key = "insert"
    rows = PERIOD_COUNT
    label = f"Bulk INSERT ({rows} rows, one per paycheck)"
    operation = "INSERT"

    def __init__(self, owner):
        super().__init__(owner)
        self.definition = _rule_less_definition(owner, name="Bulk Txn")
        db.session.commit()
        self.paychecks = _paychecks_of(owner)

    def reset(self):
        db.session.execute(
            db.text("DELETE FROM budget.transactions WHERE template_id = :tid"),
            {"tid": self.definition.id},
        )
        db.session.commit()

    def act(self):
        for paycheck in self.paychecks:
            place_row_of(self.definition, paycheck, scenario_id=self.scenario_id)
        db.session.flush()


class Update(Workload):
    """Bulk UPDATE of the ``notes`` column, :data:`ROWS_PER_PERIOD` rows per paycheck.

    UPDATEs are the most common write in a budgeting app (editing amounts,
    marking done, changing statuses).  The column written is ``notes`` -- the
    ROW's own: a placed row carries no figure (its definition does), and
    ``ck_transactions_amount_ownership`` refuses a stored one on a derived row
    (plan step balance:X-bi-7c).

    **The note ALTERNATES between two values** (**R-BAL150**).  Re-writing the
    value a row already holds is a no-op the trigger answers with no audit
    row, and until this step every run after the first did exactly that.  The
    two are the same length, for the reason :class:`Regenerate`'s names are.

    :data:`ROWS_PER_PERIOD` rows per paycheck rather than one: a 52-row UPDATE
    ran in ~1.7 ms (measured 2026-08-28, on the amount column), small enough
    that the fixed per-statement costs (parse, plan, one round trip) are a
    large share of it.  A wider batch amortises
    them into the per-row work the trigger actually affects.  The rows are
    :data:`ROWS_PER_PERIOD` rule-less definitions, each placed in every
    paycheck (a definition holds one row per paycheck and day, so the copies
    are definitions, not rows).
    """

    key = "update"
    rows = PERIOD_COUNT * ROWS_PER_PERIOD
    label = f"Bulk UPDATE ({rows} rows, the notes column)"
    operation = "UPDATE"

    _NOTES = ("benchmark touch A", "benchmark touch B")

    def __init__(self, owner):
        super().__init__(owner)
        definitions = [
            _rule_less_definition(owner, name=f"Update Txn {copy}")
            for copy in range(ROWS_PER_PERIOD)
        ]
        for paycheck in _paychecks_of(owner):
            for definition in definitions:
                place_row_of(definition, paycheck, scenario_id=self.scenario_id)
        db.session.commit()
        self.note = None

    def reset(self):
        first, second = self._NOTES
        self.note = second if self.note == first else first
        db.session.commit()

    def act(self):
        db.session.execute(
            db.text(
                "UPDATE budget.transactions SET notes = :note "
                "WHERE name LIKE 'Update Txn%'"
            ),
            {"note": self.note},
        )
        db.session.flush()


class Delete(Workload):
    """Bulk DELETE of one rule-less definition's row in every paycheck.

    Each reset places a fresh batch under a definition of its own, through the
    app's row placer (:class:`Insert`'s vehicle); the raw DELETE takes the rows
    and leaves the definition, which is what the act is about.
    """

    key = "delete"
    rows = PERIOD_COUNT
    label = f"Bulk DELETE ({rows} rows)"
    operation = "DELETE"

    def __init__(self, owner):
        super().__init__(owner)
        self.paychecks = _paychecks_of(owner)
        self.batch = 0

    def reset(self):
        self.batch += 1
        definition = _rule_less_definition(self.owner, name=self._batch_name())
        for paycheck in self.paychecks:
            place_row_of(definition, paycheck, scenario_id=self.scenario_id)
        db.session.commit()

    def act(self):
        db.session.execute(
            db.text("DELETE FROM budget.transactions WHERE name LIKE :pattern"),
            {"pattern": self._batch_name()},
        )
        db.session.flush()

    def _batch_name(self):
        """Return the current batch's row name; a pattern that matches only it."""
        return f"Delete batch {self.batch}"


#: Every workload, in the order both readers run them.
WORKLOADS = (Generate, Regenerate, Insert, Update, Delete)


# ---------------------------------------------------------------------------
# The cost surface, and the one the timings were measured against
# ---------------------------------------------------------------------------

#: The triggers that audit ``budget.transactions``: every trigger on it that
#: calls the audit function, whatever its name or events.  The pin reads them
#: and the report switches them off (:func:`audit_triggers_off`), so both mean
#: the same triggers.
_AUDIT_ATTACHMENTS = (
    "FROM pg_trigger WHERE tgrelid = 'budget.transactions'::regclass "
    "AND tgfoid = 'system.audit_trigger_func()'::regprocedure"
)

#: The catalogue reads whose answers decide what ONE audited write on
#: ``budget.transactions`` costs (**R-BAL149**): the trigger function's whole
#: definition (its body, and the attributes a body cannot show -- ``SECURITY
#: DEFINER``, a ``SET`` clause, volatility); :data:`_AUDIT_ATTACHMENTS`, and
#: whether each is enabled; that table's columns (name, type, storage,
#: compression) and its ``toast_tuple_target``, because the function copies the
#: whole row into the audit log (``to_jsonb`` of the new row on an insert, of
#: the old on a delete, of both on an update, which it also walks key by key),
#: so a wider row is a heavier write (the developer's answer after this
#: step's second review, **R-BAL154**: 12 added text columns took an insert's
#: audit data from 668 to 1,284 bytes with the pin unmoved), and
#: ``toast_tuple_target`` decides when the row's values are stored compressed
#: or out of line, which the function must then expand; and
#: ``system.audit_log`` as the function's INSERT meets it -- the relation's
#: kind, persistence, ``fillfactor`` and ``toast_tuple_target``, row security
#: and access method, every column's type, storage, compression, collation and
#: default, its indexes and constraints, its id sequence and that sequence's
#: persistence, and its triggers, rules and row-security policies, with
#: whether each trigger and rule is enabled and whom each policy applies to.
#: An index added to the audit log is one more index insert on every audited
#: write in the application, with the function's text unchanged.
#:
#: NOT read, and why.  Outside both tables: the server's settings,
#: tablespaces and roles, and the database's publications -- so neither the
#: tablespace either table or its indexes is stored in, nor the role the
#: application writes as and whether row security binds it, which
#: ``system.audit_log``'s owner also decides (an owner bypasses row security
#: unless it is forced).  The report runs as the test cluster's superuser,
#: which row security never binds, so a changed ``system.audit_log`` policy
#: moves the pin but a re-measure here cannot see what it costs production.
#: What costs the write in both of the report's arms alike, or costs no write
#: at all: ``budget.transactions``' indexes, constraints, rules (one can add
#: a statement or suppress the write, not widen the row), policies, AFTER
#: triggers other than the audit ones, replica identity and every storage
#: option but ``toast_tuple_target``; ``system.audit_log``'s replica identity
#: (the function only inserts into it), every storage option but the two
#: above (autovacuum's, for one; ``user_catalog_table`` costs an insert only
#: under ``wal_level = logical``, which neither this repository nor the
#: production compose files set) and its TOAST table's options.  What fills
#: ``budget.transactions``' row: its column defaults and generation
#: expressions and its BEFORE triggers other than the audit ones.  Any of
#: them that writes a wider value makes every audited row it fills
#: heavier, but R-BAL154 pins the columns' names, types and storage, and most
#: such changes (a new default status, say) widen nothing.  And should
#: ``system.audit_log`` become partitioned, a change that moves the pin, what
#: is set on its partitions afterwards.  Each read returns text lines in a
#: fixed order.
_COST_SURFACE_QUERIES = (
    (
        "the trigger function",
        "SELECT pg_get_functiondef('system.audit_trigger_func()'::regprocedure)",
    ),
    (
        "every trigger calling it on budget.transactions, and whether each is enabled",
        "SELECT concat_ws(' ', pg_get_triggerdef(oid), 'enabled', tgenabled::text) "
        f"{_AUDIT_ATTACHMENTS} ORDER BY 1",
    ),
    (
        "budget.transactions columns (the row the function copies whole)",
        "SELECT concat_ws(' ', attname, format_type(atttypid, atttypmod), "
        "attstorage, attcompression) FROM pg_attribute "
        "WHERE attrelid = 'budget.transactions'::regclass AND attnum > 0 "
        "AND NOT attisdropped ORDER BY attnum",
    ),
    (
        "budget.transactions toast_tuple_target",
        "SELECT concat_ws(' ', 'toast_tuple_target', "
        "(SELECT option_value FROM pg_options_to_table(c.reloptions) "
        "WHERE option_name = 'toast_tuple_target')) "
        "FROM pg_class AS c WHERE c.oid = 'budget.transactions'::regclass",
    ),
    (
        "system.audit_log: kind, persistence, fillfactor and toast_tuple_target, "
        "row security, access method",
        "SELECT concat_ws(' ', c.relkind, c.relpersistence, "
        "(SELECT string_agg(option_name || '=' || option_value, ',' "
        "ORDER BY option_name) FROM pg_options_to_table(c.reloptions) "
        "WHERE option_name IN ('fillfactor', 'toast_tuple_target')), "
        "c.relrowsecurity, c.relforcerowsecurity, am.amname) FROM pg_class AS c "
        "LEFT JOIN pg_am AS am ON am.oid = c.relam "
        "WHERE c.oid = 'system.audit_log'::regclass",
    ),
    (
        "system.audit_log columns",
        "SELECT concat_ws(' ', a.attname, format_type(a.atttypid, a.atttypmod), "
        "a.attnotnull, a.attstorage, a.attcompression, co.collname, "
        "pg_get_expr(d.adbin, d.adrelid)) FROM pg_attribute AS a "
        "LEFT JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "LEFT JOIN pg_collation AS co ON co.oid = a.attcollation "
        "WHERE a.attrelid = 'system.audit_log'::regclass AND a.attnum > 0 "
        "AND NOT a.attisdropped ORDER BY a.attnum",
    ),
    (
        "system.audit_log indexes",
        "SELECT pg_get_indexdef(indexrelid) FROM pg_index "
        "WHERE indrelid = 'system.audit_log'::regclass ORDER BY 1",
    ),
    (
        "system.audit_log constraints",
        "SELECT concat_ws(' ', conname, pg_get_constraintdef(oid)) FROM pg_constraint "
        "WHERE conrelid = 'system.audit_log'::regclass ORDER BY 1",
    ),
    (
        "system.audit_log triggers, and whether each is enabled",
        "SELECT concat_ws(' ', pg_get_triggerdef(oid), 'enabled', tgenabled::text) "
        "FROM pg_trigger "
        "WHERE tgrelid = 'system.audit_log'::regclass AND NOT tgisinternal "
        "ORDER BY 1",
    ),
    (
        "system.audit_log rules, and whether each is enabled",
        "SELECT concat_ws(' ', pg_get_ruledef(oid), 'enabled', ev_enabled::text) "
        "FROM pg_rewrite "
        "WHERE ev_class = 'system.audit_log'::regclass ORDER BY 1",
    ),
    (
        "system.audit_log row-security policies, and whom each applies to",
        "SELECT concat_ws(' ', polname, polcmd, polpermissive, "
        "polroles::regrole[]::text, "
        "pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid)) "
        "FROM pg_policy WHERE polrelid = 'system.audit_log'::regclass ORDER BY 1",
    ),
    (
        "system.audit_log's id sequence, and its persistence",
        "SELECT concat_ws(' ', s.seqtypid::regtype, s.seqstart, s.seqincrement, "
        "s.seqmax, s.seqmin, s.seqcache, s.seqcycle, c.relpersistence) "
        "FROM pg_sequence AS s JOIN pg_class AS c ON c.oid = s.seqrelid "
        "WHERE s.seqrelid = pg_get_serial_sequence('system.audit_log', 'id')::regclass",
    ),
)


def cost_surface():
    """Return the audit trigger's cost surface, read from the live database.

    Read from the test database's catalogue, so a MIGRATION's change to what
    :data:`_COST_SURFACE_QUERIES` reads is in it (that constant says what it
    reads and what it does not), with one exception.  **What
    ``app.audit_infrastructure`` re-applies is the MODULE's, not a
    migration's**: the test template's build runs it after ``alembic upgrade``
    (``scripts/build_test_template.py``), so the trigger FUNCTION, its
    ATTACHMENTS and the module's own three audit-log indexes read here as the
    template installs them, whatever a migration would leave in production (a
    migration that DROPS one of those indexes is re-created over).  That
    divergence is finding **balance:BAL-556**, measured by this step's
    reviews: a migration that slowed the function and re-attached the trigger
    BEFORE left this surface unchanged.

    The reads run under ``search_path = pg_catalog``, set for this transaction
    only, because the catalogue's text names an object without its schema when
    the schema is on the path: a role or database default could otherwise move
    the fingerprint with nothing about the trigger changed.

    Returns:
        str: One section per :data:`_COST_SURFACE_QUERIES` entry, each a
        ``-- <title>`` line followed by that read's lines.
    """
    path = db.session.execute(db.text("SELECT current_setting('search_path')")).scalar_one()
    db.session.execute(db.text("SELECT set_config('search_path', 'pg_catalog', true)"))
    sections = []
    for title, query in _COST_SURFACE_QUERIES:
        lines = db.session.execute(db.text(query)).scalars().all()
        sections.append("\n".join([f"-- {title}", *lines]))
    db.session.execute(
        db.text("SELECT set_config('search_path', :path, true)"), {"path": path},
    )
    return "\n".join(sections) + "\n"


def fingerprint_of(surface):
    """Return the SHA-256 hex digest of a :func:`cost_surface` text."""
    return hashlib.sha256(surface.encode("utf-8")).hexdigest()


#: The ``ALTER TABLE`` action that puts a trigger back in each
#: ``pg_trigger.tgenabled`` state PostgreSQL has.
_TRIGGER_STATE_ACTION = {
    "O": "ENABLE",
    "D": "DISABLE",
    "R": "ENABLE REPLICA",
    "A": "ENABLE ALWAYS",
}

#: The ``tgenabled`` states whose trigger fires in a session whose
#: ``session_replication_role`` is ``origin``, as this suite's sessions are.
_FIRES_IN_AN_ORIGIN_SESSION = ("O", "A")


@contextlib.contextmanager
def audit_triggers_off():
    """Switch off every trigger :data:`_AUDIT_ATTACHMENTS` names, then restore each.

    The report's without-trigger arm runs inside this, so it switches off
    exactly the triggers the pin reads: a second trigger calling the function
    is off in that arm too, rather than inflating the base the overhead is
    measured against.  On the way out each trigger goes back to the state the
    schema gave it, so the with-trigger arm times the triggers as the pin
    records them, a disabled one included.  Nothing is committed here.  On an
    exception nothing is restored either: the test fails, and its database is
    a clone of its own that is thrown away.

    Raises:
        LookupError: When no trigger on ``budget.transactions`` that calls
            the function fires in an origin session -- none exists, or each
            is disabled or replica-only -- because the two arms would then
            time the same unaudited write.  An attachment that fires, but not
            on a workload's operation (its events, an ``UPDATE OF`` list, a
            ``WHEN`` clause, statement level), is not refused here: the
            gate's count fails it.
    """
    states = db.session.execute(
        db.text(f"SELECT quote_ident(tgname), tgenabled::text {_AUDIT_ATTACHMENTS} ORDER BY 1")
    ).all()
    if not any(state in _FIRES_IN_AN_ORIGIN_SESSION for _, state in states):
        raise LookupError(
            "no trigger on budget.transactions that calls "
            f"system.audit_trigger_func() fires in an origin session: {states}"
        )
    for name, _ in states:
        db.session.execute(
            db.text(f"ALTER TABLE budget.transactions DISABLE TRIGGER {name}")
        )
    yield
    for name, state in states:
        db.session.execute(
            db.text(
                f"ALTER TABLE budget.transactions {_TRIGGER_STATE_ACTION[state]} "
                f"TRIGGER {name}"
            )
        )


# The audit trigger's measured overhead, one dated block per measurement, on
# the dev box (PostgreSQL in docker) through the report's paired harness,
# serially.  This table and the pin below are ONE record: a change to the
# cost surface fails the gate until it is re-measured here and re-pinned
# (R-BAL144).  Overhead is (with - without) / without, the median of the
# per-pair ratios.
#
# 2026-08-28, FOURTEEN runs across a range of machine load (PostgreSQL 17):
#
#   generate    2.5 -   9.6 %      base 34-37 ms
#   regenerate -3.7 -   1.3 %      base 78-82 ms   (see below)
#   insert      3.1 -  10.3 %      base 51-53 ms
#   update    290.2 - 309.7 %      base  1.8-1.9 ms  (260 rows; see below)
#   delete     21.4 -  31.9 %      base  7.7-7.7 ms
#
# 2026-09-18, FIVE runs, ordinary load, after the insert, update and delete
# rows moved onto the one-off row placer (plan step balance:X-bi-7c):
#
#   insert      6.1 -  12.3 %      base 26-28 ms   (one INSERT + flush per row)
#   update    125.2 - 140.8 %      base  4.6 ms    (the notes column; 260 rows)
#   delete     21.3 -  25.4 %      base  5.5-5.7 ms
#
# **The regenerate and update figures above time a trigger that wrote NO
# audit row** (R-BAL150), and they do not compare with the block below.
# Regenerate's pass changed nothing, so it issued no UPDATE and the trigger
# never fired at all: its range is noise around zero.  Every update sample
# after the first re-wrote the value the first had written (the amount on
# 2026-08-28, the note on 2026-09-18), so the trigger fired on every row and
# wrote nothing: the cost of firing it and computing a row's changed fields,
# not of auditing a change.
#
# 2026-09-25, FIVE runs on a BUSY machine (load average 22-23, another
# session's pytest live during all five), PostgreSQL 18.6 (the test image), on
# the pinned surface below, every workload changing its rows on every run
# (the base is each run's median without-trigger time):
#
#   generate   -1.5 -   6.3 %      base 34.4-37.4 ms  (25 rows)
#   regenerate  3.1 -   9.0 %      base 67.6-73.1 ms  (25 rows)
#   insert      2.9 -  10.9 %      base 46.4-71.0 ms  (52 rows)
#   update    152.2 - 161.4 %      base  7.3-8.5 ms   (260 rows)
#   delete     28.0 -  44.0 %      base  6.0-6.7 ms   (52 rows)
#
# 2026-10-27, FIVE runs on a quiet machine (load average 2.2-3.2 over the
# minute before each run, falling from a peer's suite that had ended a minute
# earlier; no other pytest live), PostgreSQL 18.6 (the test image), after plan
# step balance:X-bi-6-5 dropped ``budget.transactions.transfer_id`` (migration
# ``c956c1d83108``), the one change to the surface pinned above.  The bases sit
# below the busy 2026-09-25 block's; generate, regenerate and insert top out
# 2.2-4.6 points above its tops, update sits wholly below its range, and
# delete's range starts below it:
#
#   generate    4.7 -  10.9 %      base 19.3-20.2 ms  (25 rows)
#   regenerate  8.5 -  11.6 %      base 35.9-37.6 ms  (25 rows)
#   insert      4.7 -  13.1 %      base 23.4-24.3 ms  (52 rows)
#   update    131.9 - 146.1 %      base  5.2-5.8 ms   (260 rows)
#   delete     25.2 -  34.3 %      base  5.1-5.4 ms   (52 rows)

#: :func:`fingerprint_of` the :func:`cost_surface` of the database the
#: 2026-10-27 block above was measured on, the test image (PostgreSQL 18.6).
#: The 2026-09-25 pin still held at commit ``bec6f2218``, the one below that
#: migration (its full suite passed this gate), and of what the surface reads
#: the migration changes only the audited table's column list.  A
#: PostgreSQL upgrade that re-renders the catalogue's text moves the
#: fingerprint too, and a re-measure is the right answer then as well.
PINNED_COST_FINGERPRINT = "6665a3656a1c1a166f9847e0a505528d0b91158a4545597a041bbb2264c3783b"
