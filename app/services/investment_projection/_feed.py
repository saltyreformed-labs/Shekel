"""
Shekel Budget App -- the account payroll feed (plan steps salary:R14-b, S3-e-2).

One account's side of what the paycheck engine prices: what payroll puts into
it on a payday, and the gross of the paycheck that did it.  A VALUE built by
:func:`app.services.projection_inputs.load_payroll_feeds` and read by every
consumer; it computes no deduction of its own, which is the whole point of
the step that introduced it (ruling **R-SAL2**).

**It prices a payday ON DEMAND and carries no window** (ruling **R-SAL15**,
plan step **salary:S3-e-2**).  Until that step it was two dictionaries built
over the owner's SAVED paydays, and past the last of them it INVENTED a
figure -- the gross held at the last priced paycheck, the employee amount at
a "complete calendar year's average" -- six rules over that fold, each
measured wrong (finding **N-541**, ruling **R-SAL10**), and four false
sentences about them in this docstring (**N-542** to **N-546**).  The
remedy was never a seventh rule: the read pass's
:class:`~app.services.income_service.ProfilePaychecks` answers any payday the
owner's cadence reaches (plan step **salary:S3-d**), so the feed holds two
RESOLVERS the loader builds over it and there is no window to run out of.
The alternative -- every caller stating the periods it will read, and a
lookup that RAISES past them -- was refused because ``/retirement`` fills
one batch at the base retirement date and reads it at every retire-later
probe axis, which is the defect ``R-SAL14`` deleted one tier down.

**A resolver takes the PERIOD, not its payday** (ruling **R-SAL19**).  The
engine prices a :class:`~app.services.pay_calendar.DerivedPeriod` -- it
reads ``start_date`` and ``period_id`` -- and every caller already holds
one, so the feed's input is the engine's input.  Keying by date was an
artifact of the deleted dictionaries; keeping it would have meant
re-deriving the period from a date the caller had just peeled off it, and a
fence around the dates that are not paydays.  That revises one clause of
**R-SAL18**: an ORM ``budget.pay_periods`` row spells its key ``id`` and
cannot reach the engine, so it no longer serves
:func:`~app.services.investment_projection.build_contribution_timeline`;
no production caller hands one (all six sites pass ``DerivedPeriod``\\ s).

**Both presence facts are DERIVED from the resolvers**, one home each
(``CLAUDE.md`` rule 14).  An account is payroll-linked exactly when an
active deduction on an active profile names it, which is exactly when the
loader builds it an employee resolver; it funds an employer contribution
exactly when its funding profile is known, which is exactly when it has a
gross resolver.  Plan step salary:S3-e-1 carried ``is_payroll_linked`` as a
required field because the maps could disagree with it; a resolver's
presence cannot.

It lives in its own module because the two halves of
:mod:`app.services.investment_projection` together broke pylint's 1000-line
ceiling at R14-b; the package re-exports every public name under its
original path.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.services.pay_calendar import DerivedPeriod
from app.utils.money import ZERO


@dataclass(frozen=True)
class AccountPayrollFeed:
    """What ONE account's payroll puts in, priced per PERIOD by the engine.

    Two resolvers over the read pass's per-profile pricer, each ``None``
    where the fact it answers does not exist for this account.  A resolver
    is asked for a period and answers off the paycheck the engine prices for
    it -- raise-aware, inflation-escalated, cadence-placed and clamped to
    each line's own calendar-year ``annual_cap``, because the engine applied
    all four before this fold saw the line -- and the memo behind it
    (:class:`~app.services.income_service.ProfilePaychecks`) prices each
    payday once per read pass however many consumers ask.

    **Asking is the only cost, and it is paid where the asking is.**  The
    loader built this over pricers whose own database work is done at
    construction, and every ORM relationship the engine reads is loaded
    before the first walk -- ``deductions`` by
    :func:`~app.services.projection_inputs._load_funding_profiles`'s
    ``subqueryload``, the pay stubs that price the taxes among them (their
    lines by ``lazy="selectin"``, plan step salary:S11-c-2c); the
    raises are converted to :class:`~app.services.salary_raises.RaiseTerms`
    values at :meth:`~app.services.income_service.PaycheckPricing
    .for_profile`, inside the loader, with each type's name resolved from
    its FK through the ref cache (plan step salary:S3-f-1), so the walk reads
    no raise relationship at all; a deduction's method and timing are read
    as the ``calc_method_id`` / ``paycheck_line_kind_id`` columns -- so a
    resolver fired mid-walk in a pure module issues no query.  Counted at the
    cursor rather than assumed, by ``test_projection_inputs.test_the_payroll_feeds
    .TestLoadPayrollFeeds.test_a_resolver_fired_past_the_loader_issues_NO_query``
    over a profile WITH a recurring raise inside the walked window, which
    fails on the deductions' lazy ``SELECT`` the moment the loader's
    eager-load is removed.  That profile records no pay stub, so the count
    grades the deductions' half and not the stubs': the stubs' eager load is
    read off the loader, not measured.
    The module's *no database access* contract is kept by the loader
    supplying the callables, which is the shape ruling **R-SAL15** took.

    **A period BEFORE the calendar's first payday is refused, not held.**
    The dictionaries held their EARLIEST figure backward over such a day;
    a resolver hands the period to the engine, whose ``_month_ordinal``
    raises ``PayCalendarError`` for a payday the calendar cannot place.
    Unreachable from production -- ``projection_axis`` raises its opening
    day to the opening bound, the seam walks ``reported_periods()``, and
    both current-period producers answer a saved period or ``None`` -- and
    a refusal is the right answer to a question nothing asks, where a held
    figure was a wrong one waiting for a caller.

    Attributes:
        employee: ``period -> Decimal`` -- what this account received from
            payroll on that period's paycheck: the sum of every
            :class:`~app.services.paycheck_calculator.PricedLine` naming
            this account across every profile that funds it, pre- and
            post-tax alike, ``$0.00`` included where a cadence skipped the
            deduction.  ``None`` when no active deduction on an active
            profile names this account.
        gross: ``period -> Decimal`` -- the gross of the paycheck the
            account's FUNDING profile was paid on that period
            (``budget.investment_params.salary_profile_id``, ruling
            **R-SAL5**).  ``None`` when no funding profile is known, which
            is the developer's 2026-09-04 ruling: an employer contribution
            whose funding job is unrecorded models NO money and the surface
            says so.  An ARCHIVED profile is unknown for this purpose too,
            and so is one that is not this owner's.
    """

    employee: Callable[[DerivedPeriod], Decimal] | None
    gross: Callable[[DerivedPeriod], Decimal] | None

    @classmethod
    def absent(cls) -> "AccountPayrollFeed":
        """Return the feed of an account no payroll funds.

        The explicit token for "this account has no payroll feed", so a caller
        that means it says so and no reader has to decide whether two missing
        resolvers were meant or forgotten.  It models no employee contribution
        and no employer contribution, which are different facts that happen
        to share this value.

        Returns:
            The :class:`AccountPayrollFeed` with neither resolver.
        """
        return cls(employee=None, gross=None)

    @property
    def is_payroll_linked(self) -> bool:
        """Whether an active deduction on an active profile NAMES this account.

        A PRESENCE fact, whatever the deduction pays: one pricing ``$0.00``
        on every payday still means the owner has wired it up, so
        ``/retirement``'s "nothing linked yet" prompt must not tell them to
        create what exists, and :func:`build_contribution_timeline`'s
        deduction path emits its zeros rather than nothing (ruling
        **R-SAL17**).  It is the presence of the employee resolver, which the
        loader builds exactly when such a deduction exists.

        Returns:
            ``True`` when a deduction funds this account.
        """
        return self.employee is not None

    @property
    def funds_employer(self) -> bool:
        """Whether a known funding profile can size an employer contribution.

        ``False`` when ``budget.investment_params.salary_profile_id`` is unset
        or names an archived profile -- the state in which the developer's
        2026-09-04 ruling models no employer money and the surface says the
        funding job is not set.  A PRESENCE test about the profile link and
        never about the dollars: a profile paid ``$0.00`` still funds.

        Returns:
            ``True`` when a funding profile's paychecks price this feed.
        """
        return self.gross is not None

    def employee_at(self, period: DerivedPeriod) -> Decimal:
        """Return what this account received from payroll on *period*'s payday.

        Args:
            period: The pay period whose paycheck to read.  Saved or
                projected: the engine prices both by the same rules.

        Returns:
            The engine's own figure -- ``$0.00`` included, where a cadence
            skipped the deduction -- and ``$0.00`` for an account no deduction
            funds.
        """
        return ZERO if self.employee is None else self.employee(period)

    def gross_at(self, period: DerivedPeriod) -> Decimal | None:
        """Return the funding profile's gross for the paycheck on *period*.

        Also the ``salary_basis`` resolver
        :func:`~app.services.growth_engine.project_balance` takes: that hook
        is ``period -> gross`` and so is this, so a caller passes the bound
        method rather than wrapping it.

        Args:
            period: The pay period whose paycheck to read.

        Returns:
            The engine's own gross, or ``None`` when no funding profile is
            known -- the refusal :attr:`funds_employer` names, kept as
            ``None`` rather than ``$0.00`` so a caller cannot spend it as a
            basis.
        """
        return None if self.gross is None else self.gross(period)
