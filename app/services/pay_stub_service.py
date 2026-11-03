"""Shekel Budget App -- the pay stub ENTRY door's service (plan step salary:S11-b).

A real pay stub is TRANSCRIBED line by line into the four tables of
:mod:`app.models.pay_stub` (``S11-a``) through this service and nothing else.
Since plan step **salary:S11-c-2c** the paycheck engine prices each
paycheck's four taxes from one switched-on stub (rulings **R-SAL42**,
**R-SAL54**; ``paycheck_calculator._stubs``), so what this door saves moves
money: a stub's figures and the kinds it records are the engine's input,
and the switch takes a stub out of pricing.

**What the door refuses, and whose rule each refusal is:**

* a date that is not a payday the app holds or projects a paycheck for
  (ruling **R-SAL49**, "Record the payday first"), and a payday later than the
  owner's next one (**R-SAL48**, "Up to next payday") -- asked of a new stub
  and of an edit that CHANGES the date, never of an edit that keeps it
  (**R-SAL53**, "Keep its date, allow edits": a stub whose payday later left
  the pay record stays correctable in place).  One test answers both
  "is it a payday" and "what does the app price that day": the calendar's own
  span, :meth:`~app.services.pay_calendar.PayCalendar.span_containing`.  A day
  below the record is refused as the record's (``your pay record starts
  ...``); once the pay calendar's "Add earlier paychecks" door exists
  (``pay_calendar:C18-b``, not yet built), an earlier stub gets in by
  recording its payday there first;
* a NEW stub on a payday that already holds one, and an edit that moves a
  stub onto such a payday (fork 4, "One per payday", as ruling **R-SAL52**,
  "Refuse, show the saved one", amends it: nothing is overwritten unseen --
  the entry flow opens a held payday's stub instead);
* a stub missing any of the four taxes (``$0.00`` is a figure, a blank is
  not: decomposition item 2, "the four taxes required");
* a printed GROSS that base pay and the taxable earnings do not add up to, to
  the cent (ruling **R-SAL99**, "Check the gross too", closing finding
  **SAL-590**, which reads the printed gross as exactly that sum): a gross
  typed into the Base pay box counts every taxable earning twice, and an
  earning moved between taxable and after-tax moves the gross but not the
  net, so only this check sees it -- typed once like the net, never stored.
  On a job whose stub's gross also holds the after-tax earnings (ruling
  **R-SAL102**, a yes/no on the profile, closing finding **SAL-592**) those
  are added too, and that job's check cannot see the taxable / after-tax
  slip.  How a miss is worded is read off the net's miss beside it, naming
  the job's setting where the pair allows it (rulings **R-SAL104**,
  **R-SAL106**, **R-SAL108**, **R-SAL110**, **R-SAL111**;
  :mod:`app.services.pay_stub_gross`);
* a printed net the lines do not add up to, to the cent (ruling **R-SAL42**,
  decomposition item 2: typed once as a check, never stored);
* a one-off named like one of the profile's paycheck lines or a tax, or like
  another one-off on the same stub (ruling **R-SAL45**, compared ignoring
  capitals and extra spaces, **R-SAL51** (b)) -- the line and tax clash asked
  only of a one-off the save ADDS or RENAMES (**R-SAL57**, "Check only what a
  save adds"), so a one-off the door saved never blocks its own stub and a
  paycheck line stays free to take a name a saved one-off already has.

The payday's calendar range, the base pay and the amount bounds are the entry
schema's; a line or kind id the owner does not hold is not a refusal but a
NOT-FOUND (the 404 rule).

**A stub line records its own kind** (ruling **R-SAL58**, "Stub records its
kind", which retired **R-SAL56**'s refusal): the kind the stub prints each
line under, entered beside its amount, pre-set to the app's.  Every total
reads the stub's own kinds, never the paycheck line's, so editing a line --
its kind included -- moves no saved stub (finding **SAL-567**); the report
lists a kind that differs from the app's the way it lists an amount.

**Every write keys on the owned profile and stub, never on input.**  A stub's
``salary_profile_id`` is the profile the route resolved; a child row's
``pay_stub_id`` is never read from input at all (review L5 of ``S11-a``): each
child is appended to, updated on or removed from its stub's own collection, so
the database's keys are the only thing that places it.

**An edit writes the stub ROW whenever it changes anything**, including a
child-only edit, because the row's ``version_id`` guards the stub as one act
only if the row is in the UPDATE (``PayStub``'s docstring: SQLAlchemy bumps it
for the row alone).  An edit that changes nothing writes nothing and bumps
nothing -- the status seam's rule -- so re-saving a form nobody changed is not
a conflict.  A DOUBLE submit whose first post changed the stub IS one: the
second carries the version the first consumed, and the route turns it away
as stale like every versioned form in the app.

Flask-free: takes plain values and ORM rows, returns plain values; every
function that prices takes the route's
:class:`~app.services.balance_at.BalanceContext`.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.exceptions import NotFoundError, PayStubRefused
from app.extensions import db
from app.models.pay_stub import (
    PayStub,
    PayStubLineAmount,
    PayStubOneOff,
    PayStubWithholding,
)
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_line_kinds, withholding_kinds
from app.services.pay_stub_gross import gross_refusals
from app.services.paycheck_calculator import (
    StubTotals,
    one_offs_change_taxes,
    stub_totals,
)
from app.services.salary_paydays import paycheck_on, payday_refusal_for_door

if TYPE_CHECKING:
    from app.services.balance_at import BalanceContext


# ── Values ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LineFigure:
    """What a stub prints for one of the profile's paycheck lines (**R-SAL58**).

    Attributes:
        paycheck_line_kind_id: The kind the stub prints it under, which may
            differ from the paycheck line's own.
        amount: Its figure, ``>= 0``.
    """
    paycheck_line_kind_id: int
    amount: Decimal


@dataclass(frozen=True)
class OneOffFigure:
    """One named one-off a stub prints (fork 8b, "Keep it as a one-off").

    Attributes:
        name: The name as printed, outer spaces trimmed.
        paycheck_line_kind_id: Which of the five paycheck-line kinds it is.
        amount: Its figure, ``>= 0``.
    """
    name: str
    paycheck_line_kind_id: int
    amount: Decimal


@dataclass(frozen=True)
class StubFigures:
    """Everything a stub RECORDS, as the entry form states it.

    The printed gross and net are not here: they are CHECKS the door runs
    (:class:`PrintedTotals`), not figures the stub keeps (rule 14 -- both are
    derived from these).

    Attributes:
        payday: The stub's date.
        base_pay: The base pay line, ``> 0``.
        line_amounts: ``paycheck_line_id -> LineFigure`` for each of the
            profile's paycheck lines the stub prints; a line it does not print
            is absent.
        withholdings: ``withholding_kind_id -> amount``, one per tax.
        one_offs: The one-offs, in the order the form listed them.
        notes: Free text, or ``None``.
    """
    payday: date
    base_pay: Decimal
    line_amounts: Mapping[int, LineFigure]
    withholdings: Mapping[int, Decimal]
    one_offs: tuple[OneOffFigure, ...]
    notes: str | None


@dataclass(frozen=True)
class PrintedTotals:
    """The two totals a stub PRINTS, typed once at the door as checks and never stored.

    What the stub's own figures must add up to
    (:class:`~app.services.paycheck_calculator.StubTotals`): the
    gross (ruling **R-SAL99**) and the net (ruling **R-SAL42**).  Both are
    derived from the figures, so keeping either would be a second home for
    it (rule 14); they exist only for the one submission that checks them.

    Attributes:
        gross: The gross pay the stub prints, which R-SAL99 reads as base pay
            plus every taxable earning, and R-SAL102 as that plus every
            after-tax earning on a job whose stub prints them in its gross.
        net: The net pay the stub prints.
    """
    gross: Decimal
    net: Decimal


@dataclass(frozen=True)
class LineComparison:
    """One paycheck line set beside itself: the stub's figure and kind, and the app's.

    Fork 2, "Taxes only": "When you enter a stub, the app lists any line that
    disagrees with it so you can fix one side."  ``None`` on a side means that
    side does not have the line on this payday -- the stub does not print it,
    or the app does not take it.  Since ruling **R-SAL58** the kind is compared
    too: the stub records the kind it prints the line under, and one that
    differs from the paycheck line's is listed like a figure that differs.

    Attributes:
        name: The paycheck line's name.
        kind: The paycheck line's own kind.
        stub_kind: The kind the stub prints it under, or ``None`` when the
            stub does not print it.
        on_stub: The stub's figure, or ``None``.
        in_app: What the app prices the line at that payday, or ``None``.
    """
    name: str
    kind: PaycheckLineKindEnum
    stub_kind: PaycheckLineKindEnum | None
    on_stub: Decimal | None
    in_app: Decimal | None

    @property
    def kind_label(self) -> str:
        """The paycheck line's kind, worded (:data:`~app.services.paycheck_line_kinds.LABELS`)."""
        return paycheck_line_kinds.LABELS[self.kind]

    @property
    def stub_kind_label(self) -> str | None:
        """The stub's kind, worded, or ``None`` when the stub does not print the line."""
        return paycheck_line_kinds.LABELS[self.stub_kind] if self.stub_kind is not None else None

    @property
    def kind_agrees(self) -> bool:
        """Whether the stub prints the line under the paycheck line's kind.

        A line the stub does not print has no kind on the stub to disagree.
        """
        return self.stub_kind is None or self.stub_kind == self.kind

    @property
    def agrees(self) -> bool:
        """Whether both sides have the line at the same figure, under the same kind."""
        return self.on_stub == self.in_app and self.kind_agrees


@dataclass(frozen=True)
class OneOffRow:
    """A one-off as the report lists it: its name, its kind worded, its figure.

    Attributes:
        name: The one-off's own name.
        kind_label: Its kind, worded (:data:`~app.services.paycheck_line_kinds.LABELS`).
        amount: The figure.
    """
    name: str
    kind_label: str
    amount: Decimal


@dataclass(frozen=True)
class StubReport:
    """A saved stub set beside the paycheck the app prices on its payday.

    Attributes:
        totals: What the stub's figures add up to.
        app_base_pay: The salary's base pay that payday, or ``None`` when the
            app holds no paycheck there any longer (the pay record moved).
        base_gap: The stub's base pay less the salary's (fork 5, "The app's
            salary": the entry screen shows the gap), or ``None`` with it.
        lines: Every paycheck line the stub prints or the app takes that
            payday, in the profile's line order.
        one_offs: The one-offs, in the order they were entered.
        one_off_changes_taxes: Whether a one-off changes the stub's taxes,
            so that it prices a paycheck only when no SWITCHED-ON stub of that
            paycheck's own lines is on or before it -- the engine picker's own
            answer (:func:`app.services.paycheck_calculator.one_offs_change_taxes`,
            ruling **R-SAL123**), which the page states rather than restating
            the rule (plan step salary:S11-c-2c), in **R-SAL126**'s words.
    """
    totals: StubTotals
    app_base_pay: Decimal | None
    base_gap: Decimal | None
    lines: tuple[LineComparison, ...]
    one_offs: tuple[OneOffRow, ...]
    one_off_changes_taxes: bool

    @property
    def disagreements(self) -> int:
        """How many lines disagree -- the count the screen leads with."""
        return sum(1 for line in self.lines if not line.agrees)


@dataclass(frozen=True)
class StubSummary:
    """One stub as the profile page's "Pay stubs" card lists it (**R-SAL51** (a)).

    Attributes:
        stub_id: The stub's id, for its link and its switch.
        payday: Its date.
        net: What its figures add up to.
        use_for_pricing: Its switch.
        version_id: The row's counter, which the switch's form carries.
    """
    stub_id: int
    payday: date
    net: Decimal
    use_for_pricing: bool
    version_id: int


@dataclass(frozen=True)
class FormLines:
    """The profile's paycheck lines, split the way the entry form lists them.

    Ruling **R-SAL50**, "Pick the payday first": "the form lists the paycheck
    lines the app takes that payday ..., the lines it doesn't take that payday
    under their own heading".

    Attributes:
        taken: The lines the app prices on the payday, in line order.
        not_taken: Every other line of the profile, in line order.
    """
    taken: tuple[PaycheckLine, ...]
    not_taken: tuple[PaycheckLine, ...]


# ── The payday ─────────────────────────────────────────────────────


def payday_refusal(ctx: "BalanceContext", day: date, today: date) -> str | None:
    """Return why *day* cannot carry a stub, or ``None`` when it can.

    The stub door's wording of
    :func:`~app.services.salary_paydays.payday_refusal_for_door`, the rule's
    one home (ruling **R-SAL93**, "Each door names its own"): "A stub can be
    entered up to your next payday, ...".

    Args:
        ctx: The route's :class:`~app.services.balance_at.BalanceContext`.
        day: The stub's date.
        today: The owner's civil today (the display timezone's).

    Returns:
        The message to show, or ``None``.
    """
    return payday_refusal_for_door(ctx, day, today, door_words="A stub can be entered")


# ── Reads ──────────────────────────────────────────────────────────


def stub_summaries(profile: SalaryProfile) -> list[StubSummary]:
    """Return one :class:`StubSummary` per stub of *profile*, newest payday first.

    The three child collections load in one ``SELECT ... IN`` each for every
    stub at once, not three per stub (``PayStub``'s own ``lazy="selectin"``,
    since plan step salary:S11-c-2c; an explicit option here until then): the
    card renders on every visit to the profile page.
    """
    stubs = (
        db.session.query(PayStub)
        .filter(PayStub.salary_profile_id == profile.id)
        .order_by(PayStub.payday.desc())
        .all()
    )
    return [
        StubSummary(
            stub_id=stub.id, payday=stub.payday,
            net=totals_of(profile, figures_of(stub)).net,
            use_for_pricing=stub.use_for_pricing, version_id=stub.version_id,
        )
        for stub in stubs
    ]


def stub_on(profile: SalaryProfile, day: date) -> PayStub | None:
    """Return the profile's stub dated *day*, or ``None``."""
    return (
        db.session.query(PayStub)
        .filter(PayStub.salary_profile_id == profile.id, PayStub.payday == day)
        .one_or_none()
    )


def form_lines(profile: SalaryProfile, ctx: "BalanceContext", day: date) -> FormLines:
    """Split the profile's lines into those the app takes on *day* and the rest.

    Args:
        profile: The owned salary profile.
        ctx: The route's :class:`~app.services.balance_at.BalanceContext`.
        day: The payday the form is for: one :func:`payday_refusal`
            accepted, a saved stub's, or a refused form's re-rendered date.

    Returns:
        The :class:`FormLines`.  With no paycheck on *day* (a saved stub whose
        payday the record no longer holds) every line is "not taken".
    """
    priced = _app_paycheck(profile, ctx, day)
    taken_ids = priced[1].keys() if priced is not None else set()
    return FormLines(
        taken=tuple(line for line in profile.lines if line.id in taken_ids),
        not_taken=tuple(line for line in profile.lines if line.id not in taken_ids),
    )


def figures_of(stub: PayStub) -> StubFigures:
    """Return what a saved stub records, as the entry form states it."""
    return StubFigures(
        payday=stub.payday,
        base_pay=stub.base_pay,
        line_amounts={
            row.paycheck_line_id: LineFigure(row.paycheck_line_kind_id, row.amount)
            for row in stub.line_amounts
        },
        withholdings={row.withholding_kind_id: row.amount for row in stub.withholdings},
        one_offs=tuple(
            OneOffFigure(row.name, row.paycheck_line_kind_id, row.amount)
            for row in sorted(stub.one_offs, key=lambda row: row.id)
        ),
        notes=stub.notes,
    )


def totals_of(profile: SalaryProfile, figures: StubFigures) -> StubTotals:
    """Return what *figures* add up to, each line by the kind the STUB prints it under.

    Ruling **R-SAL58**: a line figure and a one-off each carry their own kind,
    so the stub's totals read nothing off the paycheck lines but their
    identity, and an edit of a line moves no saved stub.  The arithmetic is
    the engine's (:func:`~app.services.paycheck_calculator.stub_totals`, since
    plan step **salary:S11-c-2c**), so the gross this door checks is the gross
    the engine prices a saved stub's paycheck at; this function adds only the
    ownership check.

    Args:
        profile: The owned profile whose lines *figures* names.
        figures: The stub's figures.

    Returns:
        The :class:`~app.services.paycheck_calculator.StubTotals`.

    Raises:
        NotFoundError: *figures* names a line the profile does not hold.
    """
    line_ids = {line.id for line in profile.lines}
    for line_id in figures.line_amounts:
        if line_id not in line_ids:
            raise NotFoundError(f"paycheck line {line_id} is not this profile's")
    return stub_totals(
        figures.base_pay, (*figures.line_amounts.values(), *figures.one_offs),
        figures.withholdings.values(),
    )


def stub_report(profile: SalaryProfile, stub: PayStub, ctx: "BalanceContext") -> StubReport:
    """Set a saved stub beside the paycheck the app prices on its payday.

    Args:
        profile: The stub's (owned) profile.
        stub: The saved stub.
        ctx: The route's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`StubReport`.
    """
    figures = figures_of(stub)
    priced = _app_paycheck(profile, ctx, stub.payday)
    app_base, app_lines = priced if priced is not None else (None, {})
    lines = tuple(
        _compare(line, figures.line_amounts.get(line.id), app_lines.get(line.id))
        for line in profile.lines
        if line.id in figures.line_amounts or line.id in app_lines
    )
    return StubReport(
        totals=totals_of(profile, figures),
        app_base_pay=app_base,
        base_gap=(figures.base_pay - app_base) if app_base is not None else None,
        lines=lines,
        one_offs=tuple(
            OneOffRow(
                one_off.name, _kind_label(one_off.paycheck_line_kind_id), one_off.amount,
            )
            for one_off in figures.one_offs
        ),
        one_off_changes_taxes=one_offs_change_taxes(figures.one_offs),
    )


def _compare(
    line: PaycheckLine, on_stub: LineFigure | None, in_app: Decimal | None,
) -> LineComparison:
    """Set one paycheck line's stub figure beside the app's price for it that payday."""
    return LineComparison(
        name=line.name,
        kind=ref_cache.paycheck_line_kind_member(line.paycheck_line_kind_id),
        stub_kind=(
            ref_cache.paycheck_line_kind_member(on_stub.paycheck_line_kind_id)
            if on_stub is not None else None
        ),
        on_stub=on_stub.amount if on_stub is not None else None,
        in_app=in_app,
    )


def _app_paycheck(
    profile: SalaryProfile, ctx: "BalanceContext", day: date,
) -> tuple[Decimal, dict[int, Decimal]] | None:
    """Price the app's own paycheck on *day*: ``(base pay, {line id: amount})``.

    Through the read pass's pricer, the one every salary surface reads, so the
    comparison shows what the grid and the cockpit show.  ``None`` when the
    app holds no paycheck on *day*.
    """
    period = paycheck_on(ctx.calendar(), day)
    if period is None:
        return None
    breakdown = ctx.paychecks().for_profile(profile).at(period)
    return (
        breakdown.earnings.base_biweekly,
        {line.paycheck_line_id: line.amount for line in breakdown.priced_lines},
    )


def _kind_label(kind_id: int) -> str:
    """Word a paycheck-line kind id through the one label map."""
    return paycheck_line_kinds.LABELS[ref_cache.paycheck_line_kind_member(kind_id)]


# ── Writes ─────────────────────────────────────────────────────────


def record_stub(
    profile: SalaryProfile, figures: StubFigures, printed: PrintedTotals,
    ctx: "BalanceContext", today: date,
) -> PayStub:
    """Record a NEW stub for *figures.payday*; a payday already holding one is refused.

    Fork 4 ("One per payday") as ruling **R-SAL52**, "Refuse, show the saved
    one", amends it: the entry flow opens a held payday's stub
    at its first step (**R-SAL50**), so a record reaching a held payday is a
    form left open while the same payday was entered elsewhere, and it is
    refused rather than let overwrite figures the owner never saw.

    Args:
        profile: The owned profile.
        figures: The stub's figures (shape-checked by the entry schema).
        printed: The gross and net the stub prints, checked and discarded.
        ctx: The route's :class:`~app.services.balance_at.BalanceContext`.
        today: The owner's civil today.

    Returns:
        The stub, flushed.

    Raises:
        PayStubRefused: Any refusal the module docstring lists.
        NotFoundError: *figures* names a line or kind the owner does not hold.
    """
    errors = _payday_errors(ctx, figures.payday, today)
    if not errors and stub_on(profile, figures.payday) is not None:
        errors["payday"] = held_payday_refusal(figures.payday)
    # A new stub holds no one-off yet: every one it prints is one it adds.
    _refuse(profile, figures, printed, errors, held=frozenset())
    stub = PayStub(salary_profile_id=profile.id, payday=figures.payday,
                   base_pay=figures.base_pay, notes=figures.notes)
    db.session.add(stub)
    _write(stub, figures)
    db.session.flush()
    return stub


def edit_stub(
    stub: PayStub, figures: StubFigures, printed: PrintedTotals,
    ctx: "BalanceContext", today: date,
) -> PayStub:
    """Rewrite a saved stub's figures, its payday included.

    Args:
        stub: The saved stub, its ownership and version already checked by
            the route; its profile is the one its lines must belong to.
        figures: The stub's figures (shape-checked by the entry schema).
        printed: The gross and net the stub prints, checked and discarded.
        ctx: The route's :class:`~app.services.balance_at.BalanceContext`.
        today: The owner's civil today.

    Returns:
        The stub, flushed.

    Raises:
        PayStubRefused: Any refusal the module docstring lists, including a
            payday another of the profile's stubs holds.
        NotFoundError: *figures* names a line or kind the owner does not hold.
    """
    profile = stub.salary_profile
    # R-SAL53: the payday rules are asked only of a date the edit CHANGES; a
    # stub keeps its own date -- the document's -- even once the pay record
    # no longer holds that payday.
    errors = {}
    if figures.payday != stub.payday:
        errors = _payday_errors(ctx, figures.payday, today)
        if not errors and stub_on(profile, figures.payday) is not None:
            errors["payday"] = (
                f"Your {figures.payday.isoformat()} stub already exists; open it "
                f"to change it."
            )
    # R-SAL57: read BEFORE the write replaces them, so the clash check knows
    # which of the submitted one-offs the stub already holds.
    held = frozenset(name_key(one_off.name) for one_off in stub.one_offs)
    _refuse(profile, figures, printed, errors, held=held)
    _write(stub, figures)
    db.session.flush()
    return stub


def held_payday_refusal(payday: date) -> str:
    """R-SAL52's words for a NEW stub on a payday that already holds one.

    Spelled once for the two places that meet it: the service's own check
    and the record route's arm for the unique key, which is how the same
    refusal arrives when two records of one payday race past that check.
    """
    return (
        f"Your {payday.isoformat()} stub was entered while this form was open; "
        f"open it to change it."
    )


def set_use_for_pricing(stub: PayStub, on: bool) -> bool:
    """Turn a stub's "Use for pricing" switch (fork 8a', **R-SAL51** (a)).

    Nothing is deleted either way: off keeps the stub, greyed and editable.

    Args:
        stub: The saved stub, its version already checked by the route.
        on: The state the owner asked for.

    Returns:
        Whether the switch moved; an identical request writes nothing.
    """
    if stub.use_for_pricing == on:
        return False
    stub.use_for_pricing = on
    return True


def _payday_errors(ctx: "BalanceContext", day: date, today: date) -> dict[str, str]:
    """:func:`payday_refusal` as the refusal map's ``payday`` entry."""
    message = payday_refusal(ctx, day, today)
    return {"payday": message} if message is not None else {}


def _refuse(
    profile: SalaryProfile, figures: StubFigures, printed: PrintedTotals,
    errors: dict[str, str], *, held: frozenset[str],
) -> None:
    """Add the tax, one-off, gross and net refusals to *errors*; raise if any stand.

    Args:
        profile: The owned profile.
        figures: The stub's figures.
        printed: The gross and net the stub prints.
        errors: The refusals found so far (the payday's), added to.
        held: The :func:`name_key` of every one-off the stub already holds --
            empty for a new stub -- for :func:`_one_off_errors`.

    Raises:
        NotFoundError: An id the owner does not hold (a tampered form).
        PayStubRefused: *errors* is not empty.
    """
    _check_ids(figures)
    missing_tax = False
    for kind_id, label in withholding_kinds.kind_options():
        if kind_id not in figures.withholdings:
            errors[f"tax-{kind_id}"] = f"Enter the stub's {label} ($0.00 if none)."
            missing_tax = True
    errors.update(_one_off_errors(profile, figures.one_offs, held))
    totals = totals_of(profile, figures)
    errors.update(gross_refusals(
        figures.base_pay, totals, printed,
        includes_after_tax=profile.stub_gross_includes_after_tax,
    ))
    # The net is checked only over a COMPLETE set of taxes: without one, the
    # lines' sum leaves it out and the check would blame figures that are right.
    if not missing_tax and totals.net != printed.net:
        errors["printed_net"] = (
            f"The lines add up to ${totals.net:,.2f}, but the stub prints "
            f"${printed.net:,.2f} (a difference of ${abs(totals.net - printed.net):,.2f}).  "
            f"Check each figure against the stub."
        )
    if errors:
        raise PayStubRefused(errors)


def _check_ids(figures: StubFigures) -> None:
    """Refuse a tax kind, or a line's or one-off's kind, the reference lists do not hold."""
    tax_ids = {kind_id for kind_id, _ in withholding_kinds.kind_options()}
    if not set(figures.withholdings) <= tax_ids:
        raise NotFoundError("a withholding kind the app does not hold")
    for figure in (*figures.line_amounts.values(), *figures.one_offs):
        if ref_cache.paycheck_line_kind_member(figure.paycheck_line_kind_id) is None:
            raise NotFoundError("a paycheck-line kind the app does not hold")


def name_key(name: str) -> str:
    """The form a name is COMPARED in: capitals and extra spaces ignored (R-SAL51 (b))."""
    return " ".join(name.split()).casefold()


def _one_off_errors(
    profile: SalaryProfile, one_offs: Iterable[OneOffFigure], held: frozenset[str],
) -> dict[str, str]:
    """Refuse a one-off named like a paycheck line, a tax or another one-off.

    Ruling **R-SAL45**: "S11-b's screen refuses a one-off whose name matches
    one of the profile's paycheck lines or a tax name ('enter it on the line
    instead')".  Every line of the profile counts, ended or not: a stub names
    a line by the line, whatever its span.

    **Asked only of a one-off the save ADDS or RENAMES** (ruling **R-SAL57**,
    "Check only what a save adds", which narrows R-SAL45): one whose name the
    stub does not already hold, compared in :func:`name_key`'s form -- the form
    the clash itself is judged in, so a one-off re-saved under its own name,
    or re-cased, meets no clash it did not already carry.  A paycheck line may
    therefore take a saved one-off's name without blocking that stub's next
    edit.  Two one-offs of one name on one stub are refused whatever either's
    age: that is a clash within the save itself.

    Args:
        profile: The owned profile.
        one_offs: The one-offs the save submits, in form order.
        held: The :func:`name_key` of every one-off the stub already holds.

    Returns:
        ``{"one_off:<index>": message}`` for each refused one-off.
    """
    lines = {name_key(line.name): line.name for line in profile.lines}
    taxes = {name_key(label): label for label in withholding_kinds.LABELS.values()}
    seen: set[str] = set()
    errors = {}
    for index, one_off in enumerate(one_offs):
        key = name_key(one_off.name)
        added = key not in held
        if added and key in lines:
            message = (
                f"'{one_off.name}' is your paycheck line '{lines[key]}'; enter "
                f"it on the line instead."
            )
        elif added and key in taxes:
            message = (
                f"'{one_off.name}' is the tax '{taxes[key]}'; enter it with "
                f"the taxes instead."
            )
        elif key in seen:
            message = f"Two one-offs are named '{one_off.name}'."
        else:
            seen.add(key)
            continue
        errors[f"one_off:{index}"] = message
    return errors


def _write(stub: PayStub, figures: StubFigures) -> None:
    """Make *stub* record exactly *figures*, row by row.

    Each child table is diffed on its natural key -- the line, the tax, the
    one-off's name -- so an unchanged figure is not rewritten (the audit log
    records what CHANGED) and no row is deleted and re-inserted under the same
    key.  When anything changed on a SAVED stub the row itself is written, so
    its ``version_id`` covers the edit (the module docstring).
    """
    with db.session.no_autoflush:
        changed = _sync_all(stub, figures)
    if changed and inspect(stub).persistent:
        flag_modified(stub, "base_pay")


def _sync_all(stub: PayStub, figures: StubFigures) -> bool:
    """Apply *figures* to *stub*'s own fields and its three collections.

    Run under ``no_autoflush``: loading a collection after a field changed
    would otherwise flush the row's UPDATE mid-edit, and the forced write
    after it would bump the version a SECOND time for one edit.

    Returns:
        Whether anything changed.
    """
    changed = False
    for attr in ("payday", "base_pay", "notes"):
        if getattr(stub, attr) != getattr(figures, attr):
            setattr(stub, attr, getattr(figures, attr))
            changed = True
    changed |= _sync(
        stub.line_amounts, "paycheck_line_id",
        {line_id: (line.paycheck_line_kind_id, line.amount)
         for line_id, line in figures.line_amounts.items()},
        lambda key, value: PayStubLineAmount(
            paycheck_line_id=key, paycheck_line_kind_id=value[0], amount=value[1],
        ),
        fields=("paycheck_line_kind_id", "amount"),
    )
    changed |= _sync(
        stub.withholdings, "withholding_kind_id", figures.withholdings,
        lambda key, amount: PayStubWithholding(withholding_kind_id=key, amount=amount),
    )
    one_offs = {one_off.name: one_off for one_off in figures.one_offs}
    changed |= _sync(
        stub.one_offs, "name",
        {name: (one_off.paycheck_line_kind_id, one_off.amount)
         for name, one_off in one_offs.items()},
        lambda key, value: PayStubOneOff(
            name=key, paycheck_line_kind_id=value[0], amount=value[1],
        ),
        fields=("paycheck_line_kind_id", "amount"),
    )
    return changed


def _sync(
    rows: list[Any], key_attr: str, wanted: Mapping[Any, Any],
    make: Callable[[Any, Any], Any], fields: tuple[str, ...] = ("amount",),
) -> bool:
    """Diff one child collection onto *wanted*; return whether it changed.

    Args:
        rows: The stub's collection (``delete-orphan``: removing deletes).
        key_attr: The child's natural-key attribute.
        wanted: ``key -> value``; a one-field child's value is its amount, a
            several-field child's is a tuple in *fields* order.
        make: Builds a new child from ``(key, value)``.
        fields: The attributes a value writes.

    Returns:
        Whether any row was added, removed or rewritten.
    """
    changed = False
    held = {getattr(row, key_attr): row for row in rows}
    for key, row in held.items():
        if key not in wanted:
            rows.remove(row)
            changed = True
    for key, value in wanted.items():
        values = value if len(fields) > 1 else (value,)
        row = held.get(key)
        if row is None:
            rows.append(make(key, value))
            changed = True
            continue
        for attr, new in zip(fields, values):
            if getattr(row, attr) != new:
                setattr(row, attr, new)
                changed = True
    return changed


# ── The paycheck-line delete (fork 10, R-SAL51 (c)) ─────────────


def line_delete_refusal(line: PaycheckLine) -> str | None:
    """Return why a paycheck line cannot be deleted, or ``None`` when it can.

    Fork 10, "Refuse; end it instead": a line any stub names is refused, and
    **R-SAL51** (c) names every such stub, newest first.  The database refuses
    the same delete (``fk_pay_stub_line_amounts_paycheck_line`` is
    ``RESTRICT``); this is the door's wording of it.

    Args:
        line: The owned :class:`~app.models.paycheck_line.PaycheckLine`.

    Returns:
        The message, or ``None``.
    """
    paydays = [
        payday for (payday,) in (
            db.session.query(PayStub.payday)
            .join(PayStubLineAmount, PayStubLineAmount.pay_stub_id == PayStub.id)
            .filter(PayStubLineAmount.paycheck_line_id == line.id)
            .order_by(PayStub.payday.desc())
        )
    ]
    if not paydays:
        return None
    dates = [payday.isoformat() for payday in paydays]
    named = dates[0] if len(dates) == 1 else f"{', '.join(dates[:-1])} and {dates[-1]}"
    noun = "stub" if len(dates) == 1 else "stubs"
    return f"{line.name} is on your {named} {noun}; end it instead."


__all__ = [
    "FormLines",
    "LineComparison",
    "LineFigure",
    "OneOffFigure",
    "OneOffRow",
    "PrintedTotals",
    "StubFigures",
    "StubReport",
    "StubSummary",
    "edit_stub",
    "figures_of",
    "form_lines",
    "held_payday_refusal",
    "line_delete_refusal",
    "name_key",
    "payday_refusal",
    "record_stub",
    "set_use_for_pricing",
    "stub_on",
    "stub_report",
    "stub_summaries",
    "totals_of",
]
