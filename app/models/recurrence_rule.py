"""
Shekel Budget App -- Recurrence Rule Model (budget schema)

Defines the pattern by which transactions are auto-generated into
future pay periods (every_period, monthly, annual, etc.).

**This table states its recurrence in EIGHT columns and carries no second
statement of any of them.**  ``interval_n`` / ``unit_id`` / ``placement_id`` /
``shift_id`` / ``starts_on`` / ``nominal_day`` / ``weekday_reading`` /
``max_per_month`` are what a caller AUTHORS and what every reader takes;
beside them sit only the closing bound's exclusive arc and one carried flag
(below).  ``due_day_of_month`` -- a servicer's date for a bill the cadence
scheduled elsewhere -- was dropped at plan step
recurrence:R5-a (ruling **R-R96**, migration ``1c569c51b449``): a rule's own day
IS the day its rows are due, and a recurring due day that differs from the day
money moves is a term of the LOAN (``loan_params.payment_day``), not of the
rule.  None carried one on the production clone measured 2026-09-23, and
the migration refuses to run over one that does.  The seventh, the per-month
ceiling, arrived at plan step **salary:R15-a** (ruling **R-SAL29**) as the
cadence's third value: "every paycheck, at most 2 a month" is how a payroll
benefit taken on a month's first two paychecks is stated, and neither axis
could say it (ledger row **D59**).  The eighth, how the first occurrence is
READ, arrived at plan step **recurrence:R8-c** (rulings **plan#2668**,
**plan#2670**, amending **R-R25**): "the third Tuesday" and "the last Friday"
are dates read by their weekday, and the week number and weekday come from
``starts_on`` itself, so the reading is the one fact the date cannot carry.

**One column is CARRIED rather than authored, and a key holds it** (plan step
plan#2302, ruling plan#2748).  ``unit_has_day_of_month`` is the unit's own
``ref.recurrence_units.has_day_of_month`` on this row, because a PostgreSQL
CHECK reads only the row it checks and two of the pairings here turn on which
kind of unit the rule names: a per-month ceiling needs a unit that can repeat
within a month, and a nominal day needs a unit with a day to clamp.
``fk_recurrence_rules_unit_day_of_month`` holds the copy to the unit, so a row
whose flag disagrees with its unit is refused rather than reconciled.

**Seven columns were DROPPED at plan step R7c-c** (migration
``d9f5c1a48b73``), each a derived encoding the write door maintained:
``pattern_id`` (a closed set of eight names for cadences the two axes state
directly), ``day_of_month`` and ``month_of_year`` (the cycle's day and its
residue class, both carried by the first occurrence), ``start_date`` (the
opening bound, which the first occurrence IS), ``start_period_id`` (the
paycheck a rule started in), ``offset_periods`` (the cycle phase, derived from
the first occurrence on every read), and ``interval_n``'s ENCODED value -- the
column held ``1`` for every pattern whose interval was baked into its name, so
a Quarterly rule read as monthly at face value until that migration re-pointed
it.

Plan step R2d (developer ruling, 2026-08-07) refused to store the two-axis
values while they were a DERIVATION over the closed-set columns plus the
owner's schedule, because a stored derivation is a cache and a cache drifts the
moment one writer moves one side alone.  That is not what they are: the form
collects ``starts_on`` and the loan sync writes it from a contract, so there is
no input left for it to lag.  A stored derivation is a cache; a stored authored
value is a fact.  (A day-less LOAN payment is the one shape still measured
against the schedule -- see ``recurrence._authoring._author`` and plan ledger
row **D39**, which plan step R5 owns.)

Ruling **R-R16** is what collapsed the four anchor columns: the first
occurrence is the earliest thing a cadence produces, its day is the cycle's day
and its month is the cycle's month.  Plan ledger row **D28** measured what
keeping them apart would have cost, at 18 of 24 live multi-month rules firing
in the wrong months forever.

``end_date`` and ``max_occurrences`` are ONE authored value above this table
(``app.services.recurrence.EndBound``, plan step R7b-3): the occurrence walk
has read both since plan step R3, and the write door splits the single bound
into this pair on the way in and rejoins it on the way out.  That is what makes
the exclusive arc below structural rather than maintained -- no value in the
application can state two closing bounds, so nothing has to check.

**Every write goes through one door** (:mod:`app.services.recurrence`).  A
caller states what it AUTHORS -- a ``RecurrenceSpec``, never a column -- and
the seam writes the whole spec.  Nothing in ``app/`` or ``scripts/`` constructs
or mutates this model outside that seam.

**A rule is OWNED BY its template, and plan step R-F6 turned that arrow
around.**  The FK used to point from the template to the rule
(``transaction_templates.recurrence_rule_id ON DELETE SET NULL``, and its
transfer twin), which is a satellite referenced by its parent rather than
declaring one -- and three consequences followed from the direction alone:

1. **A hard-deleted template stranded its rule forever** (finding **F-6**).
   ``SET NULL`` fires on the wrong side, so no ``ondelete`` in that direction
   could dispose of the rule; the two hard-delete routes each had to remember,
   and neither did.  Three rows had accumulated on production.
2. **1:1 was unstateable**, so the application carried a runtime census --
   ``_recurrence_form_helpers._rule_is_exclusively_owned`` -- counting the
   templates referencing a rule before daring to delete it, and
   ``scripts/integrity_check`` carried check **OR-02** scanning for the
   orphans afterwards.  Both are DELETED: the arc plus a unique index per arm
   says what they asked.
3. **The rule carried its own ``user_id``**, which the template also carried,
   with nothing tying the two together.  It is a property now
   (:attr:`RecurrenceRule.user_id`), read through the owner, so the pair
   cannot disagree.

The shape is ``budget.template_amount_versions``' exactly (developer,
2026-08-11) -- the OTHER satellite of these same two parents: an exclusive arc
of two nullable typed FKs under one CHECK, ``ON DELETE CASCADE`` on each. The
arc is EXACTLY-one rather than at-most-one, for that table's own reason one
noun over: a cadence nothing repeats is not a cadence.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin


class RecurrenceRule(CreatedAtMixin, db.Model):
    """A recurrence pattern owned by exactly one recurring definition.

    **Written only through :mod:`app.services.recurrence`**, which takes a
    complete authored spec rather than a column at a time.  See the module
    docstring for why the two-axis values the redesign adds are computed
    rather than stored, and for why the owning FK points from this table
    rather than at it.
    """

    __tablename__ = "recurrence_rules"
    __table_args__ = (
        # ---- The OWNING arc (plan step R-F6) -----------------------------
        #
        # Exactly one owner.  ``<>`` on two NULL tests is XOR, so both-set and
        # neither-set are equally refused -- and NEITHER-set is what finding
        # **F-6** measured three of on production, a rule whose template was
        # hard-deleted out from under it.  With the FK pointing this way the
        # disposal is the DATABASE'S (``ON DELETE CASCADE`` on each arm below),
        # so it holds for every door that will ever delete a template rather
        # than for the two that exist today.
        #
        # EXACTLY-one rather than at-most-one, which is the same choice
        # ``ck_template_amount_versions_one_owner`` made and for the same kind
        # of reason: an amount nobody stated for anything is not an amount, and
        # a cadence nothing repeats on is not a cadence.  Nothing in the
        # application holds an owner-less rule at all, not even an unsaved one:
        # ``build_transient_rule`` takes an owner exactly as ``author_rule``
        # does, so what a transient rule lacks is a ROW, never an owner.
        # THREE arms since plan step salary:R15-b (ruling R-SAL32): a payroll
        # deduction's cadence is a rule too.  ``<>`` on two NULL tests was XOR;
        # exactly one of three is the sum of the three set-flags equalling 1.
        db.CheckConstraint(
            "(transaction_template_id IS NOT NULL)::int"
            " + (transfer_template_id IS NOT NULL)::int"
            " + (paycheck_line_id IS NOT NULL)::int = 1",
            name="ck_recurrence_rules_one_owner",
        ),
        # 1:1, per arm, and this is what the runtime census plan step R-F6
        # deleted was standing in for: ``_rule_is_exclusively_owned`` counted
        # the templates referencing a rule on every clear, because with the FK
        # on the other side two templates COULD name one rule and deleting it
        # would have stripped a second definition's cadence silently.  A
        # template names at most one rule because it has no column to name a
        # second with; a rule serves at most one template because of these.
        #
        # PARTIAL for the reason the precedent table's twins are: half this
        # table's rows carry NULL in either key, and an index over those NULLs
        # answers no query.  Each doubles as the index the reverse lookup
        # needs, which is every read of ``rule.user_id`` and every ORM load of
        # the owner.
        db.Index(
            "uq_recurrence_rules_transaction_template_id",
            "transaction_template_id",
            unique=True,
            postgresql_where=db.text("transaction_template_id IS NOT NULL"),
        ),
        db.Index(
            "uq_recurrence_rules_transfer_template_id",
            "transfer_template_id",
            unique=True,
            postgresql_where=db.text("transfer_template_id IS NOT NULL"),
        ),
        db.Index(
            "uq_recurrence_rules_paycheck_line_id",
            "paycheck_line_id",
            unique=True,
            postgresql_where=db.text("paycheck_line_id IS NOT NULL"),
        ),
        db.CheckConstraint("interval_n > 0", name="ck_recurrence_rules_positive_interval"),
        # The per-month ceiling's floor (plan step salary:R15-a).  NULL is the
        # one spelling of "no ceiling"; a zero would be a rule that never fires
        # spelled as a cadence, the ``Once`` shape plan step R2e-3 deleted.
        # There is deliberately no upper CHECK, for the reason ``interval_n``
        # has none: a ceiling above what a month can hold is vacuous rather
        # than wrong, and the ``smallint`` type is the top.
        db.CheckConstraint(
            "max_per_month IS NULL OR max_per_month > 0",
            name="ck_recurrence_rules_positive_max_per_month",
        ),
        # Which UNITS may carry one (plan step plan#2302): a cadence measured
        # in whole months fires at most once a month, so a ceiling beside one
        # could never bind.  It reads the carried flag rather than ``unit_id``
        # because a CHECK comparing that to a literal id would tie the table
        # to a seed.  ``recurrence._resolution._require_month_ceiling_pair``
        # answers first, with the sentence a person can act on; this guards a
        # writer that never sees it -- a restore, a hand edit, a raw-SQL
        # migration.
        db.CheckConstraint(
            "max_per_month IS NULL OR NOT unit_has_day_of_month",
            name="ck_recurrence_rules_max_per_month_unit",
        ),
        # At most ONE closing bound -- the EXCLUSIVE ARC this pair of nullable
        # columns is.  A rule that both ends on a date and after N occurrences
        # has two answers to "when does this stop", and the engine would have
        # to pick one; the schema refuses the question.
        #
        # SQL has no sum type to write "one of three shapes" in, which is why
        # the arc is a pair plus a CHECK here and ONE value above the door
        # (``app.services.recurrence.EndBound``, plan step R7b-3).  This
        # constraint therefore guards writers that never see that value -- a
        # restore, a hand edit, a raw-SQL migration -- rather than the
        # application, which cannot express the violation.
        db.CheckConstraint(
            "end_date IS NULL OR max_occurrences IS NULL",
            name="ck_recurrence_rules_single_end_bound",
        ),
        db.CheckConstraint(
            "max_occurrences IS NULL OR max_occurrences > 0",
            name="ck_recurrence_rules_positive_max_occurrences",
        ),
        # ``nominal_day`` records a day the FIRST OCCURRENCE'S MONTH clamped,
        # and nothing else (ruling R-R3).  Three conjuncts and none is implied
        # by the others: the range keeps it a real month-end day; the
        # comparison keeps it from restating a day ``starts_on`` already
        # carries -- a ``nominal_day`` of 15 beside a 15th would be the second
        # representation the D28 ruling removes; and the CLAMP EQUALITY keeps
        # it from sitting beside a date that was never clamped at all.
        #
        # **The third conjunct landed at plan step R7c-b and it retired a
        # FENCE.**  Without it ``(starts_on = 2026-04-15, nominal_day = 30)``
        # passes -- 30 is in range and exceeds 15 -- and April HAS a 30th, so
        # the rule would fire on a day the date does not name.  Only a runtime
        # guard in ``recurrence._occurrence._require_generable`` caught that,
        # and a guard whose entire reachability condition is "the schema cannot
        # say it" is what this project removes rather than tests.  Presence now
        # IMPLIES the clamp happened, so the absence has ONE meaning.
        #
        # ``EXTRACT(day FROM <date>)`` is IMMUTABLE for a ``date`` argument
        # (it lowers to ``date_part(text, date)``; the STABLE spellings are the
        # ``timestamptz`` ones), and ``date_trunc`` is cast to ``::timestamp``
        # for the same reason -- which is what lets both appear in a CHECK at
        # all.  Verified against the live server rather than assumed.
        db.CheckConstraint(
            "nominal_day IS NULL OR ("
            "nominal_day BETWEEN 29 AND 31 "
            "AND nominal_day > EXTRACT(day FROM starts_on) "
            "AND EXTRACT(day FROM starts_on) = LEAST(nominal_day, "
            "EXTRACT(day FROM (date_trunc('month', starts_on::timestamp) "
            "+ INTERVAL '1 month - 1 day'))))",
            name="ck_recurrence_rules_nominal_day",
        ),
        # Only a unit with a day of the month has a day to clamp (plan step
        # plan#2302), which the CHECK above cannot say: it holds the clamp, and
        # a paycheck rule starting 2026-04-30 with a nominal day of 31 passes
        # it.  ``recurrence._nominal_day._require_nominal_day_pair`` answers
        # first; this guards the writer that never sees it, as the ceiling's
        # twin above does.
        db.CheckConstraint(
            "nominal_day IS NULL OR unit_has_day_of_month",
            name="ck_recurrence_rules_nominal_day_unit",
        ),
        # ``weekday_reading`` holds one of two literals, and the position each
        # needs of ``starts_on`` (plan step recurrence:R8-c, rulings plan#2668
        # and plan#2670).  The week number and the weekday come from the date,
        # so this is all a row states about them:
        #
        # * ``'from_start'`` -- the weekday counted from the month's start,
        #   "the third Tuesday".  Only the 28th or earlier: four whole weeks
        #   fit in the shortest month, so every month holds a 1st-4th of every
        #   weekday and only some hold a 5th, and there is no fifth.
        # * ``'from_end'`` -- the month's last such weekday, so the date sits
        #   within a week of its month's end.
        #
        # **Two literals rather than a ``ref`` id, because of this CHECK**: it
        # must name which value it is checking, and a CHECK comparing a ref id
        # to a literal would tie the table to a seed (the reason the
        # ``max_per_month`` paragraph above gives).  The two arms are also the
        # whole domain, so a third value fails both.  The UNIT half -- only a
        # month-measured cadence has a month coordinate -- names a ref id for
        # that same reason, so it is held at the door and at construction
        # (``recurrence._month_coordinate._require_weekday_reading_pair``), as
        # ``nominal_day``'s and ``max_per_month``'s are; plan#2302 moves all
        # three here.  IMMUTABLE expressions only, for the reason the
        # ``nominal_day`` CHECK above gives.
        db.CheckConstraint(
            "weekday_reading IS NULL "
            "OR (weekday_reading = 'from_start' "
            "AND EXTRACT(day FROM starts_on) <= 28) "
            "OR (weekday_reading = 'from_end' "
            "AND EXTRACT(day FROM starts_on) + 7 > "
            "EXTRACT(day FROM (date_trunc('month', starts_on::timestamp) "
            "+ INTERVAL '1 month - 1 day')))",
            name="ck_recurrence_rules_weekday_reading",
        ),
        # The EXCLUSIVE ARC ruling R-R25 put on this table: a nominal day reads
        # the first occurrence by its clamped day of the month and a weekday
        # reading by its weekday, and one date is read one way.  Stateable
        # only because both sit on this row -- the satellite table the arc
        # replaced could not have carried it, a CHECK reading one row.
        db.CheckConstraint(
            "weekday_reading IS NULL OR nominal_day IS NULL",
            name="ck_recurrence_rules_one_month_reading",
        ),
        # A closing DATE never precedes the first occurrence.  **HELD BACK from
        # plan step R7c-b to plan step R7d-g on a developer ruling
        # (2026-08-15), and the absence was a ruling rather than an
        # omission**: until R7d-g these two columns held two different KINDS
        # of fact -- what a user AUTHORS about a repeating definition, where a
        # stop before the start is a mistake to report, and what the app
        # DERIVED for a recurring loan payment, where an EMPTY window is a
        # legitimate answer (a loan paid off before its first contractual
        # installment owes nothing, and ``loan_recurrence_sync`` stated that
        # by writing a payoff below the installment date).  A CHECK cannot
        # tell the two apart, so it would have turned a correct derived state
        # into an unhandled ``CheckViolation`` out of a balance true-up.
        # R7d-g deleted the writers of the derived half, NULLed what they had
        # written (migration ``bf50951a3599``, ruling **R-R80**), and the derived
        # stop lives only as a VALUE the composed door builds on read
        # (``recurrence.DerivedStop``) -- so every stored pair is an owner's
        # word, and the ONE writer of these columns
        # (``recurrence._authoring._author``) grades the pair it is about to
        # store -- the NORMALISED start beside the stated stop -- and refuses
        # an inverted one before touching the row (``EmptyAuthoredWindowError``;
        # the two authoring doors grade the AUTHORED pair for the user's
        # sentence, and the loan-params door translates the writer's refusal
        # into one naming the transfer).  True by construction at the
        # writer; this is the backstop for one that never sees it.  A COUNT
        # bound cannot invert against a date, so ``max_occurrences`` is not
        # named here.
        db.CheckConstraint(
            "end_date IS NULL OR end_date >= starts_on",
            name="ck_recurrence_rules_valid_window",
        ),
        # How far the application's calendar reaches, mirrored on the column
        # for a writer that never sees a schema -- the same job
        # ``ck_template_amount_versions_effective_date_range`` does for the
        # other user-authored date, and the same two dates
        # (``app.utils.dates.CALENDAR_DATE_MIN`` / ``_MAX``).
        #
        # **It backs a measured 500 rather than a hypothetical one.**  Past the
        # saved horizon the pay calendar PROJECTS the covering paycheck by
        # adding ``cadence_days`` to a start; a ``starts_on`` near
        # ``date.max`` overflows that addition with an ``OverflowError`` from
        # outside the recurrence package's error hierarchy, so the recurrence
        # preview -- which reads the value from ``request.args``, where no
        # schema stands -- answered a stack trace to any signed-in user.
        # ``_resolution._require_authored_start_window`` is the door-side
        # mirror this backs.
        db.CheckConstraint(
            "starts_on BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'",
            name="ck_recurrence_rules_starts_on_range",
        ),
        # The carried flag IS its unit's (plan step plan#2302), onto
        # ``uq_recurrence_units_id_has_day_of_month``.  ``ON DELETE RESTRICT``
        # matches ``fk_recurrence_rules_unit_id``, which stays beside it as
        # ``fk_transactions_owner_account``'s single-column twin does: that key
        # is about the unit's EXISTENCE and this one about AGREEMENT.  It also
        # holds the unit's side: a span changed so the generated flag flips is
        # refused while any rule names the unit.
        db.ForeignKeyConstraint(
            ["unit_id", "unit_has_day_of_month"],
            ["ref.recurrence_units.id", "ref.recurrence_units.has_day_of_month"],
            name="fk_recurrence_rules_unit_day_of_month",
            ondelete="RESTRICT",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # ---- The owner: exactly one of these two (plan step R-F6) ------------
    #
    # ``ON DELETE CASCADE`` is the whole of finding **F-6**'s fix: the rule
    # goes when its definition does, from the hard-delete routes, from a user
    # deletion, from a restore, from hand SQL -- from anywhere, because it is
    # the database that disposes of it rather than a route that remembers to.
    # ``budget.recurrence_rules`` is in ``audit_infrastructure.AUDITED_TABLES``
    # and a cascaded DELETE fires row-level triggers, so the disposal is still
    # recorded in ``system.audit_log`` (verified against the live server).
    #
    # Nullable, and the nullability is the ARC rather than an optional owner:
    # ``ck_recurrence_rules_one_owner`` above requires exactly one, so every
    # row has an owner and no row has two.
    #
    # Pylint: ``duplicate-code`` -- the arc's two FK columns and the two
    # relationships below read almost verbatim like
    # ``template_amount_version``'s, and that is the POINT rather than an
    # accident: both are satellites of the same two parent tables and the
    # developer ruled the same shape for both (2026-08-11 there, 2026-08-18
    # here).  What is shared is a SCHEMA DECLARATION, not logic -- the two
    # tables' unique indexes differ (one row per owner here, one per owner per
    # date there), their relationships carry different ``back_populates`` and
    # cardinality, and their ``__table_args__`` share nothing else -- so a
    # mixin could hoist the two ``db.Column`` calls and would leave every
    # constraint that gives them meaning behind, which is indirection that
    # removes no rule (coding-standards rule 13).  One-sided disable; the
    # precedent module stays un-disabled.
    # pylint: disable=duplicate-code
    transaction_template_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transaction_templates.id", ondelete="CASCADE",
            name="fk_recurrence_rules_transaction_template_id",
        ),
        nullable=True,
    )
    transfer_template_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transfer_templates.id", ondelete="CASCADE",
            name="fk_recurrence_rules_transfer_template_id",
        ),
        nullable=True,
    )
    # The third arm (plan step salary:R15-b, rulings R-SAL3 and R-SAL32): a
    # payroll deduction's own FREQUENCY is a recurrence rule against the pay
    # calendar -- "every paycheck, at most 2 a month" for the 24-per-year
    # line, "monthly, on the first paycheck on or after the 1st" for the 12
    # -- and NO rule is R-SAL3's NULL, every paycheck.  Same disposal as the
    # other two arms: the database cascades the rule with its deduction.
    paycheck_line_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "salary.paycheck_lines.id", ondelete="CASCADE",
            name="fk_recurrence_rules_paycheck_line_id",
        ),
        nullable=True,
    )
    # ---- The cadence: the two axes, their interval and their anchor ------
    #
    # **The WHOLE of what a rule says about when it fires, since plan step
    # R7c-c.**  They landed NULLABLE at R7c-a beside the closed-set columns
    # they replace, were backfilled and dual-written there, became
    # authoritative and ``NOT NULL`` at R7c-b, and the encoding beside them was
    # dropped at R7c-c -- the expand / migrate / contract ruling R-R18 laid
    # out, with the destructive DDL last and no translation shim in any leaf.
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.recurrence_units.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_unit_id",
        ),
        nullable=False,
    )
    # The unit's ``has_day_of_month``, carried so this row's CHECKs can read it
    # (see the module docstring).  NOT NULL because the composite key is
    # ``MATCH SIMPLE``: a NULL member is not checked at all, so a nullable copy
    # would hold nothing.  Written beside ``unit_id`` by the one writer,
    # ``recurrence._authoring._author``.
    unit_has_day_of_month = db.Column(db.Boolean, nullable=False)
    placement_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.period_placements.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_placement_id",
        ),
        nullable=False,
    )
    shift_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.business_day_shifts.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_shift_id",
        ),
        nullable=False,
    )
    # The rule's FIRST OCCURRENCE, and since the developer ruling of
    # 2026-08-14 (plan ledger row D28) that is its ONE meaning for every unit:
    # for a calendar cadence the first date the cadence fires on, for a
    # pay-period cadence the payday of the first paycheck it bills in.  Nothing
    # is generated before it, and its position in the cycle IS the rule's
    # phase -- which is why ``day_of_month`` and ``month_of_year`` have nothing
    # left to say once R7c-b's readers move here.
    #
    # The ruling REPLACED this arc's earlier specification, which made the
    # column the opening validity BOUND.  A bound is not in the cycle's residue
    # class, so it cannot carry the phase: measured on a 2026-08-14 production
    # clone, 18 of the 24 live multi-month rules would have fired in the wrong
    # months forever under that reading.
    #
    # Produced by ``recurrence._resolution.resolve``, which normalises a
    # pay-period cadence's authored date onto the payday hosting it and returns
    # every other unit's verbatim -- and that answer is also what the R7c-a
    # migration's SQL backfill is proven against.  ``NOT NULL`` from R7c-b,
    # which is the leaf that made every reader take it.
    starts_on = db.Column(db.Date, nullable=False)
    # The day the rule MEANS when ``starts_on``'s own month was too short to
    # hold it -- April has no 31st, so a day-31 rule first occurring there
    # carries ``starts_on = 2026-04-30`` and ``nominal_day = 31``.  NULL when
    # the date holds the day itself, which is every rule whose day is 1-28,
    # every rule that does not fire on a day of the month at all, and all 46
    # live rules as of 2026-08-14.  Presence is the discriminator (ruling
    # R-R3), and it is what stops a month-end rule decaying to the 30th
    # forever.
    #
    # **The domain is 29-31, not 1-31**, and the difference is the ruling: a
    # value at or below ``starts_on``'s own day would be a SECOND statement of
    # a day the date already holds, which is the two-representations defect
    # this step removes.  The CHECK says both halves -- the range, and that it
    # exceeds the day the date carries -- so the only rows it admits are the
    # ones where the date genuinely lost the intent.
    nominal_day = db.Column(db.SmallInteger, nullable=True)
    # How ``starts_on`` is read when it is read by its WEEKDAY:
    # ``'from_start'`` ("the third Tuesday") or ``'from_end'`` ("the last
    # Friday"), the values of ``recurrence.WeekdayReading``.  NULL reads it by
    # its day of the month, which is every rule stored before plan step
    # recurrence:R8-c and every rule whose unit has no month coordinate:
    # absence is the discriminator and ``ck_recurrence_rules_weekday_reading``
    # constrains presence, the discipline ``nominal_day`` keeps one column up.
    # A string because the CHECK above names the values; the write door
    # stores the member's value and the read door reads the member back.
    weekday_reading = db.Column(db.String(10), nullable=True)
    # How many ``unit_id``\\ s pass between occurrences.
    #
    # **It says what the cadence says from plan step R7c-c, and it did not
    # before.**  ``encode_cadence`` wrote ``1`` for every pattern whose interval
    # was baked into its NAME, so a Quarterly rule stored
    # ``(interval_n = 1, unit_id = month)`` -- MONTHLY at face value, 12
    # occurrences a year where 4 were owed -- and every reader had to recover
    # the ``3`` through ``pattern_id``.  That step's migration re-points the
    # four live rules the encoding touched (2 quarterly to 3, 2 semi-annual to
    # 6) and drops the pattern the value had to be read through.
    #
    # ``CHECK (interval_n > 0)`` is the BOTTOM of the domain, and the ``integer``
    # type is the top.  There is no upper CHECK because there is nothing for one
    # to say that the type does not: an interval whose stride carries the second
    # occurrence past the calendar this application reaches is honest -- such a
    # rule fires once, and ``_months.walk_months`` stops there rather than
    # walking off the end of ``date``.
    #
    # **The top half is stated at the SUBMISSION and it was not, until plan step
    # R7c-c** (``app.utils.digit_strings.MAX_INTEGER_COLUMN``, stated there
    # since balance:X-dj).  A value above ``int4`` reaches the flush as an
    # unhandled ``NumericValueOutOfRange`` -- a 500 on a
    # door a crafted POST reaches -- and until that step the accident of the
    # closed pattern set covered three of the four units, because
    # ``is_authorable`` refused any MONTH or YEAR interval above 6.
    interval_n = db.Column(
        db.Integer, nullable=False, default=1, server_default=db.text("1"),
    )
    # The most occurrences any one calendar month admits (plan step
    # salary:R15-a, ruling R-SAL29, amended 2026-09-13): ``2`` on an
    # every-paycheck rule is a month's first two paychecks ON THE OWNER'S
    # CALENDAR whatever paycheck the rule started on -- the cadence eleven of
    # the developer's twelve payroll deductions carry and his Health
    # Insurance Allowance is paid on -- and on a week cadence the rule's own
    # first N of the month, a weekly date being no payday.  Applied by the
    # occurrence walk for every unit (``recurrence._occurrence._ceilinged``)
    # and read by every count of "how often" (``recurrence.Cadence``), so a
    # ceilinged rule prices at 24 a year and not 26 everywhere it is asked.
    #
    # NULLABLE because ``NULL`` IS the ordinary rule: no ceiling, which is
    # every rule authored before this step and every calendar-month cadence,
    # whose occurrences cannot repeat within a month and on which a value here
    # is refused at construction and by
    # ``ck_recurrence_rules_max_per_month_unit``.  Presence means the ceiling
    # can bind, so the absence has one meaning -- the same discipline
    # ``nominal_day`` keeps one column up.
    max_per_month = db.Column(db.SmallInteger, nullable=True)
    # Optional end date -- recurrence stops generating after this date.
    # NULL means indefinite (no end).
    end_date = db.Column(db.Date, nullable=True)
    # Count-bounded end: stop after this many occurrences.  Genuinely
    # optional and mutually exclusive with ``end_date`` (see the
    # ``ck_recurrence_rules_single_end_bound`` CHECK above); NULL means "not
    # count-bounded", which is what all 46 live rules carry as of 2026-08-13.
    # Read by the occurrence walk since plan step R3
    # (``recurrence._occurrence._bounded``); its first WRITER is plan step
    # R7b-3's "Ends" form control, which took the count-bounded end over from
    # plan step R8's four add-ons.
    max_occurrences = db.Column(db.Integer, nullable=True)

    # Relationships
    #
    # **FOUR have been deleted from this model and none replaced.**
    # ``pattern`` went at plan step R7a (ledger row **D17**): ``lazy="joined"``,
    # so every rule load eager-joined ``ref.recurrence_patterns`` for a single
    # reader, the ``recurrence_cell`` macro's fallback branch, which titled the
    # row's ``name`` for a pattern the application does not model.  The
    # Recurring surface words a recurrence from what it MEANS
    # (:func:`app.services.recurrence.describe`), so the branch, the join and
    # the last ``.name``-for-display coupling on this table left together --
    # and plan step R7c-c dropped the column behind it.
    # ``start_period`` went at plan step R7b-4 (row **D30**), the same defect
    # one line down: eager-joined ``budget.pay_periods`` for ZERO readers.
    # ``month_anchor`` went at plan step R7c-c with the table it pointed at
    # (ruling **R-R16**): ``budget.recurrence_month_anchors`` was to hold the
    # clamped day once the anchor became a column, and that day is
    # ``nominal_day`` above instead, under a CHECK tying its presence to
    # meaning.  The counter-argument it settled is worth keeping: a nullable
    # column is a wide sparse table one column at a time UNLESS its absence is
    # the discriminator and its presence is constrained, which
    # ``ck_recurrence_rules_nominal_day`` is what makes true here.
    #
    # ``weekday_anchor`` went at plan step **R8-c** with its table, unwritten
    # (ruling **R-R25**, amended by **plan#2668**; migration
    # ``897a6663d221``).  ``budget.recurrence_weekday_anchors`` was to hold
    # "the third Friday" as ``(nth_week, weekday)``, and a rule fires on a
    # day-of-month OR an nth-weekday, never both: an invariant that is a CHECK
    # only while both facts sit on one row.  On this row the two fields
    # became one, ``weekday_reading``, because the week number and the weekday
    # are ``starts_on``'s own, and only WHICH reading -- from the month's start
    # or as its last such weekday -- is a fact the date cannot carry.
    # The owning definition, one arm of the exclusive arc each.  MANY-TO-ONE
    # onto a primary key, so SQLAlchemy resolves each from the identity map
    # when the owner is already loaded (the ``use_get`` path) -- which is every
    # reader that reached the rule through ``template.recurrence_rule``, so
    # reading :attr:`user_id` back off a rule costs no query on the paths that
    # have one.  ``lazy="select"`` rather than joined: a rule is loaded FROM
    # its template far more often than the reverse, and eager-joining the owner
    # would re-fetch on every such load the row the caller is already holding.
    transaction_template = db.relationship(
        "TransactionTemplate", back_populates="recurrence_rule",
    )
    transfer_template = db.relationship(
        "TransferTemplate", back_populates="recurrence_rule",
    )
    paycheck_line = db.relationship(
        "PaycheckLine", back_populates="recurrence_rule",
    )

    @property
    def user_id(self) -> int:
        """The owner, reached through the definition this rule belongs to.

        A COLUMN until plan step R-F6, beside the ``user_id`` its template
        carried, with nothing tying the two together -- so the pair could
        disagree and one runtime comparison in one route helper was the whole
        of what noticed.  Derived here instead, which is what makes them one
        value rather than two that agree.

        Kept under the column's own name deliberately: every reader states
        ``calendar_for(rule.user_id)`` or ``RecurrenceSpec(user_id=...)``, and
        those readers are asking who owns the rule, not which table holds the
        answer.

        Returns:
            The owning user's id.

        Raises:
            ValueError: The rule has no owner at all.  Unreachable through
                either write door -- both take one -- and unreachable for a
                row, which ``ck_recurrence_rules_one_owner`` refuses without
                one (exactly one of THREE since plan step salary:R15-b).  What
                it catches is a rule CONSTRUCTED directly, which is a fixture
                bypassing the seam rather than an application state,
                and it names that rather than surfacing as an ``AttributeError``
                on ``None`` three frames down.
        """
        # ``is None`` rather than truthiness, per the coding standard: an ORM
        # instance defines no ``__bool__`` today, so the two agree -- and a
        # model that later defined one would silently make this read the wrong
        # arm of the arc.
        owner = self.transaction_template
        if owner is None:
            owner = self.transfer_template
        if owner is None:
            # The deduction arm (plan step salary:R15-b) reaches the owner
            # through its profile: ``PaycheckLine.user_id`` is that read.
            owner = self.paycheck_line
        if owner is None:
            raise ValueError(
                "this RecurrenceRule has no owning definition, so it has no "
                "owner to report.  A rule with no owning FK set is a "
                "TRANSIENT rule (build_transient_rule); state the user on the "
                "RecurrenceSpec instead of reading it back off the rule."
            )
        return owner.user_id

    def __repr__(self):
        return (
            f"<RecurrenceRule id={self.id} "
            f"every {self.interval_n} unit={self.unit_id} "
            f"from {self.starts_on}>"
        )
