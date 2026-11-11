"""
Shekel Budget App -- Escrow Calculator

Pure-function service for mortgage escrow calculations.
No database access -- operates only on values passed in.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.utils.effective_dated import in_effect_on
from app.utils.money import (
    CENTS,
    MONTHS_PER_YEAR,
    ZERO,
    round_money,
    round_money_floor,
)


@dataclass(frozen=True)
class EscrowComponentDisplay:
    """Display DTO for one escrow component (MED-04 / E-16).

    Carries both the stored annual amount and the derived per-period
    monthly amount so the Jinja template renders without inline
    arithmetic -- the previous template-resident
    ``comp.annual_amount|float / 12`` introduced a binary-float cast
    on a Decimal before the divide, masking precision drift behind
    the formatter.  The monthly amounts are cent-allocated across the
    component set (largest remainder, deep-hunt #17) so the rendered
    rows sum exactly to :func:`calculate_monthly_escrow`'s
    sum-then-round total -- see :func:`_allocate_monthly_amounts`.

    ``inflation_rate`` is the storage-domain decimal fraction (e.g.
    ``Decimal("0.03")`` for 3 %); ``inflation_rate_pct`` is the same
    value multiplied by 100 for display, kept here so the template
    does not multiply rates inline either (E-16 applies to
    rate-arithmetic as much as to dollar-arithmetic).
    """

    id: int
    name: str
    annual_amount: Decimal
    monthly_amount: Decimal
    inflation_rate: Decimal | None
    inflation_rate_pct: Decimal | None


@dataclass(frozen=True)
class EscrowVersionDisplay:  # pylint: disable=too-many-instance-attributes
    """One version row in an escrow line's history drawer.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed because this is a
    cohesive display row: every field is one precomputed cell / control-state of a
    single version's drawer row, read side-by-side by the template, mirroring the
    other display DTOs here (:class:`EscrowComponentDisplay`).  Nesting them would
    fragment one row for no design gain.

    The supersession model exposes a line's whole timeline; this DTO is one row of
    it, precomputed so the drawer template renders without arithmetic or
    string-status comparisons (the "templates display, never compute" constraint).
    ``id`` is the VERSION id (the target of the per-version edit / delete controls,
    distinct from the summary's LINE id).  ``monthly_amount`` is the current
    version's cent-allocated summary monthly (so the drawer's current row matches
    the badge) and ``annual / 12`` rounded for every other row.  ``status_key`` is
    a CSS-modifier token (``current`` / ``scheduled`` / ``past``) interpolated into
    the row class, and ``status_label`` its human caption -- neither is compared in
    the template.  ``is_editable`` / ``is_deletable`` are the forward-only guard's
    verdict for this row: a version at or before the latest settled payment's DUE
    date is frozen (editing / deleting it would move a settled split), and a
    line's only version cannot be deleted (use the line-level remove).
    """

    id: int
    effective_date: date
    annual_amount: Decimal
    monthly_amount: Decimal
    inflation_rate_pct: Decimal | None
    is_removed: bool
    status_key: str
    status_label: str
    is_editable: bool
    is_deletable: bool


@dataclass(frozen=True)
class EscrowLineDisplay:
    """One escrow line on the loan card: its current-state summary + version drawer.

    Composes the existing per-line summary (:class:`EscrowComponentDisplay`, the
    line's in-effect-today amount with the cent-allocated monthly that sums to the
    escrow badge) with the full version history the drawer expands to
    (:class:`EscrowVersionDisplay`) and ``has_scheduled`` -- whether a future-dated
    version exists, so the collapsed summary can flag a queued change the
    today-only amount would otherwise hide.  Only ACTIVE lines (their in-effect
    version is not a removal tombstone) get a card; a fully-removed line is absent,
    exactly as the pre-drawer list showed.
    """

    summary: "EscrowComponentDisplay"
    versions: list["EscrowVersionDisplay"]
    has_scheduled: bool


@dataclass(frozen=True)
class ResolvedEscrowLine:
    """An escrow line resolved to the version in effect on a given date.

    The output of :func:`resolve_active_lines` and the input every "today's
    escrow" surface consumes -- the supersession model's answer to "what escrow
    does this line carry on date D."  ``id`` is the LINE's stable identity (not a
    version id): it is the target of the escrow tab's delete/edit control, so a
    rename or amount change never breaks the link.  ``annual_amount`` /
    ``inflation_rate`` come from the resolved version; ``inflation_rate`` drives
    the forward "next year" display projection (:func:`project_monthly_escrow`),
    which compounds it in whole annual steps from today -- never from a version's
    insert timestamp.

    Exposes exactly the attributes :func:`calculate_monthly_escrow`,
    :func:`project_monthly_escrow`, and :func:`build_escrow_display` read
    (``id``, ``name``, ``annual_amount``, ``inflation_rate``), so the resolved
    set is a drop-in for the component list those functions took under the legacy
    model.
    """

    id: int
    name: str
    annual_amount: Decimal
    inflation_rate: Decimal | None


def _allocate_monthly_amounts(annuals: list[Decimal]) -> list[Decimal]:
    """Cent-allocate the monthly escrow total across components (#17).

    Largest-remainder allocation: each component starts from its
    floored ``annual / 12`` and the leftover cents -- the difference
    between the sum of floors and the sum-then-rounded total -- go to
    the components with the largest fractional remainders (ties broken
    by input order; Python's sort is stable).  The result is per-row
    display values that each lie within one cent of the exact
    ``annual / 12`` AND sum exactly to the same total
    :func:`calculate_monthly_escrow` computes -- so the escrow tab's
    rows always add up to its badge.  The
    aggregate's own sum-then-round rule (the E-26 boundary rounding
    feeding the loan payment) is untouched; only the per-row display
    split changes.

    Args:
        annuals: Full-precision annual amounts of the ACTIVE
            components, in display order.

    Returns:
        The per-component monthly display amounts, same order, summing
        to ``round_money(sum(annual / 12))``.
    """
    exacts = [annual / MONTHS_PER_YEAR for annual in annuals]
    total = round_money(sum(exacts, ZERO))
    bases = [round_money_floor(exact) for exact in exacts]
    remainder_cents = int((total - sum(bases, ZERO)) / CENTS)
    by_remainder = sorted(
        range(len(exacts)),
        key=lambda i: exacts[i] - bases[i],
        reverse=True,
    )
    monthlies = list(bases)
    for i in by_remainder[:remainder_cents]:
        monthlies[i] += CENTS
    return monthlies


def build_escrow_display(components: list) -> list[EscrowComponentDisplay]:
    """Build display DTOs for the escrow components list (MED-04 / E-16).

    Builds one display row per GIVEN component and cent-allocates the
    aggregate monthly total across them via :func:`_allocate_monthly_amounts`,
    so each row's monthly value is within one cent of its exact ``annual / 12``
    and the rows sum exactly to the badge total ``calculate_monthly_escrow``
    renders beside them (deep-hunt #17 -- per-row HALF_UP rounding made two
    $100/yr components display 8.33 + 8.33 = 16.66 against a 16.67 badge).

    Like :func:`calculate_monthly_escrow`, this does NOT filter by active state
    -- the caller supplies the set to display (today's active lines, via
    :func:`resolve_active_lines`).
    Processing the identical set both functions receive keeps the rows-sum-to-
    badge invariant true for ANY input, rather than only when the caller happens
    to pre-filter removed components out (they both would otherwise diverge on a
    removed component -- rows omit it, badge counts it -- resurfacing #17).

    Args:
        components: Iterable of escrow components with ``.id``,
            ``.name``, ``.annual_amount``, and optionally ``.inflation_rate``.

    Returns:
        List of :class:`EscrowComponentDisplay`, one per input component, in
        input order.
    """
    annuals = [Decimal(str(comp.annual_amount)) for comp in components]
    monthlies = _allocate_monthly_amounts(annuals)
    rows: list[EscrowComponentDisplay] = []
    for comp, annual, monthly in zip(components, annuals, monthlies):
        if getattr(comp, "inflation_rate", None) is not None:
            inflation = Decimal(str(comp.inflation_rate))
            inflation_pct = inflation * Decimal("100")
        else:
            inflation = None
            inflation_pct = None
        rows.append(EscrowComponentDisplay(
            id=comp.id,
            name=comp.name,
            annual_amount=annual,
            monthly_amount=monthly,
            inflation_rate=inflation,
            inflation_rate_pct=inflation_pct,
        ))
    return rows


def _after_forward_boundary(effective_date: date, boundary: date | None) -> bool:
    """Whether an escrow version at ``effective_date`` clears the forward-only guard.

    ``True`` when the version takes effect strictly after ``boundary`` -- the latest
    settled payment's DUE date
    (:func:`app.services.loan_loaders.latest_settled_payment_due_date`) -- so
    editing or deleting it cannot move an already-settled payment's escrow split.
    ``boundary is None`` (the loan has no settled payment) means nothing is frozen,
    so every version clears.

    Args:
        effective_date: The version's effective date.
        boundary: The latest settled payment's due date, or ``None``.

    Returns:
        ``True`` when the version is safe to edit / delete, ``False`` when frozen.
    """
    return boundary is None or effective_date > boundary


def _version_status(version, current_id: int | None, on_date: date) -> tuple[str, str]:
    """Classify one escrow version for the drawer: ``(css_key, human_label)``.

    A future-dated version is ``scheduled`` (a queued change, or a queued removal
    when it is a tombstone); the in-effect-today version (``version.id ==
    current_id``) is ``current``; anything else on/before today is a superseded
    ``past`` version (a past removal when it is a tombstone).  The key is a CSS
    modifier token and the label its caption, both precomputed so the template
    never compares a status string.

    Args:
        version: The :class:`~app.models.escrow_line.EscrowComponentVersion`.
        current_id: The id of the line's in-effect-today version, or ``None``.
        on_date: Today (the resolution date the ``scheduled`` cutoff uses).

    Returns:
        ``(status_key, status_label)`` for the version.
    """
    if version.effective_date > on_date:
        return ("scheduled", "Scheduled removal" if version.is_removed else "Scheduled")
    if version.id == current_id:
        return ("current", "Current")
    return ("past", "Removed" if version.is_removed else "Past")


def _build_version_rows(
    line, on_date: date, boundary: date | None, current_monthly: Decimal,
) -> list[EscrowVersionDisplay]:
    """Build one escrow line's full version-history rows for the drawer (ascending).

    Every version of the line, oldest first, each precomputed into an
    :class:`EscrowVersionDisplay`: the in-effect-today version carries the summary's
    cent-allocated ``current_monthly`` (so the drawer's current row matches the
    badge) and every other row a plain ``annual / 12``; ``is_editable`` /
    ``is_deletable`` apply the forward-only guard
    (:func:`_after_forward_boundary`) -- a frozen (settled-affecting) version is
    read-only, a removal tombstone is not amount-editable, and a line's only version
    cannot be deleted (the line-level remove handles that).

    Args:
        line: The :class:`~app.models.escrow_line.EscrowLine` with ``versions``.
        on_date: Today, for status classification.
        boundary: The forward-only guard boundary (latest settled due date).
        current_monthly: The summary's cent-allocated monthly for the current row.

    Returns:
        The line's version rows, ascending by ``effective_date``.
    """
    current = in_effect_on(line.versions, on_date)
    current_id = current.id if current is not None else None
    rows: list[EscrowVersionDisplay] = []
    for version in sorted(line.versions, key=lambda v: v.effective_date):
        annual = Decimal(str(version.annual_amount))
        monthly = (
            current_monthly if version.id == current_id
            else round_money(annual / MONTHS_PER_YEAR)
        )
        inflation_pct = (
            Decimal(str(version.inflation_rate)) * Decimal("100")
            if version.inflation_rate is not None else None
        )
        status_key, status_label = _version_status(version, current_id, on_date)
        after = _after_forward_boundary(version.effective_date, boundary)
        is_scheduled = version.effective_date > on_date
        rows.append(EscrowVersionDisplay(
            id=version.id,
            effective_date=version.effective_date,
            annual_amount=annual,
            monthly_amount=monthly,
            inflation_rate_pct=inflation_pct,
            is_removed=version.is_removed,
            status_key=status_key,
            status_label=status_label,
            # An unfrozen, non-tombstone version is editable in place; only a
            # SCHEDULED (future) version is per-version deletable -- a current /
            # past amount is corrected by editing it or removing the whole line.
            is_editable=after and not version.is_removed,
            is_deletable=after and is_scheduled,
        ))
    return rows


def _upcoming_summary(line, on_date: date) -> "EscrowComponentDisplay | None":
    """Summary for a line that is NOT active today but has an upcoming version.

    A line whose earliest non-removed version is still in the future (e.g. a new
    line added with a future effective date, or one whose current version was
    edited / deleted forward) has no in-effect-today amount, so
    :func:`resolve_active_lines` drops it.  Surfacing it here off its earliest
    upcoming version keeps it VISIBLE on the card (as a "Scheduled" line) rather
    than silently vanishing after the operator schedules it -- the invisible-change
    trap the drawer exists to avoid.  Its monthly is a plain ``annual / 12`` (it is
    not part of today's badge, which counts only active lines), so no
    cent-allocation applies.

    Args:
        line: The :class:`~app.models.escrow_line.EscrowLine`.
        on_date: Today.

    Returns:
        An :class:`EscrowComponentDisplay` from the earliest upcoming non-removed
        version, or ``None`` when the line has no upcoming non-removed version
        (fully removed, or empty).
    """
    upcoming = [
        version for version in line.versions
        if version.effective_date > on_date and not version.is_removed
    ]
    if not upcoming:
        return None
    version = min(upcoming, key=lambda v: v.effective_date)
    annual = Decimal(str(version.annual_amount))
    inflation = (
        Decimal(str(version.inflation_rate))
        if version.inflation_rate is not None else None
    )
    return EscrowComponentDisplay(
        id=line.id,
        name=line.name,
        annual_amount=annual,
        monthly_amount=round_money(annual / MONTHS_PER_YEAR),
        inflation_rate=inflation,
        inflation_rate_pct=(
            inflation * Decimal("100") if inflation is not None else None
        ),
    )


def build_escrow_card(
    lines: list, on_date: date, forward_boundary: date | None,
) -> list[EscrowLineDisplay]:
    """Build the loan escrow card: each visible line's summary + version drawer.

    The single escrow-card display builder both the loan-dashboard GET and the
    escrow HTMX routes render, so the inline card and every post-mutation swap are
    byte-identical.  A line is shown when it has ANY non-removed version -- active
    TODAY (summary from :func:`resolve_active_lines` -> :func:`build_escrow_display`,
    preserving its cent-allocated rows-sum-to-badge invariant) OR only UPCOMING
    (:func:`_upcoming_summary`, so a future-dated line never silently vanishes).
    Each card layers the line's full version history
    (:func:`_build_version_rows`) plus ``has_scheduled``.  A fully-removed line (its
    in-effect version is a tombstone and it has no upcoming real version) is
    omitted, exactly as the pre-drawer list behaved.

    Args:
        lines: The account's :class:`~app.models.escrow_line.EscrowLine` rows with
            their ``versions`` loaded (:func:`app.services.loan_loaders.load_escrow_lines`).
        on_date: Today -- the date the summary and the ``current`` / ``scheduled``
            status split resolve against.
        forward_boundary: The forward-only guard boundary -- the latest settled
            payment's DUE date, or ``None`` when nothing is settled -- that
            decides each version row's ``is_editable`` / ``is_deletable``.

    Returns:
        One :class:`EscrowLineDisplay` per visible line, in the loader's name order.
    """
    active = {s.id: s for s in build_escrow_display(resolve_active_lines(lines, on_date))}
    cards: list[EscrowLineDisplay] = []
    for line in lines:
        summary = active.get(line.id) or _upcoming_summary(line, on_date)
        if summary is None:
            continue
        cards.append(EscrowLineDisplay(
            summary=summary,
            versions=_build_version_rows(
                line, on_date, forward_boundary, summary.monthly_amount,
            ),
            has_scheduled=any(
                version.effective_date > on_date for version in line.versions
            ),
        ))
    return cards


def calculate_monthly_escrow(components: list) -> Decimal:
    """Sum the given escrow components' annual amounts / 12.

    The pure summation primitive: the caller supplies the component set already
    resolved for the date in question.  For the supersession escrow model that
    resolution is :func:`resolve_active_lines` (each line's in-effect, non-removed
    version on a date), and :func:`escrow_monthly_as_of` is the date-keyed wrapper
    that pairs the two -- the split reads it per payment date, the display / cash
    surfaces on today.  This function no longer filters by active state itself; it
    sums exactly the components handed to it.  Inflation is NOT applied here --
    recorded past/present escrow is exact; the forward-projection escalation lives
    in :func:`project_monthly_escrow` (a display concern only), so the loan-payment
    split never touches inflation by construction.

    Args:
        components: List of objects with ``.annual_amount``.

    Returns:
        Monthly escrow amount rounded to 2 decimal places.
    """
    total = ZERO
    for comp in components:
        total += Decimal(str(comp.annual_amount)) / MONTHS_PER_YEAR
    return round_money(total)


def project_monthly_escrow(components: list, years: int) -> Decimal:
    """Project a resolved escrow set forward by whole annual steps (display only).

    The forward-projection counterpart to :func:`calculate_monthly_escrow`: each
    component's stored annual amount is compounded by its ``inflation_rate`` over
    ``years`` whole annual steps (a component with no rate is carried unchanged),
    then summed as ``annual / 12`` with the same sum-then-round boundary (E-26).
    A forward DISPLAY estimate ONLY -- recorded past/present escrow is exact and
    the loan-payment split never calls this; it drives the loan card's "next year"
    escrow note (:func:`app.routes.loan.dashboard._project_next_year_escrow`).

    Compounding is keyed to a whole number of annual steps from today, NOT to the
    elapsed span since a version's ``created_at`` (a technical insert timestamp
    the old inflation math read): the estimate is stable regardless of when the
    version was recorded or when the page is viewed, and matches the per-year
    meaning of ``inflation_rate`` (spec Sec. 8).

    Args:
        components: The resolved-today escrow set (:func:`resolve_active_lines`),
            each with ``.annual_amount`` and optional ``.inflation_rate``.
        years: The whole number of annual steps to project forward (``1`` for the
            next-year note); ``0`` returns today's figure unchanged.

    Returns:
        The projected monthly escrow total, rounded to cents.
    """
    total = ZERO
    for comp in components:
        annual = Decimal(str(comp.annual_amount))
        rate = getattr(comp, "inflation_rate", None)
        if rate is not None:
            annual = annual * (1 + Decimal(str(rate))) ** years
        total += annual / MONTHS_PER_YEAR
    return round_money(total)


def resolve_active_lines(lines: list, on_date: date) -> list[ResolvedEscrowLine]:
    """Resolve each escrow line to its in-effect, non-removed version on ``on_date``.

    For every line, pick the version in effect on ``on_date``
    (:func:`~app.utils.effective_dated.in_effect_on`); drop the line when it
    has no version yet or its in-effect version is a removal tombstone
    (``is_removed``), so it contributes nothing on that date.  The returned
    rows preserve input line order (the
    loaders sort by name), which the display cent-allocation relies on for stable
    tie-breaking.

    The single "what escrow is active on date D" resolver: both the display
    surfaces (via :func:`build_escrow_display`) and the monthly total (via
    :func:`escrow_monthly_as_of`) read exactly this set, so a rendered row and the
    figure it is summed into can never disagree.

    Args:
        lines: :class:`~app.models.escrow_line.EscrowLine` objects, each exposing
            ``id``, ``name``, and ``versions``.
        on_date: The date to resolve every line's in-effect version for.

    Returns:
        One :class:`ResolvedEscrowLine` per line active on ``on_date``, in the
        input line order; empty when no line is active.
    """
    resolved: list[ResolvedEscrowLine] = []
    for line in lines:
        # A removal tombstone is a normal version here (it wins when it is
        # the latest on or before the date); this loop decides it counts 0.
        version = in_effect_on(line.versions, on_date)
        if version is None or version.is_removed:
            continue
        inflation = version.inflation_rate
        resolved.append(ResolvedEscrowLine(
            id=line.id,
            name=line.name,
            annual_amount=Decimal(str(version.annual_amount)),
            inflation_rate=(
                Decimal(str(inflation)) if inflation is not None else None
            ),
        ))
    return resolved


def escrow_monthly_as_of(lines: list, on_date: date) -> Decimal:
    """Monthly escrow total for a loan's lines as of ``on_date`` (the DRY heart).

    Resolves each line to its in-effect, non-removed version
    (:func:`resolve_active_lines`) and sums ``annual_amount / 12`` with the same
    sum-then-round boundary :func:`calculate_monthly_escrow` uses (E-26).  The
    ONE function every date-keyed escrow figure flows through: the loan-payment
    split reads it on each payment's date and the cash / display surfaces read it
    on today, so the escrow built into a payment's cash and the escrow its split
    subtracts are the same figure by construction, never by coincidence.  No
    inflation is applied -- recorded past/present escrow is exact; inflation is a
    forward-projection display concern only (:func:`project_monthly_escrow`).

    Args:
        lines: :class:`~app.models.escrow_line.EscrowLine` objects with their
            ``versions`` loaded.
        on_date: The date to compute the monthly escrow total for.

    Returns:
        The rounded monthly escrow total; ``Decimal("0.00")`` when no line is
        active on ``on_date``.
    """
    return calculate_monthly_escrow(resolve_active_lines(lines, on_date))


def calculate_total_payment(monthly_pi: Decimal, components: list) -> Decimal:
    """P&I + monthly escrow = total monthly payment.

    Args:
        monthly_pi: Monthly principal & interest payment.
        components: Escrow components for the account (the resolved-today set).

    Returns:
        Total monthly payment (P&I + escrow).
    """
    escrow = calculate_monthly_escrow(components)
    return round_money(monthly_pi + escrow)


@dataclass(frozen=True)
class _MergedLineView:
    """A line-shaped view of a prospective merged version set (merge planning).

    :func:`plan_escrow_line_merge` builds one of these to resolve the merged
    line's escrow via :func:`escrow_monthly_as_of` WITHOUT touching the database,
    so the merge's escrow-preservation invariant is checked before any write.
    Exposes exactly the ``id`` / ``name`` / ``versions`` attributes
    :func:`resolve_active_lines` reads off a real
    :class:`~app.models.escrow_line.EscrowLine`.
    """

    id: int
    name: str
    versions: list


@dataclass(frozen=True)
class EscrowMergeCandidate:
    """One line offered as a merge SOURCE in another line's drawer.

    ``id`` is the line to fold in; ``label`` is a human tag disambiguating it --
    the line name plus its effective-date span and a ``removed`` marker -- so the
    operator can identify a hidden removed predecessor (which the card itself does
    not show) apart from an active line.  An active name is unique per account, but
    a removed line may share a name with an active one, so the span / marker is
    what tells the two apart.
    """

    id: int
    label: str


@dataclass(frozen=True)
class EscrowMergePlan:
    """A validated plan to fold one escrow line's history into another.

    The pure result of :func:`plan_escrow_line_merge`: which of the SOURCE line's
    versions to repoint onto the target (``versions_to_move``) and which to drop as
    superseded by a target version already on the same date (``versions_to_drop``),
    or an actionable ``error`` naming why the merge is unsafe.  The target's own
    versions are never touched, so its history is preserved verbatim.  ``error`` is
    set only on the reject path, where both version lists are empty; on the success
    path the two lists partition the source line's versions (those repointed onto
    the target and those dropped) -- which is the empty partition for the degenerate
    versionless source the UI never offers.
    """

    versions_to_move: list
    versions_to_drop: list
    error: str | None


def _escrow_unchanged_by_merge(source, target, merged_versions: list) -> bool:
    """Whether folding SOURCE + TARGET into one line preserves escrow on every date.

    The safety heart of the merge: compares the monthly escrow resolved on every
    version-boundary date -- the union of both lines' effective dates, the only
    dates the step-function escrow can change -- summed across the two SEPARATE
    lines against the single MERGED line.  A step function is constant between its
    breakpoints, so equality at every breakpoint is equality on every date.  When
    it holds, no payment's escrow (settled or projected) moves, so the merge
    preserves every derived split byte-for-byte: the split reads escrow by amount
    via :func:`escrow_monthly_as_of`, so escrow that is byte-identical on every date
    leaves every derived split byte-identical too, and with it every ledger line
    the loan's walk reads off them.  (The posting reconcile each escrow write ran
    after the merge re-derived the same stored ledger, an idempotent no-op, until
    plan task plan#3076 deleted it.)

    Args:
        source: The line being folded in.
        target: The surviving line.
        merged_versions: The prospective merged version list (the target's kept
            versions plus the moved source versions).

    Returns:
        ``True`` when escrow-as-of every boundary date is unchanged by the merge.
    """
    merged_line = _MergedLineView(
        id=target.id, name=target.name, versions=merged_versions,
    )
    dates = {version.effective_date for version in source.versions}
    dates.update(version.effective_date for version in target.versions)
    return all(
        escrow_monthly_as_of([source, target], on_date)
        == escrow_monthly_as_of([merged_line], on_date)
        for on_date in dates
    )


def plan_escrow_line_merge(source, target) -> EscrowMergePlan:
    """Plan folding SOURCE's version history into TARGET, preserving escrow-per-date.

    Reunifies a line whose history split across two lines -- the rename-split the
    supersession model now avoids, but a legacy backfill (one line per historical
    name) or a Remove+Add can still produce.  Builds the unified version set: every
    TARGET version is kept, plus each SOURCE version whose ``effective_date`` the
    target does not already carry; a same-date collision keeps the target's version
    and drops the source's, since the operator chose the target as the surviving
    line.  It then VERIFIES (:func:`_escrow_unchanged_by_merge`) that the escrow
    resolved on every date is byte-identical before and after; when a date's escrow
    would move, the two lines genuinely OVERLAP in time (two concurrent charges,
    not one renamed line), so the merge is REJECTED rather than silently dropping a
    charge or moving a settled payment's split.

    Only source versions are ever moved or dropped, so a merge cannot be used to
    mutate the target's own history.  Because escrow-per-date is preserved, the
    forward-only guard is subsumed (no settled split moves), so the worked-out
    ledger reads the same lines after a merge as before it (each split reads the
    escrow amount, never a line id).

    Args:
        source: The :class:`~app.models.escrow_line.EscrowLine` to fold in and
            delete (its versions are moved to / dropped from the target).
        target: The :class:`~app.models.escrow_line.EscrowLine` to keep as the
            surviving line and history.

    Returns:
        An :class:`EscrowMergePlan`: the source versions to move and to drop on
        success, or an ``error`` message when the merge would change escrow.
    """
    target_dates = {version.effective_date for version in target.versions}
    versions_to_move = [
        version for version in source.versions
        if version.effective_date not in target_dates
    ]
    versions_to_drop = [
        version for version in source.versions
        if version.effective_date in target_dates
    ]
    merged_versions = list(target.versions) + versions_to_move
    if not _escrow_unchanged_by_merge(source, target, merged_versions):
        return EscrowMergePlan(
            versions_to_move=[],
            versions_to_drop=[],
            error=(
                "These lines are active at the same time, so merging them would "
                "change the escrow on some dates. Overlapping escrow lines can't be "
                "merged (if one is a duplicate, remove it instead)."
            ),
        )
    return EscrowMergePlan(
        versions_to_move=versions_to_move,
        versions_to_drop=versions_to_drop,
        error=None,
    )


def build_merge_candidates(lines: list) -> list[EscrowMergeCandidate]:
    """Build the merge-source candidates for the escrow card (an account's lines).

    One :class:`EscrowMergeCandidate` per line -- INCLUDING fully-removed lines,
    which :func:`build_escrow_card` hides but which are exactly the rename-split
    predecessors merge exists to fold back in.  Each label is
    ``name (first[ - last] effective date[, removed])`` so a hidden line is
    identifiable; the template offers, in each drawer, every candidate whose id is
    not that drawer's own line, so an empty / single-line account gets no merge
    control (nothing to merge in).

    Args:
        lines: The account's :class:`~app.models.escrow_line.EscrowLine` rows with
            their ``versions`` loaded
            (:func:`app.services.loan_loaders.load_escrow_lines`).

    Returns:
        One candidate per line that has at least one version, in the loader's name
        order; empty when the account has none.
    """
    candidates: list[EscrowMergeCandidate] = []
    for line in lines:
        if not line.versions:
            continue
        dates = sorted(version.effective_date for version in line.versions)
        latest = max(line.versions, key=lambda version: version.effective_date)
        span = dates[0].strftime("%b %Y")
        if dates[-1] != dates[0]:
            span = f"{span} - {dates[-1].strftime('%b %Y')}"
        if latest.is_removed:
            span = f"{span}, removed"
        candidates.append(EscrowMergeCandidate(
            id=line.id, label=f"{line.name} ({span})",
        ))
    return candidates
