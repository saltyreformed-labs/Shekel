"""
Shekel Budget App -- Salary Raise Model (salary schema)

Tracks scheduled salary raises (merit, COLA, custom) that start on a
stated date to adjust the pay for paycheck calculation.
"""

from app import ref_cache
from app.extensions import db
from app.models.mixins import (
    CreatedAtMixin,
    OptimisticLockMixin,
    SalaryProfileScopedMixin,
)


class SalaryRaise(SalaryProfileScopedMixin, OptimisticLockMixin, CreatedAtMixin, db.Model):
    """A scheduled salary raise event.

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent raise edits race for
    the bump; the loser raises ``StaleDataError`` and the route
    surfaces a flash + redirect.  See commit C-18 of the 2026-04-15
    security remediation plan.

    **A raise starts on a DATE since plan step salary plan#3048** (rulings
    **plan#3090** and **plan#3094**, migration ``6740ce13e174``).  It stated
    a month and a year until then, which the engine landed on the month's
    1st; the developer's employer pays each weekday at the pay in force that
    day (ruling **plan#3089**), so the day moves a straddling paycheck's
    money, and the pair could not state it.  A RECURRING raise repeats on its
    own month and day every year it is believed, which is why it may not
    start on February 29 (``ck_salary_raises_recurring_not_on_february_29``):
    three years in four have no such day to repeat on.

    Duplicate prevention (F-051 / C-23): the composite unique
    constraint ``uq_salary_raises_profile_type_date`` on
    ``(salary_profile_id, raise_type_id, effective_date)`` rejects a
    second raise with the same shape on the same salary profile.
    Without it a double-submit of the raise form -- network retry,
    double-click, browser back-and-resubmit -- creates two rows with
    identical start dates; the paycheck calculator then applies the
    raise twice (``salary * 1.03 * 1.03`` instead of ``salary *
    1.03``), silently overstating projected gross pay until the user
    notices the drift.  It keyed on the month until the date replaced
    the pair, and a double submit posts the same date, so the narrower
    key guards the same failure; two raises of one type on two days of
    one month are two stated facts under it.  ``is_recurring`` is
    intentionally NOT part of the key: a recurring raise on (profile,
    type, date) already covers that exact day, so adding a one-time
    raise with the same key compounds the recurring effect on the
    targeted year and is the same class of double-application bug
    F-051 documents.
    """

    __tablename__ = "salary_raises"
    __table_args__ = (
        db.CheckConstraint(
            "(percentage IS NOT NULL AND flat_amount IS NULL) OR "
            "(percentage IS NULL AND flat_amount IS NOT NULL)",
            name="ck_salary_raises_one_method",
        ),
        # The 2000-2100 window the create/update schema's date ``Range``
        # enforces (``RAISE_YEAR_MIN`` / ``RAISE_YEAR_MAX``), as a window of
        # days: the year CHECK's (F-077 / C-24 / DH-#57) carried onto the
        # date that replaced the year (plan step plan#3048).
        db.CheckConstraint(
            "effective_date >= DATE '2000-01-01' AND effective_date <= DATE '2100-12-31'",
            name="ck_salary_raises_valid_effective_date",
        ),
        # salary:S3-b / R-SAL11: a raise cannot end before it starts.  The
        # rule the column exists to make unbreakable -- under the global
        # horizon it replaces, a raise dated past the cutoff was storable
        # and silently never applied (measured 2026-09-05: a one-time
        # $8,000 promotion recorded for 2035 under a 2031 cutoff left the
        # $91,675.00 base untouched at 2040).  Against the start DATE's year
        # since plan step plan#3048.
        db.CheckConstraint(
            "terminal_year IS NULL OR "
            "terminal_year >= EXTRACT(YEAR FROM effective_date)",
            name="ck_salary_raises_terminal_year_not_before_effective",
        ),
        # A recurring raise repeats on its own month and day each year it is
        # believed (ruling plan#3090), so a February 29 start has no day to
        # repeat on in three years of four.  The schema refuses it first, in
        # words (``RaiseCreateSchema.validate_recurring_start_day``).
        db.CheckConstraint(
            "NOT (is_recurring AND EXTRACT(MONTH FROM effective_date) = 2 "
            "AND EXTRACT(DAY FROM effective_date) = 29)",
            name="ck_salary_raises_recurring_not_on_february_29",
        ),
        # The same 2000-2100 window ``ck_salary_raises_valid_effective_date``
        # holds the start to.  The lower bound is DERIVED -- it follows
        # from the ordering CHECK above plus that sibling -- so dropping the
        # sibling silently unbounds this column below, which is a dependency
        # worth naming since nothing else states it.
        db.CheckConstraint(
            "terminal_year IS NULL OR terminal_year <= 2100",
            name="ck_salary_raises_valid_terminal_year",
        ),
        # An end year on a ONE-TIME raise is inert: ``_applications`` gates a
        # one-time raise on its start year being ``<= terminal_year``, which the ordering
        # CHECK already guarantees, so no value can move a figure.  The end
        # year asks how long a FORECAST is believed; a one-time raise is a
        # recorded fact that happens once.  Developer ruling 2026-09-05,
        # made against both adversarial reviews of salary:S3-b -- which each
        # found the state and each declined to add the constraint on their
        # own authority.
        db.CheckConstraint(
            "terminal_year IS NULL OR is_recurring",
            name="ck_salary_raises_terminal_year_only_on_a_recurring_raise",
        ),
        db.CheckConstraint(
            "percentage IS NULL OR percentage > 0",
            name="ck_salary_raises_positive_pct",
        ),
        db.CheckConstraint(
            "flat_amount IS NULL OR flat_amount > 0",
            name="ck_salary_raises_positive_flat",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_salary_raises_version_id_positive",
        ),
        db.UniqueConstraint(
            "salary_profile_id", "raise_type_id", "effective_date",
            name="uq_salary_raises_profile_type_date",
        ),
        # F-071 / F-079 / C-42: child-FK index restored after the
        # 22b3dd9d9ed3 migration dropped it without restoration.  The
        # paycheck calculator joins salary_raises to its parent
        # salary_profile on every projection; without this index the
        # join is a sequential scan that scales linearly with the
        # total raise-row count across all users.
        db.Index(
            "idx_salary_raises_profile", "salary_profile_id",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # F-073 / C-43: explicit ondelete=RESTRICT + fk_* name.  See
    # app/extensions.py for the full SHEKEL_NAMING_CONVENTION
    # rationale and the close-out story for finding F-078.
    raise_type_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.raise_types.id",
            name="fk_salary_raises_raise_type_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    #: The day the raise starts: its first application, and for a recurring
    #: raise the month and day it repeats on each later year it is believed
    #: (rulings plan#3090 and plan#3094).
    effective_date = db.Column(db.Date, nullable=False)
    percentage = db.Column(db.Numeric(5, 4))
    flat_amount = db.Column(db.Numeric(12, 2))
    is_recurring = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    #: The LAST year this raise is believed to happen, or ``NULL`` for
    #: indefinitely (plan steps **salary:S3-b** and **S3-c**, ruling
    #: **R-SAL11**).  A recorded raise is a fact; a raise marked
    #: ``is_recurring`` is partly a forecast, and a forecast decays -- this
    #: is where that decay is stated, per raise.  It replaced one global
    #: ``auth.user_settings.merit_raise_horizon_years`` applied by raise TYPE
    #: on ``/retirement`` alone, which plan step salary:S3-c deleted after
    #: carrying each owner's value onto these rows.
    #:
    #: **Nullable because "indefinitely" is a real belief and the common
    #: one**: a COLA does not stop, because inflation does not stop at a
    #: planning horizon.  ``NULL`` is not "unanswered"; it is the answer that
    #: says the raise carries no end.  The FORM therefore cannot leave it
    #: unanswered either -- a recurring raise must pick "no end year" or a
    #: year (:class:`~app.schemas.validation.salary.RaiseCreateSchema`), on
    #: the developer's 2026-09-05 ruling that a default here would be one
    #: global belief in per-raise clothing.
    #:
    #: **It is THE horizon, for every engine.**
    #: :func:`app.services.salary_raises.applications_between` reads it on
    #: the pay list's walk, which both the paycheck pipeline and the pension's
    #: salary path (:func:`app.services.pension_calculator
    #: .project_profile_salaries`) read since plan step salary:X-av-3a
    #: (through :meth:`app.services.payroll_basis.PayrollBasis.pay_rate_on`
    #: since task plan#3100), and
    #: since salary:S3-c neither invents a cutoff of its own -- so the two
    #: long-horizon salary paths that used to disagree past a global cutoff
    #: are one path.
    terminal_year = db.Column(db.Integer)
    notes = db.Column(db.Text)
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="raises")
    raise_type = db.relationship("RaiseType", lazy="joined")

    @property
    def raise_type_name(self) -> str:
        """The display name of this raise's type (``"merit"``, ``"cola"``, ...).

        The one attribute the paycheck engine reads of a raise's type, so a
        row and a :class:`~app.services.salary_raises.RaiseTerms` value expose
        it under one name (plan step salary:S3-f-1).  For DISPLAY only, as
        every ``ref`` name is; logic compares ``raise_type_id``.

        **Resolved from ``raise_type_id`` through the ref cache, not from the
        ``raise_type`` relationship**, and an adversarial review of that step
        is why.  The relationship is joined and the column is ``NOT NULL``,
        which makes it total for every PERSISTED row -- but SQLAlchemy does
        not lazy-load a relationship on a pending instance, so a row built
        with only its FK set and never flushed reads ``raise_type`` as
        ``None``.  The X-bl invariance control
        (``tests/manual/verify_amount_resolver.py``) prices exactly such a
        row, by design never flushed, and the cache answers it from the FK.
        The relationship is read only for a row whose type the enum does not
        name -- a hand-inserted ``ref.raise_types`` row, which the schema
        permits and nothing in the application writes -- and that row's own
        ``name`` is the honest answer for it.

        Returns:
            The type's name.
        """
        member = ref_cache.raise_type_member(self.raise_type_id)
        if member is not None:
            return member.value
        return self.raise_type.name

    def __repr__(self):
        amt = f"{self.percentage}%" if self.percentage else f"${self.flat_amount}"
        return f"<SalaryRaise {amt} effective {self.effective_date}>"
