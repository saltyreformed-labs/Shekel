"""
Shekel Budget App -- The Retirement Picture At A Plan Point (C2-f2d-2)

**ONE producer of "what does this retirement plan look like", and the ONE
loader a ``/retirement`` render runs on.**

Until plan step C2-f2d-2 there were two implementations of that one thing.
``retirement_dashboard_service.compute_gap_data`` computed the picture at the
STORED retirement date under optional what-if sliders; ``retirement_levers._probe``
computed it at a SHIFTED retirement date under the stored sliders.  They are the
same function over a union of parameters, written twice -- and on the default page
load the lever solver's month-0 probe recomputed, from its own loaded inputs, the
exact picture the readiness hero had already drawn beside it.  Measured on a
production clone: funded ratio ``0.7463``, required ``$1,120,707.00``, projected
after tax ``$836,398.65``, derived twice and agreeing.  Ledger row **P57**.

Agreeing is not the same as being one answer.  Two derivations agree until
somebody edits one of them, and nothing in the app can notice the day they part
-- the page would simply show a funded verdict and a lever card solved against a
different plan, both stated as fact.  This module replaces the second derivation
rather than reconciling it.

**The two values that make it one producer.**

* :class:`PlanPoint` -- WHICH plan, RESOLVED: the withdrawal rate this plan is
  solved at, whether it came from the settings or from a what-if slider, the
  assumed return, the retire-later lever's month offset, and -- since plan step
  salary:S3-f-2b -- the end year each recurring raise is BELIEVED through,
  whether it came from the raise's row or from the rail's probe.  It is frozen
  and hashable because it is the memo key, and resolved because a memo key must
  be CANONICAL -- two spellings of one plan are the two derivations this module
  exists to remove.  Built through :attr:`RetirementInputs.stored_plan` and
  :meth:`RetirementInputs.plan_with`.
* :class:`RetirementInputs` -- everything a render loads, loaded ONCE, and
  point-INDEPENDENT by construction: the read pass, the gap inputs, the stored
  retirement date, the projection context and its batch.  A point never reloads
  any of it, which is why the retire-later binary search can probe ten candidate
  dates for the price of one query set.

:func:`picture_at` is the join: it derives the picture at a point and memoizes it
on the inputs, so the readiness hero and the lever card asking for the same point
receive the same :class:`RetirementPicture` object rather than two equal ones.

All functions take plain data and return plain data.  No Flask imports.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCadence, PeriodWindow
from app.services.projection_inputs import price_payroll_feeds
from app.services.retirement_dashboard_service import (
    BelievedPayroll,
    GapInputs,
    PensionSummary,
    compute_current_paycheck,
    compute_gap_net_biweekly,
    compute_pension_summary,
    load_gap_inputs,
    recurring_raises,
    resolve_estimated_tax_rate,
    resolve_planned_retirement_date,
    resolve_retirement_date_provenance,
    resolve_swr_fraction,
)
from app.services.retirement_gap_calculator import (
    RetirementGapAnalysis,
    calculate_gap,
    funded_ratio_state,
)
from app.services.retirement_projection import (
    ProjectionBatch,
    RetirementProjectionContext,
    build_projection_context,
    load_projection_batch,
    project_accounts_with_batch,
    resolve_projection_axis,
)
from app.services.salary_raises import EndYearError, RaiseTerms, end_year_of
from app.utils.dates import add_months

#: Percentage scaler, PUBLIC because the display boundary needs it: the blended
#: return is carried as the fraction the growth math takes and the assumptions
#: rail renders a percent, so the route scales it here rather than keeping a
#: fourth private copy of ``Decimal("100")``.  ``assumed_annual_return`` is
#: stored as a fraction; the blended average is formed in PERCENT and quantized
#: THERE before being handed back, which is where the two-decimal rounding
#: happens -- see :meth:`RetirementPicture.blended_return` for why that order is
#: load-bearing rather than cosmetic.
PCT_SCALE = Decimal("100")

# Two-decimal quantum for the blended percentage, matching the assumptions
# rail's ``"%.2f"|format`` so the rate the contribution solver divides by is the
# rate the page displays, to the digit.
_PCT_QUANTUM = Decimal("0.01")

# Default assumed-annual-return percentage when no account carries weight to
# blend from.  7% matches the S&P 500's long-run inflation-adjusted total return
# (Damodaran historical-returns dataset, ~1928-2024) and is the conservative
# midpoint of common retirement-planning assumptions (5-10%).
_DEFAULT_RETURN_PCT = Decimal("7.00")

# Fork F1's explicit rate: an UNSET estimated retirement tax rate is treated as
# an explicit zero with a flag the assumptions panel surfaces, never as a
# truthiness fallback and never as a reason to skip the after-tax block.
_UNSET_TAX_RATE = Decimal("0")

# Funded means the quantized funded ratio reaches at least this value.
_FULLY_FUNDED = Decimal("1")


class RaiseProbeError(ValueError):
    """A rail probe named a raise this owner has no recurring row for, or broke
    the end-year rule against the row it named.

    Raised by :meth:`RetirementInputs.plan_with` (plan step salary:S3-f-2b) so
    the readiness route can answer a designed 422 rather than resolve a stale
    bookmark or a URL edit into a silently unchanged plan.  Every probe that
    failed is reported, not just the first, the way a schema reports every
    field.  **Each refusal names WHICH HALF of the pair it is about** (plan
    step salary:S3-f-4, ruling **R-SAL33**): the route renders it on that
    half's control, exactly as the rail's Save renders the same rule's
    refusal, and a refusal of the pair as a whole -- the row is not one of
    this owner's -- names no half.

    Attributes:
        errors: ``{raise_id: (field, message)}`` -- one entry per refused
            probe; *field* is :class:`~app.services.salary_raises
            .EndYearError`'s vocabulary (``"mode"`` or ``"year"``) for a rule
            refusal and ``None`` for a raise the owner has no row for.
    """

    def __init__(self, errors: "dict[int, tuple[str | None, str]]") -> None:
        """Record every refused probe.

        Args:
            errors: ``{raise_id: (field, message)}``.
        """
        super().__init__(
            "; ".join(
                f"raise {rid}: {message}" for rid, (_, message) in errors.items()
            ),
        )
        self.errors = errors


#: What a rail probe names a raise's end year as: :func:`~app.services
#: .salary_raises.end_year_of`'s ``(mode, year)`` pair, keyed by raise id.  The
#: readiness schema gathers it off the wire; :meth:`RetirementInputs.plan_with`
#: resolves it against the rows.
RaiseProbes = dict[int, tuple[str, int | None]]


@dataclass(frozen=True)
class PlanPoint:
    """WHICH retirement plan a picture is of -- RESOLVED, never a delta.

    Frozen and hashable because it is :func:`picture_at`'s memo key, and the
    memo is what makes the readiness hero and the lever card's baseline ONE
    object instead of two equal ones.  **A memo key must therefore be CANONICAL:
    two spellings of one plan are two derivations of one figure, which is the
    defect (row **P57**) this module exists to remove.**

    So the assumption a what-if can move is stored here as the VALUE it
    resolves to, not as the override it arrived as.  The first draft stored
    overrides, and it was measured wrong the same day (adversarial design
    review, 2026-08-16): every saveable row in the assumptions rail renders
    pre-filled with its stored value, so every real fragment request ships that
    value back as an "override".  ``PlanPoint(swr_override=Decimal("0.04"))``
    and ``PlanPoint()`` are then two keys for one plan, and the what-if panel
    derives its baseline and its "override" as two full projections agreeing in
    every digit -- P57's own sentence, restated inside the step that closed it.
    Resolved values cannot express that: 4% is 4% whichever door it came
    through.  *It was measured on the merit-horizon row, which plan step
    salary:S3-c deleted (ruling **R-SAL11**: a raise's end year is a fact on
    the raise); the rail's remaining saveable rows are pre-filled the same way,
    so the rule outlived the row it was found on.*

    Built through :attr:`RetirementInputs.stored_plan` and
    :meth:`RetirementInputs.plan_with`, because resolving an assumption needs
    the owner's settings and this record deliberately does not carry them.

    Attributes:
        month_offset: Whole months added to EACH qualifying pension's own
            planned retirement date -- the retire-later lever's search axis.
            It stays a delta where the other two became values, and the reason
            is in :func:`app.services.retirement_dashboard_service.compute_pension_summary`:
            the offset shifts every pension SEPARATELY, growing each one's years
            of service and high-salary window from its own date.  An owner with
            pensions dated 2044 and 2046 delayed by a year retires against 2045
            AND 2047, which one resolved date cannot say.  ``0`` is the stored
            plan (:func:`app.utils.dates.add_months` with 0 months is the
            identity).
        swr: The fractional safe-withdrawal rate this plan is solved at, already
            resolved from the what-if or the stored settings.
        return_rate_override: A fractional annual return applied UNIFORMLY to
            every account, or ``None`` -- and this one is genuinely an OVERRIDE
            rather than a resolved value, because ``None`` does not stand for a
            stored number.  It means "each account grows at its own stored
            rate", which no single rate expresses.  Its input renders empty for
            the same reason (see ``dashboard.html``'s assumed-return row).
        raise_end_years: One ``(raise_id, terminal_year)`` per RECURRING raise
            on every ACTIVE profile -- the set the assumptions rail lists
            (:func:`~app.services.retirement_dashboard_service.recurring_raises`)
            -- sorted by raise id, each year RESOLVED: the row's own unless the
            rail probed it, ``None`` for *no end year* (plan step
            salary:S3-f-2b, rulings **R-SAL20** and **R-SAL21**).  The same
            canonical-value discipline as ``swr``: a probe carrying the stored
            year unchanged -- which every pre-filled rail row submits on every
            refresh -- IS the stored plan, so the memo cannot hold two keys
            for one belief.  Read through :meth:`terms_for`, never indexed by
            a consumer.
    """

    month_offset: int
    swr: Decimal
    return_rate_override: Decimal | None
    raise_end_years: tuple[tuple[int, int | None], ...]

    def terms_for(self, profile) -> tuple[RaiseTerms, ...]:
        """The raise set this point believes *profile* under.

        Every row's terms, with each recurring raise's end year read off this
        point where the point names the row.  A row the point does not name
        -- a one-time raise, which carries no end year, or a raise on a
        profile the rail does not list (an archived profile a pension still
        points at) -- keeps its own stored year, so the answer is total over
        any profile a reader hands it.

        **This is the one producer of a believed set**, and every salary-path
        reader on the picture takes one: the pension summary, the income
        target, the current paycheck and the payroll feeds
        (:func:`_derive_picture`).  At the stored point it equals
        :func:`~app.services.salary_raises.terms_of` over the rows by VALUE,
        which is what makes
        :meth:`~app.services.income_service.PaycheckPricing.for_profile`
        answer the pricer the rows already built rather than a second one.

        Args:
            profile: A :class:`~app.models.salary_profile.SalaryProfile`,
                read for its ``raises``.

        Returns:
            The tuple of :class:`~app.services.salary_raises.RaiseTerms`, in
            the rows' order.
        """
        believed = dict(self.raise_end_years)
        return tuple(
            replace(
                RaiseTerms.of(row),
                terminal_year=believed.get(row.id, row.terminal_year),
            )
            for row in profile.raises
        )


@dataclass(frozen=True)
class RetirementInputs:
    """Everything one ``/retirement`` render loads, loaded exactly once.

    **Nothing here is re-read per plan**, which is what makes one load serve
    every candidate: the retire-later search probes up to ten dates and none of
    them queries.  What DOES vary with the point -- the salary path, the
    pension benefit, the projection axis and the per-account walk (the employer
    salary basis too, until salary:S3-e-2) -- is derived in :func:`picture_at`
    from these.  So is the current paycheck since plan step salary:S3-f-2a,
    derived per point off the pass's pricer, and so are the payroll feeds
    since salary:S3-f-2b; both vary with the point only through its raise
    set.  For a profile that funds no account, the FIRST derivation at a set
    is where that profile's pricer is built, with no query since plan step
    salary:X-at-1 moved the tax law into the code (three tax-series queries
    until then, which the loader issued itself before salary:S3-f-2a), and
    every later point at that set is the memo's.

    **The precise invariant, because "point-independent" is not quite true of
    ``base_ctx`` and an earlier draft of this paragraph claimed it was**
    (adversarial reviews, 2026-08-16): that context carries a
    ``planned_retirement_date`` and a ``return_rate_override``, both of which
    :func:`_derive_picture` replaces per point.  Sharing one ``batch`` across
    plans is therefore safe because of a property of the LOADER, not of the
    context: :func:`~app.services.retirement_projection.load_projection_batch`
    reads only ``balance_ctx`` and ``accounts`` -- never the horizon, the
    return override or the employer basis.  (It read two PERIOD fields as well
    until pay-calendar plan step C2-f2d-3 deleted them from that context; both
    are derived from ``balance_ctx`` now, which this list already named, so the
    property is unchanged and the surface it rests on is smaller.)
    Teaching it to read one of those would silently hand every
    point in a render the stored plan's batch, so the property is pinned by
    ``tests/test_services/test_retirement_plan.py``'s
    ``TestTheBatchIsHorizonIndependent`` rather than left to this note.

    Built by :func:`load_retirement_inputs`, which a ROUTE calls; nothing below
    a route builds one, for the same reason nothing below a route builds a read
    pass (plan step C2-f2d-1, ledger row **P43**).

    Attributes:
        balance_ctx: The render's
            :class:`~app.services.balance_at.BalanceContext` -- the owner, the
            baseline scenario, the pinned clock, and the memos that resolve each
            loan and derive the pay calendar once for the whole render.
        gap: The :class:`~app.services.retirement_dashboard_service.GapInputs`
            bundle: settings, active pensions, active salary profiles and the
            owner's pay cadence.  (The current paycheck left it at plan step
            salary:S3-f-2a: it is a function of the point, derived below.)
        base_date: The STORED plan's resolved retirement date (a pension's beats
            the settings', latest pension wins), or ``None`` when neither
            supplies one -- the page's no-horizon state.
        base_ctx: The account set and period calendar the projection runs over,
            as a context.  Its three point-dependent fields are placeholders --
            :func:`_derive_picture` replaces all three for every point,
            including the stored one -- so the employer salary basis is left
            ``None`` here rather than built and thrown away, which is what an
            earlier draft did once per render for nothing.
        batch: The date-independent projection batch (payroll wiring and its
            stored-set feeds, contributions, params, balances) loaded once from
            *base_ctx*.  Shared across every point, which is what makes its
            seed memo a hit rather than a second fold -- the single largest
            cost this step removes.  Its FEEDS are the one part a point
            re-derives (:func:`_believed_batch`, plan step salary:S3-f-2b):
            priced from the same wiring under the point's raise set, on a copy
            that shares this batch's memo.
        picture_memo: ``{PlanPoint: RetirementPicture}``.  Not a field a caller
            reads: it is :func:`picture_at`'s store, held here because the
            memo's LIFETIME is the render's.  Excluded from ``repr`` and from
            equality for the reason ``ProjectionBatch.seed_memo`` is: a cache is
            not part of the value's identity.
    """

    balance_ctx: BalanceContext
    gap: GapInputs
    base_date: date | None
    base_ctx: RetirementProjectionContext
    batch: ProjectionBatch
    picture_memo: "dict[PlanPoint, RetirementPicture]" = field(
        default_factory=dict, repr=False, compare=False,
    )

    @property
    def stored_plan(self) -> PlanPoint:
        """The plan as STORED: no delay, every assumption from the settings.

        The ``/retirement`` page's own point, and the baseline every what-if is
        stated against.  It is a property of the INPUTS rather than a module
        constant because a resolved plan belongs to an owner: 4% is this
        owner's stored withdrawal rate, not a universal one.

        Returns:
            The stored :class:`PlanPoint`.
        """
        return self.plan_with()

    def plan_with(
        self, *, swr_override=None, return_rate_override=None,
        raise_probes: RaiseProbes | None = None,
    ) -> PlanPoint:
        """Resolve a what-if against this owner's stored settings and rows.

        **The canonicalising door.**  An override that equals the stored value
        resolves to the same :class:`PlanPoint` as no override at all, so the
        memo cannot hold two keys for one plan -- which matters because a
        saveable rail input is pre-filled with the stored value and therefore
        submits it on every single fragment request.  The rail's per-raise
        probe (plan step salary:S3-f-2b) is resolved HERE too, because
        grading an end-year answer needs the raise's ROW -- its effective
        year, and whether this owner has such a recurring raise at all -- and
        the rows are what these inputs hold.  One door for every probe means
        one place that refuses a stale bookmark, rather than a resolver that
        refuses it and a point-builder that would have to refuse it again.

        Args:
            swr_override: A fractional safe-withdrawal rate, or ``None`` for the
                stored one.
            return_rate_override: A uniform fractional annual return, or
                ``None`` to leave each account on its own stored rate.
            raise_probes: ``{raise_id: (mode, year)}`` -- each named raise's
                end-year answer in :func:`~app.services.salary_raises
                .end_year_of`'s vocabulary (ruling **R-SAL13**: the mode is
                authoritative), or ``None`` for every raise's stored year.  A
                probe equal to the stored year is the stored plan.

        Returns:
            The resolved :class:`PlanPoint`, at no delay.

        Raises:
            RaiseProbeError: A probe names a raise that is not one of this
                owner's recurring raises on an active profile (a stale
                bookmark, or a URL edit), or its answer breaks the ONE
                end-year rule against that raise's row.  Every failing probe is
                reported.
        """
        return PlanPoint(
            month_offset=0,
            swr=(
                swr_override if swr_override is not None
                else resolve_swr_fraction(self.gap.settings)
            ),
            return_rate_override=return_rate_override,
            raise_end_years=self._believed_end_years(raise_probes),
        )

    def _believed_end_years(
        self, raise_probes: RaiseProbes | None,
    ) -> tuple[tuple[int, int | None], ...]:
        """Resolve *raise_probes* against the rows into the point's field.

        The rows are the membership: a probe on an id
        :func:`~app.services.retirement_dashboard_service.recurring_raises`
        does not list is refused by name, and an answer on a row it does list
        goes through :func:`~app.services.salary_raises.end_year_of` against
        THAT row's effective year -- the same rule the salary form applies to
        its payload, so the rail cannot accept a year the form would refuse.

        Args:
            raise_probes: See :meth:`plan_with`.

        Returns:
            The resolved ``(raise_id, terminal_year)`` tuple, sorted by id.

        Raises:
            RaiseProbeError: See :meth:`plan_with`.
        """
        rows = recurring_raises(self.gap.salary_profiles)
        by_id = {row.id: row for row in rows}
        believed: dict[int, int | None] = {}
        errors: dict[int, tuple[str | None, str]] = {}
        for raise_id, (mode, year) in (raise_probes or {}).items():
            row = by_id.get(raise_id)
            if row is None:
                errors[raise_id] = (
                    None, "Not one of your recurring raises; reload the page.",
                )
                continue
            try:
                believed[raise_id] = end_year_of(mode, year, row.effective_date.year)
            except EndYearError as exc:
                errors[raise_id] = (exc.field, exc.message)
        if errors:
            raise RaiseProbeError(errors)
        return tuple(sorted(
            ((row.id, believed.get(row.id, row.terminal_year)) for row in rows),
            key=lambda pair: pair[0],
        ))

    @property
    def date_provenance(self) -> dict:
        """WHO owns the resolved retirement date, with the facts to say so.

        On the inputs because it is point-independent and because the
        alternative was a three-hop reach-through from the readiness shaper
        (``picture.inputs.gap.pensions``, ``picture.inputs.gap.settings``) --
        which is what kept that module importing the resolver's own.

        Returns:
            ``date`` / ``source`` / ``pension_id`` / ``pension_name`` -- see
            :func:`~app.services.retirement_dashboard_service.resolve_retirement_date_provenance`.
        """
        return resolve_retirement_date_provenance(
            self.gap.pensions, self.gap.settings,
        )

    @property
    def stored_tax_rate(self) -> Decimal | None:
        """The estimated retirement tax rate as the user STORED it.

        On the INPUTS rather than on a picture because it is point-independent:
        no :class:`PlanPoint` varies it, so every picture derived from these
        inputs is computed at the same rate and a per-picture copy would be one
        fact under N keys.  The ``no_horizon`` lever state reads it without
        deriving a picture at all, which is the case that proves the placement.

        Returns:
            The stored fractional rate -- an explicit zero preserved (E-12) --
            or ``None`` when settings are absent or the column is NULL, which
            is what drives :attr:`tax_rate_missing`.
        """
        return resolve_estimated_tax_rate(self.gap.settings)

    @property
    def tax_rate_missing(self) -> bool:
        """Whether the owner has never stated an estimated retirement tax rate.

        Returns:
            True when the rate is unset (fork F1's flag, which the assumptions
            panel surfaces as "not set -- 0% assumed").  A stored ``0%`` is a
            real answer and returns False.
        """
        return self.stored_tax_rate is None

    @property
    def effective_tax_rate(self) -> Decimal:
        """The rate every after-tax figure here is actually computed at.

        Returns:
            The stored rate, or fork F1's explicit ``Decimal("0")`` when unset
            -- which is what keeps the after-tax block always populated instead
            of the analysis silently dropping its own frame.
        """
        stored = self.stored_tax_rate
        return _UNSET_TAX_RATE if stored is None else stored


def load_retirement_inputs(balance_ctx: BalanceContext) -> RetirementInputs:
    """Load a ``/retirement`` render's point-independent inputs, once.

    **A ROUTE calls this and hands the result down.**  Before plan step
    C2-f2d-2 the verdict producer and the lever solver each ran this whole load
    for themselves: measured on a production clone, one page render issued 179
    queries of which 86 -- 48% -- were the second copy.

    Args:
        balance_ctx: The render's
            :class:`~app.services.balance_at.BalanceContext`, pinned once by the
            route.  Its owner scopes every query below and its ``as_of`` is the
            one clock every picture derived from these inputs is measured at.

    Returns:
        The :class:`RetirementInputs` bundle, with an empty picture memo.

    Raises:
        PayCalendarError: The owner has no resolvable pay cadence -- no
            ``budget.pay_schedule`` row, which since plan step C4-b-2 IMPLIES no
            pay periods (``fk_pay_periods_schedule``).  Since plan step
            pay_calendar:C4-d it is raised where the calendar is BUILT rather
            than where the cadence is read.
            See :func:`app.services.retirement_dashboard_service.load_gap_inputs`.
    """
    gap = load_gap_inputs(balance_ctx)
    base_date = resolve_planned_retirement_date(gap.pensions, gap.settings)
    base_ctx = build_projection_context(
        balance_ctx,
        # The two point-dependent fields are placeholders: every picture
        # replaces the horizon and the return override.  (A third, the
        # employer salary basis, left at plan step salary:S3-e-2.)
        base_date,
        None,
    )
    return RetirementInputs(
        balance_ctx=balance_ctx,
        gap=gap,
        base_date=base_date,
        base_ctx=base_ctx,
        batch=load_projection_batch(base_ctx),
    )


@dataclass(frozen=True)
class RetirementPicture:
    """The whole retirement picture at ONE plan point.

    What the readiness hero renders, what the chart plots, and what each
    retire-later probe compares -- one record, produced in one place
    (:func:`picture_at`).

    **Everything derivable is a PROPERTY rather than a field.**  The funded
    ratio, the tax-rate facts, the safe-withdrawal rate and the blended return
    are all functions of what is stored here, and storing them beside their own
    inputs is the denormalization this arc exists to remove: a stored copy is a
    thing that can disagree with what it was copied from.

    Attributes:
        inputs: The :class:`RetirementInputs` this picture was derived FROM.
            A picture is meaningless without them -- the same figures under a
            different owner or a different clock are a different answer -- so
            this is provenance, not a convenience bag.  It is not the shape
            :func:`app.services.retirement_levers._contribution_outcome`
            refuses: that is a LEAF taking two numbers, and handing it a whole
            picture would make it depend on a record it does not read.  A
            consumer that wants a point-independent fact should read it off a
            NAMED property of the inputs (:attr:`RetirementInputs.date_provenance`,
            :attr:`RetirementInputs.tax_rate_missing`) rather than walking
            ``picture.inputs.gap.<field>``, which is the reach-through an
            adversarial review caught here on 2026-08-16.
        point: The :class:`PlanPoint` this is the picture OF.
        retirement_date: The horizon this picture was projected to -- the stored
            date shifted by ``point.month_offset`` -- or ``None`` when the owner
            has set no retirement date at all.
        axis: The :class:`~app.services.pay_calendar.PeriodWindow` every
            projection below ran over: the owner's own paychecks from the read
            pass's clock to :attr:`retirement_date`.  Carried rather than
            rebuilt (plan step C2-e): its LENGTH is the countdown's
            "paychecks remaining" and the contribution lever's annuity factor
            folds over exactly it, so a rebuild that came back a different
            length would solve for a contribution that does not close the gap.
        projections: One dict per retirement / investment account (see
            :func:`app.services.retirement_projection._project_one_account`).
        pension: The :class:`~app.services.retirement_dashboard_service.PensionSummary`
            at this point -- the summed monthly benefit, the salary-by-year
            series behind it, and the per-pension derivation entries the page
            footer states one line each from.
        net: The NET-frame :class:`~app.services.retirement_gap_calculator.RetirementGapAnalysis`
            (Gate A ruling 2): every figure after the estimated retirement tax,
            so the verdict compares like with like.  Computed at the EXPLICIT
            (possibly fork-F1 zero) rate, which is what keeps the after-tax
            fields always populated.
    """

    inputs: RetirementInputs
    point: PlanPoint
    retirement_date: date | None
    axis: PeriodWindow
    projections: list[dict]
    pension: PensionSummary
    net: RetirementGapAnalysis

    @property
    def as_of(self) -> date:
        """The read pass's clock: the day the axis opens after.

        Returns:
            The pinned ``as_of``, so a page reporting "years remaining" beside
            this projection measures it from the same day the projection did.
        """
        return self.inputs.balance_ctx.as_of

    @property
    def pay_cadence(self) -> PayCadence:
        """How often the owner is paid going forward: the LATEST era's rhythm.

        Off the gap inputs.  Its reader spreads each account's contribution
        limit over a year's paychecks for the lever's headroom (where the
        contribution it is set against is today's paycheck's, priced at
        today's rhythm -- finding **SAL-571**, ruling **R-SAL72**).  **It no
        longer turns a paycheck into monthly income**, which is what this
        docstring said until plan step salary:X-av-2: the gap converts at the
        rhythm of the paycheck it compares against
        (:class:`~app.services.retirement_dashboard_service.GapPaycheck`,
        ruling **R-SAL70**).

        Returns:
            The owner's :class:`~app.services.pay_calendar.PayCadence`.
        """
        return self.inputs.gap.pay_cadence

    @property
    def safe_withdrawal_rate(self) -> Decimal:
        """The fractional SWR this picture's required savings were solved at.

        Read off the analysis rather than stored a second time: ``calculate_gap``
        already carries the rate it used, so there is no copy to keep in step.

        Returns:
            The fractional rate (``0.04`` for the 4% rule).
        """
        return self.net.safe_withdrawal_rate

    @property
    def blended_return(self) -> Decimal:
        """The annual return fraction this picture's growth actually ran at.

        ONE definition, where there were three per render before plan step
        C2-f2d-2: the assumptions rail's displayed rate, the readiness chart's
        needed-path rate, and the contribution lever's annuity rate each derived
        it, and the lever's is the one that divides the shortfall to produce the
        "contribute $X per period" the user is told to act on.  Three
        derivations of the rate a solver divides by is three chances for the
        page to display a return its own advice was not solved at.

        A uniform ``return_rate_override`` IS the blend -- every account's weight
        carries the same rate -- which is why it short-circuits rather than being
        blended against stored rates it has already replaced.

        **The percent round-trip is deliberate.**  The average is formed in
        percent and quantized to two decimals THERE, then scaled back to a
        fraction, because the quantized percent is what the rail displays: doing
        the division in fractions instead would hand the annuity factor a rate
        with more digits than the page states.

        Returns:
            The fractional annual return (e.g. ``Decimal("0.105")`` for 10.5%).
        """
        if self.point.return_rate_override is not None:
            return self.point.return_rate_override
        return _stored_blend_percent(
            self.projections, self.inputs.batch.params_by_account,
        ) / PCT_SCALE

    @property
    def funded_state(self) -> tuple[Decimal | None, bool]:
        """The after-tax funded ratio and the zero-requirement state.

        Returns:
            ``(funded_ratio, no_savings_needed)`` -- see
            :func:`app.services.retirement_gap_calculator.funded_ratio_state`.
        """
        return funded_ratio_state(self.net)

    @property
    def is_funded(self) -> bool:
        """Whether this plan reaches full funding.

        Funded means the requirement is zero (the pension covers the whole gap)
        or the quantized funded ratio reaches 100%.

        Returns:
            bool -- the predicate the retire-later binary search bisects on.
        """
        funded_ratio, no_savings_needed = self.funded_state
        return no_savings_needed or funded_ratio >= _FULLY_FUNDED


def _stored_blend_percent(projections, params_by_account) -> Decimal:
    """Balance-weight each account's STORED annual return, as a percentage.

    **The params come off the render's batch, not a query.**  This loop issued
    one ``InvestmentParams`` lookup per account and ran three times per render
    until plan step C2-f2d-2, while
    :func:`~app.services.retirement_projection.load_projection_batch` had already
    loaded every one of those rows in a single ``IN``.

    Zero is a real value (E-12), on both terms.  A stable-value sleeve at
    exactly 0.00% return must contribute its balance to the denominator -- a
    truthiness check dropped it entirely, and two $100k accounts at 0% and 7%
    then reported 7.00% instead of the true blended 3.50%.  A zero BALANCE is
    equally real: it contributes weight zero rather than being skipped.

    Args:
        projections: The per-account projection dicts, each carrying its
            ``account`` and its ``current_balance``.
        params_by_account: The batch's ``{account_id: InvestmentParams}``;
            accounts with no params row are absent and contribute nothing,
            because no stored rate is known for them.

    Returns:
        The weighted average as a Decimal PERCENT quantized to two decimals, or
        :data:`_DEFAULT_RETURN_PCT` when no account carries a known rate.
    """
    total_balance = Decimal("0")
    weighted_return = Decimal("0")
    for proj in projections:
        params = params_by_account.get(proj["account"].id)
        if params is None or params.assumed_annual_return is None:
            continue
        # INDEXED, not defaulted: ``_project_one_account`` writes
        # ``current_balance`` on EVERY projection dict it returns, so a missing
        # key is a producer defect and fails loud rather than substituting a
        # different account's fact.
        balance = proj["current_balance"]
        total_balance += balance
        weighted_return += balance * params.assumed_annual_return
    if total_balance > 0:
        return (
            weighted_return / total_balance * PCT_SCALE
        ).quantize(_PCT_QUANTUM)
    return _DEFAULT_RETURN_PCT


def _believed_batch(
    inputs: RetirementInputs, point: PlanPoint,
) -> ProjectionBatch:
    """The render's batch with its payroll feeds priced under *point*'s set.

    The batch is loaded once and is point-independent; what a point changes
    about it is WHICH raise set the payroll feeds price from (plan step
    salary:S3-f-2b).  So the wiring the batch carries is priced again here
    through the pass's pricer under :meth:`PlanPoint.terms_for` -- the
    resolvers rebuilt, no row re-read -- and the feeds are replaced on a copy.
    ``dataclasses.replace`` hands the copy the SAME ``seed_memo`` dict, so the
    forward seed a probe already resolved for an axis is still a hit.

    **What it costs depends on the set.**  At the stored set the pricers are
    the ones the loader built (the memo key is the canonical set): zero
    statements, zero constructions, and the copy's feeds price exactly what
    ``inputs.batch.feeds`` price -- rebuilt anyway rather than special-cased,
    because two paths to one feed is the shape this module removes.  At a
    PROBED set the first pricer for each profile the probe names is built here,
    and that construction issues no statement -- it loaded the profile's tax
    series, three statements per profile per distinct set, until plan step
    salary:X-at-1 moved the law into the code -- measured by
    ``TestThePointBelievesARaiseSet.test_a_probed_set_is_the_one_legitimate_second_pricer``.

    Args:
        inputs: The render's loaded inputs.
        point: The plan point whose raise set the feeds price under.

    Returns:
        A :class:`~app.services.retirement_projection.ProjectionBatch`
        sharing every field of ``inputs.batch`` but ``feeds``.
    """
    return replace(inputs.batch, feeds=price_payroll_feeds(
        inputs.batch.payroll, inputs.balance_ctx.paychecks(), point.terms_for,
    ))


def picture_at(
    inputs: RetirementInputs, point: PlanPoint,
) -> RetirementPicture:
    """The retirement picture at *point*, derived once per render.

    **The memo is the whole point of this function existing rather than
    :func:`_derive_picture` being called directly.**  The ``/retirement`` page
    renders the readiness verdict at the stored plan and then runs the
    retire-later search, whose month-0 probe is that same point; without the
    memo the page computes one picture twice and displays it as two independent
    facts (ledger row **P57**).  With it, the lever card's baseline IS the object
    the hero rendered -- not an equal one.

    Keyed on the point's VALUES, because that is what the answer is a function
    of: two callers asking for the same plan are asking one question.

    Args:
        inputs: The render's :class:`RetirementInputs`, which owns the memo.
        point: The :class:`PlanPoint` to picture.

    Returns:
        The :class:`RetirementPicture` at *point*.
    """
    if point not in inputs.picture_memo:
        inputs.picture_memo[point] = _derive_picture(inputs, point)
    return inputs.picture_memo[point]


def _derive_picture(
    inputs: RetirementInputs, point: PlanPoint,
) -> RetirementPicture:
    """Compute the picture at *point* from the render's loaded inputs.

    BOTH sides of the gap move with the plan: a later date extends the
    salary path, the pension's years of service and its high-salary window
    and the growth horizon, and it re-derives the income target from that
    longer path -- so the required target moves as well as the projected
    balance.  Every one of those is recomputed here; nothing that was LOADED
    is.  *The employer salary basis left this list at plan step
    salary:S3-e-2*: the payroll feed prices every period's gross through the
    engine, so a later date extends the axis and nothing else has to be
    rebuilt for it.  *The current paycheck JOINED it at plan step
    salary:S3-f-2a* (ruling **R-SAL21**): it is priced off the pass's pricer
    per point rather than loaded once, because the raise set a point is
    believed under can move this year's paycheck.  *And the RAISE SET became
    the point's at plan step salary:S3-f-2b* (ruling **R-SAL20**): the
    pension's salary path, the income target's, the current paycheck and the
    payroll feeds all read :meth:`PlanPoint.terms_for`, so a probe over one
    raise's end year moves every figure that raise feeds and no figure twice
    -- one belief per picture.  At the stored set every read resolves to the
    pricer the batch loader built and the pricer's memo answers after the
    first; a probed set builds ONE pricer per profile it names, which issues
    no query since plan step salary:X-at-1 (three per distinct set before).

    Args:
        inputs: The render's loaded inputs.
        point: The plan point to derive.

    Returns:
        The :class:`RetirementPicture`, uncached (:func:`picture_at` caches it).
    """
    gap = inputs.gap
    retirement_date = (
        None if inputs.base_date is None
        else add_months(inputs.base_date, point.month_offset)
    )
    # The render's ONE day, threaded into both producers that open a salary
    # path (pay-calendar plan step C2-f2e, ledger row **P55**; there were
    # three until salary:S3-e-2 deleted ``build_employer_salary_basis``).
    # Each read ``date.today().year`` for itself, and this function runs once
    # per PLAN POINT -- the retire-later lever probes about ten -- so a render
    # crossing a New Year could project the verdict card's path from year N
    # and the lever card's from N+1.
    as_of = inputs.balance_ctx.as_of
    calendar = inputs.balance_ctx.calendar()
    pension = compute_pension_summary(
        gap.pensions, as_of, point.terms_for, calendar, point.month_offset,
    )
    # The current paycheck off the PASS's pricer -- the same
    # ``ProfilePaychecks`` the payroll feed below prices from, so the income
    # target and the feed cannot price one payday two ways (plan step
    # salary:S3-f-2a; they did, by ``$31.29``, when this was a load-time
    # snapshot priced by a direct engine call with no calibration) -- under
    # the set THIS point believes, beside that set, as one argument.
    payroll = BelievedPayroll(
        terms_for=point.terms_for,
        current_paycheck=compute_current_paycheck(
            inputs.balance_ctx, gap.salary_profiles, point.terms_for,
        ),
    )
    # Every point-dependent field replaced together, from a context the render
    # built once: the account query and the period calendar do not move with a
    # candidate date, so this re-queries nothing.
    ctx = replace(
        inputs.base_ctx,
        planned_retirement_date=retirement_date,
        return_rate_override=point.return_rate_override,
    )
    axis = resolve_projection_axis(ctx)
    projections = project_accounts_with_batch(
        ctx, _believed_batch(inputs, point), axis,
    )
    # The paycheck the gap compares against AND the rhythm it is paid at,
    # together (ruling R-SAL70): the current paycheck standing in for the
    # final year's is converted at its own rhythm, not the latest era's.
    paycheck = compute_gap_net_biweekly(
        gap, payroll, retirement_date, as_of, calendar,
    )
    net = calculate_gap(
        net_biweekly_pay=paycheck.net,
        pay_cadence=paycheck.cadence,
        monthly_pension_income=pension.monthly_income,
        retirement_account_projections=projections,
        safe_withdrawal_rate=point.swr,
        # The EXPLICIT rate (fork F1): an unset rate is a zero with a flag, so
        # the after-tax fields are always populated and the readiness verdict
        # never silently drops its own frame.
        estimated_tax_rate=inputs.effective_tax_rate,
    )
    return RetirementPicture(
        inputs=inputs,
        point=point,
        retirement_date=retirement_date,
        axis=axis,
        projections=projections,
        pension=pension,
        net=net,
    )
