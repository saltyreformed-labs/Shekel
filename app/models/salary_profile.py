"""
Shekel Budget App -- Salary Profile Model (salary schema)

A salary profile defines an income source with its pay list, filing status,
state, and links to raises and deductions for paycheck calculation.
"""

from app.extensions import db
from app.models.mixins import (
    IsActiveMixin,
    OptimisticLockMixin,
    SortOrderMixin,
    TimestampMixin,
    UserScopedMixin,
)


#: How many days a pay period may end before the payday payroll scheduled for
#: it (``period_end_days_before_payday``, ruling plan#3096).  ZERO is a period
#: that ends on its scheduled payday; below zero would be a period scheduled to
#: be paid before it ends, which one gap cannot state for every paycheck (a
#: month-anchored period paid on the 25th), so the job's form refuses a Period
#: End after the payday its paycheck was scheduled for.  Not after the day it was PAID: a
#: payday convention that pays early, by more days than the gap, pays before
#: the period ends, and that stub is the job's to copy (task plan#3124).  A
#: YEAR is the top: no payroll pays a period that ended over a year before,
#: and the bound turns a mistyped year on a stub's date into a refusal rather
#: than a stored fact.  ``ck_salary_profiles_period_end_days_range`` is built
#: from these two, and the form's mistyped-year refusal quotes the top.
PERIOD_END_DAYS_BEFORE_PAYDAY_MIN = 0
PERIOD_END_DAYS_BEFORE_PAYDAY_MAX = 365


class SalaryProfile(
    UserScopedMixin, IsActiveMixin, SortOrderMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
    """A salary income profile used for net paycheck calculation.

    **It does NOT record how often the owner is paid, and that is plan step
    R-F16.**  ``pay_periods_per_year`` was an ``Integer`` column here, offered
    as a 12 / 24 / 26 / 52 dropdown, and it was the DIVISOR the paycheck
    engine turned an annual salary into one paycheck with.
    The owner's pay cadence (``budget.pay_schedule.cadence_days`` then; a
    ``budget.pay_eras`` row's since plan step ``pay_calendar:C17-a``) is
    the same fact -- the rhythm the owner's paydays arrive on -- and nothing
    validated one against the other,
    so a profile saying 26 beside a 7-day cadence modelled DOUBLE the owner's
    income (finding **F-16**).  The count is now derived from the cadence
    alone, by :attr:`app.services.pay_calendar.PayCadence.periods_per_year`,
    which is the one producer every monthly-equivalent conversion already
    read.  A profile's paycheck recurs every pay period BY DEFINITION -- that
    is what ``salary.profiles._paycheck_template`` authors -- so there was
    never a per-profile count for this column to hold.

    **Nor does it record what the salary pays, since plan step
    salary:X-av-3a** (ruling **R-SAL59**).  ``annual_salary`` was a
    ``Numeric(12, 2)`` column here, one undated yearly figure for all time, so
    editing it re-priced every paycheck not yet received and a correction
    could not be told from a raise (finding **N-237**).  What one paycheck
    pays from a dated payday on is :attr:`pay_entries`' fact
    (:class:`~app.models.salary_pay_entry.SalaryPayEntry`); the yearly figure
    is derived from it and never stored.

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent profile edits race for
    the bump; the loser raises ``StaleDataError`` and the route
    surfaces a flash + redirect.  See commit C-18 of the 2026-04-15
    security remediation plan.
    """

    __tablename__ = "salary_profiles"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "scenario_id", "name",
            name="uq_salary_profiles_user_scenario_name",
        ),
        # Within a scenario, a paycheck definition belongs to at most ONE
        # salary profile, active or not (plan step salary:X-av-1, ruling
        # R-SAL63 as scoped by R-SAL69, closing finding N-294).  The
        # amount model's PRICING lookup finds a row's profile within the row's
        # scenario, and two profiles on one template there gave its
        # ``{template_id: profile}`` map two candidates for one key, of which
        # it kept whichever the query returned last.  ``create_profile``
        # mints a fresh template per profile, so no door produces the state;
        # this makes it unstorable for every writer.  PER SCENARIO because a
        # template belongs to none: the rule lets a what-if scenario give the
        # same paycheck its own salary (five template-keyed doors ignore
        # scenario, and owner-scoped profile readers would count both: ledger
        # row SAL-570).  NULLs stay distinct (PostgreSQL's
        # default): a template's hard delete sets ``template_id`` NULL, and
        # several profiles may have lost theirs.
        db.UniqueConstraint(
            "scenario_id", "template_id",
            name="uq_salary_profiles_scenario_template",
        ),
        db.CheckConstraint("qualifying_children >= 0", name="ck_salary_profiles_nonneg_children"),
        db.CheckConstraint("other_dependents >= 0", name="ck_salary_profiles_nonneg_dependents"),
        db.CheckConstraint("additional_income >= 0", name="ck_salary_profiles_nonneg_add_income"),
        db.CheckConstraint(
            "additional_deductions >= 0",
            name="ck_salary_profiles_nonneg_add_deductions",
        ),
        db.CheckConstraint(
            "extra_withholding >= 0",
            name="ck_salary_profiles_nonneg_extra_withholding",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_salary_profiles_version_id_positive",
        ),
        db.CheckConstraint(
            "period_end_days_before_payday BETWEEN "
            f"{PERIOD_END_DAYS_BEFORE_PAYDAY_MIN} AND {PERIOD_END_DAYS_BEFORE_PAYDAY_MAX}",
            name="ck_salary_profiles_period_end_days_range",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transaction_templates.id", ondelete="SET NULL"),
    )
    # F-073 / C-43: explicit ondelete=RESTRICT + fk_* name.  Closes
    # the audit gap on the nine ref-table FKs that defaulted to
    # PostgreSQL's implicit NO ACTION; see app/extensions.py for the
    # full SHEKEL_NAMING_CONVENTION rationale.
    filing_status_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.filing_statuses.id",
            name="fk_salary_profiles_filing_status_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    name = db.Column(
        db.String(200), nullable=False,
        server_default=db.text("'Primary'"),
    )
    state_code = db.Column(
        db.String(2), nullable=False, default="NC",
        server_default=db.text("'NC'"),
    )
    # W-4 fields (IRS Pub 15-T Percentage Method inputs)
    qualifying_children = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    other_dependents = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    additional_income = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default=db.text("0"),
    )  # W-4 Step 4(a): other income
    additional_deductions = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default=db.text("0"),
    )  # W-4 Step 4(b): extra deductions
    extra_withholding = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default=db.text("0"),
    )  # W-4 Step 4(c): extra withholding per period
    # What this job's pay stub PRINTS as its gross (plan step salary:S11-c-2b,
    # ruling R-SAL102, "A yes/no on each job"): false -- base pay plus the
    # taxable earnings, the developer's employer and where every job starts --
    # or true, a stub whose gross also holds the after-tax earnings (a
    # reimbursement, say).  Read by the stub door (``pay_stub_service._refuse``,
    # which hands it to ``pay_stub_gross``), the entry form's line under the
    # gross box (``routes/salary/stubs._form_page``) and this profile's own
    # form; it prices nothing.  Switching it moves no saved stub, whose
    # printed gross was a check and is never stored, but it is undated: an
    # edit of an older stub is checked under today's answer, so a stub saved
    # under the other one can refuse an edit until the answer is switched
    # back (R-SAL102's per-job design).
    stub_gross_includes_after_tax = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    # How many days each of this job's pay periods ends before the payday
    # payroll SCHEDULED for it, the grid day before any holiday moved it
    # (plan#3048, ruling plan#3096).  The owner never types it: the form asks
    # one stub's Pay Date and Period End and turns them into this through the
    # calendar's match of a paid day to its planned one
    # (``salary_pay_period.days_before_payday``).  Read by the one producer of
    # a paycheck's pay period, ``pay_calendar.days_paid_for``.  No default: it
    # is a fact about the owner's employer, so every job states it.
    period_end_days_before_payday = db.Column(db.Integer, nullable=False)

    # is_active + sort_order: from IsActiveMixin / SortOrderMixin.
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    scenario = db.relationship("Scenario", lazy="joined")
    # The backref is what lets "is this template salary-linked" be answered from
    # the SESSION's state rather than from the last committed row (plan step
    # X-au-a).  ``template_amount_service.is_salary_linked_template`` reads the
    # collection, so a profile deactivated but not yet flushed already reads as
    # inactive -- the template gains its own stated amount at that instant, and
    # the amount write door opens its series in the same unit of work.  A
    # predicate issuing its own SELECT could not see that pending change.
    template = db.relationship(
        "TransactionTemplate", lazy="joined",
        backref=db.backref("salary_profiles", lazy="select"),
    )
    filing_status = db.relationship("FilingStatus", lazy="joined")
    # By start date, then by id (task plan#3128): two raises of different
    # types may start on one day, and the order they load in is the order the
    # walk compounds them in and the banner names them in
    # (``salary_raises.applications_between`` keeps input order within a
    # date and a method).  By date alone, the database answered such a pair
    # in whatever order it read them, which an edit of the first one's date
    # or type was measured to reverse; each step rounds to the cent, so the
    # reversal can move one.  The id is the order they were written in.
    raises = db.relationship(
        "SalaryRaise", back_populates="salary_profile",
        cascade="all, delete-orphan", lazy="select",
        order_by="[SalaryRaise.effective_date, SalaryRaise.id]",
    )
    # The pay list (plan step salary:X-av-3a), in payday order: the engine
    # reads it as an attribute the caller loaded, as it reads ``raises`` and
    # ``lines`` (``projection_inputs`` eager-loads all three).
    pay_entries = db.relationship(
        "SalaryPayEntry", back_populates="salary_profile",
        cascade="all, delete-orphan", lazy="select",
        order_by="SalaryPayEntry.payday",
    )
    # The profile's payroll LINES -- every deduction, and from plan step
    # salary:R18-b every earning line too (ruling R-SAL38); which side a row
    # is on is its ``paycheck_line_kind_id``.  ``deductions`` until R18-a.
    # By sort order, then by id (task plan#3148): no door sets a line's sort
    # order, so every line holds 0 and the id, the order they were added in,
    # is what orders them.  By sort order alone, the salary page's lines
    # section, the pay stub form and its report listed them, and the
    # cockpit listed lines of one kind and amount, in whatever order the
    # database read them, which renaming a line was measured to change.  The
    # order moves no money: each line is priced on its own and the results
    # summed.
    lines = db.relationship(
        "PaycheckLine", back_populates="salary_profile",
        cascade="all, delete-orphan", lazy="select",
        order_by="[PaycheckLine.sort_order, PaycheckLine.id]",
    )
    # The profile's transcribed pay stubs (plan step salary:S11-c-2c), in
    # payday order: the engine prices each paycheck's four taxes from one of
    # them (``paycheck_calculator._stubs``), reading them as an attribute the
    # caller loaded, as it reads the three collections above.  READ-ONLY,
    # because no write may reach a stub through its profile:
    # ``pay_stubs.salary_profile_id`` is RESTRICT, a trigger refuses a move,
    # and every stub write is the entry door's (``pay_stub_service``), which
    # writes ``PayStub`` rows rather than appending here.  ``PayStub``'s own
    # three collections load together (``lazy="selectin"``), so pricing
    # costs four queries per profile however many stubs it holds.
    pay_stubs = db.relationship(
        "PayStub", viewonly=True, lazy="select", order_by="PayStub.payday",
    )

    def __repr__(self):
        return f"<SalaryProfile '{self.name}'>"
