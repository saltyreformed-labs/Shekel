"""Salary, paycheck-line and pay-stub-checkpoint schemas."""


from datetime import datetime, timezone
from decimal import Decimal

from marshmallow import (
    fields,
    post_load,
    pre_load,
    validate,
    validates,
    validates_schema,
    ValidationError,
)

from app import ref_cache, tax_law
from app.enums import CalcMethodEnum
from app.services import paycheck_line_kinds, payroll_line_cadence
from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _MONETARY_INPUT_CEILING,
    _NON_NEGATIVE_MONETARY,
    _PAY_AMOUNT_RANGE,
    _PAYDAY_RANGE,
    _PERCENT_INPUT_RANGE,
    _RAISE_DATE_RANGE,
    _RAISE_YEAR_RANGE,
    _normalize_empty_inputs,
)
from app.schemas.validation._recurrence import RecurrenceFormFieldsMixin
from app.services.salary_raises import (
    RAISE_END_MODES,
    EndYearError,
    end_year_of,
)
from app.utils.dates import to_display_date

# The raise form's end-year answer is a MODE beside the year (plan step
# **salary:S3-c**, ruling **R-SAL13**); the vocabulary and the ONE rule that
# grades an answer live in :mod:`app.services.salary_raises` since plan step
# salary:S3-f-2b, where the ``/retirement`` rail's probe reaches them too.  The
# mode is consumed by :meth:`RaiseCreateSchema.drop_end_year_mode` and never
# reaches the model.  This maps the rule's two halves onto THIS form's controls.
_END_YEAR_FIELDS = {"mode": "raise_end_mode", "year": "terminal_year"}


#: ``(month, day)`` of the one date a yearly repeat cannot land on every year.
_LEAP_DAY = (2, 29)


def _require_supported_state(value: str) -> None:
    """Refuse a profile's state unless the tax law lists it.

    Plan step salary:X-at-3, ruling **salary:R-SAL78**: "the profile form
    offers only those, and a save naming another state is refused".  Until
    then the field took any two characters -- ``ZZ``, or ``nc``, which is not
    ``NC`` to the law -- and the paycheck engine priced that profile's state
    tax at ``$0.00`` (finding **SAL-575**).  The question is
    :meth:`app.tax_law.TaxLaw.supports`, asked of the law when the form is
    validated rather than when this module loads, so the law a release ships
    -- or a test installs -- is the law that decides.

    Args:
        value: The submitted state code.

    Raises:
        ValidationError: The law does not list *value*.
    """
    law = tax_law.LAW
    if not law.supports(value):
        raise ValidationError(
            f"Shekel has no tax rules for {value!r}; it supports "
            f"{tax_law.name_states(law.supported_states)}."
        )


class SalaryProfileCreateSchema(BaseSchema):
    """Validates POST data for creating a salary profile."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    # The profile's FIRST pay entry: what one paycheck pays, and the payday it
    # pays it from (plan step salary:X-av-3a, rulings R-SAL59 and R-SAL61:
    # "Pay is only ever typed per paycheck").  Whether the day is a payday, and
    # not later than the owner's next one, is the service's question
    # (``pay_list_service``, through ``salary_paydays.payday_refusal_for_door``:
    # rulings R-SAL90 and R-SAL93).
    pay_amount = fields.Decimal(
        required=True, places=2, as_string=True, validate=_PAY_AMOUNT_RANGE,
    )
    pay_payday = fields.Date(required=True, validate=_PAYDAY_RANGE)
    filing_status_id = RowId(required=True)
    state_code = fields.String(required=True, validate=_require_supported_state)
    # W-4 fields (IRS Pub 15-T)
    qualifying_children = fields.Integer(
        load_default=0, validate=validate.Range(min=0, max=99),
    )
    other_dependents = fields.Integer(
        load_default=0, validate=validate.Range(min=0, max=99),
    )
    # F-074 / C-24: Added explicit Range(>= 0) to backstop the DB
    # CHECK (``additional_income >= 0``); the column is
    # ``Numeric(12, 2)`` and the upper bound is a generous form-
    # layer ceiling (see ``_NON_NEGATIVE_MONETARY``).
    additional_income = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # F-074 / C-24: Same as additional_income.  DB CHECK
    # ``additional_deductions >= 0``.
    additional_deductions = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # F-074 / C-24: Per-period extra withholding.  DB CHECK
    # ``extra_withholding >= 0``.
    extra_withholding = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # What the job's pay stub prints as its gross (plan step salary:S11-c-2b,
    # rulings R-SAL102 and R-SAL105): "no", the column's own default, unless
    # the form says otherwise.
    stub_gross_includes_after_tax = fields.Boolean(load_default=False)
    # One stub's Pay Date and Period End (plan#3048, ruling plan#3096: "a new
    # job asks for the two dates when you set it up").  Whether the Pay Date
    # is a payday, and what gap the two state, are the calendar's questions
    # (``salary_pay_period.days_before_payday``).
    stub_pay_date = fields.Date(required=True, validate=_PAYDAY_RANGE)
    stub_period_end = fields.Date(required=True, validate=_PAYDAY_RANGE)


class SalaryProfileUpdateSchema(BaseSchema):
    """Validates POST data for updating a salary profile.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(validate=validate.Length(min=1, max=200))
    filing_status_id = RowId()
    state_code = fields.String(validate=_require_supported_state)
    # W-4 fields (IRS Pub 15-T)
    qualifying_children = fields.Integer(
        validate=validate.Range(min=0, max=99),
    )
    other_dependents = fields.Integer(
        validate=validate.Range(min=0, max=99),
    )
    # F-074 / C-24: See :class:`SalaryProfileCreateSchema` for the
    # bound rationale; the same Range applies on update.
    additional_income = fields.Decimal(
        places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    additional_deductions = fields.Decimal(
        places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    extra_withholding = fields.Decimal(
        places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # Absent leaves the stored answer alone, like every field above.  The
    # form renders it as a two-option select rather than a checkbox because
    # of exactly that: an unticked checkbox submits nothing, so "no" could
    # never be saved over a "yes" (plan step salary:S11-c-2b, R-SAL105).
    stub_gross_includes_after_tax = fields.Boolean()
    # The pay-period pair (ruling plan#3096), absent together to leave the
    # stored gap alone like every field above.  One without the other states
    # no gap, which ``salary_pay_period.days_before_payday`` refuses in words:
    # this form flashes a schema error as "correct the highlighted errors"
    # and highlights nothing.
    stub_pay_date = fields.Date(validate=_PAYDAY_RANGE)
    stub_period_end = fields.Date(validate=_PAYDAY_RANGE)

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))


class SalaryPayEntryRecordSchema(BaseSchema):
    """Validates POST data for recording a pay change on a salary's pay list.

    Plan step **salary:X-av-3b**, ruling **R-SAL61**: "'Record a pay change'
    adds one from a payday you pick" -- what one paycheck pays, and the
    payday it pays it from.  Whether the payday is a payday, is not later
    than the owner's next one, and is not one another entry already holds,
    are the service's questions
    (:func:`~app.services.pay_list_service.record_entry`).
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    amount = fields.Decimal(
        required=True, places=2, as_string=True, validate=_PAY_AMOUNT_RANGE,
    )
    payday = fields.Date(required=True, validate=_PAYDAY_RANGE)


class SalaryPayEntryFixSchema(SalaryPayEntryRecordSchema):
    """Validates POST data for fixing one entry of a salary's pay list.

    Plan step **salary:X-av-3a**, ruling **R-SAL61**: "'Fix' edits one" --
    its amount, its payday, or both: the two fields a Record states, so they
    are inherited rather than spelled again.  Whether a CHANGED payday is a
    payday, and whether another entry already holds it, are the service's
    questions (:func:`~app.services.pay_list_service.fix_entry`).
    ``version_id`` is the entry's optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    version_id = RowId(required=True, validate=validate.Range(min=1))


class SalaryPayEntryRemoveSchema(BaseSchema):
    """Validates POST data for removing one entry of a salary's pay list.

    Plan step **salary:X-av-3b**, ruling **R-SAL68** ("Remove, except the last
    one"): the entry's optimistic-locking counter alone, so a Remove rendered
    before another tab's Fix of the entry is refused rather than deleting pay
    the owner has not seen.  Whether the entry is the profile's only one is
    the service's question
    (:func:`~app.services.pay_list_service.remove_entry`).
    """

    version_id = RowId(required=True, validate=validate.Range(min=1))


class RaiseCreateSchema(BaseSchema):
    """Validates POST data for adding a salary raise."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    raise_type_id = RowId(required=True)
    # The day the raise starts (plan step salary plan#3048, ruling plan#3090):
    # the window is ``ck_salary_raises_valid_effective_date``'s, built from the
    # one home of its years (``salary_raises.RAISE_YEAR_MIN`` / ``_MAX``).
    effective_date = fields.Date(required=True, validate=_RAISE_DATE_RANGE)
    # F-011 / C-24: Tightened from Range(-100, 1000) to a positive,
    # column-fitting bound.  The user enters percent (e.g. "3" for a
    # 3% raise); the route divides by 100 before persistence into
    # ``salary.salary_raises.percentage`` (``Numeric(5, 4)`` -- max
    # storable 9.9999 == 999.99% raise).  A zero or negative raise is
    # not a raise at all (the audit's "no pay cuts" policy -- pay
    # cuts are not modelled today; revisit if that changes).  200% is
    # the realistic upper: a single-event 3x salary jump is already
    # extraordinary, and any larger entry is a typo we want rejected
    # at the form rather than silently amplified by recurring-raise
    # compounding.  The DB CHECK ``percentage > 0`` is the storage-
    # tier counterpart.
    percentage = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(
            min=Decimal("0.01"), max=Decimal("200"),
        ),
    )
    # F-011 / C-24: Tightened from Range(-1e7, 1e7) to a positive,
    # column-fitting bound on a flat-dollar raise, dollars a year (R-SAL65).
    # ``salary.salary_raises.flat_amount`` is ``Numeric(12, 2)``; the
    # DB CHECK ``flat_amount > 0`` rejects zero/negative.  The ceiling is
    # the named one the one-paycheck bound and ``_NON_NEGATIVE_MONETARY``
    # take too, ``_MONETARY_INPUT_CEILING`` ($10M, read by name since task
    # plan#3577) -- well above any realistic raise but below the column
    # limit, so an order-of-magnitude typo is rejected with a clean 400
    # rather than being committed.
    flat_amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(
            min=Decimal("0.01"), max=_MONETARY_INPUT_CEILING,
        ),
    )
    is_recurring = fields.Boolean(load_default=False)
    # The last year the raise is believed to happen (plan step
    # **salary:S3-c**).  ``allow_none`` because ``NULL`` is a real answer --
    # "believed indefinitely" -- rather than an unanswered field; the shared
    # ``Range`` mirrors the column's window so the owner gets a message on the
    # control instead of an IntegrityError.  Its LOWER bound against the
    # effective year is a cross-field rule -- see :meth:`validate_end_year`,
    # which mirrors ``ck_salary_raises_terminal_year_not_before_effective``.
    terminal_year = fields.Integer(allow_none=True, validate=_RAISE_YEAR_RANGE)
    raise_end_mode = fields.String(validate=validate.OneOf(RAISE_END_MODES))
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))

    @validates_schema
    def validate_end_year(self, data, **kwargs):
        """Require a recurring raise to STATE how long it is believed.

        The one-time half is this schema's own:
        ``ck_salary_raises_terminal_year_only_on_a_recurring_raise`` -- a
        one-time raise is a recorded fact that happens once, so an end year on
        one is storable-but-inert and the column forbids it outright.  The
        recurring half is :func:`~app.services.salary_raises.end_year_of`, the
        ONE end-year rule (ruling **R-SAL22**), which this calls against the
        payload's effective year and the ``/retirement`` rail's per-raise probe
        calls against the ROW's (plan step salary:S3-f-2b): the mode must be
        answered (an unanswered end year would silently mean *indefinitely*,
        so it is REFUSED rather than quietly believed forever), a year is
        required where the mode demands one, and a raise cannot end before it
        starts (``ck_salary_raises_terminal_year_not_before_effective``).

        **It does NOT police the mode against the year**, and an adversarial
        review of plan step salary:S3-c is why: it did, and the refusal it
        produced made the step's own headline operation impossible in a
        browser.  Editing a raise that ends 2031 fills the year box; choosing
        "no end year" left 2031 sitting in it, and the form then refused with
        a message about a control the owner had just said was irrelevant.  The
        remedy is the one CLAUDE.md rule 14 names -- delete a home rather than
        keep two in step -- so the MODE is authoritative, and
        :meth:`drop_end_year_mode` resolves through the same rule.

        Each is stated here as well as in the database so the refusal
        happens before an ``IntegrityError`` a raise route would surface as a
        generic failure; neither copy is the source of truth for the other,
        and the database's is the one that cannot be bypassed.

        **Each message is said in its own words since task plan#3125**:
        ``salary.add_raise`` and ``update_raise`` load through the shared
        loader (:func:`~app.routes._form_errors.load_form_or_refuse`), whose
        allowlist carries ``raise_end_mode`` and ``terminal_year``.  Until then
        both doors flashed "Please correct the highlighted errors and try
        again." for every refusal, on a form that highlights nothing.
        Nothing is rendered ON the control: the message is the reason box
        the Raises card is redrawn with on an htmx save (task plan#3136),
        and the flash on a plain post.

        Raises:
            ValidationError: The end year contradicts the raise's kind, is
                unanswered on a recurring raise, is missing where the mode
                demands one, or precedes the effective year.
        """
        year = data.get("terminal_year")
        if not data.get("is_recurring", False):
            if year is not None:
                raise ValidationError(
                    "A one-time raise happens once, so it cannot carry an "
                    "end year.",
                    field_name="terminal_year",
                )
            return

        # ``effective_date`` is required and this hook is skipped on field
        # errors (marshmallow's ``skip_on_field_errors`` default), so it is
        # present here.
        try:
            end_year_of(
                data.get("raise_end_mode"), year, data["effective_date"].year,
            )
        except EndYearError as exc:
            raise ValidationError(
                exc.message, field_name=_END_YEAR_FIELDS[exc.field],
            ) from exc

    @post_load
    def drop_end_year_mode(self, data, **kwargs):
        """Resolve the form-only mode away, leaving the column's own value.

        The route builds a ``SalaryRaise`` with ``**data`` and the update
        route calls ``setattr`` for each key it recognises, so a control
        that is not a column has to be consumed before either sees it.

        **The mode is AUTHORITATIVE, which is what makes the end year ONE
        value rather than two.**  "No end year" resolves to ``NULL`` whatever
        the year box happens to hold, so the two controls cannot disagree and
        no rule has to hold them in step.  They can disagree in a real
        browser, which is how an adversarial review of plan step salary:S3-c
        found it: the edit form fills the year box from the stored value, so
        choosing "no end year" on a raise that ends 2031 leaves 2031 sitting
        in it.  The earlier form of this schema REFUSED that, which made the
        headline operation of the whole step -- "this raise no longer has an
        end year" -- impossible to perform in a browser while its route test
        passed, because that test posted an empty box no rendered form emits.

        **The resolution is :func:`~app.services.salary_raises.end_year_of`'s**
        (plan step salary:S3-f-2b), the same call :meth:`validate_end_year`
        has already made and passed by the time this runs, so "none means
        NULL" is spelled once for the form, the rail's probe and the Save.

        ``terminal_year`` is forced PRESENT (as ``None``) rather than merely
        left absent, because the update path applies only the keys it is
        handed: without this, switching a raise from "ends after 2031" to "no
        end year" would submit nothing for the column and silently keep 2031.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's contract, unused.

        Returns:
            The payload with ``raise_end_mode`` removed and ``terminal_year``
            always present -- ``None`` wherever the mode said there is no end
            year, whatever the year control carried.
        """
        mode = data.pop("raise_end_mode", None)
        if data.get("is_recurring", False):
            data["terminal_year"] = end_year_of(
                mode, data.get("terminal_year"), data["effective_date"].year,
            )
        data.setdefault("terminal_year", None)
        return data

    @validates_schema
    def validate_recurring_start_day(self, data, **kwargs):
        """Refuse a RECURRING raise that starts on February 29.

        A recurring raise repeats on its start's month and day every year it
        is believed (ruling **plan#3090**), and three years in four have no
        February 29 to repeat on.  The form refuses it here, in words, before
        ``ck_salary_raises_recurring_not_on_february_29`` could.  The rule is
        the Recur box's and the date's together, so it lands under
        marshmallow's whole-form key, which the shared loader's allowlist
        carries (rulings **plan#3135** and **plan#3137**, task plan#3125): the
        raise doors flash it, while a date merely missing keeps the generic
        prompt.

        Raises:
            ValidationError: On a recurring raise dated February 29.
        """
        starts = data.get("effective_date")
        if starts is None or not data.get("is_recurring", False):
            return
        if (starts.month, starts.day) == _LEAP_DAY:
            raise ValidationError(
                "A raise that repeats every year can't start on February 29, "
                "because most years don't have one. Start it on February 28 "
                "or March 1."
            )

    @validates_schema
    def validate_one_method(self, data, **kwargs):
        """Require exactly one of ``percentage`` or ``flat_amount``.

        A raise is either a percentage bump or a flat dollar amount a year,
        never both and never neither; the two columns are mutually exclusive
        in ``salary.salary_raises``.  The refusal belongs to neither control,
        so it lands under marshmallow's whole-form key, which the shared
        loader's allowlist carries (ruling **plan#3135**), and its words name
        the two boxes by their labels on the form.

        Raises:
            ValidationError: If both fields are set, or neither is.
        """
        has_pct = data.get("percentage") is not None
        has_flat = data.get("flat_amount") is not None
        if has_pct == has_flat:
            raise ValidationError(
                "Enter the raise as either a percentage or a flat amount a "
                "year, but not both."
            )


class RaiseUpdateSchema(RaiseCreateSchema):
    """Validates POST data for updating an existing salary raise.

    Inherits all required-field and cross-field rules from
    :class:`RaiseCreateSchema` (the salary edit form submits the
    full record on every save), and adds the optimistic-locking
    ``version_id`` pin; see :class:`TransactionUpdateSchema` for the
    contract.  Commit C-18 of the 2026-04-15 security remediation
    plan.
    """

    version_id = RowId(validate=validate.Range(min=1))


class PaycheckLineCreateSchema(RecurrenceFormFieldsMixin, BaseSchema):
    """Validates POST data for adding a payroll line to a salary profile.

    **A line of any kind since plan step salary:R18-b** (ruling
    **R-SAL38**): ``paycheck_line_kind_id`` names its position in the
    paycheck's waterfall, and the one rule that differs by side --
    ``target_account_id``, the account a DEDUCTION funds -- is
    :meth:`validate_target_account_is_a_deduction`.  ``DeductionCreateSchema``
    until then.

    **The line's CADENCE arrives through the shared recurrence controls since
    plan step salary:R15-c** (ruling **R-SAL31**): ``recurrence_unit``,
    ``interval_n``, ``recurrence_placement``, ``max_per_month`` and their
    cross-field rules, exactly as the two template forms submit them,
    replacing the 26 / 24 / 12 ``select`` the column plan step R15-b dropped
    stood behind.  **And its SPAN, since plan step salary:R18-c** (ruling
    **R-SAL38** (2), amending R-SAL30 and R-SAL31): the whole
    :class:`~app.schemas.validation._recurrence.RecurrenceFormFieldsMixin`
    -- ``starts_on`` with its ``nominal_day``, and the closing bound's three
    controls composed into one :class:`~app.services.recurrence.EndBound`
    -- because a line that began mid-employment or ended (the developer's
    `$100` allowance ran 2026-03-26 to 2026-06-30) is otherwise
    unrepresentable.  Two things differ from a template's form.  A BLANK
    start is legal and means the opening payday (R-SAL30's default survives
    as the default; :attr:`recurrence_start_is_required` is off, and
    ``app.routes.salary.items`` derives the unit's zero at the opening,
    ruling **R-SAL36**, before the recurrence seam reads the payload).  An
    empty unit (the form's "Does not repeat")
    arrives as a present ``None`` -- every paycheck, ruling **R-SAL3** -- and
    an ABSENT unit is a submission that said nothing about the cadence, which
    the update route reads as "leave the stored rule alone".

    The ``amount`` field carries dual semantics keyed off
    ``calc_method_id``:

      - ``CalcMethodEnum.FLAT`` -- the user enters a per-paycheck
        dollar amount (e.g. "500.00") that is persisted as-is in
        ``salary.paycheck_lines.amount`` (``Numeric(12, 4)``).
      - ``CalcMethodEnum.PERCENTAGE`` -- the user enters a percent
        of BASE pay (e.g. "6" for 6%; ruling **R-SAL38**: the salary
        rate, never the gross a taxable earning joins); the route
        divides by 100 before persistence so the storage value is
        the decimal fraction.

    The wide field-level ``Range`` accommodates the dollar case;
    the cross-field validator ``validate_amount_against_calc_method``
    additionally rejects implausibly large percent inputs (a 500%
    deduction is a typo, not a deduction) per F-012 / C-24.
    """

    #: A blank "Starts on" is the opening payday (ruling R-SAL38 (2), keeping
    #: R-SAL30's default as the default), so a chosen cadence with no start
    #: beside it is not refused here: the route derives the start.
    recurrence_start_is_required = False

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    paycheck_line_kind_id = RowId(required=True)
    calc_method_id = RowId(required=True)
    # F-012 / C-24: Added explicit positive Range to backstop the DB
    # CHECK (``amount > 0``).  Column is ``Numeric(12, 4)``; min
    # 0.0001 is the smallest representable positive value.  $1M cap
    # is generous (a single deduction line of $1M per paycheck is
    # already nonsense) but rejects the obvious "extra digit" typo
    # at the form rather than letting an IntegrityError come back as
    # a 500.  The percent-input ceiling (calc_method = PERCENTAGE)
    # is enforced separately by
    # ``validate_amount_against_calc_method`` because the
    # field-level Range cannot see ``calc_method_id``.
    amount = fields.Decimal(
        required=True, places=4, as_string=True,
        validate=validate.Range(
            min=Decimal("0.0001"), max=Decimal("1000000"),
        ),
    )
    # F-012 / C-24: ``annual_cap`` is nullable in the model (NULL =
    # uncapped); when present, must be positive (DB CHECK
    # ``annual_cap IS NULL OR annual_cap > 0``).  Column is
    # ``Numeric(12, 2)``; $100M cap is far above the largest
    # realistic annual cap (HSA family limit ~$8K, 401(k) elective
    # ~$23K, 401(k) including employer ~$70K).
    annual_cap = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0"), min_inclusive=False,
            max=Decimal("100000000"),
        ),
    )
    inflation_enabled = fields.Boolean(load_default=False)
    # F-077 / C-24: Schema validates the user-input percent (e.g.
    # ``3`` for a 3% annual escalation); the route divides by 100
    # before persistence into ``Numeric(5, 4)``.  DB CHECK pins
    # storage to ``[0, 1]``.
    inflation_rate = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=_PERCENT_INPUT_RANGE,
    )
    inflation_effective_month = fields.Integer(
        validate=validate.Range(min=1, max=12), allow_none=True
    )
    target_account_id = RowId(allow_none=True)

    @validates_schema
    def validate_amount_against_calc_method(self, data, **kwargs):
        """Cap ``amount`` to a sane percent range when the calc method is PERCENTAGE.

        The field-level Range is wide enough to admit any plausible
        flat-dollar deduction; without this cross-field rule the
        same wide bound silently accepts a "500" entered against
        ``calc_method = PERCENTAGE`` (read as "500% of gross") and
        produces a deduction that drains every paycheck to zero.
        Cap percent inputs at 100% -- the realistic ceiling for a
        single deduction line -- and let
        ``CalcMethodEnum.FLAT`` keep the wider field-level bound.
        F-012 / C-24 of the 2026-04-15 security remediation plan.

        Raises:
            ValidationError: When ``calc_method_id`` resolves to
                ``PERCENTAGE`` and ``amount`` is greater than 100.
        """

        calc_method_id = data.get("calc_method_id")
        amount = data.get("amount")
        if calc_method_id is None or amount is None:
            return
        try:
            percentage_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
        except KeyError:
            # ref_cache not initialised -- the route layer will
            # surface the missing reference data; no extra check we
            # can do here.
            return
        if calc_method_id != percentage_id:
            return
        if amount > Decimal("100"):
            raise ValidationError(
                "Percentage lines must be at most 100%.",
                field_name="amount",
            )

    @validates_schema
    def validate_target_account_is_a_deduction(self, data, **kwargs):
        """Refuse a target account on an EARNING kind.

        A deduction may name the account it funds -- the contribution feed
        (``app.services.projection_inputs``) reads every active line's
        ``target_account_id`` as a payroll contribution INTO that account.  An
        earning is money the paycheck pays OUT to the owner and funds nothing,
        so a target on one would feed the investment projection a
        contribution nobody makes; the door refuses it here, which is the
        one place the pair is authored (plan step salary:R18-b, ruling
        **R-SAL38**).  The side is read through
        :func:`app.services.paycheck_line_kinds.is_deduction`, IDs for logic.

        Raises:
            ValidationError: When ``target_account_id`` is set and
                ``paycheck_line_kind_id`` is an earning kind.
        """
        kind_id = data.get("paycheck_line_kind_id")
        if kind_id is None or data.get("target_account_id") is None:
            return
        if not paycheck_line_kinds.is_deduction(kind_id):
            raise ValidationError(
                paycheck_line_kinds.EARNING_TARGET_REFUSAL,
                field_name="target_account_id",
            )

    @validates_schema
    def validate_cadence_picks_paychecks(self, data, **kwargs):
        """Refuse a cadence a payroll line may not repeat by (ruling plan#2591).

        A line's rule picks WHICH paychecks take it, and the salary page offers
        only the units that pick paychecks
        (:func:`~app.services.payroll_line_cadence.line_cadences`); this is
        that offer's door-side half, asked through the same
        :func:`~app.services.payroll_line_cadence.picks_paychecks`, so a
        crafted POST naming the week unit is a field error rather than a line
        the paycheck engine takes once per paycheck however many weeks it
        holds -- or a ``KeyError`` out of the default start.  The template
        forms' own refusal (``validate_authorable_cadence``) still runs beside
        it; this narrows the set, it does not replace that one.

        Skipped when no unit is named: "Does not repeat" is every paycheck
        (ruling **R-SAL3**), and an absent unit leaves the stored rule alone.

        Raises:
            ValidationError: When the submitted unit is one a payroll line
                may not repeat by.
        """
        unit = data.get("recurrence_unit")
        if unit is None or payroll_line_cadence.picks_paychecks(unit):
            return
        raise ValidationError(
            "A payroll line repeats by paychecks, months or years, not weeks. "
            "For an amount set per week, enter what each paycheck takes and "
            "repeat it every paycheck.",
            field_name="recurrence_unit",
        )


class PaycheckLineUpdateSchema(PaycheckLineCreateSchema):
    """Validates POST data for updating an existing payroll line.

    Inherits the required-field rules and the two cross-field rules from
    :class:`PaycheckLineCreateSchema` (the salary edit form submits the
    full record on every save), and adds the optimistic-locking
    ``version_id`` pin; see :class:`TransactionUpdateSchema` for the
    contract.  Commit C-18 of the 2026-04-15 security remediation
    plan.

    **The earning-kind target rule is only half answered here** (plan step
    salary:R18-b, an adversarial review of it): the cross-field rule below
    reads the POSTED pair, and this door treats an absent key as *leave the
    stored value alone* (the cadence keys' contract), so a payload that
    flips a stored deduction's kind to an earning and omits the target
    passes it.  :func:`app.routes.salary.items.update_line` judges the
    EFFECTIVE pair -- the posted kind beside the target the row will carry
    -- with the same sentence.
    """

    version_id = RowId(validate=validate.Range(min=1))


class YtdTaxCheckpointSchema(BaseSchema):
    """Validates POST data for a YTD tax checkpoint (T-P2).

    The five year-to-date figures a user reads off a real pay stub plus
    the stub's ``as_of_date``.  Each figure is a non-negative
    ``Numeric(12, 2)`` (the schema-tier counterpart to the table's
    ``ck_ytd_tax_checkpoints_nonneg_*`` CHECKs), and the cross-field rule
    below rejects any withholding line larger than gross (the
    ``*_le_gross`` CHECKs' counterpart) -- a stub where federal exceeds
    gross is a data-entry typo, caught here as a clean field-level 400
    rather than an opaque IntegrityError.  ``as_of_date`` cannot be in the
    future (a stub for a date that has not happened yet is nonsense),
    bounded against the display-timezone civil day so the check follows
    the user's wall clock, not the server's UTC day (the timezone display
    policy).
    """

    # The four withholding lines, keyed by their form field name -> the
    # error surface for the ``<= gross`` cross-check below.
    _WITHHOLDING_FIELDS = (
        "ytd_federal",
        "ytd_state",
        "ytd_social_security",
        "ytd_medicare",
    )

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    as_of_date = fields.Date(required=True)
    ytd_gross = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_federal = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_state = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_social_security = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_medicare = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))

    @validates("as_of_date")
    def validate_not_future(self, value, **kwargs):
        """Reject an ``as_of_date`` after the display-timezone today.

        A pay stub can only measure withholding through a date that has
        already happened.  The bound is the user's wall-clock day
        (``America/New_York``), not the server's UTC day, so a late-evening
        Eastern entry near midnight is not spuriously rejected as "future"
        by a UTC clock that has already rolled over.

        Raises:
            ValidationError: When *value* is after the display-tz today.
        """
        today = to_display_date(datetime.now(timezone.utc))
        if value > today:
            raise ValidationError(
                f"as_of_date {value.isoformat()} is in the future "
                f"(after {today.isoformat()})."
            )

    @validates_schema
    def validate_components_le_gross(self, data, **kwargs):
        """Reject any withholding line that exceeds YTD gross.

        A federal / state / Social Security / Medicare figure larger than
        the gross it was withheld from is impossible -- a swapped or
        extra-digit typo.  The error is attached to the offending line so
        the route's field-level handler highlights the right input; the
        DB ``*_le_gross`` CHECKs are the storage-tier backstop for a
        raw-SQL bypass.

        Raises:
            ValidationError: When a withholding line exceeds ``ytd_gross``.
        """
        gross = data.get("ytd_gross")
        if gross is None:
            return
        errors: dict[str, list[str]] = {}
        for field_name in self._WITHHOLDING_FIELDS:
            value = data.get(field_name)
            if value is not None and value > gross:
                errors[field_name] = [
                    f"{field_name} {value} exceeds ytd_gross {gross}."
                ]
        if errors:
            raise ValidationError(errors)
