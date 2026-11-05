"""
Shekel Budget App -- Pay Stub Models (salary schema)

A real PAY STUB, TRANSCRIBED line by line and dated (plan step **salary:S11-a**,
the tables leaf of ``S11``; ruling **R-SAL42**, which amends **R-SAL41** and
**R-SAL9**).  Four tables, one per kind of thing a stub prints (round 4, fork
11: "a table per kind of line"):

    salary.pay_stubs               one row per stub: its payday, base pay, the
                                   "Use for pricing" switch
    salary.pay_stub_line_amounts   what the stub shows for one PAYCHECK LINE
    salary.pay_stub_withholdings   what the stub shows for one TAX
    salary.pay_stub_one_offs       a named amount the paycheck lines do not hold

**Every column of the three child tables is required**, so a row that is two
things at once -- a paycheck line's amount that is also a tax, a one-off with no
kind -- cannot exist, and neither can a duplicate WITHIN a table: one amount per
paycheck line, one per tax, one one-off per name, each per stub.  The ruling's
own words were "Every column is required, so a row that is two things at once
or a duplicate can't exist"; **a duplicate ACROSS tables is not structural** --
a one-off named like one of the profile's paycheck lines, or like a tax, is
storable -- and ruling **R-SAL45** assigns it to the entry door (``S11-b``),
which refuses a one-off whose name matches a paycheck line or a tax ("enter it
on the line instead") beside its printed-net check.

**What is NOT stored, and why.**  Gross, taxable wages and net pay are DERIVED
from the lines (rule 14); the printed gross and net are typed once at the entry
door as checks against the lines and never kept (rulings **R-SAL99** and
**R-SAL42**).

**What a stub line records is its own** (ruling **R-SAL58**, "Stub records its
kind", plan step ``S11-c-1``, which retired **R-SAL56**'s refusal before it was
built): the amount the stub prints for a paycheck line AND the kind it prints it
under, as a one-off keeps its own kind.  The line's kind is the app's plan and
stays editable; the stub's is what the document printed on its payday.  The two
may differ, and the entry door lists a mismatch the way it lists an amount's,
so a saved stub always adds up by its own figures whatever later happens to its
lines (finding **SAL-567**).

**Nothing here is ever deleted** (fork 8a', "Nothing is ever deleted"), and since
ruling **R-SAL44** that holds for every routine writer, not only for the absence
of a delete door (a superuser disabling triggers is the named limit, in
:mod:`app.pay_stub_infrastructure`).  A stub the owner no longer wants pricing is switched off
(``use_for_pricing``), kept, and still editable.  Three guards:

* **The stub row** -- a trigger family (:mod:`app.pay_stub_infrastructure`)
  refuses a ``DELETE`` of a stub, a change of its ``salary_profile_id`` and
  (ruling **R-SAL46**) a ``TRUNCATE`` of any of the four tables.  Payday,
  base pay, the switch, notes and every line stay editable; removing one line
  is a row ``DELETE`` and stays allowed (and audited).
* **What holds a stub** -- ``pay_stubs.salary_profile_id`` is ``ON DELETE
  RESTRICT``, not the ``CASCADE`` :class:`~app.models.mixins.SalaryProfileScopedMixin`
  carries, so a profile holding a stub cannot be hard-deleted, and nor,
  through the profile's own cascade from its user, can the owner (the salary
  door ARCHIVES a profile and never deletes one).
* **What a stub holds** -- a line amount's paycheck line is ``ON DELETE
  RESTRICT`` (fork 10: "Deleting a paycheck line that any stub names is refused
  ... end it instead"), so a line a stub names outlives its end.

The child tables' stub keys ``CASCADE`` (a line of a stub is part of it), which
runs only under a deliberate lift of the trigger family.

**Trigger order decides nothing.**  A profile delete reaches a stub two ways --
through the stub's own key, and through the paycheck line's cascade from the
profile -- and ruling **R-CC32** states the principle: a delete's outcome must
not depend on which path PostgreSQL evaluates first.  The stub key's
``RESTRICT`` refuses at the first level, so the refusal names it; and the
trigger family's ``DELETE`` arm fires on a cascaded delete of a stub like any
other, so even a ``CASCADE`` on that key would be refused rather than let order
decide (measured by this leaf's second review, in both orders).

**A stub's line is its own profile's, by key.**  A line amount carries the stub's
``salary_profile_id`` as a co-located key column, held equal to the stub's by
``fk_pay_stub_line_amounts_pay_stub`` (onto ``uq_pay_stubs_id_profile``) and to
the paycheck line's by ``fk_pay_stub_line_amounts_paycheck_line`` (onto
``uq_paycheck_lines_id_profile``).  So a stub cannot name another job's line, or
another owner's, whatever a writer passes.  It is the construction
``fk_transaction_entries_owner_transaction`` / ``_owner_account`` use (ruling
**R-BAL76**): a key the database keeps true instead of a copy a writer keeps in
step.

The one writer is the entry door (``S11-b``,
:mod:`app.services.pay_stub_service`).  Since plan step **salary:S11-c-2c** the
paycheck engine prices each paycheck's four taxes from one switched-on stub
(``paycheck_calculator._stubs``), reading a profile's stubs through
``SalaryProfile.pay_stubs``, a read-only relationship.
"""

from app.extensions import db
from app.models.mixins import OptimisticLockMixin, TimestampMixin


class PayStub(OptimisticLockMixin, TimestampMixin, db.Model):
    """One real pay stub of one salary profile, dated at its payday.

    Columns:

      ``payday``          -- the stub's date, which the entry door requires to be
          one of the owner's paydays.  UNIQUE per profile (fork 4, "One stub per
          payday per job"): entering a date again OPENS that stub for editing
          (ruling **R-SAL50**), a new stub on a held payday is refused rather
          than let overwrite it (**R-SAL52**), and the audit log keeps every
          edit's old figures.  A DATE and not a pay-period key, because
          the date is the document's own fact and a pay period is a row the pay
          calendar may regenerate.
      ``base_pay``        -- the base pay line the stub prints.  Required and
          above zero.  It feeds only the DIFFERENCE the formulas price; the
          paycheck's own base pay keeps its one home in the salary and its
          raises (fork 5, "The app's salary").
      ``use_for_pricing`` -- the "Use for pricing" switch, on when entered (fork
          8a').  Off keeps the stub and prices every paycheck, past and future,
          as if it had never been entered.  A named column rather than
          :class:`~app.models.mixins.IsActiveMixin`'s ``is_active``, whose meaning
          is "stops driving new work while its history stays valid": this switch
          reaches backward too.
      ``notes``           -- free text.  Nullable: a stub needs no annotation to
          be valid.

    ``salary_profile_id`` is declared here rather than taken from
    :class:`~app.models.mixins.SalaryProfileScopedMixin`, whose ``CASCADE`` this
    table must not have (see the module docstring).  It never changes after the
    stub is entered: the trigger family refuses a move.

    ``version_id`` is :class:`~app.models.mixins.OptimisticLockMixin`'s, and
    **it guards THIS ROW only**.  SQLAlchemy bumps it when the stub row itself is
    updated, never for an edit to a line amount, a withholding or a one-off
    alone (measured by this leaf's adversarial review: a child-only edit left it
    at ``1``).  So two concurrent line-only edits both commit and the later
    silently wins, unless the entry door (``S11-b``) writes the stub row on
    every edit of its lines -- which is what makes its ``StaleDataError`` guard
    cover the stub as one act.
    """

    __tablename__ = "pay_stubs"
    __table_args__ = (
        db.UniqueConstraint(
            "salary_profile_id", "payday",
            name="uq_pay_stubs_profile_payday",
        ),
        # The superkey ``fk_pay_stub_line_amounts_pay_stub`` targets, so a line
        # amount's co-located profile key is the stub's by construction.
        db.UniqueConstraint(
            "id", "salary_profile_id",
            name="uq_pay_stubs_id_profile",
        ),
        db.CheckConstraint("base_pay > 0", name="ck_pay_stubs_positive_base_pay"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_pay_stubs_version_id_positive",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "salary.salary_profiles.id",
            name="fk_pay_stubs_salary_profile_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    payday = db.Column(db.Date, nullable=False)
    base_pay = db.Column(db.Numeric(12, 2), nullable=False)
    # Default on at BOTH tiers: the Python-side default serves the ORM
    # constructor, the ``server_default`` a raw INSERT, and the two agree so
    # ``compare_server_default`` reports no drift.
    use_for_pricing = db.Column(
        db.Boolean, nullable=False, default=True,
        server_default=db.text("true"),
    )
    notes = db.Column(db.Text)
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.
    # created_at / updated_at: from TimestampMixin.

    salary_profile = db.relationship("SalaryProfile")
    # The three kinds of line a stub prints, each owned by the stub: removing
    # one from its collection deletes it, which is how the entry door edits a
    # stub line by line.  ``line_amounts`` joins over BOTH columns of
    # ``fk_pay_stub_line_amounts_pay_stub``, so appending an amount copies the
    # stub's profile into the amount's co-located key.  Each loads WITH the
    # stub, one query per collection for every stub loaded together
    # (``selectin``), since plan step salary:S11-c-2c: the engine reads all
    # three of a profile's every switched-on stub to choose and price one, so
    # a lazy load per stub would cost three queries per stub per profile.
    line_amounts = db.relationship(
        "PayStubLineAmount", back_populates="pay_stub",
        cascade="all, delete-orphan", lazy="selectin",
    )
    withholdings = db.relationship(
        "PayStubWithholding", back_populates="pay_stub",
        cascade="all, delete-orphan", lazy="selectin",
    )
    one_offs = db.relationship(
        "PayStubOneOff", back_populates="pay_stub",
        cascade="all, delete-orphan", lazy="selectin",
    )

    def __repr__(self):
        return (
            f"<PayStub profile_id={self.salary_profile_id} "
            f"payday={self.payday} base_pay={self.base_pay}>"
        )


class PayStubLineAmount(db.Model):
    """What one stub shows for one of its profile's PAYCHECK LINES.

    One row per (stub, paycheck line) -- ``uq_pay_stub_line_amounts_stub_line``.
    The amount is the stub's figure for that line and ``paycheck_line_kind_id``
    the kind the stub prints it under (a taxable earning, a pre-tax deduction,
    ...; ruling **R-SAL58**), both the DOCUMENT's facts on its payday.  The
    paycheck line's own amount and kind are the app's plan: a stub that
    disagrees with either is REPORTED by the entry door for the owner to fix
    one side (fork 2, "Taxes only"), and a later edit of the line moves nothing
    here.

    ``salary_profile_id`` is a co-located KEY, not a copy: two composite keys
    hold it equal to the stub's profile and to the paycheck line's, so the pair
    can only name a line of the stub's own profile (see the module docstring).
    """

    __tablename__ = "pay_stub_line_amounts"
    __table_args__ = (
        db.UniqueConstraint(
            "pay_stub_id", "paycheck_line_id",
            name="uq_pay_stub_line_amounts_stub_line",
        ),
        db.CheckConstraint(
            "amount >= 0",
            name="ck_pay_stub_line_amounts_nonneg_amount",
        ),
        # The amount is PART of its stub and goes with it.
        db.ForeignKeyConstraint(
            ["pay_stub_id", "salary_profile_id"],
            ["salary.pay_stubs.id", "salary.pay_stubs.salary_profile_id"],
            name="fk_pay_stub_line_amounts_pay_stub",
            ondelete="CASCADE",
        ),
        # ...and the line it names is the SAME profile's, and cannot be
        # deleted while a stub names it (fork 10: end it instead).
        db.ForeignKeyConstraint(
            ["paycheck_line_id", "salary_profile_id"],
            [
                "salary.paycheck_lines.id",
                "salary.paycheck_lines.salary_profile_id",
            ],
            name="fk_pay_stub_line_amounts_paycheck_line",
            ondelete="RESTRICT",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    pay_stub_id = db.Column(db.Integer, nullable=False)
    paycheck_line_id = db.Column(db.Integer, nullable=False)
    salary_profile_id = db.Column(db.Integer, nullable=False)
    paycheck_line_kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.paycheck_line_kinds.id",
            name="fk_pay_stub_line_amounts_paycheck_line_kind_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    pay_stub = db.relationship("PayStub", back_populates="line_amounts")

    def __repr__(self):
        return (
            f"<PayStubLineAmount stub_id={self.pay_stub_id} "
            f"line_id={self.paycheck_line_id} ${self.amount}>"
        )


class PayStubWithholding(db.Model):
    """What one stub shows for one TAX.

    One row per (stub, withholding kind) --
    ``uq_pay_stub_withholdings_stub_kind``; the kind comes from
    ``ref.withholding_kinds`` (:class:`app.enums.WithholdingKindEnum`), so a tax
    this app does not yet name is a new reference row rather than a new column.
    A stub supplies these and ONLY these to pricing (fork 2, "Taxes only").
    ``$0.00`` is a real figure (a federal line fully offset by credits), which is
    why the bound is ``>= 0``; which taxes a job's stub MUST carry is the entry
    door's rule, not this table's.
    """

    __tablename__ = "pay_stub_withholdings"
    __table_args__ = (
        db.UniqueConstraint(
            "pay_stub_id", "withholding_kind_id",
            name="uq_pay_stub_withholdings_stub_kind",
        ),
        db.CheckConstraint(
            "amount >= 0",
            name="ck_pay_stub_withholdings_nonneg_amount",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    pay_stub_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "salary.pay_stubs.id",
            name="fk_pay_stub_withholdings_pay_stub_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    withholding_kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.withholding_kinds.id",
            name="fk_pay_stub_withholdings_withholding_kind_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    pay_stub = db.relationship("PayStub", back_populates="withholdings")

    def __repr__(self):
        return (
            f"<PayStubWithholding stub_id={self.pay_stub_id} "
            f"kind_id={self.withholding_kind_id} ${self.amount}>"
        )


class PayStubOneOff(db.Model):
    """A named amount a stub prints that no paycheck line holds.

    Fork 8b, "Keep it as a one-off": a line that appeared on one stub only (a
    bonus, a one-time adjustment) is stored on the stub under its own name and
    one of the five paycheck-line kinds (``ref.paycheck_line_kinds``).  What that
    meant for pricing was the ruling's, verbatim: *"That stub then matches no
    normal paycheck, so it prices a paycheck only when no stub with matching
    lines exists, and the formulas price the one-off out."*  Ruling
    **R-SAL123** ("Only tax-changing one-offs", 2026-10-05) amends it, and plan
    step salary:S11-c-2c builds the amended rule
    (``app.services.paycheck_calculator.one_offs_change_taxes``): only a
    one-off that changes the stub's taxes -- a taxable earning or a pre-tax
    deduction, carrying money -- does that; an after-tax or post-tax one-off
    does not.  The name is unique within the stub
    (``uq_pay_stub_one_offs_stub_name``) and never blank; a name that clashes
    with one of the profile's paycheck lines or a tax is the entry door's to
    refuse (ruling **R-SAL45**), not this table's.
    """

    __tablename__ = "pay_stub_one_offs"
    __table_args__ = (
        db.UniqueConstraint(
            "pay_stub_id", "name",
            name="uq_pay_stub_one_offs_stub_name",
        ),
        db.CheckConstraint(
            "amount >= 0",
            name="ck_pay_stub_one_offs_nonneg_amount",
        ),
        # NOT NULL alone admits an empty string, which is a missing name by
        # another spelling; the ``ck_merchants_name_not_blank`` construction.
        db.CheckConstraint(
            "btrim(name) <> ''",
            name="ck_pay_stub_one_offs_name_not_blank",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    pay_stub_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "salary.pay_stubs.id",
            name="fk_pay_stub_one_offs_pay_stub_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    paycheck_line_kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.paycheck_line_kinds.id",
            name="fk_pay_stub_one_offs_paycheck_line_kind_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    name = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    pay_stub = db.relationship("PayStub", back_populates="one_offs")

    def __repr__(self):
        return (
            f"<PayStubOneOff stub_id={self.pay_stub_id} "
            f"'{self.name}' ${self.amount}>"
        )
